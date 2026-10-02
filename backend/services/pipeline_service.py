"""The orchestrator instance shared by the API process.

Runs no longer happen here — they are rows on the job queue, executed by
`jobs.worker`. What is left is the orchestrator used for the synchronous,
sub-second operations the routes do inline (storyboard edits), and a way for
the few endpoints whose callers expect the answer in the response to wait for
a queued job.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

import jobs
from agents.orchestrator import PipelineOrchestrator

_orchestrator = PipelineOrchestrator()

# Long enough for an edit that rewrites the script and redraws every shot.
JOB_WAIT_S = 900.0


def orchestrator() -> PipelineOrchestrator:
    """The shared orchestrator (routes use it for synchronous storyboard edits)."""
    return _orchestrator


def run_and_wait(kind: str, project_id: str, payload: Dict[str, Any],
                 timeout_s: float = JOB_WAIT_S,
                 poll_s: float = 0.2) -> Tuple[jobs.Job, Dict[str, Any]]:
    """Queue a job and block until it finishes; returns it and its final payload.

    For the classic page's edit and revert, which answer with the result. They
    still go through the queue rather than running here, so they wait their
    turn behind the project's other jobs and run on a worker like everything
    else that writes a version. A failure is not retried: an edit that failed
    once fails the same way again.
    """
    job = jobs.enqueue(kind, project_id, payload, max_attempts=1)
    deadline = time.monotonic() + timeout_s
    while True:
        current = jobs.get(job.id)
        if current is not None and current.done:
            final = next((e for e in reversed(jobs.events_since(job.id, 0))
                          if e["phase"] == "complete"), {})
            return current, final.get("payload") or {}
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{job.id} is still {current.status if current else 'missing'}")
        time.sleep(poll_s)


def failure_reason(error: str | None) -> str:
    """A job's error without the exception class names stacked in front of it."""
    text = (error or "").strip()
    while True:
        head, sep, rest = text.partition(": ")
        if sep and head.isidentifier() and head.endswith(("Error", "Exception", "Failed")):
            text = rest
        else:
            return text
