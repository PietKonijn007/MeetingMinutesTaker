"""Chat engine for 'talk to your notes' — RAG over meeting minutes.

Combines semantic search (embeddings) + FTS5 keyword search for
hybrid retrieval, then sends context + conversation to the user's
configured LLM for synthesis with streamed responses.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from meeting_minutes.config import AppConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a meeting minutes assistant. The user is asking questions about their past meetings.

You have access to relevant excerpts from their meeting transcripts, minutes, action items, and decisions. Use ONLY the provided context to answer the question. If the context doesn't contain enough information, say so.

When referencing specific meetings, include the meeting date and title so the user can find them.

Format your response in clear markdown. Use bullet points for lists. Be concise but thorough.

If the user asks for a summary across multiple meetings, synthesize the key themes and patterns.

Context from meeting minutes:
"""


def _build_context_block(results: list[dict[str, Any]]) -> str:
    """Build a context block from search results for the LLM prompt."""
    blocks: list[str] = []
    seen_texts: set[str] = set()

    for r in results:
        text = r["text"]
        if text in seen_texts:
            continue
        seen_texts.add(text)

        header_parts = []
        if r.get("meeting_date"):
            header_parts.append(f"Date: {r['meeting_date']}")
        if r.get("meeting_id"):
            header_parts.append(f"Meeting: {r['meeting_id'][:8]}")
        if r.get("chunk_type"):
            header_parts.append(f"Type: {r['chunk_type']}")
        if r.get("speaker"):
            header_parts.append(f"Speaker: {r['speaker']}")
        if r.get("owner"):
            header_parts.append(f"Owner: {r['owner']}")

        header = " | ".join(header_parts)
        blocks.append(f"[{header}]\n{text}")

    return "\n\n---\n\n".join(blocks)


def _extract_citations(results: list[dict[str, Any]], session) -> list[dict[str, Any]]:
    """Extract unique meeting citations from search results."""
    from meeting_minutes.system3.db import MeetingORM

    seen_ids: set[str] = set()
    citations: list[dict[str, Any]] = []

    for r in results:
        mid = r.get("meeting_id")
        if not mid or mid in seen_ids:
            continue
        seen_ids.add(mid)

        meeting = session.query(MeetingORM).filter_by(meeting_id=mid).first()
        citations.append({
            "meeting_id": mid,
            "title": meeting.title if meeting else f"Meeting {mid[:8]}",
            "date": r.get("meeting_date"),
            "chunk_type": r.get("chunk_type"),
        })

    return citations


