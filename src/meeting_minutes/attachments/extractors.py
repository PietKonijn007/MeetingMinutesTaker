"""Per-kind text extraction.

This batch ships PDF text-layer extraction only. OCR (for images and for
scanned PDFs where the text-layer is empty), DOCX, PPTX, and link-fetch
land in the next batch — see ``specs/09-attachments.md`` for the table.

Each extractor returns ``(extracted_text, extraction_method)``. The method
string is recorded in the sidecar frontmatter so future readers can tell
*how* the text came to be — useful for debugging and for the summarizer
prompt (OCR output should be treated more skeptically than parsed text).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger(__name__)


class ExtractionError(RuntimeError):
    """Raised when extraction fails for any reason. Worker turns this into
    a sidecar ``summary_status: error`` and a DB ``status='error'``.
    """


def extract(path: Path, mime_type: str | None) -> tuple[str, str]:
    """Dispatch to the right extractor by mime type.

    Falls back on file extension when ``mime_type`` is missing or
    unrecognized — clients sometimes upload PDFs with a generic
    ``application/octet-stream``.
    """
    handler = _resolve_handler(path, mime_type)
    if handler is None:
        raise ExtractionError(
            f"No extractor for mime={mime_type!r} ext={path.suffix!r}"
        )
    return handler(path)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _resolve_handler(
    path: Path, mime_type: str | None
) -> Callable[[Path], tuple[str, str]] | None:
    if mime_type == "application/pdf" or path.suffix.lower() == ".pdf":
        return extract_pdf
    return None


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def extract_pdf(path: Path) -> tuple[str, str]:
    """Extract text from a PDF's text layer.

    Returns ``(text, "pdf-text-layer")``. Pages are joined with a clear
    ``--- Page N ---`` separator so the summarizer can refer to specific
    pages later.

    If the text layer is empty or near-empty (a scanned PDF), the OCR
    fallback in the next batch will take over. For now we surface what
    pypdf gives us — possibly empty — and log a warning. Empty extractions
    still produce a valid sidecar; the summarizer will note "no text
    extracted" when it runs.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError(
            "pypdf is not installed. Run `pip install pypdf>=4.0.0` "
            "or reinstall the project to pick up the new dependency."
        ) from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF: {exc}") from exc

    if reader.is_encrypted:
        # Try the empty password — some PDFs are "encrypted" with no
        # password, which pypdf surfaces as is_encrypted=True until you
        # decrypt with "". If that fails, give up cleanly.
        try:
            reader.decrypt("")
        except Exception:
            raise ExtractionError("PDF is password-protected; cannot extract.")

    pages: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            _LOG.warning("PDF %s: page %d extraction failed: %s", path.name, idx, exc)
            page_text = ""
        if page_text.strip():
            pages.append(f"--- Page {idx} ---\n{page_text.strip()}")

    body = "\n\n".join(pages)
    if not body.strip():
        _LOG.info(
            "PDF %s: text layer is empty — likely scanned, awaiting OCR fallback.",
            path.name,
        )
    return body, "pdf-text-layer"
