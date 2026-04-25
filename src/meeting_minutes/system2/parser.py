"""Parse LLM markdown response into structured minutes data."""

from __future__ import annotations

import re
import uuid
from typing import Optional

from meeting_minutes.models import (
    ActionItem,
    ActionItemProposalState,
    ActionItemStatus,
    Decision,
    MeetingContext,
    MinutesSection,
    ParsedMinutes,
    StructuredMinutesResponse,
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
        detailed_notes = self._extract_detailed_notes(llm_response)
        sections = self._extract_sections(llm_response)
        action_items = self._extract_action_items(llm_response)
        decisions = self._extract_decisions(llm_response)
        key_topics = self._extract_key_topics(llm_response, sections)

        return ParsedMinutes(
            meeting_id=meeting_context.meeting_id,
            title=title,
            summary=summary,
            detailed_notes=detailed_notes,
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

    def _extract_detailed_notes(self, text: str) -> str:
        """Extract the narrative block under ## Detailed Notes / ## Extensive Notes / ## Meeting Notes."""
        match = re.search(
            r"^#{1,3}\s+(?:Detailed\s+Notes|Extensive\s+Notes|Meeting\s+Notes)\s*\n(.*?)(?=^#{1,3}\s|\Z)",
            text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        return match.group(1).strip() if match else ""

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
        if "detailed notes" in heading_lower or "extensive notes" in heading_lower or "meeting notes" in heading_lower:
            return "detailed_notes"
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
                    proposal_state=ActionItemProposalState.PROPOSED,
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
                        proposal_state=ActionItemProposalState.PROPOSED,
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


class StructuredMinutesAdapter:
    """Convert StructuredMinutesResponse to ParsedMinutes."""

    def adapt(self, structured: StructuredMinutesResponse, meeting_context: MeetingContext) -> ParsedMinutes:
        """Map structured LLM output to ParsedMinutes."""
        # Map discussion_points to MinutesSection
        sections = [
            MinutesSection(heading=dp.topic, content=dp.summary, type="discussion")
            for dp in structured.discussion_points
        ]

        # Map action items
        action_items = [
            ActionItem(
                description=ai.description,
                owner=ai.owner,
                due_date=ai.due_date,
                priority=ai.priority,
                transcript_segment_ids=ai.transcript_segment_ids,
                status=ActionItemStatus.OPEN,
                proposal_state=ActionItemProposalState.PROPOSED,
            )
            for ai in structured.action_items
        ]

        # Map decisions
        decisions = [
            Decision(
                description=d.description,
                made_by=d.made_by,
                rationale=d.rationale,
                confidence=d.confidence,
                transcript_segment_ids=d.transcript_segment_ids,
            )
            for d in structured.decisions
        ]

        return ParsedMinutes(
            meeting_id=meeting_context.meeting_id,
            title=structured.title,
            tldr=getattr(structured, "tldr", "") or "",
            summary=structured.summary,
            detailed_notes=structured.detailed_notes,
            sections=sections,
            action_items=action_items,
            decisions=decisions,
            key_topics=structured.key_topics,
            raw_llm_response=structured.model_dump_json(),
            meeting_context={
                "title": structured.title,
                "date": meeting_context.date,
                "attendees": meeting_context.attendees,
            },
            confidentiality=getattr(structured, "confidentiality", None),
            sentiment=structured.sentiment,
            participants=structured.participants,
            discussion_points=structured.discussion_points,
            risks_and_concerns=structured.risks_and_concerns,
            open_questions=list(getattr(structured, "open_questions", []) or []),
            follow_ups=structured.follow_ups,
            parking_lot=structured.parking_lot,
            prior_action_updates=list(getattr(structured, "prior_action_updates", []) or []),
            email_draft=getattr(structured, "email_draft", None),
            meeting_effectiveness=structured.meeting_effectiveness,
        )
