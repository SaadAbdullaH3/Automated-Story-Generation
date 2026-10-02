"""Accounts: who exists, who may sign in, and who owns which project."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, select

from shared import db
from shared.utils.logging import get_logger

from . import passwords

log = get_logger("auth")

# After this many wrong passwords the account stops answering for a while.
# Per account rather than per IP, because the attacker picks the IP.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_MINUTES = 15

# Stored for accounts that sign in elsewhere. Not an argon2 hash, so no
# password ever verifies against it — there is nothing to guess.
NO_PASSWORD = "!external"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthError(Exception):
    """Sign-up or sign-in refused. The message is safe to show a user."""


@dataclass
class User:
    id: str
    email: str
    role: str = "user"
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "email": self.email, "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": (self.last_login_at.isoformat()
                              if self.last_login_at else None),
        }


def _user(row) -> User:
    return User(id=row["id"], email=row["email"], role=row["role"],
                is_active=bool(row["is_active"]), created_at=row["created_at"],
                last_login_at=row["last_login_at"])


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def count() -> int:
    with db.get_engine().connect() as c:
        return int(c.execute(select(func.count()).select_from(db.users)).scalar() or 0)


def signups_allowed() -> bool:
    """The first account is always allowed; after that it is a deployment choice."""
    if count() == 0:
        return True
    return os.getenv("ALLOW_SIGNUPS", "").strip().lower() in ("1", "true", "yes")


def get(user_id: str) -> Optional[User]:
    with db.get_engine().connect() as c:
        row = c.execute(select(db.users).where(db.users.c.id == user_id)).mappings().first()
    return _user(row) if row else None


def get_by_email(email: str) -> Optional[User]:
    with db.get_engine().connect() as c:
        row = c.execute(
            select(db.users).where(db.users.c.email == normalise_email(email))
        ).mappings().first()
    return _user(row) if row else None


def list_users() -> List[User]:
    with db.get_engine().connect() as c:
        rows = c.execute(select(db.users).order_by(db.users.c.created_at.asc())).mappings().all()
    return [_user(r) for r in rows]


def create(email: str, password: str, role: str = "user") -> User:
    """Create an account. The very first one is an admin, whatever is asked for."""
    try:
        password_hash = passwords.hash_password(password)
    except passwords.WeakPassword as e:
        raise AuthError(str(e)) from e
    return _insert(email, password_hash, role)


def _insert(email: str, password_hash: str, role: str = "user") -> User:
    email = normalise_email(email)
    if "@" not in email or len(email) < 3:
        raise AuthError("that doesn't look like an email address")

    first = count() == 0
    if first:
        role = "admin"
    if role not in ("user", "admin"):
        raise AuthError(f"unknown role {role!r}")

    user = User(id="usr_" + uuid.uuid4().hex[:16], email=email, role=role,
                created_at=utcnow())
    from sqlalchemy.exc import IntegrityError
    try:
        with db.get_engine().begin() as c:
            c.execute(db.users.insert().values(
                id=user.id, email=user.email, password_hash=password_hash,
                role=user.role, is_active=True, created_at=user.created_at,
                failed_attempts=0,
            ))
    except IntegrityError as e:
        # The unique index is the real guard — two simultaneous signups with
        # the same address both pass a "does it exist" check.
        raise AuthError("that email address is already registered") from e

    log.info("created %s account %s", user.role, user.email)
    if first:
        adopted = adopt_unowned_projects(user.id)
        if adopted:
            log.info("first admin adopted %d existing project(s)", adopted)
    return user


def authenticate(email: str, password: str) -> User:
    """Check a password. Raises AuthError with a deliberately vague message."""
    generic = AuthError("email or password is incorrect")
    with db.get_engine().connect() as c:
        row = c.execute(
            select(db.users).where(db.users.c.email == normalise_email(email))
        ).mappings().first()

    if row is None:
        # Same work as a real check, so timing doesn't reveal who is registered.
        passwords.dummy_verify()
        raise generic

    now = utcnow()
    if row["locked_until"] and row["locked_until"] > now:
        wait = int((row["locked_until"] - now).total_seconds() // 60) + 1
        raise AuthError(f"too many failed attempts — try again in {wait} minute(s)")
    if not row["is_active"]:
        raise AuthError("that account is disabled")

    if not passwords.verify(row["password_hash"], password):
        _record_failure(row["id"], int(row["failed_attempts"] or 0))
        raise generic

    values = {"failed_attempts": 0, "locked_until": None, "last_login_at": now}
    if passwords.needs_rehash(row["password_hash"]):
        # Cost parameters moved on since this password was set.
        values["password_hash"] = passwords.hash_password(password)
    with db.get_engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == row["id"]).values(**values))
    return _user({**row, "last_login_at": now})


def _record_failure(user_id: str, previous: int) -> None:
    attempts = previous + 1
    values: dict = {"failed_attempts": attempts}
    if attempts >= MAX_FAILED_ATTEMPTS:
        values["locked_until"] = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        log.warning("account %s locked after %d failed attempts", user_id, attempts)
    with db.get_engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == user_id).values(**values))


# ---- signing in elsewhere (GitHub) -----------------------------------------

PROVIDER_LABELS = {"github": "GitHub"}


def _label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def user_for_identity(provider: str, subject: str) -> Optional[User]:
    with db.get_engine().connect() as c:
        row = c.execute(
            select(db.users).join(db.identities, db.identities.c.user_id == db.users.c.id)
            .where(db.identities.c.provider == provider,
                   db.identities.c.subject == subject)
        ).mappings().first()
    return _user(row) if row else None


def identities_for(user_id: str) -> List[str]:
    """The providers this account can sign in with besides its password."""
    with db.get_engine().connect() as c:
        rows = c.execute(select(db.identities.c.provider)
                         .where(db.identities.c.user_id == user_id)).all()
    return sorted({r[0] for r in rows})


def _link(user_id: str, provider: str, subject: str, login: str) -> None:
    from sqlalchemy.exc import IntegrityError
    try:
        with db.get_engine().begin() as c:
            c.execute(db.identities.insert().values(
                provider=provider, subject=subject, user_id=user_id,
                login=login, created_at=utcnow()))
    except IntegrityError as e:
        # The primary key is the real guard against two accounts racing for it.
        raise AuthError(f"that {_label(provider)} account is connected to a different "
                        "account") from e


def connect_identity(user: User, provider: str, subject: str, login: str) -> None:
    """Let a signed-in account sign in with `provider` from now on."""
    holder = user_for_identity(provider, subject)
    if holder is not None and holder.id != user.id:
        raise AuthError(f"that {_label(provider)} account is connected to a different account")
    if holder is None:
        _link(user.id, provider, subject, login)
        log.info("%s connected %s (%s)", user.email, provider, login)


def sign_in_external(provider: str, subject: str, login: str,
                     email: Optional[str]) -> User:
    """The account a sign-in from `provider` belongs to, making one if allowed.

    Never joins an existing account because the email matches: addresses here
    are not proven, so whoever registered the address first would receive the
    sign-in. That account connects the provider while signed in instead.
    """
    user = user_for_identity(provider, subject)
    if user is not None:
        if not user.is_active:
            raise AuthError("that account is disabled")
        now = utcnow()
        with db.get_engine().begin() as c:
            c.execute(db.users.update().where(db.users.c.id == user.id)
                      .values(last_login_at=now))
        user.last_login_at = now
        return user
    name = _label(provider)
    if not email:
        raise AuthError(f"{name} didn't share a verified email address — verify one "
                        f"there, or sign in with a password")
    if get_by_email(email) is not None:
        raise AuthError("an account with that email already exists — sign in with your "
                        f"password, then connect {name} from the top bar")
    if not signups_allowed():
        raise AuthError("sign-ups are closed on this deployment")
    user = _insert(email, NO_PASSWORD)
    try:
        _link(user.id, provider, subject, login)
    except AuthError:
        # Another sign-in claimed this identity first; don't leave an
        # account behind that nothing can sign in to.
        with db.get_engine().begin() as c:
            c.execute(db.users.delete().where(db.users.c.id == user.id))
        raise
    log.info("created %s account %s through %s", user.role, user.email, name)
    return user


def set_password(user_id: str, password: str) -> None:
    try:
        password_hash = passwords.hash_password(password)
    except passwords.WeakPassword as e:
        raise AuthError(str(e)) from e
    with db.get_engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == user_id).values(
            password_hash=password_hash, failed_attempts=0, locked_until=None))


def set_role(user_id: str, role: str) -> None:
    if role not in ("user", "admin"):
        raise AuthError(f"unknown role {role!r}")
    with db.get_engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == user_id).values(role=role))


def set_active(user_id: str, active: bool) -> None:
    with db.get_engine().begin() as c:
        c.execute(db.users.update().where(db.users.c.id == user_id).values(is_active=active))


# ---- project ownership -------------------------------------------------------

def register_project(project_id: str, owner_id: Optional[str]) -> None:
    """Record who a project belongs to. Idempotent; never changes an owner."""
    with db.get_engine().begin() as c:
        existing = c.execute(
            select(db.projects.c.project_id)
            .where(db.projects.c.project_id == project_id)
        ).first()
        if existing:
            return
        c.execute(db.projects.insert().values(
            project_id=project_id, owner_id=owner_id, created_at=utcnow()))


def owner_of(project_id: str) -> Optional[str]:
    with db.get_engine().connect() as c:
        return c.execute(
            select(db.projects.c.owner_id)
            .where(db.projects.c.project_id == project_id)
        ).scalar()


def may_access(user: Optional[User], project_id: str) -> bool:
    """Admins see everything; everyone else sees only what they own.

    A project with no row and no owner was made outside the web app (the CLI),
    so it stays admin-only rather than becoming public by accident.
    """
    if user is None:
        return False
    if user.is_admin:
        return True
    return owner_of(project_id) == user.id


def projects_for(user: User) -> Optional[List[str]]:
    """Project ids this user may see, or None meaning 'no restriction' (admin)."""
    if user.is_admin:
        return None
    with db.get_engine().connect() as c:
        rows = c.execute(
            select(db.projects.c.project_id)
            .where(db.projects.c.owner_id == user.id)
        ).all()
    return [r[0] for r in rows]


def adopt_unowned_projects(owner_id: str) -> int:
    """Give the first admin the projects nobody owns.

    That means projects made before accounts existed, and projects whose owner
    was deleted — otherwise a removed account would strand its films with an
    id that resolves to nobody.
    """
    from state_manager.state_manager import StateManager
    with db.get_engine().connect() as c:
        rows = c.execute(select(db.projects.c.project_id, db.projects.c.owner_id)).all()
        live_users = {r[0] for r in c.execute(select(db.users.c.id)).all()}
    owned = {pid for pid, owner in rows if owner and owner in live_users}
    has_row = {pid for pid, _ in rows}

    adopted = 0
    for project_id in StateManager().list_projects():
        if project_id in owned:
            continue
        if project_id in has_row:
            with db.get_engine().begin() as c:
                c.execute(db.projects.update()
                          .where(db.projects.c.project_id == project_id)
                          .values(owner_id=owner_id))
        else:
            register_project(project_id, owner_id)
        adopted += 1
    return adopted
