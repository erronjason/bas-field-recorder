# Recorder GUI — Implementation Roadmap

## Overview

`recorder_gui` is a standalone system tray application that records both sides of any call (microphone + system audio), integrates with the existing `diarized_transcriber.py` pipeline, and surfaces a full recording management UI: playback, speaker naming, timestamped transcripts, live notes, and a browseable history. Initial target is Windows; the architecture is designed to extend to macOS and Linux without changing any GUI code.

---

## Goals

| Goal | Phase |
|---|---|
| Record mic + system audio from any call app (Zoom, Teams, Meet, etc.) | 1 |
| Pause and resume recording mid-call | 1 |
| Take live notes during a recording | 1 |
| System tray presence — minimal footprint, always accessible | 1 |
| Output recordings directly to `workspace/` | 1 |
| Distributable Windows `.exe` binary | 1 |
| Auto-transcribe after recording stops | 2 |
| Persist live notes and initialize speaker name map in JSON after transcription | 2 |
| In-tray status while transcription runs | 2 |
| Settings window (model, language, backend, devices) | 2 |
| Recording list with per-recording detail panel | 3 |
| Transcribe / Retranscribe per recording | 3 |
| Name speakers (map SPEAKER_XX → real names, re-render transcript) | 3 |
| Audio playback with seek | 3 |
| Transcript view toggle: reading mode vs. timestamped-segment mode | 3 |
| Copy full transcript to clipboard | 3 |
| Edit notes per recording (post-call) | 3 |
| macOS and Linux audio backend support | 4 |

---

## Architecture

### Stack

| Layer | Technology | Rationale |
|---|---|---|
| GUI + system tray | PySide6 (Qt 6, LGPL) | `QSystemTrayIcon` built in; identical code on all platforms; full widget toolkit for detail panel |
| Audio playback | `PySide6.QtMultimedia` | `QMediaPlayer` plays WAV natively on Windows via Windows Media Foundation; included in PySide6 package |
| Audio capture — Windows | PyAudioWPatch 0.2.12.8 | WASAPI loopback + mic; Jan 2026 release; purpose-built for this |
| Audio capture — macOS (Phase 4) | SoundCard via CoreAudio | `include_loopback=True`; requires Screen & System Audio Recording TCC grant |
| Audio capture — Linux (Phase 4) | SoundCard via PulseAudio/PipeWire | Monitor source loopback, no special permissions needed |
| Audio mixing | NumPy + SciPy | `resample_poly` for post-capture resampling; NumPy already in tree via whisperX |
| Binary packaging | PyInstaller | Battle-tested; Nuitka's compile time is impractical with PyTorch in the dependency tree |
| Transcription | `diarized_transcriber.py` (existing) | Invoked as `QProcess` subprocess; CLI stays independently usable |

### JSON schema extension

`diarized_transcriber.py` produces the base JSON. The GUI **enriches** it in-place after transcription by adding two optional top-level fields if absent. Neither field requires changes to `diarized_transcriber.py`.

```json
{
  "backend": "local",
  "audio_file": "recording_20260715_143201.wav",
  "speakers_detected": 2,
  "speaker_names": {
    "SPEAKER_00": "Jason",
    "SPEAKER_01": "Teddy"
  },
  "notes": "Key points:\n- Follow up on budget by Friday\n- Teddy to send revised proposal",
  "segments": [
    { "speaker": "SPEAKER_00", "start": 0.00, "end": 4.12, "text": "Hey, good to connect." }
  ]
}
```

- `speaker_names` — initialized to `{}` by the GUI after first transcription. Updated when the user names speakers.
- `notes` — initialized to `""`. Written from the live notes panel on recording stop; editable post-call in the detail panel.
- Existing JSON files without these fields are backward-compatible — the GUI treats absent fields as empty.

The `.txt` rendering layer reads `speaker_names` and substitutes real names if present: `SPEAKER_00` → `Jason`.

### Process model

