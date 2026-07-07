# Diarized Transcriber

Transcribes audio files and automatically detects who is speaking when. Outputs both a structured JSON file and a human-readable text transcript, each segment labeled by speaker.

Built on [whisperX](https://github.com/m-bain/whisperX) (local) and [AssemblyAI](https://www.assemblyai.com/) (cloud).

---

## Features

- **Real speaker diarization** — audio-feature clustering, not round-robin guessing
- **Two backends** — local (free, private, GPU-accelerated) or cloud (faster for multi-speaker)
- **Auto speaker detection** — no need to know the speaker count in advance
- **Dual output** — JSON for downstream processing, plain text for reading
- **GPU acceleration** — automatically uses NVIDIA CUDA if available

---

## Quick Start

**1. Install dependencies**

```bash
pip install -r requirements.txt
```

> First install pulls PyTorch + CUDA (~2.5 GB). For GPU acceleration, install the CUDA build of PyTorch:
> ```bash
> pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
> ```

**2. Get a HuggingFace token** (local backend only — free)

- Create an account at [huggingface.co](https://huggingface.co)
- Go to **Settings → Access Tokens → New token** (read scope is sufficient)
- Accept the model terms at [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)

**3. Drop your audio file into `workspace/`**

```
workspace/
  my recording.m4a   ← put your file here
```

**4. Run**

```bash
python diarized_transcriber.py "my recording.m4a" --hf-token YOUR_HF_TOKEN
```

Output files appear in `workspace/`:
```
workspace/
  my recording.json
  my recording.txt
```

---

## Usage

```
python diarized_transcriber.py <file> [options]
```

### Arguments

| Argument | Description |
|---|---|
| `file` | Audio filename in `workspace/`, or a full path |

### Options

| Option | Default | Description |
|---|---|---|
| `--backend` | `local` | `local` (whisperX + pyannote) or `cloud` (AssemblyAI) |
| `--speakers` | auto | Force a specific speaker count if auto-detection is wrong |
| `--model` | `medium` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` |
| `--language` | `en` | Language code. Pass `auto` to auto-detect |
| `--output` | input filename | Output base name (no extension) |
| `--hf-token` | `$HF_TOKEN` | HuggingFace token (local backend) |
| `--api-key` | `$ASSEMBLYAI_API_KEY` | AssemblyAI API key (cloud backend) |

### Environment variables

Set these to avoid passing tokens on every run:

```bash
export HF_TOKEN=hf_...
export ASSEMBLYAI_API_KEY=...
```

---

## Output formats

### JSON (`workspace/<name>.json`)

```json
{
  "backend": "local",
  "audio_file": "workspace/my recording.m4a",
  "speakers_detected": 2,
  "segments": [
    { "speaker": "SPEAKER_00", "start": 0.83, "end": 2.04, "text": "Good, good." },
    { "speaker": "SPEAKER_01", "start": 2.23, "end": 8.38, "text": "Back from our road trip..." }
  ]
}
```

### Text (`workspace/<name>.txt`)

```
[Speaker 1] (0.83s - 2.04s): Good, good.
[Speaker 2] (2.23s - 8.38s): Back from our road trip to the east...
```

Speaker IDs (`SPEAKER_00`, `SPEAKER_01`, …) are normalized to `Speaker 1`, `Speaker 2`, … in the text output. The JSON retains the raw IDs for programmatic use.

---

## Backend comparison

| | Local | Cloud |
|---|---|---|
| **Cost** | Free | ~$0.37–$0.65/hr of audio |
| **Privacy** | Audio stays on your machine | Audio uploaded to AssemblyAI |
| **Speed (GPU)** | ~10–20x real-time | ~10–15x real-time |
| **Speed (CPU)** | ~0.3–0.5x real-time (slow) | ~10–15x real-time |
| **2-speaker accuracy** | ~85–95% | ~85–90% |
| **5+ speaker accuracy** | ~70–80% | ~85–90% |
| **Setup** | HuggingFace token + model terms | AssemblyAI API key |

### When to use cloud

- No NVIDIA GPU available
- 5+ speaker meetings where accuracy matters most
- You need results fast from a long recording on CPU

```bash
python diarized_transcriber.py "meeting.m4a" --backend cloud --api-key YOUR_KEY
```

---

## Model size guide (local backend)

| Model | VRAM | Speed | Accuracy |
|---|---|---|---|
| `tiny` | ~1 GB | Fastest | Lower |
| `base` | ~1 GB | Fast | Moderate |
| `small` | ~2 GB | Fast | Good |
| `medium` | ~5 GB | Moderate | Better |
| `large` | ~10 GB | Slow | Best |

Use `--model small` if you hit GPU out-of-memory errors with `medium`.

---

## Supported audio formats

`wav`, `m4a`, `mp3`, `mp4`, `ogg`, `flac`, `webm`

---

## Notes on accuracy

- Speaker labels (`Speaker 1`, `Speaker 2`, …) are detected from audio features — the tool has no way to know *who* they are by name. Identify them manually after reviewing the transcript.
- Short back-channel responses ("yeah", "right", "uh-huh") are the most likely to be mis-attributed.
- Accuracy improves when speakers have distinct voices and long uninterrupted turns.
- For recordings with significant silence at the start, pass `--language en` (already the default) to prevent language mis-detection.
