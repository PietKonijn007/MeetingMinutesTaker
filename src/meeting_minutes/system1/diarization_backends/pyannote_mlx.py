"""MLX-accelerated hybrid diarization backend (engine: ``pyannote-mlx``).

Strategy: keep pyannote's segmentation, clustering, and audio I/O, but
replace the WeSpeaker speaker-embedding forward pass — measured at ~95%
of total runtime on Apple Silicon — with an MLX-native implementation.

The hybrid approach gives us most of the speedup with a fraction of the
implementation surface compared to a full MLX port:

* Segmentation: pyannote's pyannet model on PyTorch/MPS — already fast
  (~30s for a 2-hour file), no need to port.
* Embedding: MLX-native ResNet-34 from ``mlx-community/wespeaker-voxceleb-resnet34-LM``.
  This is the bottleneck stage and the one MLX is good at.
* Clustering: pyannote's agglomerative clustering on numpy/scipy — runs
  on CPU regardless of device, no need to touch.

The MLX weights are downloaded from Hugging Face on first use. The model
architecture is loaded dynamically from the same repo (``resnet_embedding.py``).

**Experimental.** Cosine similarity between MLX and PyTorch embeddings on
the same input is reported at ~88-95% in upstream notes — within the
range that produces reasonable clustering, but may slightly affect DER
versus the native pyannote backend. Benchmark on your own data before
relying on this for production minutes.

**Apple Silicon only.** On other platforms the standard pyannote backend
is faster — there's no reason to use this.
"""

from __future__ import annotations

import importlib.util
import platform
from pathlib import Path
from typing import Any

from meeting_minutes.config import DiarizationConfig

from .base import logger
from .pyannote_local import PyannoteLocalBackend


