from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(BaseModel):
    """A registered account. `password_hash` never leaves the backend - API responses use a
    separate schema that omits it. It is None for accounts created via Google sign-in, which
    are identified by `google_sub` (Google's stable account ID) instead."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    email: str
    password_hash: str | None = None
    google_sub: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
