"""Render meeting minutes to PDF via markdown-it-py → WeasyPrint (EXP-1).

WeasyPrint requires native libpango + cairo; on macOS install via:
    brew install pango cairo gdk-pixbuf libffi
Then ``DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib mm serve``. The
import is deferred so the rest of ``meeting_minutes`` keeps working on
systems without pango.
"""

from __future__ import annotations

from pathlib import Path

from meeting_minutes.export import ExportDependencyMissing
from meeting_minutes.system3.db import MeetingORM


def _require_weasyprint():
    """Lazy-import WeasyPrint, translating ImportError / OSError into a
    friendly :class:`ExportDependencyMissing`.
    """
    try:
        from weasyprint import CSS, HTML  # type: ignore
    except ImportError as exc:  # pragma: no cover - package missing
        raise ExportDependencyMissing(
            "Install weasyprint to enable PDF export: pip install weasyprint"
        ) from exc
    except OSError as exc:  # native libpango / cairo missing
        raise ExportDependencyMissing(
            "WeasyPrint is installed but its native libraries are missing. "
            "On macOS run: brew install pango cairo gdk-pixbuf libffi "
            "(original error: " + str(exc) + ")"
        ) from exc
    return HTML, CSS


def _require_markdown_it():
    try:
        from markdown_it import MarkdownIt  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ExportDependencyMissing(
            "Install markdown-it-py to enable PDF export: pip install markdown-it-py"
        ) from exc
    return MarkdownIt


def _stylesheet_path() -> Path:
    """Locate the packaged pdf.css. Returns None-like path if absent."""
    # templates/export/pdf.css sits at the project root.
    return Path(__file__).resolve().parent.parent.parent.parent / "templates" / "export" / "pdf.css"


def _build_html(meeting: MeetingORM, *, with_transcript: bool) -> str:
    """Convert stored markdown + optional transcript into a self-contained HTML doc."""
    MarkdownIt = _require_markdown_it()
    md = MarkdownIt("commonmark", {"html": False, "linkify": True})

    minutes_md = (meeting.minutes.markdown_content or "").strip()
    body_html = md.render(minutes_md)

    transcript_html = ""
    if with_transcript and meeting.transcript is not None and meeting.transcript.full_text:
        # Render transcript as a single preformatted block so formatting doesn't
        # throw off the CommonMark parser (transcripts contain arbitrary chars).
        from html import escape

        transcript_html = (
            '<section class="mm-transcript">'
            "<h2>Full Transcript</h2>"
            f"<pre>{escape(meeting.transcript.full_text.strip())}</pre>"
            "</section>"
        )

    title = (meeting.title or "Meeting").strip()
    date_str = meeting.date.strftime("%Y-%m-%d") if meeting.date else ""
    attendees = ", ".join(a.name for a in (meeting.attendees or []) if a and a.name)
    duration = meeting.duration or ""

    meta_bits = []
    if date_str:
        meta_bits.append(f"<strong>Date:</strong> {date_str}")
    if duration:
        meta_bits.append(f"<strong>Duration:</strong> {duration}")
    if attendees:
        meta_bits.append(f"<strong>Attendees:</strong> {attendees}")

    from html import escape

    meta_html = " &nbsp;·&nbsp; ".join(meta_bits)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)}</title>
</head>
<body>
  <h1>{escape(title)}</h1>
  <p class="mm-meta" data-date="{escape(date_str)}">{meta_html}</p>
  {body_html}
  {transcript_html}
</body>
</html>
"""


def render_pdf(meeting: MeetingORM, *, with_transcript: bool = False) -> bytes:
    """Render ``meeting`` to a PDF byte stream."""
    HTML, CSS = _require_weasyprint()
    html = _build_html(meeting, with_transcript=with_transcript)

    css_path = _stylesheet_path()
    css_args = [CSS(filename=str(css_path))] if css_path.exists() else []
    return HTML(string=html).write_pdf(stylesheets=css_args)
