"""One owner approval for a bounded subscription development task."""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from . import subscription_cycle as cycle, subscription_developer as developer, subscription_gui, subscription_manager as manager
from .catalog import Catalog
from .developer_batch import BATCH_LIMIT, check_names
from .default_instructions import public_instruction
from .lease import Blocked, development_session_scope, execution_slot, held_execution_slot
from .ledger import digest
from .python_vectors import validate_vectors
from .replica_evidence import snapshot
from . import csharp_sources, mxc_sdk_project, session_permissions


def review(runner, project, account, expires, seconds, tokens, reworks, vectors, *, permissions=None, cycles=None):
    from .runtime import require_admission

    if (type(expires) is not int or not time() < expires <= time() + 300
            or (seconds is None and tokens is None)
            or (seconds is not None and (type(seconds) is not int or not 1 <= seconds <= 300))
            or (tokens is not None and (type(tokens) is not int or not 1 <= tokens <= 100_000))
            or type(reworks) is not int or not 0 <= reworks <= 5
            or (cycles is not None and (type(cycles) is not int or not 1 <= cycles <= 100))):
        raise Blocked("Invalid session expiry, time, token or rework limit")
    events, head = runner.ledger.snapshot()
    require_admission(events)
    baseline = runner.native_manager_context(project)
    gui = isinstance(vectors, dict) and "gui" in vectors
    batch = isinstance(vectors, dict) and "files" in vectors
    runtime_kind = vectors["runtime"].get("kind") if batch and isinstance(vectors.get("runtime"), dict) else None
    winforms = runtime_kind == mxc_sdk_project.GUI_KIND
    sdk_project = runtime_kind in {mxc_sdk_project.KIND, mxc_sdk_project.GUI_KIND}
    csharp = sdk_project or runtime_kind == csharp_sources.KIND
    authorization = session_permissions.contract(permissions, runtime=batch and "runtime" in vectors,
                                                 gui=gui or winforms)
    if not gui and not batch:
        validate_vectors(vectors, baseline["acceptance"])
    catalog = Catalog(runner.ledger).read()
    data = catalog["projects"][project]["data"]
    setup = catalog["setups"].get(project)
    limits = catalog["global_budget"]
    seconds = min(300, limits["seconds"], data["budget"]["seconds"]) if seconds is None else seconds
    tokens = min(100_000, limits["tokens"], data["budget"]["tokens"]) if tokens is None else tokens
    if (seconds > min(limits["seconds"], data["budget"]["seconds"])
            or tokens > min(limits["tokens"], data["budget"]["tokens"])):
        raise Blocked("Session exceeds registered project or global limits")
    files = baseline["proposed_files_ceiling"]
    if batch:
        if (not setup or set(vectors) not in ({"files", "create"}, {"files", "create", "runtime"})
                or type(vectors["files"]) is not dict or set(vectors["files"]) != set(files)):
            raise Blocked("Batch Verifier requires vectors for every approved file")
        check_names(files, vectors["create"], "cs" if csharp else "py")
        if sdk_project:
            if (set(vectors["runtime"]) != {"kind", "launcher", "project", "scenario" if winforms else "tests"}
                    or type(vectors["runtime"]["launcher"]) is not str
                    or any(item != baseline["acceptance"] for item in vectors["files"].values())):
                raise Blocked("SDK projects require a fixed test entry and frozen acceptance")
        elif csharp:
            if ({Path(name).name for name in files} != csharp_sources.FILES or len(files) != len(csharp_sources.FILES)
                    or vectors["create"]
                    or set(vectors["runtime"]) != {"kind", "launcher", "vectors"}
                    or any(item != baseline["acceptance"] for item in vectors["files"].values())):
                raise Blocked("C# runtime requires two existing approved arithmetic files and frozen acceptance")
        else:
            for spec in vectors["files"].values():
                validate_vectors(spec, baseline["acceptance"])
        if any(not (Path(setup["replica"]) / name).parent.is_dir() for name in files):
            raise Blocked("Approved file parents must already exist in the replica")
    elif (not setup or len(files) != 1 or "/" in files[0]
            or (files[0] != "gui-test.json" if gui else not files[0].endswith(".py"))):
        raise Blocked("Select one registered Python file or the approved data-only GUI descriptor")
    image = snapshot(Path(setup["replica"]))
    missing = set(files) - image["files"].keys()
    if not image["git"] or not image["git"].get("index") or missing != set(vectors["create"] if batch else ()):
        raise Blocked("The session requires an existing file and local Git index")
    model_by_role = {name: item["model"] for name, item in data["roles"].items()}
    if any(model_by_role[role] in ("mock-v1", "deterministic") for role in ("Manager", "Developer", "Reviewer")):
        raise Blocked("Register real subscription models for Manager, Developer and Reviewer")
    manager_review = manager.review(runner, project, account, model_by_role["Manager"], expires)[0]
    sections = ("global", "project", "manager-planning", "developer", "reviewer", "verifier", "manager-final")
    scope = {"contract": "subscription-session-v1", "project_id": project, "account": account,
            "generation": len(events) + 1, "ledger_head": head, "expires": expires,
            "seconds": seconds, "tokens": tokens, "work_limit": cycles, "max_reworks": reworks,
            "replica": setup["replica"], "git_head": developer._head(Path(setup["replica"])),
            "replica_sha256": digest(image), "file": files[0], "readable_files": files, "writable_files": files,
            "baseline_sha256": digest(baseline), "manager_request_sha256": manager_review["request_sha256"],
            "goal": baseline["goal"], "acceptance": baseline["acceptance"],
            "vectors_sha256": digest(vectors), "models": model_by_role,
            "permissions": authorization,
            "backend": manager.BACKEND, "tools": ["controller-replace-one-file", "fixed-python-vectors-v1"],
            "instructions": {name: public_instruction(name)["sha256"] for name in sections}}
    if gui:
        replica = Path(setup["replica"])
        data = developer._read(replica / files[0], developer.LIMIT)
        if sha256(data).hexdigest() != image["files"][files[0]]["sha256"]:
            raise Blocked("GUI descriptor changed during session review")
        scope["gui_contract"] = subscription_gui.contract(vectors, project, replica, baseline["project_revision"], data)
        scope["tools"] = ["controller-replace-one-file", subscription_gui.TOOL]
    if batch:
        scope["batch"] = {"max_files": len(files), "formats": [".cs" if csharp else ".py"], "create": vectors["create"],
                          "delete": False, "move": False, "max_bytes": BATCH_LIMIT,
                          "before_sha256": {name: image["files"].get(name, {}).get("sha256") for name in files}}
        scope["tools"] = ["controller-replace-batch", "fixed-python-vectors-v1"]
        if sdk_project:
            from .dotnet_install import registered

            replica = Path(scope["replica"])
            spec = vectors["runtime"]
            build = mxc_sdk_project.review(replica, spec["launcher"], [spec["project"]],
                                           spec["scenario" if winforms else "tests"], min(seconds, 90),
                                           registered(runner.ledger, project, replica), winforms=winforms)
            test_root = Path(build["entry"]).parent
            library_roots = [Path(name).parent for name in build["graph"] if name != build["entry"]]
            if winforms:
                library_roots.append(test_root)
            if any(not any(Path(name).is_relative_to(root) for root in library_roots) for name in files):
                raise Blocked("Developer SDK scope must be inside a library project and outside frozen tests")
            scope["runtime"] = {"kind": runtime_kind, "build": build}
            if winforms:
                from .mxc_owned_gui import verify_images

                baseline_gui = next((event for event in reversed(events)
                                     if event["kind"] == "build_verifier_result"
                                     and event["project_id"] == project and event.get("sdk_gui")
                                     and event.get("job_active") == 0
                                     and event.get("cleanup", {}).get("status") == "REMOVED"
                                     and all(event["scope"].get(key) == build[key] for key in (
                                         "kind", "replica_sha256", "entry", "gui_scenario", "toolchain_sha256",
                                     ))), None)
                if baseline_gui:
                    evidence = verify_images(runner.ledger, baseline_gui["sdk_gui"], baseline_gui["run_id"],
                                             build["gui_scenario"], require_pass=False)
                    scope["runtime"]["baseline_gui"] = {"run_id": baseline_gui["run_id"],
                                                       "receipt_sha256": digest(baseline_gui), "evidence": evidence}
            scope["tools"] = ["controller-replace-batch", runtime_kind]
        elif csharp:
            from . import mxc_dotnet
            from .dotnet_install import registered

            replica = Path(scope["replica"])
            spec = vectors["runtime"]
            if type(spec["launcher"]) is not str:
                raise Blocked("C# runtime requires a pinned launcher path")
            scope["runtime"] = {"kind": csharp_sources.KIND, "build": mxc_dotnet.review(
                replica, spec["launcher"], files, spec["vectors"], min(seconds, 20),
                registered(runner.ledger, project, replica), arithmetic=True,
                nested=any("/" in name for name in files),
            )}
            scope["tools"] = ["controller-replace-batch", csharp_sources.KIND]
        elif "runtime" in vectors:
            from . import mxc_python

            scope["runtime"] = mxc_python.manifest(vectors["runtime"])
            if "imports" in scope["runtime"] and set(scope["runtime"]["imports"]) != set(files):
                raise Blocked("MXC module manifest does not match the approved file list")
            scope["tools"] = ["controller-replace-batch", scope["runtime"]["kind"]]
    return scope


