"""Clef q0 choice bridge. Pure supervisor code; importing never loads a model."""

import copy
import hashlib
import math
from .common import canonical, digest
from .decision_contracts import (
    PROPOSAL_FIELDS,
    VERSION,
    decision_request,
    exact,
    finite,
    guard,
    identity,
    rules,
    RULES_IDENTITY,
)
from .model_adapter import ModelAdapter

RAW_FIELDS = {
    "raw_logits",
    "raw_probabilities",
    "encoded_option_ids",
    "input_tokens",
    "encoded_sha256",
    "api",
    "peak_mlx_bytes",
    "composition",
}
MEASUREMENT_FIELDS = {"elapsed_ms", "measurement_ref"}
FALLBACK_OPTIONS = {"ask_user": "ASK_USER", "return_to_sol": "RETURN_TO_SOL"}


def _known_gaps(request):
    gaps = list(request["evidence"]["missing"]) + list(request["evidence"]["conflicts"])
    gaps += [key for key in ("artifact_kind", "task_mode", "format") if request[key] is None]
    return list(dict.fromkeys(gaps))


def _option_map(request):
    result = {}
    for i, candidate in enumerate(request["candidates"]):
        result[f"c{i:02d}"] = {"kind": "candidate", "candidate_id": candidate["candidate_id"]}
    result.update(
        {key: {"kind": "fallback", "action_id": action} for key, action in FALLBACK_OPTIONS.items()}
    )
    return result


def _record(request, option_map):
    criteria = {}
    for option_id, target in option_map.items():
        if target["kind"] == "candidate":
            candidate = next(
                c for c in request["candidates"] if c["candidate_id"] == target["candidate_id"]
            )
            criteria[option_id] = {
                "action_id": candidate["action_id"],
                "candidate_id": candidate["candidate_id"],
                "artifact_kind": candidate["artifact_kind"],
                "task_mode": candidate["task_mode"],
                "format": candidate["format"],
                "compatible": candidate["compatible"],
                "complete": candidate["complete"],
                "evidence_ids": candidate["evidence_ids"],
            }
        else:
            criteria[option_id] = {"action_id": target["action_id"], "supervisor_fallback": True}
    state = {
        "schema": "ep-clef-choice-v1",
        "request_id": request["request_id"],
        "request_ref": request["request_ref"],
        "user_quote": request["user_quote"],
        "goal": request["goal"],
        "artifact_kind": request["artifact_kind"],
        "task_mode": request["task_mode"],
        "format": request["format"],
        "evidence": request["evidence"],
        "known_gaps": _known_gaps(request),
        "allowed_actions": request["allowed_actions"],
    }
    return {
        "state": state,
        "questions": {
            "q0": {
                "type": "choice",
                "instructions": "Choose one EP candidate or an explicit safe fallback. Return only a choice.",
                "criteria": criteria,
            }
        },
    }


