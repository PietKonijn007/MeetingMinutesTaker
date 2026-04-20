"""Prompt template engine using Jinja2."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from meeting_minutes.models import MeetingContext, PromptTemplate


SYSTEM_PROMPT = """You are an expert meeting minutes assistant. Your task is to analyze meeting transcripts and produce clear, accurate, and well-structured meeting minutes.

Follow these guidelines:
- Be concise but comprehensive
- Preserve factual accuracy — only include information that was discussed
- Identify all action items with owners and deadlines when mentioned
- Capture decisions made during the meeting
- Use professional language appropriate for business documentation
- Format your response using the exact markdown structure requested"""

STRUCTURED_SYSTEM_PROMPT = """You are an expert meeting minutes assistant. Analyze the transcript carefully and extract all relevant information. Be thorough but accurate — only include information explicitly discussed in the transcript.

Guidelines:
- Generate a specific, descriptive title (not generic like "Team Meeting")
- Write a short `summary` (2-4 sentences) capturing purpose, outcomes, and critical issues
- Write `detailed_notes`: a comprehensive narrative write-up of the meeting in well-structured markdown. This is the deep record a reader uses when the short summary, decisions, and action items are not enough context. It should:
  * Walk through the meeting roughly in the order topics came up
  * Capture the reasoning, trade-offs, alternatives considered, and open questions behind each discussion — not just the conclusions
  * Attribute key points, positions, and pushback to named participants where the transcript makes it clear
  * Include concrete details mentioned in the transcript (numbers, dates, names, system/tool names, error messages)
  * Use ## / ### subheadings, paragraphs, and bullet lists as appropriate for readability
  * Be substantially longer and richer than `summary` — aim for roughly 400-1200 words depending on meeting length and density
  * NOT duplicate the discussion_points/decisions/action_items lists verbatim; instead weave the narrative around them
  * Only include information actually present in the transcript — do not invent content
- Identify all participants and their roles in the discussion
- For each participant, assess their sentiment throughout the meeting (positive, neutral, negative, or mixed)
- Capture every discussion point with a topic summary
- Extract ALL action items with owners and due dates when mentioned
- Record ALL decisions with rationale
- Note any risks, concerns, or items deferred to future meetings
- Assess overall meeting effectiveness
- Reference transcript segment IDs where possible"""


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
                    undefined=StrictUndefined,
                    trim_blocks=True,
                    lstrip_blocks=True,
                )
            else:
                # Fallback: string-based environment
                from jinja2 import BaseLoader
                self._env = Environment(loader=BaseLoader())
        return self._env

    def render(
        self,
        template: PromptTemplate,
        context: MeetingContext,
        transcript_text: str,
    ) -> str:
        """Render the full prompt using Jinja2 template."""
        env = self._get_env()

        template_vars = {
            "meeting_id": context.meeting_id,
            "title": context.title,
            "date": context.date,
            "duration": context.duration,
            "attendees": context.attendees,
            "organizer": context.organizer,
            "meeting_type": context.meeting_type,
            "transcript_text": transcript_text,
            "speakers": context.attendees,
        }

        try:
            tmpl = env.from_string(template.user_prompt_template)
            user_prompt = tmpl.render(**template_vars)
        except Exception as exc:
            # Fallback to simple substitution
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
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) separately for structured generation."""
        system_prompt = STRUCTURED_SYSTEM_PROMPT

        env = self._get_env()

        template_vars = {
            "meeting_id": context.meeting_id,
            "title": context.title,
            "date": context.date,
            "duration": context.duration,
            "attendees": context.attendees,
            "organizer": context.organizer,
            "meeting_type": context.meeting_type,
            "transcript_text": transcript_text,
            "speakers": context.attendees,
        }

        try:
            tmpl = env.from_string(template.user_prompt_template)
            user_prompt = tmpl.render(**template_vars)
        except Exception:
            user_prompt = template.user_prompt_template
            for key, val in template_vars.items():
                user_prompt = user_prompt.replace(f"{{{{{key}}}}}", str(val))

        return system_prompt, user_prompt

    def render_classification_prompt(self, transcript_excerpt: str) -> str:
        """Render a prompt to classify meeting type."""
        return f"""{SYSTEM_PROMPT}

Analyze the following meeting transcript excerpt and classify the meeting type.

Valid types: standup, one_on_one, decision_meeting, customer_meeting, brainstorm, retrospective, planning, other

Respond with a JSON object:
{{"meeting_type": "<type>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}

Transcript excerpt:
{transcript_excerpt[:3000]}"""
