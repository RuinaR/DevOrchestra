"""Owner-approved closure of an interrupted registered run with durable drain evidence."""
from time import time

from .lease import Blocked, execution_slot
from .ledger import digest


DRAINED_MARKER = "native_inspection_supervisor_drain"
RECOVERED = "native_inspection_safe_abort_recovered"
IDENTITY = ("project_id", "run_id", "request_id", "probe_kind", "approval_sha256")


def _scope(events, tail, project, run, request, expires):
    from .native_cycle import SUPERVISOR_CALLBACK_FAILED
    from .registered_cycle import KIND

    if len(events) < 3 or events[-1].get("kind") != DRAINED_MARKER:
        raise Blocked("The ledger has no current Controller drain marker")
    role, marker = events[-2:]
    starts = [row for row in events if row.get("kind") == "native_inspection_intent"
              and row.get("request_id") == request]
    intent = next((row for row in reversed(events[:-2]) if row.get("request_id") == request
                   and row.get("kind") == "native_inspection_role_intent"), {})
    original = starts[0].get("scope") if len(starts) == 1 else None
    if (len(starts) != 1 or (marker.get("project_id"), marker.get("run_id"), marker.get("request_id"),
                            marker.get("probe_kind")) != (project, run, request, KIND)
            or not all(isinstance(key, str) and key for key in (project, run, request))
            or any(row.get(key) != marker.get(key) for row in (starts[0], intent, role) for key in IDENTITY)
            or not isinstance(original, dict) or original.get("project_id") != project
            or digest(original) != marker.get("approval_sha256")
            or any(row.get("request_id") == request and row.get("kind") in {
                "native_inspection_result", RECOVERED} for row in events)
            or role.get("kind") != "native_inspection_role_result"
            or role.get("phase") != "manager-planning" or role.get("cycle") != marker.get("cycle")
            or (intent.get("phase"), intent.get("cycle")) != (role.get("phase"), role.get("cycle"))
            or role.get("process_stop_reason") != SUPERVISOR_CALLBACK_FAILED
            or role.get("process_result") != "TERMINATED_AND_DRAINED"
            or role.get("owned_tree") != "DRAINED" or marker.get("owned_tree") != "DRAINED"
            or marker.get("staged_evidence_retained") is not True):
        raise Blocked("The Controller drain evidence does not bind to the requested run")
    return {**{key: marker[key] for key in IDENTITY}, "expires": expires,
            "ledger_sequence": len(events), "ledger_head": tail,
            "role_receipt_sha256": digest(role), "drain_marker_sha256": digest(marker),
            "owned_tree": "DRAINED", "model_response": "UNKNOWN", "provider_usage": "UNKNOWN"}


def _receipt(scope, approved_at):
    return {**{key: scope[key] for key in IDENTITY}, "kind": RECOVERED, "state": "DIAGNOSTIC",
            "outcome": "RECOVERED_ABORT", "owned_tree": "DRAINED",
            "model_response": "UNKNOWN", "provider_usage": "UNKNOWN", "usage_complete": False,
            "recovery_scope": scope, "recovery_approval_sha256": digest(scope), "approved_at": approved_at}


def verifies(events, receipt):
    """Validate the recovery against the exact preceding ledger prefix during admission."""
    try:
        scope, approved_at = receipt["recovery_scope"], receipt["approved_at"]
        if (type(approved_at) is not int or type(scope["expires"]) is not int
                or not 0 < scope["expires"] - approved_at <= 900):
            return False
        tail = ""
        for seq, row in enumerate(events, 1):
            tail = digest([seq, tail, row])
        expected = _scope(events, tail, scope["project_id"], scope["run_id"], scope["request_id"], scope["expires"])
        return receipt == _receipt(expected, approved_at)
    except (Blocked, KeyError, TypeError):
        return False


def close(ledger, project, run, request, expires, *, approval=None):
    """Review without writing, or append one safe-abort receipt using the reviewed digest."""
    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("Abandoned slot requires independent ownership recovery")
        now = int(time())
        if type(expires) is not int or not 0 < expires - now <= 900:
            raise Blocked("Use a recovery approval that expires within fifteen minutes")
        events, tail = ledger.snapshot()
        scope = _scope(events, tail, project, run, request, expires)
        if approval is None:
            return {"scope": scope, "approve": digest(scope)}
        if approval != digest(scope):
            raise Blocked("Recovery approval or ledger head changed")
        receipt = _receipt(scope, now)
        seq, head = ledger.append(len(events), tail, receipt)
        return {**receipt, "sequence": seq, "ledger_head": head}
