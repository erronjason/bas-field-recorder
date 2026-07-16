"""
FastAPI transcription server — managed by recorder_gui as a child process.

Runs inside backend/venv/ (has whisperX, pyannote, torch, assemblyai).
The GUI starts this via QProcess and communicates over localhost only.

Endpoints
---------
GET  /health
POST /jobs           enqueue a new transcription job
GET  /jobs           list all jobs
GET  /jobs/{id}      get one job
DELETE /jobs/{id}    cancel queued or running job
POST /queue/pause    graceful pause (current job finishes)
POST /queue/resume   resume queued dispatch
POST /shutdown       graceful server shutdown
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import tempfile
import threading
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Make diarized_transcriber importable when this script is run directly
# from inside backend/venv/ (server_manager sets the script path explicitly).
sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

JobStatus = Literal["queued", "running", "done", "error", "cancelled"]


class Job:
    def __init__(
        self,
        audio_path: str,
        workspace_path: str,
        backend: str,
        model: str,
        language: Optional[str],
        speakers: Optional[int],
        hf_token: Optional[str],
        api_key: Optional[str],
        display_name: str,
    ) -> None:
        self.job_id = str(uuid4())
        self.audio_path = audio_path
        self.workspace_path = workspace_path
        self.backend = backend
        self.model = model
        self.language = language
        self.speakers = speakers
        self.hf_token = hf_token
        self.api_key = api_key
        self.display_name = display_name
        self.status: JobStatus = "queued"
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "audio_path": self.audio_path,
            "display_name": self.display_name,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Global queue state (protected by _lock)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_jobs: dict[str, Job] = {}
_queue: list[str] = []          # ordered pending job_ids
_queue_paused = False
_current_proc: Optional[mp.Process] = None
_current_job_id: Optional[str] = None
_worker_event = threading.Event()


# ---------------------------------------------------------------------------
# Subprocess target — runs inside a separate Process for true cancellation
# ---------------------------------------------------------------------------

def _transcription_subprocess(
    audio_path: str,
    backend_name: str,
    model: str,
    language: Optional[str],
    speakers: Optional[int],
    hf_token: Optional[str],
    api_key: Optional[str],
    workspace_path: str,
    error_file: str,
) -> None:
    """Executed in an isolated child process per job."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from diarized_transcriber import (
            CloudBackend,
            LocalBackend,
            write_json,
            write_text,
        )

        if backend_name == "local":
            lang = None if language in ("auto", None) else language
            bk = LocalBackend(model_name=model, hf_token=hf_token, language=lang)
        else:
            bk = CloudBackend(api_key=api_key or "")

        result = bk.transcribe(audio_path, num_speakers=speakers)

        ws = Path(workspace_path)
        stem = Path(audio_path).stem
        write_json(result, str(ws / f"{stem}.json"))
        write_text(result, str(ws / f"{stem}.txt"))
    except Exception as exc:  # noqa: BLE001
        Path(error_file).write_text(str(exc), encoding="utf-8")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Worker thread — picks jobs from queue and runs them serially
# ---------------------------------------------------------------------------

def _worker_loop() -> None:
    global _current_proc, _current_job_id

    while True:
        _worker_event.wait()
        _worker_event.clear()

        with _lock:
            if _queue_paused or not _queue:
                continue
            next_id = _queue.pop(0)
            job = _jobs.get(next_id)
            if job is None or job.status == "cancelled":
                _worker_event.set()
                continue
            job.status = "running"
            _current_job_id = next_id

        error_file = tempfile.mktemp(suffix="_dt_error.txt")

        ctx = mp.get_context("spawn")
        proc = ctx.Process(
            target=_transcription_subprocess,
            args=(
                job.audio_path,
                job.backend,
                job.model,
                job.language,
                job.speakers,
                job.hf_token,
                job.api_key,
                job.workspace_path,
                error_file,
            ),
            daemon=True,
        )

        with _lock:
            _current_proc = proc

        proc.start()
        proc.join()

        with _lock:
            _current_proc = None
            _current_job_id = None
            j = _jobs.get(next_id)

        if j is not None and j.status != "cancelled":
            err_path = Path(error_file)
            if proc.exitcode == 0:
                j.status = "done"
            else:
                j.status = "error"
                j.error = (
                    err_path.read_text(encoding="utf-8")
                    if err_path.exists()
                    else f"Process exited with code {proc.exitcode}"
                )

        try:
            Path(error_file).unlink(missing_ok=True)
        except Exception:
            pass

        _worker_event.set()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Diarized Transcriber Server", version="0.1.0")


class JobRequest(BaseModel):
    audio_path: str
    workspace_path: str
    backend: str = "local"
    model: str = "medium"
    language: Optional[str] = "en"
    speakers: Optional[int] = None
    hf_token: Optional[str] = None
    api_key: Optional[str] = None
    display_name: str = ""


@app.get("/health")
def health():
    return {"status": "ready"}


@app.post("/jobs", status_code=201)
def create_job(req: JobRequest):
    job = Job(
        audio_path=req.audio_path,
        workspace_path=req.workspace_path,
        backend=req.backend,
        model=req.model,
        language=req.language,
        speakers=req.speakers,
        hf_token=req.hf_token,
        api_key=req.api_key,
        display_name=req.display_name,
    )
    with _lock:
        _jobs[job.job_id] = job
        _queue.append(job.job_id)
    _worker_event.set()
    return {"job_id": job.job_id, "status": "queued"}


@app.get("/jobs")
def list_jobs():
    with _lock:
        return [j.to_dict() for j in _jobs.values()]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    with _lock:
        j = _jobs.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return j.to_dict()


@app.delete("/jobs/{job_id}")
def cancel_job(job_id: str):
    global _current_proc

    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail="Job not found")

        if j.status == "queued":
            j.status = "cancelled"
            try:
                _queue.remove(job_id)
            except ValueError:
                pass
            return {"cancelled": True}

        if j.status == "running":
            j.status = "cancelled"
            proc = _current_proc
        else:
            return {"cancelled": False, "reason": f"Job is {j.status}"}

    if proc and proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)

    _worker_event.set()
    return {"cancelled": True}


@app.post("/queue/pause")
def pause_queue():
    global _queue_paused
    with _lock:
        _queue_paused = True
    return {"paused": True}


@app.post("/queue/resume")
def resume_queue():
    global _queue_paused
    with _lock:
        _queue_paused = False
    _worker_event.set()
    return {"paused": False}


@app.post("/shutdown")
def shutdown():
    def _stop():
        import time
        time.sleep(0.15)
        sys.exit(0)

    threading.Thread(target=_stop, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mp.freeze_support()

    parser = argparse.ArgumentParser(description="Diarized Transcriber REST server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()

    worker = threading.Thread(target=_worker_loop, daemon=True, name="dt-worker")
    worker.start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