class PyannoteMLXBackend(PyannoteLocalBackend):
    """Pyannote pipeline with the embedding stage swapped for MLX."""

    def __init__(self, config: DiarizationConfig) -> None:
        super().__init__(config)
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            logger.warning(
                "pyannote-mlx engine selected on non-Apple-Silicon host — "
                "the standard pyannote engine is faster here. Falling back."
            )
            # We don't raise — the parent class behaviour is correct on
            # non-MLX hosts. The MLX swap below will simply skip itself.
        self._mlx_model = None  # populated lazily inside _load_pipeline

    def _load_pipeline(self):
        # Build the standard pyannote pipeline (segmentation+embedding+clustering).
        pipeline = super()._load_pipeline()

        # Swap the embedding model. Best-effort: if anything fails, we leave
        # the PyTorch embedding in place and warn — degrading to the
        # standard pyannote backend behaviour rather than crashing.
        try:
            mlx_callable = self._build_mlx_embedding_wrapper(pipeline._embedding)
            pipeline._embedding = mlx_callable
            logger.info(
                "Diarization (pyannote-mlx): swapped embedding stage for MLX %s",
                self._config.pyannote_mlx.embedding_model,
            )
        except Exception as exc:
            logger.warning(
                "pyannote-mlx: could not swap embedding for MLX (%s) — "
                "falling back to PyTorch embedding for this run", exc,
            )
        return pipeline

    def _build_mlx_embedding_wrapper(self, original_embedding: Any) -> Any:
        """Download the MLX wespeaker weights + module and wrap them as a
        callable that satisfies pyannote's embedding contract."""
        # Lazy imports — only paid when the user actually selects this backend.
        try:
            import mlx.core as mx  # noqa: F401
            import mlx.nn as mlx_nn  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "MLX is not installed. Run: pip install mlx"
            ) from exc

        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise RuntimeError(
                "huggingface_hub is required. Run: pip install huggingface_hub"
            ) from exc

        repo_id = self._config.pyannote_mlx.embedding_model
        # Both files live in the MLX community repo. Pin no revision so we
        # follow upstream — when fixing this in the wild, prefer a specific
        # revision once one is known to work.
        weights_path = hf_hub_download(repo_id=repo_id, filename="weights.npz")
        module_path = hf_hub_download(repo_id=repo_id, filename="resnet_embedding.py")

        # Dynamically import the model definition. This is bounded: only one
        # well-known module loads, from a trusted org (mlx-community).
        spec = importlib.util.spec_from_file_location("_mlx_resnet_embedding", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load MLX model module from {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        load_fn = getattr(module, "load_resnet34_embedding", None)
        if load_fn is None:
            raise RuntimeError(
                f"MLX repo {repo_id} doesn't expose load_resnet34_embedding()"
            )

        self._mlx_model = load_fn(weights_path)
        return _MLXEmbeddingWrapper(
            original_embedding=original_embedding,
            mlx_model=self._mlx_model,
        )


class _MLXEmbeddingWrapper:
    """Callable that replaces pyannote's PretrainedSpeakerEmbedding.

    Pyannote ships several embedding classes (ONNXWeSpeaker…,
    PyannoteAudio…, etc.) with subtly different interfaces. The ONNX one
    exposes ``compute_fbank`` publicly; the PyannoteAudio one (used by
    the community-1 pipeline) does not — it computes features internally
    inside its own ``__call__``. We therefore can't delegate feature
    extraction to the original; we compute fbank ourselves using the
    upstream wespeaker preprocessing recipe (Kaldi-compatible 80-bin
    log-mel filterbank, 25 ms frames, 10 ms hop, no dither).

    Metadata (``sample_rate``, ``dimension``, ``metric``,
    ``min_num_samples``, ``to``) still delegates to the original since
    those are uniform across pyannote's embedding classes.
    """

    # Wespeaker preprocessing constants — identical to the upstream
    # ``infer_onnx.py`` script and matched against pyannote's ONNX
    # ``compute_fbank`` signature so embeddings stay comparable.
    _NUM_MEL_BINS = 80
    _FRAME_LENGTH_MS = 25
    _FRAME_SHIFT_MS = 10
    # Wespeaker scales float audio in [-1, 1] up to int16 range before
    # extracting fbank features (kaldi historically expected int16-scale
    # input). Skipping this shifts the energy floor and tanks accuracy.
    _PRE_SCALE = 1 << 15

    def __init__(self, original_embedding: Any, mlx_model: Any) -> None:
        self._original = original_embedding
        self._mlx_model = mlx_model

    # ---- delegated metadata ------------------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._original.sample_rate

    @property
    def dimension(self) -> int:
        return self._original.dimension

    @property
    def metric(self) -> str:
        return self._original.metric

    @property
    def min_num_samples(self) -> int:
        return self._original.min_num_samples

    @property
    def min_num_frames(self) -> int:
        """Some embedding wrappers expose this; others don't. Derive it
        from ``min_num_samples`` when missing so the masked path has a
        sensible cutoff."""
        v = getattr(self._original, "min_num_frames", None)
        if v is not None:
            return v
        # frames = floor((samples - frame_length_samples) / frame_shift_samples) + 1
        sr = self.sample_rate
        frame_len = sr * self._FRAME_LENGTH_MS // 1000
        frame_shift = sr * self._FRAME_SHIFT_MS // 1000
        return max(1, (self.min_num_samples - frame_len) // frame_shift + 1)

    def to(self, device):
        # MLX manages its own scheduling; just delegate so any audio-side
        # ops stay wherever the caller put them.
        if hasattr(self._original, "to"):
            self._original.to(device)
        return self

    # ---- internal feature extraction ---------------------------------------

    def _compute_fbank(self, waveforms):
        """Per-utterance Kaldi log-mel fbank.

        Mirrors ``ONNXWeSpeakerPretrainedSpeakerEmbedding.compute_fbank``
        and the upstream ``wespeaker/bin/infer_onnx.py`` reference exactly
        so the MLX path produces embeddings comparable to the PyTorch
        reference.
        """
        import torch
        import torchaudio.compliance.kaldi as kaldi

        scaled = waveforms * self._PRE_SCALE
        return torch.stack([
            kaldi.fbank(
                w,
                num_mel_bins=self._NUM_MEL_BINS,
                frame_length=self._FRAME_LENGTH_MS,
                frame_shift=self._FRAME_SHIFT_MS,
                dither=0.0,
                sample_frequency=self.sample_rate,
            )
            for w in scaled
        ])

    # ---- the actual swap ---------------------------------------------------

    def __call__(self, waveforms, masks=None):
        """Same contract as pyannote's embedding callables:

        * waveforms: ``(B, 1, N)`` torch tensor
        * masks (optional): ``(B, num_samples)`` torch tensor of 0/1
        * returns: ``(B, dimension)`` numpy array

        Compute fbank features ourselves (don't delegate — see class
        docstring), convert to MLX, run the ResNet, return numpy.
        """
        import numpy as np
        import mlx.core as mx
        import torch.nn.functional as F

        batch_size, num_channels, _ = waveforms.shape
        assert num_channels == 1, "wespeaker expects mono audio"

        features = self._compute_fbank(waveforms.cpu())  # (B, T, 80) torch

        if masks is None:
            feat_mlx = mx.array(features.detach().cpu().numpy())
            embs_mlx = self._mlx_model(feat_mlx)
            mx.eval(embs_mlx)
            return np.asarray(embs_mlx)

        # Masked path: per-sample mask → per-frame mask via nearest-neighbour
        # interpolation, then embed only the frames that survive.
        _, num_frames, _ = features.shape
        imasks = F.interpolate(
            masks.unsqueeze(dim=1).float(), size=num_frames, mode="nearest"
        ).squeeze(dim=1) > 0.5

        out = np.full((batch_size, self.dimension), np.nan, dtype=np.float32)
        min_frames = self.min_num_frames
        for i, (feature, imask) in enumerate(zip(features, imasks)):
            masked_feature = feature[imask]
            if masked_feature.shape[0] < min_frames:
                continue
            feat_mlx = mx.array(masked_feature.detach().cpu().numpy()[None])
            emb_mlx = self._mlx_model(feat_mlx)
            mx.eval(emb_mlx)
            out[i] = np.asarray(emb_mlx)[0]
        return out
