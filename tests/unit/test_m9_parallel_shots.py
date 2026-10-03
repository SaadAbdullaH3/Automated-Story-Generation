"""M9 — shots render several at once.

Measured on the 2-core server: a shot keeps about one core busy (zoompan runs
on a single thread), so rendering them one at a time left the CPU at ~130% of
200% for the three quarters of a render that is shots.
"""
from __future__ import annotations
import threading
import time

import pytest

from agents.video_agent import agent as video_agent
from agents.video_agent import animator
from shared.timeline import ms_to_frame

from .test_m1_sync import _video_frames


def _watch(monkeypatch):
    """Count how many shots are rendering at the same moment, and what each
    scene was cut from."""
    seen = {"running": 0, "peak": 0, "scenes": {}}
    lock = threading.Lock()
    real_render, real_assemble = video_agent.render_shot, video_agent.assemble_scene

    def render(*args, **kwargs):
        with lock:
            seen["running"] += 1
            seen["peak"] = max(seen["peak"], seen["running"])
        try:
            time.sleep(0.2)          # a window in which a second shot can start, if allowed
            return real_render(*args, **kwargs)
        finally:
            with lock:
                seen["running"] -= 1

    def assemble(shots, out_path, **kwargs):
        seen["scenes"][out_path.stem] = [p.stem for p in shots]
        return real_assemble(shots, out_path, **kwargs)

    monkeypatch.setattr(video_agent, "render_shot", render)
    monkeypatch.setattr(video_agent, "assemble_scene", assemble)
    return seen


def test_a_render_runs_shots_side_by_side(small_project, monkeypatch):
    monkeypatch.setenv("SHOT_WORKERS", "2")
    seen = _watch(monkeypatch)
    state, _ = small_project(duration_s=24, scenes=3)

    assert seen["peak"] == 2
    # Each scene is still cut from its own shots, in its own order...
    for frame in state.video.frames:
        assert seen["scenes"][frame.scene_id] == [s.shot_id for s in frame.shots]
    # ...and the film is still exactly as long as the timeline.
    assert _video_frames(state.video.final_video_path) == \
        ms_to_frame(state.audio.manifest.total_duration_ms, state.video.fps)


def test_one_worker_renders_one_shot_at_a_time(small_project, monkeypatch):
    monkeypatch.setenv("SHOT_WORKERS", "1")
    seen = _watch(monkeypatch)
    small_project(duration_s=20, scenes=2)
    assert seen["peak"] == 1


def test_workers_follow_the_cores_unless_set(monkeypatch):
    monkeypatch.delenv("SHOT_WORKERS", raising=False)
    assert 1 <= animator.shot_workers() <= animator.MAX_SHOT_WORKERS
    monkeypatch.setenv("SHOT_WORKERS", "3")
    assert animator.shot_workers() == 3
    monkeypatch.setenv("SHOT_WORKERS", "many")
    assert 1 <= animator.shot_workers() <= animator.MAX_SHOT_WORKERS


@pytest.mark.skipif(not hasattr(__import__("os"), "sched_getaffinity"),
                    reason="core affinity is a Linux idea")
def test_a_container_given_two_cores_uses_two(monkeypatch):
    # os.cpu_count() reports the host's cores even inside a container pinned
    # to fewer; the affinity mask is what the process may actually run on.
    monkeypatch.delenv("SHOT_WORKERS", raising=False)
    monkeypatch.setattr(animator.os, "sched_getaffinity", lambda _pid: {0, 1})
    monkeypatch.setattr(animator.os, "cpu_count", lambda: 64)
    assert animator.shot_workers() == 2
