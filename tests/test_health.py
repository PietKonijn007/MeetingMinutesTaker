"""Tests for the HLT-1 health module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text

from meeting_minutes.config import AppConfig
from meeting_minutes.health import check_all, repair
from meeting_minutes.pipeline_state import Stage, mark_succeeded
from meeting_minutes.system3.db import (
    EmbeddingChunkORM,
    MeetingORM,
    MinutesORM,
    TranscriptORM,
)


@pytest.fixture
def seeded_session(db_session, tmp_path):
    """A session with one ingested meeting — FTS row, a transcript file, and one embedding."""
    audio_file = tmp_path / "meeting1.flac"
    audio_file.write_bytes(b"fake-flac-bytes")

    meeting = MeetingORM(
        meeting_id="m1",
        title="Standup",
        date=datetime.now(timezone.utc),
        meeting_type="standup",
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(meeting)

    db_session.add(MinutesORM(
        meeting_id="m1",
        minutes_id="min1",
        markdown_content="# Summary\nHello world",
        summary="hello",
        generated_at=datetime.now(timezone.utc),
        llm_model="x",
    ))
    db_session.add(TranscriptORM(
        meeting_id="m1",
        full_text="hello",
        language="en",
        audio_file_path=str(audio_file),
    ))
    db_session.flush()

    db_session.execute(sql_text(
        "INSERT INTO meetings_fts(meeting_id, title, transcript_text, minutes_text) "
        "VALUES (:mid, :t, :tt, :mt)"
    ), {"mid": "m1", "t": "Standup", "tt": "hello", "mt": "# Summary"})

    # One embedding chunk with a matching vector
    chunk = EmbeddingChunkORM(
        meeting_id="m1",
        chunk_type="summary",
        text="hello",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(chunk)
    db_session.flush()

    import struct
    vec_bytes = struct.pack("384f", *([0.01] * 384))
    try:
        db_session.execute(sql_text(
            "INSERT INTO embedding_vectors(chunk_id, embedding) VALUES (:cid, vec_f32(:v))"
        ), {"cid": chunk.chunk_id, "v": vec_bytes})
    except Exception:
        # sqlite-vec may not be available in CI — tests that need it will skip.
        pass

    db_session.commit()
    return db_session


@pytest.fixture
def config(tmp_path):
    c = AppConfig(data_dir=str(tmp_path))
    return c


def _vec_available(session) -> bool:
    try:
        session.execute(sql_text("SELECT vec_version()")).fetchone()
        return True
    except Exception:
        return False


def test_clean_report_is_all_ok(seeded_session, config):
    report = check_all(seeded_session, config)
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["sqlite_integrity"] == "ok"
    assert statuses["fts_row_count"] == "ok"
    assert statuses["final_meetings_have_minutes"] == "ok"
    assert statuses["audio_files_present"] == "ok"
    # voice_samples skipped because the table isn't present — should be ok
    assert statuses["voice_samples_orphan"] == "ok"


def test_fts_mismatch_fails_and_repair_rebuilds(seeded_session, config):
    # Drop the FTS row deliberately.
    seeded_session.execute(sql_text("DELETE FROM meetings_fts WHERE meeting_id = 'm1'"))
    seeded_session.commit()

    report = check_all(seeded_session, config)
    fts = next(c for c in report.checks if c.name == "fts_row_count")
    assert fts.status == "fail"
    assert fts.repairable is True
    assert fts.context["meetings_count"] == 1
    assert fts.context["fts_count"] == 0

    log = repair(report, seeded_session, config, dry_run=False)
    actions = [a["action"] for a in log.actions if a["check"] == "fts_row_count"]
    assert "rebuild" in actions

    post = check_all(seeded_session, config)
    post_fts = next(c for c in post.checks if c.name == "fts_row_count")
    assert post_fts.status == "ok"


def test_dry_run_does_not_mutate(seeded_session, config):
    seeded_session.execute(sql_text("DELETE FROM meetings_fts WHERE meeting_id = 'm1'"))
    seeded_session.commit()

    before = seeded_session.execute(sql_text("SELECT COUNT(*) FROM meetings_fts")).scalar()
    report = check_all(seeded_session, config)
    log = repair(report, seeded_session, config, dry_run=True)
    after = seeded_session.execute(sql_text("SELECT COUNT(*) FROM meetings_fts")).scalar()

    assert before == after == 0
    # All entries in the log are dry_run=True.
    assert log.actions
    assert all(a["dry_run"] is True for a in log.actions)


def test_missing_vector_orphan_detected(seeded_session, config):
    if not _vec_available(seeded_session):
        pytest.skip("sqlite-vec not loaded — cannot test vector orphan detection")

    # Delete the vector row for the existing chunk → orphan
    chunk_id = seeded_session.query(EmbeddingChunkORM.chunk_id).scalar()
    seeded_session.execute(
        sql_text("DELETE FROM embedding_vectors WHERE chunk_id = :cid"),
        {"cid": chunk_id},
    )
    seeded_session.commit()

    report = check_all(seeded_session, config)
    ev = next(c for c in report.checks if c.name == "embedding_vectors")
    assert ev.status == "fail"
    assert ev.repairable is True
    assert "m1" in ev.context["meeting_ids"]


def test_audio_missing_but_terminal_is_warn_not_fail(seeded_session, config, tmp_path):
    # Delete the on-disk audio file and mark the pipeline as terminal.
    audio_path = seeded_session.query(TranscriptORM.audio_file_path).scalar()
    Path(audio_path).unlink()

    mark_succeeded(seeded_session, "m1", Stage.INGEST)
    mark_succeeded(seeded_session, "m1", Stage.EMBED)
    mark_succeeded(seeded_session, "m1", Stage.EXPORT)

    report = check_all(seeded_session, config)
    audio_check = next(c for c in report.checks if c.name == "audio_files_present")
    assert audio_check.status == "warn"
    assert "retention" in audio_check.detail.lower() or "terminal" in audio_check.detail.lower()


def test_audio_missing_non_terminal_warns_unsafe(seeded_session, config, tmp_path):
    audio_path = seeded_session.query(TranscriptORM.audio_file_path).scalar()
    Path(audio_path).unlink()

    report = check_all(seeded_session, config)
    audio_check = next(c for c in report.checks if c.name == "audio_files_present")
    assert audio_check.status == "warn"
    assert audio_check.context.get("missing_unsafe") == ["m1"]


def test_final_meeting_without_minutes_warns(db_session, config):
    db_session.add(MeetingORM(
        meeting_id="m2",
        title="Orphan",
        date=datetime.now(timezone.utc),
        meeting_type="standup",
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    report = check_all(db_session, config)
    check = next(c for c in report.checks if c.name == "final_meetings_have_minutes")
    assert check.status == "warn"
    assert "m2" in check.context["meeting_ids"]


def test_only_parameter_restricts_repair(seeded_session, config):
    seeded_session.execute(sql_text("DELETE FROM meetings_fts WHERE meeting_id = 'm1'"))
    seeded_session.commit()

    report = check_all(seeded_session, config)
    log = repair(report, seeded_session, config, dry_run=True, only="embedding_vectors")
    # No fts_row_count actions in the log (filtered out by `only`).
    fts_actions = [a for a in log.actions if a["check"] == "fts_row_count"]
    assert fts_actions == []


def test_integrity_check_ok_on_healthy_db(seeded_session, config):
    report = check_all(seeded_session, config)
    integrity = next(c for c in report.checks if c.name == "sqlite_integrity")
    assert integrity.status == "ok"
    assert integrity.repairable is False
