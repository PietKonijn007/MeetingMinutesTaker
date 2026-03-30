"""Transcript ingestion and validation for System 2."""

from __future__ import annotations

import json
import re
from pathlib import Path

from meeting_minutes.models import TranscriptData, TranscriptJSON, TranscriptSegment


class TranscriptIngester:
    """Load, validate, and pre-process TranscriptJSON files."""

    SPEAKER_PATTERN = re.compile(r"SPEAKER_\d{2}")
    MIN_SEGMENT_DURATION = 0.5  # seconds; shorter segments get merged

    def ingest(self, transcript_path: Path) -> TranscriptData:
        """Parse, validate, and pre-process transcript JSON."""
        if not transcript_path.exists():
            raise FileNotFoundError(f"Transcript not found: {transcript_path}")

        with open(transcript_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # Validate schema
        try:
            transcript_json = TranscriptJSON(**raw)
        except Exception as exc:
            raise ValueError(f"Invalid transcript JSON: {exc}") from exc

        # Check schema version
        if transcript_json.schema_version != "1.0":
            raise ValueError(
                f"Unsupported schema version: {transcript_json.schema_version}"
            )

        # Build speaker label → name mapping
        label_to_name: dict[str, str] = {}
        for mapping in transcript_json.speakers:
            if mapping.name:
                label_to_name[mapping.label] = mapping.name

        # Load segments from transcript dict
        raw_segments = transcript_json.transcript.get("segments", [])
        segments: list[TranscriptSegment] = []
        for seg_dict in raw_segments:
            seg = TranscriptSegment(**seg_dict)
            segments.append(seg)

        # Replace SPEAKER_XX labels with names in segment text and speaker field
        for seg in segments:
            if seg.speaker and seg.speaker in label_to_name:
                seg.speaker = label_to_name[seg.speaker]
            # Replace inline SPEAKER_XX in text
            def _replace_label(m: re.Match) -> str:
                lbl = m.group(0)
                return label_to_name.get(lbl, lbl)

            seg.text = self.SPEAKER_PATTERN.sub(_replace_label, seg.text)

        # Merge short segments
        segments = self._merge_short_segments(segments)

        # Rebuild full_text after processing
        full_text = self._replace_speaker_labels(
            transcript_json.transcript.get("full_text", ""), label_to_name
        )

        # Collect resolved speaker names
        speakers: list[str] = []
        seen: set[str] = set()
        for seg in segments:
            if seg.speaker and seg.speaker not in seen:
                seen.add(seg.speaker)
                speakers.append(seg.speaker)

        return TranscriptData(
            meeting_id=transcript_json.meeting_id,
            transcript_json=transcript_json,
            full_text=full_text,
            segments=segments,
            speakers=speakers,
        )

    @staticmethod
    def _replace_speaker_labels(text: str, label_to_name: dict[str, str]) -> str:
        pattern = re.compile(r"SPEAKER_\d{2}")

        def _replace(m: re.Match) -> str:
            return label_to_name.get(m.group(0), m.group(0))

        return pattern.sub(_replace, text)

    def _merge_short_segments(
        self, segments: list[TranscriptSegment]
    ) -> list[TranscriptSegment]:
        """Merge consecutive segments that are very short."""
        if len(segments) <= 1:
            return segments

        merged: list[TranscriptSegment] = []
        i = 0
        while i < len(segments):
            seg = segments[i]
            duration = seg.end - seg.start
            if duration < self.MIN_SEGMENT_DURATION and merged:
                prev = merged[-1]
                # Merge into previous
                merged[-1] = TranscriptSegment(
                    id=prev.id,
                    start=prev.start,
                    end=seg.end,
                    speaker=prev.speaker,
                    text=(prev.text + " " + seg.text).strip(),
                    words=prev.words + seg.words,
                )
            else:
                merged.append(seg)
            i += 1

        return merged
