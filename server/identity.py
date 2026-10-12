"""Authenticated actor identity shared by HTTP auth, RBAC, jobs, and audit."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


_ROLE_RANK = {
    Role.VIEWER: 10,
    Role.ANALYST: 20,
    Role.ADMIN: 30,
}


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: Role

    def permits(self, required: Role | str) -> bool:
        required_role = required if isinstance(required, Role) else Role(required)
        return _ROLE_RANK[self.role] >= _ROLE_RANK[required_role]


class KeyState(str, Enum):
    REVOKED = "revoked"


def hash_api_key(raw_key: str) -> str:
    """Return the only key representation allowed in persistent storage."""
    if not isinstance(raw_key, str) or not raw_key:
        raise ValueError("API key must be a non-empty string")
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str, length: int = 12) -> str:
    """Return a short display-only key prefix for lifecycle administration."""
    if not isinstance(raw_key, str) or not raw_key:
        raise ValueError("API key must be a non-empty string")
    return raw_key[: max(4, int(length))]
