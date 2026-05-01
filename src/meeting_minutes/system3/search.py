"""Full-text search engine using SQLite FTS5."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from meeting_minutes.models import SearchQuery, SearchResult, SearchResults
from meeting_minutes.system3.db import MeetingORM

_LOG = logging.getLogger(__name__)


def _gather_attachment_text_for_fts(data_dir: Path, meeting_id: str) -> str:
    """Concatenate per-attachment extracted text for FTS indexing.

    Reads every sidecar under ``data/attachments/{meeting_id}/`` and
    returns ``"### {title}\\n\\n{extracted_body}"`` blocks joined by
    blank lines. Best-effort: a malformed sidecar is logged and
    skipped, never blocks indexing.

    Returns the LLM summary too — it lives in the same sidecar — but
    not as a separate block since the summary is also already in the
    rendered minutes (via the post-appended ``## Attachments`` section).
    The duplication is fine for FTS; the user's keyword query just
    matches both occurrences.
    """
    folder = data_dir / "attachments" / meeting_id
    if not folder.exists():
        return ""

    # Lazy import: search.py is imported by the API layer at startup,
    # but the attachments module pulls in pypdf / Pillow that aren't
    # always desirable to load there (slower cold start, optional dep
    # cascades). Defer until we actually need to read sidecars.
    from meeting_minutes.attachments.sidecar import parse_attachment_sidecar

    blocks: list[str] = []
    for sidecar_path in sorted(folder.glob("*.md")):
        try:
            parsed = parse_attachment_sidecar(sidecar_path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Could not parse sidecar %s for FTS: %s", sidecar_path, exc)
            continue
        title = parsed.frontmatter.get("title", "Untitled attachment")
        body = parsed.extracted.strip()
        if not body:
            continue
        blocks.append(f"### {title}\n\n{body}")
    return "\n\n".join(blocks)


class SearchEngine:
    """FTS5-based search with filter parsing."""

    TYPE_RE = re.compile(r"\btype:(\w+)\b")
    AFTER_RE = re.compile(r"\bafter:(\d{4}-\d{2}-\d{2})\b")
    BEFORE_RE = re.compile(r"\bbefore:(\d{4}-\d{2}-\d{2})\b")

    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def parse_query(self, raw_query: str) -> SearchQuery:
        """Parse structured filters from raw query string."""
        fts_query = raw_query

        meeting_type = None
        after_date = None
        before_date = None

        # Extract type: filter
        m = self.TYPE_RE.search(fts_query)
        if m:
            meeting_type = m.group(1)
            fts_query = self.TYPE_RE.sub("", fts_query).strip()

        # Extract after: filter
        m = self.AFTER_RE.search(fts_query)
        if m:
            try:
                after_date = datetime.fromisoformat(m.group(1))
            except ValueError:
                pass
            fts_query = self.AFTER_RE.sub("", fts_query).strip()

        # Extract before: filter
        m = self.BEFORE_RE.search(fts_query)
        if m:
            try:
                before_date = datetime.fromisoformat(m.group(1))
            except ValueError:
                pass
            fts_query = self.BEFORE_RE.sub("", fts_query).strip()

        return SearchQuery(
            raw_query=raw_query,
            fts_query=fts_query.strip(),
            meeting_type=meeting_type,
            after_date=after_date,
            before_date=before_date,
        )

    def search(self, query: SearchQuery) -> SearchResults:
        """Execute FTS5 search with filters. Returns ranked results."""
        params: dict = {}
        conditions: list[str] = ["1=1"]

        # FTS5 query
        if query.fts_query:
            fts_sql = """
                SELECT meeting_id, rank
                FROM meetings_fts
                WHERE meetings_fts MATCH :fts_query
                ORDER BY rank
            """
            params["fts_query"] = query.fts_query
            fts_results = self._session.execute(text(fts_sql), params).fetchall()
            fts_meeting_ids = {
                row[0]: row[1] for row in fts_results if row[0] is not None
            }

            if not fts_meeting_ids:
                return SearchResults(results=[], total_count=0, query=query)

            id_list = ",".join(f"'{mid}'" for mid in fts_meeting_ids)
            conditions.append(f"m.meeting_id IN ({id_list})")
        else:
            fts_meeting_ids = {}

        # Metadata filters
        if query.meeting_type:
            conditions.append("m.meeting_type = :meeting_type")
            params["meeting_type"] = query.meeting_type

        if query.after_date:
            conditions.append("m.date >= :after_date")
            params["after_date"] = query.after_date

        if query.before_date:
            conditions.append("m.date <= :before_date")
            params["before_date"] = query.before_date

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT m.meeting_id, m.title, m.date, m.meeting_type
            FROM meetings m
            WHERE {where_clause}
            ORDER BY m.date DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = query.limit
        params["offset"] = query.offset

        rows = self._session.execute(text(sql), params).fetchall()

        results: list[SearchResult] = []
        for row in rows:
            meeting_id, title, date_val, meeting_type = row
            rank = fts_meeting_ids.get(meeting_id, 0.0)

            # Convert date
            if isinstance(date_val, str):
                try:
                    date_val = datetime.fromisoformat(date_val)
                except Exception:
                    date_val = datetime.now(timezone.utc)

            results.append(
                SearchResult(
                    meeting_id=meeting_id,
                    title=title or "",
                    date=date_val,
                    meeting_type=meeting_type or "other",
                    snippet=self._get_snippet(meeting_id, query.fts_query),
                    rank=float(rank or 0.0),
                )
            )

        # Sort by BM25 rank when FTS was used (lower rank = better match in SQLite FTS5)
        if fts_meeting_ids:
            results.sort(key=lambda r: r.rank)

        # Count total
        count_sql = f"SELECT COUNT(*) FROM meetings m WHERE {where_clause}"
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
        total_count = self._session.execute(text(count_sql), count_params).scalar() or 0

        return SearchResults(
            results=results,
            total_count=int(total_count),
            query=query,
        )

    def _get_snippet(self, meeting_id: str, fts_query: str) -> str:
        """Return a short snippet for the meeting."""
        if not fts_query:
            return ""
        try:
            sql = """
                SELECT snippet(meetings_fts, 2, '<b>', '</b>', '...', 20)
                FROM meetings_fts
                WHERE meeting_id = :mid AND meetings_fts MATCH :q
                LIMIT 1
            """
            row = self._session.execute(
                text(sql), {"mid": meeting_id, "q": fts_query}
            ).fetchone()
            return row[0] if row else ""
        except Exception:
            return ""

    def reindex_meeting(
        self,
        meeting_id: str,
        *,
        data_dir: Path | None = None,
    ) -> None:
        """Reindex a meeting in the FTS table.

        Attachment extracted text is concatenated into the ``minutes_text``
        column so keyword search hits the body of attached PDFs / DOCX /
        OCR'd images, not just the LLM's summary of them. We don't add a
        new FTS column — that would require a schema migration to drop
        and recreate the virtual table — and the visible search snippet
        is fine carrying attachment content (it tells the user where
        the match came from).

        ``data_dir`` is optional: when omitted, attachment indexing is
        skipped (used by tests that don't care about attachments).
        Production callers go through :class:`MinutesIngester` which
        passes ``data_dir`` explicitly.
        """
        meeting = self._session.get(MeetingORM, meeting_id)
        if meeting is None:
            return

        transcript_text = ""
        if meeting.transcript:
            transcript_text = meeting.transcript.full_text or ""

        minutes_text = ""
        if meeting.minutes:
            minutes_text = meeting.minutes.markdown_content or ""

        if data_dir is not None:
            attachment_text = _gather_attachment_text_for_fts(data_dir, meeting_id)
            if attachment_text:
                minutes_text = (
                    f"{minutes_text}\n\n--- ATTACHMENT BODIES ---\n\n"
                    f"{attachment_text}"
                )

        self._session.execute(
            text("DELETE FROM meetings_fts WHERE meeting_id = :mid"),
            {"mid": meeting_id},
        )
        self._session.execute(
            text(
                "INSERT INTO meetings_fts(meeting_id, title, transcript_text, minutes_text) "
                "VALUES (:mid, :title, :tt, :mt)"
            ),
            {
                "mid": meeting_id,
                "title": meeting.title or "",
                "tt": transcript_text,
                "mt": minutes_text,
            },
        )
        self._session.commit()

    def remove_from_index(self, meeting_id: str) -> None:
        """Remove a meeting from FTS index."""
        self._session.execute(
            text("DELETE FROM meetings_fts WHERE meeting_id = :mid"),
            {"mid": meeting_id},
        )
        self._session.commit()
