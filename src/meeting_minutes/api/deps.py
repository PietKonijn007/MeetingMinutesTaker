"""FastAPI dependency injection providers."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from meeting_minutes.config import AppConfig, ConfigLoader
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import StorageEngine


def get_config() -> AppConfig:
    """Return the application configuration (loaded once per request)."""
    return ConfigLoader.load_default()


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session from the app-level session factory."""
    session_factory = request.app.state.session_factory
    session: Session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_storage(
    session: Annotated[Session, Depends(get_db_session)],
) -> StorageEngine:
    """Return a StorageEngine bound to the current request session."""
    return StorageEngine(session)


def get_search(
    session: Annotated[Session, Depends(get_db_session)],
) -> SearchEngine:
    """Return a SearchEngine bound to the current request session."""
    return SearchEngine(session)
