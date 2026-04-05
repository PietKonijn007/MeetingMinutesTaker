"""Prompt router — selects meeting type template based on confidence and overrides."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from meeting_minutes.config import GenerationConfig
from meeting_minutes.models import MeetingType, PromptTemplate

logger = logging.getLogger(__name__)


KNOWN_TYPES = {e.value for e in MeetingType}

# Also discover custom templates at runtime
def _discover_all_types(templates_dir: Path) -> set[str]:
    """Return all known meeting types including custom templates."""
    types = set(KNOWN_TYPES)
    if templates_dir.is_dir():
        for f in templates_dir.glob("*.md.j2"):
            stem = f.stem
            if stem.endswith(".md"):
                stem = stem[:-3]
            if stem == "general":
                stem = "other"
            types.add(stem)
    return types

TEMPLATE_FILENAME_MAP: dict[str, str] = {
    "standup": "standup.md.j2",
    "one_on_one": "one_on_one.md.j2",
    "team_meeting": "team_meeting.md.j2",
    "decision_meeting": "decision_meeting.md.j2",
    "customer_meeting": "customer_meeting.md.j2",
    "brainstorm": "brainstorm.md.j2",
    "retrospective": "retrospective.md.j2",
    "planning": "planning.md.j2",
    "other": "general.md.j2",
}

# Classification tool schema for Anthropic tool_use
_CLASSIFICATION_TOOL = {
    "name": "classify_meeting",
    "description": "Classify the type of meeting based on the transcript excerpt and metadata.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meeting_type": {
                "type": "string",
                "description": "The classified meeting type",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score from 0.0 to 1.0",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of why this type was chosen",
            },
        },
        "required": ["meeting_type", "confidence", "reasoning"],
    },
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
        all_types = _discover_all_types(self._templates_dir)
        if user_override:
            selected_type = user_override
        elif confidence >= self.CONFIDENCE_THRESHOLD and meeting_type in all_types:
            selected_type = meeting_type
        else:
            selected_type = "other"

        return self._load_template(selected_type)

    def _extract_type_descriptions(self, all_types: set[str]) -> dict[str, str]:
        """Extract descriptions from actual template files.

        Reads the system prompt (first section before ---) from each template
        and uses it as the type description. This way the classifier knows
        exactly what each template is designed to capture.
        """
        descriptions = {}
        for meeting_type in sorted(all_types):
            # Find the template file
            if meeting_type == "other":
                template_path = self._templates_dir / "general.md.j2"
            else:
                template_path = self._templates_dir / f"{meeting_type}.md.j2"

            if template_path.exists():
                try:
                    content = template_path.read_text(encoding="utf-8")
                    # Extract system prompt (everything before the --- separator)
                    system_prompt = content.split("\n---\n")[0].strip() if "\n---\n" in content else ""

                    # Also extract section headings to show what the template captures
                    headings = []
                    for line in content.split("\n"):
                        if line.startswith("## ") and line.strip() not in ("## Transcript", "## Title", "## Summary", "## Key Topics"):
                            heading = line[3:].strip()
                            # Remove Jinja2 template syntax
                            if "{{" not in heading and "{%" not in heading and "[" not in heading:
                                headings.append(heading)

                    # Build description: system prompt + key sections
                    desc = system_prompt[:300]  # First 300 chars of system prompt
                    if headings:
                        desc += f" Key sections: {', '.join(headings[:8])}"
                    descriptions[meeting_type] = desc

                except Exception:
                    descriptions[meeting_type] = f"Meeting type: {meeting_type.replace('_', ' ').title()}"
            else:
                descriptions[meeting_type] = f"Meeting type: {meeting_type.replace('_', ' ').title()}"

        return descriptions

    async def classify_with_llm(
        self, transcript_excerpt: str, num_speakers: int = 0,
        calendar_title: str = "", num_attendees: int = 0,
    ) -> tuple[str, float, str]:
        """Classify meeting type using LLM with tool_use for structured output.

        Returns (meeting_type, confidence, reasoning).
        Uses Anthropic tool_use to guarantee valid JSON.
        Cost: ~$0.001–0.003 per classification.
        """
        # Build type descriptions from actual template files
        all_types = _discover_all_types(self._templates_dir)
        type_descriptions = self._extract_type_descriptions(all_types)
        types_list = "\n".join(f"- **{t}**: {desc}" for t, desc in type_descriptions.items())

        # Update the tool schema with the actual enum values
        tool = dict(_CLASSIFICATION_TOOL)
        tool["input_schema"] = dict(tool["input_schema"])
        tool["input_schema"]["properties"] = dict(tool["input_schema"]["properties"])
        tool["input_schema"]["properties"]["meeting_type"] = {
            "type": "string",
            "enum": sorted(all_types),
            "description": "The classified meeting type",
        }

        system_prompt = (
            "You are a meeting type classifier. Analyze the transcript excerpt and metadata "
            "to determine the most likely meeting type. Consider: the topics discussed, "
            "number of speakers, meeting title (if available), attendee count, and the "
            "conversational style."
        )

        # Build context
        context_parts = [f"Number of speakers detected: {num_speakers}"]
        if calendar_title:
            context_parts.append(f"Calendar event title: {calendar_title}")
        if num_attendees:
            context_parts.append(f"Number of attendees: {num_attendees}")

        user_prompt = f"""Classify this meeting into one of these types:

{types_list}

Meeting metadata:
{chr(10).join(context_parts)}

Transcript excerpt (first ~10 minutes):
{transcript_excerpt[:4000]}"""

        try:
            import anthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                logger.info("No ANTHROPIC_API_KEY, falling back to keyword classification")
                return self.classify_meeting_type(transcript_excerpt, {})

            client = anthropic.AsyncAnthropic(api_key=api_key)

            # Use a fast, cheap model for classification
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "classify_meeting"},
            )

            # Extract from tool_use block
            for block in response.content:
                if block.type == "tool_use":
                    result = block.input
                    meeting_type = result.get("meeting_type", "other")
                    confidence = float(result.get("confidence", 0.5))
                    reasoning = result.get("reasoning", "")

                    # Validate the type exists
                    if meeting_type not in all_types:
                        meeting_type = "other"
                        confidence = max(confidence - 0.3, 0.1)

                    logger.info("LLM classify: %s (confidence=%.2f) — %s", meeting_type, confidence, reasoning)
                    return meeting_type, confidence, reasoning

            # No tool_use block — shouldn't happen with tool_choice forced
            logger.warning("LLM did not return tool_use block, falling back to keyword classification")
            return self.classify_meeting_type(transcript_excerpt, {})

        except Exception as e:
            logger.warning("LLM classification failed: %s, falling back to keywords", e)
            mt, conf = self.classify_meeting_type(transcript_excerpt, {})
            return mt, conf, "keyword fallback"

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
