"""Per-key authentication primitives with a safe owner-token compatibility path."""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
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


def can_read_analysis(con, analysis_id: str, *, requested_by: str | None) -> bool:
    """Return whether an analysis exists inside the caller's owner scope."""
    owner_clause = " AND requested_by = ?" if requested_by is not None else ""
    params = [analysis_id] + ([requested_by] if requested_by is not None else [])
    row = con.execute(
        f"SELECT 1 FROM analysis_history WHERE analysis_id = ?{owner_clause}", params
    ).fetchone()
    return row is not None


def can_read_artifact(con, artifact_id: str, *, requested_by: str | None) -> bool:
    """Return whether an artifact belongs to a visible analysis record."""
    owner_clause = " AND ah.requested_by = ?" if requested_by is not None else ""
    params = [artifact_id] + ([requested_by] if requested_by is not None else [])
    row = con.execute(
        f"""
        SELECT 1
        FROM analysis_artifacts aa
        JOIN analysis_history ah ON ah.analysis_id = aa.analysis_id
        WHERE aa.artifact_id = ?{owner_clause}
        """,
        params,
    ).fetchone()
    return row is not None


def _resolved_record_path(raw_path: str, root: Path) -> Path:
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def can_read_analysis_path(con, raw_path: str, *, requested_by: str | None) -> bool:
    """Authorize a result/artifact path without trusting caller-controlled paths.

    Admin callers are still constrained by ``report_reader``'s path sandbox. For
    members, the resolved target must equal an owned registered file, or be a
    descendant of an owned directory-style ``result_path``.
    """
    if requested_by is None:
        return True

    from config.settings import BIO_DB_ROOT

    target = _resolved_record_path(raw_path, BIO_DB_ROOT)
    registered: list[str] = []
    rows = con.execute(
        "SELECT result_path FROM analysis_history "
        "WHERE requested_by = ? AND result_path IS NOT NULL",
        [requested_by],
    ).fetchall()
    registered.extend(str(row[0]) for row in rows if row[0])
    try:
        artifact_rows = con.execute(
            """
            SELECT aa.file_path
            FROM analysis_artifacts aa
            JOIN analysis_history ah ON ah.analysis_id = aa.analysis_id
            WHERE ah.requested_by = ? AND aa.file_path IS NOT NULL
            """,
            [requested_by],
        ).fetchall()
        registered.extend(str(row[0]) for row in artifact_rows if row[0])
    except Exception:
        # Older/minimal schemas may not have artifact tables; history paths are
        # still authoritative and a missing optional table must not fail open.
        pass

    for stored in registered:
        candidate = _resolved_record_path(stored, BIO_DB_ROOT)
        if target == candidate:
            return True
        if candidate.is_dir():
            try:
                target.relative_to(candidate)
            except ValueError:
                continue
            return True
    return False


@contextmanager
def identity_scope(identity: Identity) -> Iterator[None]:
    token = _current_identity.set(identity)
    try:
        yield
    finally:
        _current_identity.reset(token)
