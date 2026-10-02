"""M8 — what the creator interface reads, and how it is served."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from tests.conftest import signed_in_client


@pytest.fixture
def rendered(small_project, monkeypatch):
    """A rendered film, behind a signed-in admin client."""
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from backend.routes import projects as project_routes

    state, sm = small_project(project_id="t_creator", subtitle_language="English")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(project_routes, "sm", sm)
    return state, signed_in_client(app_module.app)


# ---- the player ------------------------------------------------------------------

def test_the_player_gets_urls_never_file_paths(rendered):
    """The old page split Windows paths in the browser to guess a URL, which
    breaks as soon as assets come from a bucket."""
    state, client = rendered
    film = client.get("/api/pipeline/film/t_creator").json()

    assert film["video_url"].startswith("/assets/t_creator/")
    assert "\\" not in str(film) and ":\\" not in str(film)
    assert film["duration_ms"] == state.video.duration_ms


def test_chapters_follow_the_timeline_in_order(rendered):
    state, client = rendered
    chapters = client.get("/api/pipeline/film/t_creator").json()["chapters"]

    assert [c["scene_id"] for c in chapters] == [s.scene_id for s in state.script.scenes]
    starts = [c["start_ms"] for c in chapters]
    assert starts == sorted(starts) and starts[0] == 0
    for a, b in zip(chapters, chapters[1:]):
        assert a["end_ms"] == b["start_ms"]          # no gaps, no overlaps
    assert chapters[-1]["end_ms"] <= state.video.duration_ms + 50
    assert all(c["poster_url"] and c["poster_url"].startswith("/assets/") for c in chapters)


def test_chapter_marks_move_when_the_film_is_sped_up(rendered):
    state, client = rendered
    from backend.routes import pipeline as pipeline_routes
    state.video.speed_factor = 2.0
    pipeline_routes.sm.snapshot(state, asset_paths=[], description="speed up")

    chapters = client.get("/api/pipeline/film/t_creator").json()["chapters"]
    timings = {s.scene_id: s for s in state.audio.manifest.scenes}
    for c in chapters:
        assert c["start_ms"] == round(timings[c["scene_id"]].start_ms / 2)


def test_there_is_no_film_before_the_render(small_project, monkeypatch, isolated_dirs):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from agents.orchestrator import PipelineOrchestrator
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore

    orch = PipelineOrchestrator(
        state_manager=StateManager(VersionStore(isolated_dirs / "state.db")))
    state = orch.plan("A kite over a quiet town", target_duration_s=16, scene_count=2)
    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    client = signed_in_client(app_module.app)
    assert client.get(f"/api/pipeline/film/{state.project_id}").status_code == 404


# ---- the library and the storyboard ------------------------------------------------

def test_the_library_has_a_poster_frame_and_a_stage(rendered):
    _state, client = rendered
    films = client.get("/api/projects/").json()
    film = next(f for f in films if f["project_id"] == "t_creator")
    assert film["poster_url"].startswith("/assets/t_creator/")
    assert film["scene_count"] == 3
    assert film["duration_ms"] > 0


def test_the_cards_name_the_voices_of_the_engine_about_to_render(rendered):
    """Switching voice engine in the render bar must change who the cards say
    will speak — otherwise the storyboard describes a film that won't be made."""
    state, client = rendered
    from backend.routes import pipeline as pipeline_routes
    state.storyboard = None
    from agents.video_agent import VideoAgent
    VideoAgent().generate_storyboard(state, width=320, height=180, scene_ids=[])
    pipeline_routes.sm.snapshot(state, asset_paths=[], description="board")

    kokoro = client.get("/api/pipeline/storyboard/t_creator?engine=kokoro").json()
    edge = client.get("/api/pipeline/storyboard/t_creator?engine=edge").json()
    k_voices = {m["name"]: m["voice"] for m in kokoro["cast"]}
    e_voices = {m["name"]: m["voice"] for m in edge["cast"]}
    assert k_voices.keys() == e_voices.keys()
    assert k_voices != e_voices

    assert client.get("/api/pipeline/storyboard/t_creator?engine=nope").status_code == 400


def test_the_job_status_carries_the_prompt(isolated_dirs):
    """The studio shows the sentence the moment it opens, before any script."""
    import jobs
    from backend.services import progress
    jobs.enqueue("plan", "p_prompt", {"prompt": "A night train to nowhere"})
    assert progress.snapshot("p_prompt")["prompt"] == "A night train to nowhere"


# ---- serving the built interface --------------------------------------------------

@pytest.fixture
def app_with_web(tmp_path, monkeypatch):
    """The app as it is when web/out has been built."""
    dist = tmp_path / "out"
    (dist / "studio").mkdir(parents=True)
    (dist / "index.html").write_text("<title>new interface</title>", encoding="utf-8")
    (dist / "studio" / "index.html").write_text("<title>studio</title>", encoding="utf-8")
    monkeypatch.setenv("WEB_DIST", str(dist))
    import backend.app as app_module
    reloaded = importlib.reload(app_module)
    yield reloaded.app
    monkeypatch.delenv("WEB_DIST")
    importlib.reload(app_module)


def test_the_built_interface_is_served_at_the_root(app_with_web, isolated_dirs):
    client = TestClient(app_with_web)
    assert "new interface" in client.get("/").text
    assert "studio" in client.get("/studio/").text
    # The original page is not lost while the new one takes over.
    assert client.get("/classic/").status_code == 200


def test_the_root_mount_does_not_swallow_the_api(app_with_web, isolated_dirs):
    """A mount at "/" matches every path, so anything registered after it is
    unreachable. It has to be the last thing added."""
    client = TestClient(app_with_web)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["status"] == "ok"
    assert client.get("/api/auth/status").headers["content-type"].startswith("application/json")
    # And an API route that needs an account still says so, rather than
    # falling through to an HTML page.
    assert client.get("/api/projects/").status_code == 401
