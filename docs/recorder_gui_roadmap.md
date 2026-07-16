# Recorder GUI — Implementation Roadmap

## Overview

This document specifies the phased implementation of `recorder_gui` — a standalone system tray application that records both sides of any call (microphone + system audio), integrates with the existing `diarized_transcriber.py` pipeline, and surfaces a transcription log viewer. Initial target is Windows; the architecture is designed to extend to macOS and Linux without GUI changes.

---

## Goals

| Goal | Phase |
|---|---|
| Record mic + system audio from any call app (Zoom, Teams, Meet, etc.) | 1 |
| System tray presence — minimal footprint, always accessible | 1 |
| Output recordings directly to `workspace/` for transcription | 1 |
| Distributable Windows `.exe` binary | 1 |
| Auto-launch transcription after recording stops | 2 |
| In-tray status while transcription runs | 2 |
| Settings window (model, language, backend) | 2 |
| Transcription log viewer (browse, search, read past transcripts) | 3 |
| macOS and Linux audio backend support | 4 |

---

## Architecture

### Stack

| Layer | Technology | Rationale |
|---|---|---|
| GUI + system tray | PySide6 (Qt 6) | LGPL, cross-platform identical codebase, `QSystemTrayIcon` built in, full widget toolkit for log viewer |
| Audio — Windows | PyAudioWPatch 0.2.12.8 | WASAPI loopback + mic, Jan 2026 release, purpose-built for this use case |
| Audio — macOS (Phase 4) | SoundCard via CoreAudio | `include_loopback=True`; requires Screen & System Audio Recording TCC grant |
| Audio — Linux (Phase 4) | SoundCard via PulseAudio/PipeWire | Loopback source available natively |
| Audio mixing | NumPy | Already a transitive dependency of whisperX; float32 mixing and resampling |
| Binary packaging | PyInstaller | Battle-tested, simpler than Nuitka; Nuitka's compile time is impractical with PyTorch in the tree |
| Transcription backend | `diarized_transcriber.py` (existing) | Invoked as subprocess; keeps recorder and transcriber independently runnable |

### Process model

The GUI app is a **separate process** from the transcriber. After a recording ends, it spawns `diarized_transcriber.py` as a subprocess, streams its stdout/stderr into the GUI status area, and monitors exit code. This keeps:
- The transcriber usable standalone via CLI
- The GUI process lightweight during recording
- A clear boundary that survives future refactors of either component

```
recorder_gui.py
 ├── QApplication (PySide6)
 ├── SystemTrayApp
 │    ├── QSystemTrayIcon + QMenu
 │    ├── RecorderThread (PyAudioWPatch — two streams)
 │    ├── TranscriberWorker (QProcess wrapping diarized_transcriber.py)
 │    └── SettingsWindow (QDialog)
 └── LogViewerWindow (QMainWindow)
      ├── RecordingListPanel (QTableWidget — left)
      └── TranscriptPanel (QPlainTextEdit — right)
```

### Audio recording model

Two `pyaudiowpatch` streams run concurrently in a single background thread:

1. **Microphone stream** — default WASAPI input device, 16 kHz mono
2. **Loopback stream** — `get_default_wasapi_loopback()`, same sample rate as device (typically 48 kHz stereo), then resampled to 16 kHz mono via NumPy after capture

Chunks from both streams are appended to separate in-memory `bytearray` buffers. On stop, both buffers are decoded to `float32` NumPy arrays, resampled to 16 kHz, mixed (averaged), and written as a single 16 kHz mono WAV to `workspace/`. This is exactly the format `diarized_transcriber.py` already expects.

Sample rate note: WASAPI loopback devices report their own native rate (often 48 kHz). PyAudioWPatch's `get_default_wasapi_loopback()` returns the device info dict which includes `defaultSampleRate` — this must be used when opening the loopback stream, then resampled after capture.

---

## Directory Structure

```
diarized_transcriber/
├── diarized_transcriber.py       # existing CLI transcriber
├── recorder_gui.py               # new — entry point for the tray app
├── recorder/                     # new — recorder_gui internals
│   ├── __init__.py
│   ├── audio.py                  # RecorderThread, platform audio backends
│   ├── transcriber_worker.py     # QProcess wrapper for diarized_transcriber.py
│   ├── settings.py               # SettingsWindow (QDialog)
│   ├── log_viewer.py             # LogViewerWindow (QMainWindow)
│   ├── tray.py                   # SystemTrayApp
│   └── resources/
│       ├── icon_idle.png         # 64×64, shown when not recording
│       ├── icon_recording.png    # 64×64, shown while recording (red dot)
│       └── icon_transcribing.png # 64×64, shown while transcribing (spinner)
├── recorder_gui.spec             # PyInstaller spec file
├── workspace/                    # existing — audio I/O
├── docs/
│   ├── npu_backend_prd.md
│   └── recorder_gui_roadmap.md   # this file
└── requirements.txt              # updated per phase
```

