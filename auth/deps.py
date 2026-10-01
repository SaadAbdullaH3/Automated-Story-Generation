"""FastAPI dependencies — the single place a request becomes a user.

Routes never read the cookie themselves. They depend on `require_user` (or
`require_project`), so a new route is protected by default rather than by
someone remembering to add a check.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, WebSocket

from . import accounts, sessions
from .accounts import User


def _token(request: Request) -> Optional[str]:
    return request.cookies.get(sessions.COOKIE_NAME)


def current_user(request: Request) -> Optional[User]:
    """The signed-in user, or None. Never raises — use for optional auth."""
    return sessions.resolve(_token(request))


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        # 401 and not 403: the client should show a sign-in form.
        raise HTTPException(401, "sign in to continue")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "this needs an admin account")
    return user


def require_project(project_id: str, user: User = Depends(require_user)) -> User:
    """Authorise a project-scoped route.

    404, not 403, for a project someone else owns: a different status would
    confirm the id exists, which is itself worth hiding.
    """
    if not accounts.may_access(user, project_id):
        raise HTTPException(404, f"project {project_id} not found")
    return user


async def websocket_user(ws: WebSocket) -> Optional[User]:
    """Same cookie, but a WebSocket can't carry an HTTPException."""
    return sessions.resolve(ws.cookies.get(sessions.COOKIE_NAME))
