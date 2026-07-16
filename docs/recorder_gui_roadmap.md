# Recorder GUI — Implementation Roadmap

## Overview

`recorder_gui` is a self-contained Windows application (distributable `.exe`) that:
- Records both sides of any call (microphone + system audio) via WASAPI loopback
- Manages a local transcription server process — starting, monitoring, and stopping it automatically
- Bootstraps the full transcription backend on first launch (no separate installer)
- Surfaces a full recording management UI: playback, speaker naming, timestamped transcripts, live notes, and a browseable history

Initial target is Windows. The GUI code is platform-neutral and designed to extend to macOS and Linux in Phase 4 by swapping only the audio backend.

---

## Goals

| Goal | Phase |
|---|---|
| Record mic + system audio from any call app | 1 |
| Pause and resume recording mid-call | 1 |
| Live notes panel during recording | 1 |
| Stream audio chunks to disk (not memory) — safe for long calls | 1 |
| Crash recovery — detect and offer to recover orphaned temp recordings | 1 |
| User data in AppData — safe for packaged `.exe` | 1 |
| Prompt for recording display name on stop (skippable) | 1 |
| System tray presence — minimal footprint, always accessible | 1 |
| First-run setup wizard — bootstrap Python venv + models in-app | 2 |
| Start / monitor / stop transcription server process from GUI | 2 |
| Transcription queue with Pause Queue and per-job Cancel | 2 |
| Auto-transcribe after recording stops | 2 |
| Global hotkeys (start / pause / stop) with conflict detection | 2 |
| Cloud transcription consent dialog (one-time, stored) | 2 |
| Settings window (devices, model, language, backend, hotkeys) | 2 |
| Distributable Windows `.exe` binary | 2 |
| Recording list + per-recording detail panel | 3 |
| Audio playback with seek | 3 |
| Transcribe / Retranscribe per recording | 3 |
| Name speakers (map SPEAKER_XX → real names) | 3 |
| Transcript view toggle: reading mode vs. timestamp mode | 3 |
| Copy full transcript to clipboard | 3 |
| Edit notes post-call | 3 |
| Reveal file in Explorer / Open data directory | 3 |
| Per-recording "Keep forever" override for auto-delete | 3 |
| Auto-delete after N days (disabled by default) | 3 |
| macOS and Linux audio backend support | 4 |

---

## Architecture

### Stack

| Layer | Technology | Rationale |
|---|---|---|
| GUI + system tray | PySide6 (Qt 6, LGPL) | `QSystemTrayIcon` built in; identical code on all platforms; full widget toolkit |
| Audio playback | `PySide6.QtMultimedia` | `QMediaPlayer` plays WAV natively via Windows Media Foundation |
| Audio capture — Windows | PyAudioWPatch 0.2.12.8 | WASAPI loopback + mic; purpose-built; Jan 2026 release |
| Audio capture — macOS (Phase 4) | SoundCard via CoreAudio | `include_loopback=True`; requires TCC grant |
| Audio capture — Linux (Phase 4) | SoundCard via PulseAudio/PipeWire | Monitor source loopback |
| Audio mixing | NumPy + SciPy | `resample_poly` for post-capture resampling; already in tree via whisperX |
| Transcription API | FastAPI (local REST server) | Thin HTTP wrapper around `transcribe.py`; managed by GUI |
| HTTP client | `httpx` or `urllib` (stdlib) | GUI talks to transcription server on localhost |
| Binary packaging | PyInstaller | Build as `--onedir`; GUI binary is the sole distributable artifact |
| Backend venv | Embedded Python + pip | GUI bootstraps backend on first run; stored in `AppData\DiarizedTranscriber\backend\` |

### User data layout

All runtime data lives under a platform-appropriate user data root. The GUI resolves this at startup:

```
Windows:   %APPDATA%\DiarizedTranscriber\
macOS:     ~/Library/Application Support/DiarizedTranscriber/
Linux:     ~/.local/share/DiarizedTranscriber/
```

Structure:
```
DiarizedTranscriber/
├── workspace/               # recordings, JSON sidecars, TXT transcripts
│   ├── recording_20260715_143201.wav
│   ├── recording_20260715_143201.json
│   └── recording_20260715_143201.txt
├── tmp/                     # in-progress recording chunks (crash recovery)
│   └── recording_20260715_143201/
│       ├── mic.raw          # raw int16 PCM, native sample rate
│       ├── mic.meta         # JSON: sample_rate, channels
│       ├── loopback.raw
│       ├── loopback.meta
│       └── notes.txt        # live notes written periodically
├── backend/                 # managed Python venv for transcription server
│   ├── python/              # embedded Python distribution
│   └── venv/                # pip-installed whisperX, PyTorch, pyannote, FastAPI
├── models/                  # downloaded Whisper + pyannote model weights
└── settings.json            # app settings (devices, model, hotkeys, retention, etc.)
```

`workspace/` is user-visible and intentionally accessible. The **Open Data Directory** button opens it directly. **Reveal File** opens Explorer with the specific file selected.

### JSON schema

`transcribe.py` produces the base segment data. The GUI enriches the JSON in-place. All GUI-managed fields are initialized when the recording stops (before transcription runs).

```json
{
  "display_name": "Site call with Steve",
  "created_at": "2026-07-15T14:32:01",
  "backend": "local",
  "audio_file": "recording_20260715_143201.wav",
  "speakers_detected": 2,
  "speaker_names": {
    "SPEAKER_00": "Jason",
    "SPEAKER_01": "Steve"
  },
  "notes": "Key points:\n- Follow up on budget by Friday",
  "retain": false,
  "segments": [
    { "speaker": "SPEAKER_00", "start": 0.00, "end": 4.12, "text": "Hey, good to connect." }
  ]
}
```

GUI-owned fields (never written by `transcribe.py`):
- `display_name` — set at recording stop prompt; editable in detail panel
- `created_at` — ISO timestamp set at recording stop
- `speaker_names` — populated via speaker naming dialog post-transcription
- `notes` — from live notes panel; editable post-call
- `retain` — per-recording auto-delete override; default `false`

`transcribe.py` is unchanged — it writes `backend`, `audio_file`, `speakers_detected`, and `segments`. The GUI enriches the file before and after transcription.

### Process model

```
recorder_gui.exe  (GUI — master process)
 ├── Manages lifecycle of → transcription_server.py (FastAPI — child process)
 │    └── Runs inside backend/venv/; started on GUI launch; stopped on GUI quit
 ├── QApplication
 ├── SystemTrayApp (QSystemTrayIcon)
 │    ├── RecorderThread (QThread — disk-streaming audio capture)
 │    ├── NotesWindow (QWidget — floating notes panel during recording)
 │    ├── TranscriptionQueue (manages HTTP POSTs to local server)
 │    └── SettingsWindow (QDialog)
 └── RecordingsWindow (QMainWindow)
      ├── RecordingListPanel (QTableWidget)
      └── RecordingDetailPanel
           ├── PlayerBar (QMediaPlayer + QSlider)
           ├── ActionToolbar
           ├── TranscriptView (QPlainTextEdit, two modes)
           └── NotesPanel (QPlainTextEdit, editable)
