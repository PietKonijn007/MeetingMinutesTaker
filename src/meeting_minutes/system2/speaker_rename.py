"""Infer human names for diarized speaker labels using external notes.

When the user pastes notes from a meeting app (Teams / Zoom / Meet / Otter /
etc.), those notes typically know who said what far better than local
diarization — they've had access to the participant list and, often, active
speaker detection. This module asks the LLM to produce a
``SPEAKER_xx -> Name`` mapping by comparing a short transcript sample to the
external notes.

Design notes:
- We only ask the LLM about *generic* labels (``SPEAKER_\\d+``). Labels that
  have already been renamed to a human name are treated as authoritative and
  left alone — the caller is expected to filter them out before passing
  ``current_labels`` in.
- The LLM returns a JSON object. Anything else (hallucinated commentary, no
  JSON, malformed JSON) → we return an empty mapping and let the caller carry
  on with the original labels. Silent degradation beats partial / wrong
  mappings.
- Low temperature: we want the model to stick to what's actually supported by
  the evidence, not to invent plausible-sounding names.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from meeting_minutes.system2.llm_client import LLMClient

_LOG = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a precise assistant that maps anonymous diarization labels "
    "(SPEAKER_00, SPEAKER_01, ...) to human names by cross-referencing a "
    "meeting-app's notes/transcript. Respond ONLY with a single JSON object "
    "mapping each label to the human name you are confident about. If you "
    "cannot determine a label with confidence, omit it entirely. Do not "
    "guess. Do not include commentary, markdown fences, or any prose."
)

_USER_PROMPT_TEMPLATE = """\
Below is (A) a diarized transcript sample where each turn starts with an
anonymous label, plus one or both of these supplementary sources:
  (B) external notes exported from the meeting app — these usually
      attribute utterances to real names or list the participants;
  (C) summaries of materials attached to the meeting (slide decks,
      docs, links) — title-slide presenter names, "prepared by"
      footers, and explicit attendee lists are common signals.

Your job: produce a JSON object that maps every anonymous label listed in
"Labels to map" to the real name of the person behind it, based on the
evidence in the supplementary sources and the transcript.

Rules:
- Output ONLY a JSON object, no prose, no code fences.
- Keys must be exactly the labels listed in "Labels to map".
- Omit any label you cannot confidently identify. An empty object {{}} is a
  valid answer.
- Names must be the person's name as it appears in the supplementary
  sources (preserve capitalization).
- Do NOT invent names that do not appear in the supplementary sources.
- Attachments are weaker evidence than external notes for attribution
  (the deck author isn't necessarily a meeting participant). Use them
  to confirm or as a tie-breaker, not as the sole basis for a mapping.

Labels to map: {labels}

=== A. Diarized transcript sample ===
{transcript_sample}

=== B. External notes from the meeting app ===
{external_notes}

=== C. Attached materials (summaries) ===
{attachment_context}

Respond with the JSON object only.
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of ``text``.

    The LLM is instructed to return pure JSON, but we defensively handle the
    case where it wraps the object in markdown fences or adds a sentence of
    prose. Returns ``None`` if no JSON object can be parsed.
    """
    text = (text or "").strip()
    if not text:
        return None
    # Try the whole string first — the happy path.
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Fallback: grab the first {...} block and parse that.
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clean_mapping(
    raw: dict,
    allowed_labels: Iterable[str],
) -> dict[str, str]:
    """Reduce the LLM output to a valid ``{label: name}`` mapping.

    Drops pairs where the value is not a non-empty string, or where the key is
    not one of the labels we asked about. Keeps the interface defensive — the
    downstream rewrite code trusts whatever we return here.
    """
    allowed = set(allowed_labels)
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in allowed:
            continue
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name:
            continue
        out[key] = name
    return out


async def infer_speaker_names(
    llm: LLMClient,
    current_labels: list[str],
    transcript_sample: str,
    external_notes: str,
    attachment_context: str = "",
) -> dict[str, str]:
    """Ask the LLM to map generic diarization labels to human names.

    Parameters
    ----------
    llm
        A configured :class:`LLMClient`. Uses :meth:`LLMClient.generate` — the
        plain text path, not structured/tool-use — because the expected output
        is a tiny flat JSON object and structured generation would be
        overkill.
    current_labels
        The ``SPEAKER_xx`` labels we still need to identify. Callers should
        filter out labels that already carry human names.
    transcript_sample
        A string of the form ``LABEL: text\\nLABEL: text\\n...`` drawn from
        the current transcript. A few thousand characters is plenty — more
        just burns tokens.
    external_notes
        The verbatim paste from the meeting app, or ``""`` when not
        available. May be empty if attachment_context carries the signal.
    attachment_context
        Per-attachment summaries (title + body) for materials attached to
        the meeting. Title-slide presenter names and explicit attendee
        lists are useful tie-breakers; the LLM is told to weight this
        weaker than external_notes since attachment authors aren't
        always participants.

    Returns
    -------
    dict[str, str]
        Mapping from label to inferred human name. Always safe to pass into a
        speaker-rewrite routine — invalid entries are stripped. Returns ``{}``
        on any failure (LLM error, malformed JSON, no confident matches). The
        caller should treat an empty mapping as "no change" and move on.
    """
    # Fast-path: nothing to infer, or no supplementary signal to draw on.
    if not current_labels:
        return {}
    if not external_notes.strip() and not attachment_context.strip():
        return {}

    prompt = _USER_PROMPT_TEMPLATE.format(
        labels=", ".join(current_labels),
        transcript_sample=(transcript_sample or "").strip() or "(no transcript sample)",
        external_notes=external_notes.strip() or "(none provided)",
        attachment_context=attachment_context.strip() or "(none provided)",
    )

    try:
        resp = await llm.generate(prompt=prompt, system_prompt=_SYSTEM_PROMPT)
    except Exception as exc:  # pragma: no cover - provider failures are logged, not raised
        _LOG.warning("Speaker-name inference LLM call failed: %s", exc)
        return {}

    parsed = _extract_json_object(resp.text)
    if parsed is None:
        _LOG.info("Speaker-name inference produced no parsable JSON; skipping rename")
        return {}

    mapping = _clean_mapping(parsed, current_labels)
    if not mapping:
        _LOG.info("Speaker-name inference returned no confident matches")
    else:
        _LOG.info(
            "Speaker-name inference mapped %d/%d labels: %s",
            len(mapping),
            len(current_labels),
            mapping,
        )
    return mapping


def build_transcript_sample(
    segments: list[dict],
    max_chars: int = 3000,
) -> str:
    """Render diarized segments as ``LABEL: text`` lines, capped at ``max_chars``.

    Used as the ``transcript_sample`` argument to :func:`infer_speaker_names`.
    Truncates mid-segment if needed — the LLM doesn't need the full
    transcript, just enough turns to correlate with the external notes.
    """
    if not segments:
        return ""
    # Stable chronological order.
    ordered = sorted(segments, key=lambda s: s.get("start") or 0)

    lines: list[str] = []
    total = 0
    for seg in ordered:
        speaker = (seg.get("speaker") or "UNKNOWN").strip()
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        line = f"{speaker}: {text}"
        if total + len(line) + 1 > max_chars:
            remaining = max_chars - total
            if remaining > 20:  # only append a partial line if it's worth reading
                lines.append(line[:remaining] + "…")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)
