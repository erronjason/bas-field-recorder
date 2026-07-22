"""BAS — MCP server over the bureau records store.

Serves records produced by any Bureau instrument: search, fetch, transcripts,
participants, import, and annotation. Read + import + annotate; there is no
delete tool, by design.

Local process, stdio transport, no network surface — consistent with the
instrument's position that recordings stay on the machine.

See docs/mcp_server_spec.md.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

# Import the instrument's own store helpers rather than reimplementing the
# schema. These modules are deliberately Qt-free.
sys.path.insert(0, str(Path(__file__).parent))

from recorder import audio_import, json_store, user_data  # noqa: E402
from recorder.transcript import MODES, build_transcript  # noqa: E402

mcp = FastMCP("bas-records")


# ---------------------------------------------------------------------------
# Store access
# ---------------------------------------------------------------------------

def _records_dir() -> Path:
    return user_data.records_dir()


def _iter_records():
    """Yield (json_path, data) for every readable record, newest first."""
    rows = []
    for json_path in _records_dir().glob("*.json"):
        data = json_store.load(json_path)
        if data:
            rows.append((json_path, data))
    rows.sort(key=lambda r: r[1].get("created_at") or "", reverse=True)
    return rows


def _find(record_id: str) -> tuple[Path, dict]:
    """Resolve a record_id to its file and contents. Raises if unknown."""
    if not record_id:
        raise ValueError("record_id is required.")
    for json_path, data in _iter_records():
        if data.get("record_id") == record_id:
            return json_path, data
    raise ValueError(f"No record with record_id {record_id!r}.")


def _summary(json_path: Path, data: dict) -> dict:
    """The shape search returns — no segments, so a broad search stays small."""
    return {
        "record_id": data.get("record_id", ""),
        "display_name": data.get("display_name") or json_path.stem,
        "created_at": data.get("created_at"),
        "duration_seconds": data.get("duration_seconds"),
        "transcribed": bool(data.get("segments")),
        "speakers_detected": data.get("speakers_detected"),
        "retain": bool(data.get("retain")),
        "source": (data.get("source") or {}).get("application"),
    }


def _parse_date(value: str, field: str) -> datetime:
    """Accept an ISO date or datetime; return an aware UTC datetime."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be ISO-8601 (e.g. 2026-07-18), got {value!r}.")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _created_at(data: dict) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(data.get("created_at") or "")
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _identities(data: dict) -> set:
    """Real identities on a record: participants plus named speakers."""
    out = set()
    for p in data.get("participants") or []:
        name = (p if isinstance(p, str) else p.get("name", "")).strip()
        if name:
            out.add(name)
    for name in (data.get("speaker_names") or {}).values():
        if isinstance(name, str) and name.strip():
            out.add(name.strip())
    return out


def _persist(json_path: Path, data: dict, **fields) -> None:
    """Write GUI-owned fields safely.

    The transcriber overwrites a record wholesale and restores GUI-owned fields
    from a snapshot taken when the job was enqueued. Writing the record without
    also updating that snapshot means an in-flight transcription silently
    reverts this edit. update_gui_snapshot() is a no-op when nothing is in
    flight, so this is always correct. See spec §5.
    """
    json_store.update_fields(json_path, **fields)
    audio = json_store.audio_path_for(json_path, data)
    if audio is not None:
        json_store.update_gui_snapshot(audio, **fields)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_records(
    query: str = "",
    from_date: str = "",
    to_date: str = "",
    speaker: str = "",
    limit: int = 50,
) -> list[dict]:
    """Search the records store. Start here, then drill down with get_record.

    Returns record summaries (no transcript segments), newest first.

    Args:
        query: Case-insensitive substring matched against the record name and
            its transcript text. Omit to match all records.
        from_date: Only records created on or after this ISO date (2026-07-01).
        to_date: Only records created on or before this ISO date.
        speaker: Only records where this person is a participant or a named
            speaker. Case-insensitive substring.
        limit: Maximum records to return (default 50).
    """
    start = _parse_date(from_date, "from_date") if from_date else None
    end = _parse_date(to_date, "to_date") if to_date else None
    q = query.strip().lower()
    who = speaker.strip().lower()

    results = []
    for json_path, data in _iter_records():
        if start or end:
            created = _created_at(data)
            if created is None:
                continue
            if start and created < start:
                continue
            if end and created > end:
                continue

        if who and not any(who in name.lower() for name in _identities(data)):
            continue

        if q:
            name = (data.get("display_name") or json_path.stem).lower()
            if q not in name:
                text = " ".join(
                    (seg.get("text") or "") for seg in (data.get("segments") or [])
                ).lower()
                if q not in text and q not in (data.get("notes") or "").lower():
                    continue

        results.append(_summary(json_path, data))
        if len(results) >= max(1, limit):
            break
    return results


