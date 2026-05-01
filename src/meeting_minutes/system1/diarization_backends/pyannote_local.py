"""Local PyTorch pyannote.audio backend (engine: ``pyannote``).

This is the historical default — it runs pyannote.audio in-process using
PyTorch. On Apple Silicon it uses MPS; on NVIDIA hosts it uses CUDA;
otherwise it falls back to CPU. The embedding stage tends to dominate
runtime for long meetings; consider the ``pyannote-mlx`` backend on
Apple Silicon if that hurts.
"""

from __future__ import annotations

import os
import platform
import warnings
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.models import DiarizationResult, DiarizationSegment

from .base import DiarizationBackend, StageTimer, logger, normalize_label


class PyannoteLocalBackend(DiarizationBackend):
    """Run pyannote.audio locally on PyTorch (MPS/CUDA/CPU)."""

    def __init__(self, config: DiarizationConfig) -> None:
        super().__init__(config)
        self._pipeline = None

    @property
    def supports_embeddings(self) -> bool:
        # pyannote 4.x returns DiarizeOutput with speaker_embeddings; older
        # 3.x versions don't, but our extract_cluster_embeddings helper
        # handles both shapes. We advertise support and let the runtime
        # silently produce an empty dict if the pipeline doesn't emit them.
        return True

    def _load_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        try:
            warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
            warnings.filterwarnings("ignore", message=".*torchcodec.*")
            warnings.filterwarnings("ignore", message=".*libtorchcodec.*")
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            from pyannote.audio import Pipeline  # lazy import

            hf_token = os.environ.get("HF_TOKEN") or True
            self._pipeline = Pipeline.from_pretrained(
                self._config.model,
                token=hf_token,
            )

            # Apply batch-size overrides. Defaults match pyannote's pretrained
            # config (32 each); we expose them mostly as escape hatches —
            # measurements showed bumping them on MPS doesn't help for the
            # ResNet-34 embedder, but on CUDA larger batches do help.
            try:
                emb_bs = int(self._config.embedding_batch_size)
                seg_bs = int(self._config.segmentation_batch_size)
                if hasattr(self._pipeline, "embedding_batch_size"):
                    self._pipeline.embedding_batch_size = emb_bs
                if hasattr(self._pipeline, "segmentation_batch_size"):
                    self._pipeline.segmentation_batch_size = seg_bs
                logger.info(
                    "Diarization batch sizes: segmentation=%d embedding=%d",
                    seg_bs, emb_bs,
                )
            except Exception as bs_exc:  # pragma: no cover — best effort
                logger.warning("Could not set diarization batch sizes: %s", bs_exc)

            self._move_to_best_device()
        except ImportError as exc:
            raise RuntimeError(
                "pyannote.audio is not installed. Run: pip install pyannote.audio"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load diarization pipeline: {exc}"
            ) from exc
        return self._pipeline

    def _move_to_best_device(self) -> None:
        """Move the pipeline to MPS / CUDA / CPU and verify the move took."""
        try:
            import torch
            if (
                platform.system() == "Darwin"
                and platform.machine() == "arm64"
                and torch.backends.mps.is_available()
            ):
                device = torch.device("mps")
                logger.info("Diarization: requesting Apple Silicon GPU (MPS)")
            elif torch.cuda.is_available():
                device = torch.device("cuda")
                logger.info("Diarization: requesting NVIDIA CUDA")
            else:
                device = torch.device("cpu")
                logger.info("Diarization: requesting CPU (slow — expect ~1x real-time)")
            self._pipeline.to(device)

            # Read the device back from the pipeline's submodels so the log
            # reflects what's actually running, not what we asked for. MPS
            # historically had operator gaps that left the embedding model
            # silently on CPU.
            seg_device = embed_device = "?"
            try:
                seg_model = getattr(getattr(self._pipeline, "_segmentation", None), "model", None)
                if seg_model is not None:
                    seg_device = str(next(seg_model.parameters()).device)
            except Exception:  # pragma: no cover
                pass
            try:
                emb = getattr(self._pipeline, "_embedding", None)
                emb_module = getattr(emb, "model_", emb)
                if emb_module is not None and hasattr(emb_module, "parameters"):
                    embed_device = str(next(emb_module.parameters()).device)
                elif emb is not None:
                    emb_dev_attr = getattr(emb, "device", None)
                    if emb_dev_attr is not None:
                        embed_device = str(emb_dev_attr)
            except Exception:  # pragma: no cover
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
            logger.warning(
                "Could not move diarization pipeline to GPU: %s — using CPU",
                device_exc,
            )

    def diarize(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> DiarizationResult:
        self._last_cluster_embeddings = {}

        if not self._config.enabled:
            return self.empty_result()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            pipeline = self._load_pipeline()
            pyannote_kwargs: dict = {}
            if num_speakers is not None and num_speakers > 0:
                pyannote_kwargs["num_speakers"] = int(num_speakers)
            if min_speakers is not None and min_speakers > 0:
                pyannote_kwargs["min_speakers"] = int(min_speakers)
            if max_speakers is not None and max_speakers > 0:
                pyannote_kwargs["max_speakers"] = int(max_speakers)
            if pyannote_kwargs:
                logger.info("Diarization called with hints: %s", pyannote_kwargs)
            stage_timer = StageTimer()
            try:
                diarization = pipeline(
                    str(audio_path), hook=stage_timer, **pyannote_kwargs,
                )
            finally:
                stage_timer.finish()
        except Exception as exc:
            full_msg = f"Diarization failed: {exc}{_diagnostic_hint(exc)}"
            logger.warning(full_msg)
            warnings.warn(full_msg)
            return self.empty_result()

        # pyannote.audio 3.3+ wraps the annotation in DiarizeOutput; older
        # versions return Annotation directly. Unwrap if needed.
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
                    f"Got {type(diarization).__name__}"
                )

        segments: list[DiarizationSegment] = []
        speakers: set[str] = set()
        raw_to_norm: dict[str, str] = {}
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            raw = str(speaker)
            if raw not in raw_to_norm:
                raw_to_norm[raw] = normalize_label(raw)
            label = raw_to_norm[raw]
            segments.append(DiarizationSegment(start=turn.start, end=turn.end, speaker=label))
            speakers.add(label)

        # Lift per-cluster embeddings into our normalized label space, when
        # the pipeline produced them. Best-effort — never block diarization.
        try:
            from meeting_minutes.system1.speaker_identity import (
                extract_cluster_embeddings,
            )
            raw_embeddings = extract_cluster_embeddings(diarization)
            if raw_embeddings:
                self._last_cluster_embeddings = {
                    raw_to_norm.get(raw, normalize_label(raw)): vec
                    for raw, vec in raw_embeddings.items()
                }
        except Exception as embed_exc:
            logger.warning("Could not extract cluster embeddings: %s", embed_exc)

        return DiarizationResult(
            meeting_id="",
            segments=segments,
            num_speakers=len(speakers),
        )


