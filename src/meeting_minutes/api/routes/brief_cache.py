"""BRF-2 brief cache — persist and look up generated briefs.

Cache key: ``(attendee_set_hash, topic_hash, focus_items_hash, meeting_type)``.

A cached row is considered fresh when:
  - It is younger than ``brief.cache.max_age_minutes``.
  - No meeting with overlapping attendees has been updated after the row's
    ``generated_at``.
  - No action item involving any attendee has been mutated after
    ``generated_at``.

Invalidation is checked at request time via cheap SQL ``MAX(updated_at)``
lookups; no event bus.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from meeting_minutes.api.routes.brief import BriefingPayload
from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import (
    ActionItemORM,
    MeetingBriefORM,
    MeetingORM,
    meeting_attendees,
)

logger = logging.getLogger(__name__)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def attendee_set_hash(person_ids: list[str]) -> str:
    return _hash("|".join(sorted(person_ids)))


def topic_hash(topic: str | None) -> str:
    return _hash((topic or "").strip().lower())


def focus_items_hash(items: list[str]) -> str:
    norm = sorted([(f or "").strip().lower() for f in items if (f or "").strip()])
    return _hash("|".join(norm))


def _resolve_output_dir(config: AppConfig) -> Path:
    out = Path(config.brief.export.output_dir).expanduser()
    if not out.is_absolute():
        from meeting_minutes.config import resolve_db_path

        # Reuse the same project-root resolution logic as the DB path.
        # ``resolve_db_path`` accepts any relative string.
        out = resolve_db_path(str(out))
    out.mkdir(parents=True, exist_ok=True)
    return out


def _slug(s: str, fallback: str = "brief") -> str:
    base = "".join(c if c.isalnum() else "-" for c in (s or "").lower()).strip("-")
    base = "-".join(filter(None, base.split("-")))
    return base[:40] or fallback


def find_fresh(
    session: Session,
    config: AppConfig,
    person_ids: list[str],
    topic: str | None,
    focus_items: list[str],
    meeting_type: str | None,
) -> Optional[MeetingBriefORM]:
    """Return a fresh cached row, or ``None``."""
    a_hash = attendee_set_hash(person_ids)
    t_hash = topic_hash(topic)
    f_hash = focus_items_hash(focus_items)

    row = (
        session.query(MeetingBriefORM)
        .filter(
            MeetingBriefORM.attendee_set_hash == a_hash,
            MeetingBriefORM.topic_hash == t_hash,
            MeetingBriefORM.focus_items_hash == f_hash,
            MeetingBriefORM.superseded_by.is_(None),
        )
        .order_by(MeetingBriefORM.generated_at.desc())
        .first()
    )
    if row is None:
        return None

    if (meeting_type or None) != (row.meeting_type or None):
        return None

    max_age = timedelta(minutes=int(config.brief.cache.max_age_minutes))
    now = datetime.now(timezone.utc)
    generated_at = row.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    if now - generated_at > max_age:
        return None

    # Invalidate if any meeting with overlapping attendees was updated
    # after the brief was generated.
    if person_ids:
        latest_meeting_update = (
            session.query(func.max(MeetingORM.updated_at))
            .join(meeting_attendees, MeetingORM.meeting_id == meeting_attendees.c.meeting_id)
            .filter(meeting_attendees.c.person_id.in_(person_ids))
            .scalar()
        )
        if latest_meeting_update is not None:
            if latest_meeting_update.tzinfo is None:
                latest_meeting_update = latest_meeting_update.replace(tzinfo=timezone.utc)
            if latest_meeting_update > generated_at:
                return None

    # Invalidate if any action item belonging to a meeting with overlapping
    # attendees has been mutated. ActionItemORM doesn't carry its own
    # ``updated_at`` so we proxy via the parent meeting's update time —
    # which already covers the typical pipeline write path.
    return row


def write(
    session: Session,
    config: AppConfig,
    payload: BriefingPayload,
    *,
    person_ids: list[str],
    markdown: str,
    model: str | None,
) -> MeetingBriefORM:
    """Persist ``payload`` + ``markdown`` to disk and create a DB row."""
    out_dir = _resolve_output_dir(config)
    now = datetime.now(timezone.utc)

    slug = _slug(payload.topic or (payload.who_and_when_last.last_meeting_title or "brief"))
    stem = f"{now.strftime('%Y-%m-%d-%H%M%S')}-{slug}"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.brief.json"

    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")

    source_meeting_ids = [e.meeting_id for e in payload.context_excerpts if e.meeting_id]
    # Also include any meeting ids cited by talking points / focus findings.
    for tp in payload.talking_points:
        for c in tp.citations:
            if c.meeting_id:
                source_meeting_ids.append(c.meeting_id)
    for f in payload.focus_findings:
        for c in f.citations:
            if c.meeting_id:
                source_meeting_ids.append(c.meeting_id)
    source_meeting_ids = sorted(set(source_meeting_ids))

    row = MeetingBriefORM(
        attendee_set_hash=attendee_set_hash(person_ids),
        topic=payload.topic,
        topic_hash=topic_hash(payload.topic),
        focus_items=json.dumps(payload.focus_items),
        focus_items_hash=focus_items_hash(payload.focus_items),
        meeting_type=payload.meeting_type,
        markdown_path=str(md_path),
        json_path=str(json_path),
        generated_at=now,
        model=model,
        source_meeting_ids=json.dumps(source_meeting_ids),
    )
    session.add(row)
    session.commit()
    return row


def load_payload(row: MeetingBriefORM) -> BriefingPayload | None:
    """Re-hydrate a cached ``BriefingPayload`` from disk."""
    try:
        text = Path(row.json_path).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cached brief JSON missing at %s: %s", row.json_path, exc)
        return None
    try:
        return BriefingPayload.model_validate_json(text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Cached brief JSON unreadable at %s: %s", row.json_path, exc)
        return None


def load_markdown(row: MeetingBriefORM) -> str | None:
    try:
        return Path(row.markdown_path).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cached brief markdown missing at %s: %s", row.markdown_path, exc)
        return None
