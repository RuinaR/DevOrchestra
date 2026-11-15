"""Promote a completed, isolated subscription result under a separate owner grant."""
from hashlib import sha256
import os
from pathlib import Path
from time import time
from uuid import uuid4

from .chatgpt_subscription import read_protected, write_protected
from .developer_batch import BATCH_LIMIT, _encode, _matches, diff_text
from .lease import Blocked, execution_slot, held_execution_slot
from .ledger import check_path, digest
from .local_folder import MODE as LOCAL_MODE, inventory
from .owned_artifacts import _remove_owned_tree
from .project_onboarding import _git, _protected, _relative, _scan
from .replica_evidence import _git_identity, changes, fingerprint, snapshot
from .replica_readiness import baseline, original_matches
from .subscription_cycle import _find

REGISTERED_APPLY_BYTES = 512 * 1024


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
    from .registered_cycle import history

    native = history(ledger, project)
    if native and (not end or native[-1] in events[events.index(end) + 1:]):
        return _prepare_registered(events, head, project, expires, source, replica, frozen, onboarding, native)
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
    criteria = session["scope"].get("criteria")
    if criteria is not None and end.get("criteria_status") != dict.fromkeys(criteria, "VERIFIED"):
        raise Blocked("Original write requires evidence for every frozen criterion")
    expected = [("developer", "subscription_developer_result"), *[
        (phase, "subscription_cycle_result") for phase in ("reviewer", "verifier", "manager-final")
    ]]
    if len(end["roles"]) < len(expected):
        raise Blocked("Subscription receipt chain is incomplete")
    receipts = []
    supplemental = session["scope"].get("reverification")
    parent = None
    if supplemental:
        parent = _find(events, "subscription_session_started", project_id=project, run_id=supplemental["parent_run_id"])
        if not parent or digest(parent["scope"]) != parent["session_approval_sha256"]:
            raise Blocked("The original Developer approval is missing")
    for ref, (phase, kind) in zip(end["roles"][-4:], expected, strict=True):
        owner = parent if parent and phase in {"developer", "reviewer"} else session
        event = _find(events, kind, project_id=project, run_id=ref["run_id"],
                      development_session_id=owner["run_id"],
                      session_approval_sha256=owner["session_approval_sha256"], outcome="COMPLETED")
        if not event or ref["phase"] != phase or digest(event) != ref["sha256"] or event.get("owned_tree") != "DRAINED":
            raise Blocked("Subscription receipt chain is incomplete")
        if owner is parent and ref["sha256"] != supplemental[phase + "_sha256"]:
            raise Blocked("The inherited Developer or Reviewer receipt changed")
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
    if runtime["kind"] == "mxc-dotnet-winforms-project-v1":
        from .mxc_owned_gui import verify_images

        scenario = runtime["build"]["gui_scenario"]
        evidence = verify_images(ledger, verifier.get("sdk_gui"), verifier["run_id"], scenario)
        if (evidence["replica_sha256"] != developer["after_replica_sha256"]
                or evidence["scenario_sha256"] != digest(scenario)
                or evidence["dll_sha256"] not in {item["sha256"] for item in verifier["artifacts"].values()}):
            raise Blocked("WinForms build and visual evidence are not bound to the Developer change")
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


