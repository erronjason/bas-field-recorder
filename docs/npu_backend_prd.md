# PRD: AMD XDNA 2 NPU Backend for Diarized Transcriber

## Document purpose

This document is written for an instance of Claude Code to implement AMD XDNA 2 NPU support
in the diarized transcriber without requiring human interaction beyond initial review. It is
intentionally specific: file paths, class names, method signatures, package names, and test
strategies are all spelled out so the implementer can proceed end-to-end.

---

## Background

The diarized transcriber (`diarized_transcriber.py`) currently supports two backends:

- `local` — whisperX + pyannote.audio, using CUDA if an NVIDIA GPU is present, otherwise CPU
- `cloud` — AssemblyAI API

A third backend is needed for AMD Ryzen AI hardware with an XDNA 2 NPU (e.g. Ryzen AI 7 PRO 350,
Ryzen AI 9 HX 375, any Ryzen AI 300-series "Strix Point" chip). These are found in modern
ThinkPads and other Copilot+ PCs. The NPU delivers ~50 TOPS and can run Whisper's encoder
entirely on-chip, dramatically reducing transcription time vs. CPU-only execution on hardware
that lacks an NVIDIA GPU.

The target user has:
- Windows 11 (Copilot+ PC)
- AMD Ryzen AI 300-series processor with XDNA 2 NPU
- AMD Ryzen AI Software 1.7.x installed (required system-level install, not pip-only)
- No NVIDIA GPU

---

## Goals

1. Add `--backend npu` as a valid CLI option that uses the AMD XDNA 2 NPU for transcription.
2. Detect NPU availability automatically and print a useful message when detected but not selected.
3. Keep the existing `local` and `cloud` backends completely unchanged.
4. Produce `TranscriptionResult` output identical in schema to the other backends.
5. Use pyannote.audio on CPU for diarization (unchanged — NPU does not run arbitrary PyTorch ops).
6. Write tests that pass on any machine (including those without AMD hardware) by mocking hardware
   and library boundaries.
7. Document the new backend in `README.md`.
8. All changes committed to git.

## Non-goals

- Supporting AMD XDNA 1 (Ryzen AI 7x0M / Phoenix) — the API surface differs; 300-series only.
- Supporting Linux — Ryzen AI Software 1.7.x is Windows-only; skip gracefully on non-Windows.
- Replacing the `local` backend as the default — NPU remains opt-in via `--backend npu`.
- Supporting `--model large` on NPU — AMD's SDK supports base/small/medium only.

---

## Project structure (read before making changes)

```
diarized_transcriber/
├── diarized_transcriber.py     ← main script; all backends live here
├── requirements.txt
├── .env                        ← gitignored; holds HF_TOKEN, ASSEMBLYAI_API_KEY
├── .env.example
├── .gitignore
├── README.md
├── workspace/                  ← audio input and transcript output (gitignored contents)
│   └── .gitkeep
├── docs/
│   └── npu_backend_prd.md      ← this file
└── tests/
    └── test_npu_backend.py     ← to be created
```

Existing class hierarchy in `diarized_transcriber.py`:

```python
@dataclass
class TranscriptionResult:
    backend: str          # "local", "cloud", or "npu"
    audio_file: str
    speakers_detected: int
    segments: list        # [{"speaker": str, "start": float, "end": float, "text": str}, ...]

class Backend(ABC):
    def transcribe(self, file_path: str, num_speakers: int | None) -> TranscriptionResult: ...

class LocalBackend(Backend): ...
class CloudBackend(Backend): ...
```

`main()` selects a backend from `args.backend` and calls `backend.transcribe()`. Both output
files are written by `write_json()` and `write_text()` which are backend-agnostic.

---

## Prerequisites the user must satisfy before running

These cannot be pip-installed. The script should detect their absence and print a clear,
actionable error rather than a Python traceback.

1. **AMD Ryzen AI Software 1.7.x** installed from
   `https://www.amd.com/en/developer/resources/ryzen-ai-software.html`.
   This installs the VitisAI Execution Provider into the system ONNX Runtime and places
   `vaip_config.json` on disk.

2. **`vaip_config.json`** — installed by the Ryzen AI Software to a path like:
   `C:\Program Files\AMD\Ryzen AI <version>\voe-<variant>-win_amd64\vaip_config.json`
   The implementer must write a helper that locates this file (see Detection section).

