"""Transcript rendering from a record's segments.

Deliberately free of Qt so every reader of the store — the Records window and
the MCP server — renders a transcript identically. A transcript must not read
differently depending on which surface produced it.
"""

from __future__ import annotations

MODES = ("timestamps", "reading")


def speaker_label(raw: str, names: dict, index_map: dict) -> str:
    """Resolve a diarization label to a real identity, or Speaker N."""
    name = (names or {}).get(raw, "").strip()
    if name:
        return name
    idx = (index_map or {}).get(raw, 0)
    return f"Speaker {idx + 1}"


def build_transcript(data: dict, mode: str = "timestamps") -> str:
    """Render a record's segments as text.

    mode="timestamps" — one line per segment, stamped and speaker-labelled.
    mode="reading"    — consecutive same-speaker segments merged into blocks.

    Returns "" when the record has no segments (not yet transcribed).
    """
    segments = (data or {}).get("segments") or []
    names = (data or {}).get("speaker_names") or {}
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
            label = speaker_label(sp, names, index_map)
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
            label = speaker_label(sp, names, index_map)
            parts.append(f"{label.upper()}\n{''.join(texts)}")
        return "\n\n".join(parts)
