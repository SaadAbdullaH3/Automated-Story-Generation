"""The queue itself: enqueue, claim, heartbeat, finish, cancel.

Claiming is a single atomic UPDATE, so two workers can never take the same job:
Postgres skips rows another worker has locked, and on SQLite the second UPDATE
matches nothing because the row is no longer `queued`.

Jobs for one project run one at a time, in the order they were queued. Every
job reads the project's latest version and writes the next one, so two edits
running side by side would both start from the same version and the second
would quietly undo the first.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, or_, select

from shared import db

JOB_KINDS = ("run_full", "plan", "render", "rerun_phase", "edit", "revert")
UNFINISHED_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")

# A worker that has not checked in for this long is assumed dead and its job
# goes back on the queue (bounded by max_attempts).
STALE_AFTER_S = 120


def utcnow() -> datetime:
    """Naive UTC — the same wall clock on SQLite and Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class Job:
    id: str
    project_id: str
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    priority: int = 100
    attempts: int = 0
    max_attempts: int = 2
    error: Optional[str] = None
    worker: Optional[str] = None
    cancel_requested: bool = False
    created_at: Optional[datetime] = None
    run_after: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Job":
        return cls(**{k: row[k] for k in cls.__dataclass_fields__})

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        for key in ("created_at", "run_after", "started_at", "finished_at",
                    "heartbeat_at"):
            value = out[key]
            out[key] = value.isoformat() if isinstance(value, datetime) else None
        out["cancel_requested"] = bool(out["cancel_requested"])
        return out

    @property
    def done(self) -> bool:
        return self.status in TERMINAL_STATUSES


def new_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:12]


# ---- producing -------------------------------------------------------------

def enqueue(kind: str, project_id: str, payload: Optional[Dict[str, Any]] = None,
            priority: int = 100, max_attempts: int = 2,
            delay_s: float = 0.0) -> Job:
    """Put a job on the queue. Returns it already in `queued` state."""
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    now = utcnow()
    job = Job(
        id=new_job_id(), project_id=project_id, kind=kind,
        payload=payload or {}, status="queued", priority=priority,
        max_attempts=max_attempts, created_at=now,
        run_after=now + timedelta(seconds=delay_s),
    )
    with db.get_engine().begin() as c:
        c.execute(db.jobs.insert().values(
            id=job.id, project_id=job.project_id, kind=job.kind,
            payload=job.payload, status="queued", priority=priority,
            attempts=0, max_attempts=max_attempts, cancel_requested=False,
            created_at=job.created_at, run_after=job.run_after,
        ))
    return job


# ---- consuming -------------------------------------------------------------

def claim_statement(worker: str, now: datetime, dialect: str = "sqlite",
                    kinds: Optional[Sequence[str]] = None):
    """The one statement that takes a job.

    It is a single UPDATE so that claiming is atomic. On Postgres the inner
    SELECT skips rows another worker has locked; on SQLite the writer lock
    serialises them and the `status = 'queued'` guard makes the loser's UPDATE
    match no rows. Built separately from `claim` so the Postgres SQL can be
    compiled and checked without a Postgres server.
    """
    # A job waits while an older one for the same project is unfinished. What
    # blocks it is that row's existence, not its lock, so this holds even when
    # two workers evaluate it at the same instant under SKIP LOCKED.
    jobs, earlier = db.jobs, db.jobs.alias("earlier")
    waiting_behind = (
        select(earlier.c.id)
        .where(earlier.c.project_id == jobs.c.project_id,
               earlier.c.status.in_(UNFINISHED_STATUSES),
               or_(earlier.c.created_at < jobs.c.created_at,
                   and_(earlier.c.created_at == jobs.c.created_at,
                        earlier.c.id < jobs.c.id)))
        .exists()
    )
    nominee = (
        select(jobs.c.id)
        .where(jobs.c.status == "queued", jobs.c.run_after <= now, ~waiting_behind)
        .order_by(jobs.c.priority.asc(), jobs.c.created_at.asc())
        .limit(1)
    )
    if kinds:
        nominee = nominee.where(db.jobs.c.kind.in_(list(kinds)))
    if dialect == "postgresql":
        nominee = nominee.with_for_update(skip_locked=True)
    return (
        db.jobs.update()
        .where(db.jobs.c.id == nominee.scalar_subquery(),
               db.jobs.c.status == "queued")
        .values(status="running", worker=worker, started_at=now,
                heartbeat_at=now, attempts=db.jobs.c.attempts + 1)
        .returning(*db.jobs.c)
    )


def claim(worker: str, kinds: Optional[Sequence[str]] = None) -> Optional[Job]:
    """Take the next due job, atomically. Returns None when the queue is empty."""
    engine = db.get_engine()
    now = utcnow()
    statement = claim_statement(worker, now, engine.dialect.name, kinds)
    with engine.begin() as c:
        row = c.execute(statement).mappings().first()
    return Job.from_row(row) if row else None


def heartbeat(job_id: str) -> bool:
    """Say the worker is still alive. False means the job should stop (cancelled)."""
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update()
                  .where(db.jobs.c.id == job_id)
                  .values(heartbeat_at=utcnow()))
        cancelled = c.execute(
            select(db.jobs.c.cancel_requested).where(db.jobs.c.id == job_id)
        ).scalar()
    return not bool(cancelled)