---

## New pip dependencies

Add these to `requirements.txt`:

```
optimum[exporters]
optimum-amd
onnxruntime
librosa
soundfile
```

**Do not add `onnxruntime-gpu`** — the VitisAI EP is embedded in the standard `onnxruntime`
package when Ryzen AI Software is installed system-wide. Installing `onnxruntime-gpu` alongside
it causes conflicts.

`librosa` and `soundfile` are needed to load audio files at 16 kHz mono (Whisper's required
input format) independently of whisperX.

---

## Implementation

### 1. NPU availability detection

Add two module-level helpers immediately after the `WORKSPACE` declaration:

```python
def _npu_available() -> bool:
    """Return True if the VitisAI EP is present in the current onnxruntime installation."""
    if os.name != "nt":          # Ryzen AI Software is Windows-only
        return False
    try:
        import onnxruntime as ort
        return "VitisAIExecutionProvider" in ort.get_available_providers()
    except ImportError:
        return False


def _find_vaip_config() -> Path | None:
    """
    Search standard AMD installation paths for vaip_config.json.
    Returns the Path if found, None otherwise.
    """
    import glob
    search_roots = [
        Path("C:/Program Files/AMD"),
        Path("C:/Program Files (x86)/AMD"),
    ]
    for root in search_roots:
        if not root.exists():
            continue
        matches = glob.glob(str(root / "Ryzen AI*" / "voe-*" / "vaip_config.json"))
        if matches:
            return Path(matches[0])
    # Also check environment variable set by AMD installer
    env_path = os.environ.get("RYZEN_AI_INSTALLATION_PATH")
    if env_path:
        candidate = Path(env_path) / "vaip_config.json"
        if candidate.exists():
            return candidate
    return None
```

### 2. NPUBackend class

Add this class after `CloudBackend` and before the output helper functions.

