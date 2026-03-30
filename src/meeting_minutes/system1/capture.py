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
        self._actual_sample_rate: int = config.sample_rate

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
            if status:
                import sys
                print(f"  [audio] {status}", file=sys.stderr)
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

                # Query native sample rate — some macOS devices reject non-native rates
                try:
                    device_info = sd.query_devices(device, kind="input")
                    native_rate = int(device_info["default_samplerate"])
                    use_rate = native_rate  # always use native rate for compatibility
                    self._actual_sample_rate = use_rate
                    import sys
                    print(f"  [audio] Device: {device_info['name']}, native rate: {native_rate}Hz, using: {use_rate}Hz", file=sys.stderr)
                except Exception:
                    use_rate = self._config.sample_rate
                    self._actual_sample_rate = use_rate

                self._stream = sd.InputStream(
                    samplerate=use_rate,
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
        import sys

        with self._lock:
            if not self._recording:
                raise RuntimeError("Not recording")
            self._recording = False

        end_time = datetime.now(timezone.utc)
        print(f"  [audio] Stopping stream...", file=sys.stderr)

        # Close stream immediately — don't wait
        if self._stream is not None:
            try:
                if hasattr(self._stream, "abort"):
                    self._stream.abort()  # faster than stop() — drops remaining buffers
                elif hasattr(self._stream, "stop"):
                    self._stream.stop()
                if hasattr(self._stream, "close"):
                    self._stream.close()
            except Exception:
                pass
            self._stream = None

        print(f"  [audio] Stream closed. Writing audio file...", file=sys.stderr)

        # Write FLAC
        import numpy as np  # lazy import

        sample_rate = getattr(self, "_actual_sample_rate", self._config.sample_rate)

        if self._frames_list:
            audio_data = np.concatenate(self._frames_list, axis=0)
        else:
            audio_data = np.zeros((sample_rate, 1), dtype=np.float32)

        # Free memory immediately
        self._frames_list = []
        self._buffer = None

        print(f"  [audio] Audio: {len(audio_data)} samples, {len(audio_data)/sample_rate:.1f}s at {sample_rate}Hz", file=sys.stderr)

        audio_file = self._output_dir / f"{self._meeting_id}.flac"
        try:
            import soundfile as sf  # lazy import

            sf.write(str(audio_file), audio_data, sample_rate)
        except Exception as exc:
            raise RuntimeError(f"Failed to write audio file: {exc}") from exc

        print(f"  [audio] Saved: {audio_file.name} ({audio_file.stat().st_size / 1024:.0f} KB)", file=sys.stderr)

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
            sample_rate=sample_rate,
            recording_device=device_name,
            format=self._config.format,
        )

    def is_recording(self) -> bool:
        return self._recording

    def get_audio_level(self) -> float:
        """Return the current audio level (0.0–1.0) from recent frames.

        Uses peak amplitude (not RMS) for more responsive visualization,
        and applies logarithmic scaling for better perceptual mapping.
        """
        import numpy as np

        if not self._recording or not self._frames_list:
            return 0.0
        try:
            # Use the last few chunks for responsive but stable level
            recent_chunks = self._frames_list[-3:] if len(self._frames_list) >= 3 else self._frames_list[-1:]
            recent = np.concatenate(recent_chunks, axis=0)

            # Peak amplitude is more responsive than RMS for visualization
            peak = float(np.max(np.abs(recent)))

            # Logarithmic scaling: maps typical mic range to 0–1
            # Typical speech: peak 0.001–0.1, quiet room: 0.0001
            if peak < 1e-6:
                return 0.0

            import math
            # Map -60dB to 0dB range onto 0.0–1.0
            db = 20 * math.log10(max(peak, 1e-6))
            # -60dB = 0.0, 0dB = 1.0
            level = max(0.0, min(1.0, (db + 60) / 60))
            return level
        except Exception:
            return 0.0
