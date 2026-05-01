"""Speaker diarization engine using pyannote.audio."""

from __future__ import annotations

import re
import time
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.logging import get_logger
from meeting_minutes.models import DiarizationResult, DiarizationSegment

# Use the project's structured-JSON logger so device/timing diagnostics land
# in `logs/server.log`. A plain `logging.getLogger(__name__)` here was a dead
# end — no handler was attached anywhere up the chain, so INFO messages from
# this module silently disappeared and we couldn't tell from logs whether
# diarization was actually using MPS, CUDA, or CPU.
logger = get_logger("system1.diarize")


class _StageTimer:
    """pyannote ``hook`` callable that logs per-stage wall-clock time.

    pyannote.audio's diarization pipeline calls ``hook(step_name, ...)``
    multiple times — once at the start of each major stage and again on
    progress ticks. We only care about stage transitions, so we track the
    last-seen ``step_name`` and log when it changes. ``finish()`` flushes
    the final stage. The output looks like::

        Diarization stage: segmentation done in 47.2s
        Diarization stage: embeddings done in 2810.5s
        Diarization stage: discrete_diarization done in 2503.1s

    which tells us at a glance where pyannote is actually spending time
    so we know whether to attack batch sizes (embeddings) or clustering
    (discrete_diarization).
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
            return  # progress tick within the current stage — ignore
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
                    logger.info("Diarization: requesting Apple Silicon GPU (MPS)")
                elif torch.cuda.is_available():
                    device = torch.device("cuda")
                    logger.info("Diarization: requesting NVIDIA CUDA")
                else:
                    device = torch.device("cpu")
                    logger.info("Diarization: requesting CPU (slow — expect ~1x real-time)")
                self._pipeline.to(device)

                # Verify the move actually took effect. pyannote.audio's
                # Pipeline.to() walks submodels, but historically some
                # submodels (notably the speaker-embedding model) silently
                # stayed on CPU because of MPS operator gaps. Read the device
                # back from the pipeline state so the logs reflect reality
                # rather than intent.
                seg_device = embed_device = "?"
                try:
                    seg_model = getattr(getattr(self._pipeline, "_segmentation", None), "model", None)
                    if seg_model is not None:
                        seg_device = str(next(seg_model.parameters()).device)
                except Exception:  # pragma: no cover — diagnostics only
                    pass
                try:
                    emb = getattr(self._pipeline, "_embedding", None)
                    # PretrainedSpeakerEmbedding may wrap a nn.Module under
                    # `.model_` or be one itself; try both.
                    emb_module = getattr(emb, "model_", emb)
                    if emb_module is not None and hasattr(emb_module, "parameters"):
                        embed_device = str(next(emb_module.parameters()).device)
                    elif emb is not None:
                        # Fall back to the wrapper's reported device, if any.
                        emb_dev_attr = getattr(emb, "device", None)
                        if emb_dev_attr is not None:
                            embed_device = str(emb_dev_attr)
                except Exception:  # pragma: no cover — diagnostics only
                    pass
                pipeline_device = str(getattr(self._pipeline, "device", device))
                logger.info(
                    "Diarization device check: pipeline=%s segmentation=%s embedding=%s",
                    pipeline_device, seg_device, embed_device,
                )
                if device.type != "cpu" and (
                    seg_device.startswith("cpu") or embed_device.startswith("cpu")
                ):
                    logger.warning(
                        "Diarization: requested %s but a submodel stayed on CPU "
                        "(segmentation=%s embedding=%s) — expect slow runtime",
                        device.type, seg_device, embed_device,
                    )
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

    def diarize(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> DiarizationResult:
        """Identify speakers. Returns speaker segments with labels.

        Optional speaker-count hints are forwarded to the underlying pyannote
        pipeline. Use ``num_speakers`` when the count is known exactly (the
        single biggest precision win), or ``min_speakers``/``max_speakers``
        for bounds when only one side is known. All three are no-ops when
        unset, preserving the prior auto-detect behavior.

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
            # Build pyannote kwargs from the optional hint args. Only pass
            # the ones that are actually set so we don't surprise pyannote
            # with ``min/max=None`` overrides.
            pyannote_kwargs: dict = {}
            if num_speakers is not None and num_speakers > 0:
                pyannote_kwargs["num_speakers"] = int(num_speakers)
            if min_speakers is not None and min_speakers > 0:
                pyannote_kwargs["min_speakers"] = int(min_speakers)
            if max_speakers is not None and max_speakers > 0:
                pyannote_kwargs["max_speakers"] = int(max_speakers)
            if pyannote_kwargs:
                logger.info("Diarization called with hints: %s", pyannote_kwargs)
            stage_timer = _StageTimer()
            try:
                diarization = pipeline(
                    str(audio_path), hook=stage_timer, **pyannote_kwargs,
                )
            finally:
                # Always flush final stage timing, even if the pipeline raised
                # — partial timings are still informative for diagnosis.
                stage_timer.finish()
        except Exception as exc:
            # Graceful failure — return empty result with actionable diagnostics
            import warnings

            err_str = str(exc)
            hint = ""
            # NameError on AudioDecoder means pyannote loaded *before* torchcodec
            # was importable — its `try: from torchcodec.decoders import AudioDecoder`
            # at io.py swallowed the failure and left the name unbound. The deps
            # may now be fine on disk; the running process just has stale module
            # state. Reinstalling won't help — restart the process.
            if isinstance(exc, NameError) and "AudioDecoder" in err_str:
                hint = (
                    " — FIX: stale process — pyannote loaded before torchcodec was importable. "
                    "Restart this process (e.g. kill the `mm serve` server and relaunch). "
                    "Reinstalling deps is NOT the fix."
                )
            elif "AudioDecoder" in err_str or "torchcodec" in err_str.lower():
                hint = (
                    " — FIX: pyannote.audio 4.x requires torchcodec + ffmpeg. "
                    "Run: brew install ffmpeg && pip install torchcodec"
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
