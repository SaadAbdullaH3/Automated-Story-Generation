"""In-memory registry of in-flight runs and their latest event.

Lets the WebSocket layer subscribe + the REST status endpoint poll without a
real broker. Suitable for single-process deploys; for prod swap to Redis.
"""
from __future__ import annotations
import asyncio
import threading
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple


_state_lock = threading.Lock()
_runs: Dict[str, Dict] = {}                 # project_id -> dict
_events: Dict[str, Deque[Dict]] = defaultdict(lambda: deque(maxlen=200))
# Each subscriber queue belongs to the event loop that created it. Pipeline
# runs push events from worker threads, and asyncio.Queue isn't thread-safe,
# so pushes are handed to that loop with call_soon_threadsafe.
_subscribers: Dict[str, List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(list)


def create(project_id: str) -> None:
    with _state_lock:
        _runs[project_id] = {"project_id": project_id, "status": "running",
                             "phase": "queued", "progress": 0.0,
                             "message": "queued", "events": 0}
        _events[project_id].clear()


def push_event(project_id: str, event: Dict) -> None:
    with _state_lock:
        _events[project_id].append(event)
        run = _runs.setdefault(project_id, {"project_id": project_id, "events": 0})
        run.update({
            "phase": event.get("phase", run.get("phase")),
            "status": event.get("status", run.get("status")),
            "progress": event.get("progress", run.get("progress", 0.0)),
            "message": event.get("message", ""),
            "events": run.get("events", 0) + 1,
        })
        subscribers = list(_subscribers.get(project_id, []))
    # Fan out to all WS subscribers, on their own event loops.
    for loop, q in subscribers:
        try:
            loop.call_soon_threadsafe(_offer, q, event)
        except RuntimeError:
            pass  # loop already closed; unsubscribe will clean up


def _offer(q: asyncio.Queue, event: Dict) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        pass


def snapshot(project_id: str) -> Optional[Dict]:
    with _state_lock:
        run = _runs.get(project_id)
        if not run:
            return None
        return {**run, "recent_events": list(_events[project_id])[-20:]}


def history(project_id: str) -> List[Dict]:
    with _state_lock:
        return list(_events.get(project_id, []))


def subscribe(project_id: str) -> asyncio.Queue:
    """Must be called from inside a running event loop (e.g. a WebSocket handler)."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    with _state_lock:
        _subscribers[project_id].append((loop, q))
        replay = list(_events[project_id])
    # Replay recent events.
    for ev in replay:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            break
    return q


def unsubscribe(project_id: str, q: asyncio.Queue) -> None:
    with _state_lock:
        subs = _subscribers.get(project_id, [])
        _subscribers[project_id] = [(lp, sq) for lp, sq in subs if sq is not q]
