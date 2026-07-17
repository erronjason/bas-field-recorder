from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from . import json_store


class NamingDialog(QDialog):
    """Shown after every recording stops. Lets the user name and annotate it.

    Normally writes a JSON stub on close (empty name if skipped). If the user
    discards, no stub is written and ``.discarded`` is True — callers should
    delete the audio file.
    """

    def __init__(self, wav_path: Path, notes: str = "", parent=None):
        super().__init__(parent)
        self._wav_path = wav_path
        self._stub_written = False
        self.discarded = False

        self.setWindowTitle("Name this record")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Record name (optional):"))

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Call with the Bureau")
        self._name_edit.returnPressed.connect(self._save)
        layout.addWidget(self._name_edit)

        hint = QLabel("Leave blank to use the date and time.")
        hint.setProperty("role", "metadata")
        layout.addWidget(hint)

        dur_text = self._duration_text(wav_path)
        if dur_text:
            dur_label = QLabel(dur_text)
            dur_label.setProperty("role", "metadata")
            layout.addWidget(dur_label)

        layout.addWidget(QLabel("Notes (optional):"))

        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Field notes, context, follow-ups…")
        self._notes_edit.setFixedHeight(80)
        if notes:
            self._notes_edit.setPlainText(notes)
        layout.addWidget(self._notes_edit)

        buttons = QDialogButtonBox()
        discard_btn = buttons.addButton("Discard", QDialogButtonBox.ButtonRole.DestructiveRole)
        discard_btn.setProperty("role", "destructive")
        discard_btn.style().unpolish(discard_btn)
        discard_btn.style().polish(discard_btn)
        skip_btn = buttons.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
        save_btn = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        save_btn.setDefault(True)
        discard_btn.clicked.connect(self._discard)
        skip_btn.clicked.connect(self._skip)
        save_btn.clicked.connect(self._save)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _save(self) -> None:
        display_name = self._name_edit.text().strip()
        self._write_stub(display_name)
        self.accept()

    def _skip(self) -> None:
        self._write_stub("")
        self.reject()

    def _discard(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle("Discard record")
        msg.setText(
            "The audio file will be permanently deleted. This cannot be undone."
        )
        confirm_btn = msg.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(cancel_btn)
        msg.exec()
        if msg.clickedButton() is confirm_btn:
            self.discarded = True
            self.reject()

    def _write_stub(self, display_name: str) -> None:
        if not self._stub_written:
            notes = self._notes_edit.toPlainText().strip()
            json_store.create_stub(self._wav_path, display_name, notes)
            self._stub_written = True

    # ------------------------------------------------------------------
    # Ensure stub is always written even if the user closes the window
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if not self.discarded:
            self._write_stub("")
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _duration_text(wav_path: Path) -> str:
        if not wav_path.exists():
            return "Saving record…"
        try:
            import soundfile as sf
            info = sf.info(str(wav_path))
            secs = int(info.duration)
            m, s = divmod(secs, 60)
            h, m2 = divmod(m, 60)
            if h:
                return f"Duration: {h}:{m2:02d}:{s:02d}"
            return f"Duration: {m}:{s:02d}"
        except Exception:
            return ""
