"""Global hotkey registration via Win32 RegisterHotKey.

A background thread owns a message-only window and runs GetMessage so
WM_HOTKEY events are received regardless of which window has focus.
Qt signals are emitted cross-thread (auto-connection queues them safely).

Non-Windows platforms: HotkeyManager still exists but is a no-op so
Phase 1 / Phase 4 code doesn't need platform guards.
"""

from __future__ import annotations

import sys
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal

# ---------------------------------------------------------------------------
# VKey table (letters + digits + function keys used by default bindings)
# ---------------------------------------------------------------------------

_VK: dict[str, int] = {
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},      # A-Z → 0x41-0x5A
    **{str(d): 0x30 + d for d in range(10)},                   # 0-9
    **{f"F{n}": 0x6F + n for n in range(1, 13)},               # F1-F12
    "SPACE": 0x20, "TAB": 0x09, "RETURN": 0x0D, "ESC": 0x1B,
    "BACK": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
}

_MOD_MAP = {"CTRL": 0x0002, "SHIFT": 0x0004, "ALT": 0x0001, "WIN": 0x0008}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012

ACTIONS = ("start_stop", "pause_resume", "notes")
DEFAULT_HOTKEYS: dict[str, str] = {
    "start_stop": "Ctrl+Shift+R",
    "pause_resume": "Ctrl+Shift+P",
    "notes": "Ctrl+Shift+N",
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse "Ctrl+Shift+R" into (modifiers, vkey).

    Raises ValueError for unrecognised keys.
    """
    parts = [p.strip().upper() for p in spec.split("+")]
    mods = _MOD_NOREPEAT
    vkey: Optional[int] = None
    for part in parts:
        if part in _MOD_MAP:
            mods |= _MOD_MAP[part]
        elif part in _VK:
            vkey = _VK[part]
        else:
            raise ValueError(f"Unknown key token '{part}' in hotkey spec '{spec}'")
    if vkey is None:
        raise ValueError(f"No non-modifier key found in '{spec}'")
    return mods, vkey


# ---------------------------------------------------------------------------
# HotkeyManager
# ---------------------------------------------------------------------------

class HotkeyManager(QObject):
    """Register / unregister global hotkeys and emit signals on activation.

    Usage
    -----
    mgr = HotkeyManager()
    mgr.start_stop_triggered.connect(tray._start_or_stop)
    conflicts = mgr.register(settings.hotkey_start_stop, settings.hotkey_pause_resume, settings.hotkey_notes)
    # conflicts: dict mapping action → spec for any that failed to register
    """

    start_stop_triggered = Signal()
    pause_resume_triggered = Signal()
    notes_triggered = Signal()
    # Emitted with (action_name, hotkey_spec) for each registration failure
    conflict_detected = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._registered: dict[int, str] = {}   # hotkey_id → action
        self._conflicts: dict[str, str] = {}    # action → spec

    def register(
        self,
        start_stop: str = DEFAULT_HOTKEYS["start_stop"],
        pause_resume: str = DEFAULT_HOTKEYS["pause_resume"],
        notes: str = DEFAULT_HOTKEYS["notes"],
    ) -> dict[str, str]:
        """Register hotkeys; returns mapping of action → spec for conflicts."""
        self.unregister()

        if sys.platform != "win32":
            return {}

        specs = {
            "start_stop": start_stop,
            "pause_resume": pause_resume,
            "notes": notes,
        }
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._message_loop,
            args=(specs, ready),
            daemon=True,
            name="hotkey-loop",
        )
        self._thread.start()
        ready.wait(timeout=3)
        return dict(self._conflicts)

    def unregister(self) -> None:
        if self._thread_id:
            _post_quit(self._thread_id)
        self._thread_id = 0
        self._registered.clear()
        self._conflicts.clear()
        self._thread = None

    def conflicts(self) -> dict[str, str]:
        return dict(self._conflicts)

    # ------------------------------------------------------------------
    # Background message loop (Win32 only)
    # ------------------------------------------------------------------

    def _message_loop(self, specs: dict[str, str], ready: threading.Event) -> None:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        self._thread_id = kernel32.GetCurrentThreadId()
        self._conflicts.clear()

        for hid, (action, spec) in enumerate(specs.items(), start=1):
            try:
                mods, vkey = parse_hotkey(spec)
            except ValueError:
                self._conflicts[action] = spec
                self.conflict_detected.emit(action, spec)
                continue

            if not user32.RegisterHotKey(None, hid, mods, vkey):
                self._conflicts[action] = spec
                self.conflict_detected.emit(action, spec)
            else:
                self._registered[hid] = action

        ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY:
                action = self._registered.get(msg.wParam)
                if action == "start_stop":
                    self.start_stop_triggered.emit()
                elif action == "pause_resume":
                    self.pause_resume_triggered.emit()
                elif action == "notes":
                    self.notes_triggered.emit()

        for hid in self._registered:
            user32.UnregisterHotKey(None, hid)
        self._registered.clear()
        self._thread_id = 0


def _post_quit(thread_id: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.PostThreadMessageW(thread_id, _WM_QUIT, 0, 0)
    except Exception:
        pass
