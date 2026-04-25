"""Post-hoc meeting-type change + summary regeneration.

The user can change the meeting type of an already-processed meeting from the
Minutes tab and have the summary rebuilt against the new type's template.
This module owns the async side of that flow: flipping a status flag on the
notes sidecar, kicking off the reprocess, and — if external notes were
previously pasted — replaying the ``## External notes`` post-append so the
verbatim paste survives the regeneration.

Mirrors the design of :mod:`meeting_minutes.external_notes`:
- **Sidecar is the source of truth for status.** The UI polls
  ``GET /meetings/{id}`` to see ``meeting_type_status`` flip from
  ``processing`` → ``ready`` (or ``error``).
- **Fire-and-forget asyncio task.** Reprocess takes 15–60 s of LLM time, far
  too long to hold an HTTP request open. Endpoint returns 202 immediately.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from meeting_minutes.config import AppConfig

_LOG = logging.getLogger(__name__)

# Status values written to the notes sidecar under ``meeting_type_status``.
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
    """Update ``meeting_type_status`` (and optionally ``meeting_type_error``).

    ``error`` is only relevant when ``status == STATUS_ERROR`` — for the other
    two states we clear it so the UI doesn't show a stale message.
    """
    from meeting_minutes import external_notes as ext

    data = ext.load_notes_sidecar(data_dir, meeting_id)
    data["meeting_type_status"] = status
    if status == STATUS_ERROR:
        data["meeting_type_error"] = error or "Unknown error"
    else:
        data.pop("meeting_type_error", None)
    ext.write_notes_sidecar(data_dir, meeting_id, data)


async def run_background_retype(config: AppConfig, meeting_id: str) -> None:
    """Reprocess minutes with the (already-updated) sidecar meeting type.

    Runs as an ``asyncio.create_task``; must never raise. Any failure is
    captured on the notes sidecar as ``meeting_type_status=error`` so the UI
    can surface it.

    Sequence:
      1. Run the standard ``reprocess`` flow. The pipeline reads
         ``meeting_type`` from the sidecar (pipeline.py:592) and the new value
         is already there — written synchronously by the endpoint before this
         task was scheduled — so the new template is automatically picked up.
      2. If the sidecar carries pasted external notes, replay the
         ``## External notes`` post-append so the verbatim paste survives the
         regeneration. This mirrors what
         ``external_notes.run_background_update`` does after its own reprocess.
      3. Re-export to Obsidian so the vault copy reflects the new template.
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
                "Meeting-type change: Obsidian re-export failed for %s: %s",
                meeting_id,
                exc,
            )

        set_status(data_dir, meeting_id, STATUS_READY)
        _LOG.info("Meeting-type change: regeneration complete for %s", meeting_id)
    except Exception as exc:
        # Catch-all so the asyncio task never surfaces an unhandled
        # exception. Record the error on the sidecar for the UI.
        _LOG.exception(
            "Meeting-type change: background retype failed for %s", meeting_id,
        )
        set_status(data_dir, meeting_id, STATUS_ERROR, error=str(exc))


def schedule_background_retype(
    config: AppConfig,
    meeting_id: str,
) -> asyncio.Task:
    """Fire-and-forget wrapper around :func:`run_background_retype`.

    Returns the task so tests can await it; production callers ignore it.
    """
    return asyncio.create_task(run_background_retype(config, meeting_id))
