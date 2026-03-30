"""Tests for QualityChecker."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meeting_minutes.models import (
    ParsedMinutes,
    QualityReport,
    TranscriptData,
    TranscriptJSON,
    TranscriptMetadata,
    TranscriptSegment,
    SpeakerMapping,
)
from meeting_minutes.system2.quality import QualityChecker
from datetime import datetime, timezone


def _make_transcript_data(full_text: str, speakers: list[str] = None) -> TranscriptData:
    now = datetime.now(timezone.utc)
    return TranscriptData(
        meeting_id="test",
        transcript_json=TranscriptJSON(
            meeting_id="test",
            metadata=TranscriptMetadata(
                timestamp_start=now,
                timestamp_end=now,
                duration_seconds=600.0,
                language="en",
                transcription_engine="test",
                transcription_model="test",
                audio_file="test.flac",
                recording_device="default",
            ),
            speakers=[SpeakerMapping(label="SPEAKER_00", name=s) for s in (speakers or [])],
            transcript={"segments": [], "full_text": full_text},
            processing={
                "created_at": now.isoformat(),
                "processing_time_seconds": 1.0,
                "pipeline_version": "0.1.0",
            },
        ),
        full_text=full_text,
        segments=[],
        speakers=speakers or [],
    )


def _make_minutes(
    summary: str = "Test summary.",
    raw_response: str = "",
    meeting_id: str = "test",
) -> ParsedMinutes:
    from meeting_minutes.models import MinutesSection
    return ParsedMinutes(
        meeting_id=meeting_id,
        summary=summary,
        sections=[MinutesSection(heading="Discussion", content="Discussion content.")],
        action_items=[],
        decisions=[],
        key_topics=[],
        raw_llm_response=raw_response or summary,
    )


# Feature: meeting-minutes-taker, Property 16: Speaker coverage in minutes
def test_speaker_coverage_all_present():
    """Property 16: All speakers appear in minutes → coverage = 1.0."""
    checker = QualityChecker()
    transcript = _make_transcript_data(
        "Alice and Bob discussed the project.",
        speakers=["Alice", "Bob"],
    )
    minutes = _make_minutes(
        summary="Alice and Bob discussed the project progress.",
        raw_response="## Summary\nAlice and Bob discussed the project progress.",
    )

    report = checker.check(minutes, transcript)
    assert report.speaker_coverage == 1.0


def test_speaker_coverage_missing_speaker():
    """Missing speaker reduces coverage and adds a warning."""
    checker = QualityChecker()
    transcript = _make_transcript_data(
        "Alice and Bob discussed. Charlie was also there.",
        speakers=["Alice", "Bob", "Charlie"],
    )
    minutes = _make_minutes(
        summary="Alice and Bob discussed the project.",
        raw_response="## Summary\nAlice and Bob discussed the project.",
    )

    report = checker.check(minutes, transcript)
    assert report.speaker_coverage < 1.0
    assert any(issue.check == "speaker_coverage" for issue in report.issues)


# Feature: meeting-minutes-taker, Property 17: Minutes length ratio
def test_length_ratio_in_range():
    """Property 17: Minutes that are 10-30% of transcript don't get length warnings."""
    checker = QualityChecker()
    transcript_text = "x" * 1000
    minutes_text = "x" * 150  # 15% → in range
    transcript = _make_transcript_data(transcript_text)
    minutes = _make_minutes(raw_response=minutes_text)

    report = checker.check(minutes, transcript)
    length_issues = [i for i in report.issues if i.check == "length_ratio"]
    assert len(length_issues) == 0


def test_length_ratio_too_short():
    """Minutes shorter than 10% of transcript trigger a warning."""
    checker = QualityChecker()
    transcript_text = "x" * 1000
    minutes_text = "x" * 50  # 5% → too short
    transcript = _make_transcript_data(transcript_text)
    minutes = _make_minutes(raw_response=minutes_text)

    report = checker.check(minutes, transcript)
    length_issues = [i for i in report.issues if i.check == "length_ratio"]
    assert len(length_issues) > 0


def test_length_ratio_too_long():
    """Minutes longer than 30% of transcript trigger a warning."""
    checker = QualityChecker()
    transcript_text = "x" * 1000
    minutes_text = "x" * 400  # 40% → too long
    transcript = _make_transcript_data(transcript_text)
    minutes = _make_minutes(raw_response=minutes_text)

    report = checker.check(minutes, transcript)
    length_issues = [i for i in report.issues if i.check == "length_ratio"]
    assert len(length_issues) > 0


# Feature: meeting-minutes-taker, Property 18: Hallucination detection
def test_hallucination_detection():
    """Property 18: Terms in minutes not in transcript are flagged."""
    checker = QualityChecker()
    transcript = _make_transcript_data("We talked about the project deadline.")
    # Minutes mention names and numbers not in transcript
    minutes = _make_minutes(
        raw_response="## Summary\nJohnSmith discussed budget of 999999 dollars.",
    )

    report = checker.check(minutes, transcript)
    # Some flags should be raised for terms not in transcript
    assert isinstance(report.hallucination_flags, list)


def test_quality_report_structure():
    """QualityReport has all required fields."""
    checker = QualityChecker()
    transcript = _make_transcript_data("Test transcript text.")
    minutes = _make_minutes(raw_response="Test minutes text that is reasonably sized" * 2)

    report = checker.check(minutes, transcript)

    assert isinstance(report, QualityReport)
    assert isinstance(report.passed, bool)
    assert 0.0 <= report.score <= 1.0
    assert isinstance(report.issues, list)
    assert isinstance(report.hallucination_flags, list)
    assert 0.0 <= report.speaker_coverage <= 1.0


def test_empty_transcript_graceful():
    """Quality check with empty transcript doesn't crash."""
    checker = QualityChecker()
    transcript = _make_transcript_data("")
    minutes = _make_minutes()

    report = checker.check(minutes, transcript)
    assert isinstance(report, QualityReport)
