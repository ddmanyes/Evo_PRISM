"""Per-key authentication primitives with a safe owner-token compatibility path."""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Iterator

from server.identity import Identity, KeyState, Role, hash_api_key

if TYPE_CHECKING:
    from server.tool_catalog import ToolSpec

KeyLookup = Callable[[str], Identity | KeyState | None]

_LOCAL_ADMIN = Identity(user_id="local:process", role=Role.ADMIN)
_current_identity: ContextVar[Identity] = ContextVar(
    "evo_prism_current_identity", default=_LOCAL_ADMIN
)


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


def authorize_tool(
    identity: Identity,
    spec: "ToolSpec",
    *,
    via_queue: bool = False,
) -> AuthDecision:
    """Apply role hierarchy and the non-admin direct-analysis queue gate."""
    if not identity.permits(spec.required_role):
        return AuthDecision.reject("insufficient_role")
    if spec.queueable and identity.role is not Role.ADMIN and not via_queue:
        return AuthDecision.reject("queue_required")
    return AuthDecision.accept(identity)


def trusted_tool_arguments(arguments: dict | None, identity: Identity) -> dict:
    """Return a copy whose actor is derived from auth, never caller-controlled."""
    trusted = dict(arguments or {})
    trusted["requested_by"] = identity.user_id
    return trusted


def current_identity() -> Identity:
    return _current_identity.get()


def visible_requested_by(identity: Identity | None = None) -> str | None:
    """Return the analysis owner filter for an authenticated identity.

    ``None`` is deliberately reserved for the admin/global view.  Every
    non-admin role is mapped to its exact user_id, including viewer accounts.
    """
    actor = identity or current_identity()
    return None if actor.role is Role.ADMIN else actor.user_id


@contextmanager
def identity_scope(identity: Identity) -> Iterator[None]:
    token = _current_identity.set(identity)
    try:
        yield
    finally:
        _current_identity.reset(token)