class _Session:
    def __init__(self, runner, scope, session_id, approval, cancelled):
        self.runner, self.scope = runner, scope
        self.id, self.approval = session_id, approval
        self.cancelled = cancelled
        self.started_at = monotonic()
        self.deadline = self.started_at + scope["seconds"]
        self.remaining = scope["tokens"]
        self.reported = 0
        self.usage_complete = True
        self.phase, self.rework = None, None
        self.previous_cycle = None
        self.replica_sha256 = scope["replica_sha256"]

    def binding(self):
        return {"development_session_id": self.id, "session_approval_sha256": self.approval}

    def slot(self):
        return held_execution_slot()

    def stopped(self):
        return self.stop_reason() is not None

    def stop_reason(self):
        if self.cancelled():
            return "CANCELLED"
        if monotonic() >= self.deadline:
            return "TIME_LIMIT"
        if time() >= self.scope["expires"]:
            return "APPROVAL_EXPIRED"
        return None

    def limit(self, budget, project, phase):
        if reason := self.stop_reason():
            raise Blocked(reason)
        if project != self.scope["project_id"] or phase != self.phase:
            raise Blocked("Session does not permit this project or role")
        self.check_permissions()
        seconds = int(min(self.deadline - monotonic(), self.scope["expires"] - time()))
        if seconds < 1:
            raise Blocked("TIME_LIMIT")
        if self.remaining <= 0 and phase != "verifier":
            raise Blocked("TOKEN_LIMIT")
        budget["seconds"] = min(budget["seconds"], seconds)
        budget["tokens"] = min(budget["tokens"], self.remaining)

    def check_permissions(self):
        if (contract := self.scope.get("permissions")) is not None:
            needed = session_permissions.phases(runtime=bool(self.scope.get("runtime")),
                                                gui=bool(self.scope.get("gui_contract"))
                                                or self.scope.get("runtime", {}).get("kind") == mxc_sdk_project.GUI_KIND)
            if self.phase not in needed or not needed[self.phase] <= set(contract["granted"]):
                raise Blocked("Role requires permissions outside the approved contract")

    def check_scope(self):
        if reason := self.stop_reason():
            raise Blocked(reason)
        self.check_permissions()
        replica = Path(self.scope["replica"])
        if (digest(snapshot(replica)) != self.replica_sha256
                or digest(self.runner.native_manager_context(self.scope["project_id"])) != self.scope["baseline_sha256"]
                or any(public_instruction(name)["sha256"] != checksum
                       for name, checksum in self.scope["instructions"].items())):
            raise Blocked("Session replica, requirements or instructions changed")

    def accept_receipt(self, receipt):
        if self.phase != "verifier" and type(receipt.get("usage")) is int:
            if receipt["usage"] < 0:
                self.usage_complete = False
                raise Blocked("Session reported usage is invalid")
            self.reported += receipt["usage"]
            self.remaining = max(0, self.scope["tokens"] - self.reported)
            if self.reported > self.scope["tokens"]:
                raise Blocked("TOKEN_LIMIT")
        elif self.phase != "verifier":
            self.usage_complete = False
        if (receipt["outcome"] != "COMPLETED" or receipt.get("owned_tree") != "DRAINED"
                or any(receipt.get(key) != value for key, value in self.binding().items())):
            if receipt.get("provider_usage_status") == "LIMIT_REACHED":
                raise Blocked("PROVIDER_LIMIT")
            if receipt.get("outcome") == "UNKNOWN":
                raise Blocked("REQUEST_UNKNOWN")
            if receipt.get("reason") == "Developer proposed no file change":
                raise Blocked("NO_PROGRESS")
            raise Blocked("Session role did not settle successfully; no automatic retry")
        if self.phase != "verifier":
            if type(receipt["usage"]) is not int:
                raise Blocked("USAGE_UNKNOWN")


