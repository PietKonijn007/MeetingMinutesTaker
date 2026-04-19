"""Pipeline stage state machine (PIP-1).

Persists per-(meeting_id, stage) state so the pipeline can be resumed after
a crash or partial failure. See ``specs/07-implementation-plan-batch3.md``
for the full design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import NamedTuple

from sqlalchemy.orm import Session

from meeting_minutes.system3.db import PipelineStageORM


# Default age threshold above which a ``running`` row is considered
# interrupted (e.g. server crashed). Kept small enough that a normal pipeline
# run fits comfortably under it.
INTERRUPTED_THRESHOLD_MINUTES = 30


class Stage(str, Enum):
    """Ordered pipeline stages. Iteration order defines dependency order."""

    CAPTURE = "capture"
    TRANSCRIBE = "transcribe"
    DIARIZE = "diarize"
    GENERATE = "generate"
    INGEST = "ingest"
    EMBED = "embed"
    EXPORT = "export"

    @classmethod
    def ordered(cls) -> list["Stage"]:
        return list(cls)

    def next(self) -> "Stage | None":
        """Return the stage that follows this one, or None if terminal."""
        members = Stage.ordered()
        idx = members.index(self)
        if idx + 1 >= len(members):
            return None
        return members[idx + 1]


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageState(NamedTuple):
    meeting_id: str
    stage: Stage
    status: Status
    started_at: datetime | None
    finished_at: datetime | None
    attempt: int
    last_error: str | None
    last_error_at: datetime | None
    artifact_path: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_state(row: PipelineStageORM) -> StageState:
    return StageState(
        meeting_id=row.meeting_id,
        stage=Stage(row.stage),
        status=Status(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        attempt=row.attempt or 1,
        last_error=row.last_error,
        last_error_at=row.last_error_at,
        artifact_path=row.artifact_path,
    )


def _get_or_create(session: Session, meeting_id: str, stage: Stage) -> PipelineStageORM:
    row = (
        session.query(PipelineStageORM)
        .filter_by(meeting_id=meeting_id, stage=stage.value)
        .one_or_none()
    )
    if row is None:
        row = PipelineStageORM(
            meeting_id=meeting_id,
            stage=stage.value,
            status=Status.PENDING.value,
            attempt=1,
        )
        session.add(row)
    return row


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mark_running(session: Session, meeting_id: str, stage: Stage) -> StageState:
    """Upsert the stage row as ``running``. Increments attempt on retry."""
    row = _get_or_create(session, meeting_id, stage)
    previous_status = row.status
    row.status = Status.RUNNING.value
    row.started_at = _now()
    row.finished_at = None
    if previous_status == Status.FAILED.value:
        row.attempt = (row.attempt or 1) + 1
    elif row.attempt is None:
        row.attempt = 1
    session.commit()
    session.refresh(row)
    return _to_state(row)


def mark_succeeded(
    session: Session,
    meeting_id: str,
    stage: Stage,
    artifact_path: str | None = None,
) -> StageState:
    row = _get_or_create(session, meeting_id, stage)
    row.status = Status.SUCCEEDED.value
    row.finished_at = _now()
    row.last_error = None
    if artifact_path is not None:
        row.artifact_path = artifact_path
    session.commit()
    session.refresh(row)
    return _to_state(row)


def mark_failed(
    session: Session,
    meeting_id: str,
    stage: Stage,
    error_msg: str,
    error_code: str | None = None,
) -> StageState:
    row = _get_or_create(session, meeting_id, stage)
    row.status = Status.FAILED.value
    row.finished_at = _now()
    row.last_error = f"{error_code}: {error_msg}" if error_code else error_msg
    row.last_error_at = _now()
    session.commit()
    session.refresh(row)
    return _to_state(row)


def mark_skipped(session: Session, meeting_id: str, stage: Stage) -> StageState:
    row = _get_or_create(session, meeting_id, stage)
    row.status = Status.SKIPPED.value
    row.finished_at = _now()
    session.commit()
    session.refresh(row)
    return _to_state(row)


def get_stages(session: Session, meeting_id: str) -> list[StageState]:
    """Return all stored stage rows for a meeting in declared stage order."""
    rows = (
        session.query(PipelineStageORM)
        .filter_by(meeting_id=meeting_id)
        .all()
    )
    order = {s.value: i for i, s in enumerate(Stage.ordered())}
    rows.sort(key=lambda r: order.get(r.stage, 999))
    return [_to_state(r) for r in rows]


def next_stage(session: Session, meeting_id: str) -> Stage | None:
    """Return the first stage that is not ``succeeded`` in declared order.

    Stages with no row at all are considered pending. Returns ``None`` when
    every stage in ``Stage.ordered()`` has a ``succeeded`` row.
    """
    existing = {s.stage: s for s in get_stages(session, meeting_id)}
    for stage in Stage.ordered():
        state = existing.get(stage)
        if state is None or state.status != Status.SUCCEEDED:
            return stage
    return None


def has_terminal_state(session: Session, meeting_id: str) -> bool:
    """True when the pipeline has reached a terminal, safe-to-clean state.

    Terminal means either (a) ``export`` succeeded, or (b) every stage from
    ``ingest`` onward is ``succeeded`` or ``skipped``. Used by retention so
    audio for interrupted pipelines is preserved.
    """
    states = {s.stage: s for s in get_stages(session, meeting_id)}
    export = states.get(Stage.EXPORT)
    if export and export.status == Status.SUCCEEDED:
        return True

    terminal_from = [Stage.INGEST, Stage.EMBED, Stage.EXPORT]
    for stage in terminal_from:
        state = states.get(stage)
        if state is None:
            return False
        if state.status not in (Status.SUCCEEDED, Status.SKIPPED):
            return False
    return True


def reset_interrupted(
    session: Session,
    threshold_minutes: int = INTERRUPTED_THRESHOLD_MINUTES,
) -> list[tuple[str, Stage]]:
    """Flip ``running`` rows older than threshold to ``failed``.

    Intended to be called from the server lifespan on startup to detect
    stages that were mid-flight when the process died. Returns the list of
    (meeting_id, stage) that were reset so callers can log a summary.
    """
    cutoff = _now() - timedelta(minutes=threshold_minutes)
    rows = (
        session.query(PipelineStageORM)
        .filter(PipelineStageORM.status == Status.RUNNING.value)
        .all()
    )

    reset: list[tuple[str, Stage]] = []
    for row in rows:
        started = row.started_at
        # Normalise naive datetimes written by SQLite to UTC-aware for
        # comparison against our UTC-aware cutoff.
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is None or started < cutoff:
            row.status = Status.FAILED.value
            row.last_error = "interrupted"
            row.last_error_at = _now()
            row.finished_at = _now()
            reset.append((row.meeting_id, Stage(row.stage)))

    if reset:
        session.commit()
    return reset
