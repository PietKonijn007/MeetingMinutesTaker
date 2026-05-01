"""Speaker diarization — public façade over pluggable backends.

The actual diarization implementations live under
``meeting_minutes.system1.diarization_backends``. This module is a thin
selector + a few helper static methods that the rest of the pipeline
already imports from here. Keeping the public surface here means callers
don't need to know about the backend selector.

To add a new backend, see ``diarization_backends/__init__.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.logging import get_logger
from meeting_minutes.models import DiarizationResult
from meeting_minutes.system1.diarization_backends import (
    DiarizationBackend,
    select_backend,
)

logger = get_logger("system1.diarize")


class DiarizationEngine:
    """Public façade — dispatches to the configured backend.

    The constructor selects an implementation based on
    ``config.engine`` (see ``DiarizationConfig``). All callers continue to
    use ``engine.diarize(...)`` and ``engine.last_cluster_embeddings`` as
    before; the swap is transparent.
    """

    SPEAKER_LABEL_PATTERN = re.compile(r"^SPEAKER_\d{2}$")

    def __init__(self, config: DiarizationConfig) -> None:
        self._config = config
        self._backend: DiarizationBackend = select_backend(config)

    def diarize(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> DiarizationResult:
        """Run diarization through the configured backend."""
        return self._backend.diarize(
            audio_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )

    @property
    def last_cluster_embeddings(self) -> dict:
        """Per-cluster (SPEAKER_XX) mean embeddings from the most recent
        ``diarize()`` call. Empty when the active backend doesn't expose
        them (e.g. cloud backends without voiceprint output)."""
        return self._backend.last_cluster_embeddings

    @property
    def supports_embeddings(self) -> bool:
        """Whether the current backend can produce speaker embeddings.

        SPK-1 cross-meeting re-identification reads this to decide whether
        to attempt centroid matching against known persons.
        """
        return self._backend.supports_embeddings

    @property
    def backend(self) -> DiarizationBackend:
        """Escape hatch for tests / advanced callers."""
        return self._backend

    # ------------------------------------------------------------------
    # Static helpers — backend-agnostic post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def apply_speaker_names(
        diarization_result: DiarizationResult,
        user_names: list[str],
    ) -> dict[str, str]:
        """Map SPEAKER_XX diarization labels to user-provided names.

        Assumes user_names are given in the order speakers first appear in
        the audio. The first speaker to talk gets ``user_names[0]``, the
        second gets ``user_names[1]``, etc. Mutates
        ``diarization_result.segments`` in place. Returns the mapping for
        logging.
        """
        if not user_names or not diarization_result.segments:
            return {}

        sorted_segs = sorted(diarization_result.segments, key=lambda s: s.start)
        first_seen: list[str] = []
        for seg in sorted_segs:
            if seg.speaker not in first_seen:
                first_seen.append(seg.speaker)

        label_to_name: dict[str, str] = {}
        for i, label in enumerate(first_seen):
            if i < len(user_names):
                name = (user_names[i] or "").strip()
                if name:
                    label_to_name[label] = name

        if label_to_name:
            for seg in diarization_result.segments:
                if seg.speaker in label_to_name:
                    seg.speaker = label_to_name[seg.speaker]
        return label_to_name

    @staticmethod
    def _normalize_label(raw_label: str) -> str:
        """Coerce any backend's speaker label into SPEAKER_XX form."""
        from meeting_minutes.system1.diarization_backends import normalize_label
        return normalize_label(raw_label)

    @staticmethod
    def merge_transcript_with_diarization(
        transcript_segments,
        diarization_result: DiarizationResult,
    ):
        """Assign speaker labels to transcript segments based on overlap."""
        if not diarization_result.segments:
            return transcript_segments

        for seg in transcript_segments:
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