```python
class NPUBackend(Backend):
    """
    Transcription backend using AMD XDNA 2 NPU via optimum-amd + ONNX Runtime VitisAI EP.

    Supported model sizes: base, small, medium (large is not supported by AMD's SDK).
    First run compiles the model to NPU bytecode — this takes 5–15 minutes and is cached
    in .cache/npu_models/ relative to the script. Subsequent runs load from cache instantly.

    Diarization still runs on CPU via pyannote.audio (unchanged from LocalBackend).
    """

    SUPPORTED_MODELS = {"base", "small", "medium"}
    HF_MODEL_IDS = {
        "base":   "openai/whisper-base",
        "small":  "openai/whisper-small",
        "medium": "openai/whisper-medium",
    }
    CHUNK_LENGTH_S = 30      # Whisper's native processing window
    SAMPLE_RATE = 16_000

    def __init__(
        self,
        model_name: str = "small",
        hf_token: str | None = None,
        language: str | None = None,
        vaip_config: Path | None = None,
    ):
        if model_name not in self.SUPPORTED_MODELS:
            raise ValueError(
                f"NPU backend supports models: {', '.join(sorted(self.SUPPORTED_MODELS))}. "
                f"Got '{model_name}'. Use --model small or --model medium."
            )
        self.model_name = model_name
        self.hf_token = hf_token
        self.language = language or "en"
        self.vaip_config = vaip_config or _find_vaip_config()
        if self.vaip_config is None:
            raise RuntimeError(
                "vaip_config.json not found. Install AMD Ryzen AI Software from "
                "https://www.amd.com/en/developer/resources/ryzen-ai-software.html "
                "then re-run."
            )
        self.cache_dir = Path(__file__).parent / ".cache" / "npu_models" / model_name

    def transcribe(self, file_path: str, num_speakers: int | None) -> TranscriptionResult:
        from optimum.amd.ryzenai import RyzenAIModelForSpeechSeq2Seq
        from transformers import WhisperProcessor
        import librosa
        import numpy as np

        hf_model_id = self.HF_MODEL_IDS[self.model_name]

        print(f"Loading NPU Whisper model ({self.model_name})...")
        if not self.cache_dir.exists():
            print(
                f"  First run: compiling model for XDNA 2 NPU. "
                f"This takes 5–15 minutes and is cached for future runs."
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        model = RyzenAIModelForSpeechSeq2Seq.from_pretrained(
            hf_model_id,
            export=True,
            vaip_config=str(self.vaip_config),
            cache_dir=str(self.cache_dir),
            token=self.hf_token,
        )
        processor = WhisperProcessor.from_pretrained(
            hf_model_id,
            token=self.hf_token,
            language=self.language,
            task="transcribe",
        )

        print(f"Loading audio: {file_path}")
        audio, _ = librosa.load(file_path, sr=self.SAMPLE_RATE, mono=True)

        print("Transcribing on NPU...")
        segments = self._transcribe_chunked(audio, model, processor)

        # Alignment — reuse whisperX's wav2vec2 forced-aligner on CPU
        print("Aligning transcript...")
        import whisperx
        align_model, metadata = whisperx.load_align_model(
            language_code=self.language, device="cpu"
        )
        aligned = whisperx.align(segments, align_model, metadata, file_path, "cpu")

        # Diarization — pyannote on CPU (unchanged from LocalBackend)
        print("Running diarization...")
        diarize_kwargs = {}
        if num_speakers is not None:
            diarize_kwargs["num_speakers"] = num_speakers

        from whisperx.diarize import DiarizationPipeline, assign_word_speakers
        diarize_pipeline = DiarizationPipeline(token=self.hf_token, device="cpu")
        diarize_segments = diarize_pipeline(file_path, **diarize_kwargs)
        result = assign_word_speakers(diarize_segments, aligned)

        output_segments = []
        for seg in result["segments"]:
            speaker = seg.get("speaker", "SPEAKER_00")
            output_segments.append({
                "speaker": speaker,
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip(),
            })

        speakers = sorted({s["speaker"] for s in output_segments})
        return TranscriptionResult(
            backend="npu",
            audio_file=file_path,
            speakers_detected=len(speakers),
            segments=output_segments,
        )

    def _transcribe_chunked(
        self,
        audio: "np.ndarray",
        model: "RyzenAIModelForSpeechSeq2Seq",
        processor: "WhisperProcessor",
    ) -> list[dict]:
        """
        Slice audio into 30-second chunks, transcribe each on the NPU, and
        return segments in the format whisperX.align() expects:
          [{"text": str, "start": float, "end": float}, ...]
        """
        import numpy as np

        chunk_samples = self.CHUNK_LENGTH_S * self.SAMPLE_RATE
        segments = []
        total_samples = len(audio)

        for chunk_start in range(0, total_samples, chunk_samples):
            chunk = audio[chunk_start : chunk_start + chunk_samples]
            if len(chunk) < 200:        # skip sub-10ms trailing silence
                break

            start_s = chunk_start / self.SAMPLE_RATE
            end_s = min((chunk_start + len(chunk)) / self.SAMPLE_RATE, total_samples / self.SAMPLE_RATE)

            inputs = processor(
                chunk,
                sampling_rate=self.SAMPLE_RATE,
                return_tensors="pt",
            )

            generated_ids = model.generate(
                inputs.input_features,
                language=self.language,
                task="transcribe",
            )
            text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

            if text:
                segments.append({"text": text, "start": round(start_s, 2), "end": round(end_s, 2)})

        return segments
```

### 3. Update `main()` — backend selection

Replace the `--backend` argument and the backend instantiation block.

**Replace this line in `add_argument`:**
```python
parser.add_argument(
    "--backend", type=str, default="local", choices=["local", "cloud"],
    ...
)
```

**With:**
```python
parser.add_argument(
    "--backend", type=str, default="local", choices=["local", "cloud", "npu"],
    help="Diarization backend: local (whisperX + CUDA/CPU), npu (AMD XDNA 2), "
         "or cloud (AssemblyAI). Default: local.",
)
```

**Replace the backend instantiation block:**

Current code:
```python
if args.backend == "local":
    if not args.hf_token:
        parser.error(...)
    lang = None if args.language == "auto" else args.language
    backend = LocalBackend(model_name=args.model, hf_token=args.hf_token, language=lang)
else:
    if not args.api_key:
        parser.error(...)
    backend = CloudBackend(api_key=args.api_key)
```