---

## Phase 1 — Windows System Tray Recorder (MVP)

**Deliverable:** A packaged Windows `.exe` that lives in the system tray, records both sides of any call, and saves a WAV to `workspace/`.

### 1.1 — Project scaffolding

- Create `recorder/` package directory and all files listed above (stubs initially)
- Create `recorder_gui.py` entry point
- Create `recorder_gui.spec` PyInstaller spec
- Update `requirements.txt` with `PySide6` and `PyAudioWPatch`

### 1.2 — Tray icon and menu (`recorder/tray.py`)

Implement `SystemTrayApp(QSystemTrayIcon)`:

```
Menu structure:
  ● Diarized Transcriber        [disabled label / version]
  ─────────────────────────────
  ⏺  Start Recording
  ⏹  Stop Recording             [hidden until recording starts]
  ─────────────────────────────
  ⚙  Settings...
  📋  View Transcription Logs...
  ─────────────────────────────
  Quit
```

State machine:
- `IDLE` → `RECORDING` on Start
- `RECORDING` → `IDLE` on Stop (saves WAV, changes icon)
- Icon swaps between `icon_idle.png` and `icon_recording.png`
- Tray tooltip updates: `"Diarized Transcriber — Idle"` / `"Recording… 00:02:34"`

Key Qt requirement: `QApplication.setQuitOnLastWindowClosed(False)` — without this, the app exits when the first dialog closes.

### 1.3 — Audio capture (`recorder/audio.py`)

Implement `RecorderThread(QThread)` with signals:
- `started` — emitted when both streams are open and capturing
- `stopped(path: str)` — emitted with the saved WAV path
- `error(message: str)` — audio device errors

Core logic:

```python
# Pseudocode — both streams run in the same thread, interleaved via callback
import pyaudiowpatch as pyaudio
import numpy as np
import wave, io, threading

class RecorderThread(QThread):
    def run(self):
        p = pyaudio.PyAudio()

        loopback_info = p.get_default_wasapi_loopback()
        mic_info = p.get_default_wasapi_device(is_input=True)

        loopback_rate = int(loopback_info["defaultSampleRate"])
        mic_rate = int(mic_info["defaultSampleRate"])

        loopback_frames = []
        mic_frames = []

        def loopback_callback(in_data, *_):
            loopback_frames.append(in_data)
            return (None, pyaudio.paContinue)

        def mic_callback(in_data, *_):
            mic_frames.append(in_data)
            return (None, pyaudio.paContinue)

        loopback_stream = p.open(
            format=pyaudio.paInt16,
            channels=loopback_info["maxInputChannels"],
            rate=loopback_rate,
            input=True,
            input_device_index=loopback_info["index"],
            stream_callback=loopback_callback,
        )
        mic_stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=mic_rate,
            input=True,
            input_device_index=mic_info["index"],
            stream_callback=mic_callback,
        )

        # ... wait for self._stop_event ...

        # After stop: decode, resample to 16kHz mono, mix, write WAV
```

After both streams stop:
1. Concatenate raw bytes for each stream
2. `np.frombuffer(..., dtype=np.int16).astype(np.float32) / 32768.0`
3. Downmix loopback to mono if stereo: `loopback = loopback.reshape(-1, channels).mean(axis=1)`
4. Resample both to 16 kHz using `np.interp` or `scipy.signal.resample_poly`
5. Truncate to shortest length and mix: `mixed = np.clip((mic + loopback) * 0.5, -1.0, 1.0)`
6. Write 16-bit PCM WAV via `wave` module
7. Emit `stopped(output_path)`

Output filename format: `workspace/recording_YYYYMMDD_HHMMSS.wav`

### 1.4 — Settings window (`recorder/settings.py`)

Minimal `QDialog` — persisted to `~/.diarized_transcriber/settings.json`:

| Setting | Type | Default |
|---|---|---|
| Microphone device | dropdown (populated from WASAPI devices) | System default |
| Loopback device | dropdown (populated from WASAPI loopback devices) | System default speaker loopback |
| Output directory | path picker | `workspace/` |

