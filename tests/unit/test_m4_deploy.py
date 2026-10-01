"""M4 — running somewhere other than this laptop: asset URLs, fonts, containers."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from shared import assets, constants, fonts

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.delenv("STORAGE_URL", raising=False)
    assets.reset()
    yield
    assets.reset()


# ---- asset URLs --------------------------------------------------------------

def test_a_windows_path_becomes_a_posix_key(isolated_dirs, monkeypatch):
    """States written on Windows get read back inside a Linux container."""
    monkeypatch.setattr(constants, "OUTPUTS_DIR", Path(r"C:\app\data\outputs"))
    assert assets.key_for(r"C:\app\data\outputs\pid1\subtitles\urdu.vtt") == \
        "pid1/subtitles/urdu.vtt"
    assert assets.key_for("/srv/app/data/outputs/pid1/final.mp4") == "pid1/final.mp4"
    assert assets.key_for("") is None
    assert assets.key_for("/somewhere/else/file.mp4") is None


def test_local_storage_serves_from_the_api(isolated_dirs):
    url = assets.asset_url(constants.OUTPUTS_DIR / "pid1" / "final.mp4")
    assert url == "/assets/pid1/final.mp4"
    assert assets.backend_name() == "local"
    assert assets.asset_url(None) is None


def test_publishing_to_local_storage_copies_nothing(isolated_dirs):
    film = constants.OUTPUTS_DIR / "pid1" / "final.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    film.write_bytes(b"film")
    assert assets.publish([film]) == {str(film): "/assets/pid1/final.mp4"}


def test_object_storage_uploads_and_hands_back_a_public_url(isolated_dirs, monkeypatch):
    monkeypatch.setenv("STORAGE_URL", "s3://films")
    monkeypatch.setenv("S3_PUBLIC_BASE", "https://cdn.example.com")
    assets.reset()

    uploads = []

    class FakeS3:
        def upload_file(self, path, bucket, key):
            uploads.append((Path(path).name, bucket, key))

    monkeypatch.setattr(assets.S3Assets, "client", lambda self: FakeS3())

    film = constants.OUTPUTS_DIR / "pid1" / "final.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    film.write_bytes(b"film")

    assert assets.backend_name() == "s3"
    assert assets.publish([film]) == {str(film): "https://cdn.example.com/pid1/final.mp4"}
    assert uploads == [("final.mp4", "films", "pid1/final.mp4")]
    assert assets.asset_url(film) == "https://cdn.example.com/pid1/final.mp4"


def test_a_failed_upload_does_not_lose_the_render(isolated_dirs, monkeypatch, caplog):
    monkeypatch.setenv("STORAGE_URL", "s3://films")
    assets.reset()

    class BrokenS3:
        def upload_file(self, *_a):
            raise OSError("bucket unreachable")

    monkeypatch.setattr(assets.S3Assets, "client", lambda self: BrokenS3())
    film = constants.OUTPUTS_DIR / "pid1" / "final.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    film.write_bytes(b"film")

    assert assets.publish([film]) == {}          # reported, not raised
    assert "bucket unreachable" in caplog.text


def test_an_unusable_storage_url_falls_back_to_disk(isolated_dirs, monkeypatch, caplog):
    monkeypatch.setenv("STORAGE_URL", "gs://not-supported")
    assets.reset()
    assert assets.backend_name() == "local"
    assert "unrecognised STORAGE_URL" in caplog.text


# ---- fonts --------------------------------------------------------------------

def test_a_wide_script_gets_a_font_that_has_its_glyphs(monkeypatch):
    """"Segoe UI" only exists on Windows, so the container needs another name."""
    monkeypatch.setattr(fonts, "is_installed", lambda f: f == "Noto Sans")
    fonts._first_installed.cache_clear()
    assert fonts.font_for("Urdu") == "Noto Sans"
    assert fonts.font_for("Japanese") == "Noto Sans"
    fonts._first_installed.cache_clear()


def test_latin_and_wide_scripts_can_resolve_differently(monkeypatch):
    installed = {"Arial", "Noto Sans"}
    monkeypatch.setattr(fonts, "is_installed", lambda f: f in installed)
    fonts._first_installed.cache_clear()
    assert fonts.font_for("English") == "Arial"
    assert fonts.font_for("Hindi") == "Noto Sans"
    fonts._first_installed.cache_clear()


def test_no_font_installed_still_names_one(monkeypatch):
    monkeypatch.setattr(fonts, "is_installed", lambda _f: False)
    fonts._first_installed.cache_clear()
    assert fonts.font_for("Urdu") == fonts.WIDE_SCRIPT_FONTS[0]
    assert fonts.font_for("English") == fonts.LATIN_FONTS[0]
    fonts._first_installed.cache_clear()


def test_the_subtitle_filter_uses_the_font_that_was_resolved(tmp_path, monkeypatch):
    """The ffmpeg call must carry the resolved family, not a hard-coded name."""
    import subprocess

    from mcp.tools.video_tools import subtitle_tool

    monkeypatch.setattr(subtitle_tool, "font_for", lambda _lang: "Noto Sans")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subtitle_tool.subprocess, "run", fake_run)
    res = subtitle_tool.SubtitleTool().run(
        in_path=str(tmp_path / "in.mp4"), out_path=str(tmp_path / "out.mp4"),
        lines=[{"start_ms": 0, "end_ms": 1000, "text": "hello"}], language="Urdu")

    assert res.success and res.metadata["font"] == "Noto Sans"
    assert "FontName=Noto Sans" in " ".join(seen["cmd"])


# ---- containers ----------------------------------------------------------------

def test_compose_runs_the_api_and_the_worker_against_one_database():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    api, worker = compose["services"]["api"], compose["services"]["worker"]

    assert api["environment"]["DATABASE_URL"] == worker["environment"]["DATABASE_URL"]
    # The API must not also run a worker thread, or jobs run twice over.
    assert api["environment"]["WORKER_INLINE"] == "0"
    assert worker["command"][-1] == "worker"
    # Both wait for Postgres: their first query would otherwise race its start-up.
    for service in (api, worker):
        assert service["depends_on"]["db"]["condition"] == "service_healthy"


def test_the_image_installs_ffmpeg_and_fonts_for_non_latin_subtitles():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ffmpeg" in dockerfile
    assert "fonts-noto-core" in dockerfile and "fonts-noto-cjk" in dockerfile
    assert "USER app" in dockerfile          # the pipeline never runs as root


def test_the_build_context_excludes_secrets_and_generated_films():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignored and "data/" in ignored


def test_ready_reports_where_state_and_assets_live(isolated_dirs):
    from backend.app import app
    from shared import db
    body = TestClient(app).get("/ready").json()
    assert body == {"status": "ok", "database": db.get_engine().dialect.name,
                    "storage": "local", "inline_worker": False}


# ---- the Postgres path --------------------------------------------------------

def test_the_claim_statement_compiles_to_skip_locked_on_postgres():
    """No Postgres server here, but the SQL it would send can still be checked:
    without SKIP LOCKED, workers queue behind each other's locked rows."""
    from sqlalchemy.dialects import postgresql, sqlite

    from jobs import queue

    now = queue.utcnow()
    pg = str(queue.claim_statement("w1", now, "postgresql")
             .compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in pg
    assert "RETURNING" in pg.upper()
    # The guard that makes the loser's UPDATE match nothing must survive too.
    assert pg.count("status") >= 2

    lite = str(queue.claim_statement("w1", now, "sqlite")
               .compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE" not in lite        # SQLite has no row locks
    assert "RETURNING" in lite.upper()


def test_the_schema_builds_on_postgres_too():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from shared import db

    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in db.metadata.sorted_tables
    )
    for table in ("versions", "edit_log", "jobs", "job_events"):
        assert f"CREATE TABLE {table}" in ddl
    assert "SERIAL" in ddl.upper() or "IDENTITY" in ddl.upper()


def test_a_postgres_url_is_rewritten_for_the_installed_driver(monkeypatch):
    """Hosted Postgres hands out postgres:// URLs, which SQLAlchemy rejects."""
    from shared import db
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/storygen")
    assert db.default_url() == "postgresql+psycopg://u:p@host:5432/storygen"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/storygen")
    assert db.default_url() == "postgresql+psycopg://u:p@host/storygen"


# ---- a real S3 endpoint -------------------------------------------------------
# Opt-in, the same way TEST_DATABASE_URL works. Against Cloudflare R2:
#   TEST_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com \
#   TEST_S3_BUCKET=storygen AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
#   python -m pytest tests/unit/test_m4_deploy.py -k real_s3
LIVE_S3 = os.getenv("TEST_S3_ENDPOINT", "").strip()


@pytest.mark.skipif(not LIVE_S3,
                    reason="set TEST_S3_ENDPOINT to test against a real S3/R2 server")
def test_a_film_published_to_real_s3_comes_back_byte_for_byte(isolated_dirs, monkeypatch):
    """Everything else about the S3 backend is tested against a fake client.
    This is the one that talks the actual protocol: signing, PUT, presigned GET."""
    import requests

    bucket = os.getenv("TEST_S3_BUCKET", "storygen-test")
    monkeypatch.setenv("STORAGE_URL", f"s3://{bucket}")
    monkeypatch.setenv("S3_ENDPOINT_URL", LIVE_S3)
    monkeypatch.delenv("S3_PUBLIC_BASE", raising=False)
    assets.reset()

    store = assets.store()
    region = os.getenv("S3_REGION", "auto")
    try:
        store.client().head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001 — first run against a fresh server
        make = {"Bucket": bucket}
        if region != "us-east-1":
            # Every region but the default one must be named explicitly.
            make["CreateBucketConfiguration"] = {"LocationConstraint": region}
        store.client().create_bucket(**make)

    film = constants.OUTPUTS_DIR / "pid_live" / "final_output.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    payload = b"not really an mp4, but the bytes must survive the round trip" * 400
    film.write_bytes(payload)

    published = assets.publish([film])
    url = published[str(film)]
    assert "pid_live/final_output.mp4" in url

    fetched = requests.get(url, timeout=30)
    assert fetched.status_code == 200
    assert fetched.content == payload

    # And the key is laid out the way the rest of the app expects.
    listing = store.client().list_objects_v2(Bucket=bucket, Prefix="pid_live/")
    assert [o["Key"] for o in listing["Contents"]] == ["pid_live/final_output.mp4"]


def test_having_bucket_credentials_does_not_switch_storage_on(isolated_dirs,
                                                              monkeypatch):
    """Local disk is the default and stays the default.

    The R2 keys live in .env so a deployment can use them, but credentials
    sitting in the environment must not be what decides where assets go —
    only STORAGE_URL does. Otherwise adding a key to try something quietly
    reroutes every local render through a bucket.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "an-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "a-secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://account.r2.cloudflarestorage.com")
    monkeypatch.setenv("S3_REGION", "auto")
    monkeypatch.delenv("STORAGE_URL", raising=False)
    assets.reset()

    assert assets.backend_name() == "local"
    assert assets.asset_url(constants.OUTPUTS_DIR / "pid" / "film.mp4") == \
        "/assets/pid/film.mp4"

    # ... and naming the bucket is the single thing that changes it.
    monkeypatch.setenv("STORAGE_URL", "s3://multi-agent-storygen")
    assets.reset()
    assert assets.backend_name() == "s3"