@mcp.tool()
def get_record(record_id: str) -> dict:
    """Return a full record, including its transcript segments and notes.

    Args:
        record_id: The record's stable UUID, from search_records.
    """
    json_path, data = _find(record_id)
    audio = json_store.audio_path_for(json_path, data)
    out = dict(data)
    # Report where the audio actually is; the stored field may carry an
    # absolute path from the machine that transcribed it.
    out["audio_path"] = str(audio) if audio else None
    return out


@mcp.tool()
def get_transcript(record_id: str, mode: str = "timestamps") -> str:
    """Return a record's transcript as text.

    Returns an empty string when the record has not been transcribed yet —
    that is a state, not an error.

    Args:
        record_id: The record's stable UUID.
        mode: "timestamps" for one stamped line per segment, or "reading" for
            consecutive same-speaker segments merged into blocks.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}, got {mode!r}.")
    _, data = _find(record_id)
    return build_transcript(data, mode)


@mcp.tool()
def list_participants() -> list[dict]:
    """List everyone appearing across the records store, with record counts.

    Draws on both participants and named speakers. Unnamed diarization labels
    (Speaker 1, Speaker 2) are not identities and are excluded.
    """
    counts: dict[str, int] = {}
    for _, data in _iter_records():
        for name in _identities(data):
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": name, "record_count": n}
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# ---------------------------------------------------------------------------
# Write tools — import and annotate. No delete, by design.
# ---------------------------------------------------------------------------

@mcp.tool()
def import_audio(source_path: str, display_name: str = "") -> dict:
    """Import an existing audio file into the records store as a new record.

    The file is copied, not moved, and keeps its original format. Supported:
    .flac .wav .mp3 .m4a .mp4 .ogg .webm

    Args:
        source_path: Absolute path to the audio file to import.
        display_name: Name for the record. Defaults to the source filename.
    """
    source = Path(source_path).expanduser()
    try:
        source = source.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Cannot read {source_path!r}: {exc}")
    if not source.is_file():
        raise ValueError(f"{source_path!r} is not a file.")

    dest = audio_import.import_one(source)
    json_path = dest.with_suffix(".json")
    data = json_store.load(json_path)

    name = display_name.strip()
    if name:
        _persist(json_path, data, display_name=name)
        data = json_store.load(json_path)

    return _summary(json_path, data) | {"audio_path": str(dest)}


@mcp.tool()
def set_notes(record_id: str, notes: str) -> dict:
    """Replace a record's notes. Pass an empty string to clear them.

    Args:
        record_id: The record's stable UUID.
        notes: The new notes text, replacing whatever is there.
    """
    json_path, data = _find(record_id)
    _persist(json_path, data, notes=notes)
    return {"record_id": record_id, "notes": notes}


@mcp.tool()
def rename_record(record_id: str, display_name: str) -> dict:
    """Set a record's display name.

    Args:
        record_id: The record's stable UUID.
        display_name: The new name. Empty falls back to the date and time.
    """
    json_path, data = _find(record_id)
    name = display_name.strip()
    _persist(json_path, data, display_name=name)
    return {"record_id": record_id, "display_name": name}


@mcp.tool()
def set_retention_hold(record_id: str, retain: bool) -> dict:
    """Protect a record from the automatic retention policy, or release it.

    Args:
        record_id: The record's stable UUID.
        retain: True to hold the record indefinitely, False to release it.
    """
    json_path, data = _find(record_id)
    _persist(json_path, data, retain=bool(retain))
    return {"record_id": record_id, "retain": bool(retain)}


# ---------------------------------------------------------------------------
# Resources — application-controlled context
# ---------------------------------------------------------------------------

@mcp.resource("bas://records/{record_id}")
def record_resource(record_id: str) -> str:
    """A single record: metadata, notes, and transcript."""
    json_path, data = _find(record_id)
    name = data.get("display_name") or json_path.stem
    lines = [
        f"# {name}",
        "",
        f"record_id: {data.get('record_id', '')}",
        f"created_at: {data.get('created_at')}",
        f"duration_seconds: {data.get('duration_seconds')}",
        f"speakers_detected: {data.get('speakers_detected')}",
    ]
    if data.get("notes"):
        lines += ["", "## Notes", "", data["notes"]]
    transcript = build_transcript(data, "reading")
    lines += ["", "## Transcript", "", transcript or "(not transcribed yet)"]
    return "\n".join(lines)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
