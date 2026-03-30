"""Prompt router — selects meeting type template based on confidence and overrides."""

from __future__ import annotations

from pathlib import Path

from meeting_minutes.config import GenerationConfig
from meeting_minutes.models import MeetingType, PromptTemplate


KNOWN_TYPES = {e.value for e in MeetingType}

TEMPLATE_FILENAME_MAP: dict[str, str] = {
    "standup": "standup.md.j2",
    "one_on_one": "one_on_one.md.j2",
    "decision_meeting": "decision_meeting.md.j2",
    "customer_meeting": "customer_meeting.md.j2",
    "brainstorm": "brainstorm.md.j2",
    "retrospective": "retrospective.md.j2",
    "planning": "planning.md.j2",
    "other": "general.md.j2",
}


class PromptRouter:
    """Select prompt template based on meeting type + confidence."""

    CONFIDENCE_THRESHOLD = 0.7

    def __init__(self, config: GenerationConfig, templates_dir: Path) -> None:
        self._config = config
        self._templates_dir = templates_dir

    def select_template(
        self,
        meeting_type: str,
        confidence: float,
        user_override: str | None = None,
    ) -> PromptTemplate:
        """Select appropriate template.

        Priority:
        1. User override
        2. confidence >= 0.7 → use meeting_type template
        3. confidence < 0.7 → fall back to "other"
        """
        if user_override:
            selected_type = user_override
        elif confidence >= self.CONFIDENCE_THRESHOLD and meeting_type in KNOWN_TYPES:
            selected_type = meeting_type
        else:
            selected_type = "other"

        return self._load_template(selected_type)

    def _load_template(self, meeting_type: str) -> PromptTemplate:
        # 1. Check for a type-specific template file by convention
        convention_path = self._templates_dir / f"{meeting_type}.md.j2"
        if convention_path.exists():
            template_path = convention_path
            filename = convention_path.name
        else:
            # 2. Fall back to TEMPLATE_FILENAME_MAP for built-in aliases (e.g. "other" -> "general.md.j2")
            filename = TEMPLATE_FILENAME_MAP.get(meeting_type, "general.md.j2")
            template_path = self._templates_dir / filename

        if not template_path.exists():
            # Fall back to general
            template_path = self._templates_dir / "general.md.j2"

        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
        else:
            content = _default_template_content(meeting_type)

        # Split system prompt from user prompt template
        system_prompt, user_prompt_template = _split_template(content)

        return PromptTemplate(
            name=filename,
            meeting_type=meeting_type,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
        )

    def classify_meeting_type(
        self, transcript_excerpt: str, calendar_metadata: dict
    ) -> tuple[str, float]:
        """Simple keyword-based classification when LLM is not available."""
        text = transcript_excerpt.lower()
        type_keywords = {
            "standup": ["standup", "stand-up", "daily", "blockers", "what did you do"],
            "one_on_one": ["1:1", "one on one", "one-on-one", "career", "personal"],
            "decision_meeting": ["decision", "vote", "approve", "decide", "resolution"],
            "customer_meeting": ["customer", "client", "sales", "demo", "requirements"],
            "brainstorm": ["brainstorm", "ideation", "ideas", "creative", "think"],
            "retrospective": ["retrospective", "retro", "what went well", "what could be better"],
            "planning": ["planning", "sprint", "roadmap", "quarter", "milestone"],
        }

        scores: dict[str, int] = {}
        for meeting_type, keywords in type_keywords.items():
            scores[meeting_type] = sum(1 for kw in keywords if kw in text)

        if max(scores.values(), default=0) == 0:
            return "other", 0.0

        best_type = max(scores, key=lambda k: scores[k])
        total = sum(scores.values())
        confidence = scores[best_type] / max(total, 1)
        return best_type, min(confidence, 1.0)


def _split_template(content: str) -> tuple[str, str]:
    """Split template content into system prompt and user prompt template."""
    # Look for a separator like "---" or "=== USER PROMPT ===" or just use all as user
    separator = "\n---\n"
    if separator in content:
        parts = content.split(separator, 1)
        return parts[0].strip(), parts[1].strip()
    # If no separator, use first paragraph as system prompt
    lines = content.split("\n\n", 1)
    if len(lines) == 2:
        return lines[0].strip(), lines[1].strip()
    return "", content.strip()


def _default_template_content(meeting_type: str) -> str:
    return f"""You are a meeting minutes assistant. Extract key information from the following meeting transcript.

---
Meeting Type: {meeting_type}
Date: {{{{ date }}}}
Attendees: {{{{ attendees | join(', ') }}}}
Duration: {{{{ duration }}}}

## Transcript
{{{{ transcript_text }}}}

Please provide well-structured meeting minutes with:

## Summary
[Brief summary]

## Discussion
[Key discussion points]

## Action Items
- [ ] [Description] — Owner: [Name] (Due: [Date if mentioned])

## Decisions
- [Decision made]

## Key Topics
- [Topic 1]
- [Topic 2]
"""
