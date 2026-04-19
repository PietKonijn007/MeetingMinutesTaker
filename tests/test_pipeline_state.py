"""Tests for the pipeline state machine (PIP-1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from meeting_minutes.pipeline_state import (
    Stage,
    Status,
    get_stages,
    has_terminal_state,
    mark_failed,
    mark_running,
    mark_skipped,
    mark_succeeded,
    next_stage,
    reset_interrupted,
)
from meeting_minutes.system3.db import PipelineStageORM


MEETING_ID = "meeting-abc"


def test_stage_ordering_next():
    assert Stage.CAPTURE.next() == Stage.TRANSCRIBE
    assert Stage.TRANSCRIBE.next() == Stage.DIARIZE
    assert Stage.DIARIZE.next() == Stage.GENERATE
    assert Stage.GENERATE.next() == Stage.INGEST
    assert Stage.INGEST.next() == Stage.EMBED
    assert Stage.EMBED.next() == Stage.EXPORT
    assert Stage.EXPORT.next() is None


def test_stage_ordered_is_declared_order():
    order = Stage.ordered()
    assert [s.value for s in order] == [
        "capture", "transcribe", "diarize",
        "generate", "ingest", "embed", "export",
    ]


def test_mark_running_then_succeeded_sets_timestamps(db_session):
    before = datetime.now(timezone.utc)
    state = mark_running(db_session, MEETING_ID, Stage.TRANSCRIBE)
    assert state.status == Status.RUNNING
    assert state.started_at is not None
    assert state.finished_at is None
    assert state.attempt == 1

    succeeded = mark_succeeded(
        db_session, MEETING_ID, Stage.TRANSCRIBE, artifact_path="/tmp/x.json"
    )
    assert succeeded.status == Status.SUCCEEDED
    assert succeeded.finished_at is not None
    assert succeeded.artifact_path == "/tmp/x.json"
    assert succeeded.last_error is None

    # Started_at preserved across status changes.
    started = succeeded.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    assert started >= before.replace(microsecond=0) - timedelta(seconds=1)


def test_mark_failed_then_running_increments_attempt(db_session):
    mark_running(db_session, MEETING_ID, Stage.GENERATE)
    failed = mark_failed(db_session, MEETING_ID, Stage.GENERATE, "boom")
    assert failed.status == Status.FAILED
    assert failed.last_error == "boom"
    assert failed.last_error_at is not None
    assert failed.attempt == 1

    retried = mark_running(db_session, MEETING_ID, Stage.GENERATE)
    assert retried.status == Status.RUNNING
    assert retried.attempt == 2

    # A second retry after another failure increments again.
    mark_failed(db_session, MEETING_ID, Stage.GENERATE, "again")
    third = mark_running(db_session, MEETING_ID, Stage.GENERATE)
    assert third.attempt == 3


def test_mark_failed_with_error_code(db_session):
    mark_running(db_session, MEETING_ID, Stage.INGEST)
    state = mark_failed(
        db_session, MEETING_ID, Stage.INGEST, "disk full", error_code="E_DISK"
    )
    assert state.last_error == "E_DISK: disk full"


def test_get_stages_returns_declared_order(db_session):
    # Insert out of order.
    mark_running(db_session, MEETING_ID, Stage.EMBED)
    mark_running(db_session, MEETING_ID, Stage.CAPTURE)
    mark_running(db_session, MEETING_ID, Stage.GENERATE)
    states = get_stages(db_session, MEETING_ID)
    assert [s.stage for s in states] == [Stage.CAPTURE, Stage.GENERATE, Stage.EMBED]


def test_next_stage_first_run_is_capture(db_session):
    assert next_stage(db_session, MEETING_ID) == Stage.CAPTURE


def test_next_stage_skips_succeeded_stages(db_session):
    mark_succeeded(db_session, MEETING_ID, Stage.CAPTURE)
    mark_succeeded(db_session, MEETING_ID, Stage.TRANSCRIBE)
    mark_succeeded(db_session, MEETING_ID, Stage.DIARIZE)
    assert next_stage(db_session, MEETING_ID) == Stage.GENERATE


def test_next_stage_returns_failed_stage(db_session):
    mark_succeeded(db_session, MEETING_ID, Stage.CAPTURE)
    mark_succeeded(db_session, MEETING_ID, Stage.TRANSCRIBE)
    mark_succeeded(db_session, MEETING_ID, Stage.DIARIZE)
    mark_running(db_session, MEETING_ID, Stage.GENERATE)
    mark_failed(db_session, MEETING_ID, Stage.GENERATE, "boom")
    assert next_stage(db_session, MEETING_ID) == Stage.GENERATE


def test_next_stage_returns_none_when_all_succeeded(db_session):
    for stage in Stage.ordered():
        mark_succeeded(db_session, MEETING_ID, stage)
    assert next_stage(db_session, MEETING_ID) is None


def test_has_terminal_state_requires_ingest_or_later(db_session):
    assert has_terminal_state(db_session, MEETING_ID) is False

    mark_succeeded(db_session, MEETING_ID, Stage.CAPTURE)
    mark_succeeded(db_session, MEETING_ID, Stage.TRANSCRIBE)
    mark_succeeded(db_session, MEETING_ID, Stage.GENERATE)
    assert has_terminal_state(db_session, MEETING_ID) is False


def test_has_terminal_state_export_succeeded(db_session):
    for stage in [Stage.CAPTURE, Stage.TRANSCRIBE, Stage.GENERATE, Stage.INGEST, Stage.EXPORT]:
        mark_succeeded(db_session, MEETING_ID, stage)
    mark_skipped(db_session, MEETING_ID, Stage.EMBED)
    assert has_terminal_state(db_session, MEETING_ID) is True


def test_has_terminal_state_ingest_and_downstream_succeeded_or_skipped(db_session):
    mark_succeeded(db_session, MEETING_ID, Stage.INGEST)
    mark_skipped(db_session, MEETING_ID, Stage.EMBED)
    mark_skipped(db_session, MEETING_ID, Stage.EXPORT)
    assert has_terminal_state(db_session, MEETING_ID) is True


def test_has_terminal_state_false_when_later_stage_failed(db_session):
    mark_succeeded(db_session, MEETING_ID, Stage.INGEST)
    mark_failed(db_session, MEETING_ID, Stage.EMBED, "boom")
    assert has_terminal_state(db_session, MEETING_ID) is False


def test_reset_interrupted_flips_old_running_rows(db_session):
    # An ancient running row representing a crashed pipeline.
    row = PipelineStageORM(
        meeting_id=MEETING_ID,
        stage=Stage.GENERATE.value,
        status=Status.RUNNING.value,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        attempt=1,
    )
    db_session.add(row)
    db_session.commit()

    reset = reset_interrupted(db_session, threshold_minutes=30)
    assert (MEETING_ID, Stage.GENERATE) in reset

    states = {s.stage: s for s in get_stages(db_session, MEETING_ID)}
    assert states[Stage.GENERATE].status == Status.FAILED
    assert states[Stage.GENERATE].last_error == "interrupted"


def test_reset_interrupted_leaves_fresh_running_rows_alone(db_session):
    mark_running(db_session, "other-meeting", Stage.TRANSCRIBE)
    reset = reset_interrupted(db_session, threshold_minutes=30)
    assert reset == []

    states = get_stages(db_session, "other-meeting")
    assert states[0].status == Status.RUNNING


def test_reset_interrupted_handles_no_started_at(db_session):
    row = PipelineStageORM(
        meeting_id=MEETING_ID,
        stage=Stage.CAPTURE.value,
        status=Status.RUNNING.value,
        started_at=None,
        attempt=1,
    )
    db_session.add(row)
    db_session.commit()

    reset = reset_interrupted(db_session)
    assert (MEETING_ID, Stage.CAPTURE) in reset
    states = get_stages(db_session, MEETING_ID)
    assert states[0].status == Status.FAILED
