"""M9 — processes that start together on an empty database all come up.

The first `docker compose up` on a fresh server started the API and the worker
at the same moment; both created the tables, and the loser crashed with
`duplicate key value violates unique constraint "pg_type_typname_nsp_index"`.
A restart policy hid it — the second attempt found the tables made — but a
deployment shouldn't crash on its first boot.
"""
from __future__ import annotations

import multiprocessing
import os
import uuid

import pytest

from shared import db

PROCESSES = 6


def _bring_up(url: str, barrier, results) -> None:
    from sqlalchemy import select
    from shared import db as fresh       # imported before the barrier: only the
    barrier.wait()                        # schema step itself happens together
    try:
        engine = fresh.get_engine(url)
        with engine.connect() as c:
            c.execute(select(fresh.versions.c.id).limit(1)).all()
        results.put("ok")
    except Exception as e:  # noqa: BLE001
        results.put(f"{type(e).__name__}: {str(e).splitlines()[0][:160]}")


def _start_together(url: str):
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(PROCESSES), ctx.Queue()
    procs = [ctx.Process(target=_bring_up, args=(url, barrier, results))
             for _ in range(PROCESSES)]
    for p in procs:
        p.start()
    outcome = [results.get(timeout=120) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    return outcome


def test_processes_starting_together_on_a_fresh_sqlite_file_all_come_up(tmp_path):
    outcome = _start_together(db.url_for_path(tmp_path / "fresh.db"))
    assert outcome == ["ok"] * PROCESSES, outcome
    db.dispose_all()


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest.mark.skipif(not TEST_DATABASE_URL.startswith("postgresql"),
                    reason="set TEST_DATABASE_URL to a Postgres server")
def test_processes_starting_together_on_a_fresh_postgres_all_come_up():
    from sqlalchemy import create_engine, text
    admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    name = f"storygen_race_{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + name
        outcome = _start_together(url)
        assert outcome == ["ok"] * PROCESSES, outcome
    finally:
        db.dispose_all()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
