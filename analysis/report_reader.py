"""
Safe report reader — read analysis report text from disk with path sandboxing.

This module exists because the code-execution sandbox (`server/code_executor.py`)
blocks `open()` and friends to prevent prompt-injection-driven file exfiltration.
That blanket block also prevents the Agent from reading its own analysis reports
(e.g. `results/bulk_eda/*.md`), so we expose a *narrow, audited* tool:

  read_report(result_path) → returns the file contents (head + tail, truncated)

Trust boundary:
    - Caller supplies a path (possibly attacker-influenced via prompt injection
      against a tool argument). We MUST validate it before opening.
    - We resolve the path, then verify it lives under one of the ALLOWED_ROOTS
      AFTER symlink resolution. This blocks:
        path traversal (../../etc/passwd)
        absolute escape (/etc/passwd)
        symlink escape (results/foo.md -> /etc/passwd)
    - Extension whitelist further reduces blast radius — we only return text
      reports, not arbitrary files an attacker might have placed under results/.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config.settings import BIO_DB_ROOT, RESULTS_ROOT, DYNAMIC_CODE_DIR

# `RESULTS_ROOT` is currently `results_ana/` (legacy MSseg outputs).
# Live analysis reports land in `results/`. `DYNAMIC_CODE_DIR` archives
# bio_execute_code runs (code.py / output.txt / traceback.txt / meta.json).
ALLOWED_ROOTS: tuple[Path, ...] = (
    (BIO_DB_ROOT / "results").resolve(),
    RESULTS_ROOT.resolve(),
    DYNAMIC_CODE_DIR.resolve(),
)
# `.py` and `.json` enabled to support dynamic-code archive readback.
ALLOWED_SUFFIXES: frozenset[str] = frozenset({".md", ".txt", ".log", ".py", ".json"})

DEFAULT_MAX_CHARS: int = 8_000
DEFAULT_HEAD_FRACTION: float = 0.75  # 75% head, 25% tail when truncating


@dataclass(frozen=True)
class ReportReadResult:
    """Structured return so callers (MCP tool / Agent) can render predictably."""

    path: str
    size_bytes: int
    total_chars: int
    truncated: bool
    head: str
    tail: str
    note: str


class ReportReadError(Exception):
    """Raised for any policy violation or I/O failure. Always safe to surface."""


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_path(raw: str | Path, allowed_roots: Iterable[Path]) -> Path:
    """Resolve `raw` and confirm it lives under one of `allowed_roots`.

    Resolution happens BEFORE the containment check so symlinks pointing outside
    the sandbox are rejected (e.g. `results/foo.md` -> `/etc/passwd`).
    """
    if not raw:
        raise ReportReadError("empty path")

    p = Path(raw)
    if not p.is_absolute():
        p = (BIO_DB_ROOT / p).resolve()
    else:
        p = p.resolve()

    if not any(_is_within(p, root) for root in allowed_roots):
        raise ReportReadError(
            f"path outside allowed roots: {p} (allowed: {[str(r) for r in allowed_roots]})"
        )
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ReportReadError(
            f"extension not allowed: {p.suffix} (allowed: {sorted(ALLOWED_SUFFIXES)})"
        )
    if not p.exists():
        raise ReportReadError(f"file not found: {p}")
    if not p.is_file():
        raise ReportReadError(f"not a regular file: {p}")
    return p


def read_report(
    result_path: str | Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    head_fraction: float = DEFAULT_HEAD_FRACTION,
) -> ReportReadResult:
    """Read an analysis report and return head+tail to keep token cost bounded.

    Args:
        result_path:    Path to the report. May be absolute or BIO_DB_ROOT-relative.
        max_chars:      Hard cap on returned text (head + tail combined).
        head_fraction:  Portion of `max_chars` allocated to the head (rest = tail).

    Raises:
        ReportReadError: For any path-policy violation or I/O failure. Message is
                         safe to show to the user.
    """
    if max_chars <= 0:
        raise ReportReadError(f"max_chars must be positive, got {max_chars}")
    if not 0 < head_fraction <= 1:
        raise ReportReadError(f"head_fraction must be in (0, 1], got {head_fraction}")

    path = _validate_path(result_path, ALLOWED_ROOTS)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReportReadError(f"read failed: {exc}") from exc

    total = len(text)
    size = path.stat().st_size

    if total <= max_chars:
        return ReportReadResult(
            path=str(path),
            size_bytes=size,
            total_chars=total,
            truncated=False,
            head=text,
            tail="",
            note="full text returned (within max_chars)",
        )

    head_n = int(max_chars * head_fraction)
    tail_n = max_chars - head_n
    head = text[:head_n]
    tail = text[-tail_n:] if tail_n > 0 else ""
    return ReportReadResult(
        path=str(path),
        size_bytes=size,
        total_chars=total,
        truncated=True,
        head=head,
        tail=tail,
        note=(
            f"truncated: showing first {head_n} + last {tail_n} chars of {total}. "
            f"Re-call with larger max_chars or grep specific terms for full detail."
        ),
    )
