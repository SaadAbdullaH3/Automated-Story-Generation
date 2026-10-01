"""Job queue endpoints — what is queued, running, finished, and why it failed."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import jobs as job_queue
from auth import accounts
from auth.accounts import User
from auth.deps import require_user

router = APIRouter()


def _owned(job, user: User):
    """A job is visible exactly when its project is."""
    if job is None or not accounts.may_access(user, job.project_id):
        return None
    return job


@router.get("/")
def list_jobs(project_id: Optional[str] = None, status: Optional[str] = None,
              limit: int = Query(50, ge=1, le=200),
              user: User = Depends(require_user)):
    """Newest first. Filter by project, or by status to watch the backlog."""
    if project_id and not accounts.may_access(user, project_id):
        return []
    visible = accounts.projects_for(user)      # None = admin
    rows = job_queue.list_jobs(project_id=project_id, status=status, limit=limit)
    if visible is not None:
        allowed = set(visible)
        rows = [j for j in rows if j.project_id in allowed]
    return [j.as_dict() for j in rows]


@router.get("/{job_id}")
def get_job(job_id: str, user: User = Depends(require_user)):
    job = _owned(job_queue.get(job_id), user)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    return job.as_dict()


@router.get("/{job_id}/events")
def job_events(job_id: str, after: int = 0, limit: int = Query(200, ge=1, le=1000),
               user: User = Depends(require_user)):
    if _owned(job_queue.get(job_id), user) is None:
        raise HTTPException(404, f"no job {job_id}")
    return job_queue.events_since(job_id, after_id=after, limit=limit)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_user)):
    """Stop a job. Queued stops at once; running stops at its next step."""
    if _owned(job_queue.get(job_id), user) is None:
        raise HTTPException(404, f"no job {job_id}")
    status = job_queue.cancel(job_id)
    if status is None:
        raise HTTPException(404, f"no job {job_id}")
    return {"job_id": job_id, "status": status}
