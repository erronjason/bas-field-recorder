import json
import sys
import threading
import wave
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from . import user_data


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------

class AudioBackend(ABC):
    @abstractmethod
    def get_default_mic(self) -> dict: ...

    @abstractmethod
    def get_default_loopback(self) -> Optional[dict]: ...

    @abstractmethod
    def list_input_devices(self) -> list: ...

    @abstractmethod
    def list_loopback_devices(self) -> list: ...

    @abstractmethod
    def open_mic_stream(self, device_info: dict, callback): ...

    @abstractmethod
    def open_loopback_stream(self, device_info: dict, callback): ...

    @abstractmethod
    def close_streams(self, *streams) -> None: ...

    @abstractmethod
    def terminate(self) -> None: ...


def get_audio_backend() -> AudioBackend:
    if sys.platform == "win32":
        from .backends.wasapi import WasapiBackend
        return WasapiBackend()
    elif sys.platform == "darwin":
        from .backends.coreaudio import CoreAudioBackend
        return CoreAudioBackend()
    else:
        from .backends.pulseaudio import PulseAudioBackend
        return PulseAudioBackend()


# ---------------------------------------------------------------------------
# Shared mixdown logic
# ---------------------------------------------------------------------------

def do_mixdown(tmp_path: Path, wav_path: Path) -> None:
    """Resample mic and loopback streams to 16 kHz mono, mix, write WAV.

    Reads *.raw + *.meta files written by RecorderThread.
    Raises on any failure — caller decides how to surface the error.
    """
    import scipy.signal  # imported here to avoid loading at startup

    mic_meta = json.loads((tmp_path / "mic.meta").read_text(encoding="utf-8"))
    lb_meta = json.loads((tmp_path / "loopback.meta").read_text(encoding="utf-8"))

    mic_raw = np.fromfile(tmp_path / "mic.raw", dtype=np.int16)
    lb_raw = np.fromfile(tmp_path / "loopback.raw", dtype=np.int16)

    mic = mic_raw.astype(np.float32) / 32768.0
    lb = lb_raw.astype(np.float32) / 32768.0

    # Downmix loopback to mono
    lb_channels = lb_meta["channels"]
    if lb_channels > 1 and len(lb) > 0:
        lb = lb.reshape(-1, lb_channels).mean(axis=1)

    # Resample both to 16 kHz
    target = 16000
    mic_rate = mic_meta["sample_rate"]
    lb_rate = lb_meta["sample_rate"]

    if len(mic) > 0 and mic_rate != target:
        mic = scipy.signal.resample_poly(mic, target, mic_rate)
    if len(lb) > 0 and lb_rate != target:
        lb = scipy.signal.resample_poly(lb, target, lb_rate)

    # Handle case where one stream is empty
    if len(mic) == 0 and len(lb) == 0:
        raise ValueError("No audio data was captured.")
    elif len(mic) == 0:
        mixed = lb
    elif len(lb) == 0:
        mixed = mic
    else:
        n = min(len(mic), len(lb))
        mixed = np.clip((mic[:n] + lb[:n]) * 0.5, -1.0, 1.0)

    mixed_i16 = (mixed * 32767.0).astype(np.int16)

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target)
        wf.writeframes(mixed_i16.tobytes())


# ---------------------------------------------------------------------------
# MixdownWorker — runs do_mixdown on a background QThread
# ---------------------------------------------------------------------------

class MixdownWorker(QThread):
    mixdown_complete = Signal(Path)
    mixdown_error = Signal(str)

    def __init__(self, tmp_path: Path, wav_path: Path, parent=None):
        super().__init__(parent)
        self._tmp_path = tmp_path
        self._wav_path = wav_path

    @property
    def tmp_path(self) -> Path:
        return self._tmp_path

    def run(self) -> None:
        try:
            do_mixdown(self._tmp_path, self._wav_path)
            self.mixdown_complete.emit(self._wav_path)
        except Exception as exc:
            self.mixdown_error.emit(str(exc))


# ---------------------------------------------------------------------------
# RecorderThread — disk-streaming dual capture
# ---------------------------------------------------------------------------

class RecorderThread(QThread):
    recording_started = Signal()
    recording_stopped = Signal(Path, str)   # tmp_path, notes_text
    error = Signal(str)

    def __init__(
        self,
        backend: AudioBackend,
        mic_info: dict,
        loopback_info: Optional[dict],
        parent=None,
    ):
        super().__init__(parent)
        self._backend = backend
        self._mic_info = mic_info
        self._loopback_info = loopback_info
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()   # set = paused
        self._notes_text = ""
        self._tmp_path: Optional[Path] = None

    @property
    def tmp_path(self) -> Optional[Path]:
        return self._tmp_path

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def stop(self, notes_text: str = "") -> None:
        self._notes_text = notes_text
        self._stop_event.set()

    def run(self) -> None:
        lb_info = self._loopback_info
        if lb_info is None:
            self.error.emit(
                "No loopback device found.\n"
                "Connect a speaker or headphones and try again."
            )
            return

        session_name = "recording_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_path = user_data.tmp_dir() / session_name
        tmp_path.mkdir(parents=True, exist_ok=True)
        self._tmp_path = tmp_path

        mic_info = self._mic_info
        mic_rate = int(mic_info["defaultSampleRate"])
        lb_rate = int(lb_info["defaultSampleRate"])
        lb_channels = int(lb_info["maxInputChannels"])

        # Write meta files first so crash recovery can identify valid sessions
        (tmp_path / "mic.meta").write_text(
            json.dumps({"sample_rate": mic_rate, "channels": 1}), encoding="utf-8"
        )
        (tmp_path / "loopback.meta").write_text(
            json.dumps({"sample_rate": lb_rate, "channels": lb_channels}),
            encoding="utf-8",
        )

        mic_file = open(tmp_path / "mic.raw", "ab")
        lb_file = open(tmp_path / "loopback.raw", "ab")

        stop_ev = self._stop_event
        pause_ev = self._pause_event

        def mic_callback(in_data, _frame_count, _time_info, _status):
            if not pause_ev.is_set() and not stop_ev.is_set():
                mic_file.write(in_data)
            import pyaudiowpatch as _pa
            return (None, _pa.paContinue)

        def lb_callback(in_data, _frame_count, _time_info, _status):
            if not pause_ev.is_set() and not stop_ev.is_set():
                lb_file.write(in_data)
            import pyaudiowpatch as _pa
            return (None, _pa.paContinue)

        try:
            mic_stream = self._backend.open_mic_stream(mic_info, mic_callback)
            lb_stream = self._backend.open_loopback_stream(lb_info, lb_callback)

            self.recording_started.emit()
            self._stop_event.wait()

            self._backend.close_streams(mic_stream, lb_stream)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            mic_file.close()
            lb_file.close()

        # Persist final notes text
        notes_path = tmp_path / "notes.txt"
        if self._notes_text:
            notes_path.write_text(self._notes_text, encoding="utf-8")

        self.recording_stopped.emit(tmp_path, self._notes_text)
