"""Recording control endpoints."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from meeting_minutes.api.deps import get_config
from meeting_minutes.api.schemas import (
    AudioDeviceResponse,
    RecordingStartResponse,
    RecordingStatusResponse,
)
from meeting_minutes.config import AppConfig

router = APIRouter(tags=["recording"])

# Simple in-process state for recording.  A production system would use a
# background task or separate process, but for the single-user MVP this is
# sufficient.
_recording_state: dict = {
    "state": "idle",
    "meeting_id": None,
    "start_time": None,
    "engine": None,
}


@router.post("/api/recording/start", response_model=RecordingStartResponse)
def start_recording(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Start recording audio."""
    if _recording_state["state"] == "recording":
        raise HTTPException(status_code=409, detail="Already recording")

    from meeting_minutes.system1.capture import AudioCaptureEngine

    data_dir = Path(config.data_dir).expanduser()
    recordings_dir = data_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    engine = AudioCaptureEngine(config.recording, output_dir=recordings_dir)
    try:
        meeting_id = engine.start()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {exc}")

    _recording_state["state"] = "recording"
    _recording_state["meeting_id"] = meeting_id
    _recording_state["start_time"] = time.time()
    _recording_state["engine"] = engine

    # Also write state file for CLI interop
    state_file = Path("/tmp/mm_recording_state.json")
    state_file.write_text(json.dumps({"meeting_id": meeting_id}))

    return RecordingStartResponse(meeting_id=meeting_id, status="recording")


@router.post("/api/recording/stop")
async def stop_recording(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Stop recording and trigger the pipeline."""
    if _recording_state["state"] != "recording":
        raise HTTPException(status_code=409, detail="Not currently recording")

    engine = _recording_state["engine"]
    if engine is None:
        raise HTTPException(status_code=500, detail="Recording engine not available")

    meeting_id = _recording_state["meeting_id"]

    try:
        result = engine.stop()
    except Exception as exc:
        _recording_state["state"] = "idle"
        _recording_state["engine"] = None
        raise HTTPException(status_code=500, detail=f"Failed to stop recording: {exc}")

    _recording_state["state"] = "processing"
    _recording_state["engine"] = None

    # Clean up state file
    state_file = Path("/tmp/mm_recording_state.json")
    state_file.unlink(missing_ok=True)

    # Trigger pipeline in the background (don't block the response)
    import asyncio

    from meeting_minutes.pipeline import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(config)

    async def _run_pipeline():
        try:
            await orchestrator.run_full_pipeline(meeting_id)
        except Exception:
            pass
        finally:
            _recording_state["state"] = "idle"
            _recording_state["meeting_id"] = None
            _recording_state["start_time"] = None

    asyncio.create_task(_run_pipeline())

    return {
        "status": "processing",
        "meeting_id": meeting_id,
        "audio_file": result.audio_file,
        "duration_seconds": result.duration_seconds,
    }


@router.get("/api/recording/status", response_model=RecordingStatusResponse)
def recording_status():
    """Get current recording state."""
    elapsed = None
    if _recording_state["state"] == "recording" and _recording_state["start_time"]:
        elapsed = time.time() - _recording_state["start_time"]

    return RecordingStatusResponse(
        state=_recording_state["state"],
        meeting_id=_recording_state["meeting_id"],
        elapsed_seconds=round(elapsed, 1) if elapsed else None,
    )


@router.get("/api/audio-devices", response_model=list[AudioDeviceResponse])
def list_audio_devices():
    """List available audio input devices."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                result.append(
                    AudioDeviceResponse(
                        index=i,
                        name=d["name"],
                        max_input_channels=d["max_input_channels"],
                        default_sample_rate=d["default_samplerate"],
                    )
                )
        return result
    except Exception:
        # sounddevice may not be available in all environments
        return []
