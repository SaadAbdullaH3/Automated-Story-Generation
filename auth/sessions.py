"""Sessions.

The cookie holds a random token; the database holds only its SHA-256. A dump
of the table therefore can't be replayed as a login, and because the session is
a row rather than a signed JWT, signing out and revoking a device actually
work — no denylist, no waiting for an expiry.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select

from shared import db

from .accounts import User, get as get_user, utcnow

COOKIE_NAME = "storygen_session"
SESSION_DAYS = 14
# Re-stamping last_seen on every request would write on every page load.
TOUCH_AFTER_MINUTES = 5


@dataclass
class Session:
    id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    user_agent: Optional[str] = None
    ip: Optional[str] = None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(user_id: str, user_agent: str = "", ip: str = "",
           days: int = SESSION_DAYS) -> str:
    """Start a session. Returns the raw token — the only time it exists."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    with db.get_engine().begin() as c:
        c.execute(db.sessions.insert().values(
            id=_digest(token), user_id=user_id, created_at=now,
            expires_at=now + timedelta(days=days), last_seen_at=now,
            user_agent=(user_agent or "")[:256], ip=(ip or "")[:64],
        ))
    return token


def resolve(token: Optional[str]) -> Optional[User]:
    """The signed-in user for a cookie value, or None."""
    if not token:
        return None
    digest = _digest(token)
    now = utcnow()
    with db.get_engine().connect() as c:
        row = c.execute(
            select(db.sessions).where(db.sessions.c.id == digest)
        ).mappings().first()
    if row is None:
        return None
    if row["expires_at"] <= now:
        revoke(token)
        return None

    user = get_user(row["user_id"])
    if user is None or not user.is_active:
        # The account was deleted or disabled while the cookie was still valid.
        revoke(token)
        return None

    if (now - row["last_seen_at"]) > timedelta(minutes=TOUCH_AFTER_MINUTES):
        with db.get_engine().begin() as c:
            c.execute(db.sessions.update()
                      .where(db.sessions.c.id == digest)
                      .values(last_seen_at=now))
    return user


def revoke(token: str) -> None:
    with db.get_engine().begin() as c:
        c.execute(db.sessions.delete().where(db.sessions.c.id == _digest(token)))


def revoke_all(user_id: str) -> int:
    """Sign a user out everywhere (password change, lost laptop)."""
    with db.get_engine().begin() as c:
        result = c.execute(db.sessions.delete().where(db.sessions.c.user_id == user_id))
    return result.rowcount or 0


def list_for(user_id: str) -> List[Session]:
    with db.get_engine().connect() as c:
        rows = c.execute(
            select(db.sessions)
            .where(db.sessions.c.user_id == user_id)
            .order_by(db.sessions.c.last_seen_at.desc())
        ).mappings().all()
    return [Session(**{k: r[k] for k in Session.__dataclass_fields__}) for r in rows]


def purge_expired() -> int:
    with db.get_engine().begin() as c:
        result = c.execute(db.sessions.delete().where(db.sessions.c.expires_at <= utcnow()))
    return result.rowcount or 0


def cookie_kwargs(scheme: str = "http") -> dict:
    """How the session cookie is set.

    HttpOnly so a script can't read it, SameSite=Lax so another site can't ride
    it on a POST, and Secure whenever the request arrived over HTTPS — marking
    it Secure on plain HTTP would make the browser drop it and the sign-in
    would appear to succeed and then not work. COOKIE_SECURE forces either way
    for a deployment behind a TLS-terminating proxy.
    """
    forced = os.getenv("COOKIE_SECURE", "").strip().lower()
    if forced in ("1", "true", "yes"):
        secure = True
    elif forced in ("0", "false", "no"):
        secure = False
    else:
        secure = scheme == "https"
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": bool(secure),
        "path": "/",
        "max_age": SESSION_DAYS * 24 * 3600,
    }
