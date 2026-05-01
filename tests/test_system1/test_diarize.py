"""Tests for the diarization engine."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.models import DiarizationResult, DiarizationSegment
from meeting_minutes.system1.diarize import DiarizationEngine


# Feature: meeting-minutes-taker, Property 7: Diarization output consistency
@given(n_speakers=st.integers(min_value=1, max_value=6))
@settings(max_examples=50)
def test_diarization_speaker_label_pattern(n_speakers: int):
    """Property 7: Speaker labels match SPEAKER_XX and num_speakers is correct."""
    config = DiarizationConfig(enabled=True)
    engine = DiarizationEngine(config)

    # Mock the pipeline
    segments = []
    for i in range(n_speakers):
        turn = MagicMock()
        turn.start = float(i * 5)
        turn.end = float(i * 5 + 4)
        segments.append((turn, None, f"SPEAKER_{i:02d}"))

    mock_diarization = MagicMock()
    mock_diarization.itertracks = MagicMock(return_value=iter(segments))

    mock_pipeline = MagicMock(return_value=mock_diarization)
    engine.backend._pipeline = mock_pipeline

    # Create dummy audio file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        path = Path(f.name)
        result = engine.diarize(path)

    # Check pattern
    pattern = re.compile(r"^SPEAKER_\d{2}$")
    for seg in result.segments:
        assert pattern.match(seg.speaker), f"Invalid speaker label: {seg.speaker}"

    # Check num_speakers
    distinct_speakers = {seg.speaker for seg in result.segments}
    assert result.num_speakers == len(distinct_speakers)


def test_diarization_disabled_returns_empty():
    """Diarization with enabled=False returns empty result."""
    config = DiarizationConfig(enabled=False)
    engine = DiarizationEngine(config)

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = engine.diarize(Path(f.name))

    assert result.num_speakers == 0
    assert result.segments == []


def test_diarization_missing_file_raises():
    """Diarizing a missing file raises FileNotFoundError."""
    config = DiarizationConfig(enabled=True)
    engine = DiarizationEngine(config)
    # Don't set _pipeline, so it would try to load — but we check file first
    with pytest.raises(FileNotFoundError):
        engine.diarize(Path("/nonexistent/audio.flac"))


def test_normalize_label_standard():
    """Standard SPEAKER_XX label stays unchanged."""
    assert DiarizationEngine._normalize_label("SPEAKER_00") == "SPEAKER_00"
    assert DiarizationEngine._normalize_label("SPEAKER_12") == "SPEAKER_12"


def test_normalize_label_nonstandard():
    """Non-standard labels get converted to SPEAKER_XX format."""
    label = DiarizationEngine._normalize_label("speaker_0")
    assert re.match(r"^SPEAKER_\d{2}$", label)


def test_merge_transcript_with_diarization():
    """Speaker labels are correctly assigned to transcript segments."""
    from meeting_minutes.models import TranscriptSegment, DiarizationResult, DiarizationSegment

    transcript_segments = [
        TranscriptSegment(id=0, start=0.0, end=5.0, text="Hello"),
        TranscriptSegment(id=1, start=5.5, end=10.0, text="World"),
    ]
    diarization = DiarizationResult(
        meeting_id="test",
        segments=[
            DiarizationSegment(start=0.0, end=5.5, speaker="SPEAKER_00"),
            DiarizationSegment(start=5.5, end=11.0, speaker="SPEAKER_01"),
        ],
        num_speakers=2,
    )

    result = DiarizationEngine.merge_transcript_with_diarization(
        transcript_segments, diarization
    )

    assert result[0].speaker == "SPEAKER_00"
    assert result[1].speaker == "SPEAKER_01"


# ---------------------------------------------------------------------------
# SPK-1: embedding extraction integration with the pipeline output
# ---------------------------------------------------------------------------


def _build_mock_diarize_output(n_speakers: int, with_embeddings: bool = True, dim: int = 4):
    """Build a mock pyannote DiarizeOutput with itertracks + speaker_embeddings."""
    import numpy as np

    # Segments: each speaker talks from (i*10) to (i*10 + 8) seconds — 8 s each.
    turns = []
    for i in range(n_speakers):
        turn = MagicMock()
        turn.start = float(i * 10)
        turn.end = float(i * 10 + 8)
        turns.append((turn, None, f"SPEAKER_{i:02d}"))

    annotation = MagicMock()
    annotation.itertracks = MagicMock(return_value=iter(turns))
    annotation.labels = MagicMock(
        return_value=[f"SPEAKER_{i:02d}" for i in range(n_speakers)]
    )

    output = MagicMock()
    output.speaker_diarization = annotation
    output.itertracks = annotation.itertracks  # so diarize's unwrap picks it up

    if with_embeddings:
        # Distinct unit-ish embeddings per speaker.
        output.speaker_embeddings = np.array(
            [[float(i + 1)] + [0.0] * (dim - 1) for i in range(n_speakers)],
            dtype=np.float32,
        )
    else:
        output.speaker_embeddings = None

    return output


def test_diarize_surfaces_cluster_embeddings_when_available():
    """pyannote 4.x DiarizeOutput-style pipeline surfaces per-cluster
    embeddings on the engine via last_cluster_embeddings."""
    import tempfile
    import numpy as np
    from meeting_minutes.config import DiarizationConfig
    from meeting_minutes.system1.diarize import DiarizationEngine

    config = DiarizationConfig(enabled=True)
    engine = DiarizationEngine(config)
    output = _build_mock_diarize_output(n_speakers=2, with_embeddings=True)

    # Make the pipeline unwrap path prefer speaker_diarization over itertracks.
    # (Our diarize() checks hasattr itertracks on the top-level wrapper; our
    # mock provides both — but the first check succeeds, so the unwrap skips
    # the speaker_diarization branch. We explicitly provide itertracks to
    # keep the test aligned with what the engine actually sees.)
    engine.backend._pipeline = MagicMock(return_value=output)

    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = engine.diarize(Path(f.name))

    assert result.num_speakers == 2
    embeddings = engine.last_cluster_embeddings
    assert set(embeddings.keys()) == {"SPEAKER_00", "SPEAKER_01"}
    assert embeddings["SPEAKER_00"].shape == (4,)


def test_diarize_without_embeddings_leaves_cluster_embeddings_empty():
    """Older pyannote (no speaker_embeddings) leaves the SPK-1 field empty."""
    import tempfile
    from meeting_minutes.config import DiarizationConfig
    from meeting_minutes.system1.diarize import DiarizationEngine

    config = DiarizationConfig(enabled=True)
    engine = DiarizationEngine(config)
    output = _build_mock_diarize_output(n_speakers=1, with_embeddings=False)
    engine.backend._pipeline = MagicMock(return_value=output)

    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        engine.diarize(Path(f.name))

    assert engine.last_cluster_embeddings == {}


def test_spk1_pipeline_skips_clusters_below_min_speech_duration():
    """A cluster with < 5 s of speech does not get a sample row."""
    import tempfile
    import numpy as np
    from meeting_minutes.config import DiarizationConfig
    from meeting_minutes.models import DiarizationSegment
    from meeting_minutes.system1 import speaker_identity as si
    from meeting_minutes.system1.diarize import DiarizationEngine
    from meeting_minutes.system3.db import (
        MeetingORM, PersonORM, VoiceSampleORM, get_session_factory,
    )

    # Build a diarization result with one long (>5s) and one short (<5s) cluster.
    segments = [
        DiarizationSegment(start=0.0, end=10.0, speaker="SPEAKER_00"),
        DiarizationSegment(start=15.0, end=18.0, speaker="SPEAKER_01"),
    ]

    # Fake embeddings for both clusters.
    embeddings = {
        "SPEAKER_00": np.array([1.0, 0.0, 0.0], dtype=np.float32),
        "SPEAKER_01": np.array([0.0, 1.0, 0.0], dtype=np.float32),
    }

    # Seed a person with a confirmed sample so match_clusters has a candidate.
    factory = get_session_factory("sqlite:///:memory:")
    session = factory()
    try:
        session.add(MeetingORM(meeting_id="m-test", title="t", meeting_type="standup"))
        session.add(PersonORM(person_id="p-a", name="Alice"))
        session.commit()
        session.add(VoiceSampleORM(
            person_id="p-a", meeting_id="m-test", cluster_id="SPEAKER_99",
            embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes(),
            embedding_dim=3, confirmed=True,
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ))
        session.commit()

        # The long cluster should match; the short one must not.
        eligible = {
            cid: vec for cid, vec in embeddings.items()
            if si.min_speech_duration_ok(segments, cid)
        }
        assert set(eligible.keys()) == {"SPEAKER_00"}

        matches = si.match_clusters(session, eligible)
        assert "SPEAKER_00" in matches
        assert "SPEAKER_01" not in matches  # filtered out upstream

        # Simulate what the pipeline does — write unconfirmed sample only for
        # the long cluster.
        for cid, vec in eligible.items():
            m = matches[cid]
            if m.person_id is not None:
                si.write_sample(
                    session, person_id=m.person_id, meeting_id="m-test",
                    cluster_id=cid, embedding=vec, confirmed=False,
                )

        # Verify SPEAKER_00 got a row, SPEAKER_01 did not.
        rows = session.query(VoiceSampleORM).filter_by(meeting_id="m-test").all()
        cluster_ids = {r.cluster_id for r in rows if r.cluster_id in {"SPEAKER_00", "SPEAKER_01"}}
        assert cluster_ids == {"SPEAKER_00"}
    finally:
        session.close()
