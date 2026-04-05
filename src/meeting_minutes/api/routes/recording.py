"""Recording control endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from meeting_minutes.api.deps import get_config
from meeting_minutes.api.schemas import (
    AudioDeviceResponse,
    PipelineJobStatus,
    RecordingStartRequest,
    RecordingStartResponse,
    RecordingStatusResponse,
)
from meeting_minutes.config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recording"])

# ---------------------------------------------------------------------------
# State: one active recording, many concurrent pipeline jobs
# ---------------------------------------------------------------------------

# Lock to prevent PortAudio device re-scan from racing with stream operations
_audio_lock = threading.Lock()

# Current recording (only one at a time)
_current_recording: dict = {
    "state": "idle",        # "idle" | "recording"
    "meeting_id": None,
    "start_time": None,
    "engine": None,
    "language": None,
}

# Background pipeline jobs (many can be tracked, but run sequentially via queue)
# { meeting_id: { "step": "transcribing", "progress": 0.5, "error": None, "started_at": time.time() } }
_pipeline_jobs: dict[str, dict] = {}

# Pipeline queue — ensures only one heavy pipeline runs at a time
# (Whisper large-v3 uses ~10GB RAM, two concurrent would thrash)
_pipeline_queue: asyncio.Queue = asyncio.Queue()
_pipeline_worker_started = False


@router.post("/api/recording/start", response_model=RecordingStartResponse)
def start_recording(
    config: Annotated[AppConfig, Depends(get_config)],
    body: RecordingStartRequest = RecordingStartRequest(),
):
    """Start recording audio. Optionally override audio device and language."""
    try:
        # Only check if we're currently recording — don't care about pipeline jobs
        if _current_recording["state"] == "recording":
            eng = _current_recording.get("engine")
            if eng and eng.is_recording():
                raise HTTPException(status_code=409, detail="Already recording")

        from meeting_minutes.system1.capture import AudioCaptureEngine

        # Override config with request values if provided
        rec_config = config.recording.model_copy()
        if body.audio_device:
            rec_config.audio_device = body.audio_device

        language = body.language if body.language else None

        data_dir = Path(config.data_dir).expanduser()
        recordings_dir = data_dir / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Starting recording — device: %s", rec_config.audio_device)

        engine = AudioCaptureEngine(rec_config, output_dir=recordings_dir)
        meeting_id = engine.start()

        _current_recording["state"] = "recording"
        _current_recording["meeting_id"] = meeting_id
        _current_recording["start_time"] = time.time()
        _current_recording["engine"] = engine
        _current_recording["language"] = language

        # Also write state file for CLI interop
        state_file = Path("/tmp/mm_recording_state.json")
        state_file.write_text(json.dumps({"meeting_id": meeting_id}))

        logger.info("Recording started — meeting: %s", meeting_id)
        return RecordingStartResponse(meeting_id=meeting_id, status="recording")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Recording start error: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/recording/stop")
async def stop_recording(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Stop recording and trigger the pipeline."""
    if _current_recording["state"] != "recording":
        raise HTTPException(status_code=409, detail="Not currently recording")

    engine = _current_recording["engine"]
    if engine is None:
        raise HTTPException(status_code=500, detail="Recording engine not available")

    meeting_id = _current_recording["meeting_id"]

    try:
        # Run stop (which writes the FLAC file) in a thread to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, engine.stop)
    except Exception as exc:
        _current_recording["state"] = "idle"
        _current_recording["engine"] = None
        raise HTTPException(status_code=500, detail=f"Failed to stop recording: {exc}")

    # Reset recording state immediately so a new recording can start
    _current_recording["state"] = "idle"
    _current_recording["meeting_id"] = None
    _current_recording["start_time"] = None
    _current_recording["engine"] = None
    _current_recording["language"] = None

    # Clean up state file
    state_file = Path("/tmp/mm_recording_state.json")
    state_file.unlink(missing_ok=True)

    # Create pipeline job entry — starts as "queued" until the worker picks it up
    _pipeline_jobs[meeting_id] = {
        "step": "queued",
        "progress": 0.0,
        "error": None,
        "started_at": time.time(),
    }

    # Enqueue pipeline job (runs sequentially to avoid memory thrashing)
    _pipeline_queue.put_nowait((meeting_id, config))
    _ensure_pipeline_worker()

    return {
        "status": "processing",
        "meeting_id": meeting_id,
        "audio_file": result.audio_file,
        "duration_seconds": result.duration_seconds,
    }


