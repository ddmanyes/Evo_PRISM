"""Model-independent, bounded EP decision contracts. Source text is data."""

import copy
import math
import re
from .common import digest, canonical

VERSION = 2
ACTIONS = {
    "RETRIEVE_ARTIFACT",
    "PROPOSE_FROM_L2",
    "INSPECT_HISTORY",
    "QUERY_EVIDENCE",
    "ASK_USER",
    "RETURN_TO_SOL",
}
REQUEST_FIELDS = {
    "schema_version",
    "request_id",
    "task_id",
    "attempt_id",
    "tenant",
    "owner",
    "acl_version",
    "request_ref",
    "user_quote",
    "goal",
    "artifact_kind",
    "task_mode",
    "format",
    "evidence",
    "candidates",
    "allowed_actions",
    "deadline",
    "cost_limit_ms",
}
BUNDLE_FIELDS = {
    "snapshot_id",
    "snapshot_hash",
    "observed_at",
    "valid_until",
    "tenant",
    "owner",
    "acl_version",
    "documents",
    "source_hashes",
    "missing",
    "conflicts",
}
CANDIDATE_FIELDS = {
    "candidate_id",
    "action_id",
    "artifact_kind",
    "task_mode",
    "format",
    "tenant",
    "owner",
    "acl_version",
    "evidence_ids",
    "parameters",
    "parameter_schema",
    "compatible",
    "complete",
}
PROPOSAL_FIELDS = {
    "schema_version",
    "request_id",
    "snapshot_id",
    "action_id",
    "candidate_id",
    "parameters",
    "evidence_ids",
    "missing_evidence",
    "abstain",
    "unsupported",
    "identity",
    "elapsed_ms",
    "usage",
}
IDENTITY_FIELDS = {"model", "revision", "checkpoint", "template", "runtime", "adapter_version"}


def exact(value, fields, reason):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(reason)


