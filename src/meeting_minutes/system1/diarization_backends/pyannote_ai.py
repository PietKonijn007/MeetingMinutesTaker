"""pyannoteAI hosted-API backend (engine: ``pyannote-ai``).

Calls the cloud service maintained by the pyannote.audio authors. Two
relevant tiers, configurable via ``pyannote_ai.tier`` in config:

* ``community-1`` — the same open-weights model as the local community-1
  pipeline (~€0.04/hr at the time of writing)
* ``precision-2`` — proprietary flagship with the best published DER
  numbers in 2026 (~€0.11/hr)

The API key is read from an env var (default ``PYANNOTEAI_API_KEY``). If
unset, ``diarize()`` returns an empty result with a clear log message
rather than crashing the recording pipeline.

Result handling:
* The SDK returns a dict with ``output['diarization']`` — a list of
  ``{start, end, speaker}`` segments.
* When the optional voiceprint API is exposed alongside, callers can
  request per-speaker embeddings via the SPK-1 layer; this backend
  doesn't fetch them by default to keep cost predictable.
"""

from __future__ import annotations

import os
from pathlib import Path

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.models import DiarizationResult, DiarizationSegment

from .base import DiarizationBackend, logger, normalize_label


class PyannoteAIBackend(DiarizationBackend):
    """Run diarization through the pyannoteAI hosted API."""

    def __init__(self, config: DiarizationConfig) -> None:
        super().__init__(config)
        self._client = None  # lazy — only created on first diarize() call
        # Lazy local fallback. Constructed only on the first cloud failure
        # so an internet-connected user never pays for the local pyannote
        # import (~3-5s, plus the model download on first run).
        self._local_fallback = None

    @property
    def supports_embeddings(self) -> bool:
        # The cloud path doesn't pull voiceprints by default (extra cost +
        # extra latency). But when ``fallback_to_local`` is on, *some* runs
        # will actually go through the local pyannote path, which does
        # surface embeddings — so SPK-1 should still attempt centroid
        # matching for those runs. Returning True when fallback is enabled
        # lets the cross-meeting re-id layer try, and it correctly no-ops
        # on the cloud-success path because ``last_cluster_embeddings`` is
        # empty there.
        return bool(self._config.fallback_to_local)

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from pyannoteai.sdk import Client
        except ImportError as exc:
            raise RuntimeError(
                "pyannoteai-sdk is not installed. Run: pip install pyannoteai-sdk"
            ) from exc

        api_key_env = self._config.pyannote_ai.api_key_env
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"pyannoteAI API key not found. Set ${api_key_env} or change "
                f"diarization.pyannote_ai.api_key_env in config.yaml."
            )
        self._client = Client(api_key)
        return self._client

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

        # ------------------------------------------------------------------
        # Try cloud first. Any failure short-circuits to the local fallback
        # (when enabled) so the user gets a diarized meeting one way or
        # another — no internet, expired key, API 5xx, malformed response,
        # they all funnel into the same recovery path.
        # ------------------------------------------------------------------
        cloud_failure: str | None = None
        try:
            client = self._get_client()
        except Exception as exc:
            cloud_failure = f"client init failed: {exc}"
            client = None

        result: DiarizationResult | None = None
        if client is not None:
            tier = self._config.pyannote_ai.tier
            logger.info(
                "Diarization (pyannoteAI): tier=%s file=%s",
                tier, audio_path.name,
            )

            kwargs: dict = {"model": tier}
            if num_speakers is not None and num_speakers > 0:
                kwargs["num_speakers"] = int(num_speakers)
            if min_speakers is not None and min_speakers > 0:
                kwargs["min_speakers"] = int(min_speakers)
            if max_speakers is not None and max_speakers > 0:
                kwargs["max_speakers"] = int(max_speakers)
            if {"num_speakers", "min_speakers", "max_speakers"} & set(kwargs):
                logger.info("Diarization called with hints: %s", {
                    k: v for k, v in kwargs.items() if k != "model"
                })

            try:
                # 1) Upload audio → media:// URL.
                media_url = client.upload(str(audio_path))
                # 2) Submit the diarize job.
                job_id = client.diarize(media_url, **kwargs)
                # 3) Block-poll until done.
                poll_interval = max(1, int(self._config.pyannote_ai.poll_interval_seconds))
                raw_result = client.retrieve(job_id, every_seconds=poll_interval)
            except Exception as exc:
                cloud_failure = f"call failed: {exc}"
            else:
                result = self._parse_result(raw_result)
                # Treat an empty-segment result as a failure too — the API
                # returned a usable response but with nothing in it, which
                # is no better than no diarization at all from the user's POV.
                if not result.segments:
                    cloud_failure = "API returned no segments"
                    result = None

        if result is not None:
            return result

        # Cloud path failed. Fall back to local pyannote when enabled — the
        # alternative is shipping an empty result, which silently breaks
        # downstream speaker labeling.
        assert cloud_failure is not None  # guarded by the elif structure
        if self._config.fallback_to_local:
            logger.warning(
                "pyannoteAI %s — falling back to local pyannote engine",
                cloud_failure,
            )
            return self._run_local_fallback(
                audio_path,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
        logger.warning(
            "pyannoteAI %s — fallback disabled (diarization.fallback_to_local=false)",
            cloud_failure,
        )
        return self.empty_result()

    def _run_local_fallback(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
    ) -> DiarizationResult:
        """Lazily build a PyannoteLocalBackend and run it.

        Mirrors the active backend's embedding-export contract: when the
        local pipeline produces per-cluster embeddings, we surface them on
        ``self._last_cluster_embeddings`` so the SPK-1 layer keeps working
        across fallback runs.
        """
        if self._local_fallback is None:
            from .pyannote_local import PyannoteLocalBackend
            self._local_fallback = PyannoteLocalBackend(self._config)
        result = self._local_fallback.diarize(
            audio_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        self._last_cluster_embeddings = self._local_fallback.last_cluster_embeddings
        return result

    def _parse_result(self, result: dict) -> DiarizationResult:
        """Convert the SDK's response dict into our DiarizationResult shape.

        The SDK returns ``{output: {diarization: [{start, end, speaker}, ...]}}``
        — keys may shift slightly between API versions, so we probe a few
        known locations and fail soft.
        """
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            logger.warning("pyannoteAI response missing 'output': %r", result)
            return self.empty_result()

        # Try common keys in order of preference.
        raw_segments = None
        for key in ("diarization", "speaker_diarization", "segments"):
            candidate = output.get(key)
            if isinstance(candidate, list):
                raw_segments = candidate
                break

        if not raw_segments:
            logger.warning("pyannoteAI response had no diarization segments")
            return self.empty_result()

        segments: list[DiarizationSegment] = []
        speakers: set[str] = set()
        for raw in raw_segments:
            if not isinstance(raw, dict):
                continue
            # Tolerate both {start, end, speaker} and pyannote-style nesting.
            start = raw.get("start")
            end = raw.get("end")
            speaker = raw.get("speaker") or raw.get("label")
            if start is None or end is None or speaker is None:
                continue
            label = normalize_label(str(speaker))
            segments.append(DiarizationSegment(start=float(start), end=float(end), speaker=label))
            speakers.add(label)

        logger.info(
            "pyannoteAI diarization: %d segments, %d speakers",
            len(segments), len(speakers),
        )
        return DiarizationResult(
            meeting_id="",
            segments=segments,
            num_speakers=len(speakers),
        )