def _native_score(raw, expected_ids):
    exact(raw, RAW_FIELDS, "clef_native_wire_schema")
    ids = raw["encoded_option_ids"]
    if (
        not isinstance(ids, list)
        or any(not isinstance(x, str) for x in ids)
        or len(ids) != len(expected_ids)
        or len(set(ids)) != len(ids)
        or ids != sorted(expected_ids)
    ):
        raise ValueError("encoded_option_mapping")
    logits, probs = raw["raw_logits"], raw["raw_probabilities"]
    if (
        not isinstance(logits, list)
        or not isinstance(probs, list)
        or len(logits) != len(ids)
        or len(probs) != len(ids)
        or any(not finite(x) for x in logits + probs)
        or any(x < 0 or x > 1 for x in probs)
        or abs(sum(probs) - 1) > 1e-3
    ):
        raise ValueError("native_score_invalid")
    peak = max(logits)
    exps = [math.exp(x - peak) for x in logits]
    total = sum(exps)
    if any(abs(p - e / total) > 1e-3 for p, e in zip(probs, exps)):
        raise ValueError("native_score_logit_probability_mismatch")
    ranked = sorted(zip(ids, probs), key=lambda item: item[1], reverse=True)
    if len(ranked) < 2 or ranked[0][1] - ranked[1][1] <= 1e-4:
        raise ValueError("native_choice_tie_or_ambiguous")
    choice = ranked[0][0]
    if (
        type(raw["input_tokens"]) is not int
        or not 0 < raw["input_tokens"] <= 16384
        or not isinstance(raw["encoded_sha256"], str)
        or len(raw["encoded_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in raw["encoded_sha256"])
        or type(raw["peak_mlx_bytes"]) is not int
        or raw["peak_mlx_bytes"] < 0
        or not isinstance(raw["composition"], dict)
    ):
        raise ValueError("native_usage_or_binding_invalid")
    api = raw["api"]
    exact(api, {"type", "choice", "confidence", "probabilities"}, "clef_api_schema")
    rounded = {name: round(prob, 4) for name, prob in zip(ids, probs)}
    if (
        api["type"] != "choice"
        or api["choice"] != choice
        or api["confidence"] != rounded[choice]
        or api["probabilities"] != rounded
    ):
        raise ValueError("clef_api_score_disagreement")
    return choice


class ClefChoiceAdapter(ModelAdapter):
    # Existing DecisionEngine permits only engineering_only adapters in non-off
    # modes. This bridge cannot silently enable production inference there.
    engineering_only = False

    def __init__(
        self,
        predictor,
        pinned_identity,
        *,
        families=("excel", "figure", "text", "code_selection"),
        formats=("xlsx", "svg", "png", "pdf", "md", "txt", "json"),
    ):
        if not callable(predictor):
            raise TypeError("predictor_required")
        self.predictor = predictor
        self.pinned_identity = identity(pinned_identity)
        self.families, self.formats = tuple(families), tuple(formats)
        self._pending_hash = None
        self.last_envelope = None

    def declaration(self):
        return {
            "identity": copy.deepcopy(self.pinned_identity),
            "families": self.families,
            "formats": self.formats,
            "native_format": "clef_mlx.q0.choice.scoring",
            "conformance_receipt": "LOCAL_SOURCE_TEXT_ONLY; RUNTIME_UNEXECUTED",
        }

    def render(self, request):
        req = copy.deepcopy(request)
        if not isinstance(req, dict) or len(canonical(req).encode()) > 65536:
            raise ValueError("request_size_limit")
        options = _option_map(req)
        record = _record(req, options)
        if len(canonical(record).encode()) > 131072:
            raise ValueError("clef_record_size_limit")
        bundle = {
            "request": req,
            "request_hash": digest(req),
            "record": record,
            "record_hash": digest(record),
            "option_map": options,
        }
        self._pending_hash = digest(bundle)
        return bundle

    def propose(self, native_input):
        if digest(native_input) != self._pending_hash:
            raise ValueError("rendered_request_changed")
        prediction = self.predictor(copy.deepcopy(native_input["record"]))
        if not isinstance(prediction, dict) or set(prediction) != {
            "raw",
            "measurement",
            "model_called",
            "operation_id",
        }:
            raise ValueError("predictor_receipt_schema")
        return {"binding": copy.deepcopy(native_input), "prediction": prediction}

    def parse_envelope(self, native_output):
        exact(native_output, {"binding", "prediction"}, "clef_bridge_output_schema")
        bundle, prediction = native_output["binding"], native_output["prediction"]
        if (
            digest(bundle) != self._pending_hash
            or digest(bundle["request"]) != bundle["request_hash"]
            or digest(bundle["record"]) != bundle["record_hash"]
            or bundle["record"] != _record(bundle["request"], bundle["option_map"])
            or bundle["option_map"] != _option_map(bundle["request"])
        ):
            raise ValueError("clef_request_binding_changed")
        exact(
            prediction,
            {"raw", "measurement", "model_called", "operation_id"},
            "predictor_receipt_schema",
        )
        if (
            type(prediction["model_called"]) is not bool
            or not isinstance(prediction["operation_id"], str)
            or not prediction["operation_id"]
        ):
            raise ValueError("predictor_operation_binding")
        if len(canonical(prediction["raw"]).encode()) > 1048576:
            raise ValueError("native_wire_size_limit")
        if prediction["model_called"]:
            comp = (
                prediction["raw"].get("composition")
                if isinstance(prediction["raw"], dict)
                else None
            )
            if (
                self.pinned_identity["checkpoint"] in ("UNRESOLVED", "NOT_LOADED")
                or self.pinned_identity["runtime"] == "NOT_LOADED"
                or not isinstance(comp, dict)
                or comp.get("fixed_tensor_equal") is not True
                or comp.get("trainable_tensors") != 0
                or comp.get("checkpoint_sha256") != self.pinned_identity["checkpoint"]
            ):
                raise ValueError("live_runtime_composition_unbound")
        measurement = prediction["measurement"]
        exact(measurement, MEASUREMENT_FIELDS, "measurement_schema")
        if (
            not finite(measurement["elapsed_ms"])
            or measurement["elapsed_ms"] < 0
            or not isinstance(measurement["measurement_ref"], str)
            or not measurement["measurement_ref"]
        ):
            raise ValueError("measurement_invalid")
        req = bundle["request"]
        if measurement["elapsed_ms"] > req["cost_limit_ms"]:
            raise ValueError("measured_cost_limit")
        option_id = _native_score(prediction["raw"], list(bundle["option_map"]))
        target = bundle["option_map"].get(option_id)
        if target is None:
            raise ValueError("unknown_choice_option")
        if target["kind"] == "candidate":
            candidate = next(
                c for c in req["candidates"] if c["candidate_id"] == target["candidate_id"]
            )
            action, candidate_id = candidate["action_id"], candidate["candidate_id"]
            parameters, evidence_ids = (
                copy.deepcopy(candidate["parameters"]),
                list(candidate["evidence_ids"]),
            )
            abstain = False
        else:
            action, candidate_id = target["action_id"], None
            parameters, evidence_ids, abstain = {}, [], True
        parsed = {
            "schema_version": VERSION,
            "request_id": req["request_id"],
            "snapshot_id": req["evidence"]["snapshot_id"],
            "action_id": action,
            "candidate_id": candidate_id,
            "parameters": parameters,
            "evidence_ids": evidence_ids,
            "missing_evidence": _known_gaps(req),
            "abstain": abstain,
            "unsupported": False,
            "identity": copy.deepcopy(self.pinned_identity),
            "elapsed_ms": measurement["elapsed_ms"],
            "usage": {"input_tokens": prediction["raw"]["input_tokens"], "output_tokens": 0},
        }
        exact(parsed, PROPOSAL_FIELDS, "proposal_schema")
        native_hash = hashlib.sha256(canonical(prediction["raw"]).encode()).hexdigest()
        from_candidate = ("action_id", "candidate_id", "parameters", "evidence_ids")
        provenance = {
            key: {
                "origin": "supervisor_candidate",
                "request_sha256": bundle["request_hash"],
                "selected_option": option_id,
                "native_sha256": native_hash,
            }
            for key in from_candidate
        }
        provenance.update(
            {
                key: {"origin": "supervisor_request", "request_sha256": bundle["request_hash"]}
                for key in (
                    "schema_version",
                    "request_id",
                    "snapshot_id",
                    "missing_evidence",
                    "abstain",
                    "unsupported",
                )
            }
        )
        provenance["action_id"] = (
            {
                "origin": "supervisor_fallback",
                "request_sha256": bundle["request_hash"],
                "selected_option": option_id,
                "native_sha256": native_hash,
            }
            if abstain
            else provenance["action_id"]
        )
        provenance["candidate_id"] = (
            {
                "origin": "supervisor_fallback",
                "request_sha256": bundle["request_hash"],
                "selected_option": option_id,
                "native_sha256": native_hash,
            }
            if abstain
            else provenance["candidate_id"]
        )
        provenance["parameters"] = (
            {
                "origin": "supervisor_fallback",
                "request_sha256": bundle["request_hash"],
                "selected_option": option_id,
                "native_sha256": native_hash,
            }
            if abstain
            else provenance["parameters"]
        )
        provenance["evidence_ids"] = (
            {
                "origin": "supervisor_fallback",
                "request_sha256": bundle["request_hash"],
                "selected_option": option_id,
                "native_sha256": native_hash,
            }
            if abstain
            else provenance["evidence_ids"]
        )
        provenance["abstain"] = {
            "origin": "supervisor_choice_mapping",
            "selected_option": option_id,
            "native_sha256": native_hash,
        }
        provenance["identity"] = {
            "origin": "pinned_runtime_identity",
            "identity_sha256": digest(self.pinned_identity),
        }
        provenance["elapsed_ms"] = {
            "origin": "measurement",
            "measurement_ref": measurement["measurement_ref"],
        }
        provenance["usage"] = {
            "origin": "native_input_tokens_and_fixed_zero_output",
            "native_sha256": native_hash,
        }
        envelope = {
            "raw_native_wire": copy.deepcopy(prediction["raw"]),
            "native_sha256": native_hash,
            "native_option_id": option_id,
            "encoded_option_ids": list(prediction["raw"]["encoded_option_ids"]),
            "normalized": parsed,
            "provenance": provenance,
            "request_sha256": bundle["request_hash"],
            "record_sha256": bundle["record_hash"],
            "model_called": prediction["model_called"],
            "operation_id": prediction["operation_id"],
            "model_supplied_full_proposal": False,
            "execution_authorized": False,
        }
        self.last_envelope = copy.deepcopy(envelope)
        return envelope

    def parse(self, native_output):
        return self.parse_envelope(native_output)["normalized"]


def assess_choice(adapter, request, *, actor, now, current_sources=None, current_state=None):
    """One predictor call after a fresh rules guard; callback checks post state.

    current_state(stage, request) may return {actor, now, sources}. Alternatively
    current_sources may be a mapping or callable(stage, request). A frozen mapping
    is only an offline fixture: it cannot detect source changes during inference.
    """
    validated = decision_request(request, actor, now)
    fallback = rules(validated)

    def observe(stage):
        if current_state is not None:
            if not callable(current_state):
                raise TypeError("current_state_callback_required")
            state = current_state(stage, copy.deepcopy(validated))
            exact(state, {"actor", "now", "sources"}, "current_state_schema")
            if state["actor"] != actor:
                raise PermissionError("trusted_actor_changed")
            return state
        sources = (
            current_sources(stage, copy.deepcopy(validated))
            if callable(current_sources)
            else current_sources
        )
        if not isinstance(sources, dict):
            raise ValueError("current_sources_required")
        return {"actor": actor, "now": now, "sources": sources}

    def guarded_fallback(state, reason, envelope=None, rejected=None, pre_guard=None):
        checked = guard(
            validated, fallback, RULES_IDENTITY, state["actor"], state["now"], state["sources"]
        )
        if not checked["pass"]:
            return {
                "primary": None,
                "recommended_action": "RETURN_TO_SOL",
                "fallback": True,
                "reason": reason or checked["reason"],
                "envelope": envelope,
                "rejected_native_output": rejected,
                "pre_guard": pre_guard,
                "guard": checked,
                "action_ready": False,
                "execution_authorized": False,
            }
        return {
            "primary": fallback,
            "recommended_action": fallback["action_id"],
            "fallback": True,
            "reason": reason,
            "envelope": envelope,
            "rejected_native_output": rejected,
            "pre_guard": pre_guard,
            "guard": checked,
            "action_ready": checked["pass"],
            "execution_authorized": False,
        }

    try:
        before = observe("pre_predict")
        pre_guard = guard(
            validated, fallback, RULES_IDENTITY, before["actor"], before["now"], before["sources"]
        )
        if not pre_guard["pass"]:
            return guarded_fallback(before, pre_guard["reason"], pre_guard=pre_guard)
    except Exception as exc:
        return {
            "primary": None,
            "recommended_action": "RETURN_TO_SOL",
            "fallback": True,
            "reason": "PREFLIGHT_" + type(exc).__name__,
            "envelope": None,
            "pre_guard": None,
            "guard": None,
            "action_ready": False,
            "execution_authorized": False,
        }

    native_output = None
    envelope = None
    parse_error = None
    try:
        rendered = adapter.render(validated)
        native_output = adapter.propose(rendered)
        envelope = adapter.parse_envelope(native_output)
    except Exception as exc:
        parse_error = "ADAPTER_" + type(exc).__name__
    try:
        after = observe("post_predict")
    except Exception as exc:
        return {
            "primary": None,
            "recommended_action": "RETURN_TO_SOL",
            "fallback": True,
            "reason": "POST_STATE_" + type(exc).__name__,
            "envelope": envelope,
            "rejected_native_output": native_output if envelope is None else None,
            "pre_guard": pre_guard,
            "guard": None,
            "action_ready": False,
            "execution_authorized": False,
        }
    if parse_error is not None:
        return guarded_fallback(after, parse_error, rejected=native_output, pre_guard=pre_guard)
    checked = guard(
        validated,
        envelope["normalized"],
        adapter.pinned_identity,
        after["actor"],
        after["now"],
        after["sources"],
    )
    if not checked["pass"]:
        return guarded_fallback(after, checked["reason"], envelope=envelope, pre_guard=pre_guard)
    return {
        "primary": envelope["normalized"],
        "recommended_action": envelope["normalized"]["action_id"],
        "fallback": False,
        "reason": None,
        "envelope": envelope,
        "pre_guard": pre_guard,
        "guard": checked,
        "action_ready": checked["pass"],
        "execution_authorized": False,
    }
