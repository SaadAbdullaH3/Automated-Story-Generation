"""Job queue endpoints — what is queued, running, finished, and why it failed."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

import jobs as job_queue

router = APIRouter()


@router.get("/")
def list_jobs(project_id: Optional[str] = None, status: Optional[str] = None,
              limit: int = Query(50, ge=1, le=200)):
    """Newest first. Filter by project, or by status to watch the backlog."""
    return [j.as_dict() for j in job_queue.list_jobs(project_id=project_id,
                                                     status=status, limit=limit)]


@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    return job.as_dict()


@router.get("/{job_id}/events")
def job_events(job_id: str, after: int = 0, limit: int = Query(200, ge=1, le=1000)):
    if job_queue.get(job_id) is None:
        raise HTTPException(404, f"no job {job_id}")
    return job_queue.events_since(job_id, after_id=after, limit=limit)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    """Stop a job. Queued stops at once; running stops at its next step."""
    status = job_queue.cancel(job_id)
    if status is None:
        raise HTTPException(404, f"no job {job_id}")
    return {"job_id": job_id, "status": status}
