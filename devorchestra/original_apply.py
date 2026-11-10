"""Promote a completed, isolated subscription result under a separate owner grant."""
from hashlib import sha256
import os
from pathlib import Path
from time import time
from uuid import uuid4

from .chatgpt_subscription import write_protected
from .developer_batch import BATCH_LIMIT, _encode, _matches, diff_text
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .owned_artifacts import _remove_owned_tree
from .project_onboarding import _git, _protected, _relative, _scan
from .replica_evidence import _git_identity, changes, fingerprint, snapshot
from .replica_readiness import baseline, original_matches
from .subscription_cycle import _find


def _prepare(runner, project, expires):
    from .runtime import require_admission

    if type(expires) is not int or not time() < expires <= time() + 300:
        raise Blocked("Original write approval must expire within five minutes")
    ledger = runner.ledger
    events, head = ledger.snapshot()
    require_admission(events)
    source, replica, frozen, onboarding = baseline(ledger, project)
    if source == replica or source.is_relative_to(replica) or replica.is_relative_to(source):
        raise Blocked("Original and replica must be separate trees")
    end = _find(events, "subscription_session_ended", project_id=project)
    if not end or end.get("outcome") != "COMPLETED" or end.get("owned_tree") != "DRAINED":
        raise Blocked("A completed subscription session is required")
    session_sha256 = digest(end)
    if _find(events, "original_apply_result", project_id=project, session_sha256=session_sha256, result="APPLIED"):
        raise Blocked("This verified session has already been applied")
    session = _find(events, "subscription_session_started", project_id=project, run_id=end["run_id"])
    if (not session or session["scope"]["replica"] != str(replica)
            or session["scope"]["replica_sha256"] != onboarding["replica_sha256"]
            or digest(session["scope"]) != session["session_approval_sha256"]
            or end["session_approval_sha256"] != session["session_approval_sha256"]):
        raise Blocked("Development must start from this exact onboarding baseline")
    expected = [("developer", "subscription_developer_result"), *[
        (phase, "subscription_cycle_result") for phase in ("reviewer", "verifier", "manager-final")
    ]]
    if len(end["roles"]) < len(expected):
        raise Blocked("Subscription receipt chain is incomplete")
    receipts = []
    for ref, (phase, kind) in zip(end["roles"][-4:], expected, strict=True):
        event = _find(events, kind, project_id=project, run_id=ref["run_id"],
                      development_session_id=session["run_id"],
                      session_approval_sha256=session["session_approval_sha256"], outcome="COMPLETED")
        if not event or ref["phase"] != phase or digest(event) != ref["sha256"] or event.get("owned_tree") != "DRAINED":
            raise Blocked("Subscription receipt chain is incomplete")
        receipts.append(event)
    developer, reviewer, verifier, final = receipts
    runtime = session["scope"].get("runtime")
    if (not runtime or verifier.get("runtime_manifest_sha256") != digest(runtime)
            or verifier.get("verdict") != "PASS" or verifier.get("exit_code") != 0
            or verifier.get("job_active") != 0 or not verifier.get("artifacts")
            or reviewer.get("decision") != "ACCEPT_FOR_VERIFICATION" or final.get("decision") != "COMPLETED"
            or any(event.get("developer_sha256") != digest(developer) for event in receipts[1:])
            or verifier.get("reviewer_sha256") != digest(reviewer)
            or final.get("verifier_sha256") != digest(verifier)):
        raise Blocked("A matching isolated build and test PASS is required")
    image = snapshot(replica)
    if digest(image) != developer["after_replica_sha256"] or not original_matches(source, frozen):
        raise Blocked("Original Git, worktree, or verified replica changed")
    paths = changes({**image, "files": frozen["files"]}, image, set(session["scope"]["writable_files"]))
    if (any(value["after"] is None for value in paths.values())
            or any(verifier.get("source_files_sha256", {}).get(name) != row["after"]["sha256"]
                   for name, row in paths.items())
            or sum(sum(part["bytes"] for part in row.values() if part) for row in paths.values()) > BATCH_LIMIT):
        raise Blocked("The verified result exceeds the original write scope")
    before, after = {}, {}
    for name, row in paths.items():
        target = source / _relative(name)
        check_path(target)
        if (_protected(name) or name in frozen["staged"] or name in frozen["raw_worktree_changed"]
                or name in frozen["local_examples"] or not target.parent.is_dir()):
            raise Blocked(f"Original path is protected or has pre-existing edits: {name}")
        # Compare exact directory entries, including aliases on case-insensitive filesystems.
        parent = source
        for part in _relative(name).parts:
            aliases = [entry.name for entry in parent.iterdir() if entry.name.casefold() == part.casefold()]
            if aliases and aliases != [part]:
                raise Blocked(f"Original path casing is ambiguous: {name}")
            parent /= part
        before[name] = target.read_bytes() if row["before"] else None
        after[name] = (replica / name).read_bytes()
        if ((row["before"] is None and target.exists())
                or any(sha256(data).hexdigest() != expected["sha256"] for data, expected in
                       ((before[name], row["before"]), (after[name], row["after"])) if expected)):
            raise Blocked(f"Original or replica bytes changed: {name}")
    scope = {"project_id": project, "original": str(source), "replica": str(replica), "expires": expires,
             "original_head": frozen["head"], "original_git": frozen["git"],
             "original_directory_id": list(_identity(source)), "paths": paths,
             "ledger_head": head, "session_sha256": session_sha256, "verifier_sha256": digest(verifier),
             "onboarding_sha256": digest(onboarding)}
    return scope, frozen, before, after