```

**Server lifecycle:**
1. GUI starts → check health endpoint `GET http://localhost:7777/health`
2. If unhealthy: launch `backend/venv/python transcription_server.py --port 7777` as `QProcess`
3. Poll health every 2 seconds until ready (max 30 seconds; show "Starting backend…" in tray tooltip)
4. If port 7777 is taken: try 7778–7780; store chosen port in `settings.json`
5. GUI quit → send `POST /shutdown` to server, then terminate the `QProcess` if it doesn't exit within 5 seconds

**Server health indicator:** Small colored dot in the Settings window header and optionally in the tray tooltip. Green = ready, yellow = starting, red = error.

### Audio recording model (disk streaming)

Audio is **never held entirely in memory**. Chunks are written to disk as they arrive.

On recording start, create `tmp/recording_YYYYMMDD_HHMMSS/`:
- Open `mic.raw` and `loopback.raw` in binary append mode (`'ab'`)
- Open `notes.txt` for periodic autosave of the notes panel contents

Each stream callback appends its chunk to the respective `.raw` file immediately. Pause is implemented by setting a `_paused` flag; callbacks return without writing during pause (streams stay open). On resume, writing resumes to the same files — the pause gap is simply absent from the recording.

On stop:
1. Close both `.raw` files
2. Read `mic.meta` and `loopback.meta` for sample rate / channel info
3. `np.fromfile('mic.raw', dtype=np.int16)` → resample to 16 kHz mono
4. `np.fromfile('loopback.raw', dtype=np.int16)` → downmix to mono → resample to 16 kHz
5. Mix, clip, write final WAV to `workspace/`
6. Delete the `tmp/recording_.../` directory on success

**Maximum RAM use during recording:** one write buffer per callback chunk (typically 512–4096 bytes). A 3-hour call uses negligible memory regardless of length.

### Crash recovery

On every launch, before entering the main event loop:

1. Scan `tmp/` for any directories matching `recording_*/`
2. For each found: read `mic.meta` to confirm it's a valid partial recording
3. Show a non-blocking tray notification (or modal if multiple found):
   > "Incomplete recording found from Jul 15 at 2:32 PM. Recover it?"  
   > **[Recover]** **[Discard]**
4. Recover: run the same mixdown pipeline on the raw files → save to `workspace/`
5. Discard: delete the `tmp/recording_.../` directory

Notes captured before the crash are in `tmp/recording_.../notes.txt` and are recovered alongside the audio.

### First-run setup wizard

On first launch (no `backend/venv/` present), show `SetupWizard` before the tray app starts:

```
Step 1 — Downloading Python runtime     [████████░░] 60%
Step 2 — Installing transcription stack [░░░░░░░░░░]  0%
Step 3 — Downloading Whisper model      [░░░░░░░░░░]  0%

This downloads approximately 3–5 GB. You only do this once.
                                              [Cancel]
```

