"""Phase 4 — FastAPI smoke tests."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from tests.conftest import signed_in_client

@pytest.fixture
def client(isolated_dirs):
    """Every endpoint here needs an account since M5."""
    return signed_in_client(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    # Either the actual SPA or the fallback message.
    assert "Agentic" in r.text


def test_classify_endpoint(client):
    r = client.post("/api/edit/classify", json={"query": "make scene 2 darker"})
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "video_frame"
    assert body["scope"].startswith("scene:")


def test_classify_subtitles(client):
    r = client.post("/api/edit/classify", json={"query": "remove the subtitles"})
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "video"
    assert body["intent"] == "remove_subtitles"


def test_history_404(client):
    r = client.get("/api/history/__nonexistent__")
    assert r.status_code == 404


def test_state_404(client):
    r = client.get("/api/pipeline/state/__missing__")
    assert r.status_code == 404