def run(runner, project, account, expires, seconds, tokens, reworks, vectors, approved, cancelled=lambda: False, *,
        permissions=None, revoked=lambda: False, cycles=None, on_progress=lambda _progress: None):
    from .runtime import native_unresolved, require_admission

    with execution_slot() as abandoned, ExitStack() as resources:
        events, head = runner._idle_diagnostic(abandoned)
        options = {"permissions": permissions} if permissions is not None else {}
        if cycles is not None:
            options["cycles"] = cycles
        scope = review(runner, project, account, expires, seconds, tokens, reworks, vectors, **options)
        if digest(scope) != approved or scope["ledger_head"] != head:
            raise Blocked("Unified development approval changed")
        session_id = uuid4().hex
        session = _Session(runner, scope, session_id, approved, lambda: cancelled() or revoked())
        fields = {"project_id": project, "run_id": session_id, "state": "DIAGNOSTIC", **session.binding()}
        runner.ledger.append(len(events), head, {**fields, "kind": "subscription_session_started", "scope": scope},
                             admission=require_admission)
        resources.enter_context(development_session_scope(session_id))
        roles, failures, outputs = [], set(), {scope["replica_sha256"]}
        attempts = 0
        cycle_count = 0
        outcome, reason = "BLOCKED", None
        gui_receipt = None

        def execute(phase, transport, *args):
            session.phase = phase
            session.check_scope()
            on_progress({"phase": phase, "cycle": max(1, cycle_count), "remaining_tokens": session.remaining})
            scope = transport.review(*args, session=session)
            if isinstance(scope, tuple):
                scope = scope[0]
            result = transport.run(*args, digest(scope), session.stopped, session=session)
            if isinstance(result, tuple):
                result = result[0]
            roles.append({"phase": phase, "run_id": result["run_id"], "sha256": digest(result),
                          "usage": result["usage"], "outcome": result["outcome"]})
            session.accept_receipt(result)
            return result

        try:
            models = scope["models"]
            planned = execute("manager-planning", manager, runner, project, account, models["Manager"], expires)
            cycle_count = 1
            while True:
                changed = execute("developer", developer, runner, project, planned["run_id"],
                                  account, models["Developer"], expires)
                session.replica_sha256 = changed["after_replica_sha256"]
                if changed["after_replica_sha256"] in outputs:
                    raise Blocked("NO_PROGRESS")
                outputs.add(changed["after_replica_sha256"])
                prefix = runner, project, changed["run_id"]
                reviewed = execute("reviewer", cycle, *prefix, "reviewer", account, models["Reviewer"], expires, None)
                if reviewed["decision"] == "REWORK":
                    failure, feedback = reviewed, cycle._model_output(runner.ledger, reviewed, "reviewer")["findings"]
                elif reviewed["decision"] == "ACCEPT_FOR_VERIFICATION":
                    verified = execute("verifier", cycle, *prefix, "verifier", None, None, expires, vectors)
                    if scope.get("gui_contract") or verified.get("sdk_gui"):
                        gui_receipt = verified
                    if verified["verdict"] == "PASS":
                        final = execute("manager-final", cycle, *prefix, "manager-final", account,
                                        models["Manager"], expires, None)
                        outcome = final["decision"]
                        if outcome == "CONTINUE":
                            if scope["work_limit"] is not None and cycle_count >= scope["work_limit"]:
                                outcome = "CYCLE_LIMIT"
                                reason = "Cycle budget exhausted"
                                break
                            session.previous_cycle = {"final": cycle._model_output(runner.ledger, final, "manager-final"),
                                                      "verification_sha256": digest(verified),
                                                      "replica_sha256": session.replica_sha256}
                            session.rework = None
                            cycle_count += 1
                            planned = execute("manager-planning", manager, runner, project, account, models["Manager"], expires)
                            continue
                        if outcome != "REWORK_REQUIRED" or attempts >= reworks:
                            break
                        failure = final
                        feedback = [cycle._model_output(runner.ledger, final, "manager-final")["next_task_or_rework"]]
                    elif verified["verdict"] != "FAIL":
                        raise Blocked("Verifier could not verify this change")
                    else:
                        failure = verified
                        feedback = [item for item in verified["observations"] if item["actual"] != item["expected"]]
                        if scope.get("gui_contract"):
                            feedback = subscription_gui.failure_feedback(runner.ledger, verified, changed, session.binding())
                        elif scope.get("runtime"):
                            feedback = {"observations": feedback, "exit_code": verified["exit_code"],
                                        "source_sha256": verified["source_sha256"], "receipt_sha256": digest(verified)}
                            if gui_failure := verified.get("sdk_gui"):
                                feedback["screenshots"] = [scene["original"]["sha256"] for scene in gui_failure["scenes"]]
                else:
                    raise Blocked("Reviewer blocked the change")
                fingerprint = digest([failure["phase"], [
                    [item.get("title", item.get("file")), item.get("input"), item["expected"], item["actual"]]
                    for item in feedback["observations"]
                ] if isinstance(feedback, dict) else feedback])
                if fingerprint in failures:
                    raise Blocked("NO_PROGRESS")
                if attempts >= reworks:
                    raise Blocked("Session rework limit reached")
                failures.add(fingerprint)
                attempts += 1
                session.rework = {"receipt": failure, "feedback": feedback}
        except (Blocked, KeyboardInterrupt) as error:
            reason = str(error) if isinstance(error, Blocked) else "CANCELLED"
            outcome = ("CANCELLED" if isinstance(error, KeyboardInterrupt) else
                       session.stop_reason() or (reason if reason in
                                                 {"NO_PROGRESS", "TIME_LIMIT", "TOKEN_LIMIT", "PROVIDER_LIMIT",
                                                  "REQUEST_UNKNOWN", "USAGE_UNKNOWN"} else "BLOCKED"))
        if revoked():
            outcome, reason = "PERMISSIONS_REVOKED", "Session permissions revoked by owner"
        current, tail = runner.ledger.snapshot()
        result = {**fields, "kind": "subscription_session_ended", "outcome": outcome, "reason": reason,
                  "roles": roles, "reworks": attempts, "cycle_count": cycle_count,
                  "elapsed_seconds": round(monotonic() - session.started_at, 3),
                  "remaining_reported_tokens": session.remaining,
                  "used_reported_tokens": session.reported, "usage_complete": session.usage_complete,
                  "owned_tree": "UNRESOLVED" if native_unresolved(current) else "DRAINED",
                  "gui": (("GUI_PASS" if gui_receipt["verdict"] == "PASS" else gui_receipt["verdict"])
                          if gui_receipt and gui_receipt.get("sdk_gui") else
                          gui_receipt.get("gui", "NOT_VERIFIED") if gui_receipt else
                          "NOT_VERIFIED" if scope.get("gui_contract")
                          or scope.get("runtime", {}).get("kind") == mxc_sdk_project.GUI_KIND else "NOT_LINKED"),
                  "gui_receipt_sha256": digest(gui_receipt) if gui_receipt else None,
                  "provider_token_limit": "UNAVAILABLE_PREVIEW"}
        runner.ledger.append(len(current), tail, result, allow_shutdown=True)
        return result


