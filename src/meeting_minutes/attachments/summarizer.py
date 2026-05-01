"""LLM-driven attachment summarizer (spec/09-attachments.md §4.3).

Runs as its own LLM call — separate from minutes generation — so the
summary can land while the meeting is still being recorded. Output is
plain markdown that goes straight into the sidecar's ``## Summary``
section and (in turn) into the minutes-generation prompt as grounded
context.

Tier picking is deterministic from extracted-text length so the user
sees consistent treatment for similar inputs. Map-reduce for >100k
chars is in the spec but lands in a follow-up batch — for now we cap
oversized inputs and log a warning so the operator knows the summary
is incomplete.

Critical-rule contract baked into the system prompt: numbers, dates,
and proper nouns must be quoted verbatim from the source. The whole
point of attaching source material is to ground the LLM, so a
paraphrased "$13M" where the slide says "$12.7M" defeats the feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from meeting_minutes.system2.llm_client import LLMClient

_LOG = logging.getLogger(__name__)

# Hard ceiling on extracted text we send to the LLM in a single shot.
# Anything past this gets truncated with a clear marker so the model
# knows the body is incomplete. Map-reduce summarization for the
# overflow ships in a later batch.
_MAX_SINGLE_SHOT_CHARS = 100_000


class SummaryTier(str, Enum):
    """Length tier picked from extracted-text size. Stored on the sidecar
    frontmatter as ``summary_target`` so the UI can show the user what
    treatment was applied."""

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    XLONG = "xlong"


# (lower_inclusive, tier) — first match wins. The thresholds match the
# table in spec §4.3; keep them in sync.
_TIER_THRESHOLDS: list[tuple[int, SummaryTier]] = [
    (30_000, SummaryTier.XLONG),
    (5_000, SummaryTier.LONG),
    (500, SummaryTier.MEDIUM),
    (0, SummaryTier.SHORT),
]


def pick_tier(extracted_text: str) -> SummaryTier:
    """Pick a length tier based on extracted-text size."""
    n = len(extracted_text or "")
    for lower, tier in _TIER_THRESHOLDS:
        if n >= lower:
            return tier
    return SummaryTier.SHORT


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You are summarizing a document that was attached to a meeting. The summary will be shown to readers reviewing the meeting minutes, and will also be fed into the prompt that generates those minutes — so it must be accurate, grounded in the source, and faithful to specifics.

CRITICAL RULES (non-negotiable):
- Do not paraphrase numbers, dates, percentages, or money amounts. Quote them exactly as they appear in the source. If the source says "$12.7M", write "$12.7M" — not "around $13M" and not "twelve million dollars".
- Do not paraphrase proper nouns (names of people, products, companies, projects, places). Spell them as they appear.
- Do not invent content. If a section is unclear, say so rather than guessing.
- When you quote the source verbatim, mark the quote with quotation marks.
- Output plain markdown only. No code fences. No frontmatter. No JSON envelope. No preamble like "Here is the summary:".

The user provides a title and an optional caption explaining why this material was attached to the meeting; let that guide what you emphasize."""


_TIER_INSTRUCTIONS: dict[SummaryTier, str] = {
    SummaryTier.SHORT: (
        "Produce a CONCISE summary in 2 to 4 sentences, as a single paragraph. "
        "No headings, no bullets — just prose. This is for a screenshot or short snippet."
    ),
    SummaryTier.MEDIUM: (
        "Produce a MODERATE summary of approximately 100 to 200 words. "
        "Lead with one paragraph capturing the gist. If the document is structured "
        "(slides, sections, or clear topics), follow the paragraph with a short bullet "
        "list of the key points. No subheadings."
    ),
    SummaryTier.LONG: (
        "Produce a DETAILED summary of approximately 250 to 500 words. "
        "Organize the summary with `###` subheadings that match the document's own "
        "structure (chapters, sections, slide groups, agenda items). Under each "
        "subheading, write a short paragraph or a bullet list. Preserve the original "
        "structure rather than imposing a different one."
    ),
    SummaryTier.XLONG: (
        "Produce a COMPREHENSIVE summary of approximately 500 to 800 words. "
        "Organize with `###` subheadings matching the document's own structure. "
        "Under each subheading, write a short paragraph followed by bullet highlights "
        "of the specific facts, numbers, and decisions in that section. "
        "End the summary with two extra subsections — `### Key numbers` (verbatim "
        "list of the most important numeric facts) and `### Key entities` (verbatim "
        "list of the most important people, products, and organizations). "
        "Skip either if not applicable."
    ),
}


