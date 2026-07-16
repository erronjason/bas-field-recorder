"""Transcription queue — submits jobs to the REST server and tracks their state.

TranscriptionQueue is owned by SystemTrayApp and talks to ServerManager for
all HTTP calls. It maintains a local mirror of job state so the UI can reflect
queue depth and individual job status without polling on every paint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from . import json_store, user_data

if TYPE_CHECKING:
    from .server_manager import ServerManager
    from .settings import Settings


# ---------------------------------------------------------------------------
# Job mirror
# ---------------------------------------------------------------------------

@dataclass
class QueuedJob:
    job_id: str
    audio_path: str
    display_name: str
    status: str = "queued"   # queued | running | done | error | cancelled
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# TranscriptionQueue
# ---------------------------------------------------------------------------

class TranscriptionQueue(QObject):
    """Manages the local view of server-side transcription jobs.

    Signals
    -------
    queue_updated()  — emitted whenever the job list changes
    job_done(str)    — job_id of a successfully completed transcription
    job_error(str, str) — (job_id, error_message)
    """

    queue_updated = Signal()
    job_done = Signal(str)
    job_error = Signal(str, str)

    _POLL_MS = 3_000

    def __init__(self, server: "ServerManager", parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._server = server
        self._jobs: dict[str, QueuedJob] = {}   # job_id → QueuedJob
        self._paused = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, audio_path: Path, settings: "Settings") -> Optional[str]:
        """Submit a job. Returns job_id on success, None on failure."""
        if not self._server.is_ready():
            return None

        json_path = audio_path.with_suffix(".json")
        stub = json_store.load(json_path) if json_path.exists() else {}

        body = {
            "audio_path": str(audio_path),
            "workspace_path": str(user_data.records_dir()),
            "backend": settings.transcription_backend,
            "model": settings.model,
            "language": settings.language or "en",
            "speakers": None,
            "hf_token": settings.hf_token or None,
            "api_key": settings.assemblyai_api_key or None,
            "display_name": stub.get("display_name", audio_path.stem),
        }

        resp = self._server.post("/jobs", body=body)
        if resp is None:
            return None

        job_id = resp.get("job_id")
        if not job_id:
            return None

        self._jobs[job_id] = QueuedJob(
            job_id=job_id,
            audio_path=str(audio_path),
            display_name=body["display_name"],
            status="queued",
        )
        self.queue_updated.emit()
        return job_id

    def cancel_job(self, job_id: str, parent_widget=None) -> None:
        """Cancel a job with a user-facing warning for running jobs."""
        job = self._jobs.get(job_id)
        if job is None:
            return

        if job.status == "running":
            reply = QMessageBox.warning(
                parent_widget,
                "Cancel transcription",
                "Cancelling a running transcription discards all progress.\n"
                "The audio file is not affected.\n\nCancel anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._server.delete(f"/jobs/{job_id}")
        if job_id in self._jobs:
            self._jobs[job_id].status = "cancelled"
        self.queue_updated.emit()

    def pause_queue(self) -> None:
        self._server.post("/queue/pause")
        self._paused = True
        self.queue_updated.emit()

    def resume_queue(self) -> None:
        self._server.post("/queue/resume")
        self._paused = False
        self.queue_updated.emit()

    def is_paused(self) -> bool:
        return self._paused

    def jobs(self) -> list[QueuedJob]:
        return list(self._jobs.values())

    def active_count(self) -> int:
        return sum(
            1 for j in self._jobs.values()
            if j.status in ("queued", "running")
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    @Slot()
    def _poll(self) -> None:
        if not self._server.is_ready():
            return

        server_jobs = self._server.get("/jobs")
        if server_jobs is None:
            return

        changed = False
        for sj in server_jobs:
            job_id = sj.get("job_id")
            status = sj.get("status", "")
            local = self._jobs.get(job_id)

            if local is None:
                # Job exists on server but not locally (server restarted, GUI re-attached)
                self._jobs[job_id] = QueuedJob(
                    job_id=job_id,
                    audio_path=sj.get("audio_path", ""),
                    display_name=sj.get("display_name", ""),
                    status=status,
                    error=sj.get("error"),
                )
                changed = True
                continue

            if local.status == status:
                continue

            local.status = status
            local.error = sj.get("error")
            changed = True

            if status == "done":
                self._on_job_done(local)
            elif status == "error":
                self.job_error.emit(job_id, local.error or "Unknown error")

        if changed:
            self.queue_updated.emit()

    def _on_job_done(self, job: QueuedJob) -> None:
        """Merge transcriber output into the JSON stub, preserving GUI fields."""
        audio_path = Path(job.audio_path)
        json_path = audio_path.with_suffix(".json")
        if json_path.exists():
            try:
                stub_before = json_store.load(json_path)
                saved_gui_fields = {
                    k: stub_before.get(k)
                    for k in (
                        "record_id", "format_revision", "display_name", "created_at",
                        "source", "duration_seconds", "participants",
                        "speaker_names", "notes", "retain",
                    )
                    if k in stub_before
                }
                # The server wrote a fresh .json; enrich it with GUI fields
                json_store.enrich_post_transcription(json_path, saved_gui_fields)
            except Exception:
                pass

        self.job_done.emit(job.job_id)
