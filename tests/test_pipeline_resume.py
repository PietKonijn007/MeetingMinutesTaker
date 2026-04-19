"""Integration tests for pipeline resume_from (PIP-1)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_minutes.config import AppConfig
from meeting_minutes.pipeline import PipelineOrchestrator
from meeting_minutes.pipeline_state import (
    Stage,
    Status,
    get_stages,
    mark_failed,
    mark_running,
    mark_succeeded,
)
from meeting_minutes.system3.db import get_session_factory


@pytest.fixture
def orchestrator_with_db(tmp_path, monkeypatch):
    """PipelineOrchestrator wired to a temporary SQLite DB on disk."""
    config = AppConfig(data_dir=str(tmp_path))
    config.storage.sqlite_path = str(tmp_path / "meetings.db")
    config.obsidian.enabled = False
    config.backup.enabled = False
    return PipelineOrchestrator(config), tmp_path


def _read_states(db_path: Path, meeting_id: str):
    session_factory = get_session_factory(f"sqlite:///{db_path}")
    session = session_factory()
    try:
        states = get_stages(session, meeting_id)
    finally:
        session.close()
    return {s.stage: s for s in states}


def _seed_state(db_path: Path, meeting_id: str, seeded: dict[Stage, Status]):
    session_factory = get_session_factory(f"sqlite:///{db_path}")
    session = session_factory()
    try:
        for stage, status in seeded.items():
            if status == Status.SUCCEEDED:
                mark_succeeded(session, meeting_id, stage)
            elif status == Status.FAILED:
                mark_running(session, meeting_id, stage)
                mark_failed(session, meeting_id, stage, "seed")
            elif status == Status.RUNNING:
                mark_running(session, meeting_id, stage)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_resume_after_failed_generate_runs_forward(orchestrator_with_db):
    """A failed generate stage resumes from generate — transcribe is skipped."""
    orchestrator, tmp_path = orchestrator_with_db
    meeting_id = str(uuid.uuid4())
    db_path = tmp_path / "meetings.db"

    _seed_state(db_path, meeting_id, {
        Stage.CAPTURE: Status.SUCCEEDED,
        Stage.TRANSCRIBE: Status.SUCCEEDED,
        Stage.DIARIZE: Status.SUCCEEDED,
        Stage.GENERATE: Status.FAILED,
    })

    gen_mock = AsyncMock(return_value=tmp_path / "minutes" / f"{meeting_id}.json")
    ingest_mock = AsyncMock()
    transcribe_mock = AsyncMock()
    embed_mock = MagicMock()
    export_mock = MagicMock()

    with patch.object(orchestrator, "run_transcription", transcribe_mock), \
         patch.object(orchestrator, "run_generation", gen_mock), \
         patch.object(orchestrator, "run_ingestion", ingest_mock), \
         patch.object(orchestrator, "_embed_meeting", embed_mock), \
         patch.object(orchestrator, "_export_to_obsidian_from_file", export_mock):
        await orchestrator.resume_from(meeting_id)

    # Transcription was already succeeded — must not run again.
    transcribe_mock.assert_not_called()
    # Generate through export should all execute.
    gen_mock.assert_called_once_with(meeting_id)
    ingest_mock.assert_called_once_with(meeting_id)
    embed_mock.assert_called_once_with(meeting_id)
    export_mock.assert_called_once_with(meeting_id)

    states = _read_states(db_path, meeting_id)
    assert states[Stage.GENERATE].status == Status.SUCCEEDED
    assert states[Stage.INGEST].status == Status.SUCCEEDED
    assert states[Stage.EMBED].status == Status.SUCCEEDED
    assert states[Stage.EXPORT].status == Status.SUCCEEDED


@pytest.mark.asyncio
async def test_resume_from_explicit_stage(orchestrator_with_db):
    """from_stage explicitly picks the starting point."""
    orchestrator, tmp_path = orchestrator_with_db
    meeting_id = str(uuid.uuid4())
    db_path = tmp_path / "meetings.db"

    _seed_state(db_path, meeting_id, {
        Stage.CAPTURE: Status.SUCCEEDED,
        Stage.TRANSCRIBE: Status.SUCCEEDED,
        Stage.DIARIZE: Status.SUCCEEDED,
        Stage.GENERATE: Status.SUCCEEDED,
        Stage.INGEST: Status.SUCCEEDED,
    })

    ingest_mock = AsyncMock()
    embed_mock = MagicMock()
    export_mock = MagicMock()

    with patch.object(orchestrator, "run_ingestion", ingest_mock), \
         patch.object(orchestrator, "_embed_meeting", embed_mock), \
         patch.object(orchestrator, "_export_to_obsidian_from_file", export_mock):
        await orchestrator.resume_from(meeting_id, from_stage=Stage.EMBED)

    # INGEST was succeeded and not in range — must not run.
    ingest_mock.assert_not_called()
    embed_mock.assert_called_once_with(meeting_id)
    export_mock.assert_called_once_with(meeting_id)


@pytest.mark.asyncio
async def test_resume_noop_when_all_succeeded(orchestrator_with_db):
    orchestrator, tmp_path = orchestrator_with_db
    meeting_id = str(uuid.uuid4())
    db_path = tmp_path / "meetings.db"

    for stage in Stage.ordered():
        _seed_state(db_path, meeting_id, {stage: Status.SUCCEEDED})

    gen_mock = AsyncMock()
    with patch.object(orchestrator, "run_generation", gen_mock):
        await orchestrator.resume_from(meeting_id)

    gen_mock.assert_not_called()


@pytest.mark.asyncio
async def test_resume_failed_stage_increments_attempt(orchestrator_with_db):
    """Retrying a failed stage via resume_from bumps the attempt counter."""
    orchestrator, tmp_path = orchestrator_with_db
    meeting_id = str(uuid.uuid4())
    db_path = tmp_path / "meetings.db"

    _seed_state(db_path, meeting_id, {
        Stage.CAPTURE: Status.SUCCEEDED,
        Stage.TRANSCRIBE: Status.SUCCEEDED,
        Stage.DIARIZE: Status.SUCCEEDED,
        Stage.GENERATE: Status.FAILED,
    })

    gen_mock = AsyncMock(return_value=tmp_path / "minutes" / f"{meeting_id}.json")
    ingest_mock = AsyncMock()
    embed_mock = MagicMock()
    export_mock = MagicMock()

    with patch.object(orchestrator, "run_generation", gen_mock), \
         patch.object(orchestrator, "run_ingestion", ingest_mock), \
         patch.object(orchestrator, "_embed_meeting", embed_mock), \
         patch.object(orchestrator, "_export_to_obsidian_from_file", export_mock):
        await orchestrator.resume_from(meeting_id)

    states = _read_states(db_path, meeting_id)
    assert states[Stage.GENERATE].attempt == 2
