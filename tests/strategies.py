"""Hypothesis strategies for generating valid test data."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import strategies as st

from meeting_minutes.models import (
    ActionItem,
    ActionItemStatus,
    Decision,
    DiscussionPoint,
    FollowUp,
    LLMUsage,
    MeetingEffectiveness,
    MinutesJSON,
    MinutesMetadata,
    MinutesSection,
    ParticipantInfo,
    RiskConcern,
    SearchQuery,
    SpeakerMapping,
    StructuredActionItem,
    StructuredDecision,
    StructuredMinutesResponse,
    TranscriptJSON,
    TranscriptMetadata,
    TranscriptSegment,
    WordTimestamp,
)


# ---------------------------------------------------------------------------
# Basic building blocks
# ---------------------------------------------------------------------------

safe_text = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs"), whitelist_characters=".,!? "),
    min_size=1,
    max_size=200,
).map(str.strip).filter(lambda s: len(s) > 0)

safe_word = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz",
    min_size=2,
    max_size=15,
)

safe_name = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ",
    min_size=2,
    max_size=50,
).map(str.strip).filter(lambda s: len(s) > 1)

safe_datetime = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(timezone.utc),
)


def word_timestamp_strategy():
    return st.builds(
        WordTimestamp,
        word=safe_word,
        start=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
        end=st.floats(min_value=0.1, max_value=3601.0, allow_nan=False, allow_infinity=False),
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )


def transcript_segment_strategy():
    return st.builds(
        TranscriptSegment,
        id=st.integers(min_value=0, max_value=10000),
        start=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
        end=st.floats(min_value=0.1, max_value=3601.0, allow_nan=False, allow_infinity=False),
        speaker=st.one_of(st.none(), st.just("SPEAKER_00"), st.just("SPEAKER_01")),
        text=safe_text,
        words=st.lists(word_timestamp_strategy(), max_size=20),
    )


def speaker_mapping_strategy():
    return st.builds(
        SpeakerMapping,
        label=st.sampled_from(["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]),
        name=st.one_of(st.none(), safe_name),
        email=st.none(),
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )


def transcript_metadata_strategy():
    @st.composite
    def _build(draw):
        start = draw(safe_datetime)
        duration = draw(st.floats(min_value=60.0, max_value=7200.0, allow_nan=False, allow_infinity=False))
        from datetime import timedelta
        end = datetime(
            start.year, start.month, start.day,
            start.hour, start.minute, start.second,
            tzinfo=start.tzinfo,
        )
        end = start + timedelta(seconds=duration)
        return TranscriptMetadata(
            timestamp_start=start,
            timestamp_end=end,
            duration_seconds=duration,
            platform=draw(st.one_of(st.none(), st.just("zoom"), st.just("teams"))),
            language=draw(st.sampled_from(["en", "nl", "de", "fr"])),
            transcription_engine="faster-whisper",
            transcription_model=draw(st.sampled_from(["tiny", "base", "small", "medium"])),
            audio_file=draw(st.just("/tmp/test.flac")),
            recording_device=draw(st.just("default")),
        )

    return _build()


def transcript_json_strategy():
    """Generate valid TranscriptJSON instances."""
    @st.composite
    def _build(draw):
        metadata = draw(transcript_metadata_strategy())
        segments = draw(st.lists(transcript_segment_strategy(), min_size=0, max_size=10))
        speakers = draw(st.lists(speaker_mapping_strategy(), min_size=0, max_size=4))

        return TranscriptJSON(
            metadata=metadata,
            speakers=speakers,
            meeting_type=draw(st.sampled_from(["standup", "planning", "other", "brainstorm"])),
            meeting_type_confidence=draw(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
            ),
            transcript={
                "segments": [s.model_dump() for s in segments],
                "full_text": " ".join(s.text for s in segments),
            },
            processing={
                "created_at": datetime.now(timezone.utc).isoformat(),
                "processing_time_seconds": draw(st.floats(min_value=0.0, max_value=600.0, allow_nan=False, allow_infinity=False)),
                "pipeline_version": "0.1.0",
            },
        )

    return _build()


def minutes_section_strategy():
    return st.builds(
        MinutesSection,
        heading=safe_text,
        content=safe_text,
        type=st.one_of(st.none(), st.sampled_from(["summary", "discussion", "action_items"])),
    )


def action_item_strategy():
    return st.builds(
        ActionItem,
        description=safe_text,
        owner=st.one_of(st.none(), safe_name),
        due_date=st.one_of(st.none(), st.just("2025-12-31")),
        status=st.sampled_from(list(ActionItemStatus)),
        mentioned_at_seconds=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
        ),
        priority=st.one_of(st.none(), st.sampled_from(["high", "medium", "low"])),
        transcript_segment_ids=st.lists(st.integers(min_value=0, max_value=100), max_size=5),
    )


def decision_strategy():
    return st.builds(
        Decision,
        description=safe_text,
        made_by=st.one_of(st.none(), safe_name),
        mentioned_at_seconds=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
        ),
        rationale=st.one_of(st.none(), safe_text),
        confidence=st.one_of(st.none(), st.sampled_from(["high", "medium", "low"])),
        transcript_segment_ids=st.lists(st.integers(min_value=0, max_value=100), max_size=5),
    )


def participant_info_strategy():
    return st.builds(
        ParticipantInfo,
        name=safe_name,
        role=st.one_of(st.none(), st.sampled_from(["facilitator", "presenter", "contributor", "observer"])),
    )


def discussion_point_strategy():
    return st.builds(
        DiscussionPoint,
        topic=safe_text,
        summary=safe_text,
        participants=st.lists(safe_name, max_size=4),
        sentiment=st.one_of(st.none(), st.sampled_from(["positive", "neutral", "tense"])),
        transcript_segment_ids=st.lists(st.integers(min_value=0, max_value=100), max_size=5),
    )


def risk_concern_strategy():
    return st.builds(
        RiskConcern,
        description=safe_text,
        raised_by=st.one_of(st.none(), safe_name),
    )


def follow_up_strategy():
    return st.builds(
        FollowUp,
        description=safe_text,
        owner=st.one_of(st.none(), safe_name),
        timeframe=st.one_of(st.none(), st.just("next week"), st.just("end of month")),
    )


def meeting_effectiveness_strategy():
    return st.builds(
        MeetingEffectiveness,
        had_clear_agenda=st.one_of(st.none(), st.booleans()),
        decisions_made=st.integers(min_value=0, max_value=20),
        action_items_assigned=st.integers(min_value=0, max_value=20),
        unresolved_items=st.integers(min_value=0, max_value=20),
    )


def structured_minutes_response_strategy():
    return st.builds(
        StructuredMinutesResponse,
        title=safe_text,
        summary=safe_text,
        meeting_type_suggestion=st.one_of(st.none(), st.sampled_from(["standup", "planning", "other"])),
        sentiment=st.one_of(st.none(), st.sampled_from(["constructive", "positive", "neutral", "tense", "negative"])),
        participants=st.lists(participant_info_strategy(), max_size=4),
        discussion_points=st.lists(discussion_point_strategy(), max_size=3),
        decisions=st.lists(
            st.builds(
                StructuredDecision,
                description=safe_text,
                made_by=st.one_of(st.none(), safe_name),
                rationale=st.one_of(st.none(), safe_text),
                confidence=st.one_of(st.none(), st.sampled_from(["high", "medium", "low"])),
            ),
            max_size=3,
        ),
        action_items=st.lists(
            st.builds(
                StructuredActionItem,
                description=safe_text,
                owner=st.one_of(st.none(), safe_name),
                due_date=st.one_of(st.none(), st.just("2026-04-30")),
                priority=st.one_of(st.none(), st.sampled_from(["high", "medium", "low"])),
            ),
            max_size=3,
        ),
        risks_and_concerns=st.lists(risk_concern_strategy(), max_size=3),
        follow_ups=st.lists(follow_up_strategy(), max_size=3),
        key_topics=st.lists(safe_word, max_size=6),
        parking_lot=st.lists(safe_text, max_size=3),
        meeting_effectiveness=st.one_of(st.none(), meeting_effectiveness_strategy()),
    )


def minutes_json_strategy():
    """Generate valid MinutesJSON instances."""
    @st.composite
    def _build(draw):
        meeting_id = draw(st.uuids()).hex
        generated_at = draw(safe_datetime)
        attendees = draw(st.lists(safe_name, min_size=0, max_size=5))

        return MinutesJSON(
            meeting_id=meeting_id,
            generated_at=generated_at,
            meeting_type=draw(st.sampled_from(["standup", "planning", "other"])),
            metadata=MinutesMetadata(
                title=draw(safe_text),
                date=draw(safe_datetime).strftime("%Y-%m-%d"),
                duration=f"{draw(st.integers(min_value=15, max_value=120))} minutes",
                attendees=attendees,
                organizer=draw(st.one_of(st.none(), safe_name)),
            ),
            summary=draw(safe_text),
            sections=draw(st.lists(minutes_section_strategy(), min_size=0, max_size=5)),
            action_items=draw(st.lists(action_item_strategy(), min_size=0, max_size=5)),
            decisions=draw(st.lists(decision_strategy(), min_size=0, max_size=5)),
            key_topics=draw(st.lists(safe_word, min_size=0, max_size=8)),
            minutes_markdown=draw(safe_text),
            llm=LLMUsage(
                provider=draw(st.sampled_from(["anthropic", "openai"])),
                model=draw(st.sampled_from(["claude-sonnet-4-6-20250514", "gpt-4o"])),
                tokens_used={"input": draw(st.integers(min_value=100, max_value=10000)), "output": draw(st.integers(min_value=50, max_value=4096))},
                cost_usd=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)),
                processing_time_seconds=draw(st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False)),
            ),
        )

    return _build()


def search_query_strategy():
    """Generate valid SearchQuery instances."""
    return st.builds(
        SearchQuery,
        raw_query=safe_word,
        fts_query=st.one_of(st.just(""), safe_word),
        meeting_type=st.one_of(st.none(), st.sampled_from(["standup", "planning", "other"])),
        after_date=st.one_of(st.none(), safe_datetime),
        before_date=st.one_of(st.none(), safe_datetime),
        limit=st.integers(min_value=1, max_value=100),
        offset=st.integers(min_value=0, max_value=100),
    )


def config_strategy():
    """Generate valid AppConfig YAML strings."""
    from meeting_minutes.config import AppConfig
    import yaml

    @st.composite
    def _build(draw):
        config = AppConfig(
            log_level=draw(st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR"])),
        )
        return yaml.dump(config.model_dump(), default_flow_style=False)

    return _build()
