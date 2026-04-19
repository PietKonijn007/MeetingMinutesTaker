"""Speaker diarization engine using pyannote.audio."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.models import DiarizationResult, DiarizationSegment

logger = logging.getLogger(__name__)


class DiarizationEngine:
    """Identify and label distinct speakers using pyannote.audio."""

    SPEAKER_LABEL_PATTERN = re.compile(r"^SPEAKER_\d{2}$")

    def __init__(self, config: DiarizationConfig) -> None:
        self._config = config
        self._pipeline = None
        # Populated by ``diarize()`` when the underlying pipeline exposes
        # per-cluster speaker embeddings (SPK-1).
        self._last_cluster_embeddings: dict = {}

    def _load_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        import os
        import platform
        import warnings

        try:
            # Suppress torchcodec/ffmpeg warnings from pyannote before import
            warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
            warnings.filterwarnings("ignore", message=".*torchcodec.*")
            warnings.filterwarnings("ignore", message=".*libtorchcodec.*")
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # suppress tokenizers warning
            from pyannote.audio import Pipeline  # lazy import

            hf_token = os.environ.get("HF_TOKEN") or True
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=hf_token,
            )

            # Move pipeline to best available device for 5-10x speedup
            try:
                import torch
                if platform.system() == "Darwin" and platform.machine() == "arm64" and torch.backends.mps.is_available():
                    device = torch.device("mps")
                    logger.info("Diarization: using Apple Silicon GPU (MPS)")
                elif torch.cuda.is_available():
                    device = torch.device("cuda")
                    logger.info("Diarization: using NVIDIA CUDA")
                else:
                    device = torch.device("cpu")
                    logger.info("Diarization: using CPU (slow — expect ~1x real-time)")
                self._pipeline.to(device)
            except Exception as device_exc:
                logger.warning("Could not move diarization pipeline to GPU: %s — using CPU", device_exc)
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
        """Identify speakers. Returns speaker segments with labels.

        Side effect: if the underlying pipeline emits per-cluster speaker
        embeddings (pyannote >= 4 on DiarizeOutput.speaker_embeddings), they
        are stashed on ``self._last_cluster_embeddings`` for the caller to
        persist via the SPK-1 pipeline. Callers that don't care can ignore
        this attribute.
        """
        self._last_cluster_embeddings: dict[str, "np.ndarray"] = {}  # type: ignore[name-defined]

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
            # Graceful failure — return empty result with actionable diagnostics
            import warnings

            err_str = str(exc)
            hint = ""
            if "AudioDecoder" in err_str or "torchcodec" in err_str.lower():
                hint = (
                    " — FIX: pyannote.audio 3.3+ requires torchcodec + ffmpeg. "
                    "Run: brew install ffmpeg && pip install torchcodec  "
                    "(or pin: pip install 'pyannote.audio<3.3')"
                )
            elif "401" in err_str or "403" in err_str or "gated" in err_str.lower() or "access" in err_str.lower():
                hint = (
                    " — FIX: HF_TOKEN missing or you haven't accepted pyannote license. "
                    "Visit https://huggingface.co/pyannote/speaker-diarization-3.1 and accept terms."
                )
            elif "ffmpeg" in err_str.lower():
                hint = " — FIX: Install ffmpeg. Run: brew install ffmpeg"

            full_msg = f"Diarization failed: {exc}{hint}"
            logger.warning(full_msg)
            warnings.warn(full_msg)
            return DiarizationResult(
                meeting_id="",
                segments=[],
                num_speakers=0,
            )

        # pyannote.audio 3.3+ returns DiarizeOutput wrapper; older versions
        # return Annotation directly. Unwrap if needed.
        annotation = diarization
        if not hasattr(annotation, "itertracks"):
            for attr in ("speaker_diarization", "diarization", "annotation"):
                candidate = getattr(annotation, attr, None)
                if candidate is not None and hasattr(candidate, "itertracks"):
                    annotation = candidate
                    break
            else:
                raise RuntimeError(
                    f"Diarization output has no usable annotation. "
                    f"Got {type(diarization).__name__} with attrs: {dir(diarization)[:20]}"
                )

        segments: list[DiarizationSegment] = []
        speakers: set[str] = set()
        # Track raw-label → normalized-label so we can align embeddings
        # (which are keyed by pyannote's original label order).
        raw_to_norm: dict[str, str] = {}

        for turn, _, speaker in annotation.itertracks(yield_label=True):
            raw = str(speaker)
            if raw not in raw_to_norm:
                raw_to_norm[raw] = self._normalize_label(raw)
            label = raw_to_norm[raw]
            segments.append(
                DiarizationSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker=label,
                )
            )
            speakers.add(label)

        # Surface per-cluster embeddings if the pipeline produced them
        # (pyannote >= 4 DiarizeOutput). We rebuild a normalized-label map
        # via the same annotation so the SPK-1 layer doesn't need to know
        # about pyannote's raw label strings.
        try:
            from meeting_minutes.system1.speaker_identity import (
                extract_cluster_embeddings,
            )
            raw_embeddings = extract_cluster_embeddings(diarization)
            if raw_embeddings:
                self._last_cluster_embeddings = {
                    raw_to_norm.get(raw, self._normalize_label(raw)): vec
                    for raw, vec in raw_embeddings.items()
                }
        except Exception as embed_exc:  # best-effort; never block diarization
            logger.warning("Could not extract cluster embeddings: %s", embed_exc)

        return DiarizationResult(
            meeting_id="",
            segments=segments,
            num_speakers=len(speakers),
        )

    @property
    def last_cluster_embeddings(self) -> dict:
        """Per-cluster (normalized SPEAKER_XX) mean embeddings from the
        most recent ``diarize()`` call. Empty if the pipeline did not
        surface embeddings. Used by the SPK-1 pipeline layer."""
        return dict(self._last_cluster_embeddings)

    @staticmethod
    def apply_speaker_names(
        diarization_result: DiarizationResult,
        user_names: list[str],
    ) -> dict[str, str]:
        """Map SPEAKER_XX diarization labels to user-provided names.

        Assumes user_names are given in the order speakers first appear in
        the audio. The first speaker to talk gets user_names[0], the second
        gets user_names[1], etc. Mutates diarization_result.segments in place.

        Returns the mapping dict (SPEAKER_XX → name) for logging/reporting.
        Labels with no corresponding user name stay unchanged (fall back to
        SPEAKER_XX).
        """
        if not user_names or not diarization_result.segments:
            return {}

        # Find unique labels in order of first appearance (by start time)
        sorted_segs = sorted(diarization_result.segments, key=lambda s: s.start)
        first_seen: list[str] = []
        for seg in sorted_segs:
            if seg.speaker not in first_seen:
                first_seen.append(seg.speaker)

        # Build mapping: first N labels → first N user_names
        label_to_name: dict[str, str] = {}
        for i, label in enumerate(first_seen):
            if i < len(user_names):
                name = (user_names[i] or "").strip()
                if name:
                    label_to_name[label] = name

        # Rewrite segment labels in place
        if label_to_name:
            for seg in diarization_result.segments:
                if seg.speaker in label_to_name:
                    seg.speaker = label_to_name[seg.speaker]

        return label_to_name

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
