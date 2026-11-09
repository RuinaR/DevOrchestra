"""Pure development-session projection and Manager decision checks."""
from datetime import datetime

from .event_audit import FIXTURE_USAGE
from .event_journal import OPEN_ROLE_EVENTS
from .lease import Blocked

DECISIONS = {
    "NEXT": "NEXT",
    "REWORK": "REWORK",
    "APPROVAL": "NEED_APPROVAL",
    "BLOCK": "BLOCKED",
    "GOAL_COMPLETE": "COMPLETED",
    "NO_PROGRESS": "NO_PROGRESS",
}


def cycle_progress(runs, acceptance):
    """Compare scoped fixture artifacts and verification, independently of run IDs."""
    implemented, verified = set(), set()
    stalled = 0
    latest = None
    for run in runs:
        if run[-1]["state"] not in ("COMPLETED", "FAILED", "BLOCKED", "CANCELLED"):
            continue
        # Operator cancellation is not a completed development attempt.
        if run[-1]["state"] == "CANCELLED":
            continue
        before = "VERIFIED" if verified else "IMPLEMENTED" if implemented else "NOT_STARTED"
        change = next((event["diff_ref"] for event in run if event["kind"] == "role_finished"
                       and event.get("role") == "Developer" and event.get("result") == "OK"
                       and event.get("diff_ref") and event.get("scope_released") is True
                       and event.get("owned_children") == []
                       and event.get("stream_settlement", {}).get("synthetic_usage_final") is True), None)
        # Rejected or incomplete artifacts do not advance the accepted implementation baseline.
        accepted = change and any(event["kind"] == "role_finished" and event.get("role") == "Reviewer"
                                  and event.get("handoff") == "VERIFYING" for event in run)
        checks = {(change, event["test"]) for event in run if accepted
                  and event["kind"] == "role_finished" and event.get("role") == "Verifier"
                  and event.get("result") == "PASS" and event.get("test")
                  and event.get("scope_released") is True and event.get("owned_children") == []
                  and event.get("stream_settlement", {}).get("synthetic_usage_final") is True}
        implementation_advanced = bool(accepted and change not in implemented)
        verification_advanced = bool(checks - verified)
        if accepted:
            implemented.add(change)
        verified.update(checks)
        stalled = 0 if implementation_advanced else stalled + 1
        latest = {"run_id": run[0]["run_id"], "before": before,
                  "after": "VERIFIED" if verified else "IMPLEMENTED" if implemented else "NOT_STARTED",
                  "implementation_advanced": implementation_advanced,
                  "verification_advanced": verification_advanced,
                  "artifact_ref": change if accepted else None,
                  "checks": sorted(name for _, name in checks)}
    return {"evidence": "SYNTHETIC", "latest": latest, "consecutive_no_progress": stalled,
            "decision": "NO_PROGRESS" if stalled >= 3 else "REPLAN" if stalled == 2 else "CONTINUE",
            # The fixed fixture cannot establish acceptance for a registered user's requirements.
            "requirements": [{"criterion": criterion, "state": "NOT_STARTED"} for criterion in acceptance]}


def usage_totals(events):
    tokens, cost = {}, {}
    for event in events:
        if event["kind"] == "role_finished":
            amounts = event
        elif event["kind"] == "stream_event" and event["stream_receipt"]["record"]["method"] == FIXTURE_USAGE:
            record = event["stream_receipt"]["record"]
            amounts = {"synthetic_tokens": record["tokens"], "synthetic_cost_microunits": record["cost_microunits"]}
        else:
            continue
        key = event["agent_run_id"]
        tokens[key] = max(tokens.get(key, 0), amounts["synthetic_tokens"])
        cost[key] = max(cost.get(key, 0), amounts["synthetic_cost_microunits"])
    return sum(tokens.values()), sum(cost.values())


def session_view(events, project=None):
    start = next((e for e in reversed(events) if e["kind"] == "session_started"
                  and (project is None or e["project_id"] == project)), None)
    if start is None:
        return None
    history = [e for e in events if e.get("development_id") == start["development_id"]]
    runs = {}
    for e in history:
        if e["run_id"] is not None:
            runs.setdefault(e["run_id"], []).append(e)
    latest = next(reversed(runs.values()), [])
    scope = (start["project_id"], start["goal_hash"], start.get("frozen", {}).get("requirements", []))
    related = {e["development_id"] for e in events if e["kind"] == "session_started"
               and (e["project_id"], e["goal_hash"], e.get("frozen", {}).get("requirements", [])) == scope}
    scoped_runs = {}
    for e in events:
        if e.get("development_id") in related and e["run_id"] is not None:
            scoped_runs.setdefault(e["run_id"], []).append(e)
    completed = sum(run[-1]["state"] == "COMPLETED" for run in runs.values())
    end = next((e for e in history if e["kind"] == "session_ended"), None)
    finishes = [e for e in history if e["kind"] == "role_finished"]
    tokens, cost = usage_totals(history)
    usage_unknown = (bool(latest and latest[-1]["kind"] in OPEN_ROLE_EVENTS)
                     or any(e["kind"] in ("recovery", "job_recovery_blocked") for e in history))
    return {
        "start": start, "end": end, "cycles": len(runs), "completed": completed,
        "runs": [{"run_id": run[0]["run_id"], "state": run[-1]["state"],
                   "tokens": usage_totals(run)[0],
                   "selection": run[0]["selection"]} for run in runs.values()],
        "current": latest, "tokens": tokens, "cost": cost,
        "progress": cycle_progress(scoped_runs.values(), start["acceptance"]),
        "role_seconds": sum(e["elapsed_seconds"] for e in finishes),
        "recorded_wall_seconds": max(0, datetime.fromisoformat(history[-1]["timestamp"]).timestamp()
                                     - start["started_epoch"]),
        "usage_kind": "UNKNOWN" if usage_unknown else "SYNTHETIC",
        "retries": sum(e["selection"] == "REWORK" for e in history if e["kind"] == "run_started"),
    }


def validate_proposal(proposal, goal_hash, verification_ref):
    if (not isinstance(proposal, dict) or set(proposal) != {"decision", "goal_hash", "evidence_ref"}
            or not isinstance(proposal["decision"], str) or proposal["decision"] not in DECISIONS
            or proposal["goal_hash"] != goal_hash or not verification_ref
            or proposal["evidence_ref"] != verification_ref):
        raise Blocked("Manager proposal does not match frozen goal and verifier evidence")
    return DECISIONS[proposal["decision"]]
