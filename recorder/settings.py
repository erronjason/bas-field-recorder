"""Settings dataclass and SettingsWindow (all tabs)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import user_data
from .hotkeys import DEFAULT_HOTKEYS, ACTIONS, parse_hotkey

if TYPE_CHECKING:
    from .audio import AudioBackend
    from .server_manager import ServerManager


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    # Recording
    mic_device_index: Optional[int] = None
    loopback_device_index: Optional[int] = None

    # Transcription
    transcription_backend: str = "local"      # "local" | "cloud"
    model: str = "medium"
    language: str = "en"
    hf_token: str = ""
    assemblyai_api_key: str = ""
    auto_transcribe: bool = True
    cloud_consent_given: bool = False

    # Hotkeys
    hotkey_start_stop: str = DEFAULT_HOTKEYS["start_stop"]
    hotkey_pause_resume: str = DEFAULT_HOTKEYS["pause_resume"]
    hotkey_notes: str = DEFAULT_HOTKEYS["notes"]

    # Server (managed at runtime; saved so manager can re-use port)
    server_port: int = 7777

    # Data
    auto_delete_days: Optional[int] = None    # None = disabled

    @classmethod
    def load(cls) -> "Settings":
        path = user_data.settings_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f for f in cls.__dataclass_fields__}
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        user_data.settings_path().write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# SettingsWindow
# ---------------------------------------------------------------------------

class SettingsWindow(QDialog):
    """Multi-tab settings dialog.

    Signals
    -------
    hotkeys_changed()  — emitted when hotkey bindings are saved, so
                         SystemTrayApp can re-register them.
    reinstall_requested() — user clicked Reinstall Backend.
    """

    hotkeys_changed = Signal()
    reinstall_requested = Signal()

    def __init__(
        self,
        settings: Settings,
        backend: "AudioBackend",
        server: Optional["ServerManager"] = None,
        hotkey_conflicts: Optional[dict] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._backend = backend
        self._server = server
        self._hk_conflicts = hotkey_conflicts or {}

        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._make_recording_tab(), "Recording")
        tabs.addTab(self._make_transcription_tab(), "Transcription")
        tabs.addTab(self._make_hotkeys_tab(), "Hotkeys")
        tabs.addTab(self._make_server_tab(), "Service")
        tabs.addTab(self._make_data_tab(), "Data")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Recording tab
    # ------------------------------------------------------------------

    def _make_recording_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Audio Devices")
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)

        self._mic_combo = QComboBox()
        self._mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for d in self._backend.list_input_devices():
            self._mic_combo.addItem(d["name"], userData=int(d["index"]))
        _select_by_data(self._mic_combo, self._settings.mic_device_index)
        form.addRow("Microphone:", self._mic_combo)

        self._lb_combo = QComboBox()
        self._lb_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        lb_devices = self._backend.list_loopback_devices()
        for d in lb_devices:
            self._lb_combo.addItem(d["name"], userData=int(d["index"]))
        if not lb_devices:
            self._lb_combo.addItem("No loopback device found")
            self._lb_combo.setEnabled(False)
        else:
            _select_by_data(self._lb_combo, self._settings.loopback_device_index)
        form.addRow("Loopback (system audio):", self._lb_combo)

        note = QLabel(
            "Loopback captures all audio playing through your speakers or headphones."
        )
        note.setProperty("role", "metadata")
        note.setWordWrap(True)
        form.addRow(note)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Transcription tab
    # ------------------------------------------------------------------

    def _make_transcription_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Transcription")
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)

        self._backend_combo = QComboBox()
        self._backend_combo.addItem("On-device", "local")
        self._backend_combo.addItem("Off-device — AssemblyAI", "cloud")
        idx = self._backend_combo.findData(self._settings.transcription_backend)
        if idx >= 0:
            self._backend_combo.setCurrentIndex(idx)
        form.addRow("Backend:", self._backend_combo)

        self._model_combo = QComboBox()
        for m in ["tiny", "base", "small", "medium", "large"]:
            self._model_combo.addItem(m)
        self._model_combo.setCurrentText(self._settings.model)
        form.addRow("Whisper model (local):", self._model_combo)

        self._lang_edit = QLineEdit(self._settings.language)
        self._lang_edit.setPlaceholderText("en")
        form.addRow("Language code:", self._lang_edit)

        self._hf_edit = QLineEdit(self._settings.hf_token)
        self._hf_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._hf_edit.setPlaceholderText("hf_…")
        form.addRow("HuggingFace token (local):", self._hf_edit)

        self._aai_edit = QLineEdit(self._settings.assemblyai_api_key)
        self._aai_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._aai_edit.setPlaceholderText("assemblyai key…")
        form.addRow("AssemblyAI API key (cloud):", self._aai_edit)

        self._auto_cb = QCheckBox("Automatically transcribe after recording stops")
        self._auto_cb.setChecked(self._settings.auto_transcribe)
        form.addRow(self._auto_cb)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Hotkeys tab
    # ------------------------------------------------------------------

    def _make_hotkeys_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Global Hotkeys")
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)

        self._hk_edits: dict[str, QLineEdit] = {}
        self._hk_warnings: dict[str, QLabel] = {}

        bindings = {
            "start_stop": ("Start / Stop capture", self._settings.hotkey_start_stop),
            "pause_resume": ("Pause / Resume capture", self._settings.hotkey_pause_resume),
            "notes": ("Open notes panel", self._settings.hotkey_notes),
        }

        for action, (label, current) in bindings.items():
            edit = QLineEdit(current)
            edit.setPlaceholderText("e.g. Ctrl+Shift+R")
            self._hk_edits[action] = edit
            form.addRow(f"{label}:", edit)

            warn = QLabel("")
            warn.setProperty("role", "error")
            if action in self._hk_conflicts:
                warn.setText("Could not register — may conflict with another app")
            self._hk_warnings[action] = warn
            form.addRow("", warn)

        note = QLabel(
            "Use Ctrl, Shift, Alt as modifiers. Example: Ctrl+Shift+R\n"
            "Changes take effect after clicking OK."
        )
        note.setProperty("role", "metadata")
        note.setWordWrap(True)
        form.addRow(note)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Server tab
    # ------------------------------------------------------------------

    def _make_server_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Transcription Service")
        vbox = QVBoxLayout(group)
        vbox.setContentsMargins(12, 12, 12, 12)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._server_status_dot = QFrame()
        self._server_status_dot.setFixedSize(6, 6)
        self._server_status_dot.setProperty("role", "dot")
        self._server_status_dot.setProperty("state", "idle")
        self._server_status_label = QLabel("Unknown")
        status_row.addWidget(self._server_status_dot)
        status_row.addWidget(self._server_status_label)
        status_row.addStretch()
        vbox.addLayout(status_row)

        if self._server:
            self._refresh_server_status()

        reinstall_btn = QPushButton("Reinstall transcription service…")
        reinstall_btn.clicked.connect(self._on_reinstall)
        vbox.addWidget(reinstall_btn)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _refresh_server_status(self) -> None:
        if self._server and self._server.is_ready():
            self._set_dot_state("active")
            port = self._server.port or "?"
            self._server_status_label.setText(f"Ready (port {port})")
        else:
            self._set_dot_state("error")
            self._server_status_label.setText("Not running")

    def _set_dot_state(self, state: str) -> None:
        self._server_status_dot.setProperty("state", state)
        self._server_status_dot.style().unpolish(self._server_status_dot)
        self._server_status_dot.style().polish(self._server_status_dot)

    def _on_reinstall(self) -> None:
        self.reinstall_requested.emit()
        self.accept()

    # ------------------------------------------------------------------
    # Data tab
    # ------------------------------------------------------------------

    def _make_data_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        group = QGroupBox("Storage")
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)

        self._auto_delete_cb = QCheckBox("Retention policy: remove records older than")
        self._auto_delete_cb.setChecked(self._settings.auto_delete_days is not None)
        self._auto_delete_spin = QSpinBox()
        self._auto_delete_spin.setRange(1, 3650)
        self._auto_delete_spin.setSuffix(" days")
        self._auto_delete_spin.setValue(self._settings.auto_delete_days or 30)
        self._auto_delete_spin.setEnabled(self._auto_delete_cb.isChecked())
        self._auto_delete_cb.toggled.connect(self._auto_delete_spin.setEnabled)

        row = QHBoxLayout()
        row.addWidget(self._auto_delete_cb)
        row.addWidget(self._auto_delete_spin)
        row.addStretch()
        form.addRow(row)

        note = QLabel(
            "Records with a retention hold are excluded from the retention policy.\n"
            "Removed records are logged to deletions.log in the data directory."
        )
        note.setProperty("role", "metadata")
        note.setWordWrap(True)
        form.addRow(note)

        open_btn = QPushButton("Open Data Directory")
        open_btn.clicked.connect(self._open_data_dir)
        form.addRow(open_btn)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _open_data_dir(self) -> None:
        import os, subprocess
        path = str(user_data.app_data_root())
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self) -> None:
        s = self._settings

        # Recording
        s.mic_device_index = self._mic_combo.currentData()
        if self._lb_combo.isEnabled():
            s.loopback_device_index = self._lb_combo.currentData()

        # Transcription
        s.transcription_backend = self._backend_combo.currentData() or "local"
        s.model = self._model_combo.currentText()
        s.language = self._lang_edit.text().strip() or "en"
        s.hf_token = self._hf_edit.text().strip()
        s.assemblyai_api_key = self._aai_edit.text().strip()
        s.auto_transcribe = self._auto_cb.isChecked()

        # Hotkeys — validate first
        old_hk = {
            "start_stop": s.hotkey_start_stop,
            "pause_resume": s.hotkey_pause_resume,
            "notes": s.hotkey_notes,
        }
        new_hk = {action: self._hk_edits[action].text().strip() for action in ACTIONS}
        hk_changed = new_hk != old_hk

        for action in ACTIONS:
            spec = new_hk[action]
            try:
                parse_hotkey(spec)
                self._hk_warnings[action].setText("")
            except ValueError as exc:
                self._hk_warnings[action].setText(str(exc))
                return  # don't save if any hotkey is invalid

        s.hotkey_start_stop = new_hk["start_stop"]
        s.hotkey_pause_resume = new_hk["pause_resume"]
        s.hotkey_notes = new_hk["notes"]

        # Data
        s.auto_delete_days = (
            self._auto_delete_spin.value() if self._auto_delete_cb.isChecked() else None
        )

        s.save()

        if hk_changed:
            self.hotkeys_changed.emit()

        self.accept()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _select_by_data(combo: QComboBox, value) -> None:
    if value is None:
        return
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
