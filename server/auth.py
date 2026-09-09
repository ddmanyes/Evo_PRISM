"""Per-key authentication primitives with a safe owner-token compatibility path."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Callable, Iterable

from server.identity import Identity, KeyState, Role, hash_api_key

KeyLookup = Callable[[str], Identity | KeyState | None]


@dataclass(frozen=True)
class AuthDecision:
    accepted: bool
    identity: Identity | None
    reason: str

    @classmethod
    def accept(cls, identity: Identity) -> "AuthDecision":
        return cls(accepted=True, identity=identity, reason="accepted")

    @classmethod
    def reject(cls, reason: str) -> "AuthDecision":
        return cls(accepted=False, identity=None, reason=reason)


def authenticate_token(
    raw_token: str | None,
    *,
    lookup_fn: KeyLookup,
    owner_tokens: Iterable[str],
) -> AuthDecision:
    """Authenticate DB identities before the legacy env-owner fallback.

    The ordering is security-critical: a revoked key must never fall through to
    the owner-token compatibility path, even if the same secret is still present
    in an environment variable during key rotation.
    """
    if not raw_token:
        return AuthDecision.reject("missing")

    resolved = lookup_fn(hash_api_key(raw_token))
    if isinstance(resolved, Identity):
        return AuthDecision.accept(resolved)
    if resolved is KeyState.REVOKED:
        return AuthDecision.reject("revoked")

    for owner_token in owner_tokens:
        if owner_token and hmac.compare_digest(raw_token, owner_token):
            return AuthDecision.accept(Identity(user_id="env:owner", role=Role.ADMIN))
    return AuthDecision.reject("invalid")