Steps:
1. Extract an embedded minimal Python distribution (bundled in the PyInstaller package as a data file — the embeddable Python zip from python.org, ~20 MB) into `backend/python/`
2. Use it to create a venv at `backend/venv/`
3. `pip install` into the venv: `whisperx pyannote.audio fastapi uvicorn torch torchaudio --index-url https://download.pytorch.org/whl/cu124` (CUDA build) or CPU fallback
4. Pre-download the configured Whisper model into `models/`
5. Write a `backend/version.json` with installed versions for update checks

The wizard can be re-run from **Settings → Transcription → Reinstall Backend**. On success, the app continues to the normal tray entry point. On cancel, the app starts in recording-only mode (no transcription until setup completes).

---

## Recording state machine

```
              ┌───────────────────────┐
              ▼                       │
  IDLE ──► RECORDING ──► PAUSED ─────┘
              │               │
              └───────────────┴──► SAVING ──► NAMING ──► IDLE
```

| State | Tray icon | Tooltip |
|---|---|---|
| IDLE | Grey circle | "Diarized Transcriber — Idle" |
| RECORDING | Red circle | "Recording… 00:02:34" |
| PAUSED | Orange circle | "Paused — 00:02:34" |
| SAVING | Orange pulse | "Saving recording…" |
| NAMING | Orange pulse | "Saving recording…" (dialog open) |
| TRANSCRIBING (queue) | Spinner | "Transcribing 2 recordings…" |

---

## Directory Structure

```
field-recorder/
├── transcribe.py           # existing CLI transcriber (unchanged)
├── transcription_server.py    # NEW — FastAPI wrapper (thin HTTP layer)
├── recorder_gui.py                   # entry point for the tray app
├── recorder/
│   ├── __init__.py
│   ├── audio.py                      # RecorderThread + AudioBackend ABC
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── wasapi.py                 # Windows (Phase 1)
│   │   ├── coreaudio.py              # macOS (Phase 4)
│   │   └── pulseaudio.py             # Linux (Phase 4)
│   ├── server_manager.py             # starts/stops/monitors the FastAPI server process
│   ├── transcription_queue.py        # queue logic, HTTP client, Pause Queue / Cancel
│   ├── setup_wizard.py               # first-run backend bootstrap UI
│   ├── crash_recovery.py             # detects and recovers orphaned tmp/ recordings
│   ├── json_store.py                 # read/write/enrich JSON sidecars
│   ├── user_data.py                  # resolves AppData path; single source of truth
│   ├── hotkeys.py                    # Win32 RegisterHotKey wrapper + conflict detection
│   ├── tray.py                       # SystemTrayApp + state machine
│   ├── notes_window.py               # floating notes panel (during recording)
│   ├── naming_dialog.py              # post-stop display name prompt
│   ├── settings.py                   # SettingsWindow (QDialog)
│   ├── recordings_window.py          # RecordingsWindow (QMainWindow)
│   ├── recording_list.py             # RecordingListPanel (QTableWidget)
│   ├── recording_detail.py           # RecordingDetailPanel
│   ├── player_bar.py                 # QMediaPlayer + controls
│   ├── speaker_dialog.py             # SpeakerNameDialog
│   └── resources/
│       ├── icon_idle.png
│       ├── icon_recording.png
│       ├── icon_paused.png
│       └── icon_transcribing.png
├── recorder_gui.spec                 # PyInstaller spec
├── build.ps1                         # enforces correct invocation dir; builds .exe
└── requirements.txt
```

---

## Phase 1 — Windows System Tray Recorder (MVP)

**Deliverable:** A working tray app that records (with pause), takes live notes, streams audio to disk, handles crashes, prompts for a name on stop, and saves clean WAV files to the user data directory.

### 1.1 — User data module (`recorder/user_data.py`)

Single source of truth for all file paths. **Every other module imports from here — never construct paths inline.**

```python
import os, sys
from pathlib import Path

def app_data_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "DiarizedTranscriber"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DiarizedTranscriber"
    else:
        return Path.home() / ".local" / "share" / "DiarizedTranscriber"

def workspace()  -> Path: return app_data_root() / "workspace"
def tmp_dir()    -> Path: return app_data_root() / "tmp"
def backend_dir()-> Path: return app_data_root() / "backend"
def models_dir() -> Path: return app_data_root() / "models"
def settings_path()->Path:return app_data_root() / "settings.json"
```

Create all directories on first import: `path.mkdir(parents=True, exist_ok=True)`.

### 1.2 — JSON store (`recorder/json_store.py`)

```python
def create_stub(wav_path, display_name, notes) -> Path:
    """Write the initial JSON sidecar immediately after recording stops."""

def load(json_path: Path) -> dict: ...
def save(json_path: Path, data: dict) -> None: ...
def enrich_post_transcription(json_path: Path) -> None:
    """Add speaker_names: {} if absent; preserve existing notes and retain."""
```

`create_stub` writes the JSON with `display_name`, `created_at`, `notes`, `retain: false`, and `segments: []`. After transcription, `enrich_post_transcription` merges the transcriber's output into the existing stub without overwriting GUI-managed fields.