@dataclass(frozen=True)
class SummaryRequest:
    """Inputs to a single summarization call."""

    title: str
    caption: str | None
    source: str  # filename or URL
    extraction_method: str
    extracted_text: str


@dataclass(frozen=True)
class SummaryResult:
    """Output of a single summarization call."""

    summary_markdown: str
    tier: SummaryTier
    truncated: bool  # True when extracted_text was capped before sending


def build_summary_prompt(req: SummaryRequest) -> tuple[str, str, SummaryTier, bool]:
    """Build (system_prompt, user_prompt, tier, was_truncated).

    Pulled out as its own function so tests can assert on the prompt
    structure without standing up an LLM client.
    """
    tier = pick_tier(req.extracted_text)
    body, truncated = _maybe_truncate(req.extracted_text)

    caption_block = (
        f"Caption (why this was attached): {req.caption}\n"
        if req.caption
        else ""
    )
    method_block = (
        f"Extraction method: {req.extraction_method} "
        f"(treat OCR output more skeptically than parsed text — letters can be wrong)\n"
        if req.extraction_method == "ocr"
        else ""
    )
    truncation_note = (
        "\n\nNOTE: The document text below has been truncated because it exceeds "
        f"{_MAX_SINGLE_SHOT_CHARS:,} characters. Summarize what is shown and "
        "explicitly note that the document is longer than what you saw."
        if truncated
        else ""
    )

    user_prompt = (
        f"Source: {req.source}\n"
        f"Title: {req.title}\n"
        f"{caption_block}"
        f"{method_block}"
        f"\n"
        f"{_TIER_INSTRUCTIONS[tier]}"
        f"{truncation_note}\n\n"
        f"--- BEGIN DOCUMENT ---\n"
        f"{body}\n"
        f"--- END DOCUMENT ---\n"
    )
    return _SYSTEM_PROMPT, user_prompt, tier, truncated


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


async def summarize_attachment(
    llm: LLMClient,
    req: SummaryRequest,
) -> SummaryResult:
    """Run one LLM summarization call. Returns markdown + tier.

    Raises whatever ``LLMClient.generate`` raises — the worker catches
    those and records the error on the row + sidecar. Empty extracted
    text short-circuits to a placeholder summary; calling the LLM with
    nothing to summarize wastes tokens.
    """
    if not (req.extracted_text or "").strip():
        _LOG.info(
            "Empty extracted text for attachment titled %r — skipping LLM call",
            req.title,
        )
        return SummaryResult(
            summary_markdown=(
                "_Could not extract text from this attachment._ "
                "The minutes generator will not have any content to draw from "
                "for this item; consider re-uploading or attaching a text-bearing "
                "version."
            ),
            tier=SummaryTier.SHORT,
            truncated=False,
        )

    system_prompt, user_prompt, tier, truncated = build_summary_prompt(req)
    response = await llm.generate(prompt=user_prompt, system_prompt=system_prompt)
    return SummaryResult(
        summary_markdown=response.text.strip(),
        tier=tier,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _maybe_truncate(text: str) -> tuple[str, bool]:
    """Cap extracted text at the single-shot limit, with a clear marker."""
    if len(text) <= _MAX_SINGLE_SHOT_CHARS:
        return text, False
    truncated = text[:_MAX_SINGLE_SHOT_CHARS]
    return truncated + "\n\n[... document truncated ...]", True
