"""Embedding engine for semantic search over meeting minutes.

Chunks meeting data (transcript, summary, action items, decisions,
discussion points) and stores vector embeddings in sqlite-vec for
hybrid FTS5 + semantic retrieval.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meeting_minutes.config import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
CHUNK_MAX_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 50


class EmbeddingEngine:
    """Generate and store embeddings for meeting chunks."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._model = None
        self._model_name = DEFAULT_MODEL

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers not installed. Run: pip install sentence-transformers"
            ) from exc

        logger.info("Loading embedding model: %s", self._model_name)
        t0 = time.time()
        self._model = SentenceTransformer(self._model_name)
        logger.info("Embedding model loaded in %.1fs", time.time() - t0)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of float vectors."""
        if not texts:
            return []
        model = self._load_model()
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [e.tolist() for e in embeddings]

    def embed_query(self, query: str) -> list[float]:
        """Embed a single search query."""
        model = self._load_model()
        embedding = model.encode(query, normalize_embeddings=True, show_progress_bar=False)
        return embedding.tolist()

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def chunk_meeting(self, meeting_id: str, data_dir: Path) -> list[dict[str, Any]]:
        """Break a meeting into embeddable chunks from on-disk JSON files.

        Returns list of chunk dicts with keys:
            meeting_id, chunk_type, speaker, text, meeting_date, meeting_type, owner
        """
        chunks: list[dict[str, Any]] = []

        minutes_path = data_dir / "minutes" / f"{meeting_id}.json"
        transcript_path = data_dir / "transcripts" / f"{meeting_id}.json"

        meeting_date = None
        meeting_type = None

        # --- Minutes chunks ---
        if minutes_path.exists():
            try:
                mdata = json.loads(minutes_path.read_text())
                meeting_date = mdata.get("metadata", {}).get("date")
                meeting_type = mdata.get("meeting_type")

                # Summary
                summary = mdata.get("summary")
                if summary:
                    chunks.append({
                        "meeting_id": meeting_id,
                        "chunk_type": "summary",
                        "speaker": None,
                        "text": summary,
                        "meeting_date": meeting_date,
                        "meeting_type": meeting_type,
                        "owner": None,
                    })

                # Discussion points
                for dp in mdata.get("discussion_points", []) or []:
                    if isinstance(dp, dict) and dp.get("summary"):
                        topic = dp.get("topic", "")
                        text = f"{topic}: {dp['summary']}" if topic else dp["summary"]
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "discussion_point",
                            "speaker": None,
                            "text": text,
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": None,
                        })

                # Action items
                for ai in mdata.get("action_items", []) or []:
                    if isinstance(ai, dict) and ai.get("description"):
                        owner = ai.get("owner")
                        text = ai["description"]
                        if owner:
                            text = f"[Action for {owner}] {text}"
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "action_item",
                            "speaker": None,
                            "text": text,
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": owner,
                        })

                # Decisions
                for d in mdata.get("decisions", []) or []:
                    if isinstance(d, dict) and d.get("description"):
                        made_by = d.get("made_by")
                        text = d["description"]
                        rationale = d.get("rationale")
                        if rationale:
                            text += f" — Rationale: {rationale}"
                        if made_by:
                            text = f"[Decision by {made_by}] {text}"
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "decision",
                            "speaker": None,
                            "text": text,
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": made_by,
                        })

                # Risks & concerns
                for rc in mdata.get("risks_and_concerns", []) or []:
                    if isinstance(rc, dict) and rc.get("description"):
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "risk",
                            "speaker": rc.get("raised_by"),
                            "text": rc["description"],
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": rc.get("raised_by"),
                        })

                # Follow-ups
                for fu in mdata.get("follow_ups", []) or []:
                    if isinstance(fu, dict) and fu.get("description"):
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "follow_up",
                            "speaker": None,
                            "text": fu["description"],
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": fu.get("owner"),
                        })

            except Exception as exc:
                logger.warning("Failed to chunk minutes for %s: %s", meeting_id, exc)

        # --- Transcript chunks (sliding window over segments) ---
        if transcript_path.exists():
            try:
                tdata = json.loads(transcript_path.read_text())
                segments = tdata.get("transcript", {}).get("segments", []) or []

                if not meeting_date:
                    meeting_date = tdata.get("metadata", {}).get("timestamp_start", "")[:10]

                current_chunk: list[str] = []
                current_speaker = None
                current_word_count = 0

                for seg in segments:
                    seg_text = seg.get("text", "").strip()
                    seg_speaker = seg.get("speaker")
                    if not seg_text:
                        continue

                    words = seg_text.split()
                    current_chunk.append(seg_text)
                    current_word_count += len(words)
                    if seg_speaker:
                        current_speaker = seg_speaker

                    if current_word_count >= CHUNK_MAX_TOKENS:
                        chunk_text = " ".join(current_chunk)
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "transcript",
                            "speaker": current_speaker,
                            "text": chunk_text,
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": None,
                        })
                        # Keep overlap
                        overlap_words = " ".join(current_chunk).split()[-CHUNK_OVERLAP_TOKENS:]
                        current_chunk = [" ".join(overlap_words)]
                        current_word_count = len(overlap_words)

                # Flush remaining
                if current_chunk:
                    chunk_text = " ".join(current_chunk)
                    if len(chunk_text.split()) > 10:
                        chunks.append({
                            "meeting_id": meeting_id,
                            "chunk_type": "transcript",
                            "speaker": current_speaker,
                            "text": chunk_text,
                            "meeting_date": meeting_date,
                            "meeting_type": meeting_type,
                            "owner": None,
                        })

            except Exception as exc:
                logger.warning("Failed to chunk transcript for %s: %s", meeting_id, exc)

        return chunks

    # ------------------------------------------------------------------
    # Index a single meeting
    # ------------------------------------------------------------------

    def index_meeting(self, meeting_id: str, session, data_dir: Path) -> int:
        """Chunk a meeting, embed, and store in the DB. Returns chunk count.

        Deletes any existing embeddings for this meeting first (idempotent).
        """
        from sqlalchemy import text as sql_text
        from meeting_minutes.system3.db import EmbeddingChunkORM

        # Delete existing chunks for this meeting
        session.query(EmbeddingChunkORM).filter_by(meeting_id=meeting_id).delete()
        try:
            session.execute(sql_text(
                "DELETE FROM embedding_vectors WHERE chunk_id IN "
                "(SELECT chunk_id FROM embedding_chunks WHERE meeting_id = :mid)"
            ), {"mid": meeting_id})
        except Exception:
            pass  # Table may not exist yet

        # Chunk the meeting
        chunks = self.chunk_meeting(meeting_id, data_dir)
        if not chunks:
            session.commit()
            return 0

        # Embed all chunk texts
        texts = [c["text"] for c in chunks]
        vectors = self.embed_texts(texts)

        now = datetime.now(timezone.utc)

        # Store chunks + vectors
        for chunk_data, vector in zip(chunks, vectors):
            chunk_orm = EmbeddingChunkORM(
                meeting_id=chunk_data["meeting_id"],
                chunk_type=chunk_data["chunk_type"],
                speaker=chunk_data.get("speaker"),
                text=chunk_data["text"],
                meeting_date=chunk_data.get("meeting_date"),
                meeting_type=chunk_data.get("meeting_type"),
                owner=chunk_data.get("owner"),
                created_at=now,
            )
            session.add(chunk_orm)
            session.flush()  # Get the auto-generated chunk_id

            # Insert vector into sqlite-vec virtual table
            import struct
            vec_bytes = struct.pack(f"{len(vector)}f", *vector)
            try:
                session.execute(sql_text(
                    "INSERT INTO embedding_vectors (chunk_id, embedding) VALUES (:cid, :vec)"
                ), {"cid": chunk_orm.chunk_id, "vec": vec_bytes})
            except Exception as exc:
                logger.warning("Failed to insert vector for chunk %d: %s", chunk_orm.chunk_id, exc)

        session.commit()
        return len(chunks)

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        session,
        limit: int = 15,
        person: str | None = None,
        after_date: str | None = None,
        before_date: str | None = None,
        chunk_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search over embedded chunks. Returns ranked results with metadata."""
        import struct
        from sqlalchemy import text as sql_text

        query_vec = self.embed_query(query)
        vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)

        # sqlite-vec KNN query: find nearest chunks
        # We fetch more than limit to allow post-filtering
        fetch_limit = limit * 5

        try:
            rows = session.execute(sql_text(
                "SELECT chunk_id, distance FROM embedding_vectors "
                "WHERE embedding MATCH :qvec "
                "ORDER BY distance LIMIT :lim"
            ), {"qvec": vec_bytes, "lim": fetch_limit}).fetchall()
        except Exception as exc:
            logger.warning("sqlite-vec search failed: %s", exc)
            return []

        if not rows:
            return []

        # Load chunk metadata for the returned IDs
        chunk_ids = [r[0] for r in rows]
        distances = {r[0]: r[1] for r in rows}

        from meeting_minutes.system3.db import EmbeddingChunkORM
        chunk_orms = session.query(EmbeddingChunkORM).filter(
            EmbeddingChunkORM.chunk_id.in_(chunk_ids)
        ).all()

        # Build results with filtering
        results = []
        for chunk in chunk_orms:
            # Apply metadata filters
            if person:
                p_lower = person.lower()
                text_match = p_lower in (chunk.text or "").lower()
                owner_match = p_lower in (chunk.owner or "").lower()
                speaker_match = p_lower in (chunk.speaker or "").lower()
                if not (text_match or owner_match or speaker_match):
                    continue
            if after_date and chunk.meeting_date and chunk.meeting_date < after_date:
                continue
            if before_date and chunk.meeting_date and chunk.meeting_date > before_date:
                continue
            if chunk_types and chunk.chunk_type not in chunk_types:
                continue

            results.append({
                "chunk_id": chunk.chunk_id,
                "meeting_id": chunk.meeting_id,
                "chunk_type": chunk.chunk_type,
                "speaker": chunk.speaker,
                "text": chunk.text,
                "meeting_date": chunk.meeting_date,
                "meeting_type": chunk.meeting_type,
                "owner": chunk.owner,
                "distance": distances.get(chunk.chunk_id, 999),
            })

        # Sort by distance (lower = closer match)
        results.sort(key=lambda r: r["distance"])
        return results[:limit]