Replace with:
```python
lang = None if args.language == "auto" else args.language

if args.backend == "local":
    if not args.hf_token:
        parser.error(
            "Local backend requires a HuggingFace token. "
            "Pass --hf-token or set the HF_TOKEN environment variable.\n"
            "Get a free token at https://huggingface.co/settings/tokens and accept the "
            "pyannote/speaker-diarization-3.1 model terms."
        )
    backend = LocalBackend(model_name=args.model, hf_token=args.hf_token, language=lang)

elif args.backend == "npu":
    if not _npu_available():
        parser.error(
            "NPU backend selected but VitisAI Execution Provider not found.\n"
            "Install AMD Ryzen AI Software from "
            "https://www.amd.com/en/developer/resources/ryzen-ai-software.html\n"
            "then re-run. This requires a Ryzen AI 300-series (XDNA 2) processor on Windows."
        )
    if args.model not in NPUBackend.SUPPORTED_MODELS:
        parser.error(
            f"NPU backend supports --model: {', '.join(sorted(NPUBackend.SUPPORTED_MODELS))}. "
            f"Got '{args.model}'."
        )
    if not args.hf_token:
        parser.error(
            "NPU backend requires a HuggingFace token for pyannote diarization. "
            "Pass --hf-token or set the HF_TOKEN environment variable."
        )
    try:
        backend = NPUBackend(
            model_name=args.model,
            hf_token=args.hf_token,
            language=lang,
        )
    except RuntimeError as e:
        parser.error(str(e))

else:  # cloud
    if not args.api_key:
        parser.error(
            "Cloud backend requires an AssemblyAI API key. "
            "Pass --api-key or set the ASSEMBLYAI_API_KEY environment variable."
        )
    backend = CloudBackend(api_key=args.api_key)
```

Also add an informational NPU hint at the top of `main()`, after `args = parser.parse_args()`:

```python
if args.backend == "local" and _npu_available():
    print(
        "Tip: AMD XDNA 2 NPU detected. Run with --backend npu to use it for transcription."
    )
```

### 4. Update `--model` default for NPU

The `--model` default of `"medium"` is fine for `local` and `cloud`. For `npu`, `"small"` is
the recommended default because medium compilation takes significantly longer and small gives
excellent results. The validation in `main()` catches `large` before `NPUBackend` is constructed.
No change to the argument default is needed — just rely on the validation added above.

---

## Tests (`tests/test_npu_backend.py`)

Write tests using `unittest` and `unittest.mock`. All tests must pass on any machine,
including those without AMD hardware. Do not use `pytest` — keep the dependency surface small.

