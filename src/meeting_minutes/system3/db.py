"""SQLAlchemy ORM models and session factory."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker
from sqlalchemy import create_engine, text


class Base(DeclarativeBase):
    pass


# Association table for meeting ↔ person (many-to-many)
meeting_attendees = Table(
    "meeting_attendees",
    Base.metadata,
    Column("meeting_id", String, ForeignKey("meetings.meeting_id")),
    Column("person_id", String, ForeignKey("persons.person_id")),
)


class MeetingORM(Base):
    __tablename__ = "meetings"

    meeting_id = Column(String, primary_key=True)
    title = Column(String)
    date = Column(DateTime)
    duration = Column(String)
    platform = Column(String, nullable=True)
    meeting_type = Column(String)
    organizer = Column(String, nullable=True)
    status = Column(String, default="draft")
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    transcript = relationship("TranscriptORM", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    minutes = relationship("MinutesORM", back_populates="meeting", uselist=False, cascade="all, delete-orphan")
    action_items = relationship("ActionItemORM", back_populates="meeting", cascade="all, delete-orphan")
    decisions = relationship("DecisionORM", back_populates="meeting", cascade="all, delete-orphan")
    attendees = relationship("PersonORM", secondary=meeting_attendees)


class TranscriptORM(Base):
    __tablename__ = "transcripts"

    meeting_id = Column(String, ForeignKey("meetings.meeting_id"), primary_key=True)
    full_text = Column(Text)
    language = Column(String)
    audio_file_path = Column(String, nullable=True)

    meeting = relationship("MeetingORM", back_populates="transcript")


class MinutesORM(Base):
    __tablename__ = "minutes"

    meeting_id = Column(String, ForeignKey("meetings.meeting_id"), primary_key=True)
    minutes_id = Column(String, unique=True)
    markdown_content = Column(Text)
    summary = Column(Text)
    generated_at = Column(DateTime)
    llm_model = Column(String)
    review_status = Column(String, default="draft")
    sentiment = Column(String, nullable=True)
    structured_json = Column(Text, nullable=True)

    meeting = relationship("MeetingORM", back_populates="minutes")


class ActionItemORM(Base):
    __tablename__ = "action_items"

    action_item_id = Column(String, primary_key=True)
    meeting_id = Column(String, ForeignKey("meetings.meeting_id"))
    description = Column(Text)
    owner = Column(String, nullable=True)
    due_date = Column(String, nullable=True)
    status = Column(String, default="open")
    mentioned_at_seconds = Column(Float, nullable=True)
    priority = Column(String, nullable=True)

    meeting = relationship("MeetingORM", back_populates="action_items")


class DecisionORM(Base):
    __tablename__ = "decisions"

    decision_id = Column(String, primary_key=True)
    meeting_id = Column(String, ForeignKey("meetings.meeting_id"))
    description = Column(Text)
    made_by = Column(String, nullable=True)
    mentioned_at_seconds = Column(Float, nullable=True)
    rationale = Column(Text, nullable=True)
    confidence = Column(String, nullable=True)

    meeting = relationship("MeetingORM", back_populates="decisions")


class PersonORM(Base):
    __tablename__ = "persons"

    person_id = Column(String, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True, nullable=True)


class EmbeddingChunkORM(Base):
    """Stores text chunks alongside their vector embeddings for semantic search."""
    __tablename__ = "embedding_chunks"

    chunk_id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.meeting_id"), index=True)
    chunk_type = Column(String)  # transcript | summary | action_item | decision | discussion_point
    speaker = Column(String, nullable=True)
    text = Column(Text)
    meeting_date = Column(String, nullable=True)
    meeting_type = Column(String, nullable=True)
    owner = Column(String, nullable=True)  # for action_items
    created_at = Column(DateTime)

    meeting = relationship("MeetingORM")


class ChatSessionORM(Base):
    """Stores chat conversations for 'talk to your notes'."""
    __tablename__ = "chat_sessions"

    session_id = Column(String, primary_key=True)
    title = Column(String, nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    messages = relationship("ChatMessageORM", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessageORM.created_at")


class ChatMessageORM(Base):
    __tablename__ = "chat_messages"

    message_id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("chat_sessions.session_id"), index=True)
    role = Column(String)  # user | assistant
    content = Column(Text)
    citations = Column(Text, nullable=True)  # JSON array of {meeting_id, title, date, chunk_text}
    created_at = Column(DateTime)

    session = relationship("ChatSessionORM", back_populates="messages")


class PipelineStageORM(Base):
    """Per-(meeting, stage) state for the resumable pipeline (PIP-1)."""
    __tablename__ = "pipeline_stages"

    meeting_id = Column(
        String,
        ForeignKey("meetings.meeting_id", ondelete="CASCADE"),
        primary_key=True,
    )
    stage = Column(String, primary_key=True)
    status = Column(String, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    attempt = Column(Integer, nullable=False, default=1)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    artifact_path = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "stage IN ('capture','transcribe','diarize','generate','ingest','embed','export')",
            name="ck_pipeline_stages_stage",
        ),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','skipped')",
            name="ck_pipeline_stages_status",
        ),
        Index("idx_pipeline_stages_status", "status"),
    )


FTS5_CREATE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
    meeting_id UNINDEXED,
    title,
    transcript_text,
    minutes_text,
    tokenize='porter unicode61'
);
"""


SQLITE_VEC_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS embedding_vectors USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[384]
);
"""


def _try_load_sqlite_vec(conn):
    """Try to load the sqlite-vec extension. Returns True if successful."""
    try:
        import sqlite_vec
        raw = conn.connection.dbapi_connection
        raw.enable_load_extension(True)
        sqlite_vec.load(raw)
        return True
    except Exception:
        return False


def create_tables(engine) -> None:
    """Create all ORM tables, FTS5 virtual table, and sqlite-vec virtual table."""
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text(FTS5_CREATE_SQL))
        if _try_load_sqlite_vec(conn):
            conn.execute(text(SQLITE_VEC_TABLE_SQL))
        conn.commit()


def get_session_factory(db_url: str = "sqlite:///:memory:"):
    """Create engine, tables, and return a session factory."""
    engine = create_engine(db_url, echo=False)

    # Register listener to load sqlite-vec on every new connection
    @event.listens_for(engine, "connect")
    def _load_vec_on_connect(dbapi_conn, connection_record):
        try:
            import sqlite_vec
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
        except Exception:
            pass

    create_tables(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
