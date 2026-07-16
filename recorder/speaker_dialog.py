"""SpeakerDialog — map diarization labels to real identities."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class SpeakerDialog(QDialog):
    """One QLineEdit per SPEAKER_XX label.

    After exec() == Accepted, read .result_names for the updated mapping.
    Does not touch segments.
    """

    def __init__(self, speaker_names: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Identify speakers")
        self.setMinimumWidth(320)

        self._fields: dict[str, QLineEdit] = {}
        self.result_names: dict[str, str] = dict(speaker_names)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel("Enter a name for each speaker label.")
        header.setWordWrap(True)
        layout.addWidget(header)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setProperty("role", "rule")
        layout.addWidget(rule)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # Sort keys: SPEAKER_00 before SPEAKER_01, etc.
        for i, key in enumerate(sorted(speaker_names.keys())):
            label = QLabel(key)
            label.setProperty("role", "measured")
            field = QLineEdit()
            field.setText(speaker_names.get(key, ""))
            field.setPlaceholderText(f"Speaker {i + 1}")
            self._fields[key] = field
            form.addRow(label, field)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self.result_names = {key: field.text().strip() for key, field in self._fields.items()}
        self.accept()
