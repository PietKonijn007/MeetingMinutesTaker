"""Minutes JSON and Markdown writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from meeting_minutes.models import (
    LLMResponse,
    LLMUsage,
    MinutesJSON,
    MinutesMetadata,
    MinutesSection,
    ParsedMinutes,
    QualityReport,
)


class MinutesJSONWriter:
    """Serialize minutes to JSON and Markdown files."""

    def write(
        self,
        minutes: ParsedMinutes,
        quality_report: QualityReport,
        llm_response: LLMResponse,
        output_dir: Path,
        meeting_context: dict | None = None,
    ) -> tuple[Path, Path]:
        """Write minutes JSON and markdown. Returns (json_path, md_path)."""
        output_dir.mkdir(parents=True, exist_ok=True)

        ctx = meeting_context or minutes.meeting_context or {}
        title = ctx.get("title", f"Meeting {minutes.meeting_id[:8]}")
        date = ctx.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        duration = ctx.get("duration", "unknown")
        attendees = ctx.get("attendees", [])
        organizer = ctx.get("organizer")
        meeting_type = ctx.get("meeting_type", "other")

        llm_usage = LLMUsage(
            provider=llm_response.provider,
            model=llm_response.model,
            tokens_used={
                "input": llm_response.input_tokens,
                "output": llm_response.output_tokens,
            },
            cost_usd=llm_response.cost_usd,
            processing_time_seconds=llm_response.processing_time_seconds,
        )

        metadata = MinutesMetadata(
            title=title,
            date=date,
            duration=duration,
            attendees=attendees,
            organizer=organizer,
        )

        # Build markdown from parsed minutes
        markdown = self._build_markdown(minutes, metadata)

        minutes_json = MinutesJSON(
            meeting_id=minutes.meeting_id,
            generated_at=datetime.now(timezone.utc),
            meeting_type=meeting_type,
            metadata=metadata,
            summary=minutes.summary,
            sections=minutes.sections,
            action_items=minutes.action_items,
            decisions=minutes.decisions,
            key_topics=minutes.key_topics,
            minutes_markdown=markdown,
            llm=llm_usage,
        )

        json_path = output_dir / f"{minutes.meeting_id}.json"
        md_path = output_dir / f"{minutes.meeting_id}.md"

        with open(json_path, "w", encoding="utf-8") as f:
            f.write(minutes_json.model_dump_json(indent=2))

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        return json_path, md_path

    @staticmethod
    def _build_markdown(minutes: ParsedMinutes, metadata: MinutesMetadata) -> str:
        """Build markdown document from parsed minutes."""
        lines: list[str] = []

        lines.append(f"# {metadata.title}")
        lines.append("")
        lines.append(f"**Date:** {metadata.date}")
        lines.append(f"**Duration:** {metadata.duration}")
        lines.append(f"**Attendees:** {', '.join(metadata.attendees)}")
        if metadata.organizer:
            lines.append(f"**Organizer:** {metadata.organizer}")
        lines.append("")

        if minutes.summary:
            lines.append("## Summary")
            lines.append("")
            lines.append(minutes.summary)
            lines.append("")

        for section in minutes.sections:
            if section.type in ("summary",):
                continue  # already rendered above
            lines.append(f"## {section.heading}")
            lines.append("")
            lines.append(section.content)
            lines.append("")

        if minutes.action_items:
            lines.append("## Action Items")
            lines.append("")
            for item in minutes.action_items:
                check = "x" if item.status.value == "done" else " "
                line = f"- [{check}] {item.description}"
                if item.owner:
                    line += f" — Owner: {item.owner}"
                if item.due_date:
                    line += f" (Due: {item.due_date})"
                lines.append(line)
            lines.append("")

        if minutes.decisions:
            lines.append("## Decisions")
            lines.append("")
            for decision in minutes.decisions:
                line = f"- {decision.description}"
                if decision.made_by:
                    line += f" (by {decision.made_by})"
                lines.append(line)
            lines.append("")

        if minutes.key_topics:
            lines.append("## Key Topics")
            lines.append("")
            for topic in minutes.key_topics:
                lines.append(f"- {topic}")
            lines.append("")

        return "\n".join(lines)