def _prepare_registered(events, head, project, expires, source, replica, frozen, onboarding, history):
    start, end = history[0], history[-1]
    scope = start["scope"]
    if (end["kind"] != "native_inspection_result" or end.get("outcome") != "COMPLETED"
            or end.get("owned_tree") != "DRAINED" or digest(scope) != start["approval_sha256"]
            or end["approval_sha256"] != start["approval_sha256"]
            or scope["onboarding_sha256"] != digest(onboarding)
            or scope["replica"] != str(replica)
            or end["criteria_status"] != dict.fromkeys(scope["criteria"], "VERIFIED")):
        raise Blocked("Registered development requires a completed, matching session")
    session_sha = digest(end)
    if _find(events, "original_apply_result", project_id=project, session_sha256=session_sha, result="APPLIED"):
        raise Blocked("This verified session has already been applied")
    final_cycle = end["cycle_count"]
    review = next((row for row in reversed(history) if row["kind"] == "native_inspection_cycle_progress"
                   and row["cycle"] == final_cycle), {})
    verified = next((row for row in reversed(history)
                     if row["kind"] in {"native_inspection_role_result", "native_inspection_verifier_reused"}
                     and row["cycle"] == final_cycle and row["phase"] == "verifier"), {})
    decision = next((row for row in reversed(history) if row["kind"] == "native_inspection_role_result"
                     and row["cycle"] == final_cycle and row["phase"] == "manager-final"), {})
    resume = scope.get("resume", {})
    if (not decision and resume.get("mode") == "postprocess"
            and resume.get("saved_response", {}).get("phase") == "manager-final"
            and any(row["kind"] == "native_inspection_role_reused" and row["cycle"] == final_cycle
                    and row["phase"] == "manager-final" and row["parent_run_id"] == resume["parent_run_id"]
                    and row["message_sha256"] == resume["saved_response"]["message_sha256"]
                    and row["source_sha256"] == end["replica_sha256"] for row in history)):
        applied = next((row for row in reversed(history) if row["kind"] == "native_inspection_decision_applied"
                        and row["cycle"] == final_cycle and row["phase"] == "manager-final"
                        and row["source_sha256"] == end["replica_sha256"]), {})
        if applied:
            decision = {"manager_decision": applied["verdict"], "outcome": "COMPLETED"}
    image = snapshot(replica)
    if (review.get("reviewer") != "ACCEPT" or review.get("controller_decision") != "COMPLETED"
            or decision.get("manager_decision") != "COMPLETED" or decision.get("outcome") != "COMPLETED"
            or verified.get("verdict") != "PASS" or verified.get("owned_tree") != "DRAINED"
            or not (verified.get("source_sha256") == review.get("source_sha256") ==
                    end["replica_sha256"] == digest(image)) or not original_matches(source, frozen)):
        raise Blocked("Registered review, verification or source evidence changed")
    allowed = set(end["changed"] if scope.get("project_edit") else scope["files"])
    paths = changes({**image, "files": frozen["files"]}, image, allowed)
    if not paths:
        raise Blocked("원본에 반영할 변경이 없습니다.")
    if any(value["after"] is None for value in paths.values()):
        raise Blocked("Registered result exceeds the original write scope")
    if sum(part["bytes"] for row in paths.values() for part in row.values() if part) > REGISTERED_APPLY_BYTES:
        raise Blocked(f"원본 반영의 변경 전후 파일 합계가 {REGISTERED_APPLY_BYTES // 1024}KiB를 넘습니다.")
    before, after = {}, {}
    for name, row in paths.items():
        path = source / _relative(name)
        check_path(path)
        if (_protected(name) or name in frozen["staged"] or name in frozen["raw_worktree_changed"]
                or name in frozen["local_examples"] or not path.parent.is_dir()):
            raise Blocked(f"Original path is protected or was already modified: {name}")
        parent = source
        for part in _relative(name).parts:
            aliases = [entry.name for entry in parent.iterdir() if entry.name.casefold() == part.casefold()]
            if aliases and aliases != [part]:
                raise Blocked("Original path casing is ambiguous")
            parent /= part
        before[name] = _read_bound(path, row["before"]) if row["before"] else None
        after[name] = _read_bound(replica / name, row["after"])
        if row["before"] is None and path.exists():
            raise Blocked("Original or registered replica bytes changed")
    return {"project_id": project, "original": str(source), "replica": str(replica), "expires": expires,
            "original_head": frozen["head"], "original_git": frozen["git"],
            **({"development_mode": LOCAL_MODE} if frozen.get("mode") == LOCAL_MODE else {}),
            "original_directory_id": list(_identity(source)), "paths": paths, "ledger_head": head,
            "session_sha256": session_sha, "verifier_sha256": digest(verified),
            "onboarding_sha256": digest(onboarding)}, frozen, before, after


def _identity(path):
    check_path(path)
    info = path.stat()
    return info.st_dev, info.st_ino


def _read_bound(path, expected):
    check_path(path)
    with path.open("rb") as stream:
        data = stream.read(expected["bytes"] + 1)
    if len(data) != expected["bytes"] or sha256(data).hexdigest() != expected["sha256"]:
        raise Blocked("Original or registered replica bytes changed")
    return data


