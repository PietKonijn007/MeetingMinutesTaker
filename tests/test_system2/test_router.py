"""Tests for PromptRouter."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from meeting_minutes.config import GenerationConfig
from meeting_minutes.models import PromptTemplate
from meeting_minutes.system2.router import PromptRouter


def _create_minimal_templates(tmp_dir: str):
    """Create minimal template files for testing."""
    tmp_path = Path(tmp_dir)
    templates = ["general", "standup", "planning", "brainstorm", "one_on_one",
                 "decision_meeting", "customer_meeting", "retrospective"]
    for name in templates:
        template_file = tmp_path / f"{name}.md.j2"
        template_file.write_text(
            f"System prompt for {name}\n\n---\nUser prompt for {{{{ title }}}} on {{{{ date }}}}"
        )


# Feature: meeting-minutes-taker, Property 13: Prompt router selection logic
@given(
    meeting_type=st.sampled_from(["standup", "planning", "brainstorm", "other", "unknown"]),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    user_override=st.one_of(st.none(), st.sampled_from(["standup", "planning", "other"])),
)
@settings(max_examples=100)
def test_prompt_router_selection_logic(
    meeting_type: str,
    confidence: float,
    user_override: str | None,
):
    """Property 13: Router follows priority: override > confidence >= 0.7 > fallback."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _create_minimal_templates(tmp_dir)
        tmp_path = Path(tmp_dir)

        config = GenerationConfig(templates_dir=tmp_dir)
        router = PromptRouter(config, tmp_path)

        template = router.select_template(
            meeting_type=meeting_type,
            confidence=confidence,
            user_override=user_override,
        )

    assert isinstance(template, PromptTemplate)
    assert template.meeting_type is not None

    if user_override:
        # Override takes priority
        assert template.meeting_type == user_override
    elif confidence >= 0.7 and meeting_type in {
        "standup", "one_on_one", "decision_meeting", "customer_meeting",
        "brainstorm", "retrospective", "planning", "other",
    }:
        assert template.meeting_type == meeting_type
    else:
        # Falls back to "other"
        assert template.meeting_type == "other"


def test_override_takes_priority(tmp_path: Path):
    """User override is used regardless of confidence."""
    _create_minimal_templates(str(tmp_path))
    config = GenerationConfig()
    router = PromptRouter(config, tmp_path)

    # Even with high confidence on "standup", override should win
    template = router.select_template(
        meeting_type="standup",
        confidence=0.9,
        user_override="planning",
    )
    assert template.meeting_type == "planning"


def test_high_confidence_uses_type(tmp_path: Path):
    """High confidence uses the meeting type template."""
    _create_minimal_templates(str(tmp_path))
    config = GenerationConfig()
    router = PromptRouter(config, tmp_path)

    template = router.select_template(
        meeting_type="standup",
        confidence=0.8,
    )
    assert template.meeting_type == "standup"


def test_low_confidence_falls_back(tmp_path: Path):
    """Low confidence falls back to 'other' template."""
    _create_minimal_templates(str(tmp_path))
    config = GenerationConfig()
    router = PromptRouter(config, tmp_path)

    template = router.select_template(
        meeting_type="standup",
        confidence=0.5,
    )
    assert template.meeting_type == "other"


def test_template_has_required_fields(tmp_path: Path):
    """Loaded template has name, meeting_type, system_prompt, user_prompt_template."""
    _create_minimal_templates(str(tmp_path))
    config = GenerationConfig()
    router = PromptRouter(config, tmp_path)

    template = router.select_template("other", 1.0)

    assert template.name
    assert template.meeting_type
    assert template.system_prompt is not None
    assert template.user_prompt_template is not None
