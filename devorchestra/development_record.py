"""Publishable cycle summaries projected from Controller evidence only."""
from collections import Counter
from difflib import unified_diff
from pathlib import Path
import re

from .lease import Blocked
from .ledger import check_path, digest
from .replica_evidence import fingerprint


DIRECTORY = "docs/development"
ROOT = DIRECTORY + "/"
CURRENT, LOG = ROOT + "CURRENT.md", ROOT + "CYCLE-LOG.md"
LIMIT = 1024**2
COUNTS = {"VERIFIED", "IMPLEMENTED", "NOT_VERIFIED", "NOT_VALIDATED", "BLOCKED", "REGRESSED",
          "REWORK", "NOT_STARTED", "USER_TEST_REQUIRED", "NEEDS_INPUT"}


def reserved(path):
    return path.casefold() == DIRECTORY or path.casefold().startswith(ROOT)


def candidate_files_digest(files):
    return digest({name: info for name, info in files.items() if not reserved(name)})


def _one_of(value, allowed):
    return value if type(value) is str and value in allowed else "UNKNOWN"


def _count(value):
    return value if type(value) is int and value >= 0 else "UNKNOWN"


def _sha(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value) else "UNKNOWN"


def _path(value):
    # Display repository-relative components as code, never an arbitrary URL or raw Markdown.
    return f"`{value}`" if re.fullmatch(r"[\w .@+/-]+", value) and len(value) <= 240 else "(path withheld)"


