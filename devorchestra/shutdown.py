"""Mock-only shutdown receipts. Runtime admission and writes share the global slot."""
from datetime import datetime, timezone
from uuid import uuid4

from .development import usage_totals
from .event_journal import OPEN_ROLE_EVENTS
from .lease import Blocked
from .ledger import digest

HELD = {"SCHEDULED", "READY", "RECOVERY_REQUIRED"}
UNKNOWN = OPEN_ROLE_EVENTS | {"job_intent", "job_created", "job_recovery_blocked", "recovery"}
TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "BLOCKED"}


def shutdown_state(events):
    return next((event for event in reversed(events)
                 if event["kind"].startswith("shutdown_")), {"state": "RUNNING"})


def resume_problem(saved, now):
    deadlines = [saved["session_deadline"]]
    if saved["state"] not in TERMINAL:
        deadlines.extend((saved["deadline"], saved["approval_expires"]))
    if any(deadline is not None and now >= deadline for deadline in deadlines):
        return "Checkpoint approval or time budget expired"
    if (saved["state"] not in ("IDLE", *TERMINAL)
            and (saved["tokens_remaining"] is not None and saved["tokens_remaining"] < 1
                 or saved["cost_remaining"] is not None and saved["cost_remaining"] < 0)):
        return "Checkpoint budget exhausted"
    return None


def checkpoint(run, catalog, development, policy, sequence, tail):
    start = run[0] if run else {}
    last = run[-1] if run else {}
    roles = [event for event in run if event["kind"] == "role_finished"]
    approval = next((event for event in reversed(run) if event["kind"] == "owner_approved_mock"), {})
    tokens, cost = usage_totals(run)
    session = development["start"] if development and not development["end"] else {}
    return {
        "sequence": sequence, "chain_hash": tail, "catalog_hash": digest(catalog), "policy_hash": policy,
        "session_ref": digest(session),
        "run_ref": digest(start), "project_id": start.get("project_id", session.get("project_id")),
        "session_id": start.get("development_id", session.get("development_id")),
        "run_id": start.get("run_id"), "task_id": start.get("task_id"), "generation": start.get("generation"),
        "state": last.get("state", "IDLE"), "current_phase": last.get("resume_phase", last.get("state")),
        "last_completed_role": roles[-1]["role"] if roles else None,
        "completed_role_refs": [event["handoff_ref"] for event in roles if "handoff_ref" in event],
        "approval_ref": digest(approval), "approval_expires": approval.get("expires"),
        "deadline": start.get("deadline"), "session_deadline": session.get("deadline"),
        "budget": start.get("budget"), "tokens_used": tokens, "cost_used": cost,
        "tokens_remaining": start["token_limit"] - tokens if start else None,
        "cost_remaining": start["budget"]["cost_microunits"] - cost if start else None,
        "session_tokens_remaining": session["limits"]["tokens"] - development["tokens"] if session else None,
        "session_cost_remaining": session["limits"]["cost_microunits"] - development["cost"] if session else None,
        "changes": [{"path": event["diff"]["path"], "diff_ref": event.get("diff_ref")}
                    for event in roles if "diff" in event],
        "git_base": start.get("git_before"), "replica": "MOCK_ONLY",
        "external_requests": "NONE_SYNTHETIC", "child_drain": "NO_PENDING_OWNED_WORK",
    }


def transition(action, current, evidence, *, request_id, unresolved, now, wait_seconds):
    state = current["state"]
    if action == "request":
        if state in HELD:
            return current
        if type(wait_seconds) is not int or not 1 <= wait_seconds <= 60:
            raise Blocked("Invalid shutdown wait bound")
        return _event("SCHEDULED", uuid4().hex, now, deadline=now + wait_seconds)
    if not request_id or current.get("request_id") != request_id:
        raise Blocked("Stale shutdown request")
    if action == "cancel" and state == "SCHEDULED":
        if unresolved:
            raise Blocked("Interrupted work requires recovery")
        return _event("CANCELLED", request_id, now)
    if action == "finalize":
        if state in ("READY", "RECOVERY_REQUIRED"):
            return current
        if state == "SCHEDULED":
            if unresolved:
                return _event("RECOVERY_REQUIRED", request_id, now, reason=(
                    "WAIT_LIMIT_OWNERSHIP_UNKNOWN" if now >= current["deadline"] else "OWNERSHIP_UNKNOWN"),
                    external_requests="UNKNOWN", child_drain="UNKNOWN")
            return _event("READY", request_id, now, checkpoint=evidence)
    if action == "resume" and state == "READY":
        saved = current["checkpoint"]
        if unresolved or saved != evidence:
            raise Blocked("Checkpoint no longer matches the ledger or configuration")
        problem = resume_problem(saved, now)
        if problem:
            raise Blocked(problem)
        return _event("RESUMED", request_id, now, checkpoint_ref=digest(saved))
    raise Blocked("Shutdown transition unavailable")


def _event(state, request_id, now, **fields):
    return {"kind": "shutdown_" + state.lower(), "state": state, "request_id": request_id,
            "project_id": None, "run_id": None, "source": "MOCK_CONTROLLER",
            "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(), **fields}
