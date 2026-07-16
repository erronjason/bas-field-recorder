import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def create_stub(wav_path: Path, display_name: str, notes: str) -> Path:
    """Write the initial JSON sidecar immediately after recording stops.

    Called before transcription runs. The transcriber overwrites this file
    with its output; enrich_post_transcription() restores the GUI-managed fields.
    """
    json_path = wav_path.with_suffix(".json")

    duration_seconds = None
    if wav_path.exists():
        try:
            import soundfile as sf
            duration_seconds = sf.info(str(wav_path)).duration
        except Exception:
            pass

    data = {
        "record_id": str(uuid.uuid4()),
        "format_revision": 1,
        "display_name": display_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio_file": wav_path.name,
        "source": {
            "application": "Field Recorder",
            "meeting_title": None,
            "call_direction": None,
            "counterparty": None,
        },
        "duration_seconds": duration_seconds,
        "participants": [],
        "backend": None,
        "speakers_detected": None,
        "speaker_names": {},
        "notes": notes,
        "retain": False,
        "segments": [],
    }
    _write(json_path, data)
    return json_path


def load(json_path: Path) -> dict:
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return {}


def save(json_path: Path, data: dict) -> None:
    _write(json_path, data)


def update_fields(json_path: Path, **fields) -> None:
    """Merge specific fields into an existing JSON file."""
    data = load(json_path)
    data.update(fields)
    _write(json_path, data)


def enrich_post_transcription(json_path: Path, saved_gui_fields: dict) -> None:
    """Merge transcriber output with GUI-managed fields after transcription.

    saved_gui_fields should contain the values read from the stub before
    the transcriber overwrote it.
    """
    data = load(json_path)
    gui_defaults = {
        "record_id": None,
        "format_revision": 1,
        "display_name": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "application": "Field Recorder",
            "meeting_title": None,
            "call_direction": None,
            "counterparty": None,
        },
        "duration_seconds": None,
        "participants": [],
        "speaker_names": {},
        "notes": "",
        "retain": False,
    }
    gui_defaults.update(saved_gui_fields)
    data.update(gui_defaults)
    _write(json_path, data)


def migrate_existing_records() -> None:
    """Backfill record_id and duration_seconds on pre-Revision-1 records."""
    from . import user_data
    for json_path in user_data.records_dir().glob("*.json"):
        data = load(json_path)
        if not data:
            continue
        changed = False
        if not data.get("record_id"):
            data["record_id"] = str(uuid.uuid4())
            changed = True
        if data.get("duration_seconds") is None:
            flac = json_path.with_suffix(".flac")
            if flac.exists():
                try:
                    import soundfile as sf
                    data["duration_seconds"] = sf.info(str(flac)).duration
                    changed = True
                except Exception:
                    pass
        if changed:
            _write(json_path, data)


def run_retention_sweep(auto_delete_days: int, exclude_record_ids: set) -> None:
    """Delete records older than auto_delete_days, skipping retained and open records."""
    from . import user_data
    cutoff = datetime.now(timezone.utc) - timedelta(days=auto_delete_days)
    log_path = user_data.deletions_log_path()
    for json_path in list(user_data.records_dir().glob("*.json")):
        data = load(json_path)
        if not data:
            continue
        if data.get("retain"):
            continue
        record_id = data.get("record_id", "")
        if record_id in exclude_record_ids:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"DEFERRED {record_id} {json_path.name} — loaded in viewer\n")
            continue
        created_str = data.get("created_at", "")
        try:
            created = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            continue
        if created < cutoff:
            flac = json_path.with_suffix(".flac")
            txt  = json_path.with_suffix(".txt")
            try:
                if flac.exists():
                    flac.unlink()
                json_path.unlink(missing_ok=True)
                txt.unlink(missing_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"DELETED {record_id} {json_path.name}\n")
            except OSError as e:
                log.error("Retention sweep could not delete %s: %s", json_path.name, e)


def _write(json_path: Path, data: dict) -> None:
    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
