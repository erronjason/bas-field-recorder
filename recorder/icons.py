"""Shared icon generation for Field Recorder.

All QIcon objects derive from the BAS three-line mark. The bottom bar color
is the only thing that changes between identity use and tray state signals.
"""

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def bas_icon(size: int = 22, bottom_color: str = "#F9EDD9") -> QIcon:
    """BAS three-line mark as a QIcon.

    bottom_color: cream (#F9EDD9) for identity/window use;
                  state-specific accent for tray state icons.
    """
    px = QPixmap(size, size)
    px.fill(QColor("transparent"))
    p = QPainter(px)

    bar_h = max(3, size * 4 // 22)
    gap = max(2, size * 3 // 22)
    mid_w = int(size * 0.75)

    p.fillRect(0, 0, size, bar_h, QColor("#BA581C"))
    p.fillRect(0, bar_h + gap, mid_w, bar_h, QColor("#C9740E"))
    p.fillRect(0, (bar_h + gap) * 2, size, bar_h, QColor(bottom_color))
    p.end()

    return QIcon(px)


def bas_svg_path(name: str = "BAS-landscape") -> Path:
    """Resolve path to a docs/ SVG asset (works in dev and frozen builds)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "docs" / f"{name}.svg"  # type: ignore[attr-defined]
    return Path(__file__).parent.parent / "docs" / f"{name}.svg"
