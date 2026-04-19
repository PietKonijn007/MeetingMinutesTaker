"""Tests for the passive speaker centroid learning module (SPK-1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from unittest.mock import MagicMock

from meeting_minutes.models import DiarizationSegment
from meeting_minutes.system1 import speaker_identity as si
from meeting_minutes.system3.db import (
    MeetingORM,
    PersonORM,
    VoiceSampleORM,
    get_session_factory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session():
    factory = get_session_factory("sqlite:///:memory:")
    s = factory()
    yield s
    s.close()


@pytest.fixture
def jon(session):
    person = PersonORM(person_id="p-jon", name="Jon")
    session.add(person)
    session.commit()
    return person


@pytest.fixture
def sarah(session):
    person = PersonORM(person_id="p-sarah", name="Sarah")
    session.add(person)
    session.commit()
    return person


@pytest.fixture
def meeting(session):
    m = MeetingORM(meeting_id="m-001", title="Test", meeting_type="standup")
    session.add(m)
    session.commit()
    return m


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(arr)
    return arr / n if n else arr


def _add_confirmed_sample(
    session,
    person_id: str,
    meeting_id: str,
    cluster_id: str,
    embedding: np.ndarray,
    *,
    confirmed: bool = True,
    created_at=None,
):
    vec = np.asarray(embedding, dtype=np.float32).reshape(-1)
    row = VoiceSampleORM(
        person_id=person_id,
        meeting_id=meeting_id,
        cluster_id=cluster_id,
        embedding=vec.tobytes(),
        embedding_dim=int(vec.shape[0]),
        confirmed=confirmed,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# compute_centroid
# ---------------------------------------------------------------------------


def test_compute_centroid_mean_of_three_samples(session, jon, meeting):
    base = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    for i in range(3):
        _add_confirmed_sample(
            session, jon.person_id, meeting.meeting_id, f"SPEAKER_0{i}",
            base + np.array([0.0, float(i), 0.0], dtype=np.float32),
        )
    centroid = si.compute_centroid(session, jon.person_id)
    np.testing.assert_allclose(centroid, np.array([1.0, 1.0, 0.0], dtype=np.float32), atol=1e-6)


def test_compute_centroid_returns_none_when_no_confirmed_samples(session, jon, meeting):
    # Unconfirmed sample must not contribute.
    _add_confirmed_sample(
        session, jon.person_id, meeting.meeting_id, "SPEAKER_00",
        np.array([1.0, 0.0, 0.0], dtype=np.float32), confirmed=False,
    )
    assert si.compute_centroid(session, jon.person_id) is None


def test_compute_centroid_respects_max_samples(session, jon, meeting):
    # 25 samples, cap at 3; only the 3 most recent should average to ~index 22-24.
    # We give each sample a distinct value and distinct created_at.
    now = datetime.now(timezone.utc)
    for i in range(25):
        _add_confirmed_sample(
            session, jon.person_id, meeting.meeting_id, f"SPEAKER_{i:02d}",
            np.array([float(i), 0.0, 0.0], dtype=np.float32),
            created_at=now - timedelta(seconds=25 - i),
        )
    centroid = si.compute_centroid(session, jon.person_id, max_samples=3)
    # Latest three are i=22, 23, 24 → mean = 23.
    np.testing.assert_allclose(centroid, np.array([23.0, 0.0, 0.0], dtype=np.float32), atol=1e-6)


# ---------------------------------------------------------------------------
# match_clusters
# ---------------------------------------------------------------------------


def test_match_clusters_high_tier_at_exactly_085(session, jon, meeting, monkeypatch):
    # Two unit vectors with dot product exactly 0.85.
    a = _unit([1.0, 0.0])
    # Choose b so cos(a, b) == 0.85: b = [0.85, sqrt(1 - 0.85^2)].
    b = np.array([0.85, np.sqrt(1 - 0.85**2)], dtype=np.float32)
    _add_confirmed_sample(session, jon.person_id, meeting.meeting_id, "SPEAKER_00", a)
    result = si.match_clusters(session, {"SPEAKER_00": b})
    match = result["SPEAKER_00"]
    assert match.person_id == jon.person_id
    assert abs(match.score - 0.85) < 1e-5
    assert match.tier == "high"


def test_match_clusters_medium_tier(session, jon, meeting):
    """Score in [0.70, 0.85) produces a medium-tier suggestion."""
    a = _unit([1.0, 0.0])
    # Pick a value safely inside the medium band (0.75 cosine).
    b = np.array([0.75, np.sqrt(1 - 0.75**2)], dtype=np.float32)
    _add_confirmed_sample(session, jon.person_id, meeting.meeting_id, "SPEAKER_00", a)
    match = si.match_clusters(session, {"SPEAKER_00": b})["SPEAKER_00"]
    assert 0.70 <= match.score < 0.85
    assert match.tier == "medium"
    assert match.person_id == jon.person_id


def test_match_clusters_unknown_tier_below_low_threshold(session, jon, meeting):
    a = _unit([1.0, 0.0])
    # 0.60 cosine — well below the 0.70 low threshold.
    b = np.array([0.60, np.sqrt(1 - 0.60**2)], dtype=np.float32)
    _add_confirmed_sample(session, jon.person_id, meeting.meeting_id, "SPEAKER_00", a)
    match = si.match_clusters(session, {"SPEAKER_00": b})["SPEAKER_00"]
    assert match.tier == "unknown"
    assert match.person_id is None  # Don't suggest a person below threshold
    assert match.person_name is None
    assert match.score > 0  # best-effort score is still reported


def test_match_clusters_threshold_boundaries():
    """Pure tier classifier: verify boundary semantics without float flakiness."""
    assert si._classify_tier(0.86) == "high"
    assert si._classify_tier(0.85) == "high"
    assert si._classify_tier(0.8499) == "medium"
    assert si._classify_tier(0.70) == "medium"
    assert si._classify_tier(0.6999) == "unknown"
    assert si._classify_tier(0.0) == "unknown"
    assert si._classify_tier(-0.5) == "unknown"


def test_match_clusters_with_no_candidates_returns_unknown(session, meeting):
    # No confirmed samples anywhere.
    b = _unit([1.0, 0.0])
    match = si.match_clusters(session, {"SPEAKER_00": b})["SPEAKER_00"]
    assert match.tier == "unknown"
    assert match.person_id is None
    assert match.score == 0.0


def test_match_clusters_picks_best_candidate(session, jon, sarah, meeting):
    # Jon at [1, 0], Sarah at [0, 1]. Cluster vector close to [1, 0].
    _add_confirmed_sample(
        session, jon.person_id, meeting.meeting_id, "SPEAKER_00",
        _unit([1.0, 0.0]),
    )
    _add_confirmed_sample(
        session, sarah.person_id, meeting.meeting_id, "SPEAKER_01",
        _unit([0.0, 1.0]),
    )
    cluster_vec = _unit([0.99, 0.14])
    match = si.match_clusters(session, {"CLUSTER_X": cluster_vec})["CLUSTER_X"]
    assert match.person_id == jon.person_id
    assert match.tier == "high"


# ---------------------------------------------------------------------------
# write_sample / confirm_sample round-trip
# ---------------------------------------------------------------------------


def test_write_sample_then_confirm_roundtrip(session, jon, meeting):
    vec = _unit([0.5, 0.5, 0.0])
    row = si.write_sample(
        session,
        person_id=jon.person_id,
        meeting_id=meeting.meeting_id,
        cluster_id="SPEAKER_00",
        embedding=vec,
        confirmed=False,
    )
    assert row.confirmed is False
    assert row.embedding_dim == 3
    restored = np.frombuffer(row.embedding, dtype=np.float32)
    np.testing.assert_allclose(restored, vec.astype(np.float32), atol=1e-6)

    confirmed = si.confirm_sample(
        session, meeting.meeting_id, "SPEAKER_00", jon.person_id,
    )
    assert confirmed is not None
    assert confirmed.confirmed is True


def test_write_sample_upserts_on_same_triple(session, jon, meeting):
    v1 = _unit([1.0, 0.0])
    v2 = _unit([0.0, 1.0])
    si.write_sample(
        session, person_id=jon.person_id, meeting_id=meeting.meeting_id,
        cluster_id="SPEAKER_00", embedding=v1, confirmed=False,
    )
    si.write_sample(
        session, person_id=jon.person_id, meeting_id=meeting.meeting_id,
        cluster_id="SPEAKER_00", embedding=v2, confirmed=True,
    )
    # Should have exactly one row, updated vector, confirmed=True.
    rows = session.query(VoiceSampleORM).filter_by(
        person_id=jon.person_id, meeting_id=meeting.meeting_id, cluster_id="SPEAKER_00",
    ).all()
    assert len(rows) == 1
    assert rows[0].confirmed is True
    restored = np.frombuffer(rows[0].embedding, dtype=np.float32)
    np.testing.assert_allclose(restored, v2.astype(np.float32), atol=1e-6)


def test_confirm_sample_returns_none_for_missing_row(session, jon, meeting):
    assert si.confirm_sample(session, meeting.meeting_id, "SPEAKER_99", jon.person_id) is None


# ---------------------------------------------------------------------------
# invalidate_contamination
# ---------------------------------------------------------------------------


def test_invalidate_contamination_relabel_jon_to_sarah(
    session, jon, sarah, meeting,
):
    # First labeling: SPEAKER_00 → Jon, confirmed.
    si.write_sample(
        session, person_id=jon.person_id, meeting_id=meeting.meeting_id,
        cluster_id="SPEAKER_00",
        embedding=_unit([1.0, 0.0]), confirmed=True,
    )
    # Correction: user says it was actually Sarah. Invalidate + write new.
    invalidated = si.invalidate_contamination(
        session, meeting.meeting_id, "SPEAKER_00", sarah.person_id,
    )
    assert invalidated == 1

    # Jon's sample should now be confirmed=False.
    jon_row = session.query(VoiceSampleORM).filter_by(
        person_id=jon.person_id, meeting_id=meeting.meeting_id, cluster_id="SPEAKER_00",
    ).one()
    assert jon_row.confirmed is False

    # Write Sarah's new confirmed sample.
    si.write_sample(
        session, person_id=sarah.person_id, meeting_id=meeting.meeting_id,
        cluster_id="SPEAKER_00",
        embedding=_unit([1.0, 0.0]), confirmed=True,
    )
    sarah_row = session.query(VoiceSampleORM).filter_by(
        person_id=sarah.person_id, meeting_id=meeting.meeting_id, cluster_id="SPEAKER_00",
    ).one()
    assert sarah_row.confirmed is True


def test_invalidate_contamination_zero_when_none_exist(session, jon, meeting):
    invalidated = si.invalidate_contamination(
        session, meeting.meeting_id, "SPEAKER_00", jon.person_id,
    )
    assert invalidated == 0


def test_invalidate_contamination_none_new_person(session, jon, meeting):
    """If new_person_id is None (user cleared the label), everyone's samples
    for this cluster are demoted."""
    si.write_sample(
        session, person_id=jon.person_id, meeting_id=meeting.meeting_id,
        cluster_id="SPEAKER_00",
        embedding=_unit([1.0, 0.0]), confirmed=True,
    )
    invalidated = si.invalidate_contamination(
        session, meeting.meeting_id, "SPEAKER_00", None,
    )
    assert invalidated == 1
    jon_row = session.query(VoiceSampleORM).filter_by(
        person_id=jon.person_id, meeting_id=meeting.meeting_id, cluster_id="SPEAKER_00",
    ).one()
    assert jon_row.confirmed is False


# ---------------------------------------------------------------------------
# min_speech_duration_ok
# ---------------------------------------------------------------------------


def test_min_speech_duration_ok_at_4999_is_false():
    segs = [DiarizationSegment(start=0.0, end=4.999, speaker="SPEAKER_00")]
    assert si.min_speech_duration_ok(segs, "SPEAKER_00") is False


def test_min_speech_duration_ok_at_5000_is_true():
    segs = [DiarizationSegment(start=0.0, end=5.0, speaker="SPEAKER_00")]
    assert si.min_speech_duration_ok(segs, "SPEAKER_00") is True


def test_min_speech_duration_sums_multiple_segments():
    segs = [
        DiarizationSegment(start=0.0, end=2.5, speaker="SPEAKER_00"),
        DiarizationSegment(start=10.0, end=12.6, speaker="SPEAKER_00"),
        DiarizationSegment(start=20.0, end=21.0, speaker="SPEAKER_01"),
    ]
    assert si.min_speech_duration_ok(segs, "SPEAKER_00") is True  # 5.1s total
    assert si.min_speech_duration_ok(segs, "SPEAKER_01") is False  # 1.0s


def test_min_speech_duration_accepts_dicts():
    segs = [
        {"start": 0.0, "end": 5.5, "speaker": "SPEAKER_00"},
    ]
    assert si.min_speech_duration_ok(segs, "SPEAKER_00") is True


def test_min_speech_duration_unknown_cluster_is_false():
    segs = [DiarizationSegment(start=0.0, end=10.0, speaker="SPEAKER_00")]
    assert si.min_speech_duration_ok(segs, "SPEAKER_99") is False


# ---------------------------------------------------------------------------
# extract_cluster_embeddings (pyannote bridge)
# ---------------------------------------------------------------------------


def test_extract_cluster_embeddings_from_diarize_output():
    """pyannote 4.x DiarizeOutput.speaker_embeddings — rows aligned with labels()."""
    mock_annotation = MagicMock()
    mock_annotation.labels.return_value = ["SPEAKER_00", "SPEAKER_01"]
    embeddings = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)

    output = MagicMock()
    output.speaker_diarization = mock_annotation
    output.speaker_embeddings = embeddings

    result = si.extract_cluster_embeddings(output)
    assert set(result.keys()) == {"SPEAKER_00", "SPEAKER_01"}
    np.testing.assert_allclose(result["SPEAKER_00"], embeddings[0])
    np.testing.assert_allclose(result["SPEAKER_01"], embeddings[1])


def test_extract_cluster_embeddings_skips_zero_rows():
    """Padding zero rows (speakers beyond centroids.shape[0]) are skipped."""
    mock_annotation = MagicMock()
    mock_annotation.labels.return_value = ["SPEAKER_00", "SPEAKER_01"]
    embeddings = np.array([[1.0, 2.0], [0.0, 0.0]], dtype=np.float32)

    output = MagicMock()
    output.speaker_diarization = mock_annotation
    output.speaker_embeddings = embeddings

    result = si.extract_cluster_embeddings(output)
    assert set(result.keys()) == {"SPEAKER_00"}


def test_extract_cluster_embeddings_returns_empty_when_no_embeddings():
    output = MagicMock()
    output.speaker_diarization = MagicMock()
    output.speaker_embeddings = None
    assert si.extract_cluster_embeddings(output) == {}


def test_extract_cluster_embeddings_handles_none():
    assert si.extract_cluster_embeddings(None) == {}
