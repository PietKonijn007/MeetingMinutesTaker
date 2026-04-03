"""Parse uploaded transcript files (TXT, CSV, JSON) into TranscriptJSON format."""

import csv
import io
import json
import uuid
from datetime import datetime, timezone

from meeting_minutes.models import TranscriptJSON, TranscriptMetadata, TranscriptSegment, SpeakerMapping


def parse_uploaded_transcript(
    content: str,
    filename: str,
    title: str,
    date: str,
    time_str: str = "",
    attendees: list[str] | None = None,
    meeting_type: str = "other",
    language: str = "en",
) -> TranscriptJSON:
    """Parse an uploaded file into TranscriptJSON format.

    Supports: .txt (plain text), .csv (speaker,text rows), .json (multiple formats)
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext == "csv":
        full_text, segments, speakers = _parse_csv(content)
    elif ext == "json":
        full_text, segments, speakers = _parse_json(content)
    else:
        full_text, segments, speakers = _parse_txt(content)

    # Build timestamp
    if time_str:
        timestamp = datetime.fromisoformat(f"{date}T{time_str}:00")
    else:
        timestamp = datetime.fromisoformat(f"{date}T00:00:00")
    timestamp = timestamp.replace(tzinfo=timezone.utc)

    # Estimate duration from segments or text length
    duration = segments[-1].end if segments and segments[-1].end > 0 else len(full_text) / 20  # rough: 20 chars/sec

    # Build attendee speaker mappings
    speaker_mappings = speakers or []
    if attendees and not speaker_mappings:
        speaker_mappings = [
            SpeakerMapping(label=f"SPEAKER_{i:02d}", name=name.strip())
            for i, name in enumerate(attendees) if name.strip()
        ]

    meeting_id = str(uuid.uuid4())

    return TranscriptJSON(
        schema_version="1.0",
        meeting_id=meeting_id,
        metadata=TranscriptMetadata(
            timestamp_start=timestamp,
            timestamp_end=timestamp,  # will be updated
            duration_seconds=duration,
            language=language,
            transcription_engine="uploaded",
            transcription_model="external",
            audio_file="",
            recording_device="uploaded",
            platform="upload",
        ),
        speakers=speaker_mappings,
        meeting_type=meeting_type,
        meeting_type_confidence=0.0,  # will be classified by LLM
        transcript={
            "segments": [s.model_dump() for s in segments],
            "full_text": full_text,
        },
        processing={
            "created_at": datetime.now(timezone.utc).isoformat(),
            "processing_time_seconds": 0,
            "pipeline_version": "1.0.0",
        },
    )


def _parse_txt(content: str) -> tuple[str, list[TranscriptSegment], list[SpeakerMapping]]:
    """Parse plain text -- entire content becomes full_text, one segment."""
    full_text = content.strip()
    segments = [
        TranscriptSegment(id=0, start=0.0, end=0.0, text=full_text)
    ] if full_text else []
    return full_text, segments, []


def _parse_csv(content: str) -> tuple[str, list[TranscriptSegment], list[SpeakerMapping]]:
    """Parse CSV with flexible column detection.

    Supports formats:
    - speaker,text
    - timestamp,speaker,text
    - text (single column)
    - Any header names containing 'speaker'/'name' and 'text'/'content'/'message'
    """
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return "", [], []

    # Detect header
    header = [h.strip().lower() for h in rows[0]]
    has_header = any(h in header for h in ["speaker", "text", "name", "content", "message", "timestamp"])

    if has_header:
        data_rows = rows[1:]
    else:
        header = []
        data_rows = rows

    # Find column indices
    speaker_col = None
    text_col = None
    time_col = None

    for i, h in enumerate(header):
        if h in ("speaker", "name", "person", "who"):
            speaker_col = i
        elif h in ("text", "content", "message", "transcript", "caption"):
            text_col = i
        elif h in ("timestamp", "time", "start", "start_time"):
            time_col = i

    # If no header detected, guess by column count
    if text_col is None:
        if len(header) >= 2 or (not has_header and len(data_rows[0]) >= 2):
            num_cols = len(data_rows[0]) if data_rows else 0
            if num_cols >= 3:
                time_col, speaker_col, text_col = 0, 1, 2
            elif num_cols == 2:
                speaker_col, text_col = 0, 1
            else:
                text_col = 0
        else:
            text_col = 0

    segments = []
    speakers_seen: dict[str, str] = {}
    full_text_parts = []

    for i, row in enumerate(data_rows):
        if not row or all(not cell.strip() for cell in row):
            continue

        text = row[text_col].strip() if text_col is not None and text_col < len(row) else ""
        speaker = row[speaker_col].strip() if speaker_col is not None and speaker_col < len(row) else None

        timestamp = 0.0
        if time_col is not None and time_col < len(row):
            try:
                ts = row[time_col].strip()
                # Try MM:SS or HH:MM:SS format
                parts = ts.split(":")
                if len(parts) == 2:
                    timestamp = float(parts[0]) * 60 + float(parts[1])
                elif len(parts) == 3:
                    timestamp = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                else:
                    timestamp = float(ts)
            except (ValueError, IndexError):
                pass

        if not text:
            continue

        # Track speakers
        speaker_label = None
        if speaker:
            if speaker not in speakers_seen:
                speakers_seen[speaker] = f"SPEAKER_{len(speakers_seen):02d}"
            speaker_label = speakers_seen[speaker]

        segments.append(TranscriptSegment(
            id=i,
            start=timestamp,
            end=timestamp,
            speaker=speaker_label,
            text=text,
        ))

        prefix = f"{speaker}: " if speaker else ""
        full_text_parts.append(f"{prefix}{text}")

    full_text = "\n".join(full_text_parts)

    speaker_mappings = [
        SpeakerMapping(label=label, name=name)
        for name, label in speakers_seen.items()
    ]

    return full_text, segments, speaker_mappings


def _parse_json(content: str) -> tuple[str, list[TranscriptSegment], list[SpeakerMapping]]:
    """Parse JSON -- handles multiple formats:

    1. Our TranscriptJSON format (has 'transcript' key with 'segments' and 'full_text')
    2. Simple format: {"segments": [{"speaker": "...", "text": "..."}]}
    3. Teams/Zoom format: {"discussions": [{"captions": [{"name": "...", "text": "..."}]}]}
    4. Array of objects: [{"speaker": "...", "text": "..."}]
    """
    data = json.loads(content)

    # Format 1: Our native TranscriptJSON
    if isinstance(data, dict) and "transcript" in data and "full_text" in data.get("transcript", {}):
        full_text = data["transcript"]["full_text"]
        segments = []
        for i, seg in enumerate(data["transcript"].get("segments", [])):
            segments.append(TranscriptSegment(
                id=seg.get("id", i),
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                speaker=seg.get("speaker"),
                text=seg.get("text", ""),
            ))
        speakers = [
            SpeakerMapping(label=s.get("label", f"SPEAKER_{i:02d}"), name=s.get("name"))
            for i, s in enumerate(data.get("speakers", []))
        ]
        return full_text, segments, speakers

    # Format 2: Simple segments array
    if isinstance(data, dict) and "segments" in data:
        return _parse_json_segments(data["segments"])

    # Format 3: Teams/Zoom export
    if isinstance(data, dict) and "discussions" in data:
        captions = []
        for disc in data["discussions"]:
            captions.extend(disc.get("captions", []))
        if captions:
            segments_data = [{"speaker": c.get("name"), "text": c.get("text", "")} for c in captions]
            return _parse_json_segments(segments_data)

    # Format 4: Direct array of objects
    if isinstance(data, list):
        return _parse_json_segments(data)

    # Fallback: dump as text
    return json.dumps(data, indent=2), [], []


def _parse_json_segments(segments_data: list) -> tuple[str, list[TranscriptSegment], list[SpeakerMapping]]:
    """Parse a list of segment-like objects."""
    segments = []
    speakers_seen: dict[str, str] = {}
    full_text_parts = []

    for i, item in enumerate(segments_data):
        if not isinstance(item, dict):
            continue

        text = item.get("text", item.get("content", item.get("message", "")))
        speaker = item.get("speaker", item.get("name", item.get("person")))
        start = float(item.get("start", item.get("start_time", item.get("timestamp", 0))))
        end = float(item.get("end", item.get("end_time", start)))

        if not text:
            continue

        speaker_label = None
        if speaker:
            if speaker not in speakers_seen:
                speakers_seen[speaker] = f"SPEAKER_{len(speakers_seen):02d}"
            speaker_label = speakers_seen[speaker]

        segments.append(TranscriptSegment(
            id=i, start=start, end=end, speaker=speaker_label, text=text,
        ))

        prefix = f"{speaker}: " if speaker else ""
        full_text_parts.append(f"{prefix}{text}")

    full_text = "\n".join(full_text_parts)
    speaker_mappings = [
        SpeakerMapping(label=label, name=name)
        for name, label in speakers_seen.items()
    ]

    return full_text, segments, speaker_mappings
