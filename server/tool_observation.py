"""Capture tool-boundary attempts including early returns; never alter results."""

import time
import uuid
from server.decision_advisor import observe


def outcome(result):
    if isinstance(result, str):
        values = [result[:512]]
    elif isinstance(result, list):
        values = [getattr(x, "text", "")[:512] for x in result[:32]]
    else:
        values = []
    return (
        "error_or_denied"
        if any(v.lower().startswith(("[error]", "[rbac]")) for v in values)
        else "returned"
    )


def emit(name, args, result, start, channel, call_id, status):
    try:
        observe(
            name,
            args,
            result,
            duration_ms=(time.monotonic() - start) * 1000,
            channel=channel,
            outcome=status,
            call_id=call_id,
        )
    except Exception:
        pass


def invoke(fn, name, args, *, channel):
    start = time.monotonic()
    cid = uuid.uuid4().hex
    emit(name, args, None, start, channel, cid, "started")
    try:
        result = fn(name, args)
    except BaseException as exc:
        emit(name, args, {"error_type": type(exc).__name__}, start, channel, cid, "exception")
        raise
    emit(name, args, result, start, channel, cid, outcome(result))
    return result


async def invoke_async(fn, name, args, *, channel):
    start = time.monotonic()
    cid = uuid.uuid4().hex
    emit(name, args, None, start, channel, cid, "started")
    try:
        result = await fn(name, args)
    except BaseException as exc:
        emit(name, args, {"error_type": type(exc).__name__}, start, channel, cid, "exception")
        raise
    emit(name, args, result, start, channel, cid, outcome(result))
    return result