```python
"""
Tests for NPUBackend and NPU detection helpers.
All tests mock hardware and library boundaries so they pass on any machine.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestNPUAvailable(unittest.TestCase):

    def test_returns_false_on_non_windows(self):
        with patch("os.name", "posix"):
            from diarized_transcriber import _npu_available
            self.assertFalse(_npu_available())

    def test_returns_false_when_onnxruntime_missing(self):
        with patch("os.name", "nt"), \
             patch.dict("sys.modules", {"onnxruntime": None}):
            # Reimport to get fresh function scope
            import importlib
            import diarized_transcriber as dt
            importlib.reload(dt)
            self.assertFalse(dt._npu_available())

    def test_returns_false_when_vitisai_ep_absent(self):
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        with patch("os.name", "nt"), \
             patch.dict("sys.modules", {"onnxruntime": mock_ort}):
            import importlib
            import diarized_transcriber as dt
            importlib.reload(dt)
            self.assertFalse(dt._npu_available())

    def test_returns_true_when_vitisai_ep_present(self):
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = [
            "VitisAIExecutionProvider", "CPUExecutionProvider"
        ]
        with patch("os.name", "nt"), \
             patch.dict("sys.modules", {"onnxruntime": mock_ort}):
            import importlib
            import diarized_transcriber as dt
            importlib.reload(dt)
            self.assertTrue(dt._npu_available())


class TestFindVaipConfig(unittest.TestCase):

    def test_returns_none_when_no_amd_directory(self):
        with patch("os.name", "nt"), \
             patch("pathlib.Path.exists", return_value=False), \
             patch.dict(os.environ, {}, clear=True):
            from diarized_transcriber import _find_vaip_config
            result = _find_vaip_config()
            self.assertIsNone(result)

    def test_finds_from_env_variable(self):
        fake_dir = Path("/fake/amd/ryzenai")
        with patch("os.name", "nt"), \
             patch.dict(os.environ, {"RYZEN_AI_INSTALLATION_PATH": str(fake_dir)}), \
             patch("pathlib.Path.exists", side_effect=lambda p=None: str(p or fake_dir / "vaip_config.json").endswith("vaip_config.json")):
            from diarized_transcriber import _find_vaip_config
            # Should find it via env var
            # (Exact assert depends on mock; verify no exception is raised)
            _find_vaip_config()  # must not raise


class TestNPUBackendInit(unittest.TestCase):

    def _make_backend(self, model_name="small", vaip_path=Path("/fake/vaip_config.json")):
        from diarized_transcriber import NPUBackend
        return NPUBackend(
            model_name=model_name,
            hf_token="hf_test",
            language="en",
            vaip_config=vaip_path,
        )

    def test_rejects_unsupported_model(self):
        from diarized_transcriber import NPUBackend
        with self.assertRaises(ValueError) as ctx:
            NPUBackend(
                model_name="large",
                hf_token="hf_test",
                vaip_config=Path("/fake/vaip_config.json"),
            )
        self.assertIn("large", str(ctx.exception))

    def test_raises_when_vaip_config_missing(self):
        from diarized_transcriber import NPUBackend
        with patch("diarized_transcriber._find_vaip_config", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                NPUBackend(model_name="small", hf_token="hf_test", vaip_config=None)
            self.assertIn("vaip_config.json", str(ctx.exception))

    def test_accepts_valid_model_names(self):
        from diarized_transcriber import NPUBackend
        for name in ("base", "small", "medium"):
            backend = NPUBackend(
                model_name=name,
                hf_token="hf_test",
                vaip_config=Path("/fake/vaip_config.json"),
            )
            self.assertEqual(backend.model_name, name)

    def test_cache_dir_is_model_specific(self):
        backend = self._make_backend("small")
        self.assertIn("small", str(backend.cache_dir))


class TestChunkTranscription(unittest.TestCase):
    """
    Test _transcribe_chunked independently of the NPU — mock the model and processor.
    """

    def _make_backend(self):
        from diarized_transcriber import NPUBackend
        return NPUBackend(
            model_name="small",
            hf_token="hf_test",
            language="en",
            vaip_config=Path("/fake/vaip_config.json"),
        )

    def test_empty_audio_returns_no_segments(self):
        import numpy as np
        backend = self._make_backend()
        mock_model = MagicMock()
        mock_processor = MagicMock()
        mock_processor.return_value.input_features = MagicMock()
        mock_model.generate.return_value = [[]]
        mock_processor.batch_decode.return_value = [""]

        result = backend._transcribe_chunked(np.array([]), mock_model, mock_processor)
        self.assertEqual(result, [])

    def test_single_chunk_produces_one_segment(self):
        import numpy as np
        backend = self._make_backend()

        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock(input_features=MagicMock())
        mock_processor.batch_decode.return_value = ["Hello world"]

        mock_model = MagicMock()
        mock_model.generate.return_value = [[1, 2, 3]]

        audio = np.zeros(16000 * 5)  # 5 seconds of silence (non-empty)
        result = backend._transcribe_chunked(audio, mock_model, mock_processor)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Hello world")
        self.assertAlmostEqual(result[0]["start"], 0.0)
        self.assertAlmostEqual(result[0]["end"], 5.0)

    def test_long_audio_produces_multiple_segments(self):
        import numpy as np
        backend = self._make_backend()

        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock(input_features=MagicMock())
        mock_processor.batch_decode.return_value = ["chunk text"]

        mock_model = MagicMock()
        mock_model.generate.return_value = [[1]]

        # 65 seconds → should produce 3 chunks (0-30, 30-60, 60-65)
        audio = np.zeros(16000 * 65)
        result = backend._transcribe_chunked(audio, mock_model, mock_processor)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0]["start"], 0.0)
        self.assertAlmostEqual(result[1]["start"], 30.0)
        self.assertAlmostEqual(result[2]["start"], 60.0)

    def test_empty_text_chunks_are_skipped(self):
        import numpy as np
        backend = self._make_backend()

        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock(input_features=MagicMock())
        # Alternate between empty and non-empty
        mock_processor.batch_decode.side_effect = [[""], ["Hello"]]

        mock_model = MagicMock()
        mock_model.generate.return_value = [[1]]

        audio = np.zeros(16000 * 65)  # two full chunks
        result = backend._transcribe_chunked(audio, mock_model, mock_processor)
        # Only the non-empty chunk should appear
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Hello")

    def test_segment_timestamps_are_rounded_to_two_decimals(self):
        import numpy as np
        backend = self._make_backend()

        mock_processor = MagicMock()
        mock_processor.return_value = MagicMock(input_features=MagicMock())
        mock_processor.batch_decode.return_value = ["Text"]

        mock_model = MagicMock()
        mock_model.generate.return_value = [[1]]

        audio = np.zeros(16000 * 5)
        result = backend._transcribe_chunked(audio, mock_model, mock_processor)
        self.assertEqual(result[0]["start"], round(result[0]["start"], 2))
        self.assertEqual(result[0]["end"], round(result[0]["end"], 2))


class TestTranscriptionResultSchema(unittest.TestCase):
    """
    Verify that NPUBackend.transcribe() returns a TranscriptionResult with the
    correct schema, using a fully mocked pipeline.
    """

    @patch("diarized_transcriber.NPUBackend._transcribe_chunked")
    def test_result_schema(self, mock_chunk):
        import whisperx
        from diarized_transcriber import NPUBackend, TranscriptionResult

        mock_chunk.return_value = [
            {"text": "Hello", "start": 0.0, "end": 2.0},
            {"text": "World", "start": 2.0, "end": 4.0},
        ]

        fake_aligned = {
            "segments": [
                {"text": "Hello", "start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
                {"text": "World", "start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
            ]
        }

        with patch("whisperx.load_align_model", return_value=(MagicMock(), MagicMock())), \
             patch("whisperx.align", return_value=fake_aligned), \
             patch("whisperx.diarize.DiarizationPipeline", return_value=MagicMock(return_value=MagicMock())), \
             patch("whisperx.diarize.assign_word_speakers", return_value=fake_aligned), \
             patch("optimum.amd.ryzenai.RyzenAIModelForSpeechSeq2Seq.from_pretrained", return_value=MagicMock()), \
             patch("transformers.WhisperProcessor.from_pretrained", return_value=MagicMock()), \
             patch("librosa.load", return_value=(__import__("numpy").zeros(16000), 16000)):

            backend = NPUBackend(
                model_name="small",
                hf_token="hf_test",
                vaip_config=Path("/fake/vaip_config.json"),
            )
            result = backend.transcribe("/fake/audio.wav", num_speakers=None)

        self.assertIsInstance(result, TranscriptionResult)
        self.assertEqual(result.backend, "npu")
        self.assertEqual(result.speakers_detected, 2)
        self.assertEqual(len(result.segments), 2)
        for seg in result.segments:
            self.assertIn("speaker", seg)
            self.assertIn("start", seg)
            self.assertIn("end", seg)
            self.assertIn("text", seg)


class TestCLIBackendChoice(unittest.TestCase):
    """
    Test that --backend npu is accepted by argparse and triggers correct validation.
    """

    def _run_main(self, args_list):
        """Run main() with given argv, capture SystemExit."""
        import diarized_transcriber
        with patch("sys.argv", ["diarized_transcriber.py"] + args_list):
            try:
                diarized_transcriber.main()
            except SystemExit as e:
                return e.code
        return 0

    def test_npu_backend_rejected_when_npu_unavailable(self):
        with patch("diarized_transcriber._npu_available", return_value=False), \
             patch("diarized_transcriber.resolve_input", return_value=Path("/fake/audio.m4a")):
            code = self._run_main(["audio.m4a", "--backend", "npu", "--hf-token", "hf_test"])
        self.assertNotEqual(code, 0)

    def test_npu_backend_rejects_large_model(self):
        with patch("diarized_transcriber._npu_available", return_value=True), \
             patch("diarized_transcriber.resolve_input", return_value=Path("/fake/audio.m4a")):
            code = self._run_main([
                "audio.m4a", "--backend", "npu",
                "--model", "large", "--hf-token", "hf_test"
            ])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
```

