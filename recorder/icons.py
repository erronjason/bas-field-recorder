"""Shared icon generation for Field Recorder.

All QIcon objects derive from the BAS three-line mark.
Identity use (window/taskbar): warm orange tones via bas_icon().
Tray state use: all bars change color together via tray_state_icon().
"""

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def _draw_mark(size: int, top: str, mid: str, bot: str) -> QIcon:
    """Render the BAS three-bar mark at the given size with explicit bar colors."""
    px = QPixmap(size, size)
    px.fill(QColor("transparent"))
    p = QPainter(px)
    bar_h = max(2, size * 2 // 22)
    gap   = max(3, size * 3 // 22)
    mid_w = int(size * 0.75)
    y0 = (size - 3 * bar_h - 2 * gap) // 2
    p.fillRect(0, y0,                  size,  bar_h, QColor(top))
    p.fillRect(0, y0 + bar_h + gap,   mid_w, bar_h, QColor(mid))
    p.fillRect(0, y0 + 2*(bar_h+gap), size,  bar_h, QColor(bot))
    p.end()
    return QIcon(px)


def bas_icon(size: int = 32) -> QIcon:
    """BAS identity mark — warm orange tones for window and taskbar."""
    return _draw_mark(size, "#BA581C", "#C9740E", "#F9EDD9")


_TRAY_COLORS: dict[str, tuple[str, str, str]] = {
    "idle":      ("#A39B90", "#A39B90", "#A39B90"),
    "recording": ("#E2761B", "#E2761B", "#E2761B"),
    "paused":    ("#6E665C", "#E2761B", "#6E665C"),
    "saving":    ("#A39B90", "#A39B90", "#A39B90"),
}


def tray_state_icon(state: str, size: int = 22) -> QIcon:
    """BAS mark colored by tray capture state (visual §6.1)."""
    t, m, b = _TRAY_COLORS.get(state, _TRAY_COLORS["idle"])
    return _draw_mark(size, t, m, b)


def bas_svg_path(name: str = "BAS-landscape") -> Path:
    """Resolve path to a docs/ SVG asset (works in dev and frozen builds)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "docs" / f"{name}.svg"  # type: ignore[attr-defined]
    return Path(__file__).parent.parent / "docs" / f"{name}.svg"