def review_winforms_reverification(runner, project, account, expires, seconds, tokens, scenario):
    """Approve more observations for the same reviewed source after a final rework decision."""
    from .runtime import require_admission

    events, head = runner.ledger.snapshot()
    require_admission(events)
    ended = cycle._find(events, "subscription_session_ended", project_id=project)
    if not ended or ended.get("outcome") != "REWORK_REQUIRED" or ended.get("owned_tree") != "DRAINED":
        raise Blocked("Supplemental verification requires a settled final rework decision")
    started = cycle._find(events, "subscription_session_started", project_id=project, run_id=ended["run_id"])
    if not started or digest(started["scope"]) != started["session_approval_sha256"]:
        raise Blocked("Previous session approval changed")
    prior = started["scope"]
    runtime = prior.get("runtime", {})
    if (runtime.get("kind") != mxc_sdk_project.GUI_KIND or prior.get("reverification")
            or ended["session_approval_sha256"] != started["session_approval_sha256"]
            or account != prior["account"] or type(expires) is not int or not time() < expires <= time() + 300
            or type(seconds) is not int or not 1 <= seconds <= prior["seconds"]
            or type(tokens) is not int or not 1 <= tokens <= prior["tokens"]):
        raise Blocked("Supplemental verification exceeds the previous session scope")
    roles = {role["phase"]: role for role in ended["roles"]}
    receipts = []
    for phase, kind in (("developer", "subscription_developer_result"), ("reviewer", "subscription_cycle_result"),
                        ("manager-final", "subscription_cycle_result")):
        ref = roles.get(phase, {})
        receipt = cycle._find(events, kind, project_id=project, run_id=ref.get("run_id"),
                              development_session_id=started["run_id"],
                              session_approval_sha256=started["session_approval_sha256"], outcome="COMPLETED")
        if not receipt or digest(receipt) != ref["sha256"] or receipt["owned_tree"] != "DRAINED":
            raise Blocked("Previous reviewed change has incomplete evidence")
        receipts.append(receipt)
    changed, reviewed, final = receipts
    if (reviewed["decision"] != "ACCEPT_FOR_VERIFICATION" or final["decision"] != "REWORK_REQUIRED"
            or reviewed["developer_sha256"] != digest(changed) or final["developer_sha256"] != digest(changed)
            or final["reviewer_sha256"] != digest(reviewed)):
        raise Blocked("Previous decisions do not refer to the same reviewed change")
    replica = Path(prior["replica"])
    cycle._current(replica, changed)
    frozen = runtime["build"]
    build = mxc_sdk_project.review(replica, frozen["launcher"], [frozen["entry"]], scenario,
                                   frozen["limits"]["max_seconds"], Path(frozen["tool_root_read_only"]), winforms=True)
    files = prior["writable_files"]
    if (any(build[key] != value for key, value in frozen.items()
            if key not in {"replica_sha256", "sources", "tests", "gui_scenario"})
            or {name: info for name, info in build["sources"].items() if name not in files}
            != {name: info for name, info in frozen["sources"].items() if name not in files}):
        raise Blocked("Supplemental verification changed the build boundary")
    spec = {"files": dict.fromkeys(files, prior["acceptance"])}
    scope = {**prior, "expires": expires, "seconds": seconds, "tokens": tokens, "max_reworks": 0,
             "generation": len(events) + 1, "ledger_head": head, "vectors_sha256": digest(spec),
             "runtime": {"kind": runtime["kind"], "build": build},
             "reverification": {"parent_run_id": started["run_id"], "developer_sha256": digest(changed),
                                "reviewer_sha256": digest(reviewed), "prior_final_sha256": digest(final)}}
    return scope, spec, [roles["developer"], roles["reviewer"]]


