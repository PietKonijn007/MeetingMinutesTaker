"""FastAPI application for the Meeting Minutes Taker web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from meeting_minutes.config import ConfigLoader, resolve_db_path
from meeting_minutes.env import load_dotenv
from meeting_minutes.system3.db import get_session_factory

# Load .env on import so API keys are available to all routes
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the DB session factory on startup; clean up on shutdown."""
    import logging

    config = ConfigLoader.load_default()
    db_path = resolve_db_path(config.storage.sqlite_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app.state.session_factory = get_session_factory(f"sqlite:///{db_path}")

    # PIP-1: flip any `running` pipeline stages older than the threshold to
    # `failed` — the process that owned them is no longer alive.
    from meeting_minutes.pipeline_state import reset_interrupted

    session = app.state.session_factory()
    try:
        reset = reset_interrupted(session)
        if reset:
            meetings = {mid for mid, _ in reset}
            logging.getLogger("meeting_minutes.pipeline").info(
                "Reset %d interrupted pipeline stages across %d meetings",
                len(reset), len(meetings),
            )

        # HLT-1: run the full health check and log results. Do NOT
        # auto-repair — user opts in via `mm repair` or the UI banner.
        try:
            from meeting_minutes.health import check_all

            report = check_all(session, config)
            health_logger = logging.getLogger("meeting_minutes.health")
            if report.overall_status == "ok":
                health_logger.info("Startup health check: all %d checks OK", len(report.checks))
            else:
                health_logger.warning(
                    "Startup health check: overall=%s", report.overall_status,
                )
                for c in report.checks:
                    if c.status != "ok":
                        health_logger.warning(
                            "  [%s] %s: %s", c.status, c.name, c.detail,
                        )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("meeting_minutes.health").warning(
                "Startup health check failed to run: %s", exc,
            )
    finally:
        session.close()

    yield
    # Nothing to clean up — SQLite handles its own close.


app = FastAPI(
    title="Meeting Minutes Taker",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS (origins from config) ────────────────────────────────────────────
_boot_config = ConfigLoader.load_default()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_boot_config.api.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Accept", "Authorization", "X-Api-Key"],
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
from meeting_minutes.api.routes.templates import router as templates_router  # noqa: E402
from meeting_minutes.api.routes.upload import router as upload_router  # noqa: E402
from meeting_minutes.api.routes.backup import router as backup_router  # noqa: E402
from meeting_minutes.api.routes.retention import router as retention_router  # noqa: E402
from meeting_minutes.api.routes.security import router as security_router  # noqa: E402
from meeting_minutes.api.routes.chat import router as chat_router  # noqa: E402
from meeting_minutes.api.routes.pipeline import router as pipeline_router  # noqa: E402
from meeting_minutes.api.routes.health import router as health_router  # noqa: E402
from meeting_minutes.api.routes.doctor import router as doctor_router  # noqa: E402
from meeting_minutes.api.routes.series import (  # noqa: E402
    meeting_lookup_router as series_meeting_router,
    router as series_router,
)
from meeting_minutes.api.routes.brief import router as brief_router  # noqa: E402
from meeting_minutes.api.ws import router as ws_router  # noqa: E402

app.include_router(meetings_router)
app.include_router(search_router)
app.include_router(actions_router)
app.include_router(decisions_router)
app.include_router(people_router)
app.include_router(stats_router)
app.include_router(recording_router)
app.include_router(config_router)
app.include_router(templates_router)
app.include_router(upload_router)
app.include_router(backup_router)
app.include_router(retention_router)
app.include_router(security_router)
app.include_router(chat_router)
app.include_router(pipeline_router)
app.include_router(health_router)
app.include_router(doctor_router)
app.include_router(series_router)
app.include_router(series_meeting_router)
app.include_router(brief_router)
app.include_router(ws_router)

# ── Static files (Svelte SPA) ────────────────────────────────────────────
# Serve the built Svelte app.  For an SPA, any path that doesn't match an
# API route or a real static file must return index.html so the client-side
# router can handle it.
_web_build = Path(__file__).resolve().parent.parent.parent.parent / "web" / "build"
if _web_build.is_dir():
    from fastapi.responses import FileResponse

    # Serve real static assets (JS, CSS, images, etc.) first
    app.mount("/_app", StaticFiles(directory=str(_web_build / "_app")), name="svelte-app")
    if (_web_build / "favicon.svg").exists():
        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon():
            return FileResponse(str(_web_build / "favicon.svg"))

    # SPA catch-all: any GET that doesn't match /api/* returns index.html
    _index_html = _web_build / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # If a real file exists in build dir (e.g. robots.txt), serve it
        requested = _web_build / full_path
        if requested.is_file() and ".." not in full_path:
            return FileResponse(str(requested))
        # Otherwise serve index.html for client-side routing
        return FileResponse(str(_index_html))
