"""One-time cloud transcription consent dialog."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout


class CloudConsentDialog(QDialog):
    """Shown exactly once, the first time the user selects the cloud backend.

    Accepted  → user consented; caller sets settings.cloud_consent_given = True.
    Rejected  → user declined; caller should revert backend to "local".
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audio will leave this device")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        body = QLabel(
            "<b>Cloud transcription sends your recording to AssemblyAI's servers</b> "
            "for processing.<br><br>"
            "Audio is transmitted over an encrypted connection and is not stored "
            "permanently by AssemblyAI after transcription completes. However, it "
            "temporarily leaves this device and is subject to AssemblyAI's privacy "
            "policy.<br><br>"
            "<b>Local transcription</b> keeps all audio on this device. It requires "
            "more disk space and a one-time model download (~1.5 GB)."
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        layout.addWidget(body)

        privacy_link = QLabel(
            '<a href="https://www.assemblyai.com/legal/privacy-policy">'
            "AssemblyAI Privacy Policy ↗</a>"
        )
        privacy_link.setOpenExternalLinks(True)
        layout.addWidget(privacy_link)

        buttons = QDialogButtonBox()
        local_btn = buttons.addButton(
            "Switch to local", QDialogButtonBox.ButtonRole.RejectRole
        )
        proceed_btn = buttons.addButton(
            "I understand — proceed", QDialogButtonBox.ButtonRole.AcceptRole
        )
        local_btn.clicked.connect(self.reject)
        proceed_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)
