"""RecordingsWindow — main Records Viewer window."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import json_store
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
        for btn in (self._btn_record, self._btn_pause, self._btn_stop):
            btn.setProperty("role", "header-ctrl")
            header_layout.addWidget(btn)
        self._btn_pause.hide()
        self._btn_stop.hide()

        header_layout.addStretch()

        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setProperty("role", "icon-btn")
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self._on_open_settings)
        header_layout.addWidget(self._settings_btn)

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
