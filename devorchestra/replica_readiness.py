"""Bind actual build evidence and read-only change review to Git onboarding."""
from pathlib import Path

from .lease import Blocked
from .ledger import check_path, digest
from .project_onboarding import _protected, _registered, _relative
from .replica_evidence import _git_identity, fingerprint, snapshot

BOUNDARIES = {"compiler_read_denied": True, "compiler_write_denied": True,
              "runtime_source_write_denied": True, "runtime_outside_read_write_denied": True,
              "runtime_loopback_error": 10013, "child_read_denied": True}
SCOPE = "TWO_CSHARP_ARITHMETIC_MODULES_OFFLINE"


def baseline(ledger, project):
    catalog, row, source = _registered(ledger, project)
    events = ledger.read()
    last = next((e for e in reversed(events) if e.get("project_id") == project
                 and e["kind"] in {"onboarding_result", "onboarding_intent"}), None)
    if not last or last["kind"] != "onboarding_result":
        raise Blocked("An established Git onboarding baseline is required")
    intent = next((e for e in reversed(events) if e["kind"] == "onboarding_intent"
                   and e["run_id"] == last["run_id"] and e.get("project_id") == project), None)
    if (not intent or digest(intent["scope"]) != last["scope_sha256"]
            or str(source) != intent["scope"]["source"]
            or row["revision"] != last["project_revision"]
            or catalog["setups"].get(project, {}).get("replica") != last["replica"]):
        raise Blocked("Onboarding baseline or registered paths changed")
    replica = Path(last["replica"])
    check_path(replica)
    if not replica.is_dir():
        raise Blocked("등록된 복제본 폴더가 없습니다. ‘처음부터 다시 시작’으로 복제본을 만드세요.")
    check_path(replica / ".git")
    if not (replica / ".git").is_dir():
        raise Blocked("등록된 복제본의 Git 기준이 없습니다. ‘처음부터 다시 시작’으로 복제본을 만드세요.")
    return source, replica, intent["scope"]["baseline"], last


def original_matches(source, frozen):
    return (_git_identity(source) == frozen["git"]
            and all(fingerprint(source / _relative(name)) == info for name, info in frozen["files"].items()))


def bind(ledger, project, scope):
    source, replica, frozen, receipt = baseline(ledger, project)
    if (str(replica) != scope["replica"] or scope["replica_sha256"] != receipt["replica_sha256"]
            or not original_matches(source, frozen)):
        raise Blocked("Original or isolated replica changed since onboarding")
    return {"onboarding_sha256": digest(receipt), "original_head": receipt["original_head"],
            "replica_head": receipt["replica_head"], "coverage": SCOPE,
            "environment": "ENVIRONMENT_READY",
            "dependency_scope": "PINNED_DOTNET_RUNTIME_AND_REFERENCE_PACK_NO_RESTORE"}


def evidence_passed(receipt):
    scope = receipt["scope"]
    expected = [tuple(pair) for pair in scope["vectors"]]
    rows = receipt.get("observations", [])
    steps = receipt.get("steps", {})
    return (receipt.get("result") == "PASS" and receipt.get("job_active") == 0
            and receipt.get("original_rechecked") is True
            and receipt.get("cleanup", {}).get("status") == "REMOVED"
            and receipt.get("boundaries") == BOUNDARIES and bool(expected)
            and [(row.get("input"), row.get("expected")) for row in rows] == expected
            and all(row.get("actual") == row.get("expected") for row in rows)
            and steps.get("compile", {}).get("exitCode") == 0
            and all(steps.get(f"run-{index}", {}).get("exitCode") == 0 for index in range(len(expected)))
            and {"app.dll", "app.runtimeconfig.json"} <= receipt.get("artifacts", {}).keys())


def project_status(ledger, project, onboarding):
    from .mxc_dotnet import ONBOARDED_KIND, review

    events = ledger.read()
    latest = next((e for e in reversed(events) if e.get("project_id") == project
                   and e["kind"] in {"build_verifier_intent", "build_verifier_result"}
                   and e.get("scope", {}).get("kind") == ONBOARDED_KIND
                   and e["scope"].get("readiness", {}).get("onboarding_sha256") == digest(onboarding)), None)
    if not latest:
        return {}
    if latest["kind"] == "build_verifier_intent":
        return {"status": "BLOCKED", "reason": "Onboarded build has no settled receipt"}
    scope = latest["scope"]
    try:
        current = review(Path(scope["replica"]), scope["launcher"], list(scope["sources"]), scope["vectors"],
                         scope["limits"]["max_seconds"], Path(scope["tool_root_read_only"]),
                         arithmetic=True, nested=True)
        if (digest(scope) != latest["scope_sha256"] or any(scope[key] != value for key, value in current.items())
                or bind(ledger, project, current) != scope["readiness"]):
            raise Blocked("Readiness evidence is stale")
    except (Blocked, OSError, ValueError):
        return {"status": "BLOCKED", "reason": "Toolchain, original, or replica no longer matches the build receipt"}
    ready = evidence_passed(latest)
    return {"status": "DEVELOPMENT_READY" if ready else "ENVIRONMENT_READY", "coverage": SCOPE,
            "environment": "ENVIRONMENT_READY", "development": "DEVELOPMENT_READY" if ready else "NOT_VALIDATED",
            "sdk": scope["sdk"], "runtime": scope["runtime"], "reference_pack": scope["environment_selection"]["reference_pack"],
            "tool_sha256": scope["tool_sha256"][scope["tool"]], "compiler_version": latest.get("compiler_version"),
            "build_result": latest["result"], "test_results": latest.get("observations", []),
            "build_receipt_sha256": digest(latest), "cleanup": latest.get("cleanup", {}).get("status"),
            "reason": latest.get("reason"), "development_authority": False}


def changes_review(ledger, project):
    """Map raw bytes to the frozen original. Never apply or stage changes."""
    source, replica, frozen, receipt = baseline(ledger, project)
    image = snapshot(replica)
    old, current = frozen["files"], image["files"]
    original_git_matches = _git_identity(source) == frozen["git"]
    try:
        original_baseline_matches = original_git_matches and original_matches(source, frozen)
    except (Blocked, OSError):
        original_baseline_matches = False
    rows = []
    for name in sorted(old.keys() | current.keys()):
        if old.get(name) == current.get(name):
            continue
        target = source / _relative(name)
        check_path(target)
        before = old.get(name)
        after = current.get(name)
        if _protected(name):
            action = "PROTECTED"
        elif not original_git_matches or (target.exists() and before is None):
            action = "CONFLICT"
        elif before is not None and (not target.is_file() or fingerprint(target) != before):
            action = "CONFLICT"
        else:
            action = "CREATED" if before is None else "DELETED" if after is None else "MODIFIED"
        rows.append({"path": name, "change": action, "baseline": before, "replica": after,
                     "original_was_staged": name in frozen["staged"],
                     "original_was_modified": name in frozen["raw_worktree_changed"]})
    return {"project_id": project, "onboarding_sha256": digest(receipt), "original_head": receipt["original_head"],
            "replica_head": receipt["replica_head"], "replica_sha256": digest(image),
            "original_git_matches": original_git_matches, "original_baseline_matches": original_baseline_matches,
            "paths": rows, "verification": ("BASELINE_ONLY" if digest(image) == receipt["replica_sha256"]
                                             else "CHANGE_NOT_VERIFIED"), "apply_authority": False}
