"""Transcription engine using faster-whisper.

Supports standard Whisper models (tiny, base, small, medium, large-v3)
and Distil-Whisper models (distil-medium.en, distil-large-v3, etc.)
which are 5-6x faster with <1% quality loss.

Automatically detects Apple Silicon and uses Metal GPU acceleration
via CTranslate2 when available.
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path

from meeting_minutes.config import TranscriptionConfig
from meeting_minutes.models import TranscriptSegment, TranscriptionResult, WordTimestamp

logger = logging.getLogger(__name__)

# Distil-Whisper model mapping (short name → HuggingFace repo)
DISTIL_MODEL_MAP = {
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}


def _detect_best_device() -> tuple[str, str]:
    """Detect the best device and compute type for the current hardware.

    Returns (device, compute_type):
    - Apple Silicon Mac: ("auto", "float16") — uses Metal GPU via CTranslate2
    - NVIDIA GPU: ("cuda", "float16") — uses CUDA
    - CPU fallback: ("cpu", "int8") — quantized for speed
    """
    # Check Apple Silicon (M1/M2/M3/M4)
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        logger.info("Detected Apple Silicon — using Metal acceleration (float16)")
        return "auto", "float16"

    # Check NVIDIA CUDA
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            logger.info("Detected NVIDIA GPU: %s — using CUDA (float16)", gpu_name)
            return "cuda", "float16"
    except ImportError:
        pass

    # CPU fallback
    logger.info("Using CPU with int8 quantization")
    return "cpu", "int8"


class TranscriptionEngine:
    """Convert audio to text with timestamps and confidence scores.

    Supports:
    - Standard models: tiny, base, small, medium, large-v3
    - Distil models: distil-small.en, distil-medium.en, distil-large-v2, distil-large-v3
    - Auto hardware detection: Apple Silicon Metal, NVIDIA CUDA, CPU fallback
    """

    def __init__(self, config: TranscriptionConfig) -> None:
        self._config = config
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel  # lazy import

            model_name = self._config.whisper_model

            # Resolve distil model names to HuggingFace repos
            if model_name in DISTIL_MODEL_MAP:
                resolved_name = DISTIL_MODEL_MAP[model_name]
                logger.info("Using Distil-Whisper model: %s → %s", model_name, resolved_name)
            else:
                resolved_name = model_name

            # Auto-detect best device and compute type
            device, compute_type = _detect_best_device()

            logger.info("Loading Whisper model: %s (device=%s, compute=%s)", resolved_name, device, compute_type)
            t0 = time.time()

            self._model = WhisperModel(
                resolved_name,
                device=device,
                compute_type=compute_type,
            )

            load_time = time.time() - t0
            logger.info("Model loaded in %.1fs", load_time)

        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            ) from exc
        except Exception as exc:
            # If Metal/CUDA fails, fall back to CPU
            logger.warning("Failed to load model with %s/%s: %s — falling back to CPU/int8", device, compute_type, exc)
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(
                    resolved_name,
                    device="cpu",
                    compute_type="int8",
                )
                logger.info("Model loaded on CPU fallback")
            except Exception as cpu_exc:
                raise RuntimeError(f"Failed to load Whisper model: {cpu_exc}") from cpu_exc

        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe audio file. Returns segments with word-level timestamps."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        model = self._load_model()

        language = None if self._config.language == "auto" else self._config.language

        initial_prompt = None
        if self._config.custom_vocabulary:
            initial_prompt = self._config.custom_vocabulary

        start = time.time()
        segments_gen, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            initial_prompt=initial_prompt,
        )

        segments: list[TranscriptSegment] = []
        full_text_parts: list[str] = []

        for i, seg in enumerate(segments_gen):
            words: list[WordTimestamp] = []
            if seg.words:
                for w in seg.words:
                    words.append(
                        WordTimestamp(
                            word=w.word,
                            start=w.start,
                            end=w.end,
                            confidence=w.probability,
                        )
                    )

            transcript_seg = TranscriptSegment(
                id=i,
                start=seg.start,
                end=seg.end,
                speaker=None,
                text=seg.text.strip(),
                words=words,
            )
            segments.append(transcript_seg)
            full_text_parts.append(seg.text.strip())

        full_text = " ".join(full_text_parts)
        detected_language = info.language if hasattr(info, "language") else (language or "en")
        processing_time = time.time() - start

        return TranscriptionResult(
            meeting_id="",  # will be set by caller
            segments=segments,
            full_text=full_text,
            language=detected_language,
            transcription_engine="faster-whisper",
            transcription_model=self._config.whisper_model,
            processing_time_seconds=processing_time,
        )

    def detect_language(self, audio_path: Path) -> str:
        """Detect language of audio file."""
        model = self._load_model()
        _, info = model.transcribe(str(audio_path), language=None)
        return info.language if hasattr(info, "language") else "en"
