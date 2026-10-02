"""M8 — edits and undo are jobs, and a film is never left half-edited.

Before this an edit ran inside the HTTP request: no progress, no way to stop
it, lost if the API restarted, and — with a second worker — free to run
alongside a render of the same film, both starting from the same version so
the second quietly undid the first. An edit that failed half way also left
its in-place changes on disk for the next edit to bake in.
"""
from __future__ import annotations

import filecmp
import time
from datetime import timedelta
from pathlib import Path

import pytest

import jobs
from agents.edit_agent import EditAgent
from agents.edit_agent.describe import describe
from backend.services.pipeline_service import failure_reason
from jobs import queue, worker
from shared import db
from shared.schemas.edit import EditCommand
from tests.conftest import signed_in_client, silence_tts


def _wait(job_id: str, timeout: float = 180.0) -> jobs.Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get(job_id)
        if job and job.done:
            return job
        time.sleep(0.1)
    raise AssertionError(f"{job_id} did not finish")


def _backdate(job_id: str, seconds: float) -> None:
    """Fix the queue order regardless of how fast the clock ticks."""
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(
            created_at=queue.utcnow() - timedelta(seconds=seconds)))


# ---- one film, one job at a time ----------------------------------------------

def test_jobs_for_one_film_run_one_at_a_time_in_order(isolated_dirs):
    first = jobs.enqueue("edit", "film_a", {"query": "make it darker"})
    second = jobs.enqueue("edit", "film_a", {"query": "speed it up"})
    other = jobs.enqueue("render", "film_b", {})
    _backdate(first.id, 3), _backdate(second.id, 2), _backdate(other.id, 1)

    assert queue.claim("w1").id == first.id
    # The second edit waits for the first; another film's job does not.
    assert queue.claim("w2").id == other.id
    assert queue.claim("w3") is None

    queue.complete(first.id)
    assert queue.claim("w3").id == second.id


def test_a_job_still_retrying_holds_its_place(isolated_dirs):
    first = jobs.enqueue("render", "film_a", {}, max_attempts=2)
    second = jobs.enqueue("edit", "film_a", {"query": "x"})
    _backdate(first.id, 2), _backdate(second.id, 1)

    claimed = queue.claim("w1")
    queue.fail(claimed.id, "provider down")          # back on the queue, backing off
    assert jobs.get(first.id).status == "queued"
    assert queue.claim("w2") is None                 # the edit must not jump ahead

    jobs.cancel(first.id)
    assert queue.claim("w2").id == second.id


# ---- the API --------------------------------------------------------------------

@pytest.fixture
def studio(small_project, monkeypatch):
    """A rendered film, the API pointed at it, and a worker running."""
    from agents.orchestrator import PipelineOrchestrator
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import history as history_routes
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service

    state, sm = small_project(project_id="t_edits")
    orch = PipelineOrchestrator(sm)
    calls = silence_tts(orch.editor.executor.audio.tools)
    monkeypatch.setattr(pipeline_service, "_orchestrator", orch)
    for module in (edit_routes, history_routes, pipeline_routes):
        monkeypatch.setattr(module, "sm", sm)
    client = signed_in_client(app_module.app)
    thread, stop = worker.start_inline(poll_interval=0.05)
    yield state, sm, client, calls
    stop.set()
    thread.join(timeout=60)


