"""Per-attachment sidecar markdown read/write.

Each attachment owns a single markdown file at
``data/attachments/{meeting_id}/{attachment_id}.md``. Structure:

.. code-block:: markdown

    ---
    attachment_id: ...
    meeting_id: ...
    kind: file|link|image
    title: ...
    source: filename or URL
    extracted_at: ISO8601
    extraction_method: pdf-text-layer | ocr | docx | pptx | link-trafilatura
    summary_status: pending | ready | error
    summary_target: short | medium | long | xlong
    ---

    ## Summary

    <LLM-generated summary — empty until the summarizer batch lands>

    ## Extracted content

    <verbatim extracted text>

The sidecar is the canonical context fed into minutes generation (in a
follow-up batch). Keeping it on disk as markdown — not in the DB — means
it survives DB rebuilds and can be hand-edited if a summary is wrong.

This module is deliberately dependency-free (no YAML lib): the frontmatter
is a small fixed key-value set we can parse with the stdlib. Frontmatter
values are strings; callers coerce as needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_FRONTMATTER_DELIM = "---"
_SUMMARY_HEADER = "## Summary"
_EXTRACTED_HEADER = "## Extracted content"


@dataclass(frozen=True)
class AttachmentSidecar:
    """In-memory representation of a sidecar markdown file."""

    frontmatter: dict[str, str]
    summary: str
    extracted: str


def write_attachment_sidecar(
    path: Path,
    *,
    frontmatter: dict[str, str],
    extracted: str,
    summary: str = "",
) -> None:
    """Write a sidecar markdown atomically (temp-file + rename).

    The summary section is always present (empty until the summarizer
    fills it in) so future readers can rely on the structure.
    """
    body_lines: list[str] = [_FRONTMATTER_DELIM]
    for key, value in frontmatter.items():
        body_lines.append(f"{key}: {value}")
    body_lines.append(_FRONTMATTER_DELIM)
    body_lines.append("")
    body_lines.append(_SUMMARY_HEADER)
    body_lines.append("")
    body_lines.append(summary.rstrip() if summary else "")
    body_lines.append("")
    body_lines.append(_EXTRACTED_HEADER)
    body_lines.append("")
    body_lines.append(extracted.rstrip() if extracted else "")
    body_lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(body_lines), encoding="utf-8")
    tmp.replace(path)


def parse_attachment_sidecar(path: Path) -> AttachmentSidecar:
    """Parse a sidecar file. Tolerant of missing sections.

    Returns an empty :class:`AttachmentSidecar` when the file doesn't
    exist — callers typically poll the sidecar while the worker is still
    extracting, and a not-yet-written file is normal.
    """
    if not path.exists():
        return AttachmentSidecar(frontmatter={}, summary="", extracted="")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    frontmatter, body_start = _parse_frontmatter(lines)
    summary, extracted = _split_sections(lines[body_start:])
    return AttachmentSidecar(
        frontmatter=frontmatter,
        summary=summary.strip(),
        extracted=extracted.strip(),
    )


def update_summary(
    path: Path,
    *,
    summary: str,
    summary_status: str,
    summary_target: str | None = None,
    error: str | None = None,
) -> None:
    """Replace the ``## Summary`` section and update ``summary_status``.

    Used by the worker once the summarizer call returns. We re-write the
    whole file so the operation is atomic — the alternative (in-place edit
    of just the summary section) is fragile and harder to make atomic.
    """
    sidecar = parse_attachment_sidecar(path)
    fm = dict(sidecar.frontmatter)
    fm["summary_status"] = summary_status
    if summary_target is not None:
        fm["summary_target"] = summary_target
    if error is not None:
        fm["summary_error"] = error
    else:
        fm.pop("summary_error", None)
    write_attachment_sidecar(
        path,
        frontmatter=fm,
        extracted=sidecar.extracted,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Return (frontmatter_dict, index_of_first_body_line).

    Tolerates: missing frontmatter (returns empty dict), trailing
    whitespace, and lines without colons (skipped). Does **not** support
    multiline values — keep frontmatter to single-line scalars.
    """
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}, 0
    fm: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_DELIM:
            return fm, i + 1
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    # No closing delimiter — treat the whole file as frontmatter and the
    # body as empty. Defensive: this file is internal, but readers should
    # not crash on malformed input.
    return fm, len(lines)


def _split_sections(body_lines: Iterable[str]) -> tuple[str, str]:
    """Split body into (summary_section, extracted_section).

    Sections are delimited by ``## Summary`` and ``## Extracted content``
    in that order. Anything before the first known header is discarded.
    """
    summary_buf: list[str] = []
    extracted_buf: list[str] = []
    current: list[str] | None = None

    for line in body_lines:
        stripped = line.strip()
        if stripped == _SUMMARY_HEADER:
            current = summary_buf
            continue
        if stripped == _EXTRACTED_HEADER:
            current = extracted_buf
            continue
        if current is not None:
            current.append(line)

    return "\n".join(summary_buf), "\n".join(extracted_buf)