def review(runner, project, expires):
    scope, _, before, after = _prepare(runner, project, expires)
    return scope, diff_text(before, after, after)


def run(runner, project, expires, approved, cancelled=lambda: False, *, held=False):
    from .runtime import require_admission

    with (held_execution_slot() if held else execution_slot()) as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        scope, frozen, before, after = _prepare(runner, project, expires)
        if cancelled() or digest(scope) != approved or head != scope["ledger_head"]:
            raise Blocked("Original write approval is stale")
        source = Path(scope["original"])
        local = scope.get("development_mode") == LOCAL_MODE
        if local:
            matches = inventory(source) == frozen
        else:
            with _git(runner.ledger, source) as (git, _):
                matches = _scan(source, git, frozen["local_examples"]) == frozen and not any(
                    data is None and git("check-ignore", "--no-index", "--", name, allowed=(0, 1)).strip()
                    for name, data in before.items())
        if not matches:
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
            if local:
                expected = {**frozen["files"], **{name: row["after"] for name, row in scope["paths"].items()}}
                exact = inventory(source)["files"] == expected
            else:
                exact = all(fingerprint(source / name) == info for name, info in frozen["files"].items()
                            if name not in after)
            if not exact:
                raise Blocked("Unrelated original files changed during the write")

        payload = {"scope": scope, "before": {name: _encode(data) for name, data in before.items()}}
        try:
            if local:
                rows = {}
                for index, (name, data) in enumerate(after.items()):
                    admit()
                    pending = stage / f"next-{index}"
                    _stage_bytes(pending, data)
                    target = source / name
                    if not _matches(target, before[name]):
                        raise Blocked("원본 파일이 반영 준비 중 변경됐습니다.")
                    rows[name] = {"before_id": list(_identity(target)) if before[name] is not None else None,
                                  "next_id": list(_identity(pending))}
                payload.update(stage_id=list(_identity(stage)), files=rows,
                               order=list(after), baseline_sha256=digest(frozen))
            write_protected(journal, payload)
        except (Blocked, OSError, ValueError, KeyboardInterrupt):
            if not local:
                raise
            result, reason = "BLOCKED", "ORIGINAL_WRITE_PREPARATION_FAILED"
        else:
            result, reason = _write(source, stage, before, after, check, admit, final_check,
                                    prepared=payload.get("files"))
        cleanup = _remove_owned_tree(stage, stage_id)
        if cleanup["status"] != "REMOVED":
            raise Blocked("Original write cleanup is unconfirmed; preserve recovery journal")
        receipt = {**binding, "kind": "original_apply_result", "result": result, "reason": reason,
                   "paths": scope["paths"], "original_head": frozen["head"], "cleanup": cleanup,
                   "verifier_sha256": scope["verifier_sha256"], "session_sha256": scope["session_sha256"]}
        if local and result == "APPLIED":
            receipt["previous_bytes_ref"] = journal.name
        runner.ledger.append(count, fence, receipt, allow_shutdown=True)
        if "previous_bytes_ref" not in receipt:
            journal.unlink(missing_ok=local)
        return receipt


