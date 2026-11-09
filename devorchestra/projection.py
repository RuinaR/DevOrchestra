"""Versioned, read-only evidence contract for the Windows console."""
from datetime import datetime, timezone

from .catalog import Catalog, DEFAULT_BUDGET, latest_requirements
from .dashboard import project_view
from .development import session_view, usage_totals
from .event_journal import OPEN_ROLE_EVENTS
from .lease import Blocked
from .ledger import digest
from .mxc_diagnostic import diagnostic_unresolved
from .policy import live_preflight
from .runtime import PHASES, POLICY_HASH, PROJECTS, ROLE_TOKENS, freeze_project
from .shutdown import resume_problem, shutdown_state
from .token_reserve import DEFAULT_RESERVE, reserve_status


def dashboard_projection_v1(events, tail, project_id, observed_at, catalog=None):
    catalog = catalog or {"projects": {}, "global_budget": DEFAULT_BUDGET, "version": 0}
    registration = catalog["projects"].get(project_id)
    names = {key: project.name for key, project in PROJECTS.items()}
    names.update((e["project_id"], e["project"]["name"]) for e in events if e["kind"] == "run_started")
    names.update((key, item["data"]["name"]) for key, item in catalog["projects"].items())
    view = project_view(events, project_id)
    global_view = project_view(events)
    project_events = [dict(event, sequence=seq) for seq, event in enumerate(events, 1)
                      if event["project_id"] == project_id]
    start = view["run"]
    requirements = latest_requirements(catalog, project_id)
    current = [e for e in project_events if start and e["run_id"] == start["run_id"]]
    latest = current[-1] if current else {}
    global_events = global_view["events"]
    global_latest = global_events[-1] if global_events else {}
    pending = global_latest if global_latest.get("kind") in OPEN_ROLE_EVENTS else {}
    phases = []
    for phase, role in PHASES.items():
        began = next((e for e in current if e.get("phase") == phase.value
                      and e["kind"] == "role_started"), None)
        ended = next((e for e in current if e.get("phase") == phase.value
                      and e["kind"] == "role_finished"), None)
        if ended:
            status = ended["state"] if ended["state"] in ("FAILED", "BLOCKED") else "RECORDED OK"
        elif began:
            status = "UNKNOWN" if latest["kind"] in OPEN_ROLE_EVENTS else latest["state"]
        else:
            status = "NEXT" if view["state"] == phase.value else "WAITING"
        phases.append({"phase": phase.value, "role": role, "state": status,
                       "started_seq": began["sequence"] if began else None,
                       "finished_seq": ended["sequence"] if ended else None,
                       "completed_with_evidence": phase.value in view.get("completed_phases", [])})

    roles = {}
    for role in dict.fromkeys(PHASES.values()):
        begins = [e for e in project_events if e.get("role") == role and e["kind"] == "role_started"]
        began = begins[-1] if begins else None
        evidence = [e for e in project_events if began and e.get("agent_run_id") == began["agent_run_id"]]
        ended = next((e for e in evidence if e["kind"] == "role_finished"), None)
        roles[role] = {"start": began, "finish": ended, "latest": evidence[-1] if evidence else None}

    job_events = [dict(event, sequence=seq) for seq, event in enumerate(events, 1)
                  if event.get("source") == "WINDOWS_SYNTHETIC_JOB"]
    last_job = job_events[-1] if job_events else None
    job_pending = last_job is not None and last_job["kind"] != "job_drained"
    recovery = next((dict(event, sequence=seq) for seq, event in reversed(list(enumerate(events, 1)))
                     if event["kind"] in ("recovery", "job_recovery_blocked", "cancelled")
                     or (event["kind"] == "job_drained" and event.get("result") == "CANCELLED")), None)
    latest_stream = next((e for e in reversed(project_events)
                          if e["kind"] in ("stream_event", "stream_interrupted")), None)
    live = live_preflight()
    shutdown = dict(shutdown_state(events))
    if shutdown["state"] == "READY":
        saved = shutdown["checkpoint"]
        shutdown["resume_problem"] = (
            "Checkpoint configuration changed"
            if (saved["catalog_hash"] != digest(catalog) or saved["policy_hash"] != POLICY_HASH
                or events[-1]["kind"] != "shutdown_ready")
            else resume_problem(saved, datetime.fromisoformat(observed_at).timestamp(), shutdown.get("budget_status"))
        )
    token_policy = catalog.get("token_reserve", DEFAULT_RESERVE)
    token_budget = None
    if start:
        latest_role = next((event for event in reversed(current) if event["kind"] == "role_started"), {})
        unknown = latest.get("kind") in OPEN_ROLE_EVENTS or any(event["kind"] == "recovery" for event in current)
        limit = min(start["token_limit"], freeze_project(project_id, catalog)["budget"]["tokens"])
        token_budget = reserve_status(limit, usage_totals(current)[0], ROLE_TOKENS, token_policy,
                                      active=latest_role.get("token_reservation", {}).get("active_reservation", 0)
                                      if latest.get("kind") in OPEN_ROLE_EVENTS else 0,
                                      verified=not unknown)
        if unknown:
            token_budget["reason"] = "USAGE_UNVERIFIED"
    recovery_state = "BLOCKED"
    if pending or job_pending or diagnostic_unresolved(events):
        recovery_state = "UNKNOWN"
    elif shutdown["state"] == "RECOVERY_REQUIRED":
        recovery_state = "RECOVERY_REQUIRED"
    elif shutdown["state"] == "READY" and not shutdown["resume_problem"]:
        recovery_state = "RESUMABLE"
    session = session_view(events, project_id)
    return {
        "schema_version": 1,
        "as_of_utc": observed_at,
        "snapshot_version": (len(events), tail),
        "observation_kind": "VERIFIED_MOCK_LEDGER",
        "project_id": project_id,
        "project_name": names[project_id],
        "project_names": names,
        "registration": registration,
        "requirements": requirements,
        "workflow": {
            "requirements_count": len(requirements),
            "cycle_requirements": start.get("requirements", []) if start else [],
            "development_admission": live["status"],
            "latest_cycle_state": latest.get("state"),
            "latest_cycle_evidence": "SYNTHETIC" if start else "NONE",
            "user_test_status": "NOT_AVAILABLE",
            "user_test_block_reason": "TRUSTED_LIVE_VERIFIER_RECEIPT_REQUIRED",
            "previous_cycle": session["start"].get("previous_cycle") if session else None,
        },
        "shutdown": shutdown,
        "recovery_state": recovery_state,
        "token_policy": token_policy,
        "token_budget": token_budget,
        "setup": catalog.get("setups", {}).get(project_id),
        "catalog_version": catalog["version"],
        "configured_global_budget": catalog["global_budget"],
        "development": session,
        "global_development": session_view(events),
        "project_state": view["state"],
        "project_states": {key: project_view(events, key)["state"] for key in names},
        "global_state": global_view["state"],
        "global_run": global_view["run"],
        "run": start,
        # P1 records intent, not a live liveness observation. Never assert an active role.
        "active_role": None,
        "unconfirmed_role": pending.get("role", pending.get("stream_role")),
        "unconfirmed_agent_run_id": pending.get("agent_run_id"),
        "pipeline_phases": phases,
        "milestone_gates": [
            {"name": "P1 · Mock regression", "state": "PASS",
             "git_sha": "0ebe8db5ca7a05715862383dff09cd9492cef4c5", "scope": "8 + CLI"},
            {"name": "P1.5 · Headless UI", "state": "PASS",
             "git_sha": "a2f650e56251c6145091d1e4686679af587b63eb", "scope": "14 + CLI"},
            {"name": "P2 · OS isolation", "state": "BLOCKED"},
        ],
        "role_cards": roles,
        "resources": {
            "elapsed": view.get("elapsed_seconds"),
            "time_budget": start.get("budget", start["project"])["seconds"] if start and start["scenario"] != "time-limit"
            else 0 if start else None,
            "deadline": start["deadline"] if start else None,
            "synthetic_tokens": view.get("synthetic_tokens"),
            "token_limit": start["token_limit"] if start else None,
            "cost_limit": start.get("budget", {}).get("cost_microunits", start["project"]["cost_limit"]) if start else None,
            "measured_tokens": "NOT_APPLICABLE" if start else "UNKNOWN",
            "cost": "NOT_APPLICABLE" if start else "UNKNOWN",
        },
        "safety": {
            "lease": "DRAIN UNVERIFIED" if job_pending else "NOT OBSERVED",
            "sandbox": "BLOCKED",
            "isolation_mode": f"{live['isolation_candidate']} CANDIDATE / WINDOWS JOB PROBE ONLY",
            "files": "BLOCKED",
            "network": "BLOCKED",
            "credentials": "BLOCKED",
            "security_baseline": {
                "git_sha": "24fc3e74f8f1ef483b00c4677a97639b02061a6f",
                "scope": "Win32 synthetic Job",
                "evidence": "tests/test_job_probe.py",
            },
            "real_execution": live["status"],
            "noninterference": live["noninterference"],
            "block_reason": "Owned Job drain unverified" if job_pending else "OS noninterference not verified",
            "job": last_job,
            "job_pending": job_pending,
            "recovery": recovery,
            "approval": next((e for e in reversed(current)
                              if e["kind"] in ("permission", "owner_approved_mock")), None),
            "latest": latest,
        },
        "events": project_events,
        "latest_stream": latest_stream,
    }


class ProjectionReader:
    """Keep a verified watermark for the lifetime of the window."""
    def __init__(self, ledger):
        self.ledger = ledger
        self.version = (0, "")

    def read(self, project_id):
        events, tail = self.ledger.snapshot()
        count, previous = self.version
        if len(events) < count:
            raise Blocked("Ledger sequence moved backwards")
        if len(events) == count:
            if tail != previous:
                raise Blocked("Ledger snapshot changed at the same sequence")
        else:
            prefix = ""
            for seq, event in enumerate(events[:count], 1):
                prefix = digest([seq, prefix, event])
            if prefix != previous:
                raise Blocked("Ledger snapshot replaced its verified history")
        view = dashboard_projection_v1(events, tail, project_id,
                                       datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       Catalog(self.ledger).read())
        self.version = view["snapshot_version"]
        return view
