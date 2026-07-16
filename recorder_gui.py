"""Entry point for the Diarized Transcriber GUI."""

import sys

from PySide6.QtWidgets import QApplication

from recorder import crash_recovery
from recorder.tray import SystemTrayApp


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Diarized Transcriber")
    app.setQuitOnLastWindowClosed(False)

    crash_recovery.check_and_recover()

    tray = SystemTrayApp()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