def text(value, maximum=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("bounded_text_required")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def identifiers(values, maximum=32):
    if not isinstance(values, list) or len(values) > maximum or len(set(values)) != len(values):
        raise ValueError("invalid_id_list")
    for value in values:
        text(value, 128)


def scope(value, actor):
    if any(value.get(k) != actor[k] for k in ("tenant", "owner", "acl_version")):
        raise PermissionError("current_owner_or_acl_mismatch")


def evidence_bundle(value, actor, now):
    exact(value, BUNDLE_FIELDS, "evidence_schema")
    scope(value, actor)
    text(value["snapshot_id"], 128)
    if (
        not finite(value["observed_at"])
        or not finite(value["valid_until"])
        or not value["observed_at"] <= now < value["valid_until"]
    ):
        raise ValueError("evidence_stale_or_future")
    if not isinstance(value["documents"], list) or len(value["documents"]) > 8:
        raise ValueError("evidence_document_limit")
    ids = []
    for doc in value["documents"]:
        exact(doc, {"evidence_id", "payload", "payload_sha256"}, "evidence_document_schema")
        text(doc["evidence_id"], 128)
        if (
            len(canonical(doc["payload"]).encode()) > 8192
            or digest(doc["payload"]) != doc["payload_sha256"]
        ):
            raise ValueError("evidence_hash_or_size")
        ids.append(doc["evidence_id"])
    identifiers(ids, 8)
    expected = {d["evidence_id"]: d["payload_sha256"] for d in value["documents"]}
    if value["source_hashes"] != expected:
        raise ValueError("source_snapshot_mismatch")
    identifiers(value["missing"])
    identifiers(value["conflicts"])
    if value["snapshot_hash"] != digest({k: v for k, v in value.items() if k != "snapshot_hash"}):
        raise ValueError("snapshot_hash_mismatch")
    return copy.deepcopy(value)


def parameters(value, schema):
    if (
        not isinstance(value, dict)
        or not isinstance(schema, dict)
        or set(value) != set(schema)
        or len(value) > 12
    ):
        raise ValueError("parameter_schema")
    for key, spec in schema.items():
        text(key, 64)
        if re.fullmatch(
            r"password|credential|authorization|secret|api[_-]?key|access[_-]?token", key, re.I
        ):
            raise ValueError("credential_not_a_decision_parameter")
        exact(spec, {"type", "enum", "minimum", "maximum"}, "parameter_spec")
        item = value[key]
        if isinstance(item, str) and re.search(r"(?i)bearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}", item):
            raise ValueError("credential_not_a_decision_parameter")
        kind = spec["type"]
        valid = (
            kind == "string"
            and isinstance(item, str)
            and len(item) <= 256
            or kind == "integer"
            and type(item) is int
            or kind == "number"
            and finite(item)
            or kind == "boolean"
            and type(item) is bool
        )
        if not valid or spec["enum"] is not None and item not in spec["enum"]:
            raise ValueError("parameter_value")
        if kind in ("integer", "number"):
            if (
                spec["minimum"] is None
                or spec["maximum"] is None
                or not finite(spec["minimum"])
                or not finite(spec["maximum"])
            ):
                raise ValueError("numeric_parameter_bounds_required")
            if not spec["minimum"] <= item <= spec["maximum"]:
                raise ValueError("parameter_bounds")
    if len(canonical(value)) > 4096:
        raise ValueError("parameter_size")
    return copy.deepcopy(value)


def decision_request(value, actor, now):
    exact(value, REQUEST_FIELDS, "decision_request_schema")
    scope(value, actor)
    if type(value["schema_version"]) is not int or value["schema_version"] != VERSION:
        raise ValueError("unsupported_schema_version")
    for key in ("request_id", "task_id", "attempt_id", "request_ref", "user_quote", "goal"):
        text(value[key], 4096 if key == "user_quote" else 2048)
    if value["artifact_kind"] not in ("excel", "figure", "text", "code_selection", None) or value[
        "task_mode"
    ] not in ("retrieve", "create", "modify", None):
        raise ValueError("unsupported_goal_contract")
    if value["format"] is not None:
        text(value["format"], 16)
    allowed_formats = {
        "excel": {"xlsx"},
        "figure": {"svg", "png", "pdf"},
        "text": {"md", "txt"},
        "code_selection": {"json"},
    }
    if (
        value["artifact_kind"]
        and value["format"]
        and value["format"] not in allowed_formats[value["artifact_kind"]]
    ):
        raise ValueError("goal_format_contradiction")
    if (
        not finite(value["deadline"])
        or value["deadline"] <= now
        or type(value["cost_limit_ms"]) is not int
        or not 1 <= value["cost_limit_ms"] <= 30000
    ):
        raise ValueError("decision_deadline_or_cost")
    identifiers(value["allowed_actions"], 6)
    if not set(value["allowed_actions"]) <= ACTIONS or not {"ASK_USER", "RETURN_TO_SOL"} <= set(
        value["allowed_actions"]
    ):
        raise ValueError("unsafe_action_allowlist")
    evidence_bundle(value["evidence"], actor, now)
    if not isinstance(value["candidates"], list) or len(value["candidates"]) > 16:
        raise ValueError("candidate_limit")
    identifiers([c["candidate_id"] for c in value["candidates"]], 16)
    for candidate in value["candidates"]:
        exact(candidate, CANDIDATE_FIELDS, "candidate_schema")
        scope(candidate, actor)
        if candidate["action_id"] not in value["allowed_actions"]:
            raise ValueError("candidate_action_not_allowed")
        identifiers(candidate["evidence_ids"], 8)
        if not set(candidate["evidence_ids"]) <= set(value["evidence"]["source_hashes"]):
            raise ValueError("candidate_evidence_missing")
        if (
            candidate["compatible"] not in (True, False, None)
            or type(candidate["complete"]) is not bool
        ):
            raise ValueError("unknown_is_not_false")
        parameters(candidate["parameters"], candidate["parameter_schema"])
    return copy.deepcopy(value)


def identity(value):
    exact(value, IDENTITY_FIELDS, "model_identity_schema")
    for item in value.values():
        text(item, 128)
    return copy.deepcopy(value)


RULES_IDENTITY = dict(
    model="fixed-rules",
    revision="ep-v2",
    checkpoint="none",
    template="decision-v2",
    runtime="python-stdlib",
    adapter_version="rules-v2",
)


def proposal(request, action, candidate=None, *, missing=None, unsupported=False):
    return dict(
        schema_version=VERSION,
        request_id=request["request_id"],
        snapshot_id=request["evidence"]["snapshot_id"],
        action_id=action,
        candidate_id=candidate["candidate_id"] if candidate else None,
        parameters=copy.deepcopy(candidate["parameters"]) if candidate else {},
        evidence_ids=list(candidate["evidence_ids"]) if candidate else [],
        missing_evidence=list(missing or []),
        abstain=candidate is None,
        unsupported=unsupported,
        identity=dict(RULES_IDENTITY),
        elapsed_ms=0,
        usage={"input_tokens": 0, "output_tokens": 0},
    )


def rules(request):
    missing = list(request["evidence"]["missing"])
    for key in ("artifact_kind", "task_mode", "format"):
        if request[key] is None:
            missing.append(key)
    if request["evidence"]["conflicts"]:
        return proposal(request, "RETURN_TO_SOL", missing=request["evidence"]["conflicts"])
    if missing:
        queries = [
            c
            for c in request["candidates"]
            if c["action_id"] == "QUERY_EVIDENCE" and c["compatible"] is True and c["complete"]
        ]
        return (
            proposal(request, "QUERY_EVIDENCE", queries[0], missing=missing)
            if queries
            else proposal(request, "ASK_USER", missing=missing)
        )
    eligible = [
        c
        for c in request["candidates"]
        if c["complete"]
        and c["compatible"] is True
        and all(c[k] == request[k] for k in ("artifact_kind", "task_mode", "format"))
    ]
    # Candidate ordering is EP's frozen retrieval order. No parsing of source instructions.
    return (
        proposal(request, eligible[0]["action_id"], eligible[0])
        if eligible
        else proposal(request, "RETURN_TO_SOL", unsupported=True)
    )


def guard(request, raw, expected_identity, actor, now, current_sources):
    try:
        decision_request(request, actor, now)
        exact(raw, PROPOSAL_FIELDS, "proposal_schema")
        if (
            type(raw["schema_version"]) is not int
            or raw["schema_version"] != VERSION
            or raw["request_id"] != request["request_id"]
            or raw["snapshot_id"] != request["evidence"]["snapshot_id"]
        ):
            raise ValueError("proposal_request_binding")
        if identity(raw["identity"]) != identity(expected_identity):
            raise ValueError("pinned_model_identity_mismatch")
        if raw["action_id"] not in request["allowed_actions"]:
            raise ValueError("proposal_action_not_allowed")
        if not finite(raw["elapsed_ms"]) or not 0 <= raw["elapsed_ms"] <= request["cost_limit_ms"]:
            raise ValueError("proposal_cost_limit")
        exact(raw["usage"], {"input_tokens", "output_tokens"}, "usage_schema")
        if any(v is not None and (type(v) is not int or v < 0) for v in raw["usage"].values()):
            raise ValueError("usage_invalid")
        if type(raw["abstain"]) is not bool or type(raw["unsupported"]) is not bool:
            raise ValueError("abstain_schema")
        identifiers(raw["evidence_ids"], 8)
        identifiers(raw["missing_evidence"])
        required_gaps = set(request["evidence"]["missing"]) | set(request["evidence"]["conflicts"])
        required_gaps.update(
            k for k in ("artifact_kind", "task_mode", "format") if request[k] is None
        )
        if not required_gaps <= set(raw["missing_evidence"]):
            raise ValueError("known_missing_evidence_omitted")
        if not set(raw["evidence_ids"]) <= set(request["evidence"]["source_hashes"]):
            raise ValueError("invented_evidence")
        if any(
            current_sources.get(k) != v for k, v in request["evidence"]["source_hashes"].items()
        ):
            raise ValueError("source_changed_before_action")
        candidate = next(
            (c for c in request["candidates"] if c["candidate_id"] == raw["candidate_id"]), None
        )
        if candidate is None:
            if (
                raw["candidate_id"] is not None
                or raw["action_id"] not in {"ASK_USER", "RETURN_TO_SOL"}
                or raw["parameters"] != {}
                or not raw["abstain"]
            ):
                raise ValueError("unknown_candidate_or_abstain")
        else:
            if (
                raw["abstain"]
                or candidate["compatible"] is not True
                or not candidate["complete"]
                or candidate["action_id"] != raw["action_id"]
            ):
                raise ValueError("candidate_eligibility")
            if raw["action_id"] != "QUERY_EVIDENCE" and any(
                candidate[k] != request[k] for k in ("artifact_kind", "task_mode", "format")
            ):
                raise ValueError("goal_candidate_mismatch")
            if not set(candidate["evidence_ids"]) <= set(raw["evidence_ids"]):
                raise ValueError("required_citations_missing")
            parameters(raw["parameters"], candidate["parameter_schema"])
            # P1 permits selection of EP's frozen parameters, not model edits to validated inputs.
            if raw["parameters"] != candidate["parameters"]:
                raise ValueError("parameter_drift_requires_new_evidence")
            if (
                request["evidence"]["conflicts"]
                or required_gaps
                and raw["action_id"] != "QUERY_EVIDENCE"
            ):
                raise ValueError("evidence_gap_before_action")
        return {
            "pass": True,
            "reason": None,
            "science_verified": False,
            "execution_authorized": False,
        }
    except (ValueError, PermissionError, TypeError, KeyError) as exc:
        return {
            "pass": False,
            "reason": str(exc),
            "science_verified": False,
            "execution_authorized": False,
        }
