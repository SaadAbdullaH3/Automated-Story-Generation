"""Sign in with GitHub — an OAuth app, authorization-code flow.

Off unless GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are set.

Two things it refuses on purpose:

- **It never joins a GitHub login to an existing account because the email
  matches.** Accounts here are made without proving the address is theirs, so
  someone could register a victim's email first and wait for the victim to
  "sign in with GitHub" into an account the attacker knows the password to
  (account pre-hijacking). An existing account connects GitHub while signed
  in instead.
- **It never accepts a callback whose `state` isn't the one this browser was
  handed** — that check is what stops another site from completing a sign-in
  into an account of its choosing (login CSRF).
"""
from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

PROVIDER = "github"
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_URL = "https://api.github.com"
SCOPES = "read:user user:email"

STATE_COOKIE = "storygen_github"
STATE_COOKIE_PATH = "/api/auth/github"
STATE_TTL_S = 600
TIMEOUT_S = 15
MODES = ("signin", "connect")

# Swapped out in tests; nothing here talks to GitHub any other way.
http = requests


class GitHubError(Exception):
    """GitHub refused, or answered with something unusable. Safe to show."""


@dataclass
class GitHubUser:
    id: str                 # stable numeric id — logins can be renamed
    login: str
    email: Optional[str]    # the primary, verified address, if GitHub shares one


def enabled() -> bool:
    return bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))


def callback_url(base_url: str) -> str:
    """Where GitHub sends the browser back.

    GITHUB_CALLBACK_URL pins it: behind a proxy the URL this process sees is
    not the one GitHub was registered with, and GitHub refuses a mismatch.
    """
    return (os.getenv("GITHUB_CALLBACK_URL")
            or base_url.rstrip("/") + "/api/auth/github/callback")


def new_state(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    return f"{mode}.{secrets.token_urlsafe(24)}"


def mode_of(state: str) -> str:
    return state.split(".", 1)[0]


def states_match(expected: Optional[str], returned: Optional[str]) -> bool:
    return bool(expected and returned) and hmac.compare_digest(expected, returned)


def authorize_url(state: str, redirect_uri: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": os.environ["GITHUB_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "allow_signup": "true",
    })


def exchange_code(code: str, redirect_uri: str) -> str:
    """Trade the one-time code for an access token."""
    if not code:
        raise GitHubError("GitHub didn't send a sign-in code — try again")
    res = http.post(TOKEN_URL, timeout=TIMEOUT_S, headers={"Accept": "application/json"},
                    data={"client_id": os.environ["GITHUB_CLIENT_ID"],
                          "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
                          "code": code, "redirect_uri": redirect_uri})
    try:
        body = res.json()
    except ValueError:
        body = {}
    token = body.get("access_token")
    if res.status_code != 200 or not token:
        # e.g. bad_verification_code: the code was used already or expired.
        reason = body.get("error_description") or body.get("error") or f"HTTP {res.status_code}"
        raise GitHubError(f"GitHub didn't accept the sign-in ({reason})")
    return token


def fetch_user(token: str) -> GitHubUser:
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    me = http.get(f"{API_URL}/user", headers=headers, timeout=TIMEOUT_S)
    if me.status_code != 200:
        raise GitHubError(f"couldn't read your GitHub profile (HTTP {me.status_code})")
    profile = me.json()
    email = None
    emails = http.get(f"{API_URL}/user/emails", headers=headers, timeout=TIMEOUT_S)
    if emails.status_code == 200:
        primary = next((e for e in emails.json()
                        if e.get("primary") and e.get("verified")), None)
        email = primary["email"] if primary else None
    return GitHubUser(id=str(profile["id"]), login=profile.get("login") or "", email=email)
