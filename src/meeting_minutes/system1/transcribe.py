"""Transcription engine with pluggable backends.

Supports multiple transcription engines via a factory pattern:
- faster-whisper: Default engine using CTranslate2 (standard + distil models)
- whisper-cpp: Alternative engine using whisper.cpp via pywhispercpp (GGML quantized)

Automatically detects Apple Silicon and uses Metal GPU acceleration
via CTranslate2 when available.
"""

from __future__ import annotations

import abc
import logging
import os
import platform
import time
from pathlib import Path

from meeting_minutes.config import TranscriptionConfig
from meeting_minutes.models import TranscriptSegment, TranscriptionResult, WordTimestamp

logger = logging.getLogger(__name__)

# Distil-Whisper model mapping (short name -> HuggingFace repo)
DISTIL_MODEL_MAP = {
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}

# Whisper model presets: quality tier -> model name
WHISPER_PRESETS = {
    "fast": "distil-medium.en",
    "balanced": "medium",
    "best": "large-v3",
}


def _detect_best_device() -> tuple[str, str]:
    """Detect the best device and compute type for the current hardware.

    Returns (device, compute_type):
    - Apple Silicon Mac: ("auto", "float16") -- uses Metal GPU via CTranslate2
    - NVIDIA GPU: ("cuda", "float16") -- uses CUDA
    - CPU fallback: ("cpu", "int8") -- quantized for speed
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


class BaseTranscriptionEngine(abc.ABC):
    """Abstract base for transcription engines."""

    def __init__(self, config: TranscriptionConfig) -> None:
        self._config = config

    @abc.abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe audio file. Returns segments with timestamps."""

    @abc.abstractmethod
    def detect_language(self, audio_path: Path) -> str:
        """Detect language of audio file."""

    @property
    def engine_name(self) -> str:
        return self.__class__.__name__


class FasterWhisperEngine(BaseTranscriptionEngine):
    """Transcription engine using faster-whisper (CTranslate2).

    Supports:
    - Standard models: tiny, base, small, medium, large-v3
    - Distil models: distil-small.en, distil-medium.en, distil-large-v2, distil-large-v3
    - Auto hardware detection: Apple Silicon Metal, NVIDIA CUDA, CPU fallback
    """

    def __init__(self, config: TranscriptionConfig) -> None:
        super().__init__(config)
        self._model = None

    @staticmethod
    def _ensure_cache_dir():
        cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise RuntimeError(
                f"Cannot write to Hugging Face cache directory: {cache}\n"
                f"Fix with: sudo chown -R $(whoami) {cache}\n"
                f"Or set HF_HOME to a writable path in your .env file."
            )

    def _load_model(self):
        if self._model is not None:
            return self._model

        self._ensure_cache_dir()

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
            logger.warning("Failed to load model with GPU: %s — falling back to CPU/int8", exc)
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


class WhisperCppEngine(BaseTranscriptionEngine):
    """Transcription engine using whisper.cpp via pywhispercpp.

    Uses GGML-quantized models for lower memory usage and faster CPU inference.
    Install with: pip install pywhispercpp
    """

    def __init__(self, config: TranscriptionConfig) -> None:
        super().__init__(config)
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from pywhispercpp.model import Model  # lazy import
        except ImportError as exc:
            raise RuntimeError(
                "pywhispercpp is not installed. Run: pip install pywhispercpp"
            ) from exc

        model_name = self._config.whisper_model
        # whisper.cpp uses simpler model names (no distil prefix needed)
        # Map our names to whisper.cpp model sizes
        cpp_model_map = {
            "distil-small.en": "small.en",
            "distil-medium.en": "medium.en",
            "distil-large-v2": "large-v2",
            "distil-large-v3": "large-v3",
        }
        resolved = cpp_model_map.get(model_name, model_name)

        logger.info("Loading whisper.cpp model: %s", resolved)
        t0 = time.time()

        self._model = Model(resolved)

        load_time = time.time() - t0
        logger.info("whisper.cpp model loaded in %.1fs", load_time)

        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Transcribe audio file using whisper.cpp."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        model = self._load_model()

        language = None if self._config.language == "auto" else self._config.language

        start = time.time()

        # pywhispercpp returns a list of segment dicts
        result_segments = model.transcribe(
            str(audio_path),
            language=language or "",
        )

        segments: list[TranscriptSegment] = []
        full_text_parts: list[str] = []

        for i, seg in enumerate(result_segments):
            text = seg.text.strip() if hasattr(seg, "text") else str(seg).strip()
            seg_start = seg.t0 / 100.0 if hasattr(seg, "t0") else 0.0
            seg_end = seg.t1 / 100.0 if hasattr(seg, "t1") else 0.0

            transcript_seg = TranscriptSegment(
                id=i,
                start=seg_start,
                end=seg_end,
                speaker=None,
                text=text,
                words=[],  # whisper.cpp word timestamps require extra config
            )
            segments.append(transcript_seg)
            full_text_parts.append(text)

        full_text = " ".join(full_text_parts)
        processing_time = time.time() - start

        return TranscriptionResult(
            meeting_id="",
            segments=segments,
            full_text=full_text,
            language=language or "en",
            transcription_engine="whisper-cpp",
            transcription_model=self._config.whisper_model,
            processing_time_seconds=processing_time,
        )

    def detect_language(self, audio_path: Path) -> str:
        """Detect language — whisper.cpp auto-detects during transcription."""
        return "en"  # whisper.cpp doesn't expose standalone language detection easily


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Engine registry: engine name -> class
_ENGINE_REGISTRY: dict[str, type[BaseTranscriptionEngine]] = {
    "whisper": FasterWhisperEngine,
    "faster-whisper": FasterWhisperEngine,
    "whisper-cpp": WhisperCppEngine,
    "whisper.cpp": WhisperCppEngine,
}


def get_transcription_engine(config: TranscriptionConfig) -> BaseTranscriptionEngine:
    """Create a transcription engine based on config.primary_engine."""
    engine_name = config.primary_engine
    engine_cls = _ENGINE_REGISTRY.get(engine_name)
    if engine_cls is None:
        available = ", ".join(sorted(_ENGINE_REGISTRY.keys()))
        raise ValueError(
            f"Unknown transcription engine: '{engine_name}'. Available: {available}"
        )
    return engine_cls(config)


def get_available_engines() -> list[dict[str, str]]:
    """Return list of available transcription engines with install status."""
    engines = []

    # faster-whisper
    try:
        import faster_whisper
        fw_status = "installed"
    except ImportError:
        fw_status = "not_installed"
    engines.append({
        "id": "whisper",
        "name": "Faster Whisper (CTranslate2)",
        "description": "Default engine. GPU-accelerated via Metal/CUDA. Best accuracy.",
        "status": fw_status,
    })

    # whisper.cpp
    try:
        import pywhispercpp
        wcpp_status = "installed"
    except ImportError:
        wcpp_status = "not_installed"
    engines.append({
        "id": "whisper-cpp",
        "name": "Whisper.cpp (GGML)",
        "description": "C++ engine with quantized models. Faster on CPU, lower memory.",
        "status": wcpp_status,
    })

    return engines


# Backwards-compatible alias so existing imports still work
TranscriptionEngine = FasterWhisperEngine
