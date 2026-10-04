"""Sign up, sign in (with a password or GitHub), sign out, and who am I."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from auth import accounts, github, sessions
from auth.accounts import AuthError, User
from auth.deps import current_user, require_admin, require_user
from auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from shared.utils.logging import get_logger

router = APIRouter()
log = get_logger("api.auth")


class Credentials(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH,
                              max_length=MAX_PASSWORD_LENGTH)


class NewUser(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    role: str = "user"


def _start_session(response: Response, user: User, request: Request) -> None:
    token = sessions.create(
        user.id,
        user_agent=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else "",
    )
    response.set_cookie(sessions.COOKIE_NAME, token,
                        **sessions.cookie_kwargs(request.url.scheme))


@router.get("/status")
def status(user: Optional[User] = Depends(current_user)):
    """Public: what the sign-in screen needs to know before anyone is signed in."""
    return {
        "authenticated": user is not None,
        "user": user.as_dict() if user else None,
        # No accounts yet: the first visitor is setting the thing up.
        "needs_setup": accounts.count() == 0,
        "signups_allowed": accounts.signups_allowed(),
        "min_password_length": MIN_PASSWORD_LENGTH,
        "github": {
            "enabled": github.enabled(),
            "connected": bool(user) and github.PROVIDER in accounts.identities_for(user.id),
        },
    }


@router.post("/register")
def register(body: Credentials, request: Request, response: Response):
    """Create an account. The first one is the admin; after that it depends
    on ALLOW_SIGNUPS, so a private deployment doesn't quietly accept strangers."""
    if not accounts.signups_allowed():
        raise HTTPException(403, "sign-ups are closed on this deployment")
    try:
        user = accounts.create(body.email, body.password)
    except AuthError as e:
        raise HTTPException(400, str(e))
    _start_session(response, user, request)
    return {"user": user.as_dict()}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    try:
        user = accounts.authenticate(body.email, body.password)
    except AuthError as e:
        # 401 with one message for every kind of failure, so the endpoint
        # can't be used to find out which addresses are registered.
        raise HTTPException(401, str(e))
    _start_session(response, user, request)
    log.info("signed in: %s", user.email)
    return {"user": user.as_dict()}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(sessions.COOKIE_NAME)
    if token:
        sessions.revoke(token)
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return user.as_dict()


@router.post("/password")
def change_password(body: PasswordChange, request: Request, response: Response,
                    user: User = Depends(require_user)):
    """Change a password and sign every other device out."""
    try:
        accounts.authenticate(user.email, body.current_password)
    except AuthError:
        raise HTTPException(403, "current password is incorrect")
    try:
        accounts.set_password(user.id, body.new_password)
    except AuthError as e:
        raise HTTPException(400, str(e))
    sessions.revoke_all(user.id)
    _start_session(response, user, request)   # keep this device signed in
    return {"ok": True, "other_sessions_ended": True}


# ---- GitHub ------------------------------------------------------------------

def _back_to_app(request: Request, **outcome: str) -> RedirectResponse:
    """Return the browser to the app, saying how it went, and forget the state."""
    res = RedirectResponse("/" + (f"?{urlencode(outcome)}" if outcome else ""), status_code=303)
    res.delete_cookie(github.STATE_COOKIE, path=github.STATE_COOKIE_PATH)
    return res


@router.get("/github/start")
def github_start(request: Request, mode: str = "signin",
                 user: Optional[User] = Depends(current_user)):
    """Send the browser to GitHub, remembering a state only this browser holds."""
    if not github.enabled():
        raise HTTPException(404, "GitHub sign-in is not set up on this deployment")
    if mode not in github.MODES:
        raise HTTPException(400, f"unknown mode {mode!r}")
    if mode == "connect" and user is None:
        return _back_to_app(request, auth_error="sign in first, then connect GitHub")
    state = github.new_state(mode)
    res = RedirectResponse(
        github.authorize_url(state, github.callback_url(str(request.base_url))),
        status_code=302)
    res.set_cookie(github.STATE_COOKIE, state, max_age=github.STATE_TTL_S, httponly=True,
                   samesite="lax", path=github.STATE_COOKIE_PATH,
                   secure=sessions.cookie_kwargs(request.url.scheme)["secure"])
    return res


@router.get("/github/callback")
def github_callback(request: Request, code: str = "", state: str = "", error: str = "",
                    user: Optional[User] = Depends(current_user)):
    """GitHub sends the browser back here. Every outcome lands on the app's
    front page; a failure says why, and nothing is signed in."""
    if not github.enabled():
        raise HTTPException(404, "GitHub sign-in is not set up on this deployment")
    if error:
        return _back_to_app(request, auth_error=(
            "GitHub sign-in was cancelled" if error == "access_denied"
            else f"GitHub refused the sign-in ({error})"))
    expected = request.cookies.get(github.STATE_COOKIE)
    if not github.states_match(expected, state):
        # Checked before the code is spent: a forged callback costs nothing.
        return _back_to_app(request, auth_error=(
            "that sign-in link expired or wasn't started here, try again"))
    try:
        token = github.exchange_code(code, github.callback_url(str(request.base_url)))
        gh = github.fetch_user(token)
        if github.mode_of(expected) == "connect":
            if user is None:
                return _back_to_app(request, auth_error="sign in first, then connect GitHub")
            accounts.connect_identity(user, github.PROVIDER, gh.id, gh.login)
            return _back_to_app(request, connected=github.PROVIDER)
        signed_in = accounts.sign_in_external(github.PROVIDER, gh.id, gh.login, gh.email)
    except (github.GitHubError, AuthError) as e:
        return _back_to_app(request, auth_error=str(e))
    except requests.RequestException:
        log.warning("GitHub could not be reached during sign-in", exc_info=True)
        return _back_to_app(request, auth_error="couldn't reach GitHub, try again")

    res = _back_to_app(request)
    _start_session(res, signed_in, request)
    log.info("signed in through GitHub: %s (%s)", signed_in.email, gh.login)
    return res


@router.get("/sessions")
def my_sessions(user: User = Depends(require_user)):
    return [
        {"created_at": s.created_at.isoformat(),
         "last_seen_at": s.last_seen_at.isoformat(),
         "expires_at": s.expires_at.isoformat(),
         "user_agent": s.user_agent, "ip": s.ip}
        for s in sessions.list_for(user.id)
    ]


# ---- administration ----------------------------------------------------------

@router.get("/users")
def list_users(_admin: User = Depends(require_admin)):
    return [u.as_dict() for u in accounts.list_users()]


@router.post("/users")
def create_user(body: NewUser, _admin: User = Depends(require_admin)):
    """Add someone even when open sign-ups are off."""
    try:
        user = accounts.create(body.email, body.password, role=body.role)
    except AuthError as e:
        raise HTTPException(400, str(e))
    return user.as_dict()


class UserUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, admin: User = Depends(require_admin)):
    target = accounts.get(user_id)
    if target is None:
        raise HTTPException(404, "no such user")
    if target.id == admin.id and (body.role == "user" or body.is_active is False):
        # Locking the last admin out of their own deployment is unrecoverable
        # without the CLI, so it is refused here.
        raise HTTPException(400, "an admin cannot demote or disable themselves")
    if body.role is not None:
        accounts.set_role(user_id, body.role)
    if body.is_active is not None:
        accounts.set_active(user_id, body.is_active)
        if not body.is_active:
            sessions.revoke_all(user_id)
    return accounts.get(user_id).as_dict()
