"""Plumbing that lets ``pipeline.py`` consume attachment summaries.

Two responsibilities, kept here so ``pipeline.py`` doesn't grow another
module's worth of logic:

1. **Gather + wait + render** — read the per-attachment sidecars for a
   meeting, optionally wait briefly for in-flight summaries, and render
   a labelled context block to splice into the LLM prompt.
2. **Post-append `## Attachments`** — after ``MinutesJSONWriter`` runs,
   tack a verbatim attachments section onto the rendered minutes
   markdown (and the JSON's embedded ``minutes_markdown``). Same
   idempotent strip-and-replace trick as
   :func:`meeting_minutes.external_notes.append_section_to_local_files`.

Reading from the sidecars (not the DB) is deliberate: the markdown is
the canonical source of truth for the summary, and it survives DB
rebuilds.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from meeting_minutes.attachments import sidecar as sidecar_mod
from meeting_minutes.attachments import storage as storage_mod

_LOG = logging.getLogger(__name__)

# Marker used for the post-appended block. Kept constant so we can
# idempotently strip stale copies on regeneration.
_SECTION_HEADER = "## Attachments"


# ---------------------------------------------------------------------------
# Gather + wait + render
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentEntry:
    """One attachment ready for injection / rendering."""

    attachment_id: str
    title: str
    source: str
    summary: str
    summary_status: str  # 'ready' | 'pending' | 'error'
    extraction_method: str
    kind: str


def gather_entries(data_dir: Path, meeting_id: str) -> list[AttachmentEntry]:
    """Read every sidecar for a meeting and return them in stable order.

    Stable order = filename order, which matches insertion order because
    attachment_ids are uuid4 — close enough to creation order for the
    rendered ``## Attachments`` section to feel deterministic. (If we
    later want strict creation-time ordering we can sort by the
    ``extracted_at`` frontmatter field.)

    Returns ``[]`` if the meeting has no attachments folder yet.
    """
    folder = storage_mod.attachment_dir(data_dir, meeting_id)
    if not folder.exists():
        return []

    entries: list[AttachmentEntry] = []
    for sidecar_path in sorted(folder.glob("*.md")):
        parsed = sidecar_mod.parse_attachment_sidecar(sidecar_path)
        fm = parsed.frontmatter
        # Skip any sidecar that's missing the basics — a half-written
        # file from a crashed worker shouldn't trip the pipeline.
        aid = fm.get("attachment_id")
        if not aid:
            continue
        entries.append(
            AttachmentEntry(
                attachment_id=aid,
                title=fm.get("title", "Untitled attachment"),
                source=fm.get("source", ""),
                summary=parsed.summary,
                summary_status=fm.get("summary_status", "pending"),
                extraction_method=fm.get("extraction_method", ""),
                kind=fm.get("kind", "file"),
            )
        )
    return entries


async def wait_for_pending(
    data_dir: Path,
    meeting_id: str,
    timeout_s: float,
    *,
    poll_interval_s: float = 0.5,
) -> list[AttachmentEntry]:
    """Wait up to ``timeout_s`` for any pending summaries to flip to ready.

    Returns the final list of entries — the caller then filters for
    ``summary_status == "ready"`` to decide what to inject. We don't
    raise on timeout; the pipeline runs minutes generation either way,
    just without the not-yet-ready summaries (they get picked up on the
    next regen).

    Polls the sidecar files directly. The summarizer worker writes to
    them atomically, so a partial-read is impossible.
    """
    deadline = asyncio.get_event_loop().time() + max(timeout_s, 0.0)
    while True:
        entries = gather_entries(data_dir, meeting_id)
        pending = [e for e in entries if e.summary_status == "pending"]
        if not pending:
            return entries
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            _LOG.warning(
                "Attachment summaries still pending after %ss for %s: %s",
                timeout_s,
                meeting_id,
                ", ".join(e.attachment_id for e in pending),
            )
            return entries
        await asyncio.sleep(min(poll_interval_s, remaining))


def render_for_speaker_rename(entries: list[AttachmentEntry]) -> str:
    """Render attachment summaries as a compact context block for speaker rename.

    Different shape from :func:`render_llm_context_block`: shorter, no
    ground-truth preamble, since the speaker-rename prompt has its own
    instructions about how to weight attachment evidence. Only ready
    summaries with non-empty bodies are included; pending or errored
    sidecars contribute nothing.
    """
    blocks: list[str] = []
    for entry in entries:
        if entry.summary_status != "ready" or not entry.summary.strip():
            continue
        blocks.append(
            f"### {entry.title}\n"
            f"Source: {entry.source or '(unspecified)'}\n\n"
            f"{entry.summary.strip()}"
        )
    return "\n\n".join(blocks)


def render_llm_context_block(entries: list[AttachmentEntry]) -> str:
    """Render the ATTACHED MATERIAL block injected into the LLM prompt.

    Only ready summaries are included — pending or errored ones are
    skipped silently (logged elsewhere). Returns ``""`` if nothing is
    ready, so the caller can append unconditionally.

    Format mirrors the spec:

    .. code-block::

        ## ATTACHED MATERIAL: <title>
        Source: <source>
        Caption / extraction method line for context

        <summary body>
    """
    blocks: list[str] = []
    for entry in entries:
        if entry.summary_status != "ready" or not entry.summary.strip():
            continue
        blocks.append(
            f"## ATTACHED MATERIAL: {entry.title}\n"
            f"Source: {entry.source or '(unspecified)'}\n"
            f"Extracted via: {entry.extraction_method or 'unknown'}\n\n"
            f"{entry.summary.strip()}"
        )
    if not blocks:
        return ""
    preamble = (
        "The following materials were attached to this meeting. Treat them "
        "as ground-truth context: when the transcript references them, "
        "prefer their exact wording for numbers, names, and direct quotes "
        "over what speakers paraphrased. Do not invent attachment content "
        "that isn't in these summaries.\n\n"
    )
    return preamble + "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Post-append `## Attachments` section
# ---------------------------------------------------------------------------


def append_attachments_section_to_files(
    data_dir: Path,
    meeting_id: str,
    entries: list[AttachmentEntry],
) -> str | None:
    """Append a verbatim ``## Attachments`` section to the rendered minutes.

    Updates both ``data/minutes/{id}.md`` and the ``minutes_markdown``
    field inside ``data/minutes/{id}.json`` so they stay in sync — the
    DB ingestion step reads from the JSON, so updating just the .md
    would silently drop the section from search and the API.

    Returns the new markdown string when at least one attachment is
    eligible for rendering, or ``None`` when the section would be empty
    (in which case any pre-existing stale section is still stripped).

    Idempotent: a pre-existing ``## Attachments`` block is removed
    before the new one is appended, so repeat calls converge.
    """
    md_path = data_dir / "minutes" / f"{meeting_id}.md"
    json_path = data_dir / "minutes" / f"{meeting_id}.json"

    # Strip any prior block before deciding whether to write a new one —
    # if there are no eligible attachments now, the file should not
    # carry a stale section from a previous run.
    base_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    cleaned = _strip_existing_section(base_md)

    rendered = _render_section(entries)
    if rendered:
        final_md = cleaned.rstrip() + "\n" + rendered
    else:
        final_md = cleaned

    if final_md != base_md:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(final_md, encoding="utf-8")

    # Sync the JSON's embedded markdown so DB ingestion picks up the
    # appended section. Best-effort: a malformed JSON shouldn't block
    # the minutes pipeline. Logged so the operator can investigate.
    if json_path.exists() and final_md != base_md:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            data["minutes_markdown"] = final_md
            json_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "Could not update minutes JSON for %s with attachments section: %s",
                meeting_id,
                exc,
            )

    return final_md if rendered else None


def _render_section(entries: list[AttachmentEntry]) -> str:
    """Render the ``## Attachments`` section. Returns "" if nothing to render."""
    rows: list[str] = []
    for entry in entries:
        # We render every attachment that has a sidecar, even if the
        # summary is empty or errored — the user wants to see the file
        # listed in the rendered minutes regardless of LLM state. The
        # body just changes to reflect what we have.
        body: str
        if entry.summary_status == "ready" and entry.summary.strip():
            body = entry.summary.strip()
        elif entry.summary_status == "error":
            body = "_Summary failed to generate._"
        else:
            body = "_Summary not yet ready._"

        # ``raw_url`` is a relative path the web UI resolves against the
        # API root. Server-rendered exports (PDF/DOCX) won't follow it
        # but they keep the citation visible, which is the point.
        raw_url = f"/api/attachments/{entry.attachment_id}/raw"
        rows.append(
            f"### {entry.title}\n"
            f"*Source: {entry.source or '(unspecified)'}*\n\n"
            f"{body}\n\n"
            f"[View source]({raw_url})"
        )

    if not rows:
        return ""

    return f"{_SECTION_HEADER}\n\n" + "\n\n".join(rows) + "\n"


def _strip_existing_section(markdown: str) -> str:
    """Drop any prior ``## Attachments`` block from ``markdown``.

    Mirrors the structure of the strip helper in
    :mod:`meeting_minutes.external_notes` — split by line, drop from our
    header until the next ``## ...`` heading or EOF, leave everything
    else alone.
    """
    if _SECTION_HEADER not in markdown:
        return markdown
    out: list[str] = []
    in_section = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped == _SECTION_HEADER:
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## ") and stripped != _SECTION_HEADER:
                in_section = False
                out.append(line)
            # Otherwise drop the line (including any `### ` subheaders
            # that belong to our section).
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else "")
