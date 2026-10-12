"""Live metadata advisor. Authentication and scientific execution stay with EP.

Only hashes and bounded technical receipts enter the separate operational ledger.
Clef shadow may score metadata on the authenticated local host; rules remain primary.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import uuid

from server.auth import current_identity, visible_requested_by
from server.identity import Role
from server.ep_decision_core.common import canonical, digest
from server.ep_decision_core.decision_contracts import (
    ACTIONS, RULES_IDENTITY, decision_request, guard, identity, rules, text,
)
from server.ep_decision_core.selection import ModelSelection

VERSION = "ep-advisor-1.1.0-clef-shadow"
DEFAULT_SELECTION = {'epoch': 'clef-live-registry-20261012-v1', 'identity': {'adapter_version': 'clef-choice-v1', 'checkpoint': 'a579a5fcacbdc7186e500ca3311920ef48734f965dd8de5c92a65f86a0d0201a', 'model': 'Clef', 'revision': 'd9ec324f7992383bdfb7a0b4eed8b4b9d10f81be', 'runtime': 'mlx-0.32.3-vlm-0.7.4', 'template': '74984eee94c2c436aa970201506a88bbf267a5eb13dfb447d574e9b10251eef5'}}
MAX_EVENTS = 10000


@contextmanager
def _connection():
    # Production overrides this to the existing Linux named lock volume.
    from config.settings import EP_DECISION_STATE_PATH
    path = Path(os.environ.get("EP_DECISION_STATE_PATH", str(EP_DECISION_STATE_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=1.0)
    os.chmod(path, 0o600)
    try:
        con.execute("PRAGMA max_page_count=4096")
        con.execute("CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY,v TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS epochs (id TEXT PRIMARY KEY,identity TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS tasks (owner TEXT,task TEXT,pin TEXT,PRIMARY KEY(owner,task))")
        con.execute("CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY,owner TEXT,receipt TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS feedback (decision TEXT PRIMARY KEY,owner TEXT,payload TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS events (n INTEGER PRIMARY KEY,owner TEXT,kind TEXT,payload TEXT NOT NULL)")
        selection = ModelSelection(DEFAULT_SELECTION["epoch"], DEFAULT_SELECTION["identity"]).validate()
        default = {"mode": "off", "revision": 0, "selection": selection, "stop_reason": None}
        con.execute("INSERT OR IGNORE INTO state VALUES ('config',?)", (canonical(default),))
        con.execute("INSERT OR IGNORE INTO epochs VALUES (?,?)", (selection["epoch"], canonical(selection["identity"])))
        con.commit()
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _config(con):
    return json.loads(con.execute("SELECT v FROM state WHERE k='config'").fetchone()[0])


def _save(con, config):
    con.execute("UPDATE state SET v=? WHERE k='config'", (canonical(config),))


def _event(con, owner, kind, payload):
    if con.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= MAX_EVENTS:
        raise OverflowError("operational_ledger_capacity")
    wire = canonical({"at": time.time(), **payload})
    if len(wire.encode()) > 4096:
        raise ValueError("operational_event_size")
    con.execute("INSERT INTO events(owner,kind,payload) VALUES (?,?,?)", (owner, kind, wire))


def _stop(reason):
    with _connection() as con:
        con.execute("BEGIN IMMEDIATE")
        cfg = _config(con)
        if cfg["mode"] != "off" or cfg["stop_reason"] is None:
            cfg.update(mode="off", revision=cfg["revision"] + 1, stop_reason=reason)
            _save(con, cfg)


def _args(args, allowed):
    # requested_by is supplied by trusted_tool_arguments; never used for ownership.
    if not isinstance(args, dict) or set(args) - set(allowed) - {"requested_by"}:
        raise ValueError("unsupported_argument")
    if len(canonical(args).encode()) > 16384:
        raise ValueError("request_size")


def _shadow_health(cfg):
    if cfg['mode'] != 'shadow':
        return {'ready': False, 'status': 'NOT_SELECTED'}
    try:
        from server.clef_shadow import health
        return health(cfg['selection'])
    except Exception as exc:
        return {'ready': False, 'status': 'UNAVAILABLE_RULES_FALLBACK', 'error_type': type(exc).__name__}


def status(args):
    _args(args, set())
    actor = current_identity()
    with _connection() as con:
        cfg = _config(con)
        counts = dict(con.execute("SELECT kind,COUNT(*) FROM events WHERE owner=? GROUP BY kind", (actor.user_id,)))
    runtime = _shadow_health(cfg)
    return canonical({"version": VERSION, **cfg, "actor": actor.user_id,
                      "native_model_enabled": cfg["mode"] == "shadow" and runtime.get("ready", False), "native_adapter_loaded": runtime.get("ready", False),
                      "shadow_runtime": runtime,
                      "logging_scope": "MCP/Web tool attempts and bounded outcome hashes; not full conversations or worker internals",
                      "execution_authorized": False, "own_events": counts,
                      "quality_status": "INDEPENDENT_MODEL_QUALITY_PENDING",
                      "training_permission": False})


def assess(args):
    _args(args, {"sample_id", "analysis_type", "goal", "artifact_kind", "task_mode", "output_format", "task_id"})
    for key in ("sample_id", "analysis_type"):
        text(args.get(key), 128)
    goal = args.get("goal", "Inspect existing analysis evidence before reuse or new work")
    text(goal, 2048)
    actor = current_identity()
    request_id = uuid.uuid4().hex
    task_id = args.get("task_id", request_id)
    text(task_id, 128)
    started = time.monotonic()
    with _connection() as con:
        cfg = _config(con)
    if cfg["mode"] == "off":
        return canonical({"status": "OFF", "selection": cfg["selection"], "execution_authorized": False})
    from store.factory import get_store
    # The established high-level API applies the current authenticated owner filter.
    rec = get_store().get_history(args["sample_id"], args["analysis_type"], requested_by=visible_requested_by(actor))
    payload = {"sample_id": args["sample_id"], "analysis_type": args["analysis_type"], "exists": rec is not None}
    if rec:
        # Intentionally exclude summaries, scientific values, paths and arbitrary parameters.
        payload.update({k: str(rec.get(k))[:256] if rec.get(k) is not None else None
                        for k in ("analysis_id", "status", "completed_at", "env_hash", "parent_analysis_id")})
        payload["status"] = "completed"  # get_history's default completed-only query.
    now = time.time()
    scope = {"tenant": "evo_prism", "owner": actor.user_id, "acl_version": "ep-rbac-v1:" + actor.role.value}
    evidence = {"snapshot_id": request_id, "observed_at": now, "valid_until": now + 60,
                **scope, "documents": [{"evidence_id": "history", "payload": payload, "payload_sha256": digest(payload)}],
                "source_hashes": {"history": digest(payload)}, "missing": [], "conflicts": []}
    evidence["snapshot_hash"] = digest(evidence)
    kind = args.get("artifact_kind", "text")
    task_mode = args.get("task_mode", "retrieve")
    output_format = args.get("output_format", "md")
    params = {"sample_id": args["sample_id"], "analysis_type": args["analysis_type"]}
    candidate = {"candidate_id": "inspect-owned-history", "action_id": "INSPECT_HISTORY",
                 "artifact_kind": kind, "task_mode": task_mode, "format": output_format,
                 **scope, "evidence_ids": ["history"], "parameters": params,
                 "parameter_schema": {k: {"type": "string", "enum": [v], "minimum": None, "maximum": None}
                                      for k, v in params.items()}, "compatible": True, "complete": True}
    request = {"schema_version": 2, "request_id": request_id, "task_id": task_id, "attempt_id": request_id,
               **scope, "request_ref": "ep:live-metadata:" + request_id, "user_quote": goal, "goal": goal,
               "artifact_kind": kind, "task_mode": task_mode, "format": output_format,
               "evidence": evidence, "candidates": [candidate], "allowed_actions": sorted(ACTIONS),
               "deadline": now + 60, "cost_limit_ms": 30000 if cfg["mode"] == "shadow" else 5000}
    validated = decision_request(request, scope, now)
    proposal = rules(validated)
    checked = guard(validated, proposal, RULES_IDENTITY, scope, time.time(), evidence["source_hashes"])
    if not checked["pass"] or (time.monotonic() - started) > 5:
        _stop("decision_guard_or_wall")
        raise ValueError("decision_guard_or_wall")
    receipt = {"decision_id": request_id, "task_hash": digest(task_id), "selection": cfg["selection"],
               "actual_backend": "fixed-rules", "action_id": proposal["action_id"], "guard": checked,
               "request_hash": digest(validated), "source_hashes": evidence["source_hashes"],
               "origin": "AUTHORIZED_LIVE_METADATA", "training_permission": False, "gold": False,
               "wall_ms": (time.monotonic() - started) * 1000}
    shadow = None
    if cfg['mode'] == 'shadow':
        # Reserve before dispatch so crashes/transport failures remain visible.
        with _connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if _config(con) != cfg:
                raise ValueError('configuration_changed_before_dispatch')
            _event(con, actor.user_id, 'SHADOW_REQUEST', {'decision_id':request_id, 'request_hash':digest(validated), 'selection_hash':digest(cfg['selection'])})
        def fresh_state(stage, req):
            with _connection() as con:
                if _config(con) != cfg:
                    raise ValueError('configuration_changed_during_shadow')
            fresh = get_store().get_history(args['sample_id'], args['analysis_type'], requested_by=visible_requested_by(actor))
            current = {'sample_id': args['sample_id'], 'analysis_type':args['analysis_type'], 'exists':fresh is not None}
            if fresh:
                current.update({k: str(fresh.get(k))[:256] if fresh.get(k) is not None else None
                                for k in ('analysis_id','status','completed_at','env_hash','parent_analysis_id')})
                current['status']='completed'
            return {'actor':scope, 'now':time.time(), 'sources':{'history':digest(current)}}
        try:
            from server.clef_shadow import evaluate
            shadow = evaluate(validated, cfg['selection'], fresh_state)
        except Exception as exc:
            shadow = {'status':'FALLBACK_RULES', 'error_type':type(exc).__name__, 'model_called':None, 'quality_eligible':False, 'execution_authorized':False}
        with _connection() as con:
            _event(con, actor.user_id, 'SHADOW_RESULT', {'decision_id':request_id, **shadow})
        after = fresh_state('final_commit', validated)
        checked_after = guard(validated, proposal, RULES_IDENTITY, after['actor'], after['now'], after['sources'])
        if not checked_after['pass']:
            raise ValueError('source_changed_during_shadow')
        receipt['shadow'] = shadow
        receipt['wall_ms'] = (time.monotonic()-started)*1000
    try:
        with _connection() as con:
            con.execute("BEGIN IMMEDIATE")
            latest = _config(con)
            if latest != cfg or latest["mode"] not in ("rules", "shadow"):
                raise ValueError("configuration_changed_before_commit")
            pin = digest({"selection": cfg["selection"], "mode": cfg["mode"], "scope": scope})
            previous = con.execute("SELECT pin FROM tasks WHERE owner=? AND task=?", (actor.user_id, digest(task_id))).fetchone()
            if previous and previous[0] != pin:
                raise ValueError("new_selection_requires_new_task")
            con.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?)", (actor.user_id, digest(task_id), pin))
            con.execute("INSERT INTO decisions VALUES (?,?,?)", (request_id, actor.user_id, canonical(receipt)))
            _event(con, actor.user_id, "DECISION", receipt)
    except (OverflowError, sqlite3.DatabaseError):
        _stop("operational_ledger_capacity_or_io")
        raise
    return canonical({**receipt, "metadata": payload, "proposal": proposal,
                      "next_tool": {"name": "bio_history_check", "arguments": params},
                      "reuse_verified": False,
                      "remaining_checks": ["producer/version/parent and artifact availability", "task-specific acceptance"],
                      "native_model_enabled": cfg["mode"] == "shadow", "shadow": shadow})


def feedback(args):
    _args(args, {"decision_id", "adoption", "acceptance", "receipt_ref"})
    actor = current_identity()
    text(args.get("decision_id"), 128)
    text(args.get("receipt_ref"), 512)
    if args.get("adoption") not in ("ADOPTED", "REJECTED", "MODIFIED", "NEEDS_EVIDENCE"):
        raise ValueError("adoption_status")
    if args.get("acceptance") is not None and type(args["acceptance"]) is not bool:
        raise ValueError("acceptance_boolean_or_unknown")
    value = {"adoption": args["adoption"], "acceptance": args.get("acceptance"),
             "receipt_ref_hash": digest(args["receipt_ref"]), "label_source": "USER_EXPLICIT",
             "training_permission": False, "gold": False}
    with _connection() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT receipt FROM decisions WHERE id=? AND owner=?", (args["decision_id"], actor.user_id)).fetchone()
        if not row:
            raise ValueError("decision_not_visible")
        previous = con.execute("SELECT payload FROM feedback WHERE decision=? AND owner=?", (args["decision_id"], actor.user_id)).fetchone()
        if previous and json.loads(previous[0]) != value:
            raise ValueError("feedback_immutable_conflict")
        if not previous:
            con.execute("INSERT INTO feedback VALUES (?,?,?)", (args["decision_id"], actor.user_id, canonical(value)))
            _event(con, actor.user_id, "OUTCOME", {"decision_id": args["decision_id"], **value})
    return canonical({"decision_id": args["decision_id"], **value})


def control(args):
    _args(args, {"action", "expected_revision", "selection"})
    actor = current_identity()
    if actor.role is not Role.ADMIN:
        raise PermissionError("admin_required")
    action = args.get("action")
    if action not in ("off", "rules", "shadow", "select") or type(args.get("expected_revision")) is not int:
        raise ValueError("control_schema")
    with _connection() as con:
        con.execute("BEGIN IMMEDIATE")
        cfg = _config(con)
        if cfg["revision"] != args["expected_revision"]:
            raise ValueError("stale_control_revision")
        if action == "select":
            selection = args.get("selection")
            if not isinstance(selection, dict) or set(selection) != {"epoch", "identity"}:
                raise ValueError("selection_schema")
            text(selection["epoch"], 128)
            identity(selection["identity"])
            prior = con.execute("SELECT identity FROM epochs WHERE id=?", (selection["epoch"],)).fetchone()
            if prior and json.loads(prior[0]) != selection["identity"]:
                raise ValueError("epoch_identity_immutable")
            con.execute("INSERT OR IGNORE INTO epochs VALUES (?,?)", (selection["epoch"], canonical(selection["identity"])))
            if cfg["mode"] == "shadow":
                raise ValueError("disable_shadow_before_select")
            cfg["selection"] = selection
        else:
            if action in ("rules", "shadow") and cfg["stop_reason"]:
                raise ValueError("latched_stop_requires_operator_repair")
            if action == "shadow":
                from server.clef_shadow import health
                health(cfg["selection"])
            cfg["mode"] = action
        cfg["revision"] += 1
        _save(con, cfg)
        try:
            _event(con, actor.user_id, "CONTROL", {"action": action, "revision": cfg["revision"], "selection_hash": digest(cfg["selection"])})
        except OverflowError:
            if action != "off":
                raise
            cfg["stop_reason"] = "operational_ledger_capacity"
            _save(con, cfg)  # OFF must commit even when the append ledger is full.
    return status({})


def _fingerprint(value):
    """Bound work even for large image blocks; describe incomplete hash coverage."""
    if isinstance(value, str):
        return digest(value[:16384]), "FULL_TEXT" if len(value) <= 16384 else "TEXT_PREFIX_16384_CHARS"
    if isinstance(value, (list, tuple)):
        # MCP returns ContentBlocks: hash bounded text, never image/blob bodies.
        remaining = 16384
        blocks = []
        has_text = False
        for block in value[:32]:
            item = {"type": type(block).__name__}
            content = getattr(block, "text", None)
            if isinstance(content, str):
                has_text = True
                item["text_prefix"] = content[:remaining]
                item["text_length"] = len(content)
                remaining -= len(item["text_prefix"])
            blocks.append(item)
        if has_text:
            return digest({"blocks": blocks, "block_count": len(value)}), "MCP_TEXT_PREFIX_16384_CHARS_AND_BLOCK_SHAPES"
    if isinstance(value, dict):
        bounded = {}
        for key, item in list(value.items())[:32]:
            bounded[str(key)[:64]] = (item[:2048] if isinstance(item, str)
                                      else item if item is None or type(item) in (int, bool)
                                      else {"type": type(item).__name__})
        return digest(bounded), "BOUNDED_ARGUMENT_FIELDS"
    return digest({"type": type(value).__name__, "length": len(value) if isinstance(value, (list, tuple, bytes)) else None}), "TYPE_SHAPE_ONLY"


def observe(tool_name, arguments, result, *, duration_ms, channel, outcome="success", call_id=None):
    """Best-effort observation cannot change existing tool results or authority."""
    try:
        argument_hash, argument_scope = _fingerprint(arguments)
        result_hash, result_scope = _fingerprint(result)
        with _connection() as con:
            con.execute("BEGIN IMMEDIATE")
            cfg = _config(con)
            _event(con, current_identity().user_id, "TOOL", {
                "tool": str(tool_name)[:128], "channel": channel, "mode": cfg["mode"], "outcome": outcome, "call_id": call_id,
                "arguments_hash": argument_hash, "arguments_hash_scope": argument_scope,
                "result_hash": result_hash, "result_hash_scope": result_scope,
                "duration_ms": max(0, duration_ms), "selection_hash": digest(cfg["selection"]),
                "origin": "AUTHORIZED_LIVE_OPERATION", "gold": False, "training_permission": False})
    except Exception:
        try:
            _stop("observer_io_or_capacity")
        except Exception:
            # Existing EP calls must remain available even when the isolated ledger fails.
            pass
