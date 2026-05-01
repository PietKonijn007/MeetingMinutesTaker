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

# Above this size, switch from a single-shot LLM call to a map-reduce
# pass: chunk the document, summarize each chunk, then summarize the
# chunk summaries to land within the xlong target.
_MAX_SINGLE_SHOT_CHARS = 100_000

# Window size for each chunk in the map step. ~30k chars is a comfortable
# middle ground: small enough that even small models keep their grip on
# detail, large enough that we don't blow up the chunk count for a
# 500-page PDF (which would land at ~500/30 ≈ 17 chunks).
_MAP_CHUNK_CHARS = 30_000

# Hard ceiling on the number of chunks we summarize. A book-length PDF
# (>500 pages) caps out at this many chunks; anything beyond gets
# truncated with a marker on the final summary. This also bounds LLM
# spend per attachment to a known maximum.
_MAX_MAP_CHUNKS = 20


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
        "Organize the summary with `### Subheading` headers that match the document's own "
        "structure (chapters, sections, slide groups, agenda items). Under each "
        "subheading, write a short paragraph or a bullet list. Preserve the original "
        "structure rather than imposing a different one.\n\n"
        "FORMATTING — IMPORTANT:\n"
        "- Use `### Heading` syntax for each top-level section. Do NOT use "
        "`**Heading**:` bold-prefix-with-colon — that renders as inline prose, "
        "not a real heading.\n"
        "- Bold (`**text**`) is for emphasis WITHIN a paragraph or bullet, not "
        "for labelling sections.\n"
        "- Each `###` heading should be on its own line with a blank line "
        "before and after."
    ),
    SummaryTier.XLONG: (
        "Produce a COMPREHENSIVE summary of approximately 500 to 800 words. "
        "Organize with `### Subheading` headers matching the document's own structure. "
        "Under each subheading, write a short paragraph followed by bullet highlights "
        "of the specific facts, numbers, and decisions in that section. "
        "End the summary with two extra subsections — `### Key numbers` (verbatim "
        "list of the most important numeric facts) and `### Key entities` (verbatim "
        "list of the most important people, products, and organizations). "
        "Skip either if not applicable.\n\n"
        "FORMATTING — IMPORTANT:\n"
        "- Use `### Heading` syntax for each top-level section. Do NOT use "
        "`**Heading**:` bold-prefix-with-colon — that renders as inline prose, "
        "not a real heading.\n"
        "- Bold (`**text**`) is for emphasis WITHIN a paragraph or bullet, not "
        "for labelling sections.\n"
        "- Each `###` heading should be on its own line with a blank line "
        "before and after."
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


def build_summary_prompt(
    req: SummaryRequest,
    *,
    tier_override: SummaryTier | None = None,
) -> tuple[str, str, SummaryTier, bool]:
    """Build (system_prompt, user_prompt, tier, was_truncated).

    Pulled out as its own function so tests can assert on the prompt
    structure without standing up an LLM client. ``tier_override`` lets
    the map-reduce reduce step demand an xlong synthesis even when the
    chunk-summary input would otherwise pick a shorter tier.
    """
    tier = tier_override or pick_tier(req.extracted_text)
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
    """Run summarization. Returns markdown + tier.

    Dispatches to a single-shot LLM call when the extracted text fits
    comfortably; otherwise runs a map-reduce pass (chunk, summarize
    each, summarize-the-summaries). Raises whatever the underlying
    ``LLMClient.generate`` raises — the worker catches those and
    records the error on the row + sidecar.
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

    if len(req.extracted_text) > _MAX_SINGLE_SHOT_CHARS:
        return await _summarize_via_map_reduce(llm, req)

    system_prompt, user_prompt, tier, truncated = build_summary_prompt(req)
    response = await llm.generate(prompt=user_prompt, system_prompt=system_prompt)
    return SummaryResult(
        summary_markdown=promote_bold_leads_to_headers(response.text.strip()),
        tier=tier,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Map-reduce
# ---------------------------------------------------------------------------


_MAP_CHUNK_INSTRUCTION = (
    "You are summarizing chunk {idx} of {total} from a longer document. "
    "Other chunks are being summarized separately and your output will be "
    "combined with theirs. Produce a focused, dense summary of THIS CHUNK "
    "only — do not speculate about content outside it. Keep numbers, dates, "
    "and proper nouns verbatim. Use markdown bullets for key points; one "
    "short paragraph of context first. ~150-300 words."
)

_REDUCE_PREAMBLE = (
    "You are producing the final summary of a long document. The text below "
    "is a sequence of per-chunk summaries (in document order) — your job is "
    "to weave them into a single coherent summary at the requested target "
    "length, without losing the verbatim numbers and proper nouns the "
    "chunks captured. Treat the chunk summaries as ground truth: do not "
    "invent content beyond them, but you may compress and re-order for "
    "narrative flow."
)


async def _summarize_via_map_reduce(
    llm: LLMClient,
    req: SummaryRequest,
) -> SummaryResult:
    """Two-phase summarization for very large attachments.

    Phase 1 (map): chunk the extracted text into ~30k char windows,
    summarize each independently. Bounded by ``_MAX_MAP_CHUNKS`` —
    anything beyond that gets dropped, and the final summary carries
    a "truncated" marker.

    Phase 2 (reduce): feed the per-chunk summaries to the LLM with the
    standard tier-based instruction so the final shape matches what a
    single-shot call would produce.

    Truncation in this path means "we couldn't summarize the whole
    document"; we still surface ``truncated=True`` on the result so
    the UI can flag it.
    """
    chunks = _split_into_chunks(req.extracted_text)
    truncated = len(chunks) > _MAX_MAP_CHUNKS
    if truncated:
        chunks = chunks[:_MAX_MAP_CHUNKS]

    # Map phase: summarize each chunk with a small, focused prompt.
    chunk_summaries: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        instruction = _MAP_CHUNK_INSTRUCTION.format(idx=idx, total=len(chunks))
        user_prompt = (
            f"Source: {req.source}\n"
            f"Title: {req.title}\n"
            f"{('Caption: ' + req.caption + chr(10)) if req.caption else ''}"
            f"\n{instruction}\n\n"
            f"--- BEGIN CHUNK {idx} ---\n{chunk}\n--- END CHUNK {idx} ---\n"
        )
        response = await llm.generate(
            prompt=user_prompt, system_prompt=_SYSTEM_PROMPT,
        )
        chunk_summaries.append(response.text.strip())
        _LOG.info(
            "Attachment %r: map chunk %d/%d summarized (%d chars in)",
            req.title, idx, len(chunks), len(chunk),
        )

    # Reduce phase: synthesize a final summary from the chunk summaries.
    # We hand the concatenated chunks to ``build_summary_prompt`` as the
    # extracted text — it picks the right tier based on length and emits
    # the same tier-style instruction the single-shot path uses, so the
    # output shape is consistent.
    reduced_input = (
        f"{_REDUCE_PREAMBLE}\n\n"
        + "\n\n".join(
            f"### Chunk {idx} summary\n{summary}"
            for idx, summary in enumerate(chunk_summaries, start=1)
        )
    )
    if truncated:
        reduced_input += (
            f"\n\n[NOTE: the source document is longer than what was summarized. "
            f"{_MAX_MAP_CHUNKS} chunks of ~{_MAP_CHUNK_CHARS} chars each were "
            f"processed; the tail of the document was skipped.]"
        )
    reduce_req = SummaryRequest(
        title=req.title,
        caption=req.caption,
        source=req.source,
        extraction_method=req.extraction_method,
        extracted_text=reduced_input,
    )
    system_prompt, user_prompt, _, _ = build_summary_prompt(
        reduce_req, tier_override=SummaryTier.XLONG,
    )
    response = await llm.generate(prompt=user_prompt, system_prompt=system_prompt)
    return SummaryResult(
        summary_markdown=promote_bold_leads_to_headers(response.text.strip()),
        tier=SummaryTier.XLONG,
        truncated=truncated,
    )


def _split_into_chunks(text: str) -> list[str]:
    """Split ``text`` into ~_MAP_CHUNK_CHARS windows, preferring paragraph breaks.

    Greedy: walk forward, accumulate paragraphs (split on blank lines)
    until adding the next one would push past the chunk size. Falls back
    to hard char-slicing for any single paragraph longer than the cap
    so we never hang on pathological input.
    """
    if not text:
        return []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        plen = len(para) + (2 if buf else 0)  # account for "\n\n" join
        if plen > _MAP_CHUNK_CHARS:
            # Pathologically long paragraph — flush whatever's buffered,
            # then hard-slice this one.
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_len = [], 0
            for i in range(0, len(para), _MAP_CHUNK_CHARS):
                chunks.append(para[i : i + _MAP_CHUNK_CHARS])
            continue
        if buf_len + plen > _MAP_CHUNK_CHARS:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [para], len(para)
        else:
            buf.append(para)
            buf_len += plen
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _maybe_truncate(text: str) -> tuple[str, bool]:
    """Cap extracted text at the single-shot limit, with a clear marker."""
    if len(text) <= _MAX_SINGLE_SHOT_CHARS:
        return text, False
    truncated = text[:_MAX_SINGLE_SHOT_CHARS]
    return truncated + "\n\n[... document truncated ...]", True


# Belt-and-braces: when a line is JUST a bold-prefix-with-colon at column 0
# ("**Foo**:" or "**Foo**:bar"), promote it to a real `### Foo` header so
# the rendered summary visually separates sections instead of running them
# together as inline prose. This catches the common LLM output style that
# slips through the FORMATTING-IMPORTANT block in the prompt.
import re as _re  # noqa: E402

_BOLD_LEAD_RE = _re.compile(r"^\*\*([^*\n]{1,80})\*\*\s*:\s*(.*)$")


def promote_bold_leads_to_headers(markdown: str) -> str:
    """Rewrite ``**Heading**: body…`` line starts into ``### Heading\\n\\nbody…``.

    Conservative — only fires when the bold-prefix is at the start of a
    line, contains < 80 chars (avoids matching long bolded sentences),
    and is followed by a colon. Preserves the body that came after the
    colon by moving it to its own paragraph beneath the new header.
    """
    if not markdown or "**" not in markdown:
        return markdown

    out: list[str] = []
    for line in markdown.splitlines():
        match = _BOLD_LEAD_RE.match(line)
        if match is None:
            out.append(line)
            continue
        heading = match.group(1).strip()
        body = match.group(2).strip()
        # A leading blank line between the previous block and the new
        # heading is required by markdown for the heading to render.
        if out and out[-1].strip():
            out.append("")
        out.append(f"### {heading}")
        if body:
            out.append("")
            out.append(body)
    return "\n".join(out)
