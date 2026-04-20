"""Export meeting minutes to PDF / DOCX / Markdown (EXP-1).

The sub-modules import their heavy dependencies lazily so a missing
native library (e.g. libpango for WeasyPrint) doesn't break ``mm`` at
import time. The API layer catches ``ExportDependencyMissing`` and
surfaces a 501 with an actionable install hint.

Public surface
--------------
``export(meeting, *, format, with_transcript=False)`` returns
``(filename, bytes, content_type)``. ``format`` is one of
``"pdf" | "docx" | "md"``.

``ExportDependencyMissing`` — raised when the native library / wheel
for the requested format isn't importable. The message is safe to show
to end users.

Users can customise the DOCX output by dropping a template at
``templates/export/docx_template.docx``; python-docx will inherit the
paragraph/heading styles from that file. The PDF renderer uses the
stylesheet at ``templates/export/pdf.css``; see ``pdf.py`` for the
rendered section structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from meeting_minutes.system3.db import MeetingORM


class ExportDependencyMissing(RuntimeError):
    """Raised when the native dep for a given export format is unavailable.

    The message is meant to be forwarded verbatim to end users (API 501
    body, CLI ``typer.echo(err=True)``) — keep it short and actionable.
    """


ExportFormat = Literal["pdf", "docx", "md"]


@dataclass
class ExportResult:
    filename: str
    content: bytes
    content_type: str


def slugify(value: str) -> str:
    """Conservative slugifier for filenames — ASCII, lowercase, hyphens."""
    value = (value or "meeting").strip()
    value = re.sub(r"[^A-Za-z0-9]+", "-", value)
    value = value.strip("-").lower()
    return value[:80] or "meeting"


def default_filename(meeting: MeetingORM, *, ext: str) -> str:
    """``{YYYY-MM-DD}_{slug(title)}.{ext}`` — the canonical default path."""
    date_part = meeting.date.strftime("%Y-%m-%d") if meeting.date else "undated"
    slug = slugify(meeting.title or meeting.meeting_id)
    return f"{date_part}_{slug}.{ext}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def export(
    meeting: MeetingORM,
    *,
    format: ExportFormat,
    with_transcript: bool = False,
) -> ExportResult:
    """Render ``meeting`` in the requested format.

    Raises ``ExportDependencyMissing`` if the native backend (WeasyPrint
    for PDF, python-docx for DOCX) is missing. Raises ``ValueError`` for
    an unknown format or a meeting with no minutes.
    """
    if meeting.minutes is None or not (meeting.minutes.markdown_content or "").strip():
        raise ValueError("No minutes available — generate minutes before exporting.")

    if format == "md":
        content = _render_markdown(meeting, with_transcript=with_transcript).encode("utf-8")
        return ExportResult(
            filename=default_filename(meeting, ext="md"),
            content=content,
            content_type="text/markdown; charset=utf-8",
        )
    if format == "pdf":
        from meeting_minutes.export.pdf import render_pdf

        content = render_pdf(meeting, with_transcript=with_transcript)
        return ExportResult(
            filename=default_filename(meeting, ext="pdf"),
            content=content,
            content_type="application/pdf",
        )
    if format == "docx":
        from meeting_minutes.export.docx import render_docx

        content = render_docx(meeting, with_transcript=with_transcript)
        return ExportResult(
            filename=default_filename(meeting, ext="docx"),
            content=content,
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )
    raise ValueError(f"Unknown export format: {format!r}")


def _render_markdown(meeting: MeetingORM, *, with_transcript: bool) -> str:
    """Return the meeting's stored markdown, optionally with full transcript."""
    md = (meeting.minutes.markdown_content or "").rstrip() + "\n"
    if with_transcript and meeting.transcript is not None and meeting.transcript.full_text:
        md += "\n---\n\n## Full Transcript\n\n"
        md += meeting.transcript.full_text.strip() + "\n"
    return md


__all__ = [
    "ExportDependencyMissing",
    "ExportFormat",
    "ExportResult",
    "default_filename",
    "export",
    "slugify",
]
