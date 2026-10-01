"""FastAPI entry point.

Mounts:
  /api/pipeline   — queue runs, re-run phases, fetch state
  /api/jobs       — the job queue: what is running, what failed, cancel
  /api/edit       — natural-language edits (Phase 5)
  /api/history    — version history + revert
  /api/projects   — list known projects
  /api/voices     — voice engines, and a sample to listen to
  /ws/progress    — live progress events for a project's current job
  /assets/...     — static asset server for generated images/videos
  /              — single-page HTML UI

By default the API also runs a worker thread, so `python main.py serve` is still
the only command needed on a laptop. Set WORKER_INLINE=0 and run
`python main.py worker` separately to scale them apart.
"""
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Ensure all MCP tools are registered.
import mcp.tools  # noqa: F401

from shared import assets, db
from shared.constants import OUTPUTS_DIR
from shared.utils.logging import get_logger

from .routes import edit as edit_routes
from .routes import history as history_routes
from .routes import jobs as job_routes
from .routes import pipeline as pipeline_routes
from .routes import projects as project_routes
from .routes import voices as voice_routes
from .websocket import progress as progress_ws

log = get_logger("api")

_worker: dict = {}


def _inline_worker_enabled() -> bool:
    return os.getenv("WORKER_INLINE", "1").strip().lower() not in ("0", "false", "no")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.get_engine()  # create the schema before the first request touches it
    if _inline_worker_enabled():
        from jobs.worker import start_inline
        thread, stop = start_inline()
        _worker.update(thread=thread, stop=stop)
        log.info("inline worker started (WORKER_INLINE=0 to run workers separately)")
    yield
    stop = _worker.pop("stop", None)
    thread = _worker.pop("thread", None)
    if stop is not None:
        stop.set()
    if thread is not None:
        # The worker finishes the step it is on; it is a daemon thread, so a
        # long render can't hold the process open indefinitely.
        thread.join(timeout=5.0)


app = FastAPI(
    title="Agentic Animated Video Generation",
    version="1.0.0",
    description="End-to-end agentic pipeline: prompt → animated short film with intelligent edits.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(pipeline_routes.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(job_routes.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(edit_routes.router, prefix="/api/edit", tags=["edit"])
app.include_router(history_routes.router, prefix="/api/history", tags=["history"])
app.include_router(project_routes.router, prefix="/api/projects", tags=["projects"])
app.include_router(voice_routes.router, prefix="/api/voices", tags=["voices"])
app.include_router(progress_ws.router, prefix="/ws", tags=["websocket"])

# Static asset server — exposes the per-project output directory so the UI can
# fetch /assets/<project_id>/final_output.mp4, frame images, etc.
app.mount("/assets", StaticFiles(directory=str(OUTPUTS_DIR)), name="assets")

# Frontend — single-page app served from /frontend.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if (FRONTEND_DIR / "src").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "src")),
              name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    idx = FRONTEND_DIR / "src" / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return HTMLResponse("<h1>Agentic Video Generator</h1><p>Frontend not built.</p>")


@app.get("/health")
def health():
    """Liveness only — cheap enough for a container probe to hit every second."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: can this instance actually reach the database?"""
    from sqlalchemy import text
    try:
        with db.get_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "database": f"{type(e).__name__}: {e}"}
    thread = _worker.get("thread")
    return {
        "status": "ok",
        "database": db.get_engine().dialect.name,
        "storage": assets.backend_name(),
        "inline_worker": bool(thread and thread.is_alive()),
    }
