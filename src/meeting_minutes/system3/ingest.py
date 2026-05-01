"""Minutes ingestion pipeline for System 3."""

from __future__ import annotations

import json
from pathlib import Path

from meeting_minutes.models import MinutesData, MinutesJSON, TranscriptJSON
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import StorageEngine


class MinutesIngester:
    """Orchestrate ingestion of Minutes_JSON into storage + search index."""

    def __init__(self, storage: StorageEngine, search: SearchEngine) -> None:
        self._storage = storage
        self._search = search

    def ingest(self, minutes_path: Path) -> str:
        """Parse JSON, store in DB, index for search. Returns meeting_id."""
        if not minutes_path.exists():
            raise FileNotFoundError(f"Minutes file not found: {minutes_path}")

        # Decrypt if the file is encrypted
        from meeting_minutes.encryption import is_encrypted, decrypt_file_text

        if is_encrypted(minutes_path):
            from meeting_minutes.config import ConfigLoader

            config = ConfigLoader.load_default()
            text = decrypt_file_text(minutes_path, config.security.encryption_key)
            raw = json.loads(text)
        else:
            with open(minutes_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

        try:
            minutes_json = MinutesJSON(**raw)
        except Exception as exc:
            raise ValueError(f"Invalid minutes JSON: {exc}") from exc

        # Also load the corresponding transcript JSON if it exists
        transcript_json = None
        transcript_dir = minutes_path.parent.parent / "transcripts"
        transcript_path = transcript_dir / f"{minutes_json.meeting_id}.json"
        if transcript_path.exists():
            try:
                if is_encrypted(transcript_path):
                    t_text = decrypt_file_text(
                        transcript_path,
                        ConfigLoader.load_default().security.encryption_key,
                    )
                    transcript_json = TranscriptJSON(**json.loads(t_text))
                else:
                    with open(transcript_path, "r", encoding="utf-8") as tf:
                        transcript_json = TranscriptJSON(**json.load(tf))
            except Exception:
                pass  # Non-fatal — minutes work without transcript

        minutes_data = MinutesData(
            minutes_json=minutes_json,
            transcript_json=transcript_json,
            json_path=str(minutes_path),
            md_path=str(minutes_path.with_suffix(".md")),
        )

        self._storage.upsert_meeting(minutes_data)
        # FTS indexing is done inside upsert_meeting, but also ensure
        # reindex — and pass data_dir so attachment text gets folded
        # into the indexed body. data_dir is the parent of the minutes
        # folder (i.e. the project's data root).
        data_dir = minutes_path.parent.parent
        self._search.reindex_meeting(
            minutes_json.meeting_id,
            data_dir=data_dir,
        )

        return minutes_json.meeting_id