Settings are loaded at startup and passed into `RecorderThread`.

### 1.5 — PyInstaller packaging (`recorder_gui.spec`)

Known issues to handle:
- **Do not invoke PyInstaller from a directory containing `__init__.py`** — causes DLL path corruption with PySide6 on Windows (Qt DLLs present but unresolvable at import time). Always invoke from project root.
- PySide6 binaries are large (~80–150 MB one-folder). Use `--onedir` (one-folder) not `--onefile` for faster startup; optionally wrap in an installer via NSIS or Inno Setup for distribution.
- Exclude unused Qt modules to reduce size: `--exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtWebEngine` etc.
- Icon must be `.ico` on Windows for the EXE resource: add an `icon_idle.ico` alongside the PNG.

Build command:
```
pyinstaller recorder_gui.spec --clean
```

Spec essentials:
```python
a = Analysis(
    ['recorder_gui.py'],
    datas=[('recorder/resources/*', 'recorder/resources')],
    hiddenimports=['pyaudiowpatch'],
)
```

### Phase 1 requirements additions

```
PySide6>=6.7.0
PyAudioWPatch>=0.2.12.8
numpy          # already present via whisperX
scipy          # for resample_poly (resampling audio streams)
```

---

## Phase 2 — Transcription Integration

**Deliverable:** After recording stops, the app offers to auto-transcribe and shows live progress in the tray menu / a progress dialog. Settings window gains transcriber options.

### 2.1 — TranscriberWorker (`recorder/transcriber_worker.py`)

Implement `TranscriberWorker(QObject)` using `QProcess`:

```python
class TranscriberWorker(QObject):
    progress = Signal(str)   # stdout lines
    finished = Signal(int)   # exit code
    error = Signal(str)

    def start(self, wav_path, settings):
        cmd = [sys.executable, "diarized_transcriber.py", wav_path,
               "--model", settings.model,
               "--language", settings.language,
               "--backend", settings.backend]
        self._proc = QProcess()
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.finished.connect(self.finished)
        self._proc.start(cmd[0], cmd[1:])
```

This resolves `diarized_transcriber.py` relative to the application path (works both in-dev and in the PyInstaller bundle where `sys._MEIPASS` applies).

### 2.2 — Post-recording dialog

When `RecorderThread` emits `stopped`, show a non-blocking tray notification:

```
"Recording saved (03:42). Transcribe now?"  [Transcribe] [Later]
```

If "Transcribe": launch `TranscriberWorker`, update tray icon to `icon_transcribing.png`, update tooltip to `"Transcribing… recording_20260715_143201.wav"`.

### 2.3 — Extended settings

Add to settings dialog:

| Setting | Type | Default |
|---|---|---|
| Auto-transcribe after recording | checkbox | off |
| Transcription backend | dropdown: local / cloud | local |
| Whisper model | dropdown: tiny/base/small/medium | small |
| Language | text (ISO 639-1) | en |
| HuggingFace token | password field | from `.env` |
| AssemblyAI key | password field | from `.env` |

Credentials entered via settings are written back to `.env` in the project directory, not stored in `settings.json`.

---

## Phase 3 — Transcription Log Viewer

**Deliverable:** A full window showing all past recordings and their transcripts, browseable and searchable.

### 3.1 — LogViewerWindow (`recorder/log_viewer.py`)

Two-panel `QMainWindow`:

**Left panel — Recording list** (`QTableWidget`):

| Column | Source |
|---|---|
| Date/Time | filename stem parsed |
| Duration | WAV header |
| Speakers | JSON `speakers_detected` |
| Status | presence of `.json` sidecar |

Sorted by date descending. Clicking a row loads the transcript in the right panel.

**Right panel — Transcript view** (`QPlainTextEdit`, read-only):

Renders the `.txt` output from `diarized_transcriber.py`. If only `.json` exists (no `.txt`), renders it on-the-fly from the JSON segments. Falls back to a "Not yet transcribed" placeholder with a "Transcribe Now" button.

**Toolbar:**
- Search bar (`QLineEdit`) — `QPlainTextEdit.find()` for in-transcript search
- "Open in Explorer" — `QDesktopServices.openUrl(QUrl.fromLocalFile(path))`
- "Copy All" — copies full transcript to clipboard
- "Delete" — removes WAV + JSON + TXT with confirmation dialog

### 3.2 — Persistence

