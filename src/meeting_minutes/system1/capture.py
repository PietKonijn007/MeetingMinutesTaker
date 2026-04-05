"""Audio capture engine using sounddevice and soundfile."""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from meeting_minutes.config import RecordingConfig
from meeting_minutes.models import AudioRecordingResult

logger = logging.getLogger(__name__)


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


def auto_select_capture_device() -> str | None:
    """Auto-detect the best capture device for meeting recording.

    Priority:
    1. MeetingCapture aggregate devices (contain BlackHole for loopback)
       — prefer the one whose non-BlackHole sub-device is currently online
    2. Any aggregate/multi-channel device with BlackHole in it
    3. System default input device

    Returns the device name, or None if no suitable device found.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        candidates = []

        for i, d in enumerate(devices):
            if d["max_input_channels"] <= 0:
                continue
            name = d["name"]
            channels = d["max_input_channels"]

            # Score: higher is better
            score = 0

            # Prefer devices with "MeetingCapture" or "Meeting Capture" in the name
            name_lower = name.lower()
            if "meetingcapture" in name_lower or "meeting capture" in name_lower:
                score += 100

            # Prefer aggregate/multi-channel devices (likely have BlackHole)
            if channels >= 2:
                score += 10

            # Prefer devices with "blackhole" in name (direct BlackHole input)
            if "blackhole" in name_lower:
                score += 5

            # Penalize raw hardware devices (no loopback)
            if channels == 1 and score < 100:
                score -= 5

            # Test if the device is actually functional by checking if it can be opened
            # (offline aggregate sub-devices cause open errors)
            if score >= 100:
                try:
                    test_stream = sd.InputStream(
                        device=i, channels=1, samplerate=d["default_samplerate"],
                        dtype="float32", blocksize=256,
                    )
                    test_stream.start()
                    test_stream.stop()
                    test_stream.close()
                    score += 50  # Device is online and functional
                except Exception:
                    score -= 200  # Device has offline sub-devices, skip it

            candidates.append((score, name, i))

        candidates.sort(key=lambda x: x[0], reverse=True)

        if candidates:
            best_score, best_name, best_idx = candidates[0]
            if best_score > 0:
                logger.info("Audio auto-selected: %s (score=%d)", best_name, best_score)
                return best_name

        # Fallback: system default
        try:
            default_info = sd.query_devices(None, kind="input")
            logger.info("Audio auto-selected default: %s", default_info['name'])
            return default_info["name"]
        except Exception as exc:
            logger.warning("Failed to query default audio device: %s", exc)
            return None

    except Exception as e:
        logger.warning("Audio auto-detect failed: %s", e)
        return None


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
        self._frames_lock = threading.Lock()  # protects _frames_list
        self._frames_list: list = []
        self._actual_sample_rate: int = config.sample_rate
        self._autosave_timer: threading.Timer | None = None
        self._autosave_interval = 300  # 5 minutes

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
        self._start_autosave()
        return self._meeting_id

    def _start_stream(self) -> None:
        """Start the audio input stream (real or injected mock)."""
        import numpy as np  # lazy import

        def _callback(indata, frames, time, status):
            if status:
                logger.warning("Audio stream status: %s", status)
            if self._recording:
                data = indata.copy()
                buf = self._buffer
                if buf is not None:
                    buf.write(data)
                with self._frames_lock:
                    self._frames_list.append(data)

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

                if self._config.audio_device == "auto":
                    # Auto-detect the best capture device
                    auto_device = auto_select_capture_device()
                    device = auto_device  # can be name or None (system default)
                else:
                    device = self._config.audio_device

                # Query native sample rate and channel count
                try:
                    device_info = sd.query_devices(device, kind="input")
                    native_rate = int(device_info["default_samplerate"])
                    max_channels = int(device_info["max_input_channels"])
                    use_rate = native_rate
                    use_channels = max_channels if max_channels > 0 else 1
                    self._actual_sample_rate = use_rate
                    logger.info("Audio device: %s, rate: %dHz, channels: %d", device_info['name'], native_rate, use_channels)
                except Exception:
                    use_rate = self._config.sample_rate
                    use_channels = 1
                    self._actual_sample_rate = use_rate

                # Wrap the callback to mix multi-channel to mono
                if use_channels > 1:
                    orig_callback = _callback
                    def _callback(indata, frames, time, status):
                        mono = indata.mean(axis=1, keepdims=True)
                        orig_callback(mono, frames, time, status)

                # Try opening the stream with device fallback on macOS errors
                try:
                    self._stream = sd.InputStream(
                        samplerate=use_rate,
                        channels=use_channels,
                        dtype="float32",
                        device=device,
                        callback=_callback,
                        blocksize=1024,
                    )
                    self._stream.start()
                except Exception as stream_exc:
                    # Fallback: try default device if specified device fails
                    exc_str = str(stream_exc)
                    if device is not None and ("Invalid" in exc_str or "!obj" in exc_str or "PortAudio" in exc_str):
                        logger.warning("Audio device error: %s. Trying default device...", stream_exc)
                        try:
                            default_info = sd.query_devices(None, kind="input")
                            use_rate = int(default_info["default_samplerate"])
                            use_channels = int(default_info["max_input_channels"])
                            self._actual_sample_rate = use_rate
                            logger.info("Audio fallback: %s, rate: %dHz, channels: %d", default_info['name'], use_rate, use_channels)
                        except Exception:
                            use_rate = self._config.sample_rate
                            use_channels = 1
                            self._actual_sample_rate = use_rate

                        # Re-create mono mix callback for new channel count
                        if use_channels > 1:
                            base_cb = orig_callback if 'orig_callback' in dir() else _callback
                            def _callback(indata, frames, time, status):
                                mono = indata.mean(axis=1, keepdims=True)
                                base_cb(mono, frames, time, status)

                        self._stream = sd.InputStream(
                            samplerate=use_rate,
                            channels=use_channels,
                            dtype="float32",
                            callback=_callback,
                            blocksize=1024,
                        )
                        self._stream.start()
                    else:
                        raise
            except Exception as exc:
                self._recording = False
                raise RuntimeError(f"Failed to open audio stream: {exc}") from exc

    def _start_autosave(self) -> None:
        """Start periodic auto-save to prevent data loss on crash."""
        def _autosave():
            if not self._recording:
                return
            self._do_autosave()
            # Schedule next auto-save
            self._autosave_timer = threading.Timer(self._autosave_interval, _autosave)
            self._autosave_timer.daemon = True
            self._autosave_timer.start()

        self._autosave_timer = threading.Timer(self._autosave_interval, _autosave)
        self._autosave_timer.daemon = True
        self._autosave_timer.start()

    def _stop_autosave(self) -> None:
        """Cancel the auto-save timer."""
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
            self._autosave_timer = None

    def _do_autosave(self) -> None:
        """Save current audio data to a temporary recovery file."""
        try:
            import numpy as np
            import soundfile as sf

            with self._frames_lock:
                if not self._frames_list:
                    return
                audio_data = np.concatenate(self._frames_list, axis=0)

            sample_rate = self._actual_sample_rate
            autosave_file = self._output_dir / f"{self._meeting_id}_autosave.flac"
            sf.write(str(autosave_file), audio_data, sample_rate)
            duration = len(audio_data) / sample_rate
            logger.info("Audio auto-saved %.0fs to %s", duration, autosave_file.name)
        except Exception as e:
            logger.warning("Audio auto-save failed: %s", e)

    def stop(self) -> AudioRecordingResult:
        """Stop recording. Write FLAC file. Return metadata."""
        import time as _time

        with self._lock:
            if not self._recording:
                raise RuntimeError("Not recording")
            self._recording = False  # callback checks this flag first

        self._stop_autosave()
        end_time = datetime.now(timezone.utc)
        logger.info("Stopping audio stream...")

        # 1. Stop the stream using stop() (not abort()) to let the callback
        #    finish its current invocation, then close. This is safer than
        #    abort() which can corrupt state mid-callback.
        if self._stream is not None:
            try:
                if hasattr(self._stream, "stop"):
                    self._stream.stop()
                if hasattr(self._stream, "close"):
                    self._stream.close()
            except Exception as exc:
                logger.warning("Error closing audio stream: %s", exc)
            self._stream = None

        # 2. Small delay to ensure the callback thread has fully exited.
        #    PortAudio's stop() waits for the current callback to finish,
        #    but we add a tiny margin for safety. Skip for mock streams (tests).
        if not self._stream_factory:
            _time.sleep(0.05)

        logger.info("Audio stream closed. Writing audio file...")

        # 3. Now safe to access _frames_list — callback is stopped.
        #    Take the lock to be absolutely sure no straggler callback runs.
        import numpy as np  # lazy import

        sample_rate = self._actual_sample_rate

        with self._frames_lock:
            if self._frames_list:
                audio_data = np.concatenate(self._frames_list, axis=0)
            else:
                audio_data = np.zeros((sample_rate, 1), dtype=np.float32)
            # Free memory
            self._frames_list = []

        self._buffer = None

        logger.info("Audio: %d samples, %.1fs at %dHz", len(audio_data), len(audio_data)/sample_rate, sample_rate)

        audio_file = self._output_dir / f"{self._meeting_id}.flac"
        try:
            import soundfile as sf  # lazy import

            sf.write(str(audio_file), audio_data, sample_rate)
        except Exception as exc:
            raise RuntimeError(f"Failed to write audio file: {exc}") from exc

        logger.info("Audio saved: %s (%.0f KB)", audio_file.name, audio_file.stat().st_size / 1024)

        # Clean up autosave file now that the real file is saved
        autosave_file = self._output_dir / f"{self._meeting_id}_autosave.flac"
        if autosave_file.exists():
            autosave_file.unlink()

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

        if not self._recording:
            return 0.0
        try:
            # Use the last few chunks for responsive but stable level
            with self._frames_lock:
                if not self._frames_list:
                    return 0.0
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