def render(events, *, project, run, cycle, branch, head, candidate, criterion, final, criteria, requirements, paths):
    if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", project) or not re.fullmatch(r"[a-f0-9]{32}", run)
            or type(cycle) is not int or not 1 <= cycle <= 999999
            or (branch is not None and not re.fullmatch(r"refs/heads/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+", branch))
            or not re.fullmatch(r"C[0-9]+-[a-f0-9]{8}", criterion)
            or final not in {"REWORK", "USER_TEST_REQUIRED", "CONTINUE", "COMPLETED"}):
        raise Blocked("Development record identity is unsupported")
    rows = [(seq, row) for seq, row in enumerate(events, 1)
            if (row.get("project_id"), row.get("run_id"), row.get("cycle")) == (project, run, cycle)]

    def match(kind, phase=None, *, bound=False):
        return next(((seq, row) for seq, row in reversed(rows) if row["kind"] == kind
                     and (phase is None or row.get("phase") == phase)
                     and (not bound or row.get("source_sha256") == candidate)), (None, {}))

    decision_seq, decision = match("native_inspection_decision_applied", bound=True)
    progress_seq, progress = match("native_inspection_cycle_progress", bound=True)
    if decision.get("verdict") != final or progress.get("criteria") != criteria:
        raise Blocked("Development record lacks a matching settled decision and criterion inventory")
    plan_seq, plan = match("native_inspection_plan_accepted")
    if plan.get("target") != criterion:
        plan_seq, plan = None, {}
    edit_seq, edit = match("native_inspection_developer_diff", bound=True)
    developer_seq, developer = match("native_inspection_role_result", "developer")
    review_seq, review = match("native_inspection_review_decision", bound=True)
    verify_seq, verification = match("native_inspection_role_result", "verifier", bound=True)
    if not verification:
        verify_seq, verification = match("native_inspection_verifier_reused", bound=True)
    visual_seq, visual = match("native_inspection_visual_review", bound=True)
    capture_seq, _ = match("native_inspection_review_capture_result", bound=True)
    play_seq, play = match("native_inspection_play_finished", bound=True)
    review_status = _one_of(review.get("verdict", progress.get("reviewer")), {"ACCEPT", "REWORK", "UNAVAILABLE"})
    verify_status = _one_of(verification.get("verdict"), {"PASS", "FAIL", "NOT_VALIDATED", "USER_TEST_REQUIRED"})
    visual_status = _one_of(visual.get("status"), {"MODEL_ANALYSIS_REPORTED", "NOT_VERIFIED", "ANALYSIS_FAILED"})
    state = _one_of(criteria.get(criterion), COUNTS)
    cause = _one_of(progress.get("decision_cause"), {"VERIFICATION_FAILED", "REVIEW_FINDING_UNCLASSIFIED",
                    "ENVIRONMENT_BLOCKED", "VERIFICATION_MISSING", "USER_ACTION_REQUIRED"})
    next_step = ("Address the review finding or failed check and verify the revised candidate." if final == "REWORK"
                 else "Supply the missing independent or user evidence before marking this criterion verified."
                 if state != "VERIFIED" else "Continue with the next open criterion under the approved scope.")
    tally = Counter(_one_of(value, COUNTS) for value in criteria.values())
    inventory = ", ".join(f"{key} {tally[key]}" for key in sorted(tally)) or "UNKNOWN"
    observations = []
    for row in verification.get("observations", ())[:40]:
        level = _one_of(row.get("level"), {"static", "build", "test", "run", "gui", "performance", "distribution", "functional"})
        coverage = _one_of(row.get("coverage"), {"partial", "criterion"})
        result = ("PASS" if digest(row["expected"]) == digest(row["actual"]) else "FAIL") if {
            "expected", "actual"} <= row.keys() else "NOT_VERIFIED"
        observations.append(f"- {level} / {coverage} / {result} · receipt `{digest(row)}`")
    usage = []
    for seq, row in rows:
        phase = _one_of(row.get("phase"), {"manager-planning", "developer", "reviewer", "manager-final"})
        if row["kind"] != "native_inspection_role_result" or phase == "UNKNOWN":
            continue
        amount = row.get("provider_usage")
        counts = (f"input {_count(amount.get('input_tokens'))}, output {_count(amount.get('output_tokens'))}"
                  if isinstance(amount, dict) else "UNKNOWN")
        usage.append(f"- {phase} · {counts} · Seq {seq}")
    filename = f"cycle-{cycle:04d}-{run}.md"
    relative = f"cycles/{filename}"
    marker = f"<!-- devorchestra-development-v1 {project} {branch} -->\n"
    findings = _count(len(visual.get("findings", ()))) if visual else "UNKNOWN"
    difference = ("Reviewer and Verifier outcomes differ. See both receipt references.\n"
                  if (review_status, verify_status) in {("ACCEPT", "FAIL"), ("REWORK", "PASS")} else "")
    summary = [f"Project `{project}` · branch `{branch or 'LOCAL_ONLY'}`", f"Base Git HEAD `{_sha(head)}`",
               f"Active criterion `{criterion}` · **{state}** · {final}",
               f"Requirements {requirements} · criteria {len(criteria)} · {inventory}",
               f"Reviewer {review_status} · independent Verifier {verify_status}",
               f"Windows GUI review {visual_status} · observed input transitions {_count(progress.get('adaptive_play_actions'))}",
               f"Remaining issue {cause}" if cause != "UNKNOWN" else "Remaining issues are recorded in the cycle evidence.",
               next_step]
    published = next((row for row in reversed(events) if row.get("kind") == "direct_git_pushed"
                      and row.get("project_id") == project and row.get("branch") == branch
                      and row.get("commit") == row.get("remote_head") == head), {})
    publication = ("This record and its code are included in the same commit. Controller receipts confirm remote publication."
                   if branch else "Local Controller record. No automatic Git publication is authorized.")
    criteria_lines = "\n".join(f"- `{key}` · {_one_of(value, COUNTS)}" for key, value in criteria.items()
                               if re.fullmatch(r"C[0-9]+-[a-f0-9]{8}", key))
    current = ("# Current development status\n\n" + "\n".join(f"- {value}" for value in summary)
               + f"\n- Latest record [{filename}]({relative})\n"
               + f"- Previous confirmed publication · cycle {_count(published.get('cycle'))} · `{_sha(published.get('commit'))}`\n"
               + f"- {publication}\n"
               + "\n## Criterion inventory\n\n" + criteria_lines + "\n"
               + "\n[Cycle log](CYCLE-LOG.md)\n")
    path_lines = "\n".join(f"- {_path(name)}" for name in sorted(paths)[:120])
    commands = developer.get("command_events", ())
    developer_status = _one_of(developer.get("outcome"), {"COMPLETED", "REQUEST_UNKNOWN", "USAGE_UNKNOWN"})
    detail = (f"# Cycle {cycle} · {criterion}\n\n" + "\n".join(f"- {value}" for value in summary)
              + f"\n- Run `{run}` · candidate SHA-256 `{_sha(candidate)}`\n"
              + f"- Controller progress Seq {progress_seq} · final Manager Seq {decision_seq} · response `{_sha(decision.get('response_sha256'))}`\n"
              + "\n## Plan and changed components\n\n"
              + f"- Plan type {_one_of(plan.get('mode'), {'edit', 'documentation', 'verify'})} · Seq {plan_seq or 'UNKNOWN'}"
                f" · plan `{_sha(plan.get('plan_sha256'))}`\n"
              + f"- Candidate changes {len(paths)} · Developer diff Seq {edit_seq or 'UNKNOWN'} · `{_sha(edit.get('cycle_diff_sha256'))}`\n"
              + f"- Developer result {developer_status} · Seq {developer_seq or 'UNKNOWN'} · command receipts {len(commands)}."
                " Developer command receipts do not establish independent verification.\n"
              + path_lines + ("\n- Additional paths are listed in the commit diff." if len(paths) > 120 else "")
              + "\n\n## Review and independent verification\n\n"
              + f"- Reviewer Seq {review_seq or 'UNKNOWN'} · finding reference `{_sha(review.get('finding_sha256'))}`\n"
              + f"- Verifier Seq {verify_seq or 'UNKNOWN'} · receipt `{_sha(progress.get('verifier_sha256'))}`\n"
              + f"- GUI capture Seq {capture_seq or 'UNKNOWN'} · visual review Seq {visual_seq or 'UNKNOWN'} · findings {findings}\n"
              + f"- Play Seq {play_seq or 'UNKNOWN'} · actions {_count(play.get('actions'))} · inputs {_count(play.get('inputs'))}\n"
              + difference
              + "- Capture, observed input, Reviewer verdict and individual checks are separate evidence. Criterion completion follows the Controller inventory.\n"
              + ("\n".join(observations) + "\n" if observations else "- No individual check details were available in this run's receipt.\n")
              + "\n## Provider usage\n\n" + ("\n".join(usage) if usage else "- UNKNOWN") + "\n"
              + "\n[Current status](../CURRENT.md) · [Cycle log](../CYCLE-LOG.md)\n")
    entry = (f"- [{cycle:04d} · {criterion} · `{run}`]({relative}) · {final} · {state} · "
             f"Verifier {verify_status} · code and record in the same commit\n")
    return marker, current, entry, ROOT + relative, detail