def _ensure_pipeline_worker():
    """Start the pipeline worker if not already running."""
    global _pipeline_worker_started
    if not _pipeline_worker_started:
        _pipeline_worker_started = True
        asyncio.create_task(_pipeline_worker())


async def _pipeline_worker():
    """Process pipeline jobs one at a time from the queue."""
    global _pipeline_worker_started
    try:
        while True:
            meeting_id, config = await asyncio.wait_for(_pipeline_queue.get(), timeout=300)
            await _run_pipeline(meeting_id, config)
            _pipeline_queue.task_done()
    except (asyncio.TimeoutError, asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        _pipeline_worker_started = False


async def _run_pipeline(meeting_id: str, config: AppConfig):
    """Run the full pipeline for a meeting as a background task."""
    job = _pipeline_jobs[meeting_id]
    try:
        from meeting_minutes.pipeline import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(config)

        # Step 1: Transcription
        job["step"] = "transcribing"
        job["progress"] = 0.0
        await orchestrator.run_transcription(meeting_id)
        job["progress"] = 1.0

        # Step 2: Generation
        job["step"] = "generating"
        job["progress"] = 0.0
        await orchestrator.run_generation(meeting_id)
        job["progress"] = 1.0

        # Step 3: Ingestion
        job["step"] = "indexing"
        job["progress"] = 0.0
        await orchestrator.run_ingestion(meeting_id)
        job["progress"] = 1.0

        # Done
        job["step"] = "done"
    except (asyncio.CancelledError, KeyboardInterrupt):
        job["step"] = "error"
        job["error"] = "Cancelled (server shutting down)"
        return
    except Exception as exc:
        job["step"] = "error"
        job["error"] = str(exc)
        logger.error("Pipeline error for meeting %s: %s: %s", meeting_id, type(exc).__name__, exc, exc_info=True)

    # Clean up after 60 seconds so UI can see completion
    try:
        await asyncio.sleep(60)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    _pipeline_jobs.pop(meeting_id, None)


@router.get("/api/recording/status", response_model=RecordingStatusResponse)
def recording_status():
    """Get current recording state including live audio level."""
    engine = _current_recording.get("engine")
    actual_state = _current_recording["state"]

    # If we think we're recording, verify the engine agrees
    if actual_state == "recording" and engine and not engine.is_recording():
        actual_state = "idle"

    elapsed = None
    audio_level = 0.0
    if actual_state == "recording" and _current_recording["start_time"]:
        elapsed = time.time() - _current_recording["start_time"]
        if engine and hasattr(engine, "get_audio_level"):
            audio_level = engine.get_audio_level()

    return RecordingStatusResponse(
        state=actual_state,
        meeting_id=_current_recording["meeting_id"],
        elapsed_seconds=round(elapsed, 1) if elapsed else None,
        audio_level=round(audio_level, 3),
    )


@router.get("/api/pipelines", response_model=list[PipelineJobStatus])
def list_pipelines():
    """Return all active/recent pipeline jobs."""
    now = time.time()
    result = []
    for mid, job in _pipeline_jobs.items():
        result.append(
            PipelineJobStatus(
                meeting_id=mid,
                step=job["step"],
                progress=job["progress"],
                error=job.get("error"),
                started_at=job["started_at"],
                elapsed_seconds=round(now - job["started_at"], 1),
            )
        )
    return result


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


@router.get("/api/auto-detect-device")
def auto_detect_device():
    """Auto-detect the best capture device for meeting recording."""
    from meeting_minutes.system1.capture import auto_select_capture_device
    device = auto_select_capture_device()
    return {"device": device, "auto": True}


@router.get("/api/audio-devices", response_model=list[AudioDeviceResponse])
def list_audio_devices():
    """List all available audio devices (input, output, and bidirectional).

    Forces PortAudio to re-scan devices on each call so newly connected
    Bluetooth/USB devices (AirPods, headsets, etc.) are detected.
    """
    try:
        import sounddevice as sd

        # Force PortAudio to re-scan — but ONLY when not recording.
        # Calling _terminate() during an active recording stream causes
        # a bus error crash (PortAudio internal state corruption).
        if _current_recording["state"] != "recording":
            with _audio_lock:
                sd._terminate()
                sd._initialize()

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
    except Exception as exc:
        # sounddevice may not be available in all environments
        logger.warning("Failed to list audio devices: %s", exc)
        return []
