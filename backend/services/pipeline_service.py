"""The orchestrator instance shared by the API process.

Runs no longer happen here — they are rows on the job queue, executed by
`jobs.worker`. What is left is the orchestrator used for the synchronous,
sub-second operations the routes do inline (storyboard edits).
"""
from __future__ import annotations

from agents.orchestrator import PipelineOrchestrator

_orchestrator = PipelineOrchestrator()


def orchestrator() -> PipelineOrchestrator:
    """The shared orchestrator (routes use it for synchronous storyboard edits)."""
    return _orchestrator
