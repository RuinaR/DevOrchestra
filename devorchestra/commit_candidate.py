"""Offline commit eligibility for a dedicated trusted synthetic evidence ledger."""
from dataclasses import asdict, dataclass
from math import isfinite
from struct import error as StructError

from .adapter import Budget
from .development import validate_proposal
from .lease import Blocked, execution_slot
from .ledger import digest
from .quarantine import MAX_FILES, FileSpec, inspect_bundle


ROLES = ("Manager", "Developer", "Reviewer", "Verifier", "Manager")


def _amounts(budget):
    return budget.seconds, budget.tokens, budget.cost_microunits


@dataclass(frozen=True)
class Approval:
    project_id: str
    session_id: str
    task_id: str
    run_id: str
    generation: int
    role_ids: tuple[str, str, str, str, str]
    goal_hash: str
    baseline: tuple[FileSpec, ...]
    tests: tuple[tuple[str, ...], ...]
    role_limits: tuple[Budget, Budget, Budget, Budget, Budget]
    total_limit: Budget
    issued: float
    expires: float
    commit_allowed: bool


@dataclass(frozen=True)
class ChangeResult:
    approval_ref: str
    audit_tail: str
    after: tuple[FileSpec, ...]


def _verify(approval, result, events, tail, before_bundle, after_bundle, now):
    """Only the controller supplies approval, clock, and the dedicated ledger."""
    if (approval.commit_allowed is not True or not isfinite(now)
            or not isfinite(approval.issued) or not isfinite(approval.expires)
            or not approval.issued <= now < approval.expires):
        raise Blocked("Commit approval missing or expired")
    if (type(result) is not ChangeResult or type(result.after) is not tuple
            or not 1 <= len(result.after) <= MAX_FILES
            or any(type(spec) is not FileSpec or type(spec.path) is not str
                   or type(spec.size) is not int or type(spec.sha256) is not str for spec in result.after)):
        raise Blocked("Malformed change result")
    approval_ref = digest(asdict(approval))
    if result.approval_ref != approval_ref or result.audit_tail != tail:
        raise Blocked("Result does not match the approved execution or audit tail")
    if (len(approval.role_ids) != len(ROLES) or len(set(approval.role_ids)) != len(ROLES)
            or len(approval.role_limits) != len(ROLES) or len(events) != len(ROLES)):
        raise Blocked("Missing, duplicate or replayed role evidence")

    before = dict(inspect_bundle(before_bundle, approval.baseline))
    try:
        after = dict(inspect_bundle(after_bundle, result.after))
    except (ValueError, StructError) as error:
        raise Blocked("Malformed change bundle") from error
    if before.keys() != after.keys() or any(before[path] == after[path] for path in before):
        raise Blocked("Change inventory differs from the approved modified files")
    manifest_ref = digest([asdict(spec) for spec in sorted(result.after, key=lambda spec: spec.path)])

    payloads = []
    usages = []
    for event, role, agent_id, limit in zip(events, ROLES, approval.role_ids, approval.role_limits):
        if (event.get("kind"), event.get("approval_ref"), event.get("role"), event.get("agent_run_id")) != (
                "synthetic_commit_evidence", approval_ref, role, agent_id):
            raise Blocked("Audit evidence belongs to a different execution or role")
        usage = event.get("usage")
        if (not isinstance(usage, list) or len(usage) != 3
                or any(type(value) is not int or not 0 <= value <= maximum
                       for value, maximum in zip(usage, _amounts(limit)))):
            raise Blocked("Role budget exceeded or usage unverified")
        usages.append(usage)
        payloads.append(event.get("evidence"))
    if any(sum(values) > maximum for values, maximum in zip(zip(*usages), _amounts(approval.total_limit))):
        raise Blocked("Total budget exceeded")

    plan, development, review, verification, decision = payloads
    if plan != {"goal_hash": approval.goal_hash} or development != {"manifest_ref": manifest_ref}:
        raise Blocked("Plan or development evidence mismatch")
    if review != {"verdict": "PASS", "manifest_ref": manifest_ref}:
        raise Blocked("Reviewer did not pass the change inventory")
    expected_tests = [{"argv": list(argv), "exit_code": 0} for argv in approval.tests]
    expected_verification = {
        "manifest_ref": manifest_ref, "review_ref": digest(events[2]), "tests": expected_tests,
    }
    if not expected_tests or digest(verification) != digest(expected_verification):
        raise Blocked("Verifier did not pass the approved tests and review")
    if validate_proposal(decision, approval.goal_hash, digest(events[3])) != "COMPLETED":
        raise Blocked("Final Manager did not accept the verified result")
    return manifest_ref


def nominate(ledger, approval, result, before_bundle, after_bundle, *, clock):
    """Record at most one candidate on the same audit chain. Never calls Git."""
    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("Abandoned execution slot")
        events, tail = ledger.snapshot()
        manifest_ref = _verify(approval, result, events, tail, before_bundle, after_bundle, clock())
        receipt = {
            "kind": "synthetic_commit_candidate",
            "approval_ref": result.approval_ref,
            "evidence_tail": tail,
            "manifest_ref": manifest_ref,
        }
        ledger.append(len(events), tail, receipt)
        return receipt


def recheck(ledger, approval, result, receipt, before_bundle, current_bundle, *, clock):
    """Revalidate a recorded candidate against fresh synthetic bytes and approval."""
    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("Abandoned execution slot")
        events, _ = ledger.snapshot()
        if len(events) != len(ROLES) + 1 or events[-1] != receipt:
            raise Blocked("Candidate receipt is stale or unrecorded")
        prefix_tail = ""
        for seq, event in enumerate(events[:-1], 1):
            prefix_tail = digest([seq, prefix_tail, event])
        manifest_ref = _verify(approval, result, events[:-1], prefix_tail, before_bundle, current_bundle, clock())
        if receipt != {"kind": "synthetic_commit_candidate", "approval_ref": result.approval_ref,
                       "evidence_tail": prefix_tail, "manifest_ref": manifest_ref}:
            raise Blocked("Candidate receipt mismatch")