Recordings are discovered at launch by scanning `workspace/` for `recording_*.wav`. The `.json` sidecar (written by `diarized_transcriber.py`) is the source of truth for transcript content and speaker count. No separate database.

---

## Phase 4 — Cross-Platform Audio Backends

**Deliverable:** The same `recorder_gui.py` entry point and all GUI code run unchanged on macOS and Linux. Only `recorder/audio.py` changes.

### 4.1 — Platform abstraction

Introduce an `AudioBackend` ABC in `recorder/audio.py`:

```python
class AudioBackend(ABC):
    @abstractmethod
    def list_input_devices(self) -> list[dict]: ...

    @abstractmethod
    def list_loopback_devices(self) -> list[dict]: ...

    @abstractmethod
    def open_streams(self, mic_device, loopback_device,
                     mic_cb, loopback_cb) -> tuple: ...

    @abstractmethod
    def close_streams(self, streams) -> None: ...
```

Factory function detects at import time:

```python
def get_audio_backend() -> AudioBackend:
    import sys
    if sys.platform == "win32":
        from .backends.wasapi import WasapiBackend
        return WasapiBackend()
    elif sys.platform == "darwin":
        from .backends.coreaudio import CoreAudioBackend
        return CoreAudioBackend()
    else:
        from .backends.pulseaudio import PulseAudioBackend
        return PulseAudioBackend()
```

`RecorderThread` receives a backend instance; knows nothing about the platform.

### 4.2 — macOS (CoreAudio via SoundCard)

Library: `soundcard` (`pip install soundcard`)

Loopback: `soundcard.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)`

**Critical macOS requirement:** System audio capture requires the user to grant **Screen & System Audio Recording** permission in System Settings → Privacy & Security. This is a macOS TCC grant — no entitlement required in the app bundle, but the binary must be approved by the user on first run. There is no code-signing workaround; the user must approve.

The app should detect missing permission and show a one-time dialog explaining this, then open the relevant System Settings pane via `QDesktopServices.openUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")`.

**No virtual audio driver needed.** Prior approaches (BlackHole, Soundflower) are unnecessary since macOS 13 ScreenCaptureKit handles system audio natively.

### 4.3 — Linux (PulseAudio / PipeWire)

Library: `soundcard` (PulseAudio backend, which also covers PipeWire via the PulseAudio compatibility layer)

Loopback: PulseAudio exposes a monitor source for each output sink (e.g., `alsa_output.pci-xxxx.analog-stereo.monitor`). SoundCard surfaces these as microphone devices when `include_loopback=True`.

No special permissions required on standard desktop Linux distributions.

### 4.4 — Binary builds per platform

PyInstaller must be run on each target OS — cross-compilation is not supported. Build matrix:

| Platform | Command | Output |
|---|---|---|
| Windows | `pyinstaller recorder_gui.spec` | `dist/recorder_gui/recorder_gui.exe` |
| macOS | `pyinstaller recorder_gui.spec` | `dist/recorder_gui.app` (bundle) |
| Linux | `pyinstaller recorder_gui.spec` | `dist/recorder_gui/recorder_gui` → wrap in AppImage |

Future: a GitHub Actions workflow that builds on each OS runner and publishes to GitHub Releases.

---

## Known Risks and Mitigations

### PyInstaller + PySide6 DLL failure on Windows
**Risk:** If PyInstaller is invoked from a directory containing `__init__.py`, Qt DLLs are present in the bundle but fail to load at runtime (`DLL load failed while importing QtWidgets`).  
**Mitigation:** Always invoke from the project root. Document in build instructions. Add a `Makefile` / `build.ps1` that enforces this.

### PyAudioWPatch loopback device absent
**Risk:** If the user has no audio output device active (e.g., HDMI disconnected) `get_default_wasapi_loopback()` returns `None`.  
**Mitigation:** `RecorderThread` checks for `None` before opening streams and emits `error("No loopback device found. Connect a speaker or headphone and retry.")`.

### macOS TCC — Screen & System Audio Recording
**Risk:** On macOS, `soundcard` loopback will silently return silence if the user hasn't granted the permission.  
**Mitigation:** Phase 4 macOS backend validates the permission before recording starts. If missing, surface a settings dialog with a direct link to System Settings and retry logic.

