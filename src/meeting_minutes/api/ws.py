"""WebSocket endpoints for live updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from meeting_minutes.api.ws_tokens import consume_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


async def _authenticate_ws(websocket: WebSocket) -> bool:
    """Validate one-time token from query string before accepting (H-1).

    Returns True if the handshake should proceed. On failure closes the
    socket with a policy-violation code and returns False.
    """
    token = websocket.query_params.get("token")
    if not consume_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True


@router.websocket("/ws/recording")
async def ws_recording(websocket: WebSocket):
    """Live recording status updates.

    Pushes JSON messages with the current recording state and active
    pipeline jobs every tick.
    """
    if not await _authenticate_ws(websocket):
        return
    await websocket.accept()
    try:
        while True:
            from meeting_minutes.api.routes.recording import (
                _current_recording,
                _pipeline_jobs,
            )

            state = _current_recording["state"]
            meeting_id = _current_recording["meeting_id"]
            start_time = _current_recording["start_time"]
            engine = _current_recording.get("engine")

            elapsed = None
            audio_level = 0.0
            if state == "recording" and start_time:
                elapsed = round(time.time() - start_time, 1)
                if engine and hasattr(engine, "get_audio_level"):
                    audio_level = round(engine.get_audio_level(), 3)

            # Build pipelines list
            now = time.time()
            pipelines = []
            for mid, job in _pipeline_jobs.items():
                pipelines.append({
                    "meeting_id": mid,
                    "step": job["step"],
                    "progress": job["progress"],
                    "error": job.get("error"),
                    "started_at": job["started_at"],
                    "elapsed_seconds": round(now - job["started_at"], 1),
                })

            payload = {
                "recording": {
                    "state": state,
                    "meeting_id": meeting_id,
                    "elapsed_seconds": elapsed,
                    "audio_level": audio_level,
                },
                "pipelines": pipelines,
            }
            await websocket.send_text(json.dumps(payload))

            # Faster updates during recording (for audio level), slower otherwise
            await asyncio.sleep(0.2 if state == "recording" else 1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket /ws/recording error: %s", exc)
        try:
            await websocket.close()
        except Exception as close_exc:
            logger.warning("Failed to close WebSocket /ws/recording: %s", close_exc)


@router.websocket("/ws/pipeline/{meeting_id}")
async def ws_pipeline(websocket: WebSocket, meeting_id: str):
    """Pipeline progress updates for a specific meeting."""
    if not await _authenticate_ws(websocket):
        return
    await websocket.accept()
    try:
        while True:
            from meeting_minutes.api.routes.recording import _pipeline_jobs

            job = _pipeline_jobs.get(meeting_id)
            if job:
                now = time.time()
                await websocket.send_text(json.dumps({
                    "meeting_id": meeting_id,
                    "step": job["step"],
                    "progress": job["progress"],
                    "error": job.get("error"),
                    "elapsed_seconds": round(now - job["started_at"], 1),
                }))
                if job["step"] in ("done", "error"):
                    break
            else:
                # Job not found — either not started or already cleaned up
                await websocket.send_text(json.dumps({
                    "meeting_id": meeting_id,
                    "step": "done",
                    "progress": 1.0,
                }))
                break

            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket /ws/pipeline/%s error: %s", meeting_id, exc)
        try:
            await websocket.close()
        except Exception as close_exc:
            logger.warning("Failed to close WebSocket /ws/pipeline/%s: %s", meeting_id, close_exc)