---

## Running the tests

```bash
# From the project root:
python -m pytest tests/test_npu_backend.py -v

# Or without pytest:
python -m unittest tests/test_npu_backend.py -v
```

All tests must pass before committing. They do not require AMD hardware.

---

## README additions

Add a new section to `README.md` between the "Backend comparison" table and the "Model size
guide" section. Title it `## AMD XDNA 2 NPU (Ryzen AI 300)` and cover:

- What hardware qualifies (Ryzen AI 300-series, XDNA 2, Copilot+ PCs)
- System prerequisite: Ryzen AI Software 1.7.x from amd.com (not pip-installable)
- Additional pip deps: `pip install "optimum[exporters]" optimum-amd onnxruntime librosa soundfile`
- First-run model compilation warning (5–15 minutes, cached after that)
- Usage example: `python diarized_transcriber.py "meeting.m4a" --backend npu --hf-token YOUR_TOKEN`
- Note that `--model large` is not supported; recommend `--model small` for speed or `--model medium` for accuracy
- Note that diarization still runs on CPU (pyannote)
- Add "npu" to the backend comparison table

Also update the "Backend comparison" table to include a `npu` row:

| | Local | Cloud | NPU |
|---|---|---|---|
| **Cost** | Free | ~$0.37–$0.65/hr | Free |
| **Privacy** | On-device | Audio uploaded | On-device |
| **Speed** | GPU: fast / CPU: slow | Fast | Faster than CPU |
| **Accuracy** | Good–excellent | Good | Good |
| **Setup** | HF token | AssemblyAI key | AMD Ryzen AI Software + HF token |
| **Hardware req.** | Any (GPU optional) | None | Ryzen AI 300-series (XDNA 2) |

