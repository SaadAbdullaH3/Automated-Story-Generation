"""M8 — sign in with GitHub.

GitHub is faked at the HTTP layer (`auth.github.http`); everything on this
side — the state check, accounts, sessions, the redirects — is the real code.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from auth import accounts, github

EMAIL = "octo@example.test"


class FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeGitHub:
    """github.com and api.github.com, as far as sign-in uses them."""

    def __init__(self):
        self.user_id = 4242
        self.login = "octo"
        self.emails = [{"email": EMAIL, "primary": True, "verified": True}]
        self.token_error = None
        self.redirect_uris: list = []

    def post(self, url, data=None, headers=None, timeout=None):
        assert url == github.TOKEN_URL and data["client_secret"] == "test-secret"
        self.redirect_uris.append(data["redirect_uri"])
        if self.token_error:
            return FakeResponse(200, {"error": self.token_error,
                                      "error_description": "The code is incorrect or expired."})
        return FakeResponse(200, {"access_token": "gho_test", "token_type": "bearer"})

    def get(self, url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer gho_test"
        if url.endswith("/user"):
            return FakeResponse(200, {"id": self.user_id, "login": self.login})
        if url.endswith("/user/emails"):
            return FakeResponse(200, self.emails)
        raise AssertionError(url)


@pytest.fixture
def fake_github(isolated_dirs, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv-test-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-secret")
    monkeypatch.delenv("GITHUB_CALLBACK_URL", raising=False)
    monkeypatch.delenv("ALLOW_SIGNUPS", raising=False)
    fake = FakeGitHub()
    monkeypatch.setattr(github, "http", fake)
    return fake


def browser() -> TestClient:
    from backend.app import app
    return TestClient(app, follow_redirects=False)


def go_through_github(client: TestClient, mode: str = "signin", state: str | None = None,
                      **callback):
    start = client.get("/api/auth/github/start", params={"mode": mode})
    assert start.status_code == 302, start.text
    given = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    params = {"code": "one-time-code", "state": state or given, **callback}
    return client.get("/api/auth/github/callback", params=params)


def outcome(res) -> dict:
    """Where the callback sent the browser, as a dict of its query."""
    assert res.status_code == 303, res.text
    url = urlparse(res.headers["location"])
    assert url.path == "/"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def signed_in_as(client: TestClient):
    me = client.get("/api/auth/me")
    return me.json()["email"] if me.status_code == 200 else None


# ---- the redirect ----------------------------------------------------------------

def test_start_sends_the_browser_to_github_with_a_state_it_remembers(fake_github):
    res = browser().get("/api/auth/github/start")
    target = urlparse(res.headers["location"])
    query = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == github.AUTHORIZE_URL
    assert query["client_id"] == ["Iv-test-client"]
    assert query["redirect_uri"] == ["http://testserver/api/auth/github/callback"]
    assert query["scope"] == ["read:user user:email"]
    cookie = res.headers["set-cookie"]
    assert f"{github.STATE_COOKIE}={query['state'][0]}" in cookie
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()


def test_github_sign_in_is_off_unless_configured(isolated_dirs, monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    client = browser()
    assert client.get("/api/auth/github/start").status_code == 404
    assert client.get("/api/auth/status").json()["github"] == {"enabled": False,
                                                                "connected": False}


# ---- signing in --------------------------------------------------------------------

def test_the_first_github_sign_in_sets_the_place_up(fake_github):
    client = browser()
    assert outcome(go_through_github(client)) == {}
    assert signed_in_as(client) == EMAIL
    assert client.get("/api/auth/me").json()["role"] == "admin"
    assert client.get("/api/auth/status").json()["github"] == {"enabled": True,
                                                                "connected": True}
    assert fake_github.redirect_uris == ["http://testserver/api/auth/github/callback"]


def test_signing_in_again_finds_the_same_account(fake_github):
    first = browser()
    go_through_github(first)
    me = first.get("/api/auth/me").json()

    # Renamed on GitHub and a new address: still the same person, by GitHub id.
    fake_github.login, fake_github.emails = "octo-renamed", [
        {"email": "new@example.test", "primary": True, "verified": True}]
    again = browser()
    assert outcome(go_through_github(again)) == {}
    assert again.get("/api/auth/me").json()["id"] == me["id"]
    assert accounts.count() == 1


def test_a_callback_this_browser_did_not_start_is_refused(fake_github):
    client = browser()
    result = outcome(go_through_github(client, state="signin.forged-by-another-site"))
    assert "expired or wasn" in result["auth_error"]
    assert signed_in_as(client) is None

    # A callback arriving with no state cookie at all (someone else's link).
    stranger = browser()
    res = stranger.get("/api/auth/github/callback",
                       params={"code": "one-time-code", "state": "signin.whatever"})
    assert "auth_error" in outcome(res) and signed_in_as(stranger) is None
    assert fake_github.redirect_uris == []        # the code was never even spent


def test_an_existing_account_is_not_joined_by_email(fake_github):
    """Pre-hijacking: whoever registered the address first must not receive
    the GitHub user's sign-in."""
    accounts.create(EMAIL, "the-squatters-password")
    client = browser()
    result = outcome(go_through_github(client))
    assert "sign in with your password, then connect GitHub" in result["auth_error"]
    assert signed_in_as(client) is None
    assert accounts.user_for_identity(github.PROVIDER, "4242") is None


