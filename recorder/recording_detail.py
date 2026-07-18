"""RecordingDetail — right pane of the Records Viewer.

Shows metadata, playback controls, transcript, and notes for the selected record.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import json_store, user_data
from .player_bar import PlayerBar
from .speaker_dialog import SpeakerDialog

if TYPE_CHECKING:
    from .transcription_queue import TranscriptionQueue


# ---------------------------------------------------------------------------
# Off-thread record loader
# ---------------------------------------------------------------------------

class _LoadWorker(QObject):
    finished = Signal(dict, str)   # (data, transcript_text)

    def __init__(self, json_path: Path) -> None:
        super().__init__()
        self._path = json_path

    @Slot()
    def run(self) -> None:
        data = json_store.load(self._path)
        transcript = _build_transcript(data, mode="timestamps")
        self.finished.emit(data, transcript)


def _speaker_label(raw: str, names: dict[str, str], index_map: dict[str, int]) -> str:
    name = names.get(raw, "").strip()
    if name:
        return name
    idx = index_map.get(raw, 0)
    return f"Speaker {idx + 1}"


def _build_transcript(data: dict, mode: str) -> str:
    segments = data.get("segments") or []
    names = data.get("speaker_names") or {}
    if not segments:
        return ""

    # Build stable speaker → index map (sort by first appearance)
    seen: list[str] = []
    for seg in segments:
        sp = seg.get("speaker", "")
        if sp and sp not in seen:
            seen.append(sp)
    index_map = {sp: i for i, sp in enumerate(seen)}

    if mode == "timestamps":
        lines = []
        for seg in segments:
            start = seg.get("start", 0.0)
            sp = seg.get("speaker", "")
            text = seg.get("text", "").strip()
            m, s = divmod(int(start), 60)
            h, m = divmod(m, 60)
            stamp = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            label = _speaker_label(sp, names, index_map)
            lines.append(f"{stamp}  {label:<20}  {text}")
        return "\n".join(lines)

    else:  # reading mode — merge consecutive same-speaker blocks
        blocks: list[tuple[str, list[str]]] = []
        for seg in segments:
            sp = seg.get("speaker", "")
            text = seg.get("text", "").strip()
            if not text:
                continue
            if blocks and blocks[-1][0] == sp:
                blocks[-1][1].append(text)
            else:
                blocks.append((sp, [text]))

        parts = []
        for sp, texts in blocks:
            label = _speaker_label(sp, names, index_map)
            parts.append(f"{label.upper()}\n{''.join(texts)}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Section label helper
# ---------------------------------------------------------------------------

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setProperty("role", "section")
    return lbl


def _rule() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setProperty("role", "rule")
    return f


# ---------------------------------------------------------------------------
# RecordingDetail
# ---------------------------------------------------------------------------

class RecordingDetail(QWidget):
    record_deleted = Signal(str)   # record_id

    def __init__(self, queue: "TranscriptionQueue", parent=None) -> None:
        super().__init__(parent)
        self._queue = queue
        self._record_id: str = ""
        self._data: dict = {}
        self._json_path: Optional[Path] = None
        self._flac_path: Optional[Path] = None
        self._transcript_mode: str = "timestamps"
        self._notes_dirty: bool = False
        self._preserve_session: bool = False

        self._load_thread: Optional[QThread] = None
        self._load_worker: Optional[_LoadWorker] = None

        self._notes_timer = QTimer()
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(1000)
        self._notes_timer.timeout.connect(self._save_notes)

        self._build_ui()
        self._show_empty()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(0)

        # ── Empty / error state placeholder ──────────────────────────
        self._empty_widget = QWidget()
        empty_v = QVBoxLayout(self._empty_widget)
        empty_v.addStretch()
        empty_v.addWidget(_section_label("Records"))
        empty_v.addWidget(_rule())
        self._empty_lbl = QLabel("Select a record from the list.")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setProperty("role", "empty-state")
        empty_v.addWidget(self._empty_lbl)
        empty_v.addStretch()
        layout.addWidget(self._empty_widget)

        # ── Detail content ────────────────────────────────────────────
        self._content = QWidget()
        self._content.hide()
        content_v = QVBoxLayout(self._content)
        content_v.setContentsMargins(0, 0, 0, 0)
        content_v.setSpacing(12)

        # Name — inline editable; saves on Enter or blur
        self._name_edit = QLineEdit()
        self._name_edit.setProperty("role", "record-title-edit")
        self._name_edit.setPlaceholderText("Untitled")
        self._name_edit.editingFinished.connect(self._on_name_edited)
        content_v.addWidget(self._name_edit)

        # Meta: date + duration
        self._meta_lbl = QLabel()
        self._meta_lbl.setProperty("role", "record-meta")
        content_v.addWidget(self._meta_lbl)

        content_v.addWidget(_rule())

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self._btn_speakers = QPushButton("Identify speakers")
        self._btn_speakers.clicked.connect(self._on_identify_speakers)
        toolbar.addWidget(self._btn_speakers)
        self._btn_retranscribe = QPushButton("Retranscribe")
        self._btn_retranscribe.clicked.connect(self._on_retranscribe)
        toolbar.addWidget(self._btn_retranscribe)
        self._btn_reveal = QPushButton("Reveal")
        self._btn_reveal.clicked.connect(self._on_reveal)
        toolbar.addWidget(self._btn_reveal)
        self._btn_delete = QPushButton("Delete")
        self._btn_delete.setProperty("role", "destructive")
        self._btn_delete.clicked.connect(self._on_delete)
        toolbar.addWidget(self._btn_delete)
        toolbar.addStretch()
        self._btn_hold = QPushButton("Retention hold")
        self._btn_hold.setProperty("role", "hold-btn")
        self._btn_hold.setCheckable(True)
        self._btn_hold.toggled.connect(self._on_hold_toggled)
        toolbar.addWidget(self._btn_hold)
        content_v.addLayout(toolbar)

        # Player bar
        self._player_bar = PlayerBar()
        content_v.addWidget(self._player_bar)

        content_v.addWidget(_rule())

        # Transcript section
        transcript_header = QHBoxLayout()
        transcript_header.addWidget(_section_label("Transcript"))
        transcript_header.addStretch()
        self._btn_mode = QPushButton("Timestamps")
        self._btn_mode.setProperty("role", "mode-toggle")
        self._btn_mode.clicked.connect(self._toggle_transcript_mode)
        transcript_header.addWidget(self._btn_mode)
        content_v.addLayout(transcript_header)
        content_v.addWidget(_rule())

        self._transcript = QPlainTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setProperty("role", "transcript")
        content_v.addWidget(self._transcript, stretch=1)

        # Notes section
        content_v.addWidget(_section_label("Notes"))
        content_v.addWidget(_rule())
        self._notes = QPlainTextEdit()
        self._notes.setProperty("role", "notes")
        self._notes.setFixedHeight(80)
        self._notes.textChanged.connect(self._on_notes_changed)
        content_v.addWidget(self._notes)

        layout.addWidget(self._content)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def loaded_record_id(self) -> str:
        return self._record_id

    @property
    def player_bar(self) -> PlayerBar:
        return self._player_bar

    @Slot(str, str)
    def load_record(self, record_id: str, json_filename: str = "") -> None:
        if not record_id and not json_filename:
            self._player_bar.release()
            self._record_id = ""
            self._data = {}
            self._json_path = None
            self._flac_path = None
            self._show_empty()
            return

        # Prefer direct path from filename (fast); fall back to full-dir scan
        if json_filename:
            candidate = user_data.records_dir() / json_filename
            json_path = candidate if candidate.exists() else self._find_json(record_id)
        else:
            json_path = self._find_json(record_id)

        if json_path is None:
            self._show_error(f"Record not found on disk.")
            return

        # Derive record_id from file when not provided (e.g. pre-migration records)
        if not record_id:
            import json as _json
            try:
                record_id = _json.loads(json_path.read_text(encoding="utf-8")).get("record_id", "") or json_path.stem
            except Exception:
                record_id = json_path.stem

        self._save_notes_immediate()
        self._player_bar.release()
        self._record_id = record_id
        self._json_path = json_path
        self._flac_path = json_path.with_suffix(".flac")

        self._show_content()
        self._start_load(json_path)

    def refresh_after_transcription(self, record_id: str) -> None:
        """Re-read this record to show a newly-arrived transcript without
        disturbing the user's session: keeps audio playback and the live
        notes/name edits intact, and persists those edits so they win over
        whatever the post-transcription restore wrote to disk.
        """
        if not record_id or record_id != self._record_id or self._json_path is None:
            return
        # Live notes are the source of truth — persist them (and refresh the
        # crash-fallback snapshot) before re-reading the transcript.
        self._save_notes_immediate()
        self._start_load(self._json_path, preserve_session=True)

    # ------------------------------------------------------------------
    # Internal: load
    # ------------------------------------------------------------------

    def _find_json(self, record_id: str) -> Optional[Path]:
        for p in user_data.records_dir().glob("*.json"):
            try:
                import json as _json
                data = _json.loads(p.read_text(encoding="utf-8"))
                if data.get("record_id") == record_id:
                    return p
            except Exception:
                pass
        return None

    def _start_load(self, json_path: Path, preserve_session: bool = False) -> None:
        # preserve_session: keep audio playback and the live notes/name edits
        # (used when refreshing in place after transcription).
        self._preserve_session = preserve_session
        # Abort any in-flight load
        if self._load_thread and self._load_thread.isRunning():
            self._load_thread.quit()
            self._load_thread.wait()

        self._load_thread = QThread(self)
        self._load_worker = _LoadWorker(json_path)
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_worker.finished.connect(self._load_thread.quit)
        self._load_thread.start()

    @Slot(dict, str)
    def _on_load_finished(self, data: dict, transcript: str) -> None:
        preserve = self._preserve_session
        self._data = data

        # Name — don't clobber an in-progress rename
        if not self._name_edit.hasFocus():
            self._name_edit.setText(data.get("display_name") or "")

        # Meta
        from datetime import datetime
        created_str = data.get("created_at", "")
        dur = data.get("duration_seconds")
        dur_str = ""
        if dur is not None:
            total = int(dur); m, s = divmod(total, 60); h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        try:
            dt = datetime.fromisoformat(created_str).astimezone()
            date_str = dt.strftime("%B %d, %Y  %H:%M")
        except (ValueError, TypeError):
            date_str = ""
        meta = "  |  ".join(x for x in [date_str, dur_str] if x)
        self._meta_lbl.setText(meta)

        # Player — keep current playback untouched when refreshing in place
        # (the audio file is unchanged by transcription)
        if not preserve and self._flac_path and self._flac_path.exists():
            self._player_bar.load(self._flac_path, data.get("duration_seconds"))

        # Transcript
        self._transcript_mode = "timestamps"
        self._btn_mode.setText("Reading")
        self._transcript.setPlainText(transcript)

        # Notes — preserve the live editor (and cursor) on an in-place refresh;
        # the user's notes were already persisted before the reload
        if not preserve:
            self._notes.blockSignals(True)
            self._notes.setPlainText(data.get("notes") or "")
            self._notes.blockSignals(False)

        # Retention hold button
        self._btn_hold.blockSignals(True)
        self._btn_hold.setChecked(bool(data.get("retain")))
        self._btn_hold.setText("Hold applied" if data.get("retain") else "Retention hold")
        self._btn_hold.setProperty("active", str(bool(data.get("retain"))).lower())
        self._btn_hold.blockSignals(False)

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _persist(self, **fields) -> None:
        """Write GUI-owned fields to the record, and mirror them into the
        crash-fallback snapshot if a transcription is in flight — so edits made
        while transcribing aren't reverted when the record is restored."""
        if not self._json_path:
            return
        json_store.update_fields(self._json_path, **fields)
        if self._flac_path:
            json_store.update_gui_snapshot(self._flac_path, **fields)

    @Slot()
    def _on_name_edited(self) -> None:
        if not self._json_path:
            return
        name = self._name_edit.text().strip()
        self._data["display_name"] = name
        self._persist(display_name=name)

    def _on_identify_speakers(self) -> None:
        if not self._json_path:
            return
        speaker_names = self._data.get("speaker_names") or {}
        if not speaker_names:
            # Build from segments
            segments = self._data.get("segments") or []
            seen: list[str] = []
            for seg in segments:
                sp = seg.get("speaker", "")
                if sp and sp not in seen:
                    seen.append(sp)
            speaker_names = {sp: "" for sp in seen}
        if not speaker_names:
            QMessageBox.information(self, "No speakers", "This record has no speaker labels yet. Transcribe it first.")
            return

        dlg = SpeakerDialog(speaker_names, parent=self)
        if dlg.exec() == SpeakerDialog.DialogCode.Accepted:
            self._data["speaker_names"] = dlg.result_names
            self._persist(speaker_names=dlg.result_names)
            # Refresh transcript with new names
            transcript = _build_transcript(self._data, mode=self._transcript_mode)
            self._transcript.setPlainText(transcript)

    def _on_retranscribe(self) -> None:
        if not self._json_path or not self._flac_path:
            return

        reply = QMessageBox.question(
            self,
            "Retranscribe",
            "Retranscribing may reassign speaker labels. Identities will need to be "
            "confirmed after the new transcript is ready.\n\n"
            f"Audio: {self._flac_path.name}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Save notes and speaker_names before enqueue
        self._save_notes_immediate()
        json_store.update_fields(
            self._json_path,
            notes=self._notes.toPlainText(),
            speaker_names=self._data.get("speaker_names") or {},
        )
        # enqueue() below captures a fresh snapshot from the just-updated stub

        # Reload settings at enqueue time (not at window-open time)
        from .settings import Settings
        settings = Settings.load()
        self._queue.enqueue(self._flac_path, settings)

    def _on_reveal(self) -> None:
        if not self._flac_path:
            return
        _reveal_path(self._flac_path)

    def _on_delete(self) -> None:
        if not self._json_path or not self._flac_path:
            return
        txt_path = self._json_path.with_suffix(".txt")
        files = "\n".join(
            str(p.name) for p in [self._flac_path, self._json_path, txt_path]
            if p.exists()
        )
        reply = QMessageBox.question(
            self,
            "Delete record",
            f"Permanently delete these files?\n\n{files}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        rid = self._record_id
        self._player_bar.release()

        # Delete FLAC first — if this fails, abort (record remains coherent)
        try:
            if self._flac_path.exists():
                self._flac_path.unlink()
        except OSError as e:
            QMessageBox.critical(
                self,
                "Delete failed",
                f"Could not delete {self._flac_path.name}.\n{e}\n\nNo files were removed.",
            )
            return

        try:
            self._json_path.unlink(missing_ok=True)
            txt_path.unlink(missing_ok=True)
        except OSError:
            pass

        self._record_id = ""
        self._data = {}
        self._json_path = None
        self._flac_path = None
        self._show_empty()
        self.record_deleted.emit(rid)

    @Slot(bool)
    def _on_hold_toggled(self, checked: bool) -> None:
        if not self._json_path:
            return
        self._data["retain"] = checked
        self._persist(retain=checked)
        self._btn_hold.setText("Hold applied" if checked else "Retention hold")
        self._btn_hold.setProperty("active", str(checked).lower())
        # Re-polish so QSS picks up the property change
        self._btn_hold.style().unpolish(self._btn_hold)
        self._btn_hold.style().polish(self._btn_hold)

    def _toggle_transcript_mode(self) -> None:
        if self._transcript_mode == "timestamps":
            self._transcript_mode = "reading"
            self._btn_mode.setText("Timestamps")
        else:
            self._transcript_mode = "timestamps"
            self._btn_mode.setText("Reading")
        if self._data:
            self._transcript.setPlainText(
                _build_transcript(self._data, mode=self._transcript_mode)
            )

    # ------------------------------------------------------------------
    # Notes auto-save
    # ------------------------------------------------------------------

    @Slot()
    def _on_notes_changed(self) -> None:
        self._notes_timer.start()

    @Slot()
    def _save_notes(self) -> None:
        if self._json_path and self._record_id:
            text = self._notes.toPlainText()
            self._persist(notes=text)
            if self._data:
                self._data["notes"] = text

    def _save_notes_immediate(self) -> None:
        self._notes_timer.stop()
        self._save_notes()

    # ------------------------------------------------------------------
    # UI state helpers
    # ------------------------------------------------------------------

    def _show_empty(self) -> None:
        self._empty_lbl.setText("Select a record from the list.")
        self._empty_widget.show()
        self._content.hide()

    def _show_error(self, msg: str) -> None:
        self._empty_lbl.setText(msg)
        self._empty_widget.show()
        self._content.hide()

    def _show_content(self) -> None:
        self._empty_widget.hide()
        self._content.show()


# ---------------------------------------------------------------------------
# OS helper (mirrors tray._reveal_path)
# ---------------------------------------------------------------------------

def _reveal_path(path: Path) -> None:
    import subprocess
    if sys.platform == "win32":
        # /select, and path must be one token — no space between them
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
