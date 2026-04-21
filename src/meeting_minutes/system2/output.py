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

        # Build structured_data dict — this is what gets persisted to the DB's
        # structured_json column and served back to the frontend via the API.
        # Without this, discussion_points/risks/follow_ups/parking_lot live
        # only in the on-disk JSON file and never reach the UI.
        participants = getattr(minutes, "participants", [])
        discussion_points = getattr(minutes, "discussion_points", [])
        risks_and_concerns = getattr(minutes, "risks_and_concerns", [])
        open_questions = getattr(minutes, "open_questions", []) or []
        follow_ups = getattr(minutes, "follow_ups", [])
        parking_lot = getattr(minutes, "parking_lot", []) or []
        prior_action_updates = getattr(minutes, "prior_action_updates", []) or []
        email_draft = getattr(minutes, "email_draft", None)
        meeting_effectiveness = getattr(minutes, "meeting_effectiveness", None)
        tldr = getattr(minutes, "tldr", "") or ""
        confidentiality = getattr(minutes, "confidentiality", None)

        structured_data = {
            "tldr": tldr,
            "confidentiality": confidentiality,
            "sentiment": getattr(minutes, "sentiment", None),
            "detailed_notes": getattr(minutes, "detailed_notes", "") or "",
            "participants": [p.model_dump() for p in participants],
            "discussion_points": [dp.model_dump() for dp in discussion_points],
            "decisions": [d.model_dump() for d in minutes.decisions],
            "action_items": [ai.model_dump() for ai in minutes.action_items],
            "risks_and_concerns": [rc.model_dump() for rc in risks_and_concerns],
            "open_questions": [oq.model_dump() for oq in open_questions],
            "follow_ups": [fu.model_dump() for fu in follow_ups],
            "parking_lot": parking_lot,
            "prior_action_updates": [pau.model_dump() for pau in prior_action_updates],
            "email_draft": email_draft.model_dump() if email_draft else None,
            "key_topics": minutes.key_topics,
            "meeting_effectiveness": meeting_effectiveness.model_dump() if meeting_effectiveness else None,
        }

        minutes_json = MinutesJSON(
            meeting_id=minutes.meeting_id,
            generated_at=datetime.now(timezone.utc),
            meeting_type=meeting_type,
            metadata=metadata,
            summary=minutes.summary,
            tldr=tldr,
            detailed_notes=getattr(minutes, "detailed_notes", "") or "",
            sections=minutes.sections,
            action_items=minutes.action_items,
            decisions=minutes.decisions,
            key_topics=minutes.key_topics,
            minutes_markdown=markdown,
            llm=llm_usage,
            confidentiality=confidentiality,
            sentiment=getattr(minutes, "sentiment", None),
            participants=participants,
            discussion_points=discussion_points,
            risks_and_concerns=risks_and_concerns,
            open_questions=open_questions,
            follow_ups=follow_ups,
            parking_lot=parking_lot,
            prior_action_updates=prior_action_updates,
            email_draft=email_draft,
            meeting_effectiveness=meeting_effectiveness,
            structured_data=structured_data,
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
        confidentiality = getattr(minutes, "confidentiality", None)
        if confidentiality:
            lines.append(f"**Confidentiality:** {confidentiality}")
        if getattr(minutes, "sentiment", None):
            lines.append(f"**Sentiment:** {minutes.sentiment}")
        lines.append("")

        tldr = getattr(minutes, "tldr", "") or ""
        if tldr.strip():
            lines.append("## TL;DR")
            lines.append("")
            lines.append(tldr.strip())
            lines.append("")

        if minutes.summary:
            lines.append("## Summary")
            lines.append("")
            lines.append(minutes.summary)
            lines.append("")

        if getattr(minutes, "detailed_notes", None):
            lines.append("## Detailed Notes")
            lines.append("")
            lines.append(minutes.detailed_notes)
            lines.append("")

        # Render discussion points if available (from structured path)
        if getattr(minutes, "discussion_points", None):
            lines.append("## Discussion")
            lines.append("")
            for dp in minutes.discussion_points:
                lines.append(f"### {dp.topic}")
                lines.append("")
                lines.append(dp.summary)
                if dp.participants:
                    lines.append(f"  *Participants: {', '.join(dp.participants)}*")
                lines.append("")
        else:
            # Fallback: render sections from text+regex path
            for section in minutes.sections:
                if section.type in ("summary", "detailed_notes"):
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
                priority_tag = f" [{item.priority.upper()}]" if getattr(item, "priority", None) else ""
                line = f"- [{check}]{priority_tag} {item.description}"
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
                if getattr(decision, "rationale", None):
                    lines.append(f"  *Rationale: {decision.rationale}*")
            lines.append("")

        if minutes.key_topics:
            lines.append("## Key Topics")
            lines.append("")
            for topic in minutes.key_topics:
                lines.append(f"- {topic}")
            lines.append("")

        # New sections from structured path
        if getattr(minutes, "risks_and_concerns", None):
            lines.append("## Risks & Concerns")
            lines.append("")
            for rc in minutes.risks_and_concerns:
                line = f"- {rc.description}"
                if rc.raised_by:
                    line += f" (raised by {rc.raised_by})"
                lines.append(line)
            lines.append("")

        if getattr(minutes, "open_questions", None):
            lines.append("## Open Questions")
            lines.append("")
            for oq in minutes.open_questions:
                line = f"- {oq.question}"
                trailers = []
                if getattr(oq, "raised_by", None):
                    trailers.append(f"raised by {oq.raised_by}")
                if getattr(oq, "owner", None):
                    trailers.append(f"owner: {oq.owner}")
                if trailers:
                    line += f" _({'; '.join(trailers)})_"
                lines.append(line)
            lines.append("")

        if getattr(minutes, "follow_ups", None):
            lines.append("## Follow-ups")
            lines.append("")
            for fu in minutes.follow_ups:
                line = f"- {fu.description}"
                if fu.owner:
                    line += f" — {fu.owner}"
                if fu.timeframe:
                    line += f" ({fu.timeframe})"
                lines.append(line)
            lines.append("")

        if getattr(minutes, "parking_lot", None):
            lines.append("## Parking Lot")
            lines.append("")
            for item in minutes.parking_lot:
                lines.append(f"- {item}")
            lines.append("")

        prior_updates = getattr(minutes, "prior_action_updates", None) or []
        if prior_updates:
            lines.append("## Prior Action Item Updates")
            lines.append("")
            for pau in prior_updates:
                line = f"- `{pau.action_item_id}` → **{pau.new_status}**"
                if getattr(pau, "evidence", None):
                    line += f" — _{pau.evidence}_"
                lines.append(line)
            lines.append("")

        email_draft = getattr(minutes, "email_draft", None)
        if email_draft and (email_draft.subject or email_draft.body):
            lines.append("## Follow-up Email Draft")
            lines.append("")
            if email_draft.subject:
                lines.append(f"**Subject:** {email_draft.subject}")
            if email_draft.to:
                lines.append(f"**To:** {', '.join(email_draft.to)}")
            if email_draft.cc:
                lines.append(f"**Cc:** {', '.join(email_draft.cc)}")
            lines.append("")
            if email_draft.body:
                lines.append(email_draft.body)
            lines.append("")

        if getattr(minutes, "meeting_effectiveness", None):
            me = minutes.meeting_effectiveness
            lines.append("## Meeting Effectiveness")
            lines.append("")
            if me.had_clear_agenda is not None:
                lines.append(f"- Clear agenda: {'Yes' if me.had_clear_agenda else 'No'}")
            lines.append(f"- Decisions made: {me.decisions_made}")
            lines.append(f"- Action items assigned: {me.action_items_assigned}")
            lines.append(f"- Unresolved items: {me.unresolved_items}")
            lines.append("")

        return "\n".join(lines)
