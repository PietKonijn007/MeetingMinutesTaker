"""Export meeting minutes to Obsidian vault as Markdown with YAML frontmatter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def export_to_obsidian(
    vault_path: str | Path,
    title: str,
    date: str,
    meeting_type: str,
    attendees: list[str],
    minutes_markdown: str,
    summary: str = "",
    action_items: list[dict] | None = None,
    decisions: list[dict] | None = None,
    key_topics: list[str] | None = None,
    meeting_id: str = "",
) -> Path:
    """Export a meeting to the Obsidian vault.

    Creates a Markdown file with YAML frontmatter in:
    {vault_path}/Meeting Minutes/{year}/{year-month}/{date} {title}.md

    Uses Obsidian-compatible features:
    - YAML frontmatter for metadata (date, type, attendees, tags)
    - [[wikilinks]] for attendee names
    - - [ ] task format for action items
    - #tags for meeting type
    """
    vault_path = Path(vault_path)

    # Build folder structure: Meeting Minutes/2026/2026-04/
    try:
        date_obj = datetime.fromisoformat(date.split("T")[0])
    except (ValueError, IndexError):
        date_obj = datetime.now()

    year = date_obj.strftime("%Y")
    year_month = date_obj.strftime("%Y-%m")
    date_str = date_obj.strftime("%Y-%m-%d")

    folder = vault_path / "Meeting Minutes" / year / year_month
    folder.mkdir(parents=True, exist_ok=True)

    # Clean title for filename
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    safe_title = safe_title.strip()[:80]
    filename = f"{date_str} {safe_title}.md"
    filepath = folder / filename

    # Build YAML frontmatter
    frontmatter_lines = [
        "---",
        f"date: {date_str}",
        f"type: {meeting_type}",
    ]

    if attendees:
        frontmatter_lines.append("attendees:")
        for a in attendees:
            frontmatter_lines.append(f"  - {a}")

    if key_topics:
        frontmatter_lines.append("topics:")
        for t in key_topics:
            frontmatter_lines.append(f"  - {t}")

    # Tags for Obsidian
    tags = [f"meeting/{meeting_type}"]
    frontmatter_lines.append("tags:")
    for tag in tags:
        frontmatter_lines.append(f"  - {tag}")

    if meeting_id:
        frontmatter_lines.append(f"meeting_id: {meeting_id}")

    frontmatter_lines.append("---")
    frontmatter = "\n".join(frontmatter_lines)

    # Build body with Obsidian wikilinks for attendees
    body = minutes_markdown

    # Replace attendee names with [[wikilinks]]
    for attendee in attendees or []:
        if attendee and len(attendee) > 1:
            # Only replace whole words, not partial matches
            body = body.replace(f"**{attendee}**", f"**[[{attendee}]]**")
            # Also in action items "Owner: Name"
            body = body.replace(f"Owner: {attendee}", f"Owner: [[{attendee}]]")
            body = body.replace(f"\u2014 {attendee}", f"\u2014 [[{attendee}]]")

    # Combine
    content = f"{frontmatter}\n\n{body}\n"

    # Write file
    filepath.write_text(content, encoding="utf-8")

    return filepath
