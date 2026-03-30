"""Audio capture engine using sounddevice and soundfile."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from meeting_minutes.config import RecordingConfig
from meeting_minutes.models import AudioRecordingResult


class CircularAudioBuffer:
    """Thread-safe circular buffer that retains the most recent N samples."""

    def __init__(self, capacity_frames: int, channels: int = 1) -> None:
        self._capacity = capacity_frames
        self._channels = channels
        self._buffer: deque = deque()
        self._lock = threading.Lock()
        self._total_frames: int = 0

    @property
    def total_frames(self) -> int:
        return self._total_frames

    def write(self, frames) -> None:  # frames: np.ndarray shape (N, channels)
        import numpy as np
        with self._lock:
            self._buffer.append(frames)
            self._total_frames += len(frames)
            # Trim to capacity: remove oldest chunks until within capacity
            current = sum(len(b) for b in self._buffer)
            while current > self._capacity and self._buffer:
                oldest = self._buffer[0]
                excess = current - self._capacity
                if len(oldest) <= excess:
                    # Remove whole chunk
                    self._buffer.popleft()
                    current -= len(oldest)
                else:
                    # Trim start of oldest chunk
                    self._buffer[0] = oldest[excess:]
                    current -= excess

    def read_all(self):
        """Return concatenated frames as a single numpy array."""
        import numpy as np  # lazy import

        with self._lock:
            if not self._buffer:
                return np.zeros((0, self._channels), dtype=np.float32)
            return np.concatenate(list(self._buffer), axis=0)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._total_frames = 0


class AudioCaptureEngine:
    """Record audio from configured audio device and save to FLAC."""

    def __init__(
        self,
        config: RecordingConfig,
        output_dir: Path | None = None,
        _stream_factory: Callable | None = None,
    ) -> None:
        self._config = config
        self._output_dir = output_dir or Path("recordings")
        self._stream_factory = _stream_factory  # injected for testing
        self._meeting_id: str | None = None
        self._recording = False
        self._start_time: datetime | None = None
        self._buffer: CircularAudioBuffer | None = None
        self._stream = None
        self._lock = threading.Lock()
        self._frames_list: list = []

    def start(self) -> str:
        """Begin recording. Returns meeting_id."""
        with self._lock:
            if self._recording:
                raise RuntimeError("Already recording")

            self._meeting_id = str(uuid.uuid4())
            self._start_time = datetime.now(timezone.utc)
            self._frames_list = []
            # 10-minute circular buffer
            capacity = self._config.sample_rate * 60 * 10
            self._buffer = CircularAudioBuffer(capacity)
            self._recording = True

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._start_stream()
        return self._meeting_id

    def _start_stream(self) -> None:
        """Start the audio input stream (real or injected mock)."""
        import numpy as np  # lazy import

        def _callback(indata, frames, time, status):
            if self._recording:
                self._buffer.write(indata.copy())
                self._frames_list.append(indata.copy())

        if self._stream_factory is not None:
            self._stream = self._stream_factory(
                samplerate=self._config.sample_rate,
                channels=1,
                dtype="float32",
                callback=_callback,
            )
        else:
            try:
                import sounddevice as sd  # lazy import

                device = (
                    None
                    if self._config.audio_device == "auto"
                    else self._config.audio_device
                )
                self._stream = sd.InputStream(
                    samplerate=self._config.sample_rate,
                    channels=1,
                    dtype="float32",
                    device=device,
                    callback=_callback,
                )
                self._stream.start()
            except Exception as exc:
                self._recording = False
                raise RuntimeError(f"Failed to open audio stream: {exc}") from exc

    def stop(self) -> AudioRecordingResult:
        """Stop recording. Write FLAC file. Return metadata."""
        with self._lock:
            if not self._recording:
                raise RuntimeError("Not recording")
            self._recording = False

        end_time = datetime.now(timezone.utc)

        # Close stream
        if self._stream is not None:
            try:
                if hasattr(self._stream, "stop"):
                    self._stream.stop()
                if hasattr(self._stream, "close"):
                    self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Write FLAC
        import numpy as np  # lazy import

        if self._frames_list:
            audio_data = np.concatenate(self._frames_list, axis=0)
        else:
            audio_data = np.zeros((self._config.sample_rate, 1), dtype=np.float32)

        audio_file = self._output_dir / f"{self._meeting_id}.flac"
        try:
            import soundfile as sf  # lazy import

            sf.write(str(audio_file), audio_data, self._config.sample_rate)
        except Exception as exc:
            raise RuntimeError(f"Failed to write audio file: {exc}") from exc

        duration = (end_time - self._start_time).total_seconds()

        device_name = (
            self._config.audio_device
            if self._config.audio_device != "auto"
            else "default"
        )

        return AudioRecordingResult(
            meeting_id=self._meeting_id,
            audio_file=str(audio_file),
            start_time=self._start_time,
            end_time=end_time,
            duration_seconds=duration,
            sample_rate=self._config.sample_rate,
            recording_device=device_name,
            format=self._config.format,
        )

    def is_recording(self) -> bool:
        return self._recording

    def get_audio_level(self) -> float:
        """Return the current RMS audio level (0.0–1.0) from recent frames."""
        import numpy as np

        if not self._recording or not self._frames_list:
            return 0.0
        try:
            # Use the last chunk of audio data for a responsive level
            recent = self._frames_list[-1] if self._frames_list else np.zeros(1)
            rms = float(np.sqrt(np.mean(recent ** 2)))
            # Clamp to 0–1, amplify for visibility (raw RMS is often very low)
            return min(1.0, rms * 10.0)
        except Exception:
            return 0.0
