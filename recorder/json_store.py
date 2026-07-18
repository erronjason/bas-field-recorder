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


# ---------------------------------------------------------------------------
# Crash-fallback snapshot of GUI-owned fields
#
# The transcriber overwrites a record's .json with just its output. The queue
# normally restores name/notes/etc. from an in-memory snapshot, but that is
# lost if the app crashes mid-transcription. These helpers mirror the snapshot
# to a file in tmp/ (never records_dir/, so it is not mistaken for a record)
# so the fields survive a crash and can be restored on restart.
# ---------------------------------------------------------------------------

def gui_snapshot_path(audio_path: Path) -> Path:
    from . import user_data
    return user_data.tmp_dir() / f"{audio_path.stem}.gui.json"


def write_gui_snapshot(audio_path: Path, snapshot: dict) -> None:
    if not snapshot:
        return
    try:
        _write(gui_snapshot_path(audio_path), snapshot)
    except OSError as e:
        log.error("Could not write GUI snapshot for %s: %s", audio_path.name, e)


def read_gui_snapshot(audio_path: Path) -> dict:
    return load(gui_snapshot_path(audio_path))


def clear_gui_snapshot(audio_path: Path) -> None:
    try:
        gui_snapshot_path(audio_path).unlink(missing_ok=True)
    except OSError:
        pass


def reconcile_gui_snapshots() -> None:
    """Restore name/notes from crash-fallback snapshots left by an interrupted
    transcription, then remove the snapshot files.

    Called once at startup. Only records the transcriber already overwrote
    (server output present but GUI fields gone) are restored here; snapshots
    for records that still look like an untouched stub are left in place so a
    job that is still queued/running can restore them via the queue's
    re-attach path.
    """
    from . import user_data
    for snap_path in user_data.tmp_dir().glob("*.gui.json"):
        snapshot = load(snap_path)
        stem = snap_path.name[: -len(".gui.json")]
        json_path = user_data.records_dir() / f"{stem}.json"

        if not snapshot or not json_path.exists():
            # Empty snapshot, or the record was discarded/deleted — drop it.
            snap_path.unlink(missing_ok=True)
            continue

        data = load(json_path)
        # The server's overwrite drops record_id (and the other GUI fields); a
        # stub or an already-restored record still has it. That is the reliable
        # signal — the stub also carries an (empty) "segments" key, so segment
        # presence alone can't distinguish them.
        if "record_id" not in data:
            # Server overwrote the stub before we could restore — do it now.
            enrich_post_transcription(json_path, snapshot)
            snap_path.unlink(missing_ok=True)
        elif data.get("segments"):
            # Transcription finished and fields already restored — snapshot stale.
            snap_path.unlink(missing_ok=True)
        # else: pristine stub — a job may still be queued/running; leave the
        # snapshot so the queue's re-attach path can restore it on completion.


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
