"""Password hashing.

Argon2id, with argon2-cffi's defaults — OWASP's first recommendation, and the
library tracks the parameters so this file doesn't have to. Nothing here is
hand-rolled: hashing a password yourself is how passwords get lost.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Short enough to be typed, long enough that argon2 is doing real work. Length
# beats composition rules, so there are no composition rules.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024   # argon2 handles long input; this stops silly payloads

_hasher = PasswordHasher()


class WeakPassword(ValueError):
    """The password doesn't meet the minimum."""


def check_strength(password: str) -> None:
    """Raise WeakPassword with a message a person can act on."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPassword("password is too long")
    if not password.strip():
        raise WeakPassword("password cannot be only whitespace")


def hash_password(password: str) -> str:
    check_strength(password)
    return _hasher.hash(password)


def verify(password_hash: str, password: str) -> bool:
    """Constant-time check. False for a wrong password or a corrupt hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current cost parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn the same time as a real check when the account doesn't exist.

    Without it, "no such user" returns measurably faster than "wrong password"
    and the login endpoint tells an attacker which addresses are registered.
    """
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-password")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


# Hashed once at import so every failed login pays the same cost.
_DUMMY_HASH = _hasher.hash("a password that is never anyone's password")
