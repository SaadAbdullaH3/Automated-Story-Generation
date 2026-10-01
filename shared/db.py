"""The one database: version log, edit log and job queue.

`DATABASE_URL` chooses the backend. The default is the SQLite file the project
has always used (`data/state.db`), so nothing extra has to be installed to run
locally; point it at Postgres (`postgresql+psycopg://...`) and the same schema
and the same SQL serve a multi-process deployment.

SQLite runs in WAL mode so the API process and a worker process can write at the
same time — without it the worker's writes would lock the API out mid-render.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, Index, Integer,
                        MetaData, String, Table, Text, create_engine, event)
from sqlalchemy.engine import Engine

from shared import constants

metadata = MetaData()

# ---- version log (Phase 5 undo) -------------------------------------------

versions = Table(
    "versions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("parent_version", Integer),
    Column("created_at", String(64), nullable=False),
    Column("description", Text, nullable=False, default=""),
    Column("state_path", Text, nullable=False),
    Column("asset_paths", JSON, nullable=False, default=list),
    Column("edit_intent", JSON),
    Index("idx_versions_project", "project_id"),
    Index("uq_versions_project_version", "project_id", "version", unique=True),
)

edit_log = Table(
    "edit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String(64), nullable=False),
    Column("created_at", String(64), nullable=False),
    Column("query", Text, nullable=False),
    Column("intent_json", JSON, nullable=False),
    Column("result_json", JSON, nullable=False),
    Index("idx_edit_log_project", "project_id"),
)

# ---- accounts ---------------------------------------------------------------

users = Table(
    "users", metadata,
    Column("id", String(64), primary_key=True),
    # Stored lower-cased; the unique index is what stops two signups racing to
    # the same address.
    Column("email", String(320), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False, default="user"),   # user | admin
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
    Column("last_login_at", DateTime),
    # Brute-force defence: counted per account, cleared on success.
    Column("failed_attempts", Integer, nullable=False, default=0),
    Column("locked_until", DateTime),
    Index("uq_users_email", "email", unique=True),
)

sessions = Table(
    "sessions", metadata,
    # The id is a hash of the cookie value, never the value itself: a stolen
    # database dump then can't be replayed as a login.
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("user_agent", String(256)),
    Column("ip", String(64)),
    Index("idx_sessions_user", "user_id"),
    Index("idx_sessions_expiry", "expires_at"),
)

projects = Table(
    "projects", metadata,
    Column("project_id", String(64), primary_key=True),
    # Null means nobody owns it — made by the CLI, before accounts existed.
    # Those are visible to admins only.
    Column("owner_id", String(64)),
    Column("created_at", DateTime, nullable=False),
    Index("idx_projects_owner", "owner_id"),
)


# ---- job queue -------------------------------------------------------------

jobs = Table(
    "jobs", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("payload", JSON, nullable=False, default=dict),
    Column("status", String(16), nullable=False, default="queued"),
    Column("priority", Integer, nullable=False, default=100),
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=2),
    Column("error", Text),
    Column("worker", String(128)),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, nullable=False),
    Column("run_after", DateTime, nullable=False),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    Column("heartbeat_at", DateTime),
    # The claim query filters on exactly these, in this order.
    Index("idx_jobs_claim", "status", "run_after", "priority"),
    Index("idx_jobs_project", "project_id"),
)

job_events = Table(
    "job_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String(64), nullable=False),
    Column("project_id", String(64), nullable=False),
    Column("phase", String(32), nullable=False, default=""),
    Column("status", String(32), nullable=False, default=""),
    Column("message", Text, nullable=False, default=""),
    Column("progress", Float, nullable=False, default=0.0),
    Column("payload", JSON),
    Column("created_at", DateTime, nullable=False),
    Index("idx_job_events_job", "job_id", "id"),
    Index("idx_job_events_project", "project_id", "id"),
)


# ---- engines ---------------------------------------------------------------

_engines: Dict[str, Engine] = {}


def default_url() -> str:
    """DATABASE_URL, or the SQLite file this project has always used."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # Heroku/Neon-style URLs name the dialect only; pick the installed driver.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    return url_for_path(constants.DB_PATH)


def url_for_path(path: Path | str) -> str:
    """A SQLite URL for a filesystem path (what tests and the CLI pass around)."""
    return "sqlite+pysqlite:///" + str(Path(path).resolve()).replace("\\", "/")


def get_engine(url: Optional[str] = None) -> Engine:
    """A process-wide engine per URL, with the schema created on first use."""
    url = url or default_url()
    engine = _engines.get(url)
    if engine is not None:
        return engine

    if url.startswith("sqlite"):
        db_file = url.split("///", 1)[-1]
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url, future=True,
            # The API serves requests on threads and the inline worker runs on
            # another; a 30 s busy timeout rides out a long write instead of
            # raising "database is locked".
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(engine, "connect", _sqlite_pragmas)
    else:
        engine = create_engine(url, future=True, pool_pre_ping=True)

    metadata.create_all(engine)
    _engines[url] = engine
    return engine


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")   # readers don't block the writer
    cur.execute("PRAGMA synchronous=NORMAL")  # WAL makes full fsync unnecessary
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def dispose_all() -> None:
    """Drop every cached engine (tests, and anything that re-points the DB)."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
