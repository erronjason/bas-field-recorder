"""BAS visual system: font resolution, palette, and QSS loader."""

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase, QPalette

log = logging.getLogger(__name__)


def _resolve(preferred: str, fallbacks: list[str]) -> str:
    families = set(QFontDatabase.families())
    for name in [preferred] + fallbacks:
        if name in families:
            if name != preferred:
                log.warning("Font '%s' not found; using '%s'", preferred, name)
            return name
    return fallbacks[-1]


def font_roles() -> dict[str, str]:
    return {
        "ui":       _resolve("Segoe UI", ["Helvetica Neue", "Arial"]),
        "section":  _resolve("Franklin Gothic Medium", ["Arial Narrow", "Arial"]),
        "measured": _resolve("Consolas", ["Courier New", "monospace"]),
    }


def bas_palette() -> QPalette:
    p = QPalette()
    ground = QColor("#171512")
    raised = QColor("#1F1C18")
    text   = QColor("#F9EDD9")
    muted  = QColor("#A39B90")
    accent = QColor("#C9740E")
    faint  = QColor("#6E665C")
    rule   = QColor("#2E2A25")

    p.setColor(QPalette.ColorRole.Window,          ground)
    p.setColor(QPalette.ColorRole.WindowText,      text)
    p.setColor(QPalette.ColorRole.Base,            raised)
    p.setColor(QPalette.ColorRole.AlternateBase,   ground)
    p.setColor(QPalette.ColorRole.Text,            text)
    p.setColor(QPalette.ColorRole.Button,          raised)
    p.setColor(QPalette.ColorRole.ButtonText,      text)
    p.setColor(QPalette.ColorRole.Highlight,       accent)
    p.setColor(QPalette.ColorRole.HighlightedText, text)
    p.setColor(QPalette.ColorRole.ToolTipBase,     raised)
    p.setColor(QPalette.ColorRole.ToolTipText,     text)
    p.setColor(QPalette.ColorRole.PlaceholderText, faint)
    p.setColor(QPalette.ColorRole.Shadow,          rule)
    p.setColor(QPalette.ColorRole.Dark,            rule)
    p.setColor(QPalette.ColorRole.Mid,             faint)
    p.setColor(QPalette.ColorRole.Midlight,        muted)
    p.setColor(QPalette.ColorRole.Light,           muted)

    dis = QPalette.ColorGroup.Disabled
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        p.setColor(dis, role, faint)

    return p


def load_qss() -> str:
    if getattr(sys, "frozen", False):
        path = Path(sys._MEIPASS) / "field_recorder.qss"  # type: ignore[attr-defined]
    else:
        path = Path(__file__).parent.parent / "field_recorder.qss"
    text = path.read_text(encoding="utf-8")
    roles = font_roles()
    text = text.replace('"Franklin Gothic Medium"', f'"{roles["section"]}"')
    text = text.replace('"Consolas"',               f'"{roles["measured"]}"')
    text = text.replace('"Segoe UI"',               f'"{roles["ui"]}"')
    return text
