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
  /api/docs       — the interactive API reference (OpenAPI at /api/openapi.json)
  /              — the web app, including its /docs pages

By default the API also runs a worker thread, so `python main.py serve` is still
the only command needed on a laptop. Set WORKER_INLINE=0 and run
`python main.py worker` separately to scale them apart.
"""
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Ensure all MCP tools are registered.
import mcp.tools  # noqa: F401

from auth import accounts
from auth.deps import require_user
from shared import assets, constants, db
from shared.utils.logging import get_logger

from .routes import auth as auth_routes
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
    title="Dastango",
    version="1.0.0",
    description="A short film from one sentence (script, voices, pictures, music and cuts), "
                "then change it by saying what you want.",
    lifespan=lifespan,
    # The interactive API reference lives under /api, so /docs belongs to the
    # product's own documentation pages.
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

# The UI is served from this same origin, so by default no cross-origin
# request is allowed at all. "*" with credentials is the combination that lets
# any site drive a signed-in user's account, so origins must be named.
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

# Signing in is the only thing that works signed out.
app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])

# Everything else needs an account. The dependency is declared here, on the
# router, so a new endpoint is protected the moment it is added — not when
# someone remembers to decorate it.
_signed_in = [Depends(require_user)]
app.include_router(pipeline_routes.router, prefix="/api/pipeline", tags=["pipeline"],
                   dependencies=_signed_in)
app.include_router(job_routes.router, prefix="/api/jobs", tags=["jobs"],
                   dependencies=_signed_in)
app.include_router(edit_routes.router, prefix="/api/edit", tags=["edit"],
                   dependencies=_signed_in)
app.include_router(history_routes.router, prefix="/api/history", tags=["history"],
                   dependencies=_signed_in)
app.include_router(project_routes.router, prefix="/api/projects", tags=["projects"],
                   dependencies=_signed_in)
app.include_router(voice_routes.router, prefix="/api/voices", tags=["voices"],
                   dependencies=_signed_in)
app.include_router(progress_ws.router, prefix="/ws", tags=["websocket"])

# Assets are films and stills, so they are served to their owner only. This
# used to be a StaticFiles mount, which meant anyone who guessed a project id
# could download the finished video however locked-down the JSON API was.
VOICE_PREVIEWS = "_voice_previews"


@app.get("/assets/{project_id}/{asset_path:path}")
def serve_asset(project_id: str, asset_path: str, user=Depends(require_user)):
    """Serve one generated file, if this account is allowed to see it."""
    if project_id != VOICE_PREVIEWS and not accounts.may_access(user, project_id):
        # 404 rather than 403: a different answer would confirm the id exists.
        raise HTTPException(404, "not found")

    # Resolved per call: bound at import it would ignore a reconfigured
    # output directory and serve from the wrong place.
    root = (constants.OUTPUTS_DIR / project_id).resolve()
    target = (root / asset_path).resolve()
    # "../.." in the path would otherwise walk out of the outputs directory.
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    # FileResponse answers Range requests, so seeking in the player still works.
    return FileResponse(target)

# ---- the interfaces -----------------------------------------------------------
# Two UIs during the move: the Next.js app (web/, built to web/out) and the
# original vanilla page (frontend/src). Whichever is built serves /, and the
# original stays reachable at /classic/ either way, so nothing is lost while
# the new one is being finished.
ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
WEB_DIST = Path(os.getenv("WEB_DIST", str(ROOT_DIR / "web" / "out")))

if (FRONTEND_DIR / "src").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "src")),
              name="static")


def _classic_page():
    idx = FRONTEND_DIR / "src" / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return HTMLResponse("<h1>Dastango</h1><p>The interface has not been built.</p>")


@app.get("/classic/", response_class=HTMLResponse)
def classic():
    """The original single-page UI, kept while the new one replaces it."""
    return _classic_page()


def web_built() -> bool:
    return (WEB_DIST / "index.html").is_file()


if not web_built():
    @app.get("/", response_class=HTMLResponse)
    def index():
        return _classic_page()


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


# Registered last on purpose — see above.
if web_built():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
