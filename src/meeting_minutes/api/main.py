"""FastAPI application for the Meeting Minutes Taker web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from meeting_minutes.config import ConfigLoader
from meeting_minutes.env import load_dotenv
from meeting_minutes.system3.db import get_session_factory

# Load .env on import so API keys are available to all routes
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the DB session factory on startup; clean up on shutdown."""
    config = ConfigLoader.load_default()
    db_path = Path(config.storage.sqlite_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app.state.session_factory = get_session_factory(f"sqlite:///{db_path}")
    yield
    # Nothing to clean up — SQLite handles its own close.


app = FastAPI(
    title="Meeting Minutes Taker",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────
from meeting_minutes.api.routes.actions import router as actions_router  # noqa: E402
from meeting_minutes.api.routes.config import router as config_router  # noqa: E402
from meeting_minutes.api.routes.decisions import router as decisions_router  # noqa: E402
from meeting_minutes.api.routes.meetings import router as meetings_router  # noqa: E402
from meeting_minutes.api.routes.people import router as people_router  # noqa: E402
from meeting_minutes.api.routes.recording import router as recording_router  # noqa: E402
from meeting_minutes.api.routes.search import router as search_router  # noqa: E402
from meeting_minutes.api.routes.stats import router as stats_router  # noqa: E402
from meeting_minutes.api.ws import router as ws_router  # noqa: E402

app.include_router(meetings_router)
app.include_router(search_router)
app.include_router(actions_router)
app.include_router(decisions_router)
app.include_router(people_router)
app.include_router(stats_router)
app.include_router(recording_router)
app.include_router(config_router)
app.include_router(ws_router)

# ── Static files (Svelte build) ──────────────────────────────────────────
# Mounted last so that /api/* routes take precedence.
_web_build = Path(__file__).resolve().parent.parent.parent.parent / "web" / "build"
if _web_build.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_build), html=True), name="static")
