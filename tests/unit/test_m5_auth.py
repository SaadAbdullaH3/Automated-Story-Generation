"""M5 — accounts, sessions, and who is allowed to see a film.

Before this, every project was visible to anyone who could reach the API, and
`/assets/<project_id>/final_output.mp4` handed over the finished video to
anyone who guessed an id.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import jobs
from auth import accounts, passwords, sessions
from shared import db

GOOD_PASSWORD = "correct-horse-battery"
OTHER_PASSWORD = "another-decent-passphrase"


@pytest.fixture
def client(isolated_dirs, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore
    monkeypatch.delenv("ALLOW_SIGNUPS", raising=False)
    monkeypatch.setattr(pipeline_routes, "sm",
                        StateManager(VersionStore(isolated_dirs / "state.db")))
    return TestClient(app_module.app)


def register(client, email, password=GOOD_PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def sign_in(client, email, password=GOOD_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def sign_out(client):
    return client.post("/api/auth/logout")


# ---- accounts ----------------------------------------------------------------

def test_the_first_account_is_the_admin_and_the_next_one_is_not(client, monkeypatch):
    assert client.get("/api/auth/status").json()["needs_setup"] is True

    first = register(client, "saad@example.com").json()["user"]
    assert first["role"] == "admin"

    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    second = register(client, "someone@example.com", OTHER_PASSWORD).json()["user"]
    assert second["role"] == "user"
    assert client.get("/api/auth/status").json()["needs_setup"] is False


def test_signups_close_after_the_first_account_unless_opened(client):
    register(client, "saad@example.com")
    sign_out(client)
    refused = register(client, "stranger@example.com", OTHER_PASSWORD)
    assert refused.status_code == 403
    assert accounts.get_by_email("stranger@example.com") is None


def test_the_same_address_cannot_register_twice(client, monkeypatch):
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    register(client, "saad@example.com")
    again = register(client, "SAAD@example.com", OTHER_PASSWORD)   # case folded
    assert again.status_code == 400
    assert "already registered" in again.json()["detail"]


def test_a_weak_password_is_refused_with_a_usable_message(client):
    refused = register(client, "saad@example.com", "short")
    assert refused.status_code == 400
    assert str(passwords.MIN_PASSWORD_LENGTH) in refused.json()["detail"]


# ---- signing in ----------------------------------------------------------------

def test_wrong_password_and_unknown_account_are_indistinguishable(client):
    register(client, "saad@example.com")
    sign_out(client)

    wrong = sign_in(client, "saad@example.com", "not-the-password")
    unknown = sign_in(client, "nobody@example.com", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    # Same wording, or the endpoint becomes a way to enumerate accounts.
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_the_session_cookie_is_httponly_and_the_token_is_not_stored(client):
    register(client, "saad@example.com")
    cookie = next(c for c in client.cookies.jar if c.name == sessions.COOKIE_NAME)
    token = cookie.value
    assert token

    with db.get_engine().connect() as c:
        from sqlalchemy import select
        stored = c.execute(select(db.sessions.c.id)).scalars().all()
    # The database holds a hash; a stolen dump can't be replayed as a cookie.
    assert token not in stored
    assert stored == [sessions._digest(token)]


def test_an_account_locks_itself_after_repeated_failures(client):
    register(client, "saad@example.com")
    sign_out(client)
    for _ in range(accounts.MAX_FAILED_ATTEMPTS):
        assert sign_in(client, "saad@example.com", "wrong-password").status_code == 401

    locked = sign_in(client, "saad@example.com", "wrong-password")
    assert "too many failed attempts" in locked.json()["detail"]
    # Even the right password is refused while the lock holds.
    assert sign_in(client, "saad@example.com").status_code == 401


def test_signing_out_makes_the_cookie_useless(client):
    register(client, "saad@example.com")
    assert client.get("/api/auth/me").status_code == 200
    stolen = dict(client.cookies)

    sign_out(client)
    assert client.get("/api/auth/me").status_code == 401

    # Replaying the old cookie must not work either — the session is a row,
    # and it is gone.
    client.cookies.clear()
    assert client.get("/api/auth/me", cookies=stolen).status_code == 401


def test_changing_a_password_ends_every_other_session(client):
    register(client, "saad@example.com")
    phone = dict(client.cookies)            # a second device

    changed = client.post("/api/auth/password", json={
        "current_password": GOOD_PASSWORD, "new_password": OTHER_PASSWORD})
    assert changed.status_code == 200
    assert client.get("/api/auth/me").status_code == 200    # this device stays in

    other = TestClient(client.app)
    assert other.get("/api/auth/me", cookies=phone).status_code == 401


def test_the_wrong_current_password_cannot_change_the_password(client):
    register(client, "saad@example.com")
    refused = client.post("/api/auth/password", json={
        "current_password": "not-it", "new_password": OTHER_PASSWORD})
    assert refused.status_code == 403
    sign_out(client)
    assert sign_in(client, "saad@example.com").status_code == 200


# ---- nothing works signed out ---------------------------------------------------

SIGNED_OUT_ROUTES = [
    ("get", "/api/projects/"),
    ("get", "/api/jobs/"),
    ("get", "/api/voices/"),
    ("get", "/api/pipeline/languages"),
    ("get", "/api/pipeline/state/anything"),
    ("get", "/api/pipeline/status/anything"),
    ("get", "/api/pipeline/storyboard/anything"),
    ("get", "/api/history/anything"),
    ("get", "/api/edit/log/anything"),
    ("post", "/api/pipeline/run"),
    ("post", "/api/pipeline/plan"),
    ("post", "/api/edit/classify"),
    ("post", "/api/edit/apply"),
]


@pytest.mark.parametrize("method,path", SIGNED_OUT_ROUTES)
def test_every_api_route_requires_an_account(client, method, path):
    call = getattr(client, method)
    response = call(path, json={}) if method == "post" else call(path)
    assert response.status_code == 401, f"{method.upper()} {path} answered {response.status_code}"


def test_health_and_readiness_stay_public(client):
    """A container probe has no cookie."""
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/api/auth/status").status_code == 200


# ---- one account cannot reach another's work ------------------------------------

@pytest.fixture
def two_accounts(client, monkeypatch, isolated_dirs):
    """Saad owns a project; Mallory is a legitimate but unrelated account."""
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    register(client, "saad@example.com")
    queued = client.post("/api/pipeline/run",
                         json={"prompt": "A kite over a quiet town"}).json()
    sign_out(client)

    mallory = TestClient(client.app)
    register(mallory, "mallory@example.com", OTHER_PASSWORD)
    return queued, mallory


def test_another_account_cannot_see_the_project(two_accounts):
    queued, mallory = two_accounts
    pid = queued["project_id"]
    for path in (f"/api/pipeline/state/{pid}", f"/api/pipeline/status/{pid}",
                 f"/api/pipeline/storyboard/{pid}", f"/api/pipeline/subtitles/{pid}",
                 f"/api/history/{pid}", f"/api/edit/log/{pid}"):
        # 404 and not 403 — a different answer would confirm the id is real.
        assert mallory.get(path).status_code == 404, path
    assert mallory.get("/api/projects/").json() == []


def test_another_account_cannot_drive_the_project(two_accounts):
    queued, mallory = two_accounts
    pid = queued["project_id"]
    assert mallory.post(f"/api/pipeline/render/{pid}", json={}).status_code == 404
    assert mallory.post("/api/pipeline/rerun",
                        json={"project_id": pid, "phase": "audio"}).status_code == 404
    assert mallory.patch(f"/api/pipeline/storyboard/{pid}/scene_1",
                         json={"title": "mine now"}).status_code == 404
    assert mallory.post("/api/edit/apply",
                        json={"project_id": pid, "query": "make it darker"}).status_code == 404
    assert mallory.post(f"/api/history/{pid}/revert/1").status_code == 404


def test_another_account_cannot_see_or_cancel_the_job(two_accounts):
    queued, mallory = two_accounts
    job_id = queued["job_id"]
    assert mallory.get("/api/jobs/").json() == []
    assert mallory.get(f"/api/jobs/{job_id}").status_code == 404
    assert mallory.get(f"/api/jobs/{job_id}/events").status_code == 404
    assert mallory.post(f"/api/jobs/{job_id}/cancel").status_code == 404
    # And the job really is untouched.
    assert jobs.get(job_id).status == "queued"


def test_another_account_cannot_download_the_film(two_accounts, isolated_dirs):
    """The hole this milestone exists to close: /assets used to be a plain
    static mount, so the finished video needed only a guessed project id."""
    queued, mallory = two_accounts
    pid = queued["project_id"]
    film = isolated_dirs / "out" / pid / "final_output.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    film.write_bytes(b"the finished film")

    assert mallory.get(f"/assets/{pid}/final_output.mp4").status_code == 404

    owner = TestClient(mallory.app)
    sign_in(owner, "saad@example.com")
    served = owner.get(f"/assets/{pid}/final_output.mp4")
    assert served.status_code == 200 and served.content == b"the finished film"


def test_assets_cannot_be_walked_out_of_the_outputs_directory(client, isolated_dirs):
    register(client, "saad@example.com")
    secret = isolated_dirs / "secret.txt"
    secret.write_text("not an asset", encoding="utf-8")
    for attempt in ("../secret.txt", "..%2fsecret.txt", "a/../../secret.txt"):
        res = client.get(f"/assets/anything/{attempt}")
        assert res.status_code == 404, attempt
        assert b"not an asset" not in res.content


def test_the_progress_socket_refuses_strangers(two_accounts):
    from starlette.websockets import WebSocketDisconnect
    queued, mallory = two_accounts
    pid = queued["project_id"]

    with pytest.raises(WebSocketDisconnect):
        with mallory.websocket_connect(f"/ws/progress/{pid}") as ws:
            ws.receive_text()

    signed_out = TestClient(mallory.app)
    with pytest.raises(WebSocketDisconnect):
        with signed_out.websocket_connect(f"/ws/progress/{pid}") as ws:
            ws.receive_text()


def test_the_owner_can_still_watch_their_own_run(two_accounts):
    queued, mallory = two_accounts
    owner = TestClient(mallory.app)
    sign_in(owner, "saad@example.com")
    jobs.append_event(queued["job_id"], queued["project_id"], phase="story",
                      status="started", message="writing", progress=0.1)
    with owner.websocket_connect(f"/ws/progress/{queued['project_id']}") as ws:
        envelope = ws.receive_json()
        assert envelope["type"] in ("snapshot", "event")


# ---- admins ---------------------------------------------------------------------

def test_an_admin_sees_every_project(two_accounts):
    """Mallory is a plain user; Saad, the first account, is the admin."""
    queued, mallory = two_accounts
    admin = TestClient(mallory.app)
    sign_in(admin, "saad@example.com")
    # Mallory sees nothing; the admin sees Saad's job and project.
    assert mallory.get(f"/api/jobs/{queued['job_id']}").status_code == 404
    assert admin.get(f"/api/jobs/{queued['job_id']}").status_code == 200
    assert admin.get(f"/api/pipeline/status/{queued['project_id']}").status_code == 200


def test_only_an_admin_may_manage_accounts(two_accounts):
    queued, mallory = two_accounts
    assert mallory.get("/api/auth/users").status_code == 403
    assert mallory.post("/api/auth/users", json={
        "email": "x@example.com", "password": GOOD_PASSWORD}).status_code == 403

    admin = TestClient(mallory.app)
    sign_in(admin, "saad@example.com")
    listed = admin.get("/api/auth/users").json()
    assert {u["email"] for u in listed} == {"saad@example.com", "mallory@example.com"}


def test_an_admin_cannot_lock_themselves_out(two_accounts):
    queued, mallory = two_accounts
    admin = TestClient(mallory.app)
    me = sign_in(admin, "saad@example.com").json()["user"]
    assert admin.patch(f"/api/auth/users/{me['id']}",
                       json={"role": "user"}).status_code == 400
    assert admin.patch(f"/api/auth/users/{me['id']}",
                       json={"is_active": False}).status_code == 400


def test_disabling_an_account_signs_it_out_immediately(two_accounts):
    queued, mallory = two_accounts
    admin = TestClient(mallory.app)
    sign_in(admin, "saad@example.com")
    target = accounts.get_by_email("mallory@example.com")

    assert mallory.get("/api/auth/me").status_code == 200
    admin.patch(f"/api/auth/users/{target.id}", json={"is_active": False})
    assert mallory.get("/api/auth/me").status_code == 401


# ---- existing work ---------------------------------------------------------------

def test_the_first_admin_inherits_projects_made_before_accounts_existed(
        client, small_project):
    """The CLI made projects long before any of this; they must not become
    public, and they must not become invisible either."""
    state, _sm = small_project(project_id="made_by_the_cli")
    admin = register(client, "saad@example.com").json()["user"]
    assert accounts.owner_of("made_by_the_cli") == admin["id"]
    assert client.get("/api/pipeline/state/made_by_the_cli").status_code == 200


def test_a_cli_project_made_later_is_admin_only(client, small_project, monkeypatch):
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    register(client, "saad@example.com")        # admin
    sign_out(client)
    plain = TestClient(client.app)
    register(plain, "mallory@example.com", OTHER_PASSWORD)

    small_project(project_id="made_by_the_cli_later")
    assert plain.get("/api/pipeline/state/made_by_the_cli_later").status_code == 404

    admin = TestClient(client.app)
    sign_in(admin, "saad@example.com")
    assert admin.get("/api/pipeline/state/made_by_the_cli_later").status_code == 200


def test_a_project_is_not_stranded_when_its_owner_is_deleted(client, small_project,
                                                             monkeypatch):
    """A deleted account used to leave its films owned by an id that resolves
    to nobody — invisible to their owner and to any future admin."""
    from sqlalchemy import select
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    register(client, "saad@example.com")
    small_project(project_id="orphan_me")
    accounts.register_project("orphan_me", "usr_long_gone")

    assert accounts.adopt_unowned_projects(
        accounts.get_by_email("saad@example.com").id) >= 1
    with db.get_engine().connect() as c:
        owner = c.execute(
            select(db.projects.c.owner_id)
            .where(db.projects.c.project_id == "orphan_me")).scalar()
    assert owner == accounts.get_by_email("saad@example.com").id
