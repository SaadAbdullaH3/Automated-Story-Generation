"""pytest configuration: ensure project root is on sys.path."""
import os
import sys
from pathlib import Path

import pytest

# Project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force the offline providers so tests are deterministic and never hit the network:
# mock LLM (template script + keyword classifier) and the PIL placeholder for images.
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("PROVIDER_IMAGE", "placeholder")

# Real credentials must never reach the tests: they would spend the owner's free
# quota and make results depend on which keys happen to be present on a machine.
os.environ["PIPELINE_SKIP_DOTENV"] = "1"      # main.py skips loading .env
for _credential in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                    "POLLINATIONS_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY", "FAL_API_KEY",
                    "REPLICATE_API_TOKEN", "HF_TOKEN", "HUGGINGFACE_API_KEY",
                    "OLLAMA_HOST", "SD_API_URL", "LOCAL_SD", "MYMEMORY_EMAIL"):
    os.environ.pop(_credential, None)
os.environ.pop("DATABASE_URL", None)

# Point the suite at a real server to prove the SQL works there, e.g.
#   TEST_DATABASE_URL=postgresql+psycopg://storygen:storygen@localhost:55432/storygen
# Without it every test gets its own throwaway SQLite file, as before. The
# tables are emptied between tests, so a shared server behaves like one.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
# Only the languages a test asks for get subtitle tracks.
os.environ.pop("SUBTITLE_EXTRA_LANGUAGES", None)

# Register all MCP tools.
import mcp.tools  # noqa: F401, E402


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Point outputs, snapshots and the database at a temp dir."""
    import shared.constants as constants
    from shared import db
    monkeypatch.setattr(constants, "OUTPUTS_DIR", tmp_path / "out")
    monkeypatch.setattr(constants, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(constants, "DB_PATH", tmp_path / "state.db")
    if TEST_DATABASE_URL:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        # Tests that name a SQLite file explicitly must land on the server too,
        # or half the suite would quietly keep testing SQLite.
        monkeypatch.setattr(db, "url_for_path", lambda _p: TEST_DATABASE_URL)
    else:
        # A DATABASE_URL in the environment would send the test's writes to a
        # real database; shared.db falls back to constants.DB_PATH without it.
        monkeypatch.delenv("DATABASE_URL", raising=False)
    constants.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    constants.STATE_DIR.mkdir(parents=True, exist_ok=True)
    yield tmp_path
    if TEST_DATABASE_URL:
        _empty_tables(db, TEST_DATABASE_URL)
    # Close the engines holding this tmp_path's SQLite files open, or Windows
    # refuses to delete them.
    db.dispose_all()


def _empty_tables(db, url: str) -> None:
    """Leave a shared server as clean as a fresh temp file."""
    engine = db.get_engine(url)
    with engine.begin() as c:
        for table in reversed(db.metadata.sorted_tables):
            c.execute(table.delete())


def silence_tts(tools) -> list:
    """Patch a ToolExecutor so TTS renders silent audio offline.

    Returns a list that records the kwargs of every audio.tts call.
    """
    calls: list = []
    real = tools.execute

    def patched(tool, **kwargs):
        if tool == "audio.tts":
            calls.append(dict(kwargs))
            kwargs["engine"] = "silent"
        return real(tool, **kwargs)

    tools.execute = patched
    return calls


def signed_in_client(app, email: str = "tester@example.com",
                     password: str = "a-test-passphrase"):
    """A TestClient holding an admin session.

    Since M5 the API needs an account for everything, and the first account
    created is the admin — which is also what the CLI-made projects in these
    tests belong to.
    """
    from fastapi.testclient import TestClient
    client = TestClient(app)
    res = client.post("/api/auth/register",
                      json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return client


def run_queued_jobs(orchestrator=None, limit: int = 10) -> list:
    """Drain the job queue the way a worker process would.

    Runs are queued by the API, not executed by it, so a test that posts to
    /api/pipeline/... has to play the worker.
    """
    from jobs import queue, worker
    ran = []
    for _ in range(limit):
        job = queue.claim("test-worker")
        if job is None:
            break
        worker.run_job(job, orchestrator=orchestrator)
        ran.append(job)
    return ran


@pytest.fixture
def fake_translation(monkeypatch):
    """Offline translator: prefixes each line with the target language."""
    from mcp.tools.llm_tools.translate_tool import TranslateTool
    calls: list = []

    def fake(self, lines, lang):
        calls.append(lang)
        return [f"[{lang}] {line}" for line in lines]

    monkeypatch.setattr(TranslateTool, "_mymemory", fake)
    return calls


@pytest.fixture
def small_project(isolated_dirs):
    """Factory: build a small fully-rendered project (silent audio, 320x180 @ 12 fps)."""
    from agents.audio_agent import AudioAgent
    from agents.story_agent.planner import template_script
    from agents.video_agent import VideoAgent
    from shared.schemas.pipeline import PipelineState
    from state_manager.snapshot import referenced_files
    from state_manager.state_manager import StateManager
    from state_manager.storage import SqliteStorage

    def build(project_id="t_small", duration_s=24, scenes=3, subtitle_language="English",
              with_subtitles=True, with_bgm=False, burn_subtitles=True):
        sm = StateManager(SqliteStorage(isolated_dirs / "state.db"))
        state = PipelineState(project_id=project_id, user_prompt="A robot learns to paint")
        state.script = template_script(project_id, state.user_prompt,
                                       target_duration_s=duration_s, scene_count=scenes)
        audio = AudioAgent()
        silence_tts(audio.tools)
        audio.run(state, with_bgm=with_bgm)
        VideoAgent().run(state, with_subtitles=with_subtitles,
                         subtitle_language=subtitle_language,
                         width=320, height=180, fps=12, burn_subtitles=burn_subtitles,
                         use_text_to_video=False, use_lip_sync=False, cinematic_post=False)
        sm.snapshot(state, asset_paths=referenced_files(state), description="initial")
        return state, sm

    return build
