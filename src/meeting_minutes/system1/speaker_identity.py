"""Passive speaker centroid learning (SPK-1).

Cluster-to-person matching, centroid computation, and sample
write/confirm/invalidate helpers. Embeddings are per-cluster mean vectors
produced by the diarization pipeline and stored on :class:`VoiceSampleORM`
as ``np.float32`` bytes. Only samples with ``confirmed=True`` contribute to
a person's centroid.

All functions here are pure logic (no pyannote, no pipeline) so they can
be unit-tested without heavy ML dependencies. The diarization pipeline
calls :func:`extract_cluster_embeddings` to bridge the two worlds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

import numpy as np
from sqlalchemy.orm import Session

from meeting_minutes.system3.db import PersonORM, VoiceSampleORM


# Centroid drift cap — only the N most recent confirmed samples contribute
# to a person's centroid. Keeps the representation current even as a
# voice changes over years.
DEFAULT_MAX_SAMPLES = 20

# Tier thresholds (cosine similarity, inclusive-lower for each tier).
HIGH_TIER_THRESHOLD = 0.85
LOW_TIER_THRESHOLD = 0.70

# Clusters shorter than this are not written as samples (too noisy).
MIN_SPEECH_SECONDS = 5.0


Tier = Literal["high", "medium", "unknown"]


@dataclass(frozen=True)
class SpeakerMatch:
    """Result of matching a single cluster against all known persons."""

    person_id: str | None
    person_name: str | None
    score: float
    tier: Tier


# ---------------------------------------------------------------------------
# Embedding extraction (pyannote bridge)
# ---------------------------------------------------------------------------


def extract_cluster_embeddings(
    diarization_output: Any,
    audio_path: Any = None,
) -> dict[str, np.ndarray]:
    """Return ``{cluster_id: mean_embedding}`` for a diarization output.

    pyannote 4.x's :class:`DiarizeOutput` already carries per-cluster mean
    embeddings in ``speaker_embeddings`` (shape ``(num_speakers, dim)``,
    aligned with ``speaker_diarization.labels()``). For older output shapes
    we fall back to ``None`` for that cluster; the caller treats missing
    clusters as "no sample written, no suggestion".

    ``audio_path`` is accepted for future-proofing (e.g. if we ever need to
    re-run inference), but is unused on pyannote >= 4.
    """
    del audio_path  # reserved for the re-inference fallback

    if diarization_output is None:
        return {}

    annotation = getattr(diarization_output, "speaker_diarization", None)
    embeddings = getattr(diarization_output, "speaker_embeddings", None)
    if annotation is None or embeddings is None:
        return {}

    try:
        labels = list(annotation.labels())
    except Exception:
        return {}

    if len(labels) == 0 or not hasattr(embeddings, "shape"):
        return {}

    result: dict[str, np.ndarray] = {}
    # Rows are aligned with labels(); rows beyond labels() are padding zeros.
    for idx, label in enumerate(labels):
        if idx >= embeddings.shape[0]:
            break
        vec = np.asarray(embeddings[idx], dtype=np.float32)
        if not np.any(vec):
            # Zero vector = padded / missing; skip.
            continue
        result[str(label)] = vec
    return result


# ---------------------------------------------------------------------------
# Centroid math & matching
# ---------------------------------------------------------------------------


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _classify_tier(score: float) -> Tier:
    if score >= HIGH_TIER_THRESHOLD:
        return "high"
    if score >= LOW_TIER_THRESHOLD:
        return "medium"
    return "unknown"


def compute_centroid(
    session: Session,
    person_id: str,
    *,
    max_samples: int = DEFAULT_MAX_SAMPLES,
) -> np.ndarray | None:
    """Mean of the N most recent confirmed samples, or ``None`` if there
    are no confirmed samples yet."""
    rows = (
        session.query(VoiceSampleORM)
        .filter(
            VoiceSampleORM.person_id == person_id,
            VoiceSampleORM.confirmed.is_(True),
        )
        .order_by(VoiceSampleORM.created_at.desc())
        .limit(max_samples)
        .all()
    )
    if not rows:
        return None

    vectors = [
        np.frombuffer(r.embedding, dtype=np.float32).reshape(r.embedding_dim)
        for r in rows
    ]
    return np.mean(np.stack(vectors, axis=0), axis=0)


def match_clusters(
    session: Session,
    cluster_embeddings: dict[str, np.ndarray],
    *,
    high_threshold: float = HIGH_TIER_THRESHOLD,
    low_threshold: float = LOW_TIER_THRESHOLD,
    max_samples: int = DEFAULT_MAX_SAMPLES,
) -> dict[str, SpeakerMatch]:
    """Match each cluster to the best candidate person (≥ 1 confirmed sample).

    For each cluster, cosine similarity is computed against every person's
    current centroid; the highest-scoring candidate wins. If no person has
    any confirmed sample, every cluster gets an ``unknown`` match with a
    zero score.
    """
    if not cluster_embeddings:
        return {}

    # Build all candidate (person_id, centroid) pairs once — persons without
    # any confirmed sample are silently excluded by compute_centroid.
    candidate_person_ids = [
        row[0]
        for row in session.query(VoiceSampleORM.person_id)
        .filter(VoiceSampleORM.confirmed.is_(True))
        .distinct()
        .all()
    ]

    candidates: list[tuple[str, str | None, np.ndarray]] = []
    for pid in candidate_person_ids:
        centroid = compute_centroid(session, pid, max_samples=max_samples)
        if centroid is None:
            continue
        person = session.get(PersonORM, pid)
        name = person.name if person else None
        candidates.append((pid, name, centroid))

    out: dict[str, SpeakerMatch] = {}
    for cluster_id, vec in cluster_embeddings.items():
        if not candidates:
            out[cluster_id] = SpeakerMatch(
                person_id=None, person_name=None, score=0.0, tier="unknown"
            )
            continue

        best_pid: str | None = None
        best_name: str | None = None
        best_score = -1.0
        for pid, name, centroid in candidates:
            score = _cosine(vec, centroid)
            if score > best_score:
                best_score = score
                best_pid = pid
                best_name = name

        # Keep explicit thresholds separate from the module defaults so
        # callers can tune without overriding globals.
        if best_score >= high_threshold:
            tier: Tier = "high"
        elif best_score >= low_threshold:
            tier = "medium"
        else:
            tier = "unknown"

        if tier == "unknown":
            # Don't attach a person we're not going to suggest.
            out[cluster_id] = SpeakerMatch(
                person_id=None,
                person_name=None,
                score=max(0.0, best_score),
                tier="unknown",
            )
        else:
            out[cluster_id] = SpeakerMatch(
                person_id=best_pid,
                person_name=best_name,
                score=best_score,
                tier=tier,
            )
    return out


# ---------------------------------------------------------------------------
# Sample write / confirm / invalidate
# ---------------------------------------------------------------------------


def write_sample(
    session: Session,
    *,
    person_id: str,
    meeting_id: str,
    cluster_id: str,
    embedding: np.ndarray,
    confirmed: bool = False,
) -> VoiceSampleORM:
    """Upsert a voice sample on ``(meeting_id, cluster_id, person_id)``.

    The embedding is stored as ``np.float32`` bytes. If a row already exists
    for this triple, its embedding/confirmed flag are updated in place —
    this matches how the diarization pipeline writes unconfirmed samples on
    every run, and the rename endpoint then flips ``confirmed=True``.
    """
    vec = np.asarray(embedding, dtype=np.float32).reshape(-1)
    dim = int(vec.shape[0])
    if dim == 0:
        raise ValueError("Cannot persist empty embedding vector")

    row = (
        session.query(VoiceSampleORM)
        .filter_by(
            meeting_id=meeting_id,
            cluster_id=cluster_id,
            person_id=person_id,
        )
        .one_or_none()
    )
    if row is None:
        row = VoiceSampleORM(
            person_id=person_id,
            meeting_id=meeting_id,
            cluster_id=cluster_id,
            embedding=vec.tobytes(),
            embedding_dim=dim,
            confirmed=confirmed,
            created_at=datetime.now(timezone.utc),
        )
        session.add(row)
    else:
        row.embedding = vec.tobytes()
        row.embedding_dim = dim
        if confirmed:
            row.confirmed = True
    session.commit()
    session.refresh(row)
    return row


def confirm_sample(
    session: Session,
    meeting_id: str,
    cluster_id: str,
    person_id: str,
) -> VoiceSampleORM | None:
    """Flip ``confirmed=True`` for a sample. Returns the row or ``None`` if
    no sample exists for that triple (caller should have written one first)."""
    row = (
        session.query(VoiceSampleORM)
        .filter_by(
            meeting_id=meeting_id,
            cluster_id=cluster_id,
            person_id=person_id,
        )
        .one_or_none()
    )
    if row is None:
        return None
    row.confirmed = True
    session.commit()
    session.refresh(row)
    return row


def invalidate_contamination(
    session: Session,
    meeting_id: str,
    cluster_id: str,
    new_person_id: str | None,
) -> int:
    """Demote every sample for this (meeting_id, cluster_id) that belongs
    to a *different* person than ``new_person_id`` to ``confirmed=False``.

    This runs when the user relabels a cluster: prior samples attributed to
    the wrong person must stop polluting that person's centroid. Returns
    the count of rows invalidated.
    """
    query = session.query(VoiceSampleORM).filter(
        VoiceSampleORM.meeting_id == meeting_id,
        VoiceSampleORM.cluster_id == cluster_id,
    )
    if new_person_id is not None:
        query = query.filter(VoiceSampleORM.person_id != new_person_id)

    rows = query.all()
    count = 0
    for row in rows:
        if row.confirmed:
            row.confirmed = False
            count += 1
    if count:
        session.commit()
    return count


# ---------------------------------------------------------------------------
# Duration filter
# ---------------------------------------------------------------------------


def min_speech_duration_ok(
    segments: Iterable[Any],
    cluster_id: str,
    *,
    min_seconds: float = MIN_SPEECH_SECONDS,
) -> bool:
    """True iff a cluster has at least ``min_seconds`` of speech.

    Segments can be any objects with ``speaker``, ``start``, ``end``
    attributes (e.g. :class:`DiarizationSegment`) or equivalent dicts.
    """
    total = 0.0
    for seg in segments:
        spk = getattr(seg, "speaker", None)
        if spk is None and isinstance(seg, dict):
            spk = seg.get("speaker")
        if spk != cluster_id:
            continue
        start = getattr(seg, "start", None)
        end = getattr(seg, "end", None)
        if start is None and isinstance(seg, dict):
            start = seg.get("start")
        if end is None and isinstance(seg, dict):
            end = seg.get("end")
        if start is None or end is None:
            continue
        total += max(0.0, float(end) - float(start))
    return total >= min_seconds


def cluster_speech_durations(segments: Iterable[Any]) -> dict[str, float]:
    """Return ``{cluster_id: total_speech_seconds}`` across segments."""
    out: dict[str, float] = {}
    for seg in segments:
        spk = getattr(seg, "speaker", None)
        if spk is None and isinstance(seg, dict):
            spk = seg.get("speaker")
        if not spk:
            continue
        start = getattr(seg, "start", None)
        end = getattr(seg, "end", None)
        if start is None and isinstance(seg, dict):
            start = seg.get("start")
        if end is None and isinstance(seg, dict):
            end = seg.get("end")
        if start is None or end is None:
            continue
        out[spk] = out.get(spk, 0.0) + max(0.0, float(end) - float(start))
    return out
