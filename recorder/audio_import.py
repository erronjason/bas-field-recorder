"""Audio file import — bring an existing recording into the records store.

An imported record is the same three siblings as a captured one
(<stem>.<ext> + <stem>.json + <stem>.txt), except the audio keeps its original
format rather than being transcoded. See docs/audio_import_spec.md.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import json_store, user_data

# Formats the transcriber accepts (transcribe.py) and QMediaPlayer can play.
SUPPORTED_EXTENSIONS = json_store.AUDIO_EXTENSIONS

FILE_DIALOG_FILTER = (
    "Audio files ("
    + " ".join(f"*{ext}" for ext in SUPPORTED_EXTENSIONS)
    + ");;All files (*)"
)

_SAFE_STEM = re.compile(r"[^A-Za-z0-9._ -]+")


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _sanitize_stem(stem: str) -> str:
    """Reduce a source filename to something safe for the flat records store."""
    cleaned = _SAFE_STEM.sub("_", stem).strip(" ._-")
    return cleaned or "imported"


def unique_destination(source: Path, records_dir: Path) -> Path:
    """Pick a collision-free destination inside the records store.

    Keeps the original name so Reveal stays meaningful; on collision appends
    _2, _3, … A stem is taken if any sibling of that stem already exists.
    """
    stem = _sanitize_stem(source.stem)
    ext = source.suffix.lower()

    def taken(candidate_stem: str) -> bool:
        if (records_dir / f"{candidate_stem}.json").exists():
            return True
        return any(
            (records_dir / f"{candidate_stem}{e}").exists()
            for e in SUPPORTED_EXTENSIONS
        )

    if not taken(stem):
        return records_dir / f"{stem}{ext}"

    n = 2
    while taken(f"{stem}_{n}"):
        n += 1
    return records_dir / f"{stem}_{n}{ext}"


def import_one(source: Path) -> Path:
    """Copy one audio file into the store and write its record stub.

    Returns the path of the imported audio file. Raises on failure.
    """
    if not source.exists():
        raise FileNotFoundError(f"{source.name} no longer exists.")
    if not is_supported(source):
        raise ValueError(f"{source.suffix or 'This file'} is not a supported audio format.")

    records_dir = user_data.records_dir()
    dest = unique_destination(source, records_dir)
    shutil.copy2(source, dest)

    # display_name comes from the original filename; duration is read here and
    # may be null for formats libsndfile can't open (mp3/m4a) — nulls are honest.
    json_path = json_store.create_stub(dest, display_name=source.stem, notes="")

    # Honest provenance: this record was imported, not captured.
    data = json_store.load(json_path)
    source_block = data.get("source") or {}
    source_block["application"] = "Imported"
    json_store.update_fields(json_path, source=source_block)

    return dest


class ImportWorker(QThread):
    """Copies audio files into the records store off the UI thread.

    Emits imported(Path) per successful file and import_error(str, str) as
    (filename, message) per failure, then finished_all().
    """

    imported = Signal(Path)
    import_error = Signal(str, str)
    finished_all = Signal()

    def __init__(self, sources: list[Path], parent=None) -> None:
        super().__init__(parent)
        self._sources = list(sources)

    def run(self) -> None:
        for source in self._sources:
            try:
                self.imported.emit(import_one(source))
            except Exception as exc:
                self.import_error.emit(source.name, str(exc))
        self.finished_all.emit()
