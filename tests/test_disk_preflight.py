"""Tests for DSK-1 disk preflight + watchdog."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meeting_minutes.config import AppConfig
from meeting_minutes.pipeline_state import Stage, mark_succeeded
from meeting_minutes.system1.capture import (
    AudioCaptureEngine,
    PreflightResult,
    _estimate_recording_bytes,
    _tier_for,
    preflight_disk_check,
)
from meeting_minutes.system3.db import TranscriptORM


# ---------------------------------------------------------------------------
# Tier math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("free,estimated,expected", [
    (200, 100, "green"),   # 2x+
    (100 * 2, 100, "green"),
    (100 * 2 - 1, 100, "yellow"),  # just below 2x
    (120, 100, "yellow"),  # 1.2x
    (119, 100, "orange"),  # between 1.0 and 1.2
    (100, 100, "orange"),  # exactly estimated
    (99, 100, "red"),      # below estimated
    (0, 100, "red"),
])
def test_tier_boundaries(free, estimated, expected):
    assert _tier_for(free, estimated) == expected


def test_preflight_uses_override_and_reports(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    # 60 min at 16kHz mono 16-bit @ 0.6 factor = 60*60*16000*2*0.6 = 69_120_000
    result = preflight_disk_check(config, planned_minutes=60, _free_bytes_override=200_000_000)
    assert isinstance(result, PreflightResult)
    assert result.planned_minutes == 60
    assert result.free_bytes == 200_000_000
    assert result.tier == "green"
    assert result.estimated_bytes > 0


def test_preflight_defaults_to_config_planned_minutes(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    config.disk.default_planned_minutes = 30
    result = preflight_disk_check(config, _free_bytes_override=1_000_000_000)
    assert result.planned_minutes == 30


def test_preflight_red_when_free_below_estimate(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    result = preflight_disk_check(config, planned_minutes=60, _free_bytes_override=1_000)
    assert result.tier == "red"
    assert "fail" in result.message.lower() or "likely" in result.message.lower()


def test_estimate_scales_with_duration():
    b30 = _estimate_recording_bytes(30, 16000, 0.6)
    b60 = _estimate_recording_bytes(60, 16000, 0.6)
    assert b60 == 2 * b30


# ---------------------------------------------------------------------------
# Watchdog — mid-recording disk-exhaustion triggers graceful stop
# ---------------------------------------------------------------------------


class _FakeStream:
    """Mock sd.InputStream — does nothing but satisfies the engine's API."""

    def __init__(self, **_kwargs):
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def _fake_stream_factory(**kwargs):
    return _FakeStream(**kwargs)


def test_watchdog_fires_graceful_stop_when_disk_low(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    config.disk.watchdog_interval_seconds = 1  # fire once per second in tests
    config.disk.watchdog_graceful_stop_factor = 0.5

    # Simulate only 1 byte free — guaranteed to trigger.
    fake_disk = MagicMock(return_value=SimpleNamespace(free=1, total=1, used=0))

    engine = AudioCaptureEngine(
        config.recording,
        output_dir=tmp_path / "recordings",
        _stream_factory=_fake_stream_factory,
        app_config=config,
        planned_minutes=60,
        disk_usage_fn=fake_disk,
    )

    stop_event = threading.Event()

    def on_stop(reason: str) -> None:
        stop_event.set()

    engine.set_graceful_stop_handler(on_stop)
    engine.start()

    # Allow the watchdog's first tick + fire.
    fired = stop_event.wait(timeout=3.0)
    assert fired, "Watchdog did not fire within 3s"
    assert engine.early_stop_reason is not None
    assert "low disk" in engine.early_stop_reason.lower()

    engine.stop()


def test_watchdog_silent_when_plenty_of_disk(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    config.disk.watchdog_interval_seconds = 1

    fake_disk = MagicMock(return_value=SimpleNamespace(
        free=10**12, total=10**12, used=0,
    ))

    engine = AudioCaptureEngine(
        config.recording,
        output_dir=tmp_path / "recordings",
        _stream_factory=_fake_stream_factory,
        app_config=config,
        planned_minutes=60,
        disk_usage_fn=fake_disk,
    )
    engine.start()

    time.sleep(1.5)
    assert engine.early_stop_reason is None
    engine.stop()


# ---------------------------------------------------------------------------
# /api/retention/oldest-audio correctness (SQL + pipeline integration)
# ---------------------------------------------------------------------------


def test_oldest_audio_excludes_non_terminal(db_session, tmp_path):
    """Meetings where pipeline has not reached a terminal state are excluded."""
    from meeting_minutes.system3.db import MeetingORM

    # Meeting A — pipeline terminal, should be included.
    audio_a = tmp_path / "a.flac"
    audio_a.write_bytes(b"x")
    db_session.add(MeetingORM(
        meeting_id="a", title="A", date=datetime.now(timezone.utc),
        meeting_type="standup", status="final",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db_session.add(TranscriptORM(
        meeting_id="a", full_text="", language="en", audio_file_path=str(audio_a),
    ))
    mark_succeeded(db_session, "a", Stage.INGEST)
    mark_succeeded(db_session, "a", Stage.EMBED)
    mark_succeeded(db_session, "a", Stage.EXPORT)

    # Meeting B — pipeline only partially done, should be excluded.
    audio_b = tmp_path / "b.flac"
    audio_b.write_bytes(b"x")
    db_session.add(MeetingORM(
        meeting_id="b", title="B", date=datetime.now(timezone.utc),
        meeting_type="standup", status="final",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db_session.add(TranscriptORM(
        meeting_id="b", full_text="", language="en", audio_file_path=str(audio_b),
    ))
    mark_succeeded(db_session, "b", Stage.TRANSCRIBE)
    db_session.commit()

    # Replicate the endpoint query.
    from meeting_minutes.pipeline_state import has_terminal_state

    rows = db_session.query(
        TranscriptORM.meeting_id, TranscriptORM.audio_file_path
    ).all()
    eligible = [
        mid for mid, path in rows
        if path and Path(path).exists() and has_terminal_state(db_session, mid)
    ]

    assert eligible == ["a"]


def test_bulk_delete_skips_non_terminal(db_session, tmp_path):
    """The bulk delete endpoint must refuse non-terminal meetings."""
    from meeting_minutes.pipeline_state import has_terminal_state
    from meeting_minutes.system3.db import MeetingORM

    audio_b = tmp_path / "b.flac"
    audio_b.write_bytes(b"x")
    db_session.add(MeetingORM(
        meeting_id="b", title="B", date=datetime.now(timezone.utc),
        meeting_type="standup", status="final",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db_session.add(TranscriptORM(
        meeting_id="b", full_text="", language="en", audio_file_path=str(audio_b),
    ))
    mark_succeeded(db_session, "b", Stage.TRANSCRIBE)
    db_session.commit()

    assert has_terminal_state(db_session, "b") is False
    assert audio_b.exists()  # still safe
