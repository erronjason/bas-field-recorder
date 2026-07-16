import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import user_data
from .audio import AudioBackend


@dataclass
class Settings:
    mic_device_index: Optional[int] = None
    loopback_device_index: Optional[int] = None

    @classmethod
    def load(cls) -> "Settings":
        path = user_data.settings_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        path = user_data.settings_path()
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


class SettingsWindow(QDialog):
    def __init__(self, settings: Settings, backend: AudioBackend, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._backend = backend

        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._make_recording_tab(), "Recording")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _make_recording_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        group = QGroupBox("Audio Devices")
        form = QFormLayout(group)
        form.setContentsMargins(12, 12, 12, 12)

        # Mic combo
        self._mic_combo = QComboBox()
        self._mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        mic_devices = self._backend.list_input_devices()
        self._mic_devices = mic_devices
        for d in mic_devices:
            self._mic_combo.addItem(d["name"], userData=int(d["index"]))
        _select_index(self._mic_combo, self._settings.mic_device_index)
        form.addRow("Microphone:", self._mic_combo)

        # Loopback combo
        self._lb_combo = QComboBox()
        self._lb_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        lb_devices = self._backend.list_loopback_devices()
        self._lb_devices = lb_devices
        for d in lb_devices:
            self._lb_combo.addItem(d["name"], userData=int(d["index"]))
        if not lb_devices:
            self._lb_combo.addItem("No loopback device found")
            self._lb_combo.setEnabled(False)
        else:
            _select_index(self._lb_combo, self._settings.loopback_device_index)
        form.addRow("Loopback (system audio):", self._lb_combo)

        note = QLabel(
            "Loopback captures all audio playing through your speakers or headphones.\n"
            "If no loopback device appears, connect an audio output device and reopen Settings."
        )
        note.setStyleSheet("color: gray; font-size: 11px;")
        note.setWordWrap(True)
        form.addRow(note)

        layout.addWidget(group)
        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _accept(self) -> None:
        mic_idx = self._mic_combo.currentData()
        self._settings.mic_device_index = mic_idx if mic_idx is not None else None

        if self._lb_combo.isEnabled():
            lb_idx = self._lb_combo.currentData()
            self._settings.loopback_device_index = lb_idx if lb_idx is not None else None

        self._settings.save()
        self.accept()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _select_index(combo: QComboBox, device_index: Optional[int]) -> None:
    if device_index is None:
        return
    for i in range(combo.count()):
        if combo.itemData(i) == device_index:
            combo.setCurrentIndex(i)
            return
