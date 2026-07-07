import argparse
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path

WORKSPACE = Path(__file__).parent / "workspace"


@dataclass
class TranscriptionResult:
    backend: str
    audio_file: str
    speakers_detected: int
    segments: list


class Backend(ABC):
    @abstractmethod
    def transcribe(self, file_path: str, num_speakers: int | None) -> TranscriptionResult:
        ...


class LocalBackend(Backend):
    def __init__(self, model_name: str = "medium", hf_token: str | None = None, language: str | None = None):
        self.model_name = model_name
        self.hf_token = hf_token
        self.language = language

    def transcribe(self, file_path: str, num_speakers: int | None) -> TranscriptionResult:
        import torch
        import whisperx

        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        print(f"Using device: {device} ({compute_type})")

        print("Loading Whisper model...")
        model = whisperx.load_model(self.model_name, device, compute_type=compute_type, language=self.language)

        print(f"Transcribing: {file_path}")
        result = model.transcribe(file_path, language=self.language)
        language = self.language or result.get("language", "en")

        print("Aligning transcript...")
        align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
        result = whisperx.align(result["segments"], align_model, metadata, file_path, device)

        print("Running diarization...")
        diarize_kwargs = {}
        if num_speakers is not None:
            diarize_kwargs["num_speakers"] = num_speakers

        from whisperx.diarize import DiarizationPipeline, assign_word_speakers
        diarize_pipeline = DiarizationPipeline(token=self.hf_token, device=device)
        diarize_segments = diarize_pipeline(file_path, **diarize_kwargs)

        result = assign_word_speakers(diarize_segments, result)

        segments = []
        for seg in result["segments"]:
            speaker = seg.get("speaker", "SPEAKER_00")
            segments.append({
                "speaker": speaker,
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip(),
            })

        speakers = sorted({s["speaker"] for s in segments})
        return TranscriptionResult(
            backend="local",
            audio_file=file_path,
            speakers_detected=len(speakers),
            segments=segments,
        )


class CloudBackend(Backend):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def transcribe(self, file_path: str, num_speakers: int | None) -> TranscriptionResult:
        import assemblyai as aai

        aai.settings.api_key = self.api_key

        config_kwargs = {"speaker_labels": True}
        if num_speakers is not None:
            config_kwargs["speakers_expected"] = num_speakers

        config = aai.TranscriptionConfig(**config_kwargs)

        print(f"Uploading and transcribing via AssemblyAI: {file_path}")
        transcript = aai.Transcriber().transcribe(file_path, config=config)

        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")

        segments = []
        for utt in transcript.utterances or []:
            segments.append({
                "speaker": f"SPEAKER_{utt.speaker}",
                "start": round(utt.start / 1000, 2),
                "end": round(utt.end / 1000, 2),
                "text": utt.text.strip(),
            })

        speakers = sorted({s["speaker"] for s in segments})
        return TranscriptionResult(
            backend="cloud",
            audio_file=file_path,
            speakers_detected=len(speakers),
            segments=segments,
        )


def _speaker_label(speaker_id: str, speaker_index: dict) -> str:
    if speaker_id not in speaker_index:
        speaker_index[speaker_id] = len(speaker_index) + 1
    return f"Speaker {speaker_index[speaker_id]}"


def write_json(result: TranscriptionResult, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2, ensure_ascii=False)


def write_text(result: TranscriptionResult, path: str) -> None:
    speaker_index = {}
    lines = []
    for seg in result.segments:
        label = _speaker_label(seg["speaker"], speaker_index)
        lines.append(f"[{label}] ({seg['start']:.2f}s - {seg['end']:.2f}s): {seg['text']}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def resolve_input(file_arg: str) -> Path:
    p = Path(file_arg)
    if p.is_absolute() or p.exists():
        return p
    candidate = WORKSPACE / p
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Audio file not found: '{file_arg}'\n"
        f"Looked in current directory and workspace ({WORKSPACE})"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio with real speaker diarization.",
        epilog=(
            "Drop your audio file into the workspace/ folder, then run:\n"
            "  python whisper_transcription.py 'my recording.m4a' --hf-token YOUR_TOKEN\n\n"
            "Output files (JSON + TXT) are written to workspace/ alongside your audio."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "file", type=str,
        help="Audio filename (looked up in workspace/) or full path. "
             "Supported: wav, m4a, mp3, mp4, ogg, flac, webm.",
    )
    parser.add_argument(
        "--backend", type=str, default="local", choices=["local", "cloud"],
        help="Diarization backend (default: local).",
    )
    parser.add_argument(
        "--speakers", type=int, default=None,
        help="Number of speakers. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--model", type=str, default="medium",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size for local backend (default: medium).",
    )
    parser.add_argument(
        "--language", type=str, default="en",
        help="Language code (default: en). Pass 'auto' to auto-detect.",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output base name (no extension). Defaults to the input filename stem. "
             "Output files are written to workspace/.",
    )
    parser.add_argument(
        "--hf-token", type=str, default=os.environ.get("HF_TOKEN"),
        help="HuggingFace token for pyannote models (local backend). Falls back to HF_TOKEN env var.",
    )
    parser.add_argument(
        "--api-key", type=str, default=os.environ.get("ASSEMBLYAI_API_KEY"),
        help="AssemblyAI API key (cloud backend). Falls back to ASSEMBLYAI_API_KEY env var.",
    )

    args = parser.parse_args()

    supported = {".wav", ".m4a", ".mp3", ".mp4", ".ogg", ".flac", ".webm"}
    ext = os.path.splitext(args.file)[1].lower()
    if ext not in supported:
        parser.error(f"Unsupported file type '{ext}'. Supported formats: {', '.join(sorted(supported))}")

    try:
        audio_path = resolve_input(args.file)
    except FileNotFoundError as e:
        parser.error(str(e))

    output_stem = args.output or audio_path.stem
    WORKSPACE.mkdir(exist_ok=True)
    json_path = WORKSPACE / f"{output_stem}.json"
    txt_path = WORKSPACE / f"{output_stem}.txt"

    if args.backend == "local":
        if not args.hf_token:
            parser.error(
                "Local backend requires a HuggingFace token. "
                "Pass --hf-token or set the HF_TOKEN environment variable.\n"
                "Get a free token at https://huggingface.co/settings/tokens and accept the "
                "pyannote/speaker-diarization-3.1 model terms."
            )
        lang = None if args.language == "auto" else args.language
        backend = LocalBackend(model_name=args.model, hf_token=args.hf_token, language=lang)
    else:
        if not args.api_key:
            parser.error(
                "Cloud backend requires an AssemblyAI API key. "
                "Pass --api-key or set the ASSEMBLYAI_API_KEY environment variable."
            )
        backend = CloudBackend(api_key=args.api_key)

    try:
        result = backend.transcribe(str(audio_path), num_speakers=args.speakers)

        write_json(result, str(json_path))
        write_text(result, str(txt_path))

        print(f"Done. Detected {result.speakers_detected} speaker(s).")
        print(f"  JSON -> {json_path}")
        print(f"  Text -> {txt_path}")
    except Exception as e:
        print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
