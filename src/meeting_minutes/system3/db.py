"""SQLAlchemy ORM models and session factory."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
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

    meeting = relationship("MeetingORM", back_populates="action_items")


class DecisionORM(Base):
    __tablename__ = "decisions"

    decision_id = Column(String, primary_key=True)
    meeting_id = Column(String, ForeignKey("meetings.meeting_id"))
    description = Column(Text)
    made_by = Column(String, nullable=True)
    mentioned_at_seconds = Column(Float, nullable=True)

    meeting = relationship("MeetingORM", back_populates="decisions")


class PersonORM(Base):
    __tablename__ = "persons"

    person_id = Column(String, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True, nullable=True)


FTS5_CREATE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
    meeting_id UNINDEXED,
    title,
    transcript_text,
    minutes_text,
    tokenize='porter unicode61'
);
"""


def create_tables(engine) -> None:
    """Create all ORM tables and FTS5 virtual table."""
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text(FTS5_CREATE_SQL))
        conn.commit()


def get_session_factory(db_url: str = "sqlite:///:memory:"):
    """Create engine, tables, and return a session factory."""
    engine = create_engine(db_url, echo=False)
    create_tables(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
