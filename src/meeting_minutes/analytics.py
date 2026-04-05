"""Talk-time analytics computed from diarized transcript segments."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpeakerAnalytics:
    speaker: str
    talk_time_seconds: float = 0.0
    talk_time_percentage: float = 0.0
    segment_count: int = 0
    question_count: int = 0
    monologues: list[dict] = field(default_factory=list)  # [{start, end, duration_seconds}]


@dataclass
class TalkTimeAnalytics:
    total_duration_seconds: float = 0.0
    speakers: list[SpeakerAnalytics] = field(default_factory=list)
    has_diarization: bool = False


def compute_talk_time_analytics(transcript_json_path: str | Path) -> TalkTimeAnalytics | None:
    """Compute per-speaker talk-time analytics from a transcript JSON file.

    Returns None if the file doesn't exist or has no segments.
    """
    path = Path(transcript_json_path)
    if not path.exists():
        return None

    with open(path) as f:
        data = json.load(f)

    segments = data.get("transcript", {}).get("segments", [])
    if not segments:
        return None

    # Check if we have diarization (any segment with a non-null speaker)
    has_diarization = any(seg.get("speaker") for seg in segments)

    total_duration = data.get("metadata", {}).get("duration_seconds", 0.0)
    if not total_duration and segments:
        total_duration = max(seg.get("end", 0) for seg in segments)

    # Build speaker map from transcript
    speaker_names = {}
    for sm in data.get("speakers", []):
        speaker_names[sm.get("label", "")] = sm.get("name") or sm.get("label", "")

    # Accumulate per-speaker stats
    speaker_data: dict[str, SpeakerAnalytics] = {}
    question_re = re.compile(r"\?\s*$")
    monologue_threshold = 180.0  # 3 minutes

    # Track consecutive segments by same speaker for monologue detection
    prev_speaker = None
    consecutive_start = 0.0
    consecutive_end = 0.0

    def _flush_consecutive(speaker: str, start: float, end: float) -> None:
        duration = end - start
        if duration >= monologue_threshold and speaker in speaker_data:
            speaker_data[speaker].monologues.append({
                "start": round(start, 1),
                "end": round(end, 1),
                "duration_seconds": round(duration, 1),
            })

    for seg in segments:
        speaker_label = seg.get("speaker") or "Unknown"
        speaker_name = speaker_names.get(speaker_label, speaker_label)
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "")
        duration = end - start

        if speaker_name not in speaker_data:
            speaker_data[speaker_name] = SpeakerAnalytics(speaker=speaker_name)

        sa = speaker_data[speaker_name]
        sa.talk_time_seconds += duration
        sa.segment_count += 1

        # Count questions (sentences ending with ?)
        sentences = re.split(r"[.!?]+", text)
        for sentence in sentences:
            if question_re.search(sentence.strip() + "?"):
                # Check if original text has ? after this sentence
                pass
        # Simpler: count ? occurrences in text
        sa.question_count += text.count("?")

        # Monologue tracking
        if speaker_name == prev_speaker:
            consecutive_end = end
        else:
            if prev_speaker is not None:
                _flush_consecutive(prev_speaker, consecutive_start, consecutive_end)
            prev_speaker = speaker_name
            consecutive_start = start
            consecutive_end = end

    # Flush last consecutive block
    if prev_speaker is not None:
        _flush_consecutive(prev_speaker, consecutive_start, consecutive_end)

    # Compute percentages
    total_talk_time = sum(sa.talk_time_seconds for sa in speaker_data.values())
    if total_talk_time > 0:
        for sa in speaker_data.values():
            sa.talk_time_percentage = round(
                (sa.talk_time_seconds / total_talk_time) * 100, 1
            )

    # Sort by talk time descending
    speakers_list = sorted(
        speaker_data.values(), key=lambda s: s.talk_time_seconds, reverse=True
    )

    # Round talk times
    for sa in speakers_list:
        sa.talk_time_seconds = round(sa.talk_time_seconds, 1)

    return TalkTimeAnalytics(
        total_duration_seconds=round(total_duration, 1),
        speakers=speakers_list,
        has_diarization=has_diarization,
    )
