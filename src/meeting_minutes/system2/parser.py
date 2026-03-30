"""Parse LLM markdown response into structured minutes data."""

from __future__ import annotations

import re
import uuid
from typing import Optional

from meeting_minutes.models import (
    ActionItem,
    ActionItemStatus,
    Decision,
    MeetingContext,
    MinutesSection,
    ParsedMinutes,
)


class MinutesParser:
    """Parse LLM markdown response into ParsedMinutes."""

    # Matches `## Heading` or `### Heading`
    HEADING_RE = re.compile(r"^#{2,3}\s+(.+)$", re.MULTILINE)

    # Matches `- [ ] description — Owner: name (Due: date)`
    ACTION_ITEM_RE = re.compile(
        r"^[-*]\s+\[\s*[ xX]?\s*\]\s+(.+?)(?:\s+[—\-–]\s+Owner:\s*([^\n(]+?))?(?:\s+\(Due:\s*([^\n)]+)\))?$",
        re.MULTILINE,
    )

    # Alternative: `- [ ] description` on one line, owner/due on same line
    ACTION_ITEM_SIMPLE_RE = re.compile(
        r"^[-*]\s+\[\s*[ xX]?\s*\]\s+(.+)$",
        re.MULTILINE,
    )

    def parse(self, llm_response: str, meeting_context: MeetingContext) -> ParsedMinutes:
        """Extract title, summary, sections, action_items, decisions, key_topics."""
        title = self._extract_title(llm_response)
        summary = self._extract_summary(llm_response)
        sections = self._extract_sections(llm_response)
        action_items = self._extract_action_items(llm_response)
        decisions = self._extract_decisions(llm_response)
        key_topics = self._extract_key_topics(llm_response, sections)

        return ParsedMinutes(
            meeting_id=meeting_context.meeting_id,
            title=title,
            summary=summary,
            sections=sections,
            action_items=action_items,
            decisions=decisions,
            key_topics=key_topics,
            raw_llm_response=llm_response,
            meeting_context={
                "title": meeting_context.title,
                "date": meeting_context.date,
                "attendees": meeting_context.attendees,
            },
        )

    def _extract_title(self, text: str) -> str:
        """Extract title from ## Title section."""
        match = re.search(
            r"^#{1,3}\s+Title\s*\n(.*?)(?=^#{1,3}\s|\Z)",
            text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if match:
            title = match.group(1).strip()
            # Clean up: remove markdown formatting, quotes, brackets
            title = re.sub(r"^[\[\"\']|[\]\"\']$", "", title.strip())
            title = re.sub(r"\*+", "", title)
            # Take only the first line if multi-line
            title = title.split("\n")[0].strip()
            if title:
                return title

        # Fallback: look for a # top-level heading
        match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if match:
            title = match.group(1).strip()
            # Skip generic "Meeting Minutes" headings
            if not re.match(r"^meeting\s*(minutes|notes)?[\s:]*$", title, re.IGNORECASE):
                return title

        return ""

    def _extract_summary(self, text: str) -> str:
        """Extract paragraph after ## Summary heading."""
        # Find ## Summary section
        match = re.search(
            r"^#{1,3}\s+Summary\s*\n(.*?)(?=^#{1,3}\s|\Z)",
            text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        # Fallback: first non-heading paragraph
        lines = text.split("\n")
        paragraphs = []
        current_para: list[str] = []
        for line in lines:
            if line.startswith("#"):
                if current_para:
                    paragraphs.append(" ".join(current_para))
                    current_para = []
            elif line.strip():
                current_para.append(line.strip())
            else:
                if current_para:
                    paragraphs.append(" ".join(current_para))
                    current_para = []
        if current_para:
            paragraphs.append(" ".join(current_para))
        return paragraphs[0] if paragraphs else ""

    def _extract_sections(self, text: str) -> list[MinutesSection]:
        """Extract all ## headings and their content."""
        sections: list[MinutesSection] = []

        # Split by ## headings
        parts = re.split(r"(?=^#{2,3}\s+)", text, flags=re.MULTILINE)

        for part in parts:
            part = part.strip()
            if not part:
                continue
            heading_match = re.match(r"^(#{2,3})\s+(.+?)$", part, re.MULTILINE)
            if not heading_match:
                continue
            heading_text = heading_match.group(2).strip()
            # Content is everything after the heading line
            content_start = heading_match.end()
            content = part[content_start:].strip()

            sections.append(
                MinutesSection(
                    heading=heading_text,
                    content=content,
                    type=self._classify_section(heading_text),
                )
            )

        return sections

    def _classify_section(self, heading: str) -> str:
        heading_lower = heading.lower()
        if "summary" in heading_lower:
            return "summary"
        if "action" in heading_lower:
            return "action_items"
        if "decision" in heading_lower:
            return "decisions"
        if "discussion" in heading_lower:
            return "discussion"
        if "topic" in heading_lower:
            return "topics"
        if "next" in heading_lower:
            return "next_steps"
        return "other"

    def _extract_action_items(self, text: str) -> list[ActionItem]:
        """Extract lines with `- [ ]` pattern."""
        action_items: list[ActionItem] = []
        seen: set[str] = set()

        for match in self.ACTION_ITEM_RE.finditer(text):
            description = match.group(1).strip()
            owner = match.group(2).strip() if match.group(2) else None
            due_date = match.group(3).strip() if match.group(3) else None

            if description in seen:
                continue
            seen.add(description)

            # Extract owner from description if present inline
            if owner is None:
                owner_match = re.search(
                    r"(?:Owner|Assigned to|Assignee)[:\s]+([A-Za-z][A-Za-z\s]+?)(?:[,.]|$)",
                    description,
                    re.IGNORECASE,
                )
                if owner_match:
                    owner = owner_match.group(1).strip()

            action_items.append(
                ActionItem(
                    description=description,
                    owner=owner,
                    due_date=due_date,
                    status=ActionItemStatus.OPEN,
                )
            )

        # If no matches from complex RE, try simple RE
        if not action_items:
            for match in self.ACTION_ITEM_SIMPLE_RE.finditer(text):
                description = match.group(1).strip()
                if description in seen:
                    continue
                seen.add(description)
                action_items.append(
                    ActionItem(
                        description=description,
                        status=ActionItemStatus.OPEN,
                    )
                )

        return action_items

    def _extract_decisions(self, text: str) -> list[Decision]:
        """Extract decisions from the ## Decisions section."""
        decisions: list[Decision] = []

        # Find ## Decisions section
        decision_section = re.search(
            r"^#{1,3}\s+Decisions?\s*\n(.*?)(?=^#{1,3}\s|\Z)",
            text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if not decision_section:
            return decisions

        section_text = decision_section.group(1)

        for line in section_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Remove bullet markers
            line = re.sub(r"^[-*•]\s*", "", line).strip()
            if line:
                # Extract "made by" if present
                made_by = None
                made_by_match = re.search(
                    r"(?:by|from|—)\s+([A-Za-z][A-Za-z\s]+?)(?:[,.]|$)",
                    line,
                    re.IGNORECASE,
                )
                if made_by_match:
                    made_by = made_by_match.group(1).strip()

                decisions.append(
                    Decision(
                        description=line,
                        made_by=made_by,
                    )
                )

        return decisions

    def _extract_key_topics(
        self, text: str, sections: list[MinutesSection]
    ) -> list[str]:
        """Infer key topics from headings and content."""
        topics: list[str] = []

        # Look for explicit "Key Topics" section
        topics_section = re.search(
            r"^#{1,3}\s+Key\s+Topics?\s*\n(.*?)(?=^#{1,3}\s|\Z)",
            text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if topics_section:
            for line in topics_section.group(1).split("\n"):
                line = re.sub(r"^[-*•\d.]\s*", "", line.strip()).strip()
                if line:
                    topics.append(line)
            return topics

        # Infer from section headings (exclude meta sections)
        skip = {"summary", "action items", "action_items", "decisions", "key topics"}
        for section in sections:
            heading = section.heading
            if heading.lower() not in skip and not heading.lower().startswith("##"):
                topics.append(heading)

        return topics[:10]  # cap at 10
