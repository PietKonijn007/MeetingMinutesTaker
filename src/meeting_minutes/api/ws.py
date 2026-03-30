"""WebSocket endpoints for live updates."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/recording")
async def ws_recording(websocket: WebSocket):
    """Live recording status updates.

    Pushes JSON messages with the current recording state every second
    while a recording is active.
    """
    await websocket.accept()
    try:
        while True:
            from meeting_minutes.api.routes.recording import _recording_state

            state = _recording_state["state"]
            meeting_id = _recording_state["meeting_id"]
            start_time = _recording_state["start_time"]
            engine = _recording_state.get("engine")

            elapsed = None
            audio_level = 0.0
            if state == "recording" and start_time:
                elapsed = round(time.time() - start_time, 1)
                if engine and hasattr(engine, "get_audio_level"):
                    audio_level = round(engine.get_audio_level(), 3)

            payload = {
                "state": state,
                "meeting_id": meeting_id,
                "elapsed_seconds": elapsed,
                "audio_level": audio_level,
                "step": _recording_state.get("pipeline_step"),
                "progress": _recording_state.get("pipeline_progress", 0.0),
            }
            await websocket.send_text(json.dumps(payload))

            # Faster updates during recording (for audio level), slower otherwise
            await asyncio.sleep(0.2 if state == "recording" else 1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/pipeline/{meeting_id}")
async def ws_pipeline(websocket: WebSocket, meeting_id: str):
    """Pipeline progress updates for a specific meeting.

    This is a placeholder that reports status.  A full implementation would
    hook into the PipelineOrchestrator progress callbacks.
    """
    await websocket.accept()
    try:
        # Send initial status
        await websocket.send_text(json.dumps({
            "meeting_id": meeting_id,
            "step": "waiting",
            "progress": 0.0,
        }))

        # Poll until client disconnects
        while True:
            from meeting_minutes.api.routes.recording import _recording_state

            state = _recording_state["state"]
            current_meeting = _recording_state["meeting_id"]

            if current_meeting == meeting_id:
                if state == "processing":
                    await websocket.send_text(json.dumps({
                        "meeting_id": meeting_id,
                        "step": "processing",
                        "progress": 0.5,
                    }))
                elif state == "idle":
                    await websocket.send_text(json.dumps({
                        "meeting_id": meeting_id,
                        "step": "done",
                        "progress": 1.0,
                    }))
                    break
            else:
                # Not the current recording — may already be done
                await websocket.send_text(json.dumps({
                    "meeting_id": meeting_id,
                    "step": "done",
                    "progress": 1.0,
                }))
                break

            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
