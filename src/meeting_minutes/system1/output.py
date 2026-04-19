"""Transcript JSON writer — combines transcription + diarization into TranscriptJSON."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from meeting_minutes.models import (
    AudioRecordingResult,
    DiarizationResult,
    SpeakerMapping,
    TranscriptJSON,
    TranscriptMetadata,
    TranscriptionResult,
)
from meeting_minutes.system1.diarize import DiarizationEngine


class TranscriptJSONWriter:
    """Combine audio + transcription + diarization results into TranscriptJSON."""

    PIPELINE_VERSION = "0.1.0"

    def write(
        self,
        meeting_id: str,
        recording: AudioRecordingResult,
        transcription: TranscriptionResult,
        diarization: DiarizationResult | None,
        output_dir: Path,
        speaker_suggestions: dict[str, dict] | None = None,
    ) -> Path:
        """Write transcript JSON to output directory. Returns file path.

        ``speaker_suggestions`` (SPK-1) maps cluster_id to a dict with keys
        ``suggested_person_id``, ``suggested_name``, ``suggestion_score``,
        ``suggestion_tier``. It is merged into the ``speakers`` array so
        the frontend can pre-fill names with the right badge.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Merge diarization into transcript segments
        segments = list(transcription.segments)
        if diarization and diarization.segments:
            segments = DiarizationEngine.merge_transcript_with_diarization(
                segments, diarization
            )

        # Build speaker mappings
        suggestions = speaker_suggestions or {}
        speakers: list[SpeakerMapping] = []
        if diarization and diarization.segments:
            seen_labels: set[str] = set()
            for d_seg in diarization.segments:
                if d_seg.speaker not in seen_labels:
                    seen_labels.add(d_seg.speaker)
                    suggestion = suggestions.get(d_seg.speaker) or {}
                    speakers.append(
                        SpeakerMapping(
                            label=d_seg.speaker,
                            suggested_person_id=suggestion.get("suggested_person_id"),
                            suggested_name=suggestion.get("suggested_name"),
                            suggestion_score=float(suggestion.get("suggestion_score", 0.0)),
                            suggestion_tier=suggestion.get("suggestion_tier"),
                        )
                    )

        metadata = TranscriptMetadata(
            timestamp_start=recording.start_time,
            timestamp_end=recording.end_time,
            duration_seconds=recording.duration_seconds,
            language=transcription.language,
            transcription_engine=transcription.transcription_engine,
            transcription_model=transcription.transcription_model,
            audio_file=recording.audio_file,
            recording_device=recording.recording_device,
        )

        transcript_dict = {
            "segments": [seg.model_dump() for seg in segments],
            "full_text": transcription.full_text,
        }

        processing_dict = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "processing_time_seconds": transcription.processing_time_seconds,
            "pipeline_version": self.PIPELINE_VERSION,
        }

        transcript_json = TranscriptJSON(
            meeting_id=meeting_id,
            metadata=metadata,
            speakers=speakers,
            transcript=transcript_dict,
            processing=processing_dict,
        )

        output_path = output_dir / f"{meeting_id}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(transcript_json.model_dump_json(indent=2))

        return output_path