```
recorder_gui.py
 ├── QApplication
 ├── SystemTrayApp (QSystemTrayIcon)
 │    ├── RecorderThread (QThread — PyAudioWPatch two-stream capture)
 │    ├── NotesWindow (QWidget — floating, shown during RECORDING/PAUSED)
 │    ├── TranscriberWorker (QProcess — wraps diarized_transcriber.py)
 │    └── SettingsWindow (QDialog)
 └── RecordingsWindow (QMainWindow)
      ├── RecordingListPanel (QTableWidget — left)
      └── RecordingDetailPanel (QWidget — right)
           ├── PlayerBar (QMediaPlayer + QSlider + time labels)
           ├── ActionToolbar (Transcribe, Retranscribe, Name Speakers, Copy, Delete)
           ├── TranscriptView (QPlainTextEdit — toggles between modes)
           └── NotesPanel (QPlainTextEdit — editable, saved to JSON)
```

### Recording state machine

```
              ┌─────────────────────┐
              ▼                     │
  IDLE ──► RECORDING ──► PAUSED ───┘
              │               │
              └───────────────┴──► SAVING ──► IDLE
                    (stop from either state)
```

| State | Tray icon | Tooltip |
|---|---|---|
| IDLE | Grey circle | "Diarized Transcriber — Idle" |
| RECORDING | Red circle | "Recording… 00:02:34" |
| PAUSED | Orange circle | "Paused — 00:02:34" |
| SAVING | Orange pulse | "Saving recording…" |
| TRANSCRIBING | Spinner | "Transcribing… recording_….wav" |

### Audio recording model

Two PyAudioWPatch callback streams run concurrently in `RecorderThread`:

1. **Microphone** — default WASAPI input, captured at device native rate (often 44.1 or 48 kHz), 1 channel
2. **Loopback** — `get_default_wasapi_loopback()`, captured at device native rate (typically 48 kHz), 2 channels

Both streams append chunks to separate in-memory `bytearray` buffers. On pause, a `_paused` flag causes callbacks to discard incoming chunks (streams stay open to avoid re-init latency on resume). On stop:

1. `np.frombuffer(..., dtype=np.int16).astype(np.float32) / 32768.0` for each buffer
2. Downmix loopback to mono: `loopback = loopback.reshape(-1, channels).mean(axis=1)`
3. `scipy.signal.resample_poly` — resample both to 16 kHz
4. Truncate to shortest, mix: `mixed = np.clip((mic + loopback) * 0.5, -1.0, 1.0)`
5. Write 16-bit PCM WAV via `wave` module to `workspace/recording_YYYYMMDD_HHMMSS.wav`
6. Emit `stopped(wav_path, notes_text)`

---

## Directory Structure

```
diarized_transcriber/
├── diarized_transcriber.py           # existing CLI transcriber (unchanged)
├── recorder_gui.py                   # entry point for the tray app
├── recorder/
│   ├── __init__.py
│   ├── audio.py                      # RecorderThread + AudioBackend ABC
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── wasapi.py                 # Windows (Phase 1)
│   │   ├── coreaudio.py              # macOS (Phase 4)
│   │   └── pulseaudio.py             # Linux (Phase 4)
│   ├── transcriber_worker.py         # QProcess wrapper for diarized_transcriber.py
│   ├── json_store.py                 # read/write/enrich JSON sidecars
│   ├── tray.py                       # SystemTrayApp + state machine
│   ├── notes_window.py               # floating notes panel (during recording)
│   ├── settings.py                   # SettingsWindow (QDialog)
│   ├── recordings_window.py          # RecordingsWindow (QMainWindow)
│   ├── recording_list.py             # RecordingListPanel (QTableWidget)
│   ├── recording_detail.py           # RecordingDetailPanel
│   ├── player_bar.py                 # PlayerBar (QMediaPlayer + controls)
│   ├── speaker_dialog.py             # SpeakerNameDialog (QDialog)
│   └── resources/
│       ├── icon_idle.png             # 64×64
│       ├── icon_recording.png        # 64×64, red circle
│       ├── icon_paused.png           # 64×64, orange circle
│       └── icon_transcribing.png     # 64×64, orange animated
├── recorder_gui.spec                 # PyInstaller spec
├── build.ps1                         # enforces correct PyInstaller invocation dir
├── workspace/                        # audio I/O (gitignored)
├── docs/
│   ├── npu_backend_prd.md
│   └── recorder_gui_roadmap.md
└── requirements.txt
```

---

## Phase 1 — Windows System Tray Recorder (MVP)

