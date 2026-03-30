"""Template management endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from meeting_minutes.api.deps import get_config
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/templates", tags=["templates"])

BUILTIN_TYPES = {
    "standup",
    "one_on_one",
    "decision_meeting",
    "customer_meeting",
    "brainstorm",
    "retrospective",
    "planning",
    "other",
}

# Valid meeting_type slug: lowercase alphanumeric + underscore
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _templates_dir(config: AppConfig) -> Path:
    """Resolve the templates directory."""
    # Try config-based path first, fall back to project-level templates/
    # routes/templates.py → routes → api → meeting_minutes → src → PROJECT_ROOT
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "templates"


def _humanize(slug: str) -> str:
    """Convert a meeting_type slug to a human-readable name."""
    return slug.replace("_", " ").title()


def _split_template(content: str) -> tuple[str, str]:
    """Split template content on first '---' separator into (system_prompt, user_prompt_template)."""
    separator = "\n---\n"
    if separator in content:
        parts = content.split(separator, 1)
        return parts[0].strip(), parts[1].strip()
    # Fallback: first paragraph as system prompt
    lines = content.split("\n\n", 1)
    if len(lines) == 2:
        return lines[0].strip(), lines[1].strip()
    return "", content.strip()


# ── Schemas ──────────────────────────────────────────────────────────────


class TemplateSummary(BaseModel):
    meeting_type: str
    filename: str
    name: str
    builtin: bool
    description: str


class TemplateDetail(BaseModel):
    meeting_type: str
    name: str
    system_prompt: str
    user_prompt_template: str
    builtin: bool
    filename: str


class TemplateUpdate(BaseModel):
    system_prompt: str
    user_prompt_template: str


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("", response_model=list[TemplateSummary])
def list_templates(config: Annotated[AppConfig, Depends(get_config)]):
    """List all available templates."""
    tdir = _templates_dir(config)
    if not tdir.is_dir():
        return []

    results: list[TemplateSummary] = []
    for path in sorted(tdir.glob("*.md.j2")):
        stem = path.stem  # e.g. "standup.md" from "standup.md.j2"
        if stem.endswith(".md"):
            meeting_type = stem[: -len(".md")]
        else:
            meeting_type = stem

        # Special case: general.md.j2 maps to "other"
        if meeting_type == "general":
            meeting_type = "other"

        # Read first line as description
        try:
            first_line = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        except Exception:
            first_line = ""

        results.append(
            TemplateSummary(
                meeting_type=meeting_type,
                filename=path.name,
                name=_humanize(meeting_type),
                builtin=meeting_type in BUILTIN_TYPES,
                description=first_line,
            )
        )

    return results


@router.get("/{meeting_type}", response_model=TemplateDetail)
def get_template(
    meeting_type: str,
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Get a single template's full content."""
    tdir = _templates_dir(config)

    # Resolve filename: "other" → "general.md.j2", everything else → "{type}.md.j2"
    if meeting_type == "other":
        filename = "general.md.j2"
    else:
        filename = f"{meeting_type}.md.j2"

    path = tdir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{meeting_type}' not found")

    content = path.read_text(encoding="utf-8")
    system_prompt, user_prompt_template = _split_template(content)

    return TemplateDetail(
        meeting_type=meeting_type,
        name=_humanize(meeting_type),
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        builtin=meeting_type in BUILTIN_TYPES,
        filename=filename,
    )


@router.put("/{meeting_type}", response_model=TemplateDetail)
def update_template(
    meeting_type: str,
    body: TemplateUpdate,
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Create or update a template."""
    if not _SLUG_RE.match(meeting_type):
        raise HTTPException(
            status_code=422,
            detail="Meeting type must be lowercase, start with a letter, and contain only letters, digits, and underscores.",
        )

    tdir = _templates_dir(config)
    tdir.mkdir(parents=True, exist_ok=True)

    if meeting_type == "other":
        filename = "general.md.j2"
    else:
        filename = f"{meeting_type}.md.j2"

    content = body.system_prompt.strip() + "\n\n---\n" + body.user_prompt_template.strip() + "\n"
    path = tdir / filename
    path.write_text(content, encoding="utf-8")

    return TemplateDetail(
        meeting_type=meeting_type,
        name=_humanize(meeting_type),
        system_prompt=body.system_prompt.strip(),
        user_prompt_template=body.user_prompt_template.strip(),
        builtin=meeting_type in BUILTIN_TYPES,
        filename=filename,
    )


@router.delete("/{meeting_type}")
def delete_template(
    meeting_type: str,
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Delete a custom template. Built-in templates cannot be deleted."""
    if meeting_type in BUILTIN_TYPES:
        raise HTTPException(status_code=403, detail="Cannot delete built-in templates")

    tdir = _templates_dir(config)
    filename = f"{meeting_type}.md.j2"
    path = tdir / filename

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{meeting_type}' not found")

    path.unlink()
    return {"ok": True, "deleted": meeting_type}