def test_connecting_github_to_the_account_you_are_signed_in_to(fake_github):
    owner = accounts.create(EMAIL, "a-test-passphrase")
    client = browser()
    assert client.post("/api/auth/login", json={"email": EMAIL,
                                                "password": "a-test-passphrase"}).status_code == 200
    assert outcome(go_through_github(client, mode="connect")) == {"connected": "github"}

    # From now on GitHub signs in to that same account.
    other_device = browser()
    assert outcome(go_through_github(other_device)) == {}
    assert other_device.get("/api/auth/me").json()["id"] == owner.id


def test_a_github_account_connected_elsewhere_cannot_be_taken(fake_github, monkeypatch):
    go_through_github(browser())                    # octo signs up with GitHub
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    accounts.create("someone@example.test", "a-test-passphrase")
    client = browser()
    client.post("/api/auth/login", json={"email": "someone@example.test",
                                         "password": "a-test-passphrase"})
    result = outcome(go_through_github(client, mode="connect"))
    assert "connected to a different account" in result["auth_error"]


def test_connecting_needs_someone_signed_in(fake_github):
    res = browser().get("/api/auth/github/start", params={"mode": "connect"})
    assert res.status_code == 303 and "auth_error" in res.headers["location"]


def test_closed_sign_ups_stay_closed_to_github(fake_github):
    accounts.create("admin@example.test", "a-test-passphrase")   # the place exists
    client = browser()
    result = outcome(go_through_github(client))
    assert "sign-ups are closed" in result["auth_error"]
    assert accounts.count() == 1


@pytest.mark.parametrize("emails", [
    [],
    [{"email": EMAIL, "primary": True, "verified": False}],
])
def test_without_a_verified_email_there_is_no_account(fake_github, emails):
    fake_github.emails = emails
    result = outcome(go_through_github(browser()))
    assert "verified email" in result["auth_error"]
    assert accounts.count() == 0


def test_a_github_account_has_no_password_to_guess(fake_github):
    go_through_github(browser())
    res = browser().post("/api/auth/login", json={"email": EMAIL, "password": ""})
    assert res.status_code in (401, 422)
    res = browser().post("/api/auth/login", json={"email": EMAIL, "password": "!external"})
    assert res.status_code == 401


def test_a_disabled_account_cannot_come_back_through_github(fake_github):
    go_through_github(browser())
    accounts.set_active(accounts.get_by_email(EMAIL).id, False)
    client = browser()
    assert "disabled" in outcome(go_through_github(client))["auth_error"]
    assert signed_in_as(client) is None


def test_what_github_refuses_is_reported_not_swallowed(fake_github):
    fake_github.token_error = "bad_verification_code"
    result = outcome(go_through_github(browser()))
    assert "GitHub didn't accept the sign-in" in result["auth_error"]

    result = outcome(go_through_github(browser(), error="access_denied"))
    assert "cancelled" in result["auth_error"]
