"""Quality assurance checks for generated minutes."""

from __future__ import annotations

import re
from typing import Optional

from meeting_minutes.models import (
    ParsedMinutes,
    QualityIssue,
    QualityReport,
    TranscriptData,
)


# Regex for detecting proper nouns (capitalized sequences), dates, and numbers
PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:,\s+\d{4})?)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


class QualityChecker:
    """Validate generated minutes against quality criteria."""

    LENGTH_RATIO_MIN = 0.10
    LENGTH_RATIO_MAX = 0.30

    def check(
        self,
        minutes: ParsedMinutes,
        transcript: TranscriptData,
    ) -> QualityReport:
        """Run completeness, length, and hallucination checks."""
        issues: list[QualityIssue] = []
        speaker_coverage = self._check_speaker_coverage(minutes, transcript, issues)
        length_ratio = self._check_length_ratio(minutes, transcript, issues)
        hallucination_flags = self._check_hallucinations(minutes, transcript, issues)

        # Score: reduce for each warning/error
        penalty = sum(0.1 for issue in issues if issue.severity == "warning")
        penalty += sum(0.2 for issue in issues if issue.severity == "error")
        score = max(0.0, 1.0 - penalty)

        passed = len([i for i in issues if i.severity == "error"]) == 0

        return QualityReport(
            passed=passed,
            score=score,
            issues=issues,
            speaker_coverage=speaker_coverage,
            length_ratio=length_ratio,
            hallucination_flags=hallucination_flags,
        )

    def _check_speaker_coverage(
        self,
        minutes: ParsedMinutes,
        transcript: TranscriptData,
        issues: list[QualityIssue],
    ) -> float:
        """Check that every speaker in the transcript appears in the minutes."""
        if not transcript.speakers:
            return 1.0

        minutes_text = minutes.summary + "\n".join(
            s.content for s in minutes.sections
        )

        found = sum(
            1 for speaker in transcript.speakers
            if speaker and speaker.lower() in minutes_text.lower()
        )
        coverage = found / len(transcript.speakers)

        if coverage < 1.0:
            missing = [
                s for s in transcript.speakers
                if s and s.lower() not in minutes_text.lower()
            ]
            issues.append(
                QualityIssue(
                    check="speaker_coverage",
                    severity="warning",
                    message=f"Some speakers not mentioned in minutes",
                    details={"missing_speakers": missing, "coverage": coverage},
                )
            )

        return coverage

    def _check_length_ratio(
        self,
        minutes: ParsedMinutes,
        transcript: TranscriptData,
        issues: list[QualityIssue],
    ) -> float:
        """Check that minutes are 10-30% of transcript length."""
        if not transcript.full_text:
            return 0.0

        minutes_text = minutes.raw_llm_response or (
            minutes.summary
            + "\n".join(s.content for s in minutes.sections)
        )

        transcript_len = len(transcript.full_text)
        minutes_len = len(minutes_text)

        if transcript_len == 0:
            return 0.0

        ratio = minutes_len / transcript_len

        if ratio < self.LENGTH_RATIO_MIN:
            issues.append(
                QualityIssue(
                    check="length_ratio",
                    severity="warning",
                    message=f"Minutes are too short (ratio: {ratio:.2%}, min: {self.LENGTH_RATIO_MIN:.0%})",
                    details={"ratio": ratio, "min": self.LENGTH_RATIO_MIN},
                )
            )
        elif ratio > self.LENGTH_RATIO_MAX:
            issues.append(
                QualityIssue(
                    check="length_ratio",
                    severity="warning",
                    message=f"Minutes are too long (ratio: {ratio:.2%}, max: {self.LENGTH_RATIO_MAX:.0%})",
                    details={"ratio": ratio, "max": self.LENGTH_RATIO_MAX},
                )
            )

        return ratio

    def _check_hallucinations(
        self,
        minutes: ParsedMinutes,
        transcript: TranscriptData,
        issues: list[QualityIssue],
    ) -> list[str]:
        """Flag entities in minutes not found in transcript."""
        if not transcript.full_text:
            return []

        transcript_lower = transcript.full_text.lower()
        minutes_text = minutes.raw_llm_response or ""

        flags: list[str] = []

        # Check proper nouns
        for match in PROPER_NOUN_RE.finditer(minutes_text):
            term = match.group(0)
            # Skip common words
            if term.lower() in _COMMON_WORDS:
                continue
            if term.lower() not in transcript_lower:
                flags.append(term)

        # Check dates
        for match in DATE_RE.finditer(minutes_text):
            term = match.group(0)
            if term.lower() not in transcript_lower:
                flags.append(term)

        # Check numbers (only substantial numbers > 3 digits to reduce noise)
        for match in NUMBER_RE.finditer(minutes_text):
            term = match.group(0)
            if len(term) > 3 and term not in transcript_lower:
                flags.append(term)

        # Deduplicate
        flags = list(dict.fromkeys(flags))

        if flags:
            issues.append(
                QualityIssue(
                    check="hallucination",
                    severity="warning",
                    message=f"Possible hallucinations detected ({len(flags)} items not in transcript)",
                    details={"flags": flags[:20]},  # cap at 20
                )
            )

        return flags


_COMMON_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "shall", "can",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "action", "items", "summary", "discussion", "decisions", "meeting",
    "attendees", "agenda", "notes", "next", "steps", "key", "topics",
    "i", "we", "you", "he", "she", "they", "it", "this", "that", "these",
    "those", "all", "any", "some", "no", "not", "yes",
}