def complete(job_id: str) -> None:
    _finish(job_id, "succeeded")


def fail(job_id: str, error: str, retry: bool = True) -> str:
    """Mark a job failed, or put it back on the queue if attempts remain."""
    now = utcnow()
    with db.get_engine().begin() as c:
        row = c.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().first()
        if not row:
            return "missing"
        requeue = retry and row["attempts"] < row["max_attempts"] and not row["cancel_requested"]
        status = "queued" if requeue else "failed"
        c.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(
            status=status,
            error=error[-2000:],
            worker=None if requeue else row["worker"],
            # Back off a little so a deterministic failure doesn't spin.
            run_after=now + timedelta(seconds=5) if requeue else row["run_after"],
            finished_at=None if requeue else now,
        ))
    return status


def _finish(job_id: str, status: str, error: Optional[str] = None) -> None:
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(
            status=status, finished_at=utcnow(), error=error))


def cancel(job_id: str) -> Optional[str]:
    """Ask a job to stop. A queued job stops now; a running one at its next step."""
    with db.get_engine().begin() as c:
        row = c.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().first()
        if not row:
            return None
        if row["status"] in TERMINAL_STATUSES:
            return row["status"]
        values: Dict[str, Any] = {"cancel_requested": True}
        if row["status"] == "queued":
            values.update(status="cancelled", finished_at=utcnow())
        c.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(**values))
        return values.get("status", "cancelling")


def mark_cancelled(job_id: str) -> None:
    _finish(job_id, "cancelled")


def requeue_stale(timeout_s: int = STALE_AFTER_S) -> int:
    """Recover jobs whose worker died mid-run. Returns how many were touched."""
    cutoff = utcnow() - timedelta(seconds=timeout_s)
    touched = 0
    with db.get_engine().begin() as c:
        rows = c.execute(
            select(db.jobs).where(db.jobs.c.status == "running",
                                  db.jobs.c.heartbeat_at < cutoff)
        ).mappings().all()
        for row in rows:
            if row["attempts"] < row["max_attempts"]:
                c.execute(db.jobs.update().where(db.jobs.c.id == row["id"]).values(
                    status="queued", worker=None, run_after=utcnow(),
                    error="worker stopped responding — requeued"))
            else:
                c.execute(db.jobs.update().where(db.jobs.c.id == row["id"]).values(
                    status="failed", finished_at=utcnow(),
                    error="worker stopped responding"))
            touched += 1
    return touched


# ---- reading ---------------------------------------------------------------

def get(job_id: str) -> Optional[Job]:
    with db.get_engine().connect() as c:
        row = c.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().first()
    return Job.from_row(row) if row else None


def list_jobs(project_id: Optional[str] = None, status: Optional[str] = None,
              limit: int = 50) -> List[Job]:
    stmt = select(db.jobs).order_by(db.jobs.c.created_at.desc()).limit(limit)
    if project_id:
        stmt = stmt.where(db.jobs.c.project_id == project_id)
    if status:
        stmt = stmt.where(db.jobs.c.status == status)
    with db.get_engine().connect() as c:
        rows = c.execute(stmt).mappings().all()
    return [Job.from_row(r) for r in rows]


def latest_for_project(project_id: str) -> Optional[Job]:
    jobs_found = list_jobs(project_id=project_id, limit=1)
    return jobs_found[0] if jobs_found else None


# ---- progress events -------------------------------------------------------

def append_event(job_id: str, project_id: str, phase: str = "", status: str = "",
                 message: str = "", progress: float = 0.0,
                 payload: Optional[Dict[str, Any]] = None) -> int:
    with db.get_engine().begin() as c:
        res = c.execute(db.job_events.insert().values(
            job_id=job_id, project_id=project_id, phase=phase, status=status,
            message=message, progress=float(progress), payload=payload,
            created_at=utcnow(),
        ))
        return int(res.inserted_primary_key[0])


def events_since(job_id: str, after_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    """Events newer than `after_id`, oldest first — the WebSocket's cursor."""
    with db.get_engine().connect() as c:
        rows = c.execute(
            select(db.job_events)
            .where(db.job_events.c.job_id == job_id, db.job_events.c.id > after_id)
            .order_by(db.job_events.c.id.asc()).limit(limit)
        ).mappings().all()
    return [_event_dict(r) for r in rows]


def project_events(project_id: str, after_id: int = 0,
                   limit: int = 200) -> List[Dict[str, Any]]:
    with db.get_engine().connect() as c:
        rows = c.execute(
            select(db.job_events)
            .where(db.job_events.c.project_id == project_id,
                   db.job_events.c.id > after_id)
            .order_by(db.job_events.c.id.asc()).limit(limit)
        ).mappings().all()
    return [_event_dict(r) for r in rows]


def _event_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"], "job_id": row["job_id"], "project_id": row["project_id"],
        "phase": row["phase"], "status": row["status"], "message": row["message"],
        "progress": row["progress"], "payload": row["payload"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
