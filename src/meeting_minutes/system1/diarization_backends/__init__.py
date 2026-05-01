"""Pluggable speaker-diarization backends.

A backend takes an audio file and returns a :class:`DiarizationResult`.
The :class:`~.base.DiarizationBackend` base class defines the contract;
concrete implementations live alongside it. The selector lives on
:class:`~meeting_minutes.system1.diarize.DiarizationEngine`, which reads
``DiarizationConfig.engine`` and instantiates the right backend.

Adding a new backend: subclass ``DiarizationBackend``, implement
``diarize()``, and register it in :func:`select_backend`.
"""

from __future__ import annotations

from meeting_minutes.config import DiarizationConfig

from .base import DiarizationBackend, normalize_label

__all__ = ["DiarizationBackend", "normalize_label", "select_backend"]


def select_backend(config: DiarizationConfig) -> DiarizationBackend:
    """Return the backend instance matching ``config.engine``.

    Imports are lazy so a user who only ever runs the local backend doesn't
    pay for the pyannoteai-sdk or mlx-core import on startup.
    """
    engine = (config.engine or "pyannote").lower()
    if engine == "pyannote":
        from .pyannote_local import PyannoteLocalBackend
        return PyannoteLocalBackend(config)
    if engine in ("pyannote-ai", "pyannoteai"):
        from .pyannote_ai import PyannoteAIBackend
        return PyannoteAIBackend(config)
    if engine in ("pyannote-mlx", "pyannote_mlx"):
        from .pyannote_mlx import PyannoteMLXBackend
        return PyannoteMLXBackend(config)
    raise ValueError(
        f"Unknown diarization engine: {config.engine!r}. "
        f"Valid options: pyannote, pyannote-ai, pyannote-mlx."
    )