### Sample rate mismatch between mic and loopback
**Risk:** Mic may be 44.1 kHz, loopback 48 kHz. Naive byte-level mixing produces distortion.  
**Mitigation:** Both streams are captured at their native rates into separate buffers. Post-capture, `scipy.signal.resample_poly` resamples both to 16 kHz before mixing. This is done in the non-real-time post-processing step, so latency is not a concern.

### Binary size
**Risk:** PySide6 + PyAudioWPatch + NumPy + SciPy produces a one-folder bundle of ~120–180 MB.  
**Mitigation:** Exclude unused Qt modules in the `.spec` file. Accept the size as a one-time install. If size becomes a blocker, evaluate replacing SciPy's `resample_poly` with a pure-NumPy linear interpolation (sufficient quality for voice at 16 kHz).

### Thread safety in QProcess / QThread
**Risk:** Signals emitted from `RecorderThread` that update UI widgets directly will crash on some Qt versions.  
**Mitigation:** All UI updates from background threads go through signals connected with `Qt.QueuedConnection` (the default for cross-thread signal-slot connections in Qt). Never touch a widget directly from `RecorderThread.run()`.

---

## Dependencies by Phase

```
# Phase 1
PySide6>=6.7.0
PyAudioWPatch>=0.2.12.8
scipy>=1.13.0

# Phase 2 (no new deps — uses QProcess)

# Phase 3 (no new deps — uses Qt widgets)

# Phase 4 (macOS / Linux)
soundcard>=0.4.6   # macOS + Linux loopback
```

Full updated `requirements.txt` after Phase 1:
```
openai-whisper
whisperx
pyannote.audio
assemblyai
python-dotenv
PySide6>=6.7.0
PyAudioWPatch>=0.2.12.8
scipy>=1.13.0
```

---

## Implementation Sequence for Claude Code

This roadmap is intended to guide a Claude Code session to implement Phase 1 without human interaction, with Phase 2–4 following in subsequent sessions.

### Phase 1 execution order

1. Create directory `recorder/` and stub `__init__.py`
2. Create `recorder/resources/` — generate three placeholder 64×64 PNG icons (idle: grey circle, recording: red circle, transcribing: orange circle) using PySide6's `QPainter` at first run if files absent — avoids binary assets in repo
3. Implement `recorder/audio.py` — `RecorderThread` with PyAudioWPatch, in-memory buffers, post-capture mix-down, WAV write
4. Implement `recorder/settings.py` — `SettingsWindow` QDialog, `Settings` dataclass, JSON persistence to `~/.diarized_transcriber/settings.json`
5. Implement `recorder/tray.py` — `SystemTrayApp`, state machine (IDLE / RECORDING), wires `RecorderThread`
6. Implement `recorder_gui.py` — entry point: `QApplication`, instantiate `SystemTrayApp`, `app.exec()`
7. Manual smoke test: run `python recorder_gui.py`, verify tray icon appears, start/stop recording, verify WAV in `workspace/`
8. Create `recorder_gui.spec` and verify `pyinstaller recorder_gui.spec --clean` produces working `.exe`
9. Commit on `feat-recorder-gui` branch

### Phase 2 execution order (subsequent session)

1. Implement `recorder/transcriber_worker.py` (`QProcess` wrapper)
2. Wire post-recording prompt in `tray.py`
3. Add transcriber settings to `SettingsWindow`
4. Test end-to-end: record → auto-transcribe → JSON + TXT in `workspace/`

### Phase 3 execution order (subsequent session)

1. Implement `recorder/log_viewer.py` (`LogViewerWindow`)
2. Wire "View Logs" menu action in `tray.py`
3. Test: browse recordings, read transcript, search, delete

### Phase 4 execution order (requires macOS/Linux hardware)

1. Create `recorder/backends/` package with `wasapi.py`, `coreaudio.py`, `pulseaudio.py`
2. Refactor `audio.py` to `AudioBackend` ABC + factory
3. Implement macOS TCC permission check
4. Test on each platform
5. Build and verify binaries on each OS

---

## Success Criteria — Phase 1

- `python recorder_gui.py` starts without error on Windows with PySide6 installed
- Tray icon appears in Windows notification area
- Start Recording → Stop Recording records a WAV to `workspace/recording_YYYYMMDD_HHMMSS.wav`
- The WAV contains both sides: microphone (user's voice) and system audio (remote participants)
- The WAV is accepted by `diarized_transcriber.py` without modification
- `pyinstaller recorder_gui.spec --clean` produces a `dist/recorder_gui/` folder with a working `recorder_gui.exe`
- The `.exe` runs on a machine without Python installed
