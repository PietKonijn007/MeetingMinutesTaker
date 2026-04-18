"""Chat API endpoints — 'talk to your notes' via RAG."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict] = []
    session_id: str


class ChatSessionListItem(BaseModel):
    session_id: str
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0


class ChatMessageItem(BaseModel):
    message_id: str
    role: str
    content: str
    citations: list[dict] = []
    created_at: str | None = None


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Send a message and get an AI-powered answer based on your meeting minutes."""
    from meeting_minutes.chat import ChatEngine

    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    engine = ChatEngine(config)

    # Load conversation history if continuing a session
    history = []
    if body.session_id:
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in engine.get_messages(session, body.session_id)
        ]

    result = await engine.query(
        user_message=body.message,
        session=session,
        session_id=body.session_id,
        conversation_history=history,
    )

    return ChatResponse(
        answer=result["answer"],
        citations=result["citations"],
        session_id=result["session_id"],
    )


@router.get("/sessions", response_model=list[ChatSessionListItem])
def list_chat_sessions(
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """List recent chat sessions."""
    from meeting_minutes.chat import ChatEngine

    engine = ChatEngine(config)
    return engine.get_sessions(session)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageItem])
def get_chat_messages(
    session_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Get all messages for a chat session."""
    from meeting_minutes.chat import ChatEngine

    engine = ChatEngine(config)
    messages = engine.get_messages(session, session_id)
    if not messages:
        raise HTTPException(status_code=404, detail=f"Chat session not found: {session_id}")
    return messages


@router.delete("/sessions/{session_id}")
def delete_chat_session(
    session_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Delete a chat session and all its messages."""
    from meeting_minutes.system3.db import ChatSessionORM

    chat_session = session.query(ChatSessionORM).filter_by(session_id=session_id).first()
    if not chat_session:
        raise HTTPException(status_code=404, detail=f"Chat session not found: {session_id}")

    session.delete(chat_session)
    session.commit()
    return {"deleted": session_id}