**Deliverable:** A packaged Windows `.exe` that lives in the system tray, records both sides of a call with pause/resume, captures live notes, and saves a WAV + notes to `workspace/`.

### 1.1 — Project scaffolding

- Create `recorder/` package and all stub files listed above
- Create `recorder/backends/` with `wasapi.py` stub
- Create `recorder_gui.py` entry point
- Create `recorder_gui.spec` and `build.ps1`
- Add Phase 1 deps to `requirements.txt`

### 1.2 — Tray icon and menu (`recorder/tray.py`)

```
Menu (IDLE):
  ● Diarized Transcriber  v0.1        [disabled]
  ─────────────────────────────────
  ⏺  Start Recording
  ─────────────────────────────────
  ⚙  Settings...
  📋  View Recordings...
  ─────────────────────────────────
  Quit

Menu (RECORDING):
  ● Diarized Transcriber — 00:02:34   [disabled, live timer]
  ─────────────────────────────────
  ⏸  Pause
  ⏹  Stop
  📝  Notes...
  ─────────────────────────────────
  ⚙  Settings...
  📋  View Recordings...
  ─────────────────────────────────
  Quit

Menu (PAUSED):
  ● Diarized Transcriber — Paused     [disabled]
  ─────────────────────────────────
  ▶  Resume
  ⏹  Stop
  📝  Notes...
  ─────────────────────────────────
  ⚙  Settings...
  📋  View Recordings...
  ─────────────────────────────────
  Quit
```

Live timer: `QTimer` fires every second in RECORDING state, incrementing elapsed display. Paused state freezes the counter.

Key Qt requirement: `QApplication.setQuitOnLastWindowClosed(False)` — prevents app exit when Notes or Settings window closes.

### 1.3 — Audio capture (`recorder/audio.py`, `recorder/backends/wasapi.py`)

`AudioBackend` ABC (forward-compatible with Phase 4):

```python
class AudioBackend(ABC):
    @abstractmethod
    def get_default_mic(self) -> dict: ...

    @abstractmethod
    def get_default_loopback(self) -> dict: ...

    @abstractmethod
    def open_streams(self, mic_info, loopback_info,
                     mic_cb, loopback_cb) -> tuple[Any, Any]: ...

    @abstractmethod
    def close_streams(self, mic_stream, loopback_stream) -> None: ...
```

`RecorderThread(QThread)` signals:
- `recording_started` — both streams open
- `recording_stopped(wav_path: str, notes: str)` — WAV written, notes text passed along
- `level_update(mic_db: float, loopback_db: float)` — for future VU meter
- `error(message: str)`

Pause is implemented by setting `self._paused = True` inside `RecorderThread`, causing both callbacks to `return (None, pyaudio.paContinue)` immediately without appending data. Resume clears the flag. Streams stay open — no re-init delay.

Output filename: `workspace/recording_YYYYMMDD_HHMMSS.wav`

### 1.4 — Live notes window (`recorder/notes_window.py`)

`NotesWindow(QWidget)` — a small always-on-top floating window, shown when the user clicks **Notes…** from the tray:

```
┌─────────────────────────────────┐
│  Notes — recording_20260715…    │
├─────────────────────────────────┤
│                                 │
│  [QPlainTextEdit — editable]    │
│                                 │
├─────────────────────────────────┤
│  [Save & Close]  [Keep Open]    │
└─────────────────────────────────┘
```

- Notes are held in memory during recording (not written to disk yet — no WAV file exists)
- On recording stop, `RecorderThread` receives the notes text via `stop(notes_text)` call before saving
- Notes are passed along in the `recording_stopped` signal for the next phase to persist into JSON

### 1.5 — Settings window (`recorder/settings.py`)

`SettingsWindow(QDialog)` — Phase 1 scope:

| Setting | Type | Default |
|---|---|---|
| Microphone device | dropdown (WASAPI inputs) | System default |
| Loopback device | dropdown (WASAPI loopbacks) | System default speaker loopback |
| Output directory | path picker | `workspace/` relative to script |

Persisted to `~/.diarized_transcriber/settings.json`.

### 1.6 — JSON store (`recorder/json_store.py`)

Utility module used by both Phase 2 (enrichment) and Phase 3 (reading):

