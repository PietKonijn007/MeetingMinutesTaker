"""Helpers for the action-item review workflow (proposed → confirmed).

After a user confirms, rejects, or edits action items, the on-disk minutes
JSON, the rendered markdown, the DB-cached markdown and the Obsidian export
all need to be updated so the curated set is what shows up in exports and
search. The DB rows themselves are mutated by the API route — these helpers
re-sync the rendered artifacts that derive from them.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import ActionItemORM, MeetingORM, MinutesORM


# ---------------------------------------------------------------------------
# Section-replacement helpers
# ---------------------------------------------------------------------------


def _render_action_items_section(items: Iterable[ActionItemORM]) -> list[str]:
    """Build the ``## Action Items`` block for confirmed items only.

    Mirrors the format used at generation time
    (:meth:`MinutesJSONWriter._build_markdown`) so re-rendered output is
    byte-identical to a fresh generate when the inputs match.
    """
    confirmed = [
        ai for ai in items
        if (ai.proposal_state or "proposed") == "confirmed"
    ]
    if not confirmed:
        return []
    lines = ["## Action Items", ""]
    for item in confirmed:
        check = "x" if (item.status or "open") == "done" else " "
        priority_tag = f" [{item.priority.upper()}]" if item.priority else ""
        line = f"- [{check}]{priority_tag} {item.description}"
        if item.owner:
            line += f" — Owner: {item.owner}"
        if item.due_date:
            line += f" (Due: {item.due_date})"
        lines.append(line)
    lines.append("")
    return lines


def _replace_action_items_in_markdown(md: str, new_section_lines: list[str]) -> str:
    """Swap the existing ``## Action Items`` section in ``md`` for the
    re-rendered one. If no section is present, insert the new one before the
    ``## Decisions`` block (or, failing that, append at the end).

    Returns the updated markdown string. When ``new_section_lines`` is empty,
    any existing section is removed without replacement.
    """
    lines = md.split("\n")
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None:
            if line.strip().lower().startswith("## action items"):
                start = i
                continue
        else:
            # Stop at the next ## heading (section boundary).
            if line.startswith("## "):
                end = i
                break

    if start is not None:
        # Drop trailing blank line(s) at the boundary so we don't accumulate
        # them on repeated re-renders.
        while end > start + 1 and lines[end - 1].strip() == "":
            end -= 1
        before = lines[:start]
        after = lines[end:]
        if new_section_lines:
            return "\n".join(before + new_section_lines + after)
        # Removing the section: drop it and one trailing blank line if the
        # previous block left one, to keep spacing tidy.
        if before and before[-1].strip() == "" and after and after[0].strip() == "":
            after = after[1:]
        return "\n".join(before + after)

    if not new_section_lines:
        return md

    # No existing section — insert before the first heading that conventionally
    # comes after Action Items. If none match, append at the end.
    for i, line in enumerate(lines):
        if line.startswith("## ") and line.strip().lower() in (
            "## decisions", "## key topics", "## risks & concerns",
            "## open questions", "## follow-ups", "## parking lot",
            "## prior action item updates", "## follow-up email draft",
            "## meeting effectiveness",
        ):
            return "\n".join(lines[:i] + new_section_lines + lines[i:])
    return md.rstrip("\n") + "\n\n" + "\n".join(new_section_lines) + "\n"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def resync_action_items_artifacts(
    *,
    session: Session,
    config: AppConfig,
    meeting_id: str,
) -> None:
    """Re-render the action-items artifacts for a meeting after a review change.

    Call after any DB mutation of :attr:`ActionItemORM.proposal_state` (or
    description/owner/due_date/status) so the on-disk minutes JSON, the
    rendered markdown, the DB-cached markdown, the FTS index and the
    Obsidian export reflect the curated set.

    Best-effort: filesystem and Obsidian errors are swallowed so a review
    submission never fails because the side-effect of an export blew up.
    """
    items = (
        session.query(ActionItemORM)
        .filter(ActionItemORM.meeting_id == meeting_id)
        .all()
    )
    new_section = _render_action_items_section(items)

    data_dir = Path(config.data_dir).expanduser()
    minutes_dir = data_dir / "minutes"
    json_path = minutes_dir / f"{meeting_id}.json"
    md_path = minutes_dir / f"{meeting_id}.md"

    enc_key = (
        config.security.encryption_key
        if config.security.encryption_enabled
        else None
    )

    # 1. Mirror DB action_items into the on-disk minutes JSON.
    if json_path.exists():
        try:
            if enc_key:
                from meeting_minutes.encryption import decrypt_file_text, encrypt_file
                raw = decrypt_file_text(json_path, enc_key)
            else:
                raw = json_path.read_text(encoding="utf-8")
            data = _json.loads(raw)

            ai_dump = []
            for ai in items:
                ai_dump.append({
                    "id": ai.action_item_id,
                    "description": ai.description,
                    "owner": ai.owner,
                    "due_date": ai.due_date,
                    "status": ai.status or "open",
                    "mentioned_at_seconds": ai.mentioned_at_seconds,
                    "priority": ai.priority,
                    "transcript_segment_ids": [],
                    "proposal_state": ai.proposal_state or "proposed",
                })
            data["action_items"] = ai_dump
            sd = data.get("structured_data") or {}
            if isinstance(sd, dict):
                sd["action_items"] = ai_dump
                data["structured_data"] = sd

            json_path.write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")
            if enc_key:
                from meeting_minutes.encryption import encrypt_file
                encrypt_file(json_path, enc_key)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ action-review: failed to update minutes JSON for {meeting_id}: {exc}")

    # 2. Rewrite the ## Action Items section in the rendered markdown.
    new_md: str | None = None
    if md_path.exists():
        try:
            if enc_key:
                from meeting_minutes.encryption import decrypt_file_text, encrypt_file
                content = decrypt_file_text(md_path, enc_key)
            else:
                content = md_path.read_text(encoding="utf-8")
            new_md = _replace_action_items_in_markdown(content, new_section)
            md_path.write_text(new_md, encoding="utf-8")
            if enc_key:
                encrypt_file(md_path, enc_key)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ action-review: failed to update markdown for {meeting_id}: {exc}")

    # 3. Sync the DB-cached markdown so /api/meetings/{id} returns the new
    #    body and the FTS index matches what Obsidian holds.
    if new_md is not None:
        m = session.get(MeetingORM, meeting_id)
        if m and m.minutes:
            m.minutes.markdown_content = new_md
        try:
            transcript_text = m.transcript.full_text if (m and m.transcript) else ""
            session.execute(
                text("DELETE FROM meetings_fts WHERE meeting_id = :mid"),
                {"mid": meeting_id},
            )
            session.execute(
                text(
                    "INSERT INTO meetings_fts(meeting_id, title, transcript_text, minutes_text) "
                    "VALUES (:mid, :title, :tt, :mt)"
                ),
                {
                    "mid": meeting_id,
                    "title": m.title if m else "",
                    "tt": transcript_text or "",
                    "mt": new_md or "",
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ action-review: FTS resync failed for {meeting_id}: {exc}")
        session.commit()

    # 4. Re-export to Obsidian. The exporter reads the JSON we just wrote.
    if config.obsidian.enabled and config.obsidian.vault_path:
        try:
            from meeting_minutes.pipeline import PipelineOrchestrator
            PipelineOrchestrator(config)._export_to_obsidian_from_file(meeting_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ action-review: Obsidian re-export failed for {meeting_id}: {exc}")