class ChatEngine:
    """Handle chat conversations with RAG over meeting minutes."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._embedding_engine = None

    def _get_embedding_engine(self):
        if self._embedding_engine is None:
            from meeting_minutes.embeddings import EmbeddingEngine
            self._embedding_engine = EmbeddingEngine(self._config)
        return self._embedding_engine

    async def query(
        self,
        user_message: str,
        session,
        session_id: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Process a user query: retrieve context, call LLM, return answer + citations.

        Returns dict with:
            answer: str
            citations: list[dict]  — unique meetings referenced
            session_id: str
        """
        from meeting_minutes.system2.llm_client import LLMClient

        # 1. Parse the query for filters (quick heuristic, not LLM-based for v1)
        filters = self._parse_filters(user_message)

        # 2. Semantic search
        engine = self._get_embedding_engine()
        results = engine.search(
            query=user_message,
            session=session,
            limit=20,
            person=filters.get("person"),
            after_date=filters.get("after_date"),
            before_date=filters.get("before_date"),
            chunk_types=filters.get("chunk_types"),
        )

        if not results:
            return {
                "answer": "I couldn't find any relevant meeting content for your question. Try rephrasing or broadening your search.",
                "citations": [],
                "session_id": session_id or str(uuid.uuid4()),
            }

        # 3. Build context
        context = _build_context_block(results)
        citations = _extract_citations(results, session)

        # 4. Build prompt with conversation history
        full_system = SYSTEM_PROMPT + context
        messages_for_llm = ""
        if conversation_history:
            for msg in conversation_history[-6:]:  # last 3 turns
                role = msg.get("role", "user")
                messages_for_llm += f"\n\n{role.upper()}: {msg['content']}"
        messages_for_llm += f"\n\nUSER: {user_message}"

        # 5. Call LLM
        llm_client = LLMClient(self._config.generation.llm)
        try:
            response = await llm_client.generate(
                prompt=messages_for_llm.strip(),
                system_prompt=full_system,
            )
            answer = response.text
        except Exception as exc:
            logger.error("Chat LLM call failed: %s", exc)
            answer = f"I encountered an error generating a response: {exc}"

        # 6. Save to chat history
        if not session_id:
            session_id = str(uuid.uuid4())

        self._save_messages(
            session=session,
            session_id=session_id,
            user_message=user_message,
            assistant_message=answer,
            citations=citations,
        )

        return {
            "answer": answer,
            "citations": citations,
            "session_id": session_id,
        }

    def _parse_filters(self, query: str) -> dict[str, Any]:
        """Quick heuristic filter extraction from the query text."""
        import re
        filters: dict[str, Any] = {}

        # Date patterns: "since April 1st", "after 2026-04-01", "from March"
        date_pattern = re.search(
            r"(?:since|after|from)\s+(\d{4}-\d{2}-\d{2})", query, re.IGNORECASE
        )
        if date_pattern:
            filters["after_date"] = date_pattern.group(1)
        else:
            # Natural date: "since April 1st" → approximate
            month_match = re.search(
                r"(?:since|after|from)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s*(\d{1,2})?",
                query, re.IGNORECASE,
            )
            if month_match:
                month_names = {
                    "january": "01", "february": "02", "march": "03", "april": "04",
                    "may": "05", "june": "06", "july": "07", "august": "08",
                    "september": "09", "october": "10", "november": "11", "december": "12",
                }
                month_num = month_names.get(month_match.group(1).lower(), "01")
                day = month_match.group(2) or "01"
                year = datetime.now().year
                filters["after_date"] = f"{year}-{month_num}-{int(day):02d}"

        before_pattern = re.search(
            r"(?:before|until|to)\s+(\d{4}-\d{2}-\d{2})", query, re.IGNORECASE
        )
        if before_pattern:
            filters["before_date"] = before_pattern.group(1)

        # Person detection: "with Jon Porter", "about Alice"
        person_match = re.search(
            r"(?:with|about|from|by|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
            query,
        )
        if person_match:
            filters["person"] = person_match.group(1)

        # Note: we intentionally do NOT filter by chunk_type even when the user
        # mentions "decisions" or "actions" — the semantic search already ranks
        # relevant chunks higher, and restricting by type kills recall when the
        # top-matching chunks happen to be transcript windows or summaries. The
        # LLM is smart enough to identify decisions/actions in the context.

        return filters

    def _save_messages(
        self,
        session,
        session_id: str,
        user_message: str,
        assistant_message: str,
        citations: list[dict],
    ) -> None:
        """Save user + assistant messages to chat history."""
        from meeting_minutes.system3.db import ChatSessionORM, ChatMessageORM

        now = datetime.now(timezone.utc)

        # Create session if it doesn't exist
        chat_session = session.query(ChatSessionORM).filter_by(session_id=session_id).first()
        if not chat_session:
            chat_session = ChatSessionORM(
                session_id=session_id,
                title=user_message[:100],
                created_at=now,
                updated_at=now,
            )
            session.add(chat_session)
        else:
            chat_session.updated_at = now

        # Save user message
        session.add(ChatMessageORM(
            message_id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=user_message,
            created_at=now,
        ))

        # Save assistant message
        session.add(ChatMessageORM(
            message_id=str(uuid.uuid4()),
            session_id=session_id,
            role="assistant",
            content=assistant_message,
            citations=json.dumps(citations) if citations else None,
            created_at=now,
        ))

        session.commit()

    def get_sessions(self, session, limit: int = 20) -> list[dict]:
        """List recent chat sessions."""
        from meeting_minutes.system3.db import ChatSessionORM

        sessions = (
            session.query(ChatSessionORM)
            .order_by(ChatSessionORM.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "message_count": len(s.messages),
            }
            for s in sessions
        ]

    def get_messages(self, session, session_id: str) -> list[dict]:
        """Get all messages for a chat session."""
        from meeting_minutes.system3.db import ChatMessageORM

        messages = (
            session.query(ChatMessageORM)
            .filter_by(session_id=session_id)
            .order_by(ChatMessageORM.created_at)
            .all()
        )
        return [
            {
                "message_id": m.message_id,
                "role": m.role,
                "content": m.content,
                "citations": json.loads(m.citations) if m.citations else [],
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