```python
def load(json_path: Path) -> dict: ...
def save(json_path: Path, data: dict) -> None: ...
def enrich(json_path: Path, notes: str = "", speaker_names: dict = None) -> None:
    """Add notes and speaker_names fields if absent; preserve existing values."""
```

### 1.7 — Icon generation

Icons are generated programmatically at first launch using `QPainter` if absent from disk — no binary assets in the repo:

- `icon_idle.png` — 64×64, grey circle (#888888)
- `icon_recording.png` — 64×64, red circle (#E53935)
- `icon_paused.png` — 64×64, orange circle (#F57C00)
- `icon_transcribing.png` — 64×64, orange circle with small inner clockwise arc (static; animation not required for MVP)

### 1.8 — PyInstaller packaging

`build.ps1` — enforces correct invocation directory to avoid PySide6 DLL resolution bug:

```powershell
Set-Location $PSScriptRoot   # project root, never a sub-package dir
pyinstaller recorder_gui.spec --clean
```

`recorder_gui.spec` essentials:
```python
a = Analysis(
    ['recorder_gui.py'],
    datas=[('recorder/resources', 'recorder/resources')],
    hiddenimports=['pyaudiowpatch'],
    excludes=[
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
    ],
)
```

Use `--onedir` (one folder), not `--onefile` — one-file startup is measurably slower with PySide6 because it extracts the entire Qt tree on every launch.

Icon must be `.ico` for the Windows EXE resource. `build.ps1` auto-converts `icon_idle.png` → `icon_idle.ico` using Pillow if needed.

### Phase 1 success criteria

- Tray icon appears in Windows notification area on launch
- Start → Record (mic + system audio) → Pause → Resume → Stop → WAV in `workspace/`
- Notes window accessible during recording; text preserved into `recording_stopped` signal
- Settings window saves/restores device selections
- `build.ps1` produces `dist/recorder_gui/recorder_gui.exe` that runs without Python installed

---

## Phase 2 — Transcription Integration

**Deliverable:** After recording stops, the app offers to transcribe. Status shows in tray. Settings gains transcriber options. Notes + speaker map are written into the JSON.

### 2.1 — TranscriberWorker (`recorder/transcriber_worker.py`)

`TranscriberWorker(QObject)` using `QProcess`:

```python
class TranscriberWorker(QObject):
    progress = Signal(str)    # stdout/stderr lines for status display
    finished = Signal(int)    # exit code (0 = success)
    error    = Signal(str)

    def start(self, wav_path: Path, settings: Settings) -> None:
        cmd = [
            sys.executable,
            str(app_path("diarized_transcriber.py")),
            str(wav_path),
            "--model",    settings.model,
            "--language", settings.language,
            "--backend",  settings.backend,
            "--hf-token", settings.hf_token,
        ]
        self._proc = QProcess()
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.readyReadStandardError.connect(self._on_stderr)
        self._proc.finished.connect(self._on_finished)
        self._proc.start(cmd[0], cmd[1:])

    def _on_finished(self, exit_code, _status):
        if exit_code == 0:
            self._enrich_json()   # add notes + speaker_names fields
        self.finished.emit(exit_code)

    def _enrich_json(self):
        json_path = self._wav_path.with_suffix('.json')
        json_store.enrich(json_path, notes=self._notes, speaker_names={})
```

`app_path()` resolves relative to `sys._MEIPASS` when running as a PyInstaller bundle, or relative to `__file__` in development.

### 2.2 — Post-recording prompt

After `RecorderThread` emits `recording_stopped`, tray shows a system notification:

```
"Recording saved (03:42). Transcribe now?"
```

Clicking the notification opens a small `QDialog`:
```
Recording: recording_20260715_143201.wav  (03:42)
──────────────────────────────────────────────────
[Transcribe Now]          [Later — View in Logs]
```

If **Transcribe Now**: launch `TranscriberWorker`, update tray icon to `icon_transcribing.png`, update tooltip. On completion, tray returns to IDLE and shows "Transcription complete" notification.

If auto-transcribe is enabled in settings, skip the dialog entirely.

### 2.3 — Extended settings

Add to `SettingsWindow`:

| Setting | Type | Default |
|---|---|---|
| Auto-transcribe after recording | checkbox | off |
| Transcription backend | dropdown: local / cloud | local |
| Whisper model | dropdown: tiny / base / small / medium | small |
| Language | text (ISO 639-1 code) | en |
| HuggingFace token | password field | read from `.env` |
| AssemblyAI API key | password field | read from `.env` |

Credentials entered here are written back to `.env` via `python-dotenv`'s `set_key()`. They are never stored in `settings.json`.

### Phase 2 success criteria

- Record → Stop → auto or prompted transcription → JSON + TXT appear in `workspace/`
- JSON contains `notes` (from recording session) and `speaker_names: {}` after transcription
- Tray shows transcription progress and completion notification
- Settings persist across app restarts

---

## Phase 3 — Recording Detail Viewer

**Deliverable:** A full `RecordingsWindow` that replaces the basic log viewer, with per-recording playback, speaker naming, retranscription, notes editing, and two transcript view modes.

### 3.1 — RecordingsWindow layout (`recorder/recordings_window.py`)

Three-pane `QMainWindow`:

```
┌────────────────────┬──────────────────────────────────────────┐
│  Recordings        │  recording_20260715_143201.wav            │
│  ─────────────     │  ──────────────────────────────────────── │
│  [search bar]      │  [PlayerBar ─────────────────────────────]│
│                    │  ▶  ━━━━━━━━━━━━●━━━  01:23 / 03:42      │
│  Date       Dur    │  ──────────────────────────────────────── │
│  Jul 15 3:42 ●     │  [Toolbar]                                │
│  Jul 12 1:12 ✓     │  [Transcribe] [Retranscribe] [👤 Speakers]│
│  Jul 10 0:48 ✓     │  [📋 Copy All] [🗑 Delete]                │
│                    │  ──────────────────────────────────────── │
│                    │  [Transcript ▼]  [Timestamps mode ⇄]      │
│                    │                                           │
│                    │  Jason (0:00 – 0:04): Hey, good to...    │
│                    │  Teddy (0:04 – 0:12): Yeah absolutely...  │
│                    │  Jason (0:12 – 0:28): So the reason I... │
│                    │                                           │
│                    │  ──────────────────────────────────────── │
│                    │  Notes                                    │
│                    │  [QPlainTextEdit — editable]              │
│                    │                           [Save Notes]    │
└────────────────────┴──────────────────────────────────────────┘
```

### 3.2 — Recording list (`recorder/recording_list.py`)

`RecordingListPanel` scans `workspace/` for `recording_*.wav` on open and on a `QFileSystemWatcher`. Columns:

| Column | Source |
|---|---|
| Date / Time | Parsed from filename stem |
| Duration | WAV header (`wave` module) |
| Status icon | ✓ = JSON exists, ⏳ = transcribing, ● = no transcript yet |
| Speakers | `speakers_detected` from JSON, or `—` |

Sorted by date descending. Clicking a row loads that recording into `RecordingDetailPanel`. The search bar filters by date string and (if transcribed) text content of the `.txt` sidecar.

### 3.3 — Player bar (`recorder/player_bar.py`)

`PlayerBar(QWidget)` wraps `QMediaPlayer` and `QAudioOutput`:

```python
self._player = QMediaPlayer()
self._audio  = QAudioOutput()
self._player.setAudioOutput(self._audio)
self._player.setSource(QUrl.fromLocalFile(str(wav_path)))
```

Controls:
- Play/Pause button — toggles `_player.play()` / `_player.pause()`
- `QSlider` (horizontal) — position; updated via `_player.positionChanged`; scrubbing calls `_player.setPosition()`
- Elapsed / total time labels — formatted `MM:SS`

`QMediaPlayer` on Windows uses Windows Media Foundation, which supports WAV natively. No additional codecs needed.

### 3.4 — Action toolbar

Buttons and their behavior:

| Button | Enabled when | Action |
|---|---|---|
| **Transcribe** | No JSON sidecar exists | Launches `TranscriberWorker`; disables button while running |
| **Retranscribe** | JSON exists | Prompts "Retranscribe? Existing transcript will be replaced." → relaunches `TranscriberWorker`; preserves `notes` and `speaker_names` in JSON after new transcription |
| **Name Speakers** | JSON exists with `speakers_detected > 0` | Opens `SpeakerNameDialog` |
| **Copy All** | JSON or TXT exists | Copies rendered transcript text to clipboard |
| **Delete** | Always | "Delete recording and all associated files?" → removes WAV + JSON + TXT |

Retranscribe preserves notes and speaker names: before launching, read current `notes` and `speaker_names` from JSON; after `TranscriberWorker` finishes, call `json_store.enrich()` with the saved values.

### 3.5 — Speaker naming dialog (`recorder/speaker_dialog.py`)

`SpeakerNameDialog(QDialog)` — one row per detected speaker:

```
┌──────────────────────────────────┐
│  Name Your Speakers              │
├──────────────────────────────────┤
│  SPEAKER_00  [__Jason__________] │
│  SPEAKER_01  [__Teddy__________] │
├──────────────────────────────────┤
│            [Cancel]  [Save]      │
└──────────────────────────────────┘
```

On Save:
1. Write `speaker_names` dict to JSON via `json_store`
2. Re-render the transcript view (substituting SPEAKER_XX with real names in display only — the raw JSON `segments` are unchanged)
3. Optionally regenerate the `.txt` sidecar with real names

Speaker labels used in the transcript view: if `speaker_names["SPEAKER_00"] == "Jason"`, render `Jason` in place of `SPEAKER_00` or `Speaker 1`. If a name is blank, fall back to `Speaker 1`, `Speaker 2`, etc.

### 3.6 — Transcript view (`RecordingDetailPanel` — transcript section)

Two rendering modes, toggled by a button in the detail panel header:

**Reading mode** (default — easier to read):
```
Jason: Hey, good to connect. So the reason I wanted to hop on a call…

Teddy: Yeah absolutely, and I've been thinking about the same thing…
```
Adjacent segments from the same speaker are merged into one paragraph.

**Timestamp mode** (each segment on its own line):
```
Jason    0:00 – 0:04   Hey, good to connect.
Jason    0:04 – 0:12   So the reason I wanted to hop on a call…
Teddy    0:12 – 0:28   Yeah absolutely, and I've been thinking…
```

Both modes use `speaker_names` substitution. Toggle state is not persisted — defaults to Reading mode on each open.

The view is `QPlainTextEdit` (read-only). If no JSON/TXT exists for the selected recording, show a placeholder: `"No transcript yet."` with a **Transcribe** button centered in the panel.

### 3.7 — Notes panel

`QPlainTextEdit` (editable) beneath the transcript view. On load, populates from `json["notes"]`. **Save Notes** writes back via `json_store.save()`. Changes are not auto-saved — the user must press Save (avoids accidental overwrite during brief viewing).

If no JSON exists (no transcription yet), notes can still be viewed and saved — `json_store` creates a minimal JSON stub: `{"notes": "...", "speaker_names": {}, "segments": []}`.

### Phase 3 success criteria

- Selecting a recording in the list loads its player, transcript, and notes
- Playback controls work; seeking works
- Transcribe / Retranscribe launch correctly; retranscribe preserves notes and speaker names
- Speaker naming dialog updates display immediately
- Both transcript view modes render correctly with named speakers
- Notes save to JSON and persist across app restarts
- Copy All copies rendered text (with real names if named)
- Delete removes all three sidecar files after confirmation

---

## Phase 4 — Cross-Platform Audio Backends

**Deliverable:** The same GUI entry point runs on macOS and Linux. Only the audio capture backend changes.

### 4.1 — Platform factory (already scaffolded in Phase 1)

```python
# recorder/audio.py
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
```

### 4.2 — macOS backend (`recorder/backends/coreaudio.py`)

Library: `soundcard>=0.4.6`

```python
loopback_mic = sc.get_microphone(
    id=str(sc.default_speaker().name), include_loopback=True
)
```

**Permission check:** Before opening streams, verify Screen & System Audio Recording TCC access. If missing, `CoreAudioBackend.get_default_loopback()` raises `PermissionError`. `RecorderThread` catches this and emits `error()`. The tray handler shows a one-time dialog:

> "System Audio Recording permission is required. Open System Settings → Privacy & Security → Screen & System Audio Recording and enable Diarized Transcriber."

With a button: **Open System Settings** → `QDesktopServices.openUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")`

No virtual audio driver (BlackHole, Soundflower) required — ScreenCaptureKit on macOS 13+ handles system audio natively under the TCC grant.

### 4.3 — Linux backend (`recorder/backends/pulseaudio.py`)

Library: `soundcard>=0.4.6` (PulseAudio backend; also works via PipeWire's PulseAudio compatibility layer)

PulseAudio exposes a monitor source for each output sink (e.g., `alsa_output.pci-0000:00:1b.0.analog-stereo.monitor`). SoundCard surfaces these when `include_loopback=True`. No special permissions needed on standard desktop Linux.

### 4.4 — QMediaPlayer on macOS / Linux

`QMediaPlayer` uses platform-native backends:
- macOS: AVFoundation (WAV supported natively)
- Linux: GStreamer (must be installed; most distros include it; WAV supported)

No code changes to `PlayerBar` — Qt handles this transparently.

### 4.5 — Per-platform binary builds

PyInstaller must run on each target OS. Build matrix:

| Platform | Output | Distribution format |
|---|---|---|
| Windows | `dist/recorder_gui/recorder_gui.exe` | Folder or NSIS installer |
| macOS | `dist/recorder_gui.app` | DMG via `create-dmg` |
| Linux | `dist/recorder_gui/recorder_gui` | AppImage via `appimagetool` |

Future: GitHub Actions matrix (`windows-latest`, `macos-latest`, `ubuntu-latest`) producing release artifacts on tag push.

---

## Known Risks and Mitigations

### PyInstaller + PySide6 DLL resolution on Windows
**Risk:** Invoking PyInstaller from a directory containing `__init__.py` corrupts Qt DLL paths at runtime.  
**Mitigation:** `build.ps1` enforces invocation from project root. Documented in build instructions.

### PyAudioWPatch — no active loopback device
**Risk:** `get_default_wasapi_loopback()` returns `None` when no audio output is active (disconnected headphones, HDMI off).  
**Mitigation:** `RecorderThread` checks for `None` before opening streams; emits `error("No loopback device found. Connect a speaker or headphones and try again.")`.

### Pause discards audio vs. silence gap
**Risk:** Pausing by discarding callback data introduces a gap in the WAV timeline. On playback and in the transcript, paused periods appear as dead air.  
**Mitigation:** This is intentional — the user paused to avoid capturing content. Document in UI: "Audio during Pause is not recorded." If silence-filling is later requested, insert a block of zeros of equivalent duration instead of discarding.

### Sample rate mismatch between mic and loopback
**Risk:** Mic often reports 44.1 kHz; loopback 48 kHz. Byte-level interleaving produces distortion.  
**Mitigation:** Each stream is captured at its own native rate into separate buffers. Post-capture, `scipy.signal.resample_poly` resamples both to 16 kHz before mixing. Not real-time — no latency concern.

### macOS TCC — silent loopback failure
**Risk:** If TCC permission is missing, SoundCard's loopback returns silence with no exception.  
**Mitigation:** Phase 4 macOS backend records a short test capture at stream open and checks RMS. If below threshold, treats as permission-denied and surfaces the settings dialog.

### QMediaPlayer — no GStreamer on Linux
**Risk:** On minimal Linux installs, GStreamer may be absent, making `QMediaPlayer` fail silently.  
**Mitigation:** Phase 4 Linux backend checks for GStreamer at startup and shows a one-time warning: "Install gstreamer1.0-plugins-good for audio playback." Transcription still works without it.

### Retranscribe race with open JSON
**Risk:** If the user has the detail panel open while retranscription is running, `json_store.save()` could conflict with `diarized_transcriber.py` writing the same file.  
**Mitigation:** `TranscriberWorker` signals `started` → detail panel disables Save Notes and Name Speakers until `finished` fires.

### Thread safety — QWidget from background thread
**Risk:** Emitting signals from `RecorderThread` that directly mutate widgets crashes Qt.  
**Mitigation:** All cross-thread signals use the default `Qt.QueuedConnection`. `RecorderThread.run()` never touches any widget directly.

### Binary size
**Risk:** PySide6 + PyAudioWPatch + NumPy + SciPy + QtMultimedia → ~150–200 MB one-folder.  
**Mitigation:** Exclude unused Qt modules in `.spec`. If size is a blocker, evaluate replacing `scipy.signal.resample_poly` with a pure-NumPy linear interpolation (adequate for 16 kHz voice).

---

## Dependencies by Phase

```
# Phase 1 additions
PySide6>=6.7.0
PyAudioWPatch>=0.2.12.8
scipy>=1.13.0

# Phase 2 — no new deps (QProcess is in PySide6)

# Phase 3 — no new deps (QMediaPlayer is in PySide6.QtMultimedia, included in PySide6)

# Phase 4 additions
soundcard>=0.4.6      # macOS + Linux loopback
```

Full `requirements.txt` after Phase 1:
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

### Phase 1

1. Create `recorder/` package, `recorder/backends/`, and all stub files
2. Implement icon generation (`recorder/tray.py` helper — `QPainter` circles, writes to `recorder/resources/` on first run)
3. Implement `recorder/backends/wasapi.py` — `WasapiBackend(AudioBackend)` wrapping PyAudioWPatch
4. Implement `recorder/audio.py` — `AudioBackend` ABC, `get_audio_backend()` factory, `RecorderThread` with pause/resume/stop
5. Implement `recorder/json_store.py` — `load`, `save`, `enrich`
6. Implement `recorder/notes_window.py` — `NotesWindow(QWidget)`, floating, always-on-top
7. Implement `recorder/settings.py` — `Settings` dataclass + `SettingsWindow(QDialog)`, Phase 1 fields only
8. Implement `recorder/tray.py` — `SystemTrayApp`, full state machine (IDLE / RECORDING / PAUSED / SAVING), wires `RecorderThread` and `NotesWindow`
9. Implement `recorder_gui.py` — `QApplication`, `setQuitOnLastWindowClosed(False)`, instantiate `SystemTrayApp`, `app.exec()`
10. Create `recorder_gui.spec` and `build.ps1`
11. Smoke test: tray icon visible; record → pause → resume → stop → WAV in `workspace/`; notes captured in `recording_stopped` signal
12. PyInstaller build test; verify `.exe` runs without Python
13. Commit on `feat-recorder-gui`

### Phase 2

1. Implement `recorder/transcriber_worker.py`
2. Wire post-recording prompt and auto-transcribe logic in `tray.py`
3. Extend `SettingsWindow` with transcriber fields
4. Implement `_enrich_json()` call in `TranscriberWorker._on_finished()`
5. End-to-end test: record → transcribe → JSON with `notes` and `speaker_names: {}` in `workspace/`

### Phase 3

1. Implement `recorder/player_bar.py` (`QMediaPlayer` + controls)
2. Implement `recorder/speaker_dialog.py` (`SpeakerNameDialog`)
3. Implement `recorder/recording_list.py` (`RecordingListPanel`, `QFileSystemWatcher`)
4. Implement `recorder/recording_detail.py` (`RecordingDetailPanel` — player, toolbar, transcript, notes)
5. Implement `recorder/recordings_window.py` (`RecordingsWindow`, wires list + detail)
6. Wire "View Recordings…" tray action to open `RecordingsWindow`
7. Full test: all per-recording actions, both transcript modes, speaker naming, notes save/restore

### Phase 4 (requires macOS/Linux hardware)

1. Implement `recorder/backends/coreaudio.py` with TCC permission check
2. Implement `recorder/backends/pulseaudio.py` with GStreamer check
3. Test on macOS: permission flow, loopback capture, playback, build `.app`
4. Test on Linux: loopback capture, GStreamer check, playback, build AppImage
5. Set up GitHub Actions matrix build

---

## Success Criteria — Full Product

- Record, pause, resume, stop from the system tray on Windows
- Live notes captured during recording and persisted to JSON
- Auto or on-demand transcription with progress visible in tray
- RecordingsWindow lists all recordings; selecting one loads player, transcript, notes
- Audio plays back with seek; timestamps align with transcript segments
- Speaker naming updates transcript display immediately
- Retranscribe preserves notes and speaker names
- Both transcript view modes (reading / timestamp) render correctly with real names
- Copy All copies clean readable text
- Delete removes WAV + JSON + TXT with confirmation
- Windows `.exe` binary runs on a machine without Python installed
