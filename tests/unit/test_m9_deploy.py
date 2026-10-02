"""M9 — the pieces a deployment leans on: the worker's health signal and the
voice model fetched on every container start."""
from __future__ import annotations

import hashlib
import importlib.util
import threading
from pathlib import Path

import pytest

from jobs import queue, worker

ROOT = Path(__file__).resolve().parents[2]


# ---- the worker says it's alive only when it is ----------------------------------

def _one_pass(monkeypatch, claim):
    stop = threading.Event()
    monkeypatch.setattr(queue, "claim", lambda *_a, **_k: (stop.set(), claim())[1])
    monkeypatch.setattr(queue, "requeue_stale", lambda: 0)
    worker.run_forever(poll_interval=0.01, stop=stop)


def test_a_worker_that_reaches_the_queue_marks_itself_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "ALIVE_FILE", tmp_path / "alive")
    _one_pass(monkeypatch, claim=lambda: None)
    assert (tmp_path / "alive").exists()


def test_a_worker_that_cannot_reach_the_queue_does_not(tmp_path, monkeypatch):
    """Its container then turns unhealthy — which is the truth."""
    monkeypatch.setattr(worker, "ALIVE_FILE", tmp_path / "alive")

    def database_down():
        raise ConnectionError("db unreachable")
    _one_pass(monkeypatch, claim=database_down)
    assert not (tmp_path / "alive").exists()


# ---- the voice model download ---------------------------------------------------------

@pytest.fixture
def get_kokoro(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("get_kokoro", ROOT / "scripts" / "get_kokoro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "TARGET", tmp_path)
    return module


def _serve(module, monkeypatch, body: bytes):
    def fake_download(_url, dest):
        Path(dest).write_bytes(body)
    monkeypatch.setattr(module, "download", fake_download)


def test_a_download_that_matches_its_checksum_is_kept(get_kokoro, tmp_path, monkeypatch):
    body = b"model bytes"
    _serve(get_kokoro, monkeypatch, body)
    assert get_kokoro.fetch("m.onnx", "url", hashlib.sha256(body).hexdigest())
    assert (tmp_path / "m.onnx").read_bytes() == body
    assert not (tmp_path / "m.onnx.part").exists()


def test_a_download_that_does_not_match_leaves_nothing_behind(get_kokoro, tmp_path,
                                                               monkeypatch):
    """The old script kept any file over 1 MB as 'already here' — an
    interrupted download included — and Kokoro loaded it as a model."""
    _serve(get_kokoro, monkeypatch, b"an error page, or half a file")
    assert not get_kokoro.fetch("m.onnx", "url", hashlib.sha256(b"model").hexdigest())
    assert list(tmp_path.iterdir()) == []


def test_a_damaged_file_already_there_is_replaced(get_kokoro, tmp_path, monkeypatch):
    good = b"model bytes"
    (tmp_path / "m.onnx").write_bytes(b"x" * 2_000_000)   # big enough to fool the old check
    _serve(get_kokoro, monkeypatch, good)
    assert get_kokoro.fetch("m.onnx", "url", hashlib.sha256(good).hexdigest())
    assert (tmp_path / "m.onnx").read_bytes() == good
