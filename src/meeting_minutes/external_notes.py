"""Post-hoc external-notes handling.

Users can paste notes exported from a meeting app (Teams / Zoom / Meet /
Gemini / Otter / etc.) into an already-processed meeting. This module owns
everything that happens after the paste: archiving the raw text, wiring it
into the notes sidecar so regeneration picks it up, driving the async
post-processing pipeline (speaker rename + full reprocess), and appending a
verbatim ``## External notes`` section to the rendered markdown so the paste
survives any future regeneration.

Design choices:
- **Single archive file per meeting** (``data/external_notes/{id}.md``),
  overwritten on re-submit. Single-user MVP; no audit trail.
- **Sidecar is the source of truth for status.** The UI polls
  ``GET /meetings/{id}`` to see ``external_notes_status`` flip from
  ``processing`` → ``ready`` (or ``error``).
- **Post-append, don't plumb-through.** The ``## External notes`` section is
  appended to the rendered ``.md`` *after* regeneration, so the LLM never
  touches the verbatim paste. That guarantees the user's text is preserved
  character-for-character across any number of future regenerations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from meeting_minutes.config import AppConfig, resolve_db_path

_LOG = logging.getLogger(__name__)

# Status values written to the notes sidecar. The UI treats any other value
# as "no background job running".
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_ERROR = "error"

# Header we use to mark the verbatim paste in the rendered markdown. Kept
# constant so regeneration can idempotently strip any stale copy before
# re-appending a fresh one.
_SECTION_HEADER = "## External notes"


# ---------------------------------------------------------------------------
# Notes sidecar helpers (data/notes/{id}.json)
# ---------------------------------------------------------------------------


def _notes_path(data_dir: Path, meeting_id: str) -> Path:
    return data_dir / "notes" / f"{meeting_id}.json"


def _archive_path(data_dir: Path, meeting_id: str) -> Path:
    return data_dir / "external_notes" / f"{meeting_id}.md"


def load_notes_sidecar(data_dir: Path, meeting_id: str) -> dict[str, Any]:
    """Read the notes sidecar, returning ``{}`` if missing or malformed.

    Defensive by design — the sidecar is touched from multiple paths
    (pipeline, PATCH /transcript/speakers, this module) and any of them may
    land in the middle of an edit on slow disks. We never crash on a bad read.
    """
    path = _notes_path(data_dir, meeting_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()) or {}
    except Exception:
        _LOG.warning("Failed to parse notes sidecar at %s — treating as empty", path)
        return {}


def write_notes_sidecar(data_dir: Path, meeting_id: str, data: dict[str, Any]) -> None:
    """Write the notes sidecar atomically (via temp-file + rename)."""
    path = _notes_path(data_dir, meeting_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def set_status(
    data_dir: Path,
    meeting_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Update ``external_notes_status`` (and optionally ``external_notes_error``).

    ``error`` is only relevant when ``status == STATUS_ERROR`` — for the other
    two states we clear it so the UI doesn't show a stale message.
    """
    data = load_notes_sidecar(data_dir, meeting_id)
    data["external_notes_status"] = status
    if status == STATUS_ERROR:
        data["external_notes_error"] = error or "Unknown error"
    else:
        data.pop("external_notes_error", None)
    write_notes_sidecar(data_dir, meeting_id, data)


# ---------------------------------------------------------------------------
# Archive file (data/external_notes/{id}.md)
# ---------------------------------------------------------------------------


