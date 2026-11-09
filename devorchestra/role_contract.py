"""Draft wire contracts. Pure validation only; no execution admission."""
from dataclasses import asdict, dataclass
import json
import re

from .development import DECISIONS
from .lease import Blocked
from .ledger import digest


MAX_OUTPUT_BYTES = 16_384
HASH = r"^[0-9a-f]{64}$"


def _text(limit=1000):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def _list(item, limit=16):
    return {"type": "array", "items": item, "maxItems": limit}


def _object(**properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _choice(*values):
    return {"type": "string", "enum": list(values)}


OUTPUT_SCHEMAS = {
    "manager-planning": _object(
        disposition=_choice("PROPOSED", "NEED_APPROVAL", "BLOCKED", "NO_PROGRESS"),
        priority_reason=_text(), task_spec=_text(3000), proposed_paths=_list(_text(240)),
        acceptance_checks=_list(_text()), stop_conditions=_list(_text()),
    ),
    "developer": _object(
        disposition=_choice("READY_FOR_REVIEW", "BLOCKED", "NO_PROGRESS"),
        summary=_text(2000), changes=_list(_object(path=_text(240), reason=_text()), 32),
        validation_attempts=_list(_text()), unfinished=_list(_text()),
    ),
    "reviewer": _object(
        decision=_choice("ACCEPT_FOR_VERIFICATION", "REWORK", "BLOCKED"),
        findings=_list(_object(severity=_choice("HIGH", "MEDIUM", "LOW"), location=_text(240),
                               observed=_text(), impact=_text(), required_fix=_text())),
        coverage_gaps=_list(_text()),
    ),
    "manager-final": _object(
        decision=_choice(*DECISIONS), goal_hash={**_text(64), "pattern": HASH},
        evidence_ref={**_text(64), "pattern": HASH}, reason=_text(2000),
        next_task_or_rework=_text(2000),
    ),
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Blocked("Duplicate role output key")
        result[key] = value
    return result


def _validate(value, schema):
    kind = schema["type"]
    if kind == "object" and type(value) is dict and set(value) == set(schema["required"]):
        for key, rule in schema["properties"].items():
            _validate(value[key], rule)
        return
    if kind == "array" and type(value) is list and len(value) <= schema["maxItems"]:
        for item in value:
            _validate(item, schema["items"])
        return
    if kind == "string" and type(value) is str:
        if "enum" in schema and value in schema["enum"]:
            return
        if ("enum" not in schema and schema["minLength"] <= len(value) <= schema["maxLength"]
                and value.strip() and all(ord(char) >= 32 and not 0xD800 <= ord(char) <= 0xDFFF
                                          for char in value)
                and ("pattern" not in schema or re.fullmatch(schema["pattern"], value))):
            return
    raise Blocked("Role output does not match its schema")


def parse_role_output(role, payload):
    if role not in OUTPUT_SCHEMAS or type(payload) is not bytes or len(payload) > MAX_OUTPUT_BYTES:
        raise Blocked("Invalid role output envelope")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise Blocked("Invalid role output JSON") from None
    _validate(value, OUTPUT_SCHEMAS[role])
    return value


def confirm_task_plan(payload, *, allowed_files, acceptance, budget, authority_ref):
    """Validate a plan against Controller-selected limits; the result grants no capabilities."""
    plan = parse_role_output("manager-planning", payload)
    paths = plan["proposed_paths"]
    if (plan["disposition"] != "PROPOSED" or not paths or len(set(paths)) != len(paths)
            or any(path not in allowed_files for path in paths)
            or not acceptance or plan["acceptance_checks"] != list(acceptance)):
        raise Blocked("Manager plan exceeds the Controller task contract")
    return {"paths": paths, "acceptance": list(acceptance), "budget": dict(budget),
            "authority_ref": authority_ref, "proposal_ref": digest(plan)}


def bind_instructions(request, layers):
    """Bind verified, Controller-supplied references. This does not load the files."""
    layers = tuple(layers)
    if (len(layers) < 4
            or any(type(item) is not dict or set(item) != {"layer", "version", "sha256"}
                   or type(item["version"]) is not str or not 0 < len(item["version"].strip()) <= 128
                   or type(item["sha256"]) is not str or not re.fullmatch(HASH, item["sha256"])
                   for item in layers)
            or [item["layer"] for item in layers] !=
               ["global", "common", *(["project"] * (len(layers) - 3)), "role"]
            or len({item["sha256"] for item in layers}) != len(layers)):
        raise Blocked("Invalid or duplicate instruction binding")
    return digest({"role_request": asdict(request), "instruction_layers": layers})


@dataclass(frozen=True)
class TransitionEvidence:
    owned_tree_drained: bool
    scope_released: bool
    receipt_committed: bool
    admission_open: bool
    next_role_and_reserve_funded: bool
    rework_remaining: int
    repeated_failure: bool
    goal_hash: str | None = None
    verifier_ref: str | None = None
    verifier_verdict: str | None = None
    goal_criteria_passed: bool = False


def decide_transition(role, payload, evidence):
    """Preview a transition using trusted Controller facts, without changing state."""
    output = parse_role_output(role, payload)
    if not all(flag is True for flag in (evidence.owned_tree_drained, evidence.scope_released,
                                          evidence.receipt_committed, evidence.admission_open)):
        return "BLOCKED"
    decision = output.get("decision", output.get("disposition"))
    if ((decision == "PROPOSED" and not output["acceptance_checks"])
            or (role == "reviewer" and decision == "REWORK" and not output["findings"])):
        return "BLOCKED"
    if role == "manager-final":
        if (output["goal_hash"] != evidence.goal_hash or output["evidence_ref"] != evidence.verifier_ref
                or evidence.verifier_verdict not in ("PASS", "FAIL")):
            return "BLOCKED"
        if decision in ("NEXT", "GOAL_COMPLETE") and evidence.verifier_verdict != "PASS":
            return "BLOCKED"
        if decision == "GOAL_COMPLETE" and evidence.goal_criteria_passed is not True:
            return "BLOCKED"
    if decision == "REWORK" and (type(evidence.rework_remaining) is not int
                                 or evidence.rework_remaining <= 0
                                 or evidence.repeated_failure is not False):
        return "NO_PROGRESS"
    transitions = {
        "PROPOSED": "DEVELOPER", "READY_FOR_REVIEW": "REVIEWER",
        "ACCEPT_FOR_VERIFICATION": "VERIFIER", "REWORK": "NEW_REWORK_CYCLE",
        "NEXT": "NEW_TASK_CYCLE", "GOAL_COMPLETE": "COMPLETED",
        "APPROVAL": "NEED_APPROVAL", "BLOCK": "BLOCKED",
    }
    if decision in ("PROPOSED", "READY_FOR_REVIEW", "ACCEPT_FOR_VERIFICATION", "REWORK", "NEXT"):
        if evidence.next_role_and_reserve_funded is not True:
            return "SCHEDULE_SHUTDOWN"
    return transitions.get(decision, decision)
