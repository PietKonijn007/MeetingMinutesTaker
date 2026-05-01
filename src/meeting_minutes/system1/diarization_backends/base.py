"""Abstract base for speaker-diarization backends.

The contract is intentionally narrow:

* ``diarize(audio_path, num_speakers, min_speakers, max_speakers)`` runs
  diarization and returns a :class:`DiarizationResult`. Speaker-count hints
  are advisory — backends pass them through to the underlying engine when
  supported and silently ignore them otherwise.
* ``last_cluster_embeddings`` exposes per-cluster speaker embeddings from
  the most recent run, when the backend can produce them. Empty dict when
  not (e.g. cloud backends without voiceprint output).
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.logging import get_logger
from meeting_minutes.models import DiarizationResult

logger = get_logger("system1.diarize")


_LABEL_PATTERN = re.compile(r"^SPEAKER_\d{2}$")


def normalize_label(raw_label: str) -> str:
    """Coerce any backend's speaker label into the canonical SPEAKER_XX form.

    Pyannote uses ``SPEAKER_00`` already; pyannoteAI returns ``SPEAKER_0`` /
    ``SPEAKER_1``; some MLX ports emit numeric IDs. We normalize on the way
    in so downstream code (transcript merge, name mapping, persistence)
    can assume the same shape.
    """
    if _LABEL_PATTERN.match(raw_label):
        return raw_label
    digits = re.findall(r"\d+", raw_label)
    if digits:
        return f"SPEAKER_{int(digits[0]):02d}"
    return "SPEAKER_00"


class StageTimer:
    """Pyannote ``hook`` callable that logs per-stage wall-clock time.

    Pyannote's pipeline calls ``hook(step_name, ...)`` repeatedly — once at
    the start of each major stage and again on progress ticks within it.
    We only care about stage transitions, so we collapse identical
    ``step_name`` calls and emit a log line when the stage changes.

    Backends that don't run pyannote (e.g. the cloud one) don't need this.
    """

    def __init__(self) -> None:
        self._stage: str | None = None
        self._stage_start: float | None = None
        self._t0 = time.monotonic()

    def __call__(
        self,
        step_name: str,
        step_artifact=None,
        file=None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        if step_name == self._stage:
            return
        now = time.monotonic()
        if self._stage is not None and self._stage_start is not None:
            logger.info(
                "Diarization stage: %s done in %.1fs",
                self._stage,
                now - self._stage_start,
            )
        self._stage = step_name
        self._stage_start = now

    def finish(self) -> None:
        now = time.monotonic()
        if self._stage is not None and self._stage_start is not None:
            logger.info(
                "Diarization stage: %s done in %.1fs",
                self._stage,
                now - self._stage_start,
            )
        logger.info("Diarization stages total: %.1fs", now - self._t0)


class DiarizationBackend(ABC):
    """Common base for all diarization implementations."""

    def __init__(self, config: DiarizationConfig) -> None:
        self._config = config
        # Subclasses populate this in ``diarize()`` when they have access to
        # speaker embeddings. The dict is keyed by normalized SPEAKER_XX.
        self._last_cluster_embeddings: dict = {}

    @abstractmethod
    def diarize(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> DiarizationResult:
        """Run diarization on ``audio_path``.

        Returns an empty result on graceful failure (rather than raising)
        so the recording pipeline can continue without speaker labels.
        """

    @property
    def last_cluster_embeddings(self) -> dict:
        """Return per-cluster (SPEAKER_XX) mean embeddings from the latest
        ``diarize()`` call. Empty when the backend doesn't expose them."""
        return dict(self._last_cluster_embeddings)

    @property
    def supports_embeddings(self) -> bool:
        """Whether this backend ever returns speaker embeddings.

        Used by the SPK-1 cross-meeting re-id layer to decide whether to
        run the centroid-matching code path. Backends that always return
        ``False`` here are treated as label-only.
        """
        return False

    @staticmethod
    def empty_result() -> DiarizationResult:
        return DiarizationResult(meeting_id="", segments=[], num_speakers=0)
