"""Read-only dashboard projection and terminal renderer."""
from .runtime import PHASES, State, last_run


def project_view(events, project=None):
    if project is not None:
        events = [event for event in events if event["project_id"] == project]
    run = last_run(events)
    if not run:
        return {"state": State.IDLE.value, "run": None, "roles": {}, "events": []}
    start, latest = run[0], run[-1]
    roles = {}
    for role in dict.fromkeys(PHASES.values()):
        entries = [event for event in run if event.get("role") == role]
        roles[role] = {
            "start": next((e for e in reversed(entries) if e["kind"] == "role_started"), None),
            "latest": entries[-1] if entries else None,
        }
    return {
        "state": latest["state"], "run": start, "roles": roles, "events": run,
        "completed_phases": [e["phase"] for e in run if e["kind"] == "role_finished"
                             and e["result"] in ("OK", "PASS") and not e.get("reason")],
        "synthetic_tokens": sum(e.get("synthetic_tokens", 0) for e in run),
        "elapsed_seconds": sum(e.get("elapsed_seconds", 0) for e in run),
    }


def render(view):
    state = view["state"]
    if view["run"] is None:
        return "[IDLE] No recorded run"
    start = view["run"]
    project = start["project"]
    latest = view["events"][-1]
    phase_bar = " > ".join(
        f"[done {phase.value}]" if phase.value in view["completed_phases"]
        else f"[{state}]" if phase.value == state else phase.value
        for phase in PHASES
    )
    lines = [
        f"{project['name']} | {project['milestone']} | MOCK | [{state}]",
        f"Run {start['run_id']} | Task {start['task_id']} | Generation {start['generation']}",
        f"Goal: {start['goal']}",
        phase_bar,
        f"Recorded role time: {view['elapsed_seconds']:.3f}s | UTC deadline: "
        f"{start['deadline']:.0f} (Unix seconds)",
        f"Synthetic tokens: {view['synthetic_tokens']}/{start['token_limit']} | "
        "Model tokens and cost: NOT_APPLICABLE",
        f"Latest evidence: {latest.get('reason', latest.get('decision', latest['kind']))}",
        "",
    ]
    for role, panel in view["roles"].items():
        record = panel["latest"]
        if record is None:
            lines.append(f"{role:<10} | No execution recorded")
            continue
        began = panel["start"]
        status = record.get("result", "START RECORDED / LIVE STATUS UNKNOWN")
        ended = record["timestamp"] if record["kind"] == "role_finished" else "UNKNOWN"
        lines.append(f"{role:<10} | {record.get('phase', began['phase'])} | {status} "
                     f"| {began['model']} / {began['reasoning']}")
        lines.append(f"             {began['timestamp']} -> {ended} | "
                     f"{record.get('decision', record.get('reason', record['kind']))}")
        if record["kind"] == "role_finished":
            lines.append(f"             {record['elapsed_seconds']:.3f}s | "
                         f"Synthetic tokens: {record['synthetic_tokens']} | Retries: {record['retries']}")
    lines.append("\nRecent verified events")
    for event in view["events"][-8:]:
        lines.append(f"{event['timestamp']} | {event['state']} | {event.get('role', 'Runner')} "
                     f"| {event['kind']}")
    return "\n".join(lines)
