"""RecordingsWindow — main Records Viewer window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
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

        self.setCentralWidget(splitter)

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

    def run_initial_sweep(self) -> None:
        QTimer.singleShot(0, self._run_sweep)

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
