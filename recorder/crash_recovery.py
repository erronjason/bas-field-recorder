import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from . import user_data
from .audio import do_mixdown


def _parse_session_time(session_name: str) -> datetime:
    """Parse timestamp from 'recording_YYYYMMDD_HHMMSS' directory name."""
    try:
        ts = session_name[len("recording_"):]
        return datetime.strptime(ts, "%Y%m%d_%H%M%S")
    except ValueError:
        return datetime.now()


def check_and_recover() -> None:
    """Scan tmp/ for orphaned recording sessions and offer to recover each one.

    Called at startup before the tray app's event loop starts.  Runs
    synchronously — blocking is acceptable here because we haven't entered
    the main event loop yet.
    """
    tmp = user_data.tmp_dir()
    # A valid session has at least mic.meta written (written first in RecorderThread)
    orphans = sorted(
        [d for d in tmp.iterdir() if d.is_dir() and (d / "mic.meta").exists()],
        key=lambda d: d.name,
    )

    for session_dir in orphans:
        dt = _parse_session_time(session_dir.name)
        label = dt.strftime("%b %d at %I:%M %p")

        reply = QMessageBox.question(
            None,
            "Incomplete record found",
            f"A record from {label} did not finish saving.\nRecover it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
            _recover(session_dir)
        else:
            shutil.rmtree(session_dir, ignore_errors=True)


def _recover(session_dir: Path) -> None:
    """Run mixdown on an orphaned session and show the naming dialog."""
    # Import here to avoid circular imports at module load
    from .naming_dialog import NamingDialog

    wav_name = session_dir.name + ".flac"
    wav_path = user_data.records_dir() / wav_name

    notes_path = session_dir / "notes.txt"
    notes = (
        notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    )

    try:
        do_mixdown(session_dir, wav_path)
    except Exception as exc:
        QMessageBox.warning(
            None,
            "Recovery failed",
            f"Could not recover the record:\n{exc}",
        )
        shutil.rmtree(session_dir, ignore_errors=True)
        return

    # Show naming dialog synchronously (we're still at startup, pre-event-loop)
    dlg = NamingDialog(wav_path, notes)
    dlg.exec()

    if dlg.discarded:
        wav_path.unlink(missing_ok=True)

    shutil.rmtree(session_dir, ignore_errors=True)
