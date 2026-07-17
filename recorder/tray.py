"""SystemTrayApp — Field Recorder state machine."""

from __future__ import annotations

import enum
import shutil
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import user_data
from .icons import tray_state_icon as _state_icon
from .audio import AudioBackend, MixdownWorker, RecorderThread, get_audio_backend
from .hotkeys import HotkeyManager
from .naming_dialog import NamingDialog
from .notes_window import NotesWindow
from .server_manager import ServerManager
from .settings import Settings, SettingsWindow
from .transcription_queue import TranscriptionQueue

if TYPE_CHECKING:
    from .recordings_window import RecordingsWindow


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    SAVING = "saving"


_icon_cache: dict[State, QIcon] = {}


def _icon(state: State, size: int = 22) -> QIcon:
    if state not in _icon_cache:
        _icon_cache[state] = _state_icon(state.value, size)
    return _icon_cache[state]


class SystemTrayApp(QSystemTrayIcon):
    """System-tray controller for Field Recorder."""

    def __init__(
        self,
        server: ServerManager,
        queue: TranscriptionQueue,
        hotkeys: HotkeyManager,
        recordings_window: Optional["RecordingsWindow"] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self._state = State.IDLE
        self._settings = Settings.load()
        self._backend: Optional[AudioBackend] = None
        self._recorder: Optional[RecorderThread] = None
        self._mixdown_worker: Optional[MixdownWorker] = None
        self._pending_tmp_path: Optional[Path] = None
        self._naming_done = False
        self._mixdown_done = False
        self._discarded = False

        self._notes_window = NotesWindow()
        self._server = server
        self._queue = queue
        self._hotkeys = hotkeys
        self._recordings_window = recordings_window

        # Hotkey signals → recording actions
        self._hotkeys.start_stop_triggered.connect(self._hotkey_start_stop)
        self._hotkeys.pause_resume_triggered.connect(self._hotkey_pause_resume)
        self._hotkeys.notes_triggered.connect(self._show_notes)

        # Queue signals → tray tooltip
        self._queue.queue_updated.connect(self._refresh_tooltip)
        self._queue.job_done.connect(self._on_job_done)
        self._queue.job_error.connect(self._on_job_error)

        # Server status → tray tooltip
        self._server.status_changed.connect(self._on_server_status)

        self._build_menu()
        self._refresh()

        self.activated.connect(self._on_activated)
        self.show()

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()

        self._act_record = QAction("Start capture", menu)
        self._act_record.triggered.connect(self._start_recording)
        menu.addAction(self._act_record)

        self._act_pause = QAction("Pause", menu)
        self._act_pause.triggered.connect(self._toggle_pause)
        menu.addAction(self._act_pause)

        self._act_stop = QAction("Stop capture", menu)
        self._act_stop.triggered.connect(self._stop_recording)
        menu.addAction(self._act_stop)

        menu.addSeparator()

        self._act_notes = QAction("Session notes", menu)
        self._act_notes.triggered.connect(self._show_notes)
        menu.addAction(self._act_notes)

        menu.addSeparator()

        self._act_pause_queue = QAction("Pause transcription queue", menu)
        self._act_pause_queue.triggered.connect(self._toggle_queue_pause)
        menu.addAction(self._act_pause_queue)

        menu.addSeparator()

        act_workspace = QAction("Open records", menu)
        act_workspace.triggered.connect(self._open_records)
        menu.addAction(act_workspace)

        menu.addSeparator()

        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.setContextMenu(menu)

    def _refresh(self) -> None:
        is_idle = self._state == State.IDLE
        is_live = self._state in (State.RECORDING, State.PAUSED)

        self._act_record.setVisible(is_idle)
        self._act_pause.setVisible(is_live)
        self._act_stop.setVisible(is_live)
        self._act_notes.setVisible(is_live)
        self._act_pause.setText("Resume" if self._state == State.PAUSED else "Pause")

        # Queue pause toggle
        q_paused = self._queue.is_paused()
        self._act_pause_queue.setText(
            "Resume transcription queue" if q_paused else "Pause transcription queue"
        )
        self._act_pause_queue.setEnabled(not is_live)

        self.setIcon(_icon(self._state))
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        parts = ["Field Recorder"]

        active = self._queue.active_count()
        if active:
            paused = " (paused)" if self._queue.is_paused() else ""
            parts.append(f"Transcribing {active} record{'s' if active > 1 else ''}{paused}")

        if not self._server.is_ready():
            parts.append("Service: not running")

        self.setToolTip(" — ".join(parts))

    # ------------------------------------------------------------------
    # Recording actions
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        if self._backend is None:
            self._backend = get_audio_backend()

        mic_info = self._resolve_mic()
        lb_info = self._resolve_loopback()
        if mic_info is None:
            self._alert("No microphone found. Open Settings to configure an input device.")
            return

        self._recorder = RecorderThread(self._backend, mic_info, lb_info, parent=self)
        self._recorder.recording_started.connect(self._on_recording_started)
        self._recorder.recording_stopped.connect(self._on_recording_stopped)
        self._recorder.error.connect(self._on_recorder_error)
        self._recorder.start()

    def _toggle_pause(self) -> None:
        if self._recorder is None:
            return
        if self._state == State.RECORDING:
            self._recorder.pause()
            self._state = State.PAUSED
        else:
            self._recorder.resume()
            self._state = State.RECORDING
        self._refresh()

    def _stop_recording(self) -> None:
        if self._recorder is None:
            return
        notes = self._notes_window.end_session()
        self._recorder.stop(notes)
        self._state = State.SAVING
        self._refresh()

    def _show_notes(self) -> None:
        self._notes_window.show()
        self._notes_window.raise_()

    def _toggle_queue_pause(self) -> None:
        if self._queue.is_paused():
            self._queue.resume_queue()
        else:
            self._queue.pause_queue()
        self._refresh()

    def _open_records(self) -> None:
        if self._recordings_window is not None:
            self._recordings_window.show()
            self._recordings_window.raise_()
            self._recordings_window.activateWindow()
        else:
            _reveal_path(user_data.records_dir())

    def _open_settings(self) -> None:
        if self._backend is None:
            self._backend = get_audio_backend()

        dlg = SettingsWindow(
            self._settings,
            self._backend,
            server=self._server,
            hotkey_conflicts=self._hotkeys.conflicts(),
        )
        dlg.hotkeys_changed.connect(self._reregister_hotkeys)
        dlg.reinstall_requested.connect(self._run_setup_wizard)
        dlg.exec()

    def _quit(self) -> None:
        if self._state != State.IDLE:
            reply = QMessageBox.question(
                None,
                "Capture in progress",
                "A capture is in progress.\nQuit anyway? The current capture will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._server.stop()
        self._hotkeys.unregister()
        QApplication.quit()

    # ------------------------------------------------------------------
    # Recorder slots
    # ------------------------------------------------------------------

    @Slot()
    def _on_recording_started(self) -> None:
        self._state = State.RECORDING
        self._refresh()
        self.showMessage("Capture", "Capture started.", msecs=2000)

        if self._recorder and self._recorder.tmp_path:
            notes_path = self._recorder.tmp_path / "notes.txt"
            self._notes_window.start_session("Call", notes_path)

    @Slot(Path, str)
    def _on_recording_stopped(self, tmp_path: Path, notes: str) -> None:
        audio_path = user_data.records_dir() / (tmp_path.name + ".flac")

        self._naming_done = False
        self._mixdown_done = False
        self._pending_tmp_path = tmp_path

        self._mixdown_worker = MixdownWorker(tmp_path, audio_path, parent=self)
        self._mixdown_worker.mixdown_complete.connect(self._on_mixdown_complete)
        self._mixdown_worker.mixdown_error.connect(self._on_mixdown_error)
        self._mixdown_worker.start()

        self._discarded = False

        dlg = NamingDialog(audio_path, notes)
        dlg.exec()
        self._naming_done = True

        if dlg.discarded:
            if self._mixdown_done:
                # Mixdown finished while dialog was open — FLAC exists, delete it now.
                audio_path.unlink(missing_ok=True)
                self._discarded = False
                self._mixdown_worker = None
                self._recorder = None
                self._state = State.IDLE
                self._refresh()
            else:
                # Mixdown still running — flag it; _on_mixdown_complete will clean up.
                self._discarded = True
        elif self._mixdown_done:
            self._finish_saving(audio_path)

    @Slot(str)
    def _on_recorder_error(self, error: str) -> None:
        self._state = State.IDLE
        self._refresh()
        QMessageBox.critical(None, "Recording error", error)

    # ------------------------------------------------------------------
    # Mixdown slots
    # ------------------------------------------------------------------

    @Slot(Path)
    def _on_mixdown_complete(self, audio_path: Path) -> None:
        self._cleanup_tmp()
        self._mixdown_done = True
        if self._naming_done:
            if self._discarded:
                audio_path.unlink(missing_ok=True)
                self._discarded = False
                self._mixdown_worker = None
                self._recorder = None
                self._state = State.IDLE
                self._refresh()
            else:
                self._finish_saving(audio_path)

    @Slot(str)
    def _on_mixdown_error(self, error: str) -> None:
        self._cleanup_tmp()
        self._mixdown_done = True
        if self._naming_done and not self._discarded:
            QMessageBox.critical(None, "Save failed", f"Could not save recording:\n{error}")
            self._finish_saving(None)

    def _cleanup_tmp(self) -> None:
        if self._pending_tmp_path:
            shutil.rmtree(self._pending_tmp_path, ignore_errors=True)
            self._pending_tmp_path = None

    def _finish_saving(self, audio_path: Optional[Path]) -> None:
        self._mixdown_worker = None
        self._recorder = None
        self._state = State.IDLE
        self._refresh()

        # Auto-transcribe if enabled, server ready, and we have a valid file
        if (
            audio_path
            and audio_path.exists()
            and self._settings.auto_transcribe
            and self._server.is_ready()
        ):
            self._maybe_consent_then_enqueue(audio_path)

    def _maybe_consent_then_enqueue(self, audio_path: Path) -> None:
        if self._settings.transcription_backend == "cloud" and not self._settings.cloud_consent_given:
            from .consent_dialog import CloudConsentDialog
            dlg = CloudConsentDialog()
            if dlg.exec() != CloudConsentDialog.DialogCode.Accepted:
                self._settings.transcription_backend = "local"
                self._settings.save()
                return
            self._settings.cloud_consent_given = True
            self._settings.save()

        self._queue.enqueue(audio_path, self._settings)

    # ------------------------------------------------------------------
    # Queue slots
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_job_done(self, job_id: str) -> None:
        self.showMessage("Transcription complete", "Record transcribed.", msecs=3000)
        self._refresh_tooltip()

    @Slot(str, str)
    def _on_job_error(self, job_id: str, error: str) -> None:
        self.showMessage("Transcription failed", error[:120], msecs=4000)
        self._refresh_tooltip()

    # ------------------------------------------------------------------
    # Server slot
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_server_status(self, status: str) -> None:
        self._refresh_tooltip()

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------

    def _reregister_hotkeys(self) -> None:
        self._settings = Settings.load()
        conflicts = self._hotkeys.register(
            self._settings.hotkey_start_stop,
            self._settings.hotkey_pause_resume,
            self._settings.hotkey_notes,
        )
        if conflicts:
            names = {"start_stop": "Start/Stop capture", "pause_resume": "Pause/Resume capture", "notes": "Notes"}
            msg = "\n".join(f"  {names[a]}: {s}" for a, s in conflicts.items())
            self.showMessage(
                "Hotkey conflict",
                f"Could not register:\n{msg}\nChange them in Settings → Hotkeys.",
                msecs=5000,
            )

    @Slot()
    def _hotkey_start_stop(self) -> None:
        if self._state == State.IDLE:
            self._start_recording()
        elif self._state in (State.RECORDING, State.PAUSED):
            self._stop_recording()

    @Slot()
    def _hotkey_pause_resume(self) -> None:
        if self._state in (State.RECORDING, State.PAUSED):
            self._toggle_pause()

    # ------------------------------------------------------------------
    # Setup wizard (from Settings → Reinstall Backend)
    # ------------------------------------------------------------------

    def _run_setup_wizard(self) -> None:
        from .setup_wizard import SetupWizard
        dlg = SetupWizard(parent=None)
        if dlg.exec() == SetupWizard.DialogCode.Accepted:
            self._server.start()

    # ------------------------------------------------------------------
    # Tray activation
    # ------------------------------------------------------------------

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self._state == State.IDLE:
                self._start_recording()
            elif self._state in (State.RECORDING, State.PAUSED):
                self._stop_recording()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_mic(self) -> Optional[dict]:
        idx = self._settings.mic_device_index
        if idx is not None:
            for d in self._backend.list_input_devices():
                if int(d["index"]) == idx:
                    return d
        return self._backend.get_default_mic()

    def _resolve_loopback(self) -> Optional[dict]:
        idx = self._settings.loopback_device_index
        if idx is not None:
            for d in self._backend.list_loopback_devices():
                if int(d["index"]) == idx:
                    return d
        return self._backend.get_default_loopback()

    def _alert(self, msg: str) -> None:
        QMessageBox.critical(None, "Field Recorder", msg)


# ---------------------------------------------------------------------------
# OS helper
# ---------------------------------------------------------------------------

def _reveal_path(path: Path) -> None:
    import os
    import subprocess
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
