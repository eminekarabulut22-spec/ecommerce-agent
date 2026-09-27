"""Password hashing and session-token helpers for the simple email/password login.

Deliberately minimal: opaque random session tokens stored in the `sessions` table (checked
against the DB on every request), not JWTs - no signing key to manage, no token parsing bugs.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

SESSION_COOKIE_NAME = "session_token"
SESSION_TTL = timedelta(days=30)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash - never let that raise into an auth check.
        return False


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_session_expiry() -> datetime:
    return datetime.now(timezone.utc) + SESSION_TTL