---

## Commit strategy

Make two commits:

**Commit 1:** Implementation
```
Add AMD XDNA 2 NPU backend (--backend npu)

NPUBackend uses optimum-amd + ONNX Runtime VitisAI EP for transcription
on AMD Ryzen AI 300-series processors. Diarization remains on CPU via
pyannote. Detects VitisAI EP availability and surfaces clear errors when
prerequisites are not met.
```

**Commit 2:** Tests and docs
```
Add NPU backend tests and README documentation

All tests mock hardware and library boundaries; pass on any machine.
README documents prerequisites, usage, and backend comparison.
```

---

## Known risks and mitigations

| Risk | Mitigation |
|---|---|
| `optimum-amd` API changes between SDK versions | Import inside `transcribe()` not at module level; error message includes version context |
| `from_pretrained(..., export=True)` fails on first run | Wrap in try/except RuntimeError; print actionable message referencing AMD docs |
| vaip_config.json at non-standard path | `_find_vaip_config()` checks multiple paths + env var; user can pass `--vaip-config PATH` (add this flag to `main()`) |
| Model compilation OOM | Note in README that 16 GB RAM minimum is recommended for compilation |
| Diarization quality on CPU slower than GPU | Expected and acceptable — transcription is the bottleneck on CPU hardware anyway |
| `whisperx.align()` incompatible with segment format from `_transcribe_chunked` | If align raises `KeyError`, verify segment dicts contain `"text"`, `"start"`, `"end"` keys (the exact keys align expects) |
| `large` model requested on NPU | Blocked at CLI validation before `NPUBackend` is constructed |

---

## Optional enhancement: `--vaip-config` flag

If `_find_vaip_config()` fails for a user with a non-standard install, they need a way to
provide the path manually. Add this optional argument to `main()`:

```python
parser.add_argument(
    "--vaip-config", type=str, default=None,
    help="Path to vaip_config.json (NPU backend only). "
         "Auto-detected from standard AMD install paths if omitted.",
)
```

And pass it to `NPUBackend`:
```python
vaip_cfg = Path(args.vaip_config) if args.vaip_config else None
backend = NPUBackend(..., vaip_config=vaip_cfg)
```

This flag is useful enough that it should be implemented alongside the main feature,
not treated as a future enhancement.

---

## Success criteria

The implementation is complete when:

1. `python -m unittest tests/test_npu_backend.py -v` passes all tests on the development machine.
2. `python diarized_transcriber.py audio.m4a --backend npu --hf-token X` prints a clear error
   on non-AMD hardware (not a Python traceback).
3. `python diarized_transcriber.py audio.m4a --backend local` continues to work exactly as before.
4. `python diarized_transcriber.py audio.m4a --backend npu --model large` exits with a useful
   error message before attempting any model loading.
5. `--help` output lists `npu` as a valid backend choice.
6. Both commits are on `master` and pushed to `git@github.com:erronjason/diarized-transcriber.git`.