def _diagnostic_hint(exc: Exception) -> str:
    """Append an actionable fix suggestion to common pyannote failure modes."""
    err_str = str(exc)
    # NameError on AudioDecoder means pyannote loaded *before* torchcodec
    # was importable — its `try: from torchcodec.decoders import AudioDecoder`
    # at io.py swallowed the failure and left the name unbound. Reinstalling
    # deps doesn't help; the running process just has stale module state.
    if isinstance(exc, NameError) and "AudioDecoder" in err_str:
        return (
            " — FIX: stale process — pyannote loaded before torchcodec was importable. "
            "Restart the server (kill `mm serve` and relaunch). Reinstalling deps is NOT the fix."
        )
    if "AudioDecoder" in err_str or "torchcodec" in err_str.lower():
        return (
            " — FIX: pyannote.audio 4.x requires torchcodec + ffmpeg. "
            "Run: brew install ffmpeg && pip install torchcodec"
        )
    if "401" in err_str or "403" in err_str or "gated" in err_str.lower() or "access" in err_str.lower():
        return (
            " — FIX: HF_TOKEN missing or you haven't accepted the pyannote license. "
            "Visit https://huggingface.co/pyannote/speaker-diarization-community-1 and accept terms."
        )
    if "ffmpeg" in err_str.lower():
        return " — FIX: Install ffmpeg. Run: brew install ffmpeg"
    return ""