def write_archive(data_dir: Path, meeting_id: str, text: str) -> Path:
    """Write the raw pasted text to the archive directory.

    One file per meeting, overwritten on re-submit (per the MVP spec). The
    user's browser already has the "old" version in memory if they care, and
    we'd rather not accumulate stale notes forever on a single-user install.
    """
    path = _archive_path(data_dir, meeting_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_archive(data_dir: Path, meeting_id: str) -> str | None:
    """Return the stored paste for a meeting, or ``None`` if none exists."""
    path = _archive_path(data_dir, meeting_id)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Markdown post-processing (local + DB + Obsidian)
# ---------------------------------------------------------------------------


def _strip_existing_section(markdown: str) -> str:
    """Remove any previous ``## External notes`` section from ``markdown``.

    We can't rely on the LLM to preserve a section added out-of-band, and on
    repeat submissions the pre-regeneration .md can still have a stale copy.
    Splitting on the exact header we control (``## External notes``) keeps
    the logic simple and deterministic — no regex against user-supplied text.
    """
    if _SECTION_HEADER not in markdown:
        return markdown
    # Walk line-by-line so we only drop from our header up to the next
    # top-level section (``## ...``) or EOF.
    out: list[str] = []
    in_section = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped == _SECTION_HEADER:
            in_section = True
            continue
        if in_section:
            # A new ``## ...`` heading ends our section. A ``### ...``
            # subheader is treated as part of our section and dropped — the
            # user's verbatim paste shouldn't normally contain ``## `` lines,
            # so this is fine.
            if stripped.startswith("## ") and not stripped.startswith("## External notes"):
                in_section = False
                out.append(line)
            # otherwise drop the line
            continue
        out.append(line)
    # Trim trailing blank lines we might have left behind.
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


def _format_section(text: str) -> str:
    """Render the verbatim paste with our canonical header + a trailing newline."""
    body = (text or "").rstrip() + "\n"
    return f"\n{_SECTION_HEADER}\n\n{body}"


def append_section_to_local_files(
    data_dir: Path,
    meeting_id: str,
    text: str,
) -> str:
    """Append ``## External notes`` to the on-disk minutes markdown and JSON.

    Updates both ``data/minutes/{id}.md`` and the ``minutes_markdown`` field
    inside ``data/minutes/{id}.json`` so they stay in sync. Returns the final
    markdown string, which the caller ships to the DB and to Obsidian.

    The function is idempotent: any pre-existing ``## External notes`` block
    is stripped before the new one is appended. That matters on repeat
    submissions — the old paste lives in the current ``.md`` and we want the
    new paste to fully replace it.
    """
    md_path = data_dir / "minutes" / f"{meeting_id}.md"
    json_path = data_dir / "minutes" / f"{meeting_id}.json"

    base_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    cleaned = _strip_existing_section(base_md)
    final_md = cleaned.rstrip() + _format_section(text)

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(final_md, encoding="utf-8")

    # Keep the JSON's embedded markdown aligned so anything reading from the
    # JSON (e.g. Obsidian export fallback at pipeline.py:1354) sees the same
    # content as the .md file.
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            data["minutes_markdown"] = final_md
            json_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            _LOG.warning("Could not update minutes JSON for %s: %s", meeting_id, exc)

    return final_md


def update_db_markdown(config: AppConfig, meeting_id: str, markdown: str) -> None:
    """Push an updated markdown blob into ``minutes.markdown_content``.

    Best-effort — logs and swallows on failure so the broader background job
    still records "ready" if everything else succeeded. Search-index
    reingestion is deliberately skipped; the full-text index was already
    rebuilt by the preceding ``run_ingestion`` call, and a few characters of
    header drift is not worth a second pass.
    """
    from meeting_minutes.system3.db import MinutesORM, get_session_factory

    db_path = resolve_db_path(config.storage.sqlite_path)
    if not db_path.exists():
        _LOG.warning("DB not found at %s; skipping markdown_content update", db_path)
        return

    session_factory = get_session_factory(f"sqlite:///{db_path}")
    session = session_factory()
    try:
        minutes = session.get(MinutesORM, meeting_id)
        if minutes is None:
            _LOG.warning("No MinutesORM for %s; skipping markdown_content update", meeting_id)
            return
        minutes.markdown_content = markdown
        session.commit()
    except Exception as exc:
        _LOG.warning("Failed to update DB markdown_content for %s: %s", meeting_id, exc)
        session.rollback()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Background pipeline orchestration
# ---------------------------------------------------------------------------


async def run_background_update(
    config: AppConfig,
    meeting_id: str,
    external_text: str,
) -> None:
    """End-to-end post-hoc update: infer speakers → reprocess → re-append → re-export.

    Runs as an ``asyncio.create_task``; must never raise. Any failure is
    captured on the notes sidecar as ``external_notes_status=error`` so the
    UI can surface it.

    Sequence:
      1. LLM-infer speaker names from the external notes (best-effort — empty
         mapping means "leave as-is").
      2. Apply the mapping to the transcript JSON (via the helper shared with
         ``PATCH /transcript/speakers``).
      3. Run the standard ``reprocess`` flow: regenerates minutes with the
         external notes injected via the user-notes rail, re-ingests the DB,
         re-exports Obsidian.
      4. Post-append ``## External notes`` to the local .md + JSON.
      5. Sync the new markdown to the DB column.
      6. Re-export to Obsidian so the verbatim paste lands in the vault too.
    """
    from meeting_minutes.api.routes.meetings import apply_speaker_mapping
    from meeting_minutes.pipeline import PipelineOrchestrator
    from meeting_minutes.system2.llm_client import LLMClient
    from meeting_minutes.system2.speaker_rename import (
        build_transcript_sample,
        infer_speaker_names,
    )

    data_dir = Path(config.data_dir).expanduser()
    try:
        # ---- 1. Speaker inference ---------------------------------------
        transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"
        if transcript_path.exists():
            try:
                tdata = json.loads(transcript_path.read_text())
                segments = tdata.get("transcript", {}).get("segments", []) or []
                all_labels = [
                    s["label"] for s in (tdata.get("speakers") or []) if s.get("label")
                ]
                # Only ask the LLM about generic SPEAKER_\d+ labels; anything
                # already renamed to a human name is treated as authoritative.
                generic = [l for l in all_labels if l.startswith("SPEAKER_")]
                if generic:
                    llm = LLMClient(config.generation.llm)
                    sample = build_transcript_sample(segments)
                    # Phase 2: also feed attachment summaries — they may
                    # carry presenter names from title slides, "prepared
                    # by" footers, or explicit attendee lists.
                    from meeting_minutes.attachments import (
                        pipeline_integration as _att_pi,
                    )
                    att_entries = _att_pi.gather_entries(data_dir, meeting_id)
                    rename_context = _att_pi.render_for_speaker_rename(att_entries)
                    mapping = await infer_speaker_names(
                        llm=llm,
                        current_labels=generic,
                        transcript_sample=sample,
                        external_notes=external_text,
                        attachment_context=rename_context,
                    )
                    if mapping:
                        apply_speaker_mapping(data_dir, meeting_id, mapping)
                        _LOG.info(
                            "External-notes: renamed %d speakers for %s",
                            len(mapping),
                            meeting_id,
                        )
            except Exception as exc:
                # Non-fatal: speaker rename is an enhancement. Continue with
                # reprocess even if inference blew up.
                _LOG.warning(
                    "External-notes: speaker inference failed for %s: %s",
                    meeting_id,
                    exc,
                )

        # ---- 2. Regenerate minutes + re-ingest --------------------------
        orchestrator = PipelineOrchestrator(config)
        await orchestrator.reprocess(meeting_id)

        # ---- 3. Post-append ## External notes ---------------------------
        final_md = append_section_to_local_files(data_dir, meeting_id, external_text)

        # ---- 4. Sync DB + Obsidian --------------------------------------
        update_db_markdown(config, meeting_id, final_md)
        # Re-export so the Obsidian copy also carries the new section.
        # ``_export_to_obsidian_from_file`` is defensive and skips quietly
        # when the Obsidian integration is disabled.
        try:
            orchestrator._export_to_obsidian_from_file(meeting_id)  # noqa: SLF001
        except Exception as exc:
            _LOG.warning(
                "External-notes: Obsidian re-export failed for %s: %s",
                meeting_id,
                exc,
            )

        set_status(data_dir, meeting_id, STATUS_READY)
        _LOG.info("External-notes: update complete for %s", meeting_id)
    except Exception as exc:
        # Catch-all so the asyncio task never surfaces an unhandled
        # exception. Record the error on the sidecar for the UI.
        _LOG.exception("External-notes: background update failed for %s", meeting_id)
        set_status(data_dir, meeting_id, STATUS_ERROR, error=str(exc))


def schedule_background_update(
    config: AppConfig,
    meeting_id: str,
    external_text: str,
) -> asyncio.Task:
    """Fire-and-forget wrapper around :func:`run_background_update`.

    Returns the task so tests can await it; production callers ignore it.
    """
    return asyncio.create_task(run_background_update(config, meeting_id, external_text))
