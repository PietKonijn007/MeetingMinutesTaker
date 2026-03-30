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
    RecordingStartRequest,
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
    body: RecordingStartRequest | None = None,
):
    """Start recording audio. Optionally override audio device and language."""
    if _recording_state["state"] == "recording" and _recording_state.get("engine") and _recording_state["engine"].is_recording():
        raise HTTPException(status_code=409, detail="Already recording")

    from meeting_minutes.config import RecordingConfig
    from meeting_minutes.system1.capture import AudioCaptureEngine

    # Override config with request values if provided
    rec_config = config.recording.model_copy()
    if body and body.audio_device:
        rec_config.audio_device = body.audio_device

    # Store the language override for the pipeline (transcription step)
    language = None
    if body and body.language:
        language = body.language

    data_dir = Path(config.data_dir).expanduser()
    recordings_dir = data_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    engine = AudioCaptureEngine(rec_config, output_dir=recordings_dir)
    try:
        meeting_id = engine.start()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {exc}")

    _recording_state["state"] = "recording"
    _recording_state["meeting_id"] = meeting_id
    _recording_state["start_time"] = time.time()
    _recording_state["engine"] = engine
    _recording_state["language"] = language

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
    """Get current recording state including live audio level."""
    # Determine the real state by checking the engine
    engine = _recording_state.get("engine")
    actual_state = _recording_state["state"]

    # If we think we're recording, verify the engine agrees
    if actual_state == "recording" and engine and not engine.is_recording():
        actual_state = "idle"

    elapsed = None
    audio_level = 0.0
    if actual_state == "recording" and _recording_state["start_time"]:
        elapsed = time.time() - _recording_state["start_time"]
        if engine and hasattr(engine, "get_audio_level"):
            audio_level = engine.get_audio_level()

    return RecordingStatusResponse(
        state=actual_state,
        meeting_id=_recording_state["meeting_id"],
        elapsed_seconds=round(elapsed, 1) if elapsed else None,
        audio_level=round(audio_level, 3),
    )


@router.get("/api/languages")
def list_languages():
    """List supported transcription languages."""
    return [
        {"code": "auto", "name": "Auto-detect"},
        {"code": "en", "name": "English"},
        {"code": "nl", "name": "Dutch"},
        {"code": "fr", "name": "French"},
        {"code": "de", "name": "German"},
        {"code": "es", "name": "Spanish"},
        {"code": "it", "name": "Italian"},
        {"code": "pt", "name": "Portuguese"},
        {"code": "ja", "name": "Japanese"},
        {"code": "zh", "name": "Chinese"},
        {"code": "ko", "name": "Korean"},
        {"code": "ru", "name": "Russian"},
        {"code": "ar", "name": "Arabic"},
        {"code": "hi", "name": "Hindi"},
        {"code": "sv", "name": "Swedish"},
        {"code": "da", "name": "Danish"},
        {"code": "no", "name": "Norwegian"},
        {"code": "fi", "name": "Finnish"},
        {"code": "pl", "name": "Polish"},
        {"code": "tr", "name": "Turkish"},
        {"code": "uk", "name": "Ukrainian"},
        {"code": "cs", "name": "Czech"},
        {"code": "el", "name": "Greek"},
        {"code": "he", "name": "Hebrew"},
        {"code": "th", "name": "Thai"},
        {"code": "vi", "name": "Vietnamese"},
        {"code": "id", "name": "Indonesian"},
    ]


@router.get("/api/audio-devices", response_model=list[AudioDeviceResponse])
def list_audio_devices():
    """List all available audio devices (input, output, and bidirectional)."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            inp = d["max_input_channels"]
            out = d["max_output_channels"]
            if inp == 0 and out == 0:
                continue
            if inp > 0 and out > 0:
                dev_type = "input/output"
            elif inp > 0:
                dev_type = "input"
            else:
                dev_type = "output"
            result.append(
                AudioDeviceResponse(
                    index=i,
                    name=d["name"],
                    max_input_channels=inp,
                    max_output_channels=out,
                    default_sample_rate=d["default_samplerate"],
                    type=dev_type,
                )
            )
        return result
    except Exception:
        # sounddevice may not be available in all environments
        return []
