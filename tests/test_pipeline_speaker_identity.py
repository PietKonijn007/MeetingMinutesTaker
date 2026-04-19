"""Integration test: full SPK-1 happy path across two meetings.

Mocks the embedding extraction boundary — real pyannote is never called —
and walks through the user flow the spec promises:

    1. First meeting: user names SPEAKER_00 Jon → sample is confirmed.
    2. Second meeting: a cluster with an embedding similar to Jon's yields a
       high-tier match with person_id=Jon *before* the user confirms.

The second assertion would fail without a confirmed sample from meeting 1,
which is exactly the compounding behaviour the spec calls out.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from meeting_minutes.system1 import speaker_identity as si
from meeting_minutes.system3.db import (
    MeetingORM,
    PersonORM,
    VoiceSampleORM,
    get_session_factory,
)


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(arr)
    return arr / n if n else arr


def test_spk1_compounds_across_meetings():
    factory = get_session_factory("sqlite:///:memory:")
    session = factory()

    try:
        # Fixtures: Jon + two meetings.
        jon = PersonORM(person_id="p-jon", name="Jon")
        session.add(jon)
        m1 = MeetingORM(meeting_id="m-001", title="1:1", meeting_type="one_on_one")
        m2 = MeetingORM(meeting_id="m-002", title="1:1 #2", meeting_type="one_on_one")
        session.add_all([m1, m2])
        session.commit()

        # ------------------------------------------------------------------
        # Meeting 1: pipeline writes unconfirmed sample; user confirms.
        # ------------------------------------------------------------------
        jon_embedding_m1 = _unit([1.0, 0.05, 0.02, 0.03])

        # Pipeline stage: no candidates yet → match_clusters returns unknown.
        matches_m1 = si.match_clusters(session, {"SPEAKER_00": jon_embedding_m1})
        assert matches_m1["SPEAKER_00"].tier == "unknown"
        assert matches_m1["SPEAKER_00"].person_id is None

        # Pipeline still writes an unconfirmed sample under the *user's* target
        # person only once the user picks a name. Here we emulate that click:
        # first the write, then the confirm.
        si.write_sample(
            session,
            person_id="p-jon",
            meeting_id="m-001",
            cluster_id="SPEAKER_00",
            embedding=jon_embedding_m1,
            confirmed=False,
        )
        # API endpoint logic: invalidate any contaminated sample, then confirm.
        si.invalidate_contamination(session, "m-001", "SPEAKER_00", "p-jon")
        confirmed_row = si.confirm_sample(session, "m-001", "SPEAKER_00", "p-jon")
        assert confirmed_row is not None
        assert confirmed_row.confirmed is True

        # ------------------------------------------------------------------
        # Meeting 2: a similar-but-not-identical embedding should auto-match
        # Jon with a high-tier suggestion, zero further user action.
        # ------------------------------------------------------------------
        jon_embedding_m2 = _unit([0.98, 0.10, 0.05, 0.04])

        matches_m2 = si.match_clusters(session, {"SPEAKER_00": jon_embedding_m2})
        match = matches_m2["SPEAKER_00"]

        assert match.person_id == "p-jon"
        assert match.person_name == "Jon"
        assert match.tier == "high", f"Expected high tier, got {match.tier} at score {match.score}"
        assert match.score >= 0.85
    finally:
        session.close()


def test_spk1_relabel_across_meetings_flips_contaminated_sample():
    """Meeting 1: Jon confirmed. Later the user realises it was Sarah and
    relabels — Jon's old sample drops out of his centroid."""
    factory = get_session_factory("sqlite:///:memory:")
    session = factory()

    try:
        session.add(MeetingORM(meeting_id="m-001", title="t", meeting_type="standup"))
        session.add(PersonORM(person_id="p-jon", name="Jon"))
        session.add(PersonORM(person_id="p-sarah", name="Sarah"))
        session.commit()

        vec = _unit([0.9, 0.1, 0.0])
        si.write_sample(
            session, person_id="p-jon", meeting_id="m-001",
            cluster_id="SPEAKER_00", embedding=vec, confirmed=True,
        )
        assert si.compute_centroid(session, "p-jon") is not None

        # Relabel: Jon → Sarah.
        invalidated = si.invalidate_contamination(
            session, "m-001", "SPEAKER_00", "p-sarah",
        )
        assert invalidated == 1
        # Jon's centroid collapses back to None; he has no other confirmed sample.
        assert si.compute_centroid(session, "p-jon") is None

        # Now the rename endpoint writes + confirms Sarah's sample.
        si.write_sample(
            session, person_id="p-sarah", meeting_id="m-001",
            cluster_id="SPEAKER_00", embedding=vec, confirmed=True,
        )
        assert si.compute_centroid(session, "p-sarah") is not None
    finally:
        session.close()
