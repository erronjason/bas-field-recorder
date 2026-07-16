import json
from datetime import datetime, timezone
from pathlib import Path


def create_stub(wav_path: Path, display_name: str, notes: str) -> Path:
    """Write the initial JSON sidecar immediately after recording stops.

    Called before transcription runs.  The transcriber will overwrite this file
    with its output; enrich_post_transcription() restores the GUI-managed fields.
    """
    json_path = wav_path.with_suffix(".json")
    data = {
        "display_name": display_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio_file": wav_path.name,
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

    saved_gui_fields should contain the values read from the stub *before*
    the transcriber overwrote it: display_name, created_at, speaker_names,
    notes, retain.
    """
    data = load(json_path)
    # Restore GUI fields, initialising speaker_names if transcriber didn't set it
    gui_defaults = {
        "display_name": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "speaker_names": {},
        "notes": "",
        "retain": False,
    }
    gui_defaults.update(saved_gui_fields)
    data.update(gui_defaults)
    _write(json_path, data)


def _write(json_path: Path, data: dict) -> None:
    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
