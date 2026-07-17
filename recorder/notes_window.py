from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NotesWindow(QWidget):
    """Floating always-on-top notes panel shown during recording."""

    _AUTOSAVE_INTERVAL_MS = 10_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notes")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.resize(380, 300)

        self._notes_path: Optional[Path] = None

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(self._AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._autosave)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._header = QLabel("Session notes")
        self._header.setProperty("role", "section")
        layout.addWidget(self._header)

        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("Session notes.")
        layout.addWidget(self._editor)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self, display_name: str, notes_path: Path) -> None:
        """Called when recording starts."""
        self._notes_path = notes_path
        self._header.setText(f"Notes — {display_name}")
        self._editor.clear()
        self._autosave_timer.start()

    def end_session(self) -> str:
        """Called when recording stops. Returns final notes text."""
        self._autosave_timer.stop()
        text = self._editor.toPlainText()
        self._flush(text)
        self._notes_path = None
        self._header.setText("Session notes")
        self.hide()
        return text

    def get_text(self) -> str:
        return self._editor.toPlainText()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _autosave(self) -> None:
        self._flush(self._editor.toPlainText())

    def _flush(self, text: str) -> None:
        if self._notes_path and text:
            try:
                self._notes_path.write_text(text, encoding="utf-8")
            except OSError:
                pass
