import enum
import shutil
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Slot
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import user_data
from .audio import AudioBackend, MixdownWorker, RecorderThread, get_audio_backend
from .naming_dialog import NamingDialog
from .notes_window import NotesWindow
from .settings import Settings, SettingsWindow


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    SAVING = "saving"


_STATE_COLORS = {
    State.IDLE: "#6c757d",
    State.RECORDING: "#dc3545",
    State.PAUSED: "#fd7e14",
    State.SAVING: "#0d6efd",
}

_icon_cache: dict[State, QIcon] = {}


def _icon(state: State, size: int = 22) -> QIcon:
    if state not in _icon_cache:
        px = QPixmap(size, size)
        px.fill(QColor("transparent"))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(_STATE_COLORS[state]))
        p.setPen(QColor("transparent"))
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.end()
        _icon_cache[state] = QIcon(px)
    return _icon_cache[state]


class SystemTrayApp(QSystemTrayIcon):
    """System-tray controller.  Owns the state machine, recorder thread,
    mixdown worker, and notes window."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._state = State.IDLE
        self._settings = Settings.load()
        self._backend: Optional[AudioBackend] = None
        self._recorder: Optional[RecorderThread] = None
        self._mixdown_worker: Optional[MixdownWorker] = None
        self._pending_tmp_path: Optional[Path] = None
        self._naming_done = False
        self._mixdown_done = False

        self._notes_window = NotesWindow()

        self._build_menu()
        self._refresh()

        self.activated.connect(self._on_activated)
        self.setToolTip("Diarized Transcriber")
        self.show()

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()

        self._act_record = QAction("Start Recording", menu)
        self._act_record.triggered.connect(self._start_recording)
        menu.addAction(self._act_record)

        self._act_pause = QAction("Pause", menu)
        self._act_pause.triggered.connect(self._toggle_pause)
        menu.addAction(self._act_pause)

        self._act_stop = QAction("Stop Recording", menu)
        self._act_stop.triggered.connect(self._stop_recording)
        menu.addAction(self._act_stop)

        menu.addSeparator()

        self._act_notes = QAction("Show Notes", menu)
        self._act_notes.triggered.connect(self._show_notes)
        menu.addAction(self._act_notes)

        menu.addSeparator()

        act_workspace = QAction("Open Recordings Folder", menu)
        act_workspace.triggered.connect(self._open_workspace)
        menu.addAction(act_workspace)

        act_data = QAction("Open Data Directory", menu)
        act_data.triggered.connect(self._open_data_dir)
        menu.addAction(act_data)

        menu.addSeparator()

        self._act_settings = QAction("Settings…", menu)
        self._act_settings.triggered.connect(self._open_settings)
        menu.addAction(self._act_settings)

        menu.addSeparator()

        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.setContextMenu(menu)

    def _refresh(self) -> None:
        """Sync all menu item visibility/labels and the tray icon to current state."""
        is_idle = self._state == State.IDLE
        is_live = self._state in (State.RECORDING, State.PAUSED)

        self._act_record.setVisible(is_idle)
        self._act_pause.setVisible(is_live)
        self._act_stop.setVisible(is_live)
        self._act_notes.setVisible(is_live)

        self._act_pause.setText(
            "Resume" if self._state == State.PAUSED else "Pause"
        )
        self._act_settings.setEnabled(is_idle)
        self.setIcon(_icon(self._state))

    # ------------------------------------------------------------------
    # User actions
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

    def _open_workspace(self) -> None:
        _reveal_path(user_data.workspace())

    def _open_data_dir(self) -> None:
        _reveal_path(user_data.app_data_root())

    def _open_settings(self) -> None:
        if self._backend is None:
            self._backend = get_audio_backend()
        dlg = SettingsWindow(self._settings, self._backend)
        dlg.exec()

    def _quit(self) -> None:
        if self._state != State.IDLE:
            reply = QMessageBox.question(
                None,
                "Recording in progress",
                "A recording is still in progress.\nQuit anyway? The current recording will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        QApplication.quit()

    # ------------------------------------------------------------------
    # Slots — recorder lifecycle
    # ------------------------------------------------------------------

    @Slot()
    def _on_recording_started(self) -> None:
        self._state = State.RECORDING
        self._refresh()
        self.showMessage("Recording", "Recording started.", msecs=2000)

        if self._recorder and self._recorder.tmp_path:
            notes_path = self._recorder.tmp_path / "notes.txt"
            self._notes_window.start_session("Call", notes_path)

    @Slot(Path, str)
    def _on_recording_stopped(self, tmp_path: Path, notes: str) -> None:
        wav_path = user_data.workspace() / (tmp_path.name + ".flac")

        self._naming_done = False
        self._mixdown_done = False
        self._pending_tmp_path = tmp_path

        self._mixdown_worker = MixdownWorker(tmp_path, wav_path, parent=self)
        self._mixdown_worker.mixdown_complete.connect(self._on_mixdown_complete)
        self._mixdown_worker.mixdown_error.connect(self._on_mixdown_error)
        self._mixdown_worker.start()

        # NamingDialog.exec() runs a nested event loop — MixdownWorker
        # signals are delivered and processed while the dialog is open.
        dlg = NamingDialog(wav_path, notes)
        dlg.exec()
        self._naming_done = True

        if self._mixdown_done:
            self._finish_saving()

    @Slot(str)
    def _on_recorder_error(self, error: str) -> None:
        self._state = State.IDLE
        self._refresh()
        QMessageBox.critical(None, "Recording error", error)

    # ------------------------------------------------------------------
    # Slots — mixdown lifecycle
    # ------------------------------------------------------------------

    @Slot(Path)
    def _on_mixdown_complete(self, wav_path: Path) -> None:  # noqa: ARG002
        self._cleanup_tmp()
        self._mixdown_done = True
        if self._naming_done:
            self._finish_saving()

    @Slot(str)
    def _on_mixdown_error(self, error: str) -> None:
        self._cleanup_tmp()
        self._mixdown_done = True
        QMessageBox.critical(None, "Save failed", f"Could not save recording:\n{error}")
        if self._naming_done:
            self._finish_saving()

    def _cleanup_tmp(self) -> None:
        if self._pending_tmp_path:
            shutil.rmtree(self._pending_tmp_path, ignore_errors=True)
            self._pending_tmp_path = None

    def _finish_saving(self) -> None:
        self._mixdown_worker = None
        self._recorder = None
        self._state = State.IDLE
        self._refresh()

    # ------------------------------------------------------------------
    # Tray activation
    # ------------------------------------------------------------------

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Double-click starts/stops recording as a quick shortcut
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
        QMessageBox.critical(None, "Diarized Transcriber", msg)


# ---------------------------------------------------------------------------
# OS helper
# ---------------------------------------------------------------------------

def _reveal_path(path: Path) -> None:
    import subprocess
    s = str(path)
    if sys.platform == "win32":
        import os
        os.startfile(s)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", s])
    else:
        subprocess.Popen(["xdg-open", s])
