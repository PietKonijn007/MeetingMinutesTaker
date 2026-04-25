"""Async post-hoc regeneration of meeting minutes.

Several user-driven flows want to "rebuild the minutes against the latest
sidecar/transcript state": changing the meeting type, renaming speakers,
pasting external notes (handled by ``external_notes.py``). They all share
the same downstream operation — ``PipelineOrchestrator.reprocess`` — so they
can share the same async-status convention.

Design choices (mirrors :mod:`meeting_minutes.external_notes`):
- **Sidecar is the source of truth for status.** The UI polls
  ``GET /meetings/{id}`` to see ``regen_status`` flip from ``processing`` →
  ``ready`` (or ``error``).
- **Fire-and-forget asyncio task.** Reprocess takes 15-60 s of LLM time, far
  too long to hold an HTTP request open. Endpoints return 202 immediately.
- **Single ``regen_status`` field**, not one per trigger. If two flows fire
  back-to-back the second one sees the first one's ``processing`` status
  and refuses with 409 — there's no value in two LLM regenerations racing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from meeting_minutes.config import AppConfig

_LOG = logging.getLogger(__name__)

# Status values written to the notes sidecar under ``regen_status``.
# The UI treats any other value as "no background job running".
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_ERROR = "error"


def set_status(
    data_dir: Path,
    meeting_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Update ``regen_status`` (and optionally ``regen_error``).

    ``error`` is only relevant when ``status == STATUS_ERROR`` — for the other
    two states we clear it so the UI doesn't show a stale message.
    """
    from meeting_minutes import external_notes as ext

    data = ext.load_notes_sidecar(data_dir, meeting_id)
    data["regen_status"] = status
    if status == STATUS_ERROR:
        data["regen_error"] = error or "Unknown error"
    else:
        data.pop("regen_error", None)
    ext.write_notes_sidecar(data_dir, meeting_id, data)


async def run_background_regen(config: AppConfig, meeting_id: str) -> None:
    """Reprocess minutes with the (already-updated) sidecar / transcript.

    Runs as an ``asyncio.create_task``; must never raise. Any failure is
    captured on the notes sidecar as ``regen_status=error`` so the UI can
    surface it.

    Sequence:
      1. Run the standard ``reprocess`` flow. The pipeline reads
         ``meeting_type`` and ``speakers`` from the sidecar at the start of
         ``run_generation``, and segment labels from the transcript JSON, so
         whatever the caller wrote synchronously before scheduling this task
         is automatically picked up.
      2. If the sidecar carries pasted external notes, replay the
         ``## External notes`` post-append so the verbatim paste survives the
         regeneration. Same logic as
         ``external_notes.run_background_update``.
      3. Re-export to Obsidian so the vault copy reflects the latest content.
    """
    from meeting_minutes import external_notes as ext
    from meeting_minutes.pipeline import PipelineOrchestrator

    data_dir = Path(config.data_dir).expanduser()
    try:
        orchestrator = PipelineOrchestrator(config)
        await orchestrator.reprocess(meeting_id)

        # Replay the verbatim ``## External notes`` paste if the user had one.
        # Reprocess just rebuilt the minutes from scratch, so any prior
        # post-appended section is gone and we have to put it back.
        sidecar = ext.load_notes_sidecar(data_dir, meeting_id)
        external_text = (sidecar.get("external_notes") or "").strip()
        if external_text:
            final_md = ext.append_section_to_local_files(
                data_dir, meeting_id, external_text,
            )
            ext.update_db_markdown(config, meeting_id, final_md)

        # Re-export so the Obsidian copy carries the regenerated content.
        try:
            orchestrator._export_to_obsidian_from_file(meeting_id)  # noqa: SLF001
        except Exception as exc:
            _LOG.warning(
                "Background regen: Obsidian re-export failed for %s: %s",
                meeting_id,
                exc,
            )

        set_status(data_dir, meeting_id, STATUS_READY)
        _LOG.info("Background regen: complete for %s", meeting_id)
    except Exception as exc:
        # Catch-all so the asyncio task never surfaces an unhandled
        # exception. Record the error on the sidecar for the UI.
        _LOG.exception("Background regen: failed for %s", meeting_id)
        set_status(data_dir, meeting_id, STATUS_ERROR, error=str(exc))


def schedule_background_regen(
    config: AppConfig,
    meeting_id: str,
) -> asyncio.Task:
    """Fire-and-forget wrapper around :func:`run_background_regen`.

    Returns the task so tests can await it; production callers ignore it.
    """
    return asyncio.create_task(run_background_regen(config, meeting_id))
