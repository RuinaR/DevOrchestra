"""Pure development-session projection and Manager decision checks."""
from datetime import datetime

from .lease import Blocked

DECISIONS = {
    "NEXT": "NEXT",
    "REWORK": "REWORK",
    "APPROVAL": "NEED_APPROVAL",
    "BLOCK": "BLOCKED",
    "GOAL_COMPLETE": "COMPLETED",
    "NO_PROGRESS": "NO_PROGRESS",
}


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
    completed = sum(run[-1]["state"] == "COMPLETED" for run in runs.values())
    end = next((e for e in history if e["kind"] == "session_ended"), None)
    finishes = [e for e in history if e["kind"] == "role_finished"]
    usage_unknown = (bool(latest and latest[-1]["kind"] == "role_started")
                     or any(e["kind"] in ("recovery", "job_recovery_blocked") for e in history))
    return {
        "start": start, "end": end, "cycles": len(runs), "completed": completed,
        "runs": [{"run_id": run[0]["run_id"], "state": run[-1]["state"],
                   "tokens": sum(e.get("synthetic_tokens", 0) for e in run),
                   "selection": run[0]["selection"]} for run in runs.values()],
        "current": latest, "tokens": sum(e["synthetic_tokens"] for e in finishes),
        "cost": sum(e["synthetic_cost_microunits"] for e in finishes),
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