def _stage_bytes(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _write(source, stage, before, after, check, admit, final_check, *, prepared=None):
    if os.name != "nt":
        raise Blocked("Original write requires Windows no-overwrite rename semantics")
    moved, installed = [], []
    try:
        for index, (name, data) in enumerate(after.items()):
            admit()
            pending = stage / f"next-{index}"
            if prepared is None:
                _stage_bytes(pending, data)
            elif (not _matches(pending, data) or list(_identity(pending)) != prepared[name]["next_id"]):
                raise Blocked("반영 준비 파일이 변경됐습니다.")
            target = source / name
            check_path(target)
            if (not _matches(target, before[name]) or (prepared and before[name] is not None
                    and list(_identity(target)) != prepared[name]["before_id"])):
                raise Blocked("Original target changed")
            if before[name] is not None:
                backup = stage / f"old-{index}"
                os.rename(target, backup)
                moved.append((name, backup))
                if (not _matches(backup, before[name])
                        or (prepared and list(_identity(backup)) != prepared[name]["before_id"])):
                    raise Blocked("Original bytes changed during the write")
            os.rename(pending, target)
            installed.append(name)
        final_check()
        if not all(_matches(source / name, data)
                   and (not prepared or list(_identity(source / name)) == prepared[name]["next_id"])
                   for name, data in after.items()):
            raise Blocked("Original result hash did not match verification")
    except (Exception, KeyboardInterrupt) as error:
        reason = "CANCELLED" if isinstance(error, KeyboardInterrupt) else str(error)
        if not installed and not moved:
            return "BLOCKED", reason
        check()
        for name in reversed(installed):
            target = source / name
            if (not _matches(target, after[name])
                    or (prepared and list(_identity(target)) != prepared[name]["next_id"])):
                raise Blocked("Rollback ownership is unknown; preserve recovery files") from error
            target.unlink()
        for name, backup in reversed(moved):
            check_path(source / name)
            if (not _matches(backup, before[name])
                    or (prepared and list(_identity(backup)) != prepared[name]["before_id"])):
                raise Blocked("Rollback backup changed; preserve recovery files") from error
            os.rename(backup, source / name)
        touched = set(installed) | {name for name, _ in moved}
        if not all(_matches(source / name, before[name]) for name in touched):
            raise Blocked("Rollback outcome is unknown; preserve recovery files") from error
        return "ROLLED_BACK", reason
    return "APPLIED", None


def _recovery_plan(runner, project):
    from .runtime import _native_pending

    ledger = runner.ledger
    events, tail = ledger.snapshot()
    pending = _native_pending(events)
    intent = next((row for row in reversed(events) if row.get("project_id") == project
                   and row["kind"] == "original_apply_intent"
                   and ("original-apply", row["run_id"]) in pending), None)
    if (not intent or pending != {("original-apply", intent["run_id"])}
            or intent["scope"].get("development_mode") != LOCAL_MODE
            or digest(intent["scope"]) != intent["scope_sha256"]):
        raise Blocked("복구할 일반 폴더 원본 반영 기록이 없습니다.")
    # This transaction spawns no processes. Allow recovery only for this exact
    # unclosed intent while all other admission gates still pass.
    runner._check_idle_diagnostic([row for row in events if row is not intent], False)
    scope = intent["scope"]
    source, _, frozen, receipt = baseline(ledger, project)
    if str(source) != scope["original"] or digest(receipt) != scope["onboarding_sha256"]:
        raise Blocked("원본 반영의 등록 기준점이 변경됐습니다.")
    run_id = intent["run_id"]
    if (intent["stage"] != f".devorchestra-apply-{run_id}"
            or intent["journal_ref"] != f"original-apply-{run_id}.dpapi"):
        raise Blocked("원본 반영 저널 경계가 변경됐습니다.")
    journal = ledger.local_directory("recovery") / intent["journal_ref"]
    data = read_protected(journal)
    if (data.get("scope") != scope or data.get("baseline_sha256") != digest(frozen)
            or list(_identity(source)) != scope["original_directory_id"]
            or set(data["order"]) != set(data["files"]) or set(data["order"]) != set(scope["paths"])
            or len(data["order"]) != len(scope["paths"])):
        raise Blocked("원본 반영 저널과 승인된 매니페스트가 다릅니다.")
    stage = source / intent["stage"]
    check_path(stage)
    stage_present = stage.exists()
    if stage_present and list(_identity(stage)) != data["stage_id"]:
        raise Blocked("원본 반영 임시 폴더의 소유권을 확인할 수 없습니다.")
    expected = dict(frozen["files"])
    states, owned_names = {}, set()
    for index, name in enumerate(data["order"]):
        pair = scope["paths"][name]
        identities = data["files"][name]
        target = source / _relative(name)
        backup = stage / f"old-{index}"
        staged = stage / f"next-{index}"
        old_id, new_id = identities["before_id"], identities["next_id"]
        check_path(target)
        check_path(backup)
        check_path(staged)
        if staged.exists():
            if fingerprint(staged) != pair["after"] or list(_identity(staged)) != new_id:
                raise Blocked("복구 후보 파일이 변경됐습니다.")
            owned_names.add(staged.name)
        if backup.exists():
            if fingerprint(backup) != pair["before"] or list(_identity(backup)) != old_id:
                raise Blocked("복구용 원본 백업이 변경됐습니다.")
            owned_names.add(backup.name)
        if target.exists():
            observed = (fingerprint(target), list(_identity(target)))
            if observed == (pair["before"], old_id) and not backup.exists():
                state = "OLD"
            elif observed == (pair["after"], new_id) and not staged.exists() and (
                    old_id is None or backup.exists() or not stage_present):
                state = "NEW"
                expected[name] = pair["after"]
            else:
                raise Blocked("반영 중 사용자가 수정한 파일이 있습니다.")
        elif old_id is None or backup.exists():
            state = "MOVED" if backup.exists() else "OLD"
            expected.pop(name, None)
        else:
            raise Blocked("복구에 필요한 원본 파일이 없습니다.")
        states[name] = state
    if (stage_present and {path.name for path in stage.iterdir()} != owned_names
            or inventory(source)["files"] != expected):
        raise Blocked("반영 대상 외의 파일 또는 임시 폴더가 변경됐습니다.")
    completed = set(states.values()) == {"NEW"}
    if not completed and not stage_present and set(states.values()) != {"OLD"}:
        raise Blocked("부분 반영 복구에 필요한 백업이 없습니다.")
    review = {"project_id": project, "run_id": run_id, "ledger_head": tail, "states": states,
              "stage_present": stage_present, "journal_sha256": digest(data),
              "action": "CONFIRM_APPLIED" if completed else "ROLL_BACK"}
    return review, len(events), intent, source, frozen, stage, journal, data


def review_recovery(runner, project):
    with execution_slot():
        return _recovery_plan(runner, project)[0]


def recover(runner, project, approval):
    with execution_slot():
        review, count, intent, source, frozen, stage, journal, data = _recovery_plan(runner, project)
        if digest(review) != approval:
            raise Blocked("원본 반영 복구 검토가 변경됐습니다.")
        scope = intent["scope"]
        result = "APPLIED" if review["action"] == "CONFIRM_APPLIED" else "ROLLED_BACK"
        if result == "ROLLED_BACK":
            for index, name in reversed(list(enumerate(data["order"]))):
                runner._check_diagnostic_fence(count, review["ledger_head"])
                current = review["states"][name]
                if current == "OLD":
                    continue
                target = source / _relative(name)
                pair = scope["paths"][name]
                identities = data["files"][name]
                if list(_identity(stage)) != data["stage_id"]:
                    raise Blocked("원본 반영 임시 폴더의 소유권이 변경됐습니다.")
                check_path(target)
                if current == "NEW":
                    if (fingerprint(target) != pair["after"]
                            or list(_identity(target)) != identities["next_id"]):
                        raise Blocked("반영 중 사용자가 수정한 파일이 있습니다.")
                    pending = stage / f"next-{index}"
                    check_path(pending)
                    os.rename(target, pending)
                if current in {"NEW", "MOVED"} and pair["before"] is not None:
                    backup = stage / f"old-{index}"
                    if (fingerprint(backup) != pair["before"]
                            or list(_identity(backup)) != identities["before_id"]):
                        raise Blocked("복구용 원본 백업이 변경됐습니다.")
                    os.rename(backup, target)
        runner._check_diagnostic_fence(count, review["ledger_head"])
        expected = dict(frozen["files"])
        if result == "APPLIED":
            expected.update((name, pair["after"]) for name, pair in scope["paths"].items())
        if inventory(source)["files"] != expected:
            raise Blocked("복구 후 원본 매니페스트가 예상과 다릅니다.")
        cleanup = (_remove_owned_tree(stage, data["stage_id"][1]) if stage.exists()
                   else {"status": "REMOVED"})
        if cleanup["status"] != "REMOVED":
            raise Blocked("복구 준비 폴더의 정리가 확인되지 않았습니다.")
        event = {key: intent[key] for key in ("project_id", "run_id", "state", "scope_sha256")}
        event.update(kind="original_apply_result", result=result, reason="RECOVERED_AFTER_INTERRUPTION",
                     paths=scope["paths"], original_head=None, cleanup=cleanup, recovery_approval=approval,
                     verifier_sha256=scope["verifier_sha256"], session_sha256=scope["session_sha256"])
        if result == "APPLIED":
            event["previous_bytes_ref"] = journal.name
        runner.ledger.append(count, review["ledger_head"], event, allow_shutdown=True)
        if "previous_bytes_ref" not in event:
            journal.unlink()
        return event
