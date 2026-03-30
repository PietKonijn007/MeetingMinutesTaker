"""Transcription engine using faster-whisper."""

from __future__ import annotations

import time
from pathlib import Path

from meeting_minutes.config import TranscriptionConfig
from meeting_minutes.models import TranscriptSegment, TranscriptionResult, WordTimestamp


class TranscriptionEngine:
    """Convert audio to text with timestamps and confidence scores."""

    def __init__(self, config: TranscriptionConfig) -> None:
        self._config = config
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel  # lazy import

            self._model = WhisperModel(
                self._config.whisper_model,
                device="cpu",
                compute_type="int8",
            )
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            ) from exc
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
