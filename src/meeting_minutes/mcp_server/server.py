"""MCP server entry point — registers tools, resources, and runs stdio transport."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager

from mcp.server import Server
from mcp.server.stdio import stdio_server

from meeting_minutes.config import ConfigLoader, resolve_db_path
from meeting_minutes.env import load_dotenv
from meeting_minutes.system3.db import get_session_factory
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import StorageEngine

from . import resources, tools

_LOG = logging.getLogger(__name__)

load_dotenv()

app = Server("meeting-minutes")


@contextmanager
def _db_context():
    """Yield (StorageEngine, SearchEngine, session) then close the session."""
    config = ConfigLoader.load_default()
    db_path = resolve_db_path(config.storage.sqlite_path)
    session_factory = get_session_factory(f"sqlite:///{db_path}")
    session = session_factory()
    try:
        storage = StorageEngine(session)
        search = SearchEngine(session)
        yield storage, search, session
    finally:
        session.close()


tools.register(app, _db_context)
resources.register(app, _db_context)


async def run() -> None:
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    """Synchronous entry point for the CLI."""
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run())
