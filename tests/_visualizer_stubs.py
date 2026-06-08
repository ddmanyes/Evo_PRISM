"""Stub functions for HELIX-Vision tests.

Placed in a non-`test_*` module so pytest's assertion rewriting does not
intercept `inspect.getsource()` / `linecache` lookups. Without this,
`compute_loc()` / `compute_complexity()` (which rely on `inspect.getsource`)
return None for stubs declared inside rewritten test modules.
"""

from __future__ import annotations


def simple_fn() -> int:
    x = 1
    y = 2
    return x + y


def branchy_fn(x: int) -> str:
    if x > 0:
        if x > 10:
            return "big"
        return "small"
    elif x < 0:
        return "negative"
    return "zero"