def patch(root, files, report):
    """Prepare a complete reversible patch without writing into the worktree."""
    marker, current, entry, detail_path, detail = report
    names = (CURRENT, LOG, detail_path)
    folded = {name.casefold(): name for name in files}
    old = {}
    for name in names:
        path = root / Path(name)
        check_path(path)
        if name.casefold() in folded and folded[name.casefold()] != name:
            raise Blocked("Development record case collision")
        if name in files:
            if name == detail_path or fingerprint(path, LIMIT) != files[name]:
                raise Blocked("Development record already exists or changed")
            raw = path.read_bytes()
            content = raw.decode("utf-8")
            if not content.startswith(marker) or not content.endswith("\n") or "\r" in content:
                raise Blocked("Development record path is owned by another project or branch")
            old[name] = content
        elif path.exists():
            raise Blocked("Development record path is occupied")
    log = old.get(LOG, marker + "# Development cycles\n\n[Current status](CURRENT.md)\n\n")
    if f"](cycles/{Path(detail_path).name})" in log:
        raise Blocked("Development record was already indexed")
    content = {CURRENT: marker + current, LOG: log + entry, detail_path: marker + detail}
    outputs = {name: text.encode("utf-8") for name, text in content.items()}
    if any(len(payload) > LIMIT for payload in outputs.values()):
        raise Blocked("Development record exceeds the publication bound")
    changes = "".join(line for name, new in content.items() for line in unified_diff(
        old.get(name, "").splitlines(keepends=True), new.splitlines(keepends=True),
        "a/" + name if name in old else "/dev/null", "b/" + name))
    return outputs, changes.encode("utf-8")