### 1.3 — Audio capture (`recorder/audio.py`, `recorder/backends/wasapi.py`)

`AudioBackend` ABC (unchanged from previous design). `RecorderThread(QThread)` changes:

- On `run()`: create `tmp/recording_YYYYMMDD_HHMMSS/`, write `.meta` files, open `.raw` files in `'ab'` mode
- Callbacks write chunks directly: `self._mic_file.write(in_data)`
- Pause flag: callbacks check `self._paused` and skip writing without closing files
- On stop: close files, emit `recording_stopped(tmp_dir: Path, notes: str)`
- `SystemTrayApp` triggers mixdown in a `QThreadPool` worker after `recording_stopped`

Mixdown worker signals: `mixdown_complete(wav_path: Path)` → triggers naming dialog.

### 1.4 — Crash recovery (`recorder/crash_recovery.py`)

Called from `recorder_gui.py` before the tray app's event loop starts:

```python
def check_and_recover(parent_widget) -> None:
    orphans = list(user_data.tmp_dir().glob("recording_*/mic.meta"))
    for meta_path in orphans:
        tmp_path = meta_path.parent
        created = _parse_timestamp(tmp_path.name)
        reply = QMessageBox.question(
            parent_widget,
            "Incomplete recording found",
            f"A recording from {created:%b %d at %I:%M %p} didn't finish saving.\n"
            f"Recover it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            _recover(tmp_path)
        else:
            shutil.rmtree(tmp_path)
```

`_recover(tmp_path)` runs the same mixdown pipeline as a normal stop, saves to `workspace/`, then shows the naming dialog for the recovered recording.

### 1.5 — Naming dialog (`recorder/naming_dialog.py`)

`NamingDialog(QDialog)` — shown after every recording stops. Non-blocking relative to the mixdown (mixdown runs in parallel; dialog appears immediately on stop).

```
┌────────────────────────────────────────────┐
│  Name this recording                       │
├────────────────────────────────────────────┤
│  [_____________________________________________]  │
│  Leave blank to use the date & time         │
├────────────────────────────────────────────┤
│  Duration: 03:42    Saved to: workspace/   │
├────────────────────────────────────────────┤
│              [Skip]        [Save]           │
└────────────────────────────────────────────┘
```

On Save: writes `display_name` to the JSON stub (creating stub if mixdown not yet complete — the stub path is already known from the timestamp). On Skip: `display_name` stays empty; UI falls back to formatted date/time.

### 1.6 — Tray menu and state machine (`recorder/tray.py`)

```
IDLE menu:
  ● Diarized Transcriber  v0.1     [disabled]
  ──────────────────────────────
  ⏺  Start Recording
  ──────────────────────────────
  ⚙  Settings...
  📋  View Recordings...
  ──────────────────────────────
  Quit

RECORDING menu:
  ● Recording — 00:02:34           [disabled, live timer]
  ──────────────────────────────
  ⏸  Pause
  ⏹  Stop
  📝  Notes...
  ──────────────────────────────
  ⚙  Settings...
  📋  View Recordings...
  ──────────────────────────────
  Quit

PAUSED menu:
  ● Paused — 00:02:34              [disabled]
  ──────────────────────────────
  ▶  Resume
  ⏹  Stop
  📝  Notes...
  ──────────────────────────────
  ⚙  Settings...
  📋  View Recordings...
  ──────────────────────────────
  Quit
```

### 1.7 — Live notes (`recorder/notes_window.py`)

Floating always-on-top `QWidget`. Notes text is autosaved to `tmp/recording_.../notes.txt` every 10 seconds via `QTimer` while recording. On recording stop, the final notes text is read from the file and passed into the JSON stub.

### 1.8 — Icons

Generated programmatically at first launch via `QPainter` if absent. No binary assets in the repo.

### 1.9 — PyInstaller packaging (`recorder_gui.spec`, `build.ps1`)

The embedded Python distribution (embeddable zip, ~20 MB) is included as a PyInstaller data file:

```python
a = Analysis(
    ['recorder_gui.py'],
    datas=[
        ('recorder/resources', 'recorder/resources'),
        ('vendor/python-3.12-embed-amd64.zip', 'vendor'),  # embedded Python for setup wizard
    ],
    hiddenimports=['pyaudiowpatch'],
    excludes=['PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.QtWebEngine',
              'PySide6.QtWebEngineCore', 'PySide6.QtCharts'],
)
```

`build.ps1` sets location to project root before invoking PyInstaller (avoids DLL resolution bug).

### Phase 1 success criteria

- Tray icon visible; Start → Record → Pause → Resume → Stop cycle works
- Audio streams to `tmp/` during recording; RAM stays flat regardless of call length
- Notes panel saves periodically to `tmp/`
- On stop: mixdown completes, naming dialog appears, WAV + JSON stub appear in `workspace/`
- Simulated crash (kill process during recording) → on relaunch, recovery dialog appears → recovered WAV in `workspace/`
- Settings and workspace correctly resolve to `%APPDATA%\DiarizedTranscriber\` on Windows

---

## Phase 2 — Transcription Integration + Server

**Deliverable:** First-run setup wizard installs the backend. The GUI starts and monitors the FastAPI transcription server. Transcription queue with Pause/Cancel, global hotkeys, cloud consent.

### 2.1 — Transcription server (`transcription_server.py`)

Thin FastAPI wrapper around `transcribe.py`:

```
GET  /health               → {"status": "ready", "model": "small"}
POST /jobs                 → {"job_id": "uuid", "status": "queued"}
     body: {wav_path, model, language, backend, hf_token}
