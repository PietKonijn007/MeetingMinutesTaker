"""Tests for retention cleanup — especially the pipeline-state interaction (PIP-1)."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from meeting_minutes.config import AppConfig
from meeting_minutes.pipeline_state import (
    Stage,
    mark_failed,
    mark_running,
    mark_skipped,
    mark_succeeded,
)
from meeting_minutes.retention import enforce_retention
from meeting_minutes.system3.db import get_session_factory


def _make_aged_audio(dir_path: Path, meeting_id: str, age_days: int) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{meeting_id}.flac"
    path.write_bytes(b"FLAC")
    old_time = time.time() - (age_days * 86400)
    os.utime(path, (old_time, old_time))
    return path


def _build_config(tmp_path: Path) -> AppConfig:
    config = AppConfig(data_dir=str(tmp_path))
    config.storage.sqlite_path = str(tmp_path / "meetings.db")
    config.retention.audio_days = 1
    config.retention.transcript_days = 0
    config.retention.minutes_days = 0
    config.retention.backup_days = 0
    return config


def _session(config: AppConfig):
    session_factory = get_session_factory(f"sqlite:///{config.storage.sqlite_path}")
    return session_factory()


def test_audio_deleted_when_export_succeeded(tmp_path):
    """A meeting with export=succeeded has audio cleaned up normally."""
    config = _build_config(tmp_path)
    meeting_id = str(uuid.uuid4())
    audio = _make_aged_audio(tmp_path / "recordings", meeting_id, age_days=10)

    session = _session(config)
    try:
        for stage in [Stage.CAPTURE, Stage.TRANSCRIBE, Stage.DIARIZE,
                      Stage.GENERATE, Stage.INGEST, Stage.EXPORT]:
            mark_succeeded(session, meeting_id, stage)
        mark_skipped(session, meeting_id, Stage.EMBED)
    finally:
        session.close()

    deleted = enforce_retention(config)
    assert deleted["audio"] == 1
    assert not audio.exists()


def test_audio_preserved_when_ingest_failed(tmp_path):
    """A meeting whose pipeline is not terminal keeps its audio."""
    config = _build_config(tmp_path)
    meeting_id = str(uuid.uuid4())
    audio = _make_aged_audio(tmp_path / "recordings", meeting_id, age_days=10)

    session = _session(config)
    try:
        mark_succeeded(session, meeting_id, Stage.CAPTURE)
        mark_succeeded(session, meeting_id, Stage.TRANSCRIBE)
        mark_succeeded(session, meeting_id, Stage.DIARIZE)
        mark_succeeded(session, meeting_id, Stage.GENERATE)
        mark_running(session, meeting_id, Stage.INGEST)
        mark_failed(session, meeting_id, Stage.INGEST, "db locked")
    finally:
        session.close()

    deleted = enforce_retention(config)
    assert deleted["audio"] == 0
    assert audio.exists()


def test_audio_preserved_when_pipeline_pending(tmp_path):
    """Pipeline stages still pending -> audio is preserved."""
    config = _build_config(tmp_path)
    meeting_id = str(uuid.uuid4())
    audio = _make_aged_audio(tmp_path / "recordings", meeting_id, age_days=10)

    session = _session(config)
    try:
        mark_succeeded(session, meeting_id, Stage.CAPTURE)
        mark_running(session, meeting_id, Stage.TRANSCRIBE)
    finally:
        session.close()

    deleted = enforce_retention(config)
    assert deleted["audio"] == 0
    assert audio.exists()


def test_audio_deleted_when_no_pipeline_rows(tmp_path):
    """Legacy meetings with no pipeline_stages rows fall through to mtime-based cleanup."""
    config = _build_config(tmp_path)
    meeting_id = str(uuid.uuid4())
    audio = _make_aged_audio(tmp_path / "recordings", meeting_id, age_days=10)

    # Initialise the DB but insert nothing.
    _session(config).close()

    deleted = enforce_retention(config)
    assert deleted["audio"] == 1
    assert not audio.exists()