def test_an_edit_is_queued_rather_than_run_in_the_request(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    state, sm = small_project(project_id="t_queued")
    monkeypatch.setattr(edit_routes, "sm", sm)
    client = signed_in_client(app_module.app)

    started = time.monotonic()
    res = client.post("/api/edit/t_queued", json={"query": "make scene 2 darker"})
    assert res.status_code == 200, res.text
    assert time.monotonic() - started < 2.0
    job = jobs.get(res.json()["job_id"])
    assert (job.kind, job.status, job.max_attempts) == ("edit", "queued", 1)
    assert job.payload["query"] == "make scene 2 darker"


def test_a_storyboard_cannot_be_edited_like_a_film(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    state, sm = small_project(project_id="t_board")
    state.video = None
    sm.snapshot(state, asset_paths=[], description="storyboard")
    monkeypatch.setattr(edit_routes, "sm", sm)
    res = signed_in_client(app_module.app).post("/api/edit/t_board", json={"query": "darker"})
    assert res.status_code == 409


def test_an_edit_runs_on_a_worker_and_says_what_it_understood(studio):
    state, sm, client, calls = studio
    run = client.post("/api/edit/t_edits",
                      json={"query": "make the voices in scene 2 whispered"}).json()
    assert _wait(run["job_id"]).status == "succeeded"

    events = jobs.events_since(run["job_id"], 0)
    understood = next(e for e in events if e["status"] == "understood")
    assert understood["message"] == "Understood: whispered voices · scene 2"
    assert "Recording the voices again" in [e["message"] for e in events]
    assert events[-1]["phase"] == "complete" and events[-1]["payload"]["version"] == 2
    assert calls and all(Path(c["out_path"]).name.startswith("scene_2_") for c in calls)

    film = client.get("/api/pipeline/film/t_edits").json()
    assert film["version"] == 2
    versions = client.get("/api/history/t_edits/film").json()
    assert [(v["kind"], v["label"], v["current"]) for v in versions] == [
        ("render", "First cut", False),
        ("edit", "make the voices in scene 2 whispered", True),
    ]
    assert versions[1]["detail"] == "whispered voices · scene 2"


def test_going_back_restores_the_film_exactly(studio):
    state, sm, client, _ = studio
    first_cut = Path(state.video.final_video_path)
    kept = first_cut.with_name("first_cut_copy.mp4")
    kept.write_bytes(first_cut.read_bytes())

    run = client.post("/api/edit/t_edits", json={"query": "apply the noir filter"}).json()
    assert _wait(run["job_id"]).status == "succeeded"
    assert not filecmp.cmp(first_cut, kept, shallow=False)

    res = client.post("/api/history/t_edits/revert/1")
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 3
    assert filecmp.cmp(first_cut, kept, shallow=False)
    labels = [v["label"] for v in client.get("/api/history/t_edits/film").json()]
    assert labels == ["First cut", "apply the noir filter", "Back to version 1"]


def test_the_classic_page_still_gets_its_answer_in_the_response(studio):
    _, _, client, _ = studio
    ok = client.post("/api/edit/apply", json={"project_id": "t_edits", "query": "speed up 1.25x"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["new_version"] == 2

    bad = client.post("/api/edit/apply", json={"project_id": "t_edits",
                                               "query": "make scene 9 darker"})
    assert bad.status_code == 400
    # The reason, not a stack of exception names in front of it.
    assert bad.json()["detail"].startswith("unknown scene 'scene_9'")


def test_a_revert_is_saved_as_the_version_it_is(small_project):
    """Restoring v1's files used to copy v1's own record in with them, and the
    revert's snapshot then wrote it over its own — so 'v3' said it was v1."""
    state, sm = small_project()
    state.video.speed_factor = 2.0
    sm.snapshot(state, asset_paths=[], description="sped up")

    sm.revert(state.project_id, 1)
    latest = sm.latest(state.project_id)
    assert latest.version == 3
    assert latest.metadata["reverted_from"] == 1
    assert not (Path(state.video.final_video_path).parent / "state.json").exists()


# ---- never half-edited ------------------------------------------------------------

def _first_shot(sm, pid):
    return Path(sm.latest(pid).video.frames[0].shot_bank[0])


def test_a_failed_edit_leaves_the_film_as_it_was(small_project, monkeypatch):
    """The filter darkens the shots in place, then cutting the film fails."""
    state, sm = small_project()
    shot = _first_shot(sm, state.project_id)
    before = shot.read_bytes()
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)

    def ffmpeg_dies(_state):
        raise RuntimeError("ffmpeg died")
    monkeypatch.setattr(agent.executor, "_recompose", ffmpeg_dies)

    result = agent.edit(EditCommand(project_id=state.project_id, query="apply the noir filter"))
    assert not result.success and "ffmpeg died" in result.error
    assert shot.read_bytes() == before
    assert sm.latest(state.project_id).version == 1


class _Stop(BaseException):
    """Stands in for a cancelled job (JobCancelled is a BaseException too)."""


def test_a_cancelled_edit_puts_its_files_back(small_project):
    """Stopped after the music is mixed in and before the film is cut again:
    the soundtrack on disk has music the saved film doesn't."""
    state, sm = small_project(with_bgm=False)
    master = Path(state.audio.master_track)
    before = master.read_bytes()
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)

    def cancel_before_the_cut(kind, info):
        if kind == "step" and info["index"] == 1:
            assert master.read_bytes() != before, "the music step changed nothing"
            raise _Stop()

    with pytest.raises(_Stop):
        agent.edit(EditCommand(project_id=state.project_id,
                               query="add tense background music"),
                   on_progress=cancel_before_the_cut)
    assert master.read_bytes() == before
    assert sm.latest(state.project_id).version == 1


def test_an_edit_starts_from_the_saved_files(small_project):
    """A worker killed mid-edit leaves changes on disk the saved version
    never had; the next edit must not build on them."""
    state, sm = small_project()
    shot = _first_shot(sm, state.project_id)
    saved = shot.read_bytes()
    shot.write_bytes(b"half-applied")

    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    assert agent.edit(EditCommand(project_id=state.project_id,
                                  query="remove the subtitles")).success
    assert shot.read_bytes() == saved


def test_an_unreadable_image_fails_the_shot_instead_of_hanging(tmp_path):
    """Found by the test above: ffmpeg loops a still as an endless input, and
    one it cannot decode it retries forever — the worker hung for good, its
    heartbeat still beating, with no step boundary for a cancel to land on."""
    import threading
    from agents.video_agent.animator import Shot, render_shot

    bad = tmp_path / "scene_1_wide.png"
    bad.write_bytes(b"half-applied")
    outcome: list = []

    def attempt():
        try:
            render_shot(Shot(image_path=str(bad), duration_ms=1000), tmp_path / "shot.mp4",
                        width=320, height=180, fps=12)
            outcome.append("rendered")
        except Exception as e:  # noqa: BLE001
            outcome.append(e)

    t = threading.Thread(target=attempt, daemon=True)
    t.start()
    t.join(timeout=30)
    assert outcome, "render_shot hung on an unreadable image"
    assert isinstance(outcome[0], ValueError) and "unreadable image" in str(outcome[0])


# ---- in words -------------------------------------------------------------------------

@pytest.mark.parametrize("intent,expected", [
    ({"intent": "apply_filter", "scope": "global", "parameters": {"filter": "cold_thriller"}},
     "a cold thriller look · the whole film"),
    ({"intent": "change_voice_tone", "scope": "character:char_protagonist",
      "parameters": {"tone": "warm"}}, "warm voices · Aria"),
    ({"intent": "speed_up", "scope": "global", "parameters": {"factor": 1.5}},
     "faster · the whole film"),
])
def test_an_edit_is_described_the_way_a_creator_would_say_it(intent, expected):
    assert describe(intent, {"char_protagonist": "Aria"}) == expected


def test_failure_reasons_lose_the_exception_names():
    assert failure_reason("EditFailed: ValueError: unknown scene 'x'") == "unknown scene 'x'"
    assert failure_reason("Scene 2: too dark") == "Scene 2: too dark"
    assert failure_reason(None) == ""
