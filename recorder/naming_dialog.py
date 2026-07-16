from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from . import json_store


class NamingDialog(QDialog):
    """Shown after every recording stops. Lets the user give it a display name.

    Always writes a JSON stub (with empty name if skipped) so downstream
    components have a consistent sidecar to read.
    """

    def __init__(self, wav_path: Path, notes: str = "", parent=None):
        super().__init__(parent)
        self._wav_path = wav_path
        self._notes = notes
        self._stub_written = False

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
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        dur_text = self._duration_text(wav_path)
        if dur_text:
            dur_label = QLabel(dur_text)
            dur_label.setStyleSheet("color: gray; font-size: 11px;")
            layout.addWidget(dur_label)

        buttons = QDialogButtonBox()
        skip_btn = buttons.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
        save_btn = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        save_btn.setDefault(True)
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

    def _write_stub(self, display_name: str) -> None:
        if not self._stub_written:
            json_store.create_stub(self._wav_path, display_name, self._notes)
            self._stub_written = True

    # ------------------------------------------------------------------
    # Ensure stub is always written even if the user closes the window
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
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