GET  /jobs                 → [{job_id, status, wav_path, progress, display_name}, ...]
GET  /jobs/{id}            → {job_id, status, progress, error}
DELETE /jobs/{id}          → cancel a queued or running job
POST /queue/pause          → pause the queue (current job finishes; no new jobs start)
POST /queue/resume         → resume the queue
POST /shutdown             → graceful server shutdown
```

Server runs on `localhost` only (not exposed to the network). One job runs at a time (serial). The queue is in-memory — jobs are re-submitted by the GUI if the server restarts.

### 2.2 — Server manager (`recorder/server_manager.py`)

`ServerManager(QObject)` — owned by `SystemTrayApp`:

```python
class ServerManager(QObject):
    status_changed = Signal(str)   # "starting", "ready", "error"

    def start(self):
        python = user_data.backend_dir() / "venv" / "Scripts" / "python.exe"
        server_script = app_resource_path("transcription_server.py")
        self._proc = QProcess()
        self._proc.start(str(python), [str(server_script), "--port", str(self._port)])
        self._health_timer = QTimer()
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start(2000)

    def stop(self):
        self._http_post("/shutdown")
        self._proc.waitForFinished(5000)
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()
```

`app_resource_path()` resolves to `sys._MEIPASS` in a frozen build or `__file__`'s parent in development.

### 2.3 — Transcription queue (`recorder/transcription_queue.py`)

`TranscriptionQueue(QObject)`:
- Maintains an ordered list of `TranscriptionJob` dataclasses
- `enqueue(wav_path, settings)` → POST to server, add to local list
- Polls `GET /jobs` every 3 seconds via `QTimer`; emits `queue_updated(jobs)` signal
- `pause_queue()` → `POST /queue/pause`; `resume_queue()` → `POST /queue/resume`
- `cancel_job(job_id)` → `DELETE /jobs/{id}`; prompts "Cancel this transcription? Progress will be lost."
- On job completion (status = "done"): calls `json_store.enrich_post_transcription()`

Queue state is visible in the tray tooltip when active: `"Transcribing 2 of 4 recordings…"`

### 2.4 — First-run setup wizard (`recorder/setup_wizard.py`)

`SetupWizard(QDialog)` — shown if `backend/venv/` does not exist:

```
┌─────────────────────────────────────────────────────┐
│  Welcome to Diarized Transcriber                    │
│                                                     │
│  First-time setup installs the local transcription  │
│  engine (~3–5 GB). You only do this once.           │
│                                                     │
│  ① Extract Python runtime    [████████████] Done   │
│  ② Create environment        [████████████] Done   │
│  ③ Install packages          [████░░░░░░░░] 38%    │
│  ④ Download Whisper model    [░░░░░░░░░░░░]         │
│                                                     │
│  Estimated time remaining: ~8 minutes               │
│                                [Cancel setup]       │
└─────────────────────────────────────────────────────┘
```

Steps run in a `QThread`. Progress is streamed from pip's `--progress-bar` output (parsed from `QProcess` stdout). On cancel: cleans up partial venv. On completion: launches `ServerManager.start()` and enters normal tray flow. Re-runnable from Settings → Transcription → **Reinstall Backend**.

### 2.5 — Global hotkeys (`recorder/hotkeys.py`)

Win32 `RegisterHotKey` via `ctypes`. Runs a background thread that calls `GetMessage` for `WM_HOTKEY` events and emits Qt signals.

Default hotkeys (configurable in Settings):

| Action | Default |
|---|---|
| Start / Stop recording | `Ctrl+Shift+R` |
| Pause / Resume | `Ctrl+Shift+P` |
| Open Notes | `Ctrl+Shift+N` |

**Conflict detection:** On registering a hotkey, if `RegisterHotKey` returns `False`, surface a warning:
> "Ctrl+Shift+R could not be registered — it may be in use by another application. Choose a different key combination in Settings."

Hotkeys are unregistered on app quit and re-registered after settings are saved.

### 2.6 — Cloud consent dialog

`CloudConsentDialog(QDialog)` — shown once, the first time the user selects "cloud" as the transcription backend:

```
┌────────────────────────────────────────────────────┐
│  Audio will leave this device                      │
├────────────────────────────────────────────────────┤
│  Cloud transcription sends your recording to       │
│  AssemblyAI's servers for processing. Audio is     │
│  not stored by this app, but is subject to         │
│  AssemblyAI's privacy policy.                      │
│                                                    │
│  Local transcription keeps all audio on-device.    │
│                             [AssemblyAI Privacy ↗] │
├────────────────────────────────────────────────────┤
│  [Switch to local]          [I understand — proceed]│
└────────────────────────────────────────────────────┘
```

`cloud_consent_given: true` stored in `settings.json`. Not shown again after consent.

### 2.7 — Extended Settings window

Phase 2 adds to `SettingsWindow`:

**Recording tab:** Microphone, loopback device, output directory  
**Transcription tab:** Backend (local/cloud), model, language, HF token, AssemblyAI key, auto-transcribe toggle  
**Hotkeys tab:** Configurable key bindings; shows conflict warnings inline  
**Server tab:** Backend status (green/red dot + version), Reinstall Backend button  
**Data tab:** Retention policy (auto-delete after N days, default off), Open Data Directory button

**Open Data Directory** → `QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_data.workspace())))`

### Phase 2 success criteria

- First-run wizard completes and backend server starts cleanly
- Server health indicator shows green in Settings
- Record → stop → name → auto or prompted transcription → JSON + TXT in `workspace/`
- Queue: enqueue two recordings; second waits; Pause Queue stops new jobs from starting; Cancel per-job works
- Global hotkeys start/stop recording without interacting with the tray
- Cloud backend triggers consent dialog on first use; not shown again after

---

## Phase 3 — Recording Detail Viewer

**Deliverable:** Full `RecordingsWindow` with per-recording playback, speaker naming, notes, retain toggle, and reveal file.

### 3.1 — RecordingsWindow layout

Three-pane `QMainWindow`:

```
┌───────────────────────┬──────────────────────────────────────────────────┐
│  Recordings           │  Site call with Steve                            │
│  ───────────────────  │  ──────────────────────────────────────────────  │
│  [search bar]         │  [▶ ━━━━━━━━━━━━●━━━  01:23 / 03:42]  [⋯]      │
│                       │  ──────────────────────────────────────────────  │
│  Site call with Steve │  [Transcribe] [Retranscribe] [👤 Speakers]       │
│  Jul 15  3:42  ✓  2   │  [📋 Copy]  [🗑 Delete]  [📁 Reveal]  [★ Keep]  │
│  Jul 12  1:12  ✓  3   │  ──────────────────────────────────────────────  │
│  Jul 10  0:48  ⏳      │  [Reading ●] [Timestamps ○]                     │
│                       │                                                  │
│                       │  Jason: Hey, good to connect. So the reason I…  │
│                       │  Steve: Yeah absolutely. I've been thinking…    │
│                       │                                                  │
│                       │  ──────────────────────────────────────────────  │
│                       │  Notes                                           │
│                       │  Follow up on budget by Friday.                 │
│                       │  Steve to send revised proposal.                │
│                       │                              [Save Notes]        │
└───────────────────────┴──────────────────────────────────────────────────┘
```

### 3.2 — Recording list

`RecordingListPanel` uses `QFileSystemWatcher` on `workspace/` to update live. List columns:

| Column | Source |
|---|---|
| Display name | `display_name` from JSON, or formatted datetime if empty |
| Date / Duration | `created_at`, WAV header |
| Status | ✓ transcribed, ⏳ in queue/running, ● not yet transcribed |
| Speakers | `speakers_detected` or `—` |

### 3.3 — Player bar (`recorder/player_bar.py`)

`PlayerBar(QWidget)` — `QMediaPlayer` + `QAudioOutput` + `QSlider`:
- Play/Pause toggle button
- Seek slider: updates from `positionChanged`; dragging calls `setPosition()`
- Elapsed / total time labels (MM:SS)
- Volume control (optional)

### 3.4 — Action toolbar

| Button | Label | Enabled when | Action |
|---|---|---|---|
| Transcribe | Transcribe | No JSON or segments empty | Enqueue in `TranscriptionQueue` |
| Retranscribe | Retranscribe | JSON + segments exist | Confirm → save notes + speaker_names → enqueue → re-enrich after |
| Name Speakers | 👤 Speakers | JSON + speakers_detected > 0 | Open `SpeakerNameDialog` |
| Copy | 📋 Copy | JSON exists | Copy rendered transcript to clipboard |
| Delete | 🗑 Delete | Always | Confirm → remove WAV + JSON + TXT |
| Reveal | 📁 Reveal | Always | Platform-specific file reveal (see below) |
| Keep | ★ Keep | Always | Toggle `retain` in JSON; button state reflects current value |

**Reveal file — platform-specific:**
```python
def reveal_file(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
```

**Keep / retain toggle:**
- Button shows filled star (★) if `retain: true`, outline star (☆) if false
- Clicking toggles `retain` in JSON via `json_store.save()`
- Label tooltip: "Excluded from auto-delete" / "Will be deleted after N days"

### 3.5 — Transcript view

Two rendering modes toggled by radio buttons:

**Reading mode** (default): adjacent segments from the same speaker are merged into paragraphs.

**Timestamp mode**: one line per segment, aligned columns.

Both modes substitute real names from `speaker_names`. Falls back to `Speaker 1`, `Speaker 2` if names are unset.

### 3.6 — Speaker naming dialog

`SpeakerNameDialog(QDialog)` — one text field per detected speaker. On Save: writes `speaker_names` to JSON, re-renders transcript view. Raw segment data is preserved unchanged.

### 3.7 — Notes panel

Editable `QPlainTextEdit` below the transcript. Populated from `json["notes"]` on load. **Save Notes** button writes back via `json_store.save()`. Not auto-saved (prevents accidental overwrite while reading).

### 3.8 — Auto-delete policy

Configured in Settings → Data:

| Setting | Type | Default |
|---|---|---|
| Auto-delete recordings after | dropdown: Off / 7 / 14 / 30 / 60 / 90 days / Custom | Off |
| Custom days | integer field | — |

Sweep runs on app launch and daily via `QTimer`. For each recording:
1. Parse `created_at` from JSON (or filename if JSON absent)
2. If age > threshold AND `retain != true` AND not currently in transcription queue → delete WAV + JSON + TXT
3. Log deletions to a `deletions.log` in `AppData\DiarizedTranscriber\` for auditability

**Per-recording override:** `retain: true` in the JSON exempts a recording from all sweeps permanently.

### Phase 3 success criteria

- All recordings listed; selecting one loads player, transcript, notes
- Playback + seek works for any WAV in `workspace/`
- Transcribe / Retranscribe enqueue correctly; retranscribe preserves notes and speaker names
- Speaker naming updates transcript display immediately
- Both transcript modes render correctly with real names
- Reveal file opens Explorer with the file selected on Windows
- Keep toggle persists across restarts
- Auto-delete sweep removes recordings older than threshold; skips `retain: true` ones
- Notes save to JSON and restore correctly

---

## Phase 4 — Cross-Platform Audio Backends

**Deliverable:** The same GUI entry point runs on macOS and Linux. Audio capture backend swaps per platform; all other code unchanged.

### 4.1 — Platform factory (scaffolded in Phase 1)

```python
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

### 4.2 — macOS backend

Library: `soundcard>=0.4.6`. Requires Screen & System Audio Recording TCC grant. Detect missing permission by checking RMS of a short test capture; surface a one-time dialog pointing to System Settings if absent.

### 4.3 — Linux backend

Library: `soundcard>=0.4.6` via PulseAudio / PipeWire. No special permissions. GStreamer required for `QMediaPlayer` — check on startup and warn if absent.

### 4.4 — Per-platform binary builds

| Platform | Output | Notes |
|---|---|---|
| Windows | `dist/recorder_gui/recorder_gui.exe` | Setup wizard extracts `vendor/python-3.12-embed-amd64.zip` |
| macOS | `dist/recorder_gui.app` | TCC permission prompt on first loopback use |
| Linux | `dist/recorder_gui/recorder_gui` → AppImage | GStreamer check on launch |

Future: GitHub Actions matrix build on tag push.

---

## Known Risks and Mitigations

### Temp file disk usage during recording
**Risk:** A 3-hour call at 48 kHz stereo 16-bit produces ~1.3 GB in `tmp/`. If disk is nearly full, writes fail.  
**Mitigation:** Check available disk space before starting recording; warn if < 2 GB free. Write a `.meta` file last — its presence signals a complete (not partial) recording to the crash recovery scanner.

### Mixdown race with naming dialog
**Risk:** Mixdown takes a few seconds for very long calls. If the user clicks Save in the naming dialog before mixdown completes, the WAV path doesn't exist yet.  
**Mitigation:** The JSON stub is written immediately with the display name. The WAV path in the stub is set to the expected final path. The mixdown worker emits `mixdown_complete` when done — the detail panel only enables Transcribe after this signal.

### Server port conflict
**Risk:** Port 7777 is already in use.  
**Mitigation:** Walk 7777–7780; store chosen port in `settings.json`. If all fail, show error: "Could not start transcription server. Check Settings → Server."

### Win32 hotkey registration failure
**Risk:** `RegisterHotKey` fails if the key combo is claimed by another app (Zoom, Teams, Windows itself).  
**Mitigation:** Catch the failure, surface a warning in the Hotkeys settings tab with the conflicting key highlighted in red. Fall back to no hotkey (recording still works via tray menu).

### Backend venv drift
**Risk:** After a system Python or pip update, the backend venv may break.  
**Mitigation:** Settings → Transcription → **Reinstall Backend** re-runs the setup wizard. Version info in `backend/version.json` lets the app detect when the installed whisperX version differs from what was last known to work.

### Cancel mid-transcription data loss
**Risk:** Cancelling a running whisperX job via `DELETE /jobs/{id}` kills the process; no checkpoint means partial work is lost.  
**Mitigation:** Warn explicitly in the Cancel dialog: "Cancelling a running transcription discards all progress. The recording is not affected." Distinguish clearly from Pause Queue, which lets the current job finish.

### Crash during mixdown (not during recording)
**Risk:** App crashes during the post-stop mixdown. The `.raw` files are complete but the final WAV was never written.  
**Mitigation:** Crash recovery checks for `tmp/` directories where `.meta` files exist but no corresponding WAV exists in `workspace/`. These are treated as recoverable — the mixdown is re-run on next launch.

### PyInstaller + PySide6 DLL resolution
**Risk:** Invoking PyInstaller from a directory containing `__init__.py` corrupts Qt DLL paths.  
**Mitigation:** `build.ps1` enforces invocation from project root.

### macOS TCC silent failure
**Risk:** SoundCard loopback returns silence without raising an exception if TCC is not granted.  
**Mitigation:** RMS check on test capture before committing to a recording session.

---

## Dependencies by Phase

```
# Phase 1
PySide6>=6.7.0
PyAudioWPatch>=0.2.12.8
scipy>=1.13.0
numpy            # already in tree via whisperX

# Phase 2
fastapi>=0.111.0
uvicorn>=0.29.0
httpx>=0.27.0    # HTTP client for queue polling

# Phase 3 — no new deps (QMediaPlayer in PySide6; file ops use stdlib)

# Phase 4 additions
soundcard>=0.4.6
```

Full `requirements.txt` after Phase 2:
```
openai-whisper
whisperx
pyannote.audio
assemblyai
python-dotenv
PySide6>=6.7.0
PyAudioWPatch>=0.2.12.8
scipy>=1.13.0
fastapi>=0.111.0
uvicorn>=0.29.0
httpx>=0.27.0
```

---

## Implementation Sequence for Claude Code

### Phase 1

1. Implement `recorder/user_data.py` — path resolution; create all dirs on import
2. Implement `recorder/json_store.py` — `create_stub`, `load`, `save`, `enrich_post_transcription`
3. Implement `recorder/backends/wasapi.py` — `WasapiBackend(AudioBackend)`
4. Implement `recorder/audio.py` — `AudioBackend` ABC, factory, `RecorderThread` with disk streaming
5. Implement `recorder/crash_recovery.py` — scan `tmp/`, offer recovery dialog
6. Implement `recorder/notes_window.py` — floating notes with 10-second autosave to `tmp/`
7. Implement `recorder/naming_dialog.py` — `NamingDialog`, parallel with mixdown
8. Implement icon generation (programmatic `QPainter` circles)
9. Implement `recorder/settings.py` — `Settings` dataclass + `SettingsWindow` Phase 1 tabs only
10. Implement `recorder/tray.py` — `SystemTrayApp`, full state machine
11. Implement `recorder_gui.py` — crash recovery check → tray app entry point
12. Create `recorder_gui.spec` and `build.ps1`
13. Smoke test: full record → pause → resume → stop → name → WAV + JSON stub in AppData
14. Crash test: kill process mid-record → relaunch → recover dialog → recovered WAV
15. Commit on `feat-recorder-gui`

### Phase 2

1. Implement `transcription_server.py` — FastAPI wrapper with all endpoints
2. Implement `recorder/server_manager.py` — `ServerManager` lifecycle
3. Implement `recorder/setup_wizard.py` — `SetupWizard` with step progress
4. Implement `recorder/transcription_queue.py` — queue, polling, pause, cancel
5. Implement `recorder/hotkeys.py` — Win32 `RegisterHotKey`, conflict detection
6. Implement cloud consent dialog
7. Extend `SettingsWindow` with Transcription, Hotkeys, Server, Data tabs
8. Wire server start into `recorder_gui.py` entry point (after crash recovery, before tray)
9. End-to-end test: record → transcribe → JSON enriched; queue two recordings; pause queue; cancel; hotkeys work

### Phase 3

1. Implement `recorder/player_bar.py`
2. Implement `recorder/speaker_dialog.py`
3. Implement `recorder/recording_list.py` with `QFileSystemWatcher`
4. Implement `recorder/recording_detail.py` — all toolbar actions, transcript modes, notes, retain toggle
5. Implement `recorder/recordings_window.py`
6. Wire "View Recordings…" tray action
7. Implement auto-delete sweep in `json_store.py`; wire into app launch and daily timer
8. Full test: all detail panel actions; retain toggle; auto-delete; reveal file

### Phase 4

1. `recorder/backends/coreaudio.py` — TCC permission check
2. `recorder/backends/pulseaudio.py` — GStreamer check
3. macOS + Linux smoke tests
4. Per-platform PyInstaller builds
5. GitHub Actions matrix build

---

## Success Criteria — Full Product

- Record, pause, resume, stop — audio streams to disk; RAM stays flat for any call length
- Simulated crash mid-recording → recovery dialog on relaunch → recovered WAV in workspace
- Live notes saved periodically; recovered on crash
- Naming prompt on stop; skippable; display name shows everywhere in UI
- First-run wizard installs backend without external tools; progress visible; re-runnable
- Transcription server starts automatically with the GUI; health shown in Settings
- Queue handles multiple recordings; Pause Queue and per-job Cancel work correctly
- Global hotkeys trigger recording; unresolvable conflicts warned clearly in Settings
- Cloud backend triggers one-time consent dialog; not shown again
- Auto-delete purges aged recordings; skips `retain: true` ones
- Reveal file opens Explorer with the file selected on Windows
- All recording actions (transcribe, retranscribe, name speakers, copy, delete, notes) work in the detail panel
- Windows `.exe` is the only artifact a user needs
