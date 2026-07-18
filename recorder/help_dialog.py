"""HelpDialog — how to use Field Recorder.

Shown once on first run and any time from the "?" link in the Records header.
BAS-styled: section labels, hairline rules, measured key combos — no new QSS.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setProperty("role", "section")
    return lbl


def _rule() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setProperty("role", "rule")
    return f


def _line(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "metadata")
    lbl.setWordWrap(True)
    return lbl


class HelpDialog(QDialog):
    """Modal usage guide. Reflects the current hotkey bindings.

    Parameters
    ----------
    hotkeys : dict with keys ``start_stop``, ``notes``, ``pause_resume``.
    open_settings : optional callback to open the Settings dialog; the
        "Change hotkeys" button is hidden when it is None.
    """

    def __init__(
        self,
        hotkeys: dict,
        open_settings: Optional[Callable] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._open_settings = open_settings

        self.setWindowTitle("How to use Field Recorder")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        start_stop = hotkeys.get("start_stop", "the record hotkey")

        # ── Getting started ───────────────────────────────────────────
        layout.addWidget(_section_label("Getting started"))
        layout.addWidget(_rule())
        layout.addWidget(_line(
            "Field Recorder captures your microphone and your system audio together, "
            "then transcribes the recording into a searchable record."
        ))
        layout.addWidget(_line(
            f"1.  Press {start_stop}, or single-click the tray icon, to start capturing."
        ))
        layout.addWidget(_line(
            "2.  Press it again to stop, then name the record and add notes."
        ))
        layout.addWidget(_line(
            "3.  It transcribes on its own. Double-click the tray icon to open Records "
            "and read or play it."
        ))

        # ── Global hotkeys ────────────────────────────────────────────
        layout.addWidget(_section_label("Global hotkeys"))
        layout.addWidget(_rule())
        layout.addWidget(_line("Work from any application, even when Field Recorder is in the tray."))

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        rows = [
            ("Record / stop capture", hotkeys.get("start_stop", "")),
            ("Take notes", hotkeys.get("notes", "")),
            ("Pause / resume capture", hotkeys.get("pause_resume", "")),
        ]
        for r, (label, combo) in enumerate(rows):
            name = QLabel(label)
            name.setProperty("role", "metadata")
            key = QLabel(combo)
            key.setProperty("role", "measured")
            grid.addWidget(name, r, 0)
            grid.addWidget(key, r, 1)
        grid.setColumnStretch(0, 1)
        grid_wrap = QWidget()
        grid_wrap.setLayout(grid)
        layout.addWidget(grid_wrap)

        # ── Tray icon ─────────────────────────────────────────────────
        layout.addWidget(_section_label("Tray icon"))
        layout.addWidget(_rule())
        layout.addWidget(_line("Single-click the tray icon to start or stop capture."))
        layout.addWidget(_line("Double-click the tray icon to open Records."))

        # ── Actions ───────────────────────────────────────────────────
        actions = QHBoxLayout()
        if self._open_settings is not None:
            remap_btn = QPushButton("Change hotkeys in Settings…")
            remap_btn.clicked.connect(self._on_remap)
            actions.addWidget(remap_btn)
        actions.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    def _on_remap(self) -> None:
        if self._open_settings is not None:
            self._open_settings()
