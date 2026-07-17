"""Entry point for the Field Recorder GUI."""

import sys

from PySide6.QtWidgets import QApplication

from recorder import crash_recovery, json_store
from recorder.hotkeys import HotkeyManager
from recorder.icons import bas_icon
from recorder.server_manager import ServerManager
from recorder.settings import Settings
from recorder.setup_wizard import SetupWizard, backend_ready
from recorder.transcription_queue import TranscriptionQueue
from recorder.tray import SystemTrayApp
from recorder.typography import bas_palette, load_qss
from recorder.recordings_window import RecordingsWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Field Recorder")
    app.setStyle("Fusion")
    app.setPalette(bas_palette())
    app.setStyleSheet(load_qss())
    app.setWindowIcon(bas_icon(32))
    app.setQuitOnLastWindowClosed(False)

    settings = Settings.load()

    # ── Crash recovery (before event loop) ───────────────────────────
    crash_recovery.check_and_recover()

    # ── One-shot schema migration ─────────────────────────────────────
    json_store.migrate_existing_records()

    # ── First-run setup wizard ────────────────────────────────────────
    if not backend_ready():
        wizard = SetupWizard()
        wizard.exec()
        # Wizard rejected → start in recording-only mode (no transcription)

    # ── Server + queue ────────────────────────────────────────────────
    server = ServerManager()
    queue = TranscriptionQueue(server)

    if backend_ready():
        server.start()

    # ── Global hotkeys ────────────────────────────────────────────────
    hotkeys = HotkeyManager()
    conflicts = hotkeys.register(
        settings.hotkey_start_stop,
        settings.hotkey_pause_resume,
        settings.hotkey_notes,
    )

    # ── Records Viewer ────────────────────────────────────────────────
    recordings_window = RecordingsWindow(server, queue)
    recordings_window.run_initial_sweep()

    # ── Tray app ──────────────────────────────────────────────────────
    tray = SystemTrayApp(server, queue, hotkeys, recordings_window=recordings_window)
    recordings_window.set_settings_opener(tray._open_settings)

    if conflicts:
        names = {"start_stop": "Start/Stop", "pause_resume": "Pause/Resume", "notes": "Notes"}
        msg = "\n".join(f"  {names.get(a, a)}: {s}" for a, s in conflicts.items())
        tray.showMessage(
            "Hotkey conflict",
            f"Could not register some hotkeys:\n{msg}\n"
            "Change them in Settings → Hotkeys.",
            msecs=6000,
        )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
