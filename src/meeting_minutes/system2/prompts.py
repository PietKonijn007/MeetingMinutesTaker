"""Prompt template engine using Jinja2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined

from meeting_minutes.models import MeetingContext, PromptTemplate


SYSTEM_PROMPT = """You are an expert meeting minutes assistant. Your task is to analyze meeting transcripts and produce clear, accurate, and well-structured meeting minutes.

Follow these guidelines:
- Be concise but comprehensive
- Preserve factual accuracy — only include information that was discussed
- Identify all action items with owners and deadlines when mentioned
- Capture decisions made during the meeting
- Use professional language appropriate for business documentation
- Format your response using the exact markdown structure requested"""

# Structured-output system prompt. The per-template system prompt (the text
# before `---` in each .md.j2) is prepended as type-specific framing.
STRUCTURED_SYSTEM_PROMPT = """You produce professional meeting minutes for a technology executive. Extract real content from the transcript — never fabricate. Apply these rules to every output:

1. TL;DR first. A ~100-word executive digest at the top, covering the biggest decision, biggest risk, most urgent action, and the single takeaway. No padding.
2. Omit empty sections. If a requested section has no real content from the transcript, skip the heading entirely. Do not write "Not discussed", "None", or any placeholder.
3. Attribute statements to named participants whenever the transcript makes it clear.
4. Distinguish decisions made vs. topics discussed.
5. Action items are verb-driven, one owner, deadline where stated.
6. Capture direct quotes for leader commitments, customer commitments, and performance-review-worthy moments.
7. Classify confidentiality conservatively (`public` | `internal` | `confidential` | `restricted`).
8. Populate every relevant structured field (tldr, summary, decisions, action_items, risks_and_concerns, open_questions, follow_ups, parking_lot, key_topics, prior_action_updates, email_draft, confidentiality, meeting_effectiveness, discussion_points, participants)."""


class _SafeUndefined(Undefined):
    """Undefined that renders as empty string instead of raising.

    Templates may reference optional context (vendors, length_mode,
    prior_actions, organizer) that the pipeline doesn't always supply.
    Treat those as empty rather than blowing up.
    """

    def __str__(self) -> str:
        return ""

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter([])

    def __len__(self) -> int:
        return 0


class PromptTemplateEngine:
    """Construct the full LLM prompt from a Jinja2 template + context + transcript."""

    def __init__(self, templates_dir: Path) -> None:
        self._templates_dir = templates_dir
        self._env: Environment | None = None

    def _get_env(self) -> Environment:
        if self._env is None:
            if self._templates_dir.exists():
                self._env = Environment(
                    loader=FileSystemLoader(str(self._templates_dir)),
                    undefined=_SafeUndefined,
                    trim_blocks=True,
                    lstrip_blocks=True,
                )
            else:
                from jinja2 import BaseLoader
                self._env = Environment(loader=BaseLoader(), undefined=_SafeUndefined)
        return self._env

    def _template_vars(
        self,
        context: MeetingContext,
        transcript_text: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        vars_ = {
            "meeting_id": context.meeting_id,
            "title": context.title,
            "date": context.date,
            "duration": context.duration,
            "attendees": context.attendees,
            "organizer": context.organizer,
            "meeting_type": context.meeting_type,
            "transcript_text": transcript_text,
            "speakers": context.attendees,
            # Optional vars with safe defaults — overridden by `extra` when
            # the pipeline has real values.
            "vendors": [],
            "length_mode": "concise",
            "prior_actions": [],
        }
        if extra:
            vars_.update(extra)
        return vars_

    def render(
        self,
        template: PromptTemplate,
        context: MeetingContext,
        transcript_text: str,
        extra_vars: dict[str, Any] | None = None,
    ) -> str:
        """Render the full prompt (system + user) as a single string — fallback path."""
        env = self._get_env()
        template_vars = self._template_vars(context, transcript_text, extra_vars)

        try:
            tmpl = env.from_string(template.user_prompt_template)
            user_prompt = tmpl.render(**template_vars)
        except Exception:
            user_prompt = template.user_prompt_template
            for key, val in template_vars.items():
                user_prompt = user_prompt.replace(f"{{{{{key}}}}}", str(val))

        system_prompt = template.system_prompt or SYSTEM_PROMPT
        return f"{system_prompt}\n\n{user_prompt}"

    def render_structured(
        self,
        template: PromptTemplate,
        context: MeetingContext,
        transcript_text: str,
        extra_vars: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) separately for structured generation.

        The returned system prompt is the shared structured-output rules with the
        per-template system prompt prepended as type-specific framing.
        """
        env = self._get_env()
        template_vars = self._template_vars(context, transcript_text, extra_vars)

        try:
            tmpl = env.from_string(template.user_prompt_template)
            user_prompt = tmpl.render(**template_vars)
        except Exception:
            user_prompt = template.user_prompt_template
            for key, val in template_vars.items():
                user_prompt = user_prompt.replace(f"{{{{{key}}}}}", str(val))

        type_framing = (template.system_prompt or "").strip()
        if type_framing:
            system_prompt = f"{type_framing}\n\n{STRUCTURED_SYSTEM_PROMPT}"
        else:
            system_prompt = STRUCTURED_SYSTEM_PROMPT

        return system_prompt, user_prompt

    def render_classification_prompt(self, transcript_excerpt: str) -> str:
        """Render a prompt to classify meeting type."""
        return f"""{SYSTEM_PROMPT}

Analyze the following meeting transcript excerpt and classify the meeting type.

Respond with a JSON object:
{{"meeting_type": "<type>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}

Transcript excerpt:
{transcript_excerpt[:3000]}"""
