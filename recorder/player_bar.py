"""PlayerBar — compact audio playback controls for the Records Viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl, Qt, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)


def _fmt_ms(ms: int) -> str:
    total = max(0, ms // 1000)
    m, s = divmod(total, 60)
    h, m2 = divmod(m, 60)
    if h:
        return f"{h}:{m2:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class PlayerBar(QWidget):
    """QMediaPlayer + seek slider + elapsed/total display.

    Load a file with load(); call release() before any delete or retranscribe
    to drop the file handle (QMediaPlayer holds FLAC open on Windows).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._audio_out.setVolume(1.0)

        self._total_ms: int = 0
        self._dragging: bool = False
        self._loaded: bool = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        self._play_btn = QPushButton("Play")
        self._play_btn.setFixedWidth(52)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self.toggle)
        layout.addWidget(self._play_btn)

        self._elapsed_lbl = QLabel("0:00")
        self._elapsed_lbl.setProperty("role", "measured")
        self._elapsed_lbl.setFixedWidth(40)
        self._elapsed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._elapsed_lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setEnabled(False)
        self._slider.setRange(0, 1000)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._slider, stretch=1)

        self._total_lbl = QLabel("0:00")
        self._total_lbl.setProperty("role", "measured")
        self._total_lbl.setFixedWidth(40)
        layout.addWidget(self._total_lbl)

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, flac_path: Path, duration_seconds: Optional[float]) -> None:
        """Load a FLAC file. duration_seconds comes from the JSON record (not the player)."""
        self.release()
        self._player.setSource(QUrl.fromLocalFile(str(flac_path)))
        self._total_ms = int((duration_seconds or 0) * 1000)
        self._total_lbl.setText(_fmt_ms(self._total_ms))
        self._elapsed_lbl.setText("0:00")
        self._slider.setValue(0)
        self._play_btn.setEnabled(True)
        self._slider.setEnabled(True)
        self._loaded = True

    def release(self) -> None:
        """Stop playback and release the file handle."""
        self._player.stop()
        self._player.setSource(QUrl())
        self._play_btn.setText("Play")
        self._play_btn.setEnabled(False)
        self._slider.setEnabled(False)
        self._slider.setValue(0)
        self._elapsed_lbl.setText("0:00")
        self._loaded = False

    def toggle(self) -> None:
        if not self._loaded:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def seek_relative(self, delta_ms: int) -> None:
        if not self._loaded or self._total_ms == 0:
            return
        new_pos = max(0, min(self._total_ms, self._player.position() + delta_ms))
        self._player.setPosition(new_pos)

    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_position_changed(self, pos_ms: int) -> None:
        self._elapsed_lbl.setText(_fmt_ms(pos_ms))
        if not self._dragging and self._total_ms > 0:
            self._slider.setValue(int(pos_ms * 1000 // self._total_ms))

    @Slot(QMediaPlayer.PlaybackState)
    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("Pause" if playing else "Play")

    @Slot()
    def _on_slider_pressed(self) -> None:
        self._dragging = True

    @Slot()
    def _on_slider_released(self) -> None:
        self._dragging = False
        if self._total_ms > 0:
            target = int(self._slider.value() * self._total_ms // 1000)
            self._player.setPosition(target)
