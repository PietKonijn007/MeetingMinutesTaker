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

    def render_classification_prompt(self, transcript_excerpt: str) -> str:
        """Render a prompt to classify meeting type."""
        return f"""{SYSTEM_PROMPT}

Analyze the following meeting transcript excerpt and classify the meeting type.

Valid types: standup, one_on_one, decision_meeting, customer_meeting, brainstorm, retrospective, planning, other

Respond with a JSON object:
{{"meeting_type": "<type>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}

Transcript excerpt:
{transcript_excerpt[:3000]}"""
