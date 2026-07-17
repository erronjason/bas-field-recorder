"""Shared icon generation for Field Recorder.

All QIcon objects derive from the BAS three-line mark.
Identity use (window/taskbar): warm orange tones via bas_icon().
Tray state use: all bars change color together via tray_state_icon().
"""

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def _draw_mark(size: int, top: str, mid: str, bot: str) -> QIcon:
    """Render the BAS three-bar mark at the given size with explicit bar colors.

    Geometry mirrors recorder/resources/brand/bas-icon.svg (32×32 reference):
      top bar:    y=0,    h=10, full width
      middle bar: y=11.5, h=9,  75% width
      bottom bar: y=22,   h=10, full width
    Bars fill the full canvas height — no vertical padding or centering.
    """
    px = QPixmap(size, size)
    px.fill(QColor("transparent"))
    p = QPainter(px)

    top_h = max(1, round(size * 10 / 32))
    gap   = max(1, round(size * 1.5 / 32))
    bot_h = top_h
    mid_h = max(1, size - 2 * top_h - 2 * gap)
    mid_w = size * 3 // 4
    y_mid = top_h + gap
    y_bot = y_mid + mid_h + gap

    p.fillRect(0, 0,     size,  top_h, QColor(top))
    p.fillRect(0, y_mid, mid_w, mid_h, QColor(mid))
    p.fillRect(0, y_bot, size,  bot_h, QColor(bot))
    p.end()
    return QIcon(px)


def bas_icon(size: int = 32) -> QIcon:
    """BAS identity mark — warm orange tones for window and taskbar."""
    return _draw_mark(size, "#BA581C", "#C9740E", "#F9EDD9")


_TRAY_COLORS: dict[str, tuple[str, str, str]] = {
    "idle":      ("#A39B90", "#A39B90", "#A39B90"),
    "recording": ("#C9740E", "#C9740E", "#C9740E"),
    "paused":    ("#6E665C", "#C9740E", "#6E665C"),
    "saving":    ("#BA581C", "#BA581C", "#BA581C"),
}


def tray_state_icon(state: str, size: int = 22) -> QIcon:
    """BAS mark colored by tray capture state (visual §6.1)."""
    t, m, b = _TRAY_COLORS.get(state, _TRAY_COLORS["idle"])
    return _draw_mark(size, t, m, b)


def bas_svg_path(name: str = "BAS-landscape") -> Path:
    """Resolve path to a brand SVG asset (works in dev and frozen builds)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "recorder" / "resources" / "brand" / f"{name}.svg"  # type: ignore[attr-defined]
    return Path(__file__).parent / "resources" / "brand" / f"{name}.svg"
