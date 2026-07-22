"""Qt wrapper around audio import — keeps the copy off the UI thread."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .audio_import import import_one


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