def _identity(path):
    check_path(path)
    info = path.stat()
    return info.st_dev, info.st_ino


def review(runner, project, expires):
    scope, _, before, after = _prepare(runner, project, expires)
    return scope, diff_text(before, after, after)


def run(runner, project, expires, approved, cancelled=lambda: False):
    from .runtime import require_admission

    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        scope, frozen, before, after = _prepare(runner, project, expires)
        if cancelled() or digest(scope) != approved or head != scope["ledger_head"]:
            raise Blocked("Original write approval is stale")
        source = Path(scope["original"])
        with _git(runner.ledger, source) as (git, _):
            inventory = _scan(source, git, frozen["local_examples"])
            ignored = any(data is None and git("check-ignore", "--no-index", "--", name, allowed=(0, 1)).strip()
                          for name, data in before.items())
        if inventory != frozen or ignored:
            raise Blocked("Original Git inventory changed or an approved new path is ignored")
        identifier = uuid4().hex
        stage = source / f".devorchestra-apply-{identifier}"
        journal = runner.ledger.local_directory("recovery", create=True) / f"original-apply-{identifier}.dpapi"
        binding = {"project_id": project, "run_id": identifier, "state": "DIAGNOSTIC", "scope_sha256": approved}
        count, fence = runner.ledger.append(len(events), head, {
            **binding, "kind": "original_apply_intent", "scope": scope, "stage": stage.name,
            "journal_ref": journal.name,
        }, admission=require_admission)
        stage.mkdir()
        stage_id = stage.stat().st_ino
        write_protected(journal, {"scope": scope, "before": {name: _encode(data) for name, data in before.items()}})

        def check():
            runner._check_diagnostic_fence(count, fence)
            if (_identity(source) != tuple(scope["original_directory_id"])
                    or _git_identity(source) != scope["original_git"]
                    or _identity(stage)[1] != stage_id):
                raise Blocked("Original Git or worktree identity changed")

        def admit():
            check()
            if cancelled() or time() >= expires:
                raise Blocked("Original write cancelled or approval expired")

        def final_check():
            admit()
            if any(fingerprint(source / name) != info for name, info in frozen["files"].items()
                   if name not in after):
                raise Blocked("Unrelated original files changed during the write")

        result, reason = _write(source, stage, before, after, check, admit, final_check)
        cleanup = _remove_owned_tree(stage, stage_id)
        if cleanup["status"] != "REMOVED":
            raise Blocked("Original write cleanup is unconfirmed; preserve recovery journal")
        receipt = {**binding, "kind": "original_apply_result", "result": result, "reason": reason,
                   "paths": scope["paths"], "original_head": frozen["head"], "cleanup": cleanup,
                   "verifier_sha256": scope["verifier_sha256"], "session_sha256": scope["session_sha256"]}
        runner.ledger.append(count, fence, receipt, allow_shutdown=True)
        journal.unlink()
        return receipt


def _write(source, stage, before, after, check, admit, final_check):
    if os.name != "nt":
        raise Blocked("Original write requires Windows no-overwrite rename semantics")
    moved, installed = [], []
    try:
        for index, (name, data) in enumerate(after.items()):
            admit()
            with (stage / f"next-{index}").open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            target = source / name
            check_path(target)
            if not _matches(target, before[name]):
                raise Blocked("Original target changed")
            if before[name] is not None:
                backup = stage / f"old-{index}"
                os.rename(target, backup)
                moved.append((name, backup))
                if not _matches(backup, before[name]):
                    raise Blocked("Original bytes changed during the write")
            os.rename(stage / f"next-{index}", target)
            installed.append(name)
        final_check()
        if not all(_matches(source / name, data) for name, data in after.items()):
            raise Blocked("Original result hash did not match verification")
    except (Exception, KeyboardInterrupt) as error:
        reason = "CANCELLED" if isinstance(error, KeyboardInterrupt) else str(error)
        if not installed and not moved:
            return "BLOCKED", reason
        check()
        for name in reversed(installed):
            target = source / name
            if not _matches(target, after[name]):
                raise Blocked("Rollback ownership is unknown; preserve recovery files") from error
            target.unlink()
        for name, backup in reversed(moved):
            check_path(source / name)
            os.rename(backup, source / name)
        touched = set(installed) | {name for name, _ in moved}
        if not all(_matches(source / name, before[name]) for name in touched):
            raise Blocked("Rollback outcome is unknown; preserve recovery files") from error
        return "ROLLED_BACK", reason
    return "APPLIED", None
