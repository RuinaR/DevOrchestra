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


def review(runner, project, account, expires, seconds, tokens, reworks, vectors):
    from .runtime import require_admission

    if (type(expires) is not int or not time() < expires <= time() + 300
            or type(seconds) is not int or not 1 <= seconds <= 300
            or type(tokens) is not int or not 1 <= tokens <= 100_000
            or type(reworks) is not int or not 0 <= reworks <= 5):
        raise Blocked("Invalid session expiry, time, token or rework limit")
    events, head = runner.ledger.snapshot()
    require_admission(events)
    baseline = runner.native_manager_context(project)
    gui = isinstance(vectors, dict) and "gui" in vectors
    batch = isinstance(vectors, dict) and "files" in vectors
    if not gui and not batch:
        validate_vectors(vectors, baseline["acceptance"])
    catalog = Catalog(runner.ledger).read()
    data = catalog["projects"][project]["data"]
    setup = catalog["setups"].get(project)
    limits = catalog["global_budget"]
    if (seconds > min(limits["seconds"], data["budget"]["seconds"])
            or tokens > min(limits["tokens"], data["budget"]["tokens"])):
        raise Blocked("Session exceeds registered project or global limits")
    files = baseline["proposed_files_ceiling"]
    if batch:
        if (not setup or set(vectors) not in ({"files", "create"}, {"files", "create", "runtime"})
                or type(vectors["files"]) is not dict or set(vectors["files"]) != set(files)):
            raise Blocked("Batch Verifier requires vectors for every approved file")
        check_names(files, vectors["create"])
        for spec in vectors["files"].values():
            validate_vectors(spec, baseline["acceptance"])
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
            "seconds": seconds, "tokens": tokens, "work_limit": 1, "max_reworks": reworks,
            "replica": setup["replica"], "git_head": developer._head(Path(setup["replica"])),
            "replica_sha256": digest(image), "file": files[0], "readable_files": files, "writable_files": files,
            "baseline_sha256": digest(baseline), "manager_request_sha256": manager_review["request_sha256"],
            "goal": baseline["goal"], "acceptance": baseline["acceptance"],
            "vectors_sha256": digest(vectors), "models": model_by_role,
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
        scope["batch"] = {"max_files": len(files), "formats": [".py"], "create": vectors["create"],
                          "delete": False, "move": False, "max_bytes": BATCH_LIMIT,
                          "before_sha256": {name: image["files"].get(name, {}).get("sha256") for name in files}}
        scope["tools"] = ["controller-replace-batch", "fixed-python-vectors-v1"]
        if "runtime" in vectors:
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
        self.deadline = monotonic() + scope["seconds"]
        self.remaining = scope["tokens"]
        self.reported = 0
        self.usage_complete = True
        self.phase, self.rework = None, None
        self.replica_sha256 = scope["replica_sha256"]

    def binding(self):
        return {"development_session_id": self.id, "session_approval_sha256": self.approval}

    def slot(self):
        return held_execution_slot()

    def stopped(self):
        return self.cancelled() or monotonic() >= self.deadline or time() >= self.scope["expires"]

    def limit(self, budget, project, phase):
        if self.stopped() or project != self.scope["project_id"] or phase != self.phase:
            raise Blocked("Session phase, cancellation or deadline does not permit execution")
        budget["tokens"] = min(budget["tokens"], self.remaining)

    def check_scope(self):
        if self.stopped():
            raise Blocked("Session cancelled or expired")
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
                raise Blocked("Session reported usage over budget")
        elif self.phase != "verifier":
            self.usage_complete = False
        if (receipt["outcome"] != "COMPLETED" or receipt.get("owned_tree") != "DRAINED"
                or any(receipt.get(key) != value for key, value in self.binding().items())):
            if receipt.get("reason") == "Developer proposed no file change":
                raise Blocked("NO_PROGRESS")
            raise Blocked("Session role did not settle successfully; no automatic retry")
        if self.phase != "verifier":
            if type(receipt["usage"]) is not int:
                raise Blocked("Session reported usage unknown")


def run(runner, project, account, expires, seconds, tokens, reworks, vectors, approved, cancelled=lambda: False):
    from .runtime import native_unresolved, require_admission

    with execution_slot() as abandoned, ExitStack() as resources:
        events, head = runner._idle_diagnostic(abandoned)
        scope = review(runner, project, account, expires, seconds, tokens, reworks, vectors)
        if digest(scope) != approved or scope["ledger_head"] != head:
            raise Blocked("Unified development approval changed")
        session_id = uuid4().hex
        session = _Session(runner, scope, session_id, approved, cancelled)
        fields = {"project_id": project, "run_id": session_id, "state": "DIAGNOSTIC", **session.binding()}
        runner.ledger.append(len(events), head, {**fields, "kind": "subscription_session_started", "scope": scope},
                             admission=require_admission)
        resources.enter_context(development_session_scope(session_id))
        roles, failures, outputs = [], set(), set()
        attempts = 0
        outcome, reason = "BLOCKED", None
        gui_receipt = None

        def execute(phase, transport, *args):
            session.phase = phase
            session.check_scope()
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
                    if scope.get("gui_contract"):
                        gui_receipt = verified
                    if verified["verdict"] == "PASS":
                        final = execute("manager-final", cycle, *prefix, "manager-final", account,
                                        models["Manager"], expires, None)
                        outcome = final["decision"]
                        break
                    if verified["verdict"] != "FAIL":
                        raise Blocked("Verifier could not verify this change")
                    failure = verified
                    feedback = [item for item in verified["observations"] if item["actual"] != item["expected"]]
                    if scope.get("gui_contract"):
                        feedback = subscription_gui.failure_feedback(runner.ledger, verified, changed, session.binding())
                    elif scope.get("runtime"):
                        feedback = {"observations": feedback, "exit_code": verified["exit_code"],
                                    "source_sha256": verified["source_sha256"], "receipt_sha256": digest(verified)}
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
            outcome = ("CANCELLED" if cancelled() or isinstance(error, KeyboardInterrupt) else
                       "TIME_LIMIT" if session.stopped() else "NO_PROGRESS" if reason == "NO_PROGRESS" else "BLOCKED")
        current, tail = runner.ledger.snapshot()
        result = {**fields, "kind": "subscription_session_ended", "outcome": outcome, "reason": reason,
                  "roles": roles, "reworks": attempts, "remaining_reported_tokens": session.remaining,
                  "used_reported_tokens": session.reported, "usage_complete": session.usage_complete,
                  "owned_tree": "UNRESOLVED" if native_unresolved(current) else "DRAINED",
                  "gui": (gui_receipt.get("gui", "NOT_VERIFIED") if gui_receipt else
                          "NOT_VERIFIED" if scope.get("gui_contract") else "NOT_LINKED"),
                  "gui_receipt_sha256": digest(gui_receipt) if gui_receipt else None,
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
