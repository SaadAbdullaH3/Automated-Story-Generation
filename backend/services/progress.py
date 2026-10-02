"""Progress for a project's current job, read from the database.

Events are rows, not objects in this process's memory, so the UI sees the same
stream whether the run is happening in the API process, in a worker process on
the same machine, or on another host — and reconnecting after a restart replays
what was missed instead of hanging on an empty socket.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

import jobs

# How often the stream looks for new rows. Fast enough to feel live, cheap
# enough that an idle browser tab costs nothing measurable.
POLL_INTERVAL_S = 0.4
HEARTBEAT_EVERY_S = 30.0
# A client that connects before its job row exists gives up after this long.
WAIT_FOR_JOB_S = 60.0


def snapshot(project_id: str) -> Optional[Dict[str, Any]]:
    """Current status plus the tail of the event log, for a polling client."""
    job = jobs.latest_for_project(project_id)
    if job is None:
        return None
    events = jobs.events_since(job.id, 0, limit=500)
    last = events[-1] if events else {}
    return {
        "project_id": project_id,
        "job_id": job.id,
        "kind": job.kind,
        # What was asked for — known before the script exists, so the studio
        # can show the sentence immediately instead of a placeholder.
        "prompt": (job.payload or {}).get("prompt"),
        "status": job.status,
        "phase": last.get("phase") or ("queued" if job.status == "queued" else job.kind),
        "progress": last.get("progress", 0.0),
        "message": last.get("message", "") or (job.error or ""),
        "events": len(events),
        "attempts": job.attempts,
        "error": job.error,
        "recent_events": events[-20:],
    }


def history(project_id: str) -> List[Dict[str, Any]]:
    job = jobs.latest_for_project(project_id)
    return jobs.events_since(job.id, 0, limit=1000) if job else []


async def stream(project_id: str,
                 poll_interval_s: float = POLL_INTERVAL_S) -> AsyncIterator[Dict[str, Any]]:
    """Yield WebSocket envelopes until the project's current job finishes.

    The database calls are synchronous, so they go to a thread — a slow query
    must not stall the event loop serving every other socket.
    """
    job = await asyncio.to_thread(jobs.latest_for_project, project_id)
    if job is not None:
        snap = await asyncio.to_thread(snapshot, project_id)
        if snap:
            yield {"type": "snapshot", "data": snap}

    cursor = 0
    waited = 0.0
    since_heartbeat = 0.0
    while True:
        if job is None:
            job = await asyncio.to_thread(jobs.latest_for_project, project_id)
            if job is None:
                waited += poll_interval_s
                if waited >= WAIT_FOR_JOB_S:
                    return
                await asyncio.sleep(poll_interval_s)
                continue

        events = await asyncio.to_thread(jobs.events_since, job.id, cursor)
        if events:
            cursor = events[-1]["id"]
            since_heartbeat = 0.0
            for event in events:
                yield {"type": "event", "data": event}
            continue  # drain a burst before sleeping again

        fresh = await asyncio.to_thread(jobs.get, job.id)
        if fresh is not None and fresh.done:
            return

        await asyncio.sleep(poll_interval_s)
        since_heartbeat += poll_interval_s
        if since_heartbeat >= HEARTBEAT_EVERY_S:
            since_heartbeat = 0.0
            yield {"type": "heartbeat"}
