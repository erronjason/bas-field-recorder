"""RecordingsWindow — main Records Viewer window."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import json_store, user_data
from .recording_detail import RecordingDetail
from .recording_list import RecordingList
from .settings import Settings

if TYPE_CHECKING:
    from .server_manager import ServerManager
    from .transcription_queue import TranscriptionQueue


class RecordingsWindow(QMainWindow):
    """Field Recorder — Records viewer.

    Left pane: RecordingList (live-updated list of records).
    Right pane: RecordingDetail (metadata, player, transcript, notes).
    """

    def __init__(
        self,
        server: "ServerManager",
        queue: "TranscriptionQueue",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._server = server
        self._queue = queue
        self._open_settings_fn: Optional[Callable] = None
        self._transcribe_fn: Optional[Callable] = None
        self._import_worker = None
        self._import_errors: list[str] = []

        self.setWindowTitle("Field Recorder — Records")
        self.setMinimumSize(800, 500)
        self.resize(1100, 680)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setProperty("role", "main-splitter")

        self._list = RecordingList()
        self._list.setMinimumWidth(220)

        self._detail = RecordingDetail(queue)

        splitter.addWidget(self._list)
        splitter.addWidget(self._detail)
        splitter.setSizes([280, 820])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        # Header strip: capture controls left, settings right
        header = QWidget()
        header.setFixedHeight(36)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(4)

        self._btn_record = QPushButton("Record")
        self._btn_pause = QPushButton("Pause")
        self._btn_stop = QPushButton("Stop")
        self._btn_import = QPushButton("Import")
        for btn in (self._btn_record, self._btn_pause, self._btn_stop, self._btn_import):
            btn.setProperty("role", "header-ctrl")
            header_layout.addWidget(btn)
        self._btn_pause.hide()
        self._btn_stop.hide()
        self._btn_import.setToolTip("Import an existing audio file as a record")
        self._btn_import.clicked.connect(self.import_audio)

        header_layout.addStretch()

        # Queue-processing indicator (left of settings): "▪ Processing N"
        self._queue_indicator = QWidget()
        qi = QHBoxLayout(self._queue_indicator)
        qi.setContentsMargins(0, 0, 0, 0)
        qi.setSpacing(6)
        self._queue_dot = QFrame()
        self._queue_dot.setProperty("role", "dot")
        self._queue_dot.setProperty("state", "active")
        qi.addWidget(self._queue_dot)
        self._queue_word = QLabel("Processing")
        self._queue_word.setProperty("role", "metadata")
        qi.addWidget(self._queue_word)
        self._queue_count = QLabel()
        self._queue_count.setProperty("role", "measured")
        qi.addWidget(self._queue_count)
        header_layout.addWidget(self._queue_indicator)
        self._queue_indicator.hide()

        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setProperty("role", "icon-btn")
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self._on_open_settings)
        header_layout.addWidget(self._settings_btn)

        self._help_btn = QPushButton("?")
        self._help_btn.setProperty("role", "icon-btn")
        self._help_btn.setFixedSize(28, 28)
        self._help_btn.setToolTip("How to use Field Recorder")
        self._help_btn.clicked.connect(self._show_help)
        header_layout.addWidget(self._help_btn)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setProperty("role", "rule")

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(header)
        vbox.addWidget(rule)
        vbox.addWidget(splitter)
        self.setCentralWidget(container)

        # Wire list ↔ detail
        self._list.record_selected.connect(self._detail.load_record)
        self._detail.record_deleted.connect(self._on_record_deleted)

        # Live transcription-queue indicator
        self._queue.queue_updated.connect(self._refresh_queue_indicator)
        self._refresh_queue_indicator()

        # Keyboard shortcuts
        self._install_shortcuts()

        # Daily retention sweep
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(86_400_000)  # 24 h
        self._sweep_timer.timeout.connect(self._run_sweep)
        self._sweep_timer.start()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_settings_opener(self, fn: Callable) -> None:
        self._open_settings_fn = fn

    def set_transcribe_handler(self, fn: Callable) -> None:
        """Supply the consent-gated enqueue used after a capture (tray owns it)."""
        self._transcribe_fn = fn

    def import_audio(self) -> None:
        """Pick existing audio files and bring them into the records store."""
        from .audio_import import FILE_DIALOG_FILTER, ImportWorker
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import audio", "", FILE_DIALOG_FILTER
        )
        if not paths:
            return

        self._import_errors = []
        self._btn_import.setEnabled(False)
        self._import_worker = ImportWorker([Path(p) for p in paths], parent=self)
        self._import_worker.imported.connect(self._on_imported)
        self._import_worker.import_error.connect(self._on_import_error)
        self._import_worker.finished_all.connect(self._on_import_finished)
        self._import_worker.start()

    @Slot(Path)
    def _on_imported(self, audio_path: Path) -> None:
        self._list._rebuild()
        # Same consent-gated path a capture takes; no-op if disabled or offline.
        if self._transcribe_fn:
            self._transcribe_fn(audio_path)

    @Slot(str, str)
    def _on_import_error(self, filename: str, message: str) -> None:
        self._import_errors.append(f"{filename}: {message}")

    @Slot()
    def _on_import_finished(self) -> None:
        self._btn_import.setEnabled(True)
        self._list._rebuild()
        if self._import_errors:
            QMessageBox.warning(
                self,
                "Import incomplete",
                "These files were not imported:\n\n" + "\n".join(self._import_errors),
            )
            self._import_errors = []

    def set_recording_controls(
        self,
        start_fn: Callable,
        pause_fn: Callable,
        stop_fn: Callable,
        hotkey: str = "",
    ) -> None:
        self._btn_record.clicked.connect(start_fn)
        self._btn_pause.clicked.connect(pause_fn)
        self._btn_stop.clicked.connect(stop_fn)
        self.update_record_hotkey(hotkey)

    def update_record_hotkey(self, hotkey: str) -> None:
        tip = f"Start capture  {hotkey}" if hotkey else "Start capture"
        self._btn_record.setToolTip(tip)

    def set_recording_state(self, state: str) -> None:
        is_idle = state == "idle"
        is_live = state in ("recording", "paused", "saving")
        self._btn_record.setVisible(is_idle)
        self._btn_pause.setVisible(is_live)
        self._btn_stop.setVisible(is_live)
        self._btn_pause.setText("Resume" if state == "paused" else "Pause")

    def refresh_list(self) -> None:
        self._list._rebuild()

    def on_transcription_done(self, audio_path: str) -> None:
        """Refresh the list (preserving scroll/selection) and, if the just-
        transcribed record is the one open in the detail panel, refresh it in
        place so the new transcript appears without disturbing playback or the
        user's in-progress notes."""
        self._list._rebuild()
        if not audio_path:
            return
        data = json_store.load(Path(audio_path).with_suffix(".json"))
        record_id = data.get("record_id", "")
        if record_id and self._detail.loaded_record_id == record_id:
            self._detail.refresh_after_transcription(record_id)

    def run_initial_sweep(self) -> None:
        QTimer.singleShot(0, self._run_sweep)

    def _show_help(self) -> None:
        """Show the usage guide, reflecting the current hotkey bindings."""
        from .help_dialog import HelpDialog
        s = Settings.load()
        hotkeys = {
            "start_stop": s.hotkey_start_stop,
            "notes": s.hotkey_notes,
            "pause_resume": s.hotkey_pause_resume,
        }
        HelpDialog(hotkeys, self._open_settings_fn, parent=self).exec()

    def maybe_show_onboarding(self) -> None:
        """Show the usage guide once, on first run."""
        marker = user_data.onboarding_marker_path()
        if marker.exists():
            return
        self._show_help()
        try:
            marker.write_text("shown", encoding="utf-8")
        except OSError:
            pass

    def _refresh_queue_indicator(self) -> None:
        """Reflect how many records are queued or transcribing. Hidden at zero."""
        n = self._queue.active_count()
        if n == 0:
            self._queue_indicator.hide()
            return
        paused = self._queue.is_paused()
        self._queue_word.setText("Paused" if paused else "Processing")
        self._queue_count.setText(str(n))
        self._queue_dot.setProperty("state", "" if paused else "active")
        # Re-polish so the QSS state change takes effect at runtime
        self._queue_dot.style().unpolish(self._queue_dot)
        self._queue_dot.style().polish(self._queue_dot)
        noun = "record" if n == 1 else "records"
        self._queue_indicator.setToolTip(
            f"Transcription queue paused — {n} {noun}" if paused
            else f"Transcribing {n} {noun}"
        )
        self._queue_indicator.show()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _on_open_settings(self) -> None:
        if self._open_settings_fn:
            self._open_settings_fn()

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _on_record_deleted(self, _record_id: str) -> None:
        self._list._rebuild()

    # ------------------------------------------------------------------
    # Retention sweep
    # ------------------------------------------------------------------

    def _run_sweep(self) -> None:
        settings = Settings.load()
        if settings.auto_delete_days is None:
            return
        exclude = set()
        rid = self._detail.loaded_record_id
        if rid:
            exclude.add(rid)
        json_store.run_retention_sweep(settings.auto_delete_days, exclude)
        self._list._rebuild()

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def _install_shortcuts(self) -> None:
        # Ctrl+F: focus search
        sc_search = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_search.activated.connect(self._list.focus_search)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        focused = self.focusWidget()
        in_text = isinstance(focused, (QPlainTextEdit,)) or (
            hasattr(focused, "metaObject") and focused.metaObject().className() == "QLineEdit"
        )

        if key == Qt.Key.Key_Space and not in_text:
            self._detail.player_bar.toggle()
            return

        if key == Qt.Key.Key_Left and not in_text:
            delta = -30_000 if mods & Qt.KeyboardModifier.ShiftModifier else -5_000
            self._detail.player_bar.seek_relative(delta)
            return

        if key == Qt.Key.Key_Right and not in_text:
            delta = 30_000 if mods & Qt.KeyboardModifier.ShiftModifier else 5_000
            self._detail.player_bar.seek_relative(delta)
            return

        if key == Qt.Key.Key_Up and not in_text:
            self._list.move_selection(-1)
            return

        if key == Qt.Key.Key_Down and not in_text:
            self._list.move_selection(1)
            return

        if key == Qt.Key.Key_Delete and not in_text:
            self._detail._on_delete()
            return

        if key == Qt.Key.Key_Escape:
            if self._list.clear_search():
                return
            self.hide()
            return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Hide instead of close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
