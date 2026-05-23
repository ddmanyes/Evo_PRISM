"""Validators for Evo_PRISM analysis modules."""

from __future__ import annotations

import re

_SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_sample_id(sample_id: str) -> None:
    """Validate sample_id format. Raises ValueError if invalid."""
    if not isinstance(sample_id, str) or not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(
            f"Invalid sample_id format: {sample_id!r}. "
            "Must be non-empty and contain only alphanumeric characters, underscores, and hyphens."
        )