def run_winforms_reverification(runner, project, account, expires, seconds, tokens, scenario, approved,
                                cancelled=lambda: False):
    from .runtime import native_unresolved, require_admission

    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        scope, spec, roles = review_winforms_reverification(runner, project, account, expires, seconds, tokens, scenario)
        if digest(scope) != approved or scope["ledger_head"] != head:
            raise Blocked("Supplemental verification approval changed")
        session_id = uuid4().hex
        session = _Session(runner, scope, session_id, approved, cancelled)
        session.replica_sha256 = scope["runtime"]["build"]["replica_sha256"]
        fields = {"project_id": project, "run_id": session_id, "state": "DIAGNOSTIC", **session.binding()}
        runner.ledger.append(len(events), head, {**fields, "kind": "subscription_session_started", "scope": scope},
                             admission=require_admission)
        verified, reason, outcome = None, None, "BLOCKED"
        with development_session_scope(session_id):
            try:
                for phase, role_account, model, inputs in (
                        ("verifier", None, None, spec), ("manager-final", account, scope["models"]["Manager"], None)):
                    session.phase = phase
                    session.check_scope()
                    args = (runner, project, roles[0]["run_id"], phase, role_account, model, expires, inputs)
                    result = cycle.run(*args, digest(cycle.review(*args, session=session)), session.stopped,
                                       session=session)
                    roles.append({"phase": phase, "run_id": result["run_id"], "sha256": digest(result),
                                  "usage": result["usage"], "outcome": result["outcome"]})
                    session.accept_receipt(result)
                    if phase == "verifier":
                        verified = result
                        if verified["verdict"] != "PASS":
                            raise Blocked("Supplemental GUI verification did not pass")
                    else:
                        outcome = result["decision"]
            except (Blocked, KeyboardInterrupt) as error:
                reason = str(error) if isinstance(error, Blocked) else "CANCELLED"
        current, tail = runner.ledger.snapshot()
        result = {**fields, "kind": "subscription_session_ended", "outcome": outcome, "reason": reason,
                  "roles": roles, "reworks": 0, "remaining_reported_tokens": session.remaining,
                  "used_reported_tokens": session.reported, "usage_complete": session.usage_complete,
                  "owned_tree": "UNRESOLVED" if native_unresolved(current) else "DRAINED",
                  "gui": "GUI_PASS" if verified and verified["verdict"] == "PASS" else "NOT_VERIFIED",
                  "gui_receipt_sha256": digest(verified) if verified else None,
                  "provider_token_limit": "UNAVAILABLE_PREVIEW"}
        runner.ledger.append(len(current), tail, result, allow_shutdown=True)
        return result


def recover(runner, project, session_id):
    """Settle an interrupted session between roles without replaying any request."""
    from .runtime import native_unresolved

    with execution_slot():
        events, head = runner.ledger.snapshot(recover_journal=True)
        matching = [event for event in events if event["run_id"] == session_id
                    and event["project_id"] == project
                    and event["kind"] in ("subscription_session_started", "subscription_session_ended")]
        if len(matching) != 1 or matching[0]["kind"] != "subscription_session_started":
            raise Blocked("No interrupted subscription session was found")
        if native_unresolved(events):
            raise Blocked("A role intent is unsettled; preserve evidence and do not replay")
        start = matching[0]
        result = {key: start[key] for key in ("project_id", "run_id", "state", "development_session_id",
                                              "session_approval_sha256")}
        result.update(kind="subscription_session_ended", outcome="UNKNOWN",
                      reason="Owner closed interrupted session without replay", owned_tree="DRAINED", gui="NOT_LINKED")
        runner.ledger.append(len(events), head, result, allow_shutdown=True)
        return result
