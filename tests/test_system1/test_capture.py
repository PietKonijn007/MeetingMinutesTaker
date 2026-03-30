"""Tests for audio capture engine."""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from meeting_minutes.config import RecordingConfig
from meeting_minutes.system1.capture import AudioCaptureEngine, CircularAudioBuffer


# ---------------------------------------------------------------------------
# CircularAudioBuffer
# ---------------------------------------------------------------------------

# Feature: meeting-minutes-taker, Property 1: Circular buffer retains most recent samples
@given(
    capacity=st.integers(min_value=10, max_value=1000),
    total_samples=st.integers(min_value=1, max_value=5000),
)
@settings(max_examples=100)
def test_circular_buffer_retains_most_recent(capacity: int, total_samples: int):
    """Property 1: Buffer retains exactly the most recent N samples."""
    buf = CircularAudioBuffer(capacity_frames=capacity, channels=1)

    # Write total_samples in chunks
    chunk_size = 10
    for i in range(0, total_samples, chunk_size):
        batch = np.ones((min(chunk_size, total_samples - i), 1), dtype=np.float32) * i
        buf.write(batch)

    result = buf.read_all()
    assert result.shape[1] == 1
    # Buffer should not exceed capacity
    assert len(result) <= capacity
    # Buffer should contain min(total_samples, capacity) samples
    assert len(result) == min(total_samples, capacity)


def test_circular_buffer_clear():
    """Buffer clears correctly."""
    buf = CircularAudioBuffer(capacity_frames=100)
    buf.write(np.ones((50, 1), dtype=np.float32))
    buf.clear()
    result = buf.read_all()
    assert len(result) == 0


def test_circular_buffer_empty_read():
    """Reading empty buffer returns empty array."""
    buf = CircularAudioBuffer(capacity_frames=100)
    result = buf.read_all()
    assert result.shape == (0, 1)


# ---------------------------------------------------------------------------
# AudioCaptureEngine
# ---------------------------------------------------------------------------

# Feature: meeting-minutes-taker, Property 2: Meeting ID uniqueness
@given(n=st.integers(min_value=2, max_value=10))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_meeting_id_uniqueness(n: int):
    """Property 2: All generated meeting IDs are valid UUIDs and pairwise distinct."""
    config = RecordingConfig(sample_rate=16000)
    ids: list[str] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for _ in range(n):
            engine = AudioCaptureEngine(
                config=config,
                output_dir=Path(tmp_dir),
                _stream_factory=_noop_stream_factory,
            )
            meeting_id = engine.start()
            ids.append(meeting_id)
            engine.stop()

    # All distinct
    assert len(set(ids)) == n
    # All valid UUIDs
    for mid in ids:
        uuid.UUID(mid)  # raises if invalid


# Feature: meeting-minutes-taker, Property 3: Recording metadata completeness
def test_recording_metadata_completeness(tmp_path: Path):
    """Property 3: AudioRecordingResult has all required non-null fields."""
    config = RecordingConfig(sample_rate=16000)
    engine = AudioCaptureEngine(
        config=config,
        output_dir=tmp_path,
        _stream_factory=_noop_stream_factory,
    )
    meeting_id = engine.start()
    time.sleep(0.05)
    result = engine.stop()

    assert result.meeting_id == meeting_id
    assert result.start_time is not None
    assert result.end_time is not None
    assert result.duration_seconds >= 0
    assert result.sample_rate == 16000
    assert result.recording_device is not None
    # Duration roughly matches end - start
    expected_duration = (result.end_time - result.start_time).total_seconds()
    assert abs(result.duration_seconds - expected_duration) < 1.0


def test_start_stop_creates_file(tmp_path: Path):
    """Recording creates a FLAC file at the expected path."""
    config = RecordingConfig(sample_rate=16000)
    engine = AudioCaptureEngine(
        config=config,
        output_dir=tmp_path,
        _stream_factory=_noop_stream_factory,
    )
    meeting_id = engine.start()
    time.sleep(0.05)
    result = engine.stop()

    assert Path(result.audio_file).exists()
    assert result.audio_file.endswith(".flac")


def test_cannot_start_twice(tmp_path: Path):
    """Starting recording twice raises RuntimeError."""
    config = RecordingConfig()
    engine = AudioCaptureEngine(
        config=config,
        output_dir=tmp_path,
        _stream_factory=_noop_stream_factory,
    )
    engine.start()
    with pytest.raises(RuntimeError, match="Already recording"):
        engine.start()
    engine.stop()


def test_cannot_stop_without_start(tmp_path: Path):
    """Stopping without starting raises RuntimeError."""
    config = RecordingConfig()
    engine = AudioCaptureEngine(
        config=config,
        output_dir=tmp_path,
        _stream_factory=_noop_stream_factory,
    )
    with pytest.raises(RuntimeError, match="Not recording"):
        engine.stop()


def test_is_recording_flag(tmp_path: Path):
    """is_recording() returns correct state."""
    config = RecordingConfig()
    engine = AudioCaptureEngine(
        config=config,
        output_dir=tmp_path,
        _stream_factory=_noop_stream_factory,
    )
    assert not engine.is_recording()
    engine.start()
    assert engine.is_recording()
    engine.stop()
    assert not engine.is_recording()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockStream:
    """Minimal mock for sounddevice.InputStream."""

    def __init__(self, **kwargs):
        self._callback = kwargs.get("callback")
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._produce_audio, daemon=True)
        self._thread.start()

    def _produce_audio(self):
        sr = 16000
        chunk = np.zeros((sr // 10, 1), dtype=np.float32)
        while self._running:
            if self._callback:
                self._callback(chunk.copy(), len(chunk), None, None)
            time.sleep(0.1)

    def stop(self):
        self._running = False

    def close(self):
        self._running = False


def _noop_stream_factory(**kwargs):
    """Create a stream that produces silent audio."""
    stream = _MockStream(**kwargs)
    stream.start()
    return stream
