"""Speaker diarization engine using pyannote.audio."""

from __future__ import annotations

import re
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.models import DiarizationResult, DiarizationSegment


class DiarizationEngine:
    """Identify and label distinct speakers using pyannote.audio."""

    SPEAKER_LABEL_PATTERN = re.compile(r"^SPEAKER_\d{2}$")

    def __init__(self, config: DiarizationConfig) -> None:
        self._config = config
        self._pipeline = None

    def _load_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        import os
        import warnings

        try:
            # Suppress torchcodec/ffmpeg warnings from pyannote
            warnings.filterwarnings("ignore", message=".*torchcodec.*")
            from pyannote.audio import Pipeline  # lazy import

            hf_token = os.environ.get("HF_TOKEN") or True
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=hf_token,
            )
        except ImportError as exc:
            raise RuntimeError(
                "pyannote.audio is not installed. Run: pip install pyannote.audio"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load diarization pipeline: {exc}"
            ) from exc
        return self._pipeline

    def diarize(self, audio_path: Path) -> DiarizationResult:
        """Identify speakers. Returns speaker segments with labels."""
        if not self._config.enabled:
            return DiarizationResult(
                meeting_id="",
                segments=[],
                num_speakers=0,
            )

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            pipeline = self._load_pipeline()
            diarization = pipeline(str(audio_path))
        except Exception as exc:
            # Graceful failure — return empty result
            import warnings

            warnings.warn(
                f"Diarization failed, continuing without speaker labels: {exc}"
            )
            return DiarizationResult(
                meeting_id="",
                segments=[],
                num_speakers=0,
            )

        segments: list[DiarizationSegment] = []
        speakers: set[str] = set()

        for turn, _, speaker in diarization.itertracks(yield_label=True):
            # Ensure SPEAKER_XX format
            label = self._normalize_label(speaker)
            segments.append(
                DiarizationSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker=label,
                )
            )
            speakers.add(label)

        return DiarizationResult(
            meeting_id="",
            segments=segments,
            num_speakers=len(speakers),
        )

    @staticmethod
    def _normalize_label(raw_label: str) -> str:
        """Ensure speaker label matches SPEAKER_XX pattern."""
        if re.match(r"^SPEAKER_\d{2}$", raw_label):
            return raw_label
        # Extract digits or assign sequential
        digits = re.findall(r"\d+", raw_label)
        if digits:
            return f"SPEAKER_{int(digits[0]):02d}"
        return "SPEAKER_00"

    @staticmethod
    def merge_transcript_with_diarization(
        transcript_segments,
        diarization_result: DiarizationResult,
    ):
        """Assign speaker labels to transcript segments based on overlap."""
        if not diarization_result.segments:
            return transcript_segments

        for seg in transcript_segments:
            seg_mid = (seg.start + seg.end) / 2
            best_speaker = None
            best_overlap = 0.0

            for d_seg in diarization_result.segments:
                overlap_start = max(seg.start, d_seg.start)
                overlap_end = min(seg.end, d_seg.end)
                overlap = max(0.0, overlap_end - overlap_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = d_seg.speaker

            seg.speaker = best_speaker

        return transcript_segments
