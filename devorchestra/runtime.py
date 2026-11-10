"""Trusted state transitions and fenced role admission."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from time import monotonic, time
from uuid import uuid4

from .adapter import (Budget, MockAdapter, ModelBackend, NativeReceipt, Outcome, RoleRequest, Scope,
                      WindowsNativeCodexAdapter, manager_backend_status, normalize_mock, require_manager_backend)
from .catalog import Catalog, latest_requirements, read_frozen_requirements
from .development import session_view, usage_totals, validate_proposal
from .event_audit import Binding, FIXTURE_USAGE, Packet
from .event_journal import EventJournal, OPEN_ROLE_EVENTS

from .lease import Blocked, SlotBlocked, current_development_session, execution_slot
from .ledger import canonical, check_path, digest
from .mxc_diagnostic import diagnostic_unresolved
from .policy import Capability, Grant, Request
from .questions import AUTHORIZATION_TYPES, new_question, pending_question, validate_response
from .shutdown import HELD, UNKNOWN, checkpoint, shutdown_state, transition
from .supervised import _path_allowed, handoff, waiting_for_handoff
from .token_reserve import DEFAULT_RESERVE, reserve_status


class State(str, Enum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    PLANNING = "PLANNING"
    DEVELOPING = "DEVELOPING"
    REVIEWING = "REVIEWING"
    VERIFYING = "VERIFYING"
    CHECKPOINTING = "CHECKPOINTING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECOVERING = "RECOVERING"
    NEED_APPROVAL = "NEED_APPROVAL"
    WAITING_FOR_USER = "WAITING_FOR_USER"


PHASES = {
    State.PLANNING: "Manager",
    State.DEVELOPING: "Developer",
    State.REVIEWING: "Reviewer",
    State.VERIFYING: "Verifier",
    State.CHECKPOINTING: "Manager",
}
PIPELINE = (*PHASES, State.COMPLETED)
TERMINAL = {State.COMPLETED, State.FAILED, State.BLOCKED, State.CANCELLED}


class Scenario(str, Enum):
    NORMAL = "normal"
    AGENT_ERROR = "agent-error"
    REVIEW_FAIL = "review-fail"
    REVIEW_REWORK = "review-rework"
    VERIFY_FAIL = "verify-fail"
    TIME_LIMIT = "time-limit"
    TOKEN_LIMIT = "token-limit"


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    repository: str
    workspace: str
    documents: tuple
    tools: tuple = ("mock.inspect",)
    files: tuple = ("fixture.txt",)
    network: tuple = ()
    checks: tuple = ("fixture-transition",)
    models: tuple = ("Manager", "Developer", "Reviewer")
    model: str = "mock-v1"
    reasoning: str = "NOT_APPLICABLE"
    seconds: int = 600
    tokens: int = 10
    cost_limit: int = 0
    auto_commit: bool = False
    auto_push: bool = False
    milestone: str = "P1"
    priority: str = "verify synthetic fixture change"


PROJECTS = {
    key: Project(key, name, f"mock://{key}/repo", f"mock://{key}/",
                 ("goal.md", "design.md", "AGENTS.md"))
    for key, name in (("fixture-a", "Fixture Alpha"), ("fixture-b", "Fixture Beta"))
}
INSTRUCTION_VERSION = "p1-mock-v1"
POLICY_HASH = digest([INSTRUCTION_VERSION, list(Capability), list(PHASES)])
ROLE_TOKENS = 1


def last_run(events):
    for index in range(len(events) - 1, -1, -1):
        if events[index]["kind"] == "run_started":
            return [e for e in events[index:]
                    if not e["kind"].startswith(("session_", "shutdown_", "mxc_diagnostic_",
                                                 "native_inspection_", "native_manager_", "supervised_",
                                                 "gui_validation_", "gui_build_", "gui_session_", "build_verifier_", "sdk_install_",
                                                 "subscription_developer_", "subscription_cycle_",
                                                 "subscription_session_", "subscription_usage_"))]
    return []


def native_unresolved(events):
    pending = set()
    for event in events:
        if event["kind"] == "native_role_intent":
            pending.add(event["agent_run_id"])
        elif event["kind"] == "native_role_result":
            pending.discard(event["agent_run_id"])
        elif event["kind"] == "native_inspection_intent":
            pending.add(("inspection", event["request_id"]))
        elif event["kind"] == "native_inspection_result":
            pending.discard(("inspection", event["request_id"]))
        elif event["kind"] == "native_manager_intent":
            pending.add(("manager", event["request_id"]))
        elif event["kind"] == "native_manager_result":
            pending.discard(("manager", event["request_id"]))
        elif event["kind"] == "subscription_developer_intent":
            pending.add(("subscription-developer", event["run_id"]))
        elif event["kind"] == "subscription_developer_result":
            pending.discard(("subscription-developer", event["run_id"]))
        elif event["kind"] == "subscription_cycle_intent":
            pending.add(("subscription-cycle", event["run_id"]))
        elif event["kind"] == "subscription_cycle_result":
            pending.discard(("subscription-cycle", event["run_id"]))
        elif event["kind"] == "gui_validation_intent":
            pending.add(("gui", event["approval_id"]))
        elif event["kind"] == "gui_validation_result":
            pending.discard(("gui", event["approval_id"]))
        elif event["kind"] == "gui_build_intent":
            pending.add(("gui-build", event["approval_id"]))
        elif event["kind"] == "gui_build_result":
            pending.discard(("gui-build", event["approval_id"]))
        elif event["kind"] == "build_verifier_intent":
            pending.add(("build-verifier", event["run_id"]))
        elif event["kind"] == "build_verifier_result":
            pending.discard(("build-verifier", event["run_id"]))
        elif event["kind"] == "sdk_install_intent":
            pending.add(("sdk-install", event["run_id"]))
        elif event["kind"] == "sdk_install_result":
            pending.discard(("sdk-install", event["run_id"]))
    return bool(pending)


def require_admission(events, *, allow_supervised=False):
    if shutdown_state(events)["state"] in HELD:
        raise Blocked("Shutdown has closed admission")
    if diagnostic_unresolved(events):
        raise Blocked("Diagnostic process ownership requires recovery")
    if native_unresolved(events):
        raise Blocked("Native process ownership requires recovery")
    pending = {event["run_id"] for event in events if event["kind"] == "subscription_session_started"}
    pending.difference_update(event["run_id"] for event in events if event["kind"] == "subscription_session_ended")
    if pending - {current_development_session()}:
        raise Blocked("An interrupted subscription session requires owner recovery")
    if pending_budget_stop(events):
        raise Blocked("Unsettled budget stop requires a shutdown checkpoint")
    if pending_question(events):
        raise Blocked("A user question is pending")
    if not allow_supervised and waiting_for_handoff(events):
        raise Blocked("A supervised Codex handoff is awaiting a result")


def pending_budget_stop(events):
    for event in reversed(events):
        if event["kind"].startswith("shutdown_") or event["kind"] == "run_started":
            return None
        if event["kind"] == "role_finished":
            return event if event.get("shutdown_reason") else None
    return None


def mock_role(phase, scenario, history):
    if phase is State.PLANNING:
        return {"decision": "Plan a fixture change from draft to ready",
                "reviewed_files": ["fixture.txt"], "result": "OK"}
    if phase is State.DEVELOPING:
        if scenario is Scenario.AGENT_ERROR:
            return {"decision": "Injected Mock agent error", "result": "FAIL"}
        after = "invalid" if scenario is Scenario.VERIFY_FAIL else "ready"
        return {"decision": "Proposed synthetic change", "result": "OK",
                "diff": {"path": "fixture.txt", "before": "draft", "after": after}}
    if phase is State.REVIEWING:
        failed = scenario is Scenario.REVIEW_FAIL
        if scenario is Scenario.REVIEW_REWORK:
            return {"decision": "Injected review rework", "result": "OK",
                    "review_output": {"decision": "REWORK", "coverage_gaps": [], "findings": [{
                        "severity": "HIGH", "location": "fixture.txt", "observed": "Synthetic review finding",
                        "impact": "Fixture acceptance incomplete", "required_fix": "Resolve fixture finding",
                    }]}}
        return {"decision": "Injected review rejection" if failed else "Fixture scope accepted",
                "result": "FAIL" if failed else "OK"}
    if phase is State.VERIFYING:
        changes = [e["diff"] for e in history if e["kind"] == "role_finished" and "diff" in e]
        passed = changes == [{"path": "fixture.txt", "before": "draft", "after": "ready"}]
        return {"decision": "Compared synthetic diff with expected fixture",
                "test": "fixture-transition", "result": "PASS" if passed else "FAIL"}
    verified = any(e["kind"] == "role_finished" and e.get("test") == "fixture-transition"
                   and e["result"] == "PASS" for e in history)
    return {"decision": "Checkpoint requires recorded verifier PASS",
            "result": "PASS" if verified else "FAIL"}


def mock_next(goal_hash, verification_ref):
    return {"decision": "NEXT", "goal_hash": goal_hash, "evidence_ref": verification_ref}


def freeze_project(project_id, catalog):
    registered = catalog["projects"].get(project_id)
    if project_id in PROJECTS:
        project = asdict(PROJECTS[project_id])
        goal = project["priority"]
        project_budget = {"seconds": project["seconds"], "tokens": project["tokens"], "cost_microunits": 0}
    elif registered:
        # Snapshot only synthetic metadata. Never pass owner filesystem paths to a role.
        config = registered["data"]
        project_budget = config["budget"]
        goal = config["goal"]
        project = {"name": config["name"], "milestone": "MOCK",
                   "model": "mock-v1", "reasoning": "NOT_APPLICABLE",
                   "seconds": project_budget["seconds"], "cost_limit": project_budget["cost_microunits"],
                   "roles": config["roles"], "acceptance": config["acceptance"],
                   "revision": registered["revision"]}
    else:
        raise Blocked("Unknown project")
    global_budget = catalog["global_budget"]
    budget = {key: min(value, global_budget[key]) for key, value in project_budget.items()}
    acceptance = project.get("acceptance", ["Verify synthetic workflow"])
    guidance = registered["data"]["documents"] if registered else project["documents"]
    requirements = latest_requirements(catalog, project_id)
    return {"project": project, "goal": goal, "acceptance": acceptance, "budget": budget,
            "project_budget": project_budget, "global_budget": global_budget,
            "guidance_hash": digest([INSTRUCTION_VERSION, POLICY_HASH, guidance, requirements]),
            "requirements": requirements}


class Runner:
    def __init__(self, ledger):
        self.ledger = ledger

    def ask_user(self, expected_run, kind, prompt, reason, impact, recommendation, decline,
                 *, wait_seconds=120, scope=None):
        if type(wait_seconds) is not int or not 1 <= wait_seconds <= 300:
            raise Blocked("Invalid question lifetime")
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            require_admission(events)
            run = last_run(events)
            work = _Session(self.ledger, len(events), tail, run)
            if (abandoned or not run or work.ids["run_id"] != expected_run or work.pending_job
                    or work.state not in PHASES or run[-1]["kind"] in OPEN_ROLE_EVENTS):
                raise Blocked("Question requires a current safe role boundary")
            approval = next((event for event in reversed(run) if event["kind"] == "owner_approved_mock"), None)
            if approval is None:
                raise Blocked("Question requires an approved task")
            expires = min(time() + wait_seconds, run[0]["deadline"], approval["expires"])
            if expires <= time():
                raise Blocked("Question task approval or deadline expired")
            question = new_question(kind, prompt, reason, impact, recommendation, decline,
                                    expires=expires, scope=scope)
            return work.emit("question_opened", State.WAITING_FOR_USER, admission_check=require_admission,
                             question=question, resume_phase=work.state.value)

    def respond_question(self, request_id, expected_run, generation, decision, answer=None):
        """Settle once, then continue only a previously authorized synthetic run."""
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            pending = pending_question(events)
            if (abandoned or not pending or type(generation) is not int or pending["question"]["id"] != request_id
                    or pending["run_id"] != expected_run or pending["generation"] != generation):
                raise Blocked("Question is no longer current")
            if (shutdown_state(events)["state"] in HELD or native_unresolved(events)
                    or diagnostic_unresolved(events) or pending_budget_stop(events)):
                raise Blocked("Question response admission is closed")
            question = pending["question"]
            expired = time() >= question["expires"]
            if not expired:
                validate_response(question, decision, answer)
            details = {"question_id": request_id, "question_type": question["type"],
                       "decision": "EXPIRED" if expired else decision, "answer": None if expired else answer,
                       "responded_by": None if expired else "OWNER",
                       "scope": question["scope"], "expires": question["expires"]}

            def admit(current):
                if shutdown_state(current)["state"] in HELD or pending_question(current) != pending:
                    raise Blocked("Question response admission changed")
                if not expired and time() >= question["expires"]:
                    raise Blocked("Question expired during response")

            if pending["kind"] in ("native_manager_result", "supervised_imported"):
                event = {**{key: pending[key] for key in ("project_id", "run_id", "task_id", "generation")},
                         **details, "question_development_id": pending.get("question_development_id"),
                         "kind": "native_manager_question_expired" if expired else "native_manager_question_responded",
                         "state": "DIAGNOSTIC", "source": "OWNER",
                         "continuation": "FRESH_MANAGER_APPROVAL_REQUIRED" if not expired and decision == "ANSWER"
                         else "BLOCKED", "timestamp": datetime.now(timezone.utc).isoformat()}
                self.ledger.append(len(events), tail, event, allow_shutdown=True, admission=admit)
                return event

            run = last_run(events)
            work = _Session(self.ledger, len(events), tail, run)
            if not run or run[-1] != pending:
                raise Blocked("Question no longer owns the task boundary")
            resume = not expired and decision == "ANSWER"
            if resume:
                continuation = "RESUMED"
            elif not expired and decision == "APPROVE" and question["type"] in AUTHORIZATION_TYPES:
                continuation = "CAPABILITY_EXECUTOR_UNVERIFIED"
            else:
                continuation = "BLOCKED"
            event = work.emit(
                "question_expired" if expired else "question_responded",
                State(pending["resume_phase"]) if resume else State.BLOCKED, admission_check=admit,
                continuation=continuation, **details,
            )
        if resume:
            return self.command("run", project=pending["project_id"], expected_run=expected_run)
        return event

    def supervised_handoff(self, action, project_id, **arguments):
        return handoff(self, action, project_id, **arguments)

    def gui_verification_scope(self, project_id, *, registered=False, role="Verifier", exe_name=None):
        return self._gui_verification_scope(project_id, registered, role, exe_name)[0]

    def _gui_verification_scope(self, project_id, registered, role, exe_name=None, events=None):
        from .gui_verification import scenario_tool, scope
        from .gui_project import load

        scenario_tool(registered, role)
        catalog = Catalog(self.ledger).read()
        record = catalog["projects"].get(project_id)
        if not record and project_id not in PROJECTS:
            raise Blocked("Register the project before approving GUI verification")
        seconds = min(20, catalog["global_budget"]["seconds"])
        if record:
            data = record["data"]
            if role not in data["roles"]:
                raise Blocked("Unknown GUI role")
            seconds = min(seconds, data["budget"]["seconds"], data["roles"][role]["budget"]["seconds"])
        if exe_name is not None and not registered:
            raise Blocked("An EXE requires a registered GUI scenario")
        form, descriptor = load(catalog, project_id, self.ledger.state_boundary) if registered else (None, None)
        approved = scope(project_id, record["revision"] if record else None, seconds, role, descriptor)
        if exe_name is not None:
            from .gui_exe import BUILD_SHA256, EXE_MEMORY, SOURCE_SHA256, read_exe, target
            from hashlib import sha256

            if events is None:
                events = self.ledger.read()
            path = target(approved["replica"], exe_name)
            build = next((event for event in reversed(events)
                          if event["kind"] == "gui_build_result" and event["project_id"] == project_id
                          and event["result"] == "PASS" and event["exe"] == str(path)), None)
            if (not build or build["scope"]["build_sha256"] != BUILD_SHA256
                    or build["scope"]["project_revision"] != record["revision"]):
                raise Blocked("BLOCKED: EXE has no current Controller build provenance or verified OS isolation")
            data = read_exe(path)
            checksum = sha256(data).hexdigest()
            if checksum != build["exe_sha256"]:
                raise Blocked("Trusted project EXE hash changed")
            generation = len(events) + 1
            identity = digest([project_id, generation, build["approval_id"], checksum, approved["descriptor_sha256"]])
            approved.update(exe=str(path), exe_sha256=checksum, fixture_sha256=SOURCE_SHA256,
                            trust="CONTROLLER_BUILT_TEST_EXE", build_id=build["approval_id"],
                            arguments=[], cwd="PRIVATE_STAGING", memory_bytes=EXE_MEMORY,
                            generation=generation, task_id=identity[:32], session_id=identity[32:],
                            cancel_token=digest(["gui-cancel", identity]))
            return approved, (form, data)
        return approved, (form, None)

    def gui_exe_build_scope(self, project_id, exe_name):
        from .gui_exe import BUILD_MEMORY, BUILD_SHA256, SOURCE_SHA256, target

        approved = self.gui_verification_scope(project_id, registered=True)
        path = target(approved["replica"], exe_name)
        if path.exists():
            raise Blocked("The GUI build target already exists")
        return {**approved, "output": str(path), "build_sha256": BUILD_SHA256,
                "source_sha256": SOURCE_SHA256, "tool": "build-reviewed-gui-test-exe",
                "cwd": "PRIVATE_STAGING", "memory_bytes": BUILD_MEMORY}

    def build_gui_exe(self, project_id, exe_name, approved_scope):
        from contextlib import ExitStack
        from hashlib import sha256
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from .gui_exe import BuildFailed, compile_exe
        from .system_powershell import hold_system_powershell

        with execution_slot() as abandoned, ExitStack() as resources:
            events, tail = self._idle_diagnostic(abandoned)
            scope = self.gui_exe_build_scope(project_id, exe_name)
            if digest(scope) != approved_scope:
                raise Blocked("GUI build approval changed")
            compiler = resources.enter_context(hold_system_powershell(scope["exe_sha256"]))
            run_id = uuid4().hex
            binding = {"project_id": project_id, "run_id": run_id, "task_id": run_id, "role": "Verifier",
                       "approval_id": uuid4().hex, "generation": len(events) + 1, "state": "DIAGNOSTIC",
                       "source": "WINDOWS_TRUSTED_GUI_BUILD", "scope": scope, "scope_sha256": approved_scope}
            count, fence = self.ledger.append(len(events), tail, {
                **binding, "kind": "gui_build_intent", "timestamp": datetime.now(timezone.utc).isoformat(),
            }, admission=require_admission)
            try:
                with TemporaryDirectory(prefix="gui-build-", dir=self.ledger.local_directory("temp", create=True)) as folder:
                    data = compile_exe(Path(folder), compiler, lambda: self._check_diagnostic_fence(count, fence),
                                       scope["timeout_seconds"])
            except BuildFailed as error:
                resources.close()
                result = {**binding, "kind": "gui_build_result", "result": "FAIL", "reason": str(error),
                          "job_active": 0, "timestamp": datetime.now(timezone.utc).isoformat()}
                self.ledger.append(count, fence, result, allow_shutdown=True)
                return result
            resources.close()
            self._check_diagnostic_fence(count, fence)
            output = Path(scope["output"])
            check_path(output)
            with output.open("xb") as stream:
                stream.write(data)
            result = {**binding, "kind": "gui_build_result", "result": "PASS", "exe": str(output),
                      "exe_sha256": sha256(data).hexdigest(), "job_active": 0,
                      "timestamp": datetime.now(timezone.utc).isoformat()}
            self.ledger.append(count, fence, result, allow_shutdown=True)
            return result

    def sdk_install_scope(self, project_id, launcher, expires):
        from pathlib import Path
        from . import dotnet_install
        from .app_server import verify_app_server_binary
        from .mxc_host_probe import LAUNCHER_PIN

        setup = Catalog(self.ledger).read()["setups"].get(project_id)
        if not setup or not Path(launcher).is_absolute() or not time() < expires <= time() + 3600:
            raise Blocked("SDK install requires a registered project, absolute launcher and current expiry")
        verify_app_server_binary(Path(launcher), approved_sha256=LAUNCHER_PIN)
        replica = Path(setup["replica"])
        return {**dotnet_install.review(replica, project_id), "replica": str(replica),
                "launcher": launcher, "launcher_sha256": LAUNCHER_PIN, "expires_epoch": expires}

    def sdk_install(self, project_id, launcher, expires, approval):
        from http.client import HTTPException
        from pathlib import Path
        from zipfile import BadZipFile
        from . import dotnet_install
        from .mxc_host_probe import OwnedMxcStopped
        from .owned_artifacts import _remove_owned_tree

        with execution_slot() as abandoned:
            events, tail = self._idle_diagnostic(abandoned)
            scope = self.sdk_install_scope(project_id, launcher, expires)
            if digest(scope) != approval:
                raise Blocked("SDK installation approval changed")
            parent = dotnet_install.project_root(self.ledger, project_id)
            target = parent / scope["sdk"]
            check_path(target)
            if target.exists():
                raise Blocked("SDK destination exists; reuse or review it")
            run_id = uuid4().hex
            scratch = self.ledger.local_directory("temp") / ("sdk-install-" + run_id)
            fields = {"run_id": run_id, "project_id": project_id, "scope": scope, "scope_sha256": approval,
                      "generation": len(events) + 1, "workspace": scratch.name}
            count, fence = self.ledger.append(len(events), tail, {**fields, "kind": "sdk_install_intent"},
                                              admission=require_admission)

            def check():
                self._check_diagnostic_fence(count, fence)
                if time() >= expires:
                    raise Blocked("SDK installation approval expired")

            self.ledger.local_directory("temp", create=True)
            check_path(scratch)
            scratch.mkdir()
            identity = scratch.stat().st_ino
            replica = Path(scope["replica"])
            try:
                output, observed = dotnet_install.install(scope, replica, scratch, check)
            except (Blocked, OSError, ValueError, KeyError, BadZipFile, HTTPException) as error:
                evidence = {"result": "BLOCKED", "reason": str(error)}
            else:
                try:
                    verified = dotnet_install.verify(scope, replica, scratch, output, check)
                except OwnedMxcStopped as error:
                    evidence = {"result": "BLOCKED", "reason": str(error)}
                else:
                    check()
                    if self.sdk_install_scope(project_id, launcher, expires) != scope:
                        raise Blocked("SDK project requirements changed")
                    parent.mkdir(parents=True, exist_ok=True)
                    check_path(target)
                    output.rename(target)
                    evidence = {"result": "PASS", "installed": str(target), **observed, **verified}
                    try:
                        identity = dotnet_install.identity_at(replica, target)
                    except Blocked:
                        identity = None
                    if identity != (scope["sdk"], verified["tool_sha256"]):
                        evidence.update(result="NOT_VERIFIED", reason="Published SDK identity could not be verified")
            self._check_diagnostic_fence(count, fence)
            cleanup = _remove_owned_tree(scratch, identity)
            if cleanup["status"] != "REMOVED":
                evidence.update(result="NOT_VERIFIED", reason="SDK installation cleanup unconfirmed")
            result = {**fields, **evidence, "cleanup": cleanup, "kind": "sdk_install_result"}
            self.ledger.append(count, fence, result, allow_shutdown=True)
            return result

    def build_verifier_scope(self, project_id, kind, launcher, sources, vectors, seconds, expires):
        from pathlib import Path
        from . import mxc_dotnet

        if kind != mxc_dotnet.KIND:
            raise Blocked("Build tool adapter is not supported")
        catalog = Catalog(self.ledger).read()
        setup = catalog["setups"].get(project_id)
        project = catalog["projects"].get(project_id)
        if not setup or not project:
            raise Blocked("Build verification requires a registered replica")
        if (type(expires) is not int or not time() < expires <= time() + 3600
                or type(seconds) is not int
                or seconds > min(catalog["global_budget"]["seconds"], project["data"]["budget"]["seconds"])):
            raise Blocked("Build verification expiry or budget is outside the project scope")
        from .dotnet_install import registered

        replica = Path(setup["replica"])
        scope = mxc_dotnet.review(replica, launcher, sources, vectors, seconds,
                                  registered(self.ledger, project_id, replica))
        return {**scope, "project_id": project_id, "project_revision": project["revision"],
                "role": "Verifier", "expires_epoch": expires}

    def build_verify(self, project_id, kind, launcher, sources, vectors, seconds, expires, approved_scope,
                     cancelled=lambda: False):
        from . import mxc_dotnet
        from .mxc_host_probe import OwnedMxcStopped
        from .owned_artifacts import cleanup, workspace

        with execution_slot() as abandoned:
            events, tail = self._idle_diagnostic(abandoned)
            scope = self.build_verifier_scope(project_id, kind, launcher, sources, vectors, seconds, expires)
            if digest(scope) != approved_scope:
                raise Blocked("Build verifier approval changed")
            run_id = uuid4().hex
            scratch = workspace(self.ledger, run_id)
            binding = {"project_id": project_id, "run_id": run_id, "task_id": run_id, "role": "Verifier",
                       "approval_id": uuid4().hex, "generation": len(events) + 1, "state": "DIAGNOSTIC",
                       "scope": scope, "scope_sha256": approved_scope}
            count, fence = self.ledger.append(len(events), tail, {
                **binding, "kind": "build_verifier_intent", "timestamp": datetime.now(timezone.utc).isoformat(),
                "cleanup_plan": {"classification": "AUTO_CLEAN", "workspace": scratch.name,
                                 "condition": "OWNED_JOBS_DRAINED"},
            }, admission=require_admission)

            def check():
                self._check_diagnostic_fence(count, fence)
                if cancelled():
                    raise Blocked("CANCELLED")
                if time() >= expires:
                    raise Blocked("APPROVAL_EXPIRED")

            check_path(self.ledger.local_directory("temp", create=True))
            scratch.mkdir()
            identity = scratch.stat().st_ino
            try:
                evidence = mxc_dotnet.run(self.ledger, scope, check, workspace=scratch)
            except OwnedMxcStopped as error:
                evidence = {"result": "NOT_VERIFIED", "reason": str(error), "job_active": 0}
            self._check_diagnostic_fence(count, fence)
            if evidence.get("job_active") != 0:
                raise Blocked("Build cleanup requires verified Job drain")
            removal = cleanup(self.ledger, run_id, scratch, identity)
            if removal["status"] != "REMOVED":
                evidence = {**evidence, "execution_result": evidence["result"], "result": "NOT_VERIFIED",
                            "reason": "BUILD_CLEANUP_UNCONFIRMED"}
            result = {**binding, **evidence, "kind": "build_verifier_result",
                      "cleanup": removal,
                      "timestamp": datetime.now(timezone.utc).isoformat()}
            self.ledger.append(count, fence, result, allow_shutdown=True)
            return result

    def _idle_diagnostic(self, abandoned):
        events, tail = self.ledger.snapshot(recover_journal=True)
        require_admission(events)
        run, development = last_run(events), session_view(events)
        if (abandoned or (run and State(run[-1]["state"]) not in TERMINAL)
                or (development and not development["end"])):
            raise Blocked("Diagnostic execution requires an idle Controller")
        return events, tail

    def _check_diagnostic_fence(self, count, fence):
        events, actual = self.ledger.snapshot()
        if len(events) != count or actual != fence:
            raise Blocked("Diagnostic execution fence changed")

    def gui_verification_receipt(self, project_id, *, role="Manager"):
        from .gui_verification import ROLES

        if "read-receipt" not in ROLES.get(role, ()):
            raise Blocked("GUI evidence access denied for this role")
        return next((event for event in reversed(self.ledger.read())
                     if event["project_id"] == project_id and (
                         event["kind"] in ("gui_validation_intent", "gui_validation_result")
                         or event["kind"] == "subscription_cycle_result" and "provenance" in event
                     )), None)

    @staticmethod
    def _gui_session_scope(scope):
        # Per-dispatch IDs still get checked in dispatch_gui_tool.
        return digest({key: value for key, value in scope.items()
                       if key not in ("generation", "task_id", "session_id", "cancel_token")})

    def approve_gui_session(self, reviewed_scope, exe_name, expires):
        """Owner entry point for three bounded runs of one registered GUI scenario."""
        with execution_slot() as abandoned:
            events, head = self._idle_diagnostic(abandoned)
            if type(expires) is not int or not time() < expires <= time() + 300:
                raise Blocked("GUI session approval must expire within five minutes")
            scope, _ = self._gui_verification_scope(
                reviewed_scope["project_id"], True, reviewed_scope["role"], exe_name, events,
            )
            if digest(scope) != digest(reviewed_scope):
                raise Blocked("GUI session scope changed; review it again")
            identifier = uuid4().hex
            self.ledger.append(len(events), head, {
                "kind": "gui_session_approved", "state": "DIAGNOSTIC", "project_id": scope["project_id"],
                "run_id": None, "session_id": identifier, "role": scope["role"],
                "generation": len(events) + 1, "scope_sha256": self._gui_session_scope(scope),
                "expires": expires, "max_runs": 3, "timestamp": datetime.now(timezone.utc).isoformat(),
            }, admission=require_admission)
            return identifier

    def _require_gui_session(self, events, session_id, scope):
        session = next((event for event in reversed(events) if event["kind"] == "gui_session_approved"
                        and event["session_id"] == session_id), None)
        if (not session or time() >= session["expires"]
                or session["scope_sha256"] != self._gui_session_scope(scope)):
            raise Blocked("GUI session expired or scope changed")
        subsequent = events[session["generation"]:]
        if any(event["kind"] == "gui_validation_result" and event.get("gui_session_id") == session_id
               and event.get("reason") == "CANCELLED" for event in subsequent):
            raise Blocked("GUI session cancelled; review it again")
        uses = sum(event["kind"] == "gui_validation_intent" and event.get("gui_session_id") == session_id
                   for event in subsequent)
        if uses >= session["max_runs"] or any(event["kind"].startswith("shutdown_") for event in subsequent):
            raise Blocked("GUI session exhausted or invalidated by shutdown")
        return session["expires"]

    def dispatch_gui_tool(self, request, owner_scope_sha256, cancelled=lambda: False):
        """Client tool request. The approval and cancellation source come from the owner channel."""
        keys = {"tool", "project_id", "role", "generation", "task_id", "session_id", "cancel_token", "exe_name"}
        if (type(request) is not dict or set(request) != keys or type(request["generation"]) is not int
                or any(type(request[key]) is not str for key in keys - {"generation"})
                or request["tool"] != "gui_verify_registered_exe"):
            raise Blocked("Unrecognized GUI tool request")
        return self.verify_gui_fixture(request["project_id"], owner_scope_sha256, cancelled,
                                       registered=True, role=request["role"], exe_name=request["exe_name"],
                                       tool_request_binding=request)

    def verify_gui_fixture(self, project_id, approved_scope, cancelled=lambda: False, *,
                           registered=False, role="Verifier", exe_name=None, tool_request_binding=None,
                           gui_session_id=None):
        """Run the approved deterministic GUI scenario in an owner-driven slot."""
        from contextlib import ExitStack
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from .gui_verification import run_fixture
        from .system_powershell import hold_system_powershell

        with execution_slot() as abandoned, ExitStack() as resources:
            events, tail = self._idle_diagnostic(abandoned)
            if cancelled():
                raise Blocked("GUI verification was cancelled before admission")
            scope, (form, exe_data) = self._gui_verification_scope(project_id, registered, role, exe_name, events)
            if form is not None and exe_data is None and any(
                    step["op"] == "menu" for scene in form["scenes"] for step in scene["steps"]):
                raise Blocked("NOT_VERIFIED: native menu requires the Controller-built EXE")
            if gui_session_id is not None:
                if not registered or approved_scope is not None or tool_request_binding is not None:
                    raise Blocked("GUI session requires an owner-channel registered scenario request")
                expires = self._require_gui_session(events, gui_session_id, scope)
                original_cancel = cancelled
                cancelled = lambda: original_cancel() or time() >= expires
            elif digest(scope) != approved_scope:
                raise Blocked("GUI verification scope changed; review it again")
            if tool_request_binding is not None:
                from .gui_exe import tool_request

                if exe_data is None or tool_request(scope) != tool_request_binding:
                    raise Blocked("GUI tool request does not match the current execution binding")
            executable = (resources.enter_context(hold_system_powershell(scope["exe_sha256"]))
                          if exe_data is None else None)
            identifier = uuid4().hex
            binding = {"project_id": project_id, "run_id": identifier, "task_id": scope.get("task_id", identifier),
                       "agent_run_id": identifier, "approval_id": uuid4().hex,
                       "generation": len(events) + 1, "role": scope["role"], "state": "DIAGNOSTIC",
                       "source": ("WINDOWS_TRUSTED_PROJECT_EXE" if exe_data is not None else
                                  "WINDOWS_OWNED_PROJECT_FORM" if registered else "WINDOWS_OWNED_GUI_FIXTURE"),
                       "scope": scope,
                       "scope_sha256": digest(scope)}
            if gui_session_id is not None:
                binding["gui_session_id"] = gui_session_id
            count, fence = self.ledger.append(len(events), tail, {
                **binding, "kind": "gui_validation_intent", "timestamp": datetime.now(timezone.utc).isoformat(),
            }, admission=require_admission)

            temp = self.ledger.local_directory("temp", create=True)
            with TemporaryDirectory(prefix="gui-verification-", dir=temp) as staging:
                receipt = run_fixture(scope, Path(staging), cancelled, lambda: self._check_diagnostic_fence(count, fence),
                                      executable, form, exe_data)
                if exe_data is not None:
                    receipt["working_directory"] = staging
                if receipt["scenes"]:
                    artifacts = self.ledger.local_directory("logs", create=True) / f"gui-{identifier}"
                    check_path(artifacts)
                    artifacts.mkdir()
                    images = [scene[key] for scene in receipt["scenes"] for key in ("original", "processed")]
                    if receipt["comparison"]:
                        images.append(receipt["comparison"])
                    for image in images:
                        name = image["file"]
                        (Path(staging) / name).replace(artifacts / name)
                    receipt["artifact_dir"] = artifacts.name
            resources.close()
            result = {**binding, **receipt, "kind": "gui_validation_result",
                      "timestamp": datetime.now(timezone.utc).isoformat()}
            self.ledger.append(count, fence, result, allow_shutdown=True)
            return result

    def manager_context(self, project_id, development_id):
        """Assemble owner-selected documents in memory from a frozen session."""
        events, _ = self.ledger.snapshot()
        view = session_view(events, project_id)
        if not view or view["start"]["development_id"] != development_id:
            raise Blocked("Explicit latest project session required")
        start = view["start"]
        frozen = start["frozen"]
        return {
            "project_id": project_id, "development_id": development_id,
            "goal": frozen["goal"], "acceptance": frozen["acceptance"],
            "requirements": read_frozen_requirements(
                Catalog(self.ledger).read(), project_id, frozen["requirements"], self.ledger.state_boundary,
            ),
            "previous_cycle": start.get("previous_cycle"),
            "clarifications": [
                {"question_id": event["question_id"], "answer": event["answer"]}
                for event in events if event["kind"] in ("question_responded", "native_manager_question_responded")
                and event.get("question_development_id", event.get("development_id")) == development_id
                and event["project_id"] == project_id
                and event["question_type"] == "CLARIFICATION" and event["decision"] == "ANSWER"
            ],
        }

    def prepare_tool_free_manager(self, approval, request, *, development_id=None):
        """Keep the previous entry point explicitly closed in subscription-only mode."""
        raise Blocked("RESPONSES_DISABLED_SUBSCRIPTION_ONLY")

    def native_manager_context(self, project_id):
        """Read a registered planning baseline without opening a synthetic session."""
        catalog = Catalog(self.ledger).read()
        record = catalog["projects"].get(project_id)
        if not record:
            raise Blocked("Register a project before preparing a Manager request")
        project = record["data"]
        refs = latest_requirements(catalog, project_id)
        if not refs:
            raise Blocked("Register at least one requirement for the Manager")
        files = project["requested_scope"]["files"]
        if any(not _path_allowed(path) for path in files) or len(set(map(str.casefold, files))) != len(files):
            raise Blocked("Manager proposal scope contains an ambiguous or protected path")
        baseline = digest([project_id, record["revision"], refs])
        role = project["roles"]["Manager"]
        limits = {key: min(value, role["budget"][key], catalog["global_budget"][key])
                  for key, value in project["budget"].items()}
        return {
            "project_id": project_id, "development_id": baseline,
            "project_revision": record["revision"], "registered_git_sha": project["git_sha"],
            "goal": project["goal"], "acceptance": project["acceptance"],
            "model": role["model"], "reasoning": role["reasoning"], "budget_ceiling": limits,
            "proposed_files_ceiling": files,
            "requirements": read_frozen_requirements(catalog, project_id, refs, self.ledger.state_boundary),
            "clarifications": [
                {"question_id": event["question_id"], "answer": event["answer"]}
                for event in self.ledger.read() if event["kind"] == "native_manager_question_responded"
                and event.get("question_development_id") == baseline and event["project_id"] == project_id
                and event["question_type"] == "CLARIFICATION" and event["decision"] == "ANSWER"
            ],
        }

    def inspect_manager_admission(self, project_id, backend=ModelBackend.APP_SERVER):
        """Read-only preview for the project picker. It cannot grant a turn."""
        decision = manager_backend_status(backend)
        if backend != ModelBackend.APP_SERVER:
            return {**decision, "summary": None}

        try:
            context = self.native_manager_context(project_id)
        except (Blocked, OSError, ValueError):
            return {"backend": decision["backend"], "state": "BLOCKED", "reason": "REQUIREMENTS_UNAVAILABLE",
                    "message": "프로젝트에 현재 내용과 일치하는 요구사항을 등록하세요.", "summary": None}
        return {**decision, "summary": {
            "model": context["model"], "reasoning": context["reasoning"],
            "requirements": len(context["requirements"]), "input_sha256": digest(context),
            "budget_ceiling": context["budget_ceiling"],
        }}

    def review_native_manager(self, project_id, binary, cli_sha256, home, expires, *,
                              backend=ModelBackend.APP_SERVER):
        """Freeze an owner-reviewable, expiring one-shot request without starting Codex."""
        from pathlib import Path

        from .app_server import verify_app_server_binary
        from .native_manager import MANAGER_CONFIG, review_manager_layers

        require_manager_backend(backend)
        if type(expires) is not int or not time() < expires <= time() + 300:
            raise Blocked("Manager review expiry must be within the next five minutes")
        events, head = self.ledger.snapshot()
        require_admission(events)
        setup = Catalog(self.ledger).read()["setups"].get(project_id)
        if not setup:
            raise Blocked("Register the Manager work copy before reviewing a turn")
        baseline = self.native_manager_context(project_id)
        if baseline["model"] in ("mock-v1", "deterministic"):
            raise Blocked("Select a subscription model in the project Manager settings")
        replica = Path(setup["replica"])
        binary, home = Path(binary), Path(home)
        verify_app_server_binary(binary, approved_sha256=cli_sha256)
        layers = review_manager_layers(self.ledger, project_id, replica, home)
        budget = {**baseline["budget_ceiling"], "seconds": min(60, baseline["budget_ceiling"]["seconds"]),
                  "cost_microunits": 0}
        return {
            "contract": "manager-owner-turn-v2", "backend": ModelBackend(backend).value,
            "role": "Manager", "project_id": project_id,
            "generation": len(events) + 1, "ledger_head": head, "input_sha256": digest(baseline),
            "project_revision": baseline["project_revision"], "registered_git_sha": baseline["registered_git_sha"],
            "binary": str(binary), "cli_sha256": cli_sha256, "replica": str(replica), "home": str(home),
            "config_ref": digest(MANAGER_CONFIG), "instruction_layers": list(layers),
            "model": baseline["model"], "reasoning": baseline["reasoning"], "budget": budget,
            "expires": expires, "readable": [str(replica)], "writable": [], "tools": [], "network": [],
            "authentication": "CHATGPT_SUBSCRIPTION_ONLY",
        }

    def run_reviewed_native_manager(self, project_id, binary, cli_sha256, home, expires, approved_sha256, *,
                                    backend=ModelBackend.APP_SERVER):
        """Materialize a reviewed request and defer all authority to native admission."""
        from pathlib import Path

        from .native_manager import ManagerApproval, manager_request_hash

        review = self.review_native_manager(project_id, binary, cli_sha256, home, expires, backend=backend)
        if digest(review) != approved_sha256:
            raise Blocked("Manager review changed; review the current request again")
        baseline = self.native_manager_context(project_id)
        if digest(baseline) != review["input_sha256"]:
            raise Blocked("Manager requirements changed after review")
        layers = tuple(review["instruction_layers"])
        budget = Budget(**review["budget"])
        ids = {key: digest([key, approved_sha256])[:32] for key in ("approval", "run", "task", "agent")}
        request = RoleRequest(
            project_id, ids["task"], ids["run"], review["generation"], ids["agent"], "Manager",
            review["model"], review["reasoning"], baseline["development_id"],
            Scope(review["replica"], (ids["approval"],), readable=(review["replica"],)),
            budget, (review["input_sha256"],), baseline["goal"], min(expires, time() + budget.seconds),
        )
        approval = ManagerApproval(
            ids["approval"], expires, Path(review["binary"]), Path(review["replica"]), Path(review["home"]),
            layers, manager_request_hash(request, layers, cli_sha256), budget.tokens, 1024, cli_sha256,
        )
        return self.plan_native_manager(approval, request, reviewed_head=review["ledger_head"],
                                        owner_review_sha256=approved_sha256)

    def plan_native_manager(self, approval, request, *, reviewed_head=None, owner_review_sha256=None):
        """Internal one-shot planning entry; never grants another role."""
        from pathlib import Path
        from .native_manager import (
            ManagerApproval, ManagerTurnUnsettled, ROLE, prepare_manager, require_live_manager_admission,
            run_owned_manager,
        )
        from .role_contract import TransitionEvidence, confirm_task_plan, decide_transition

        require_live_manager_admission(approval)
        if type(approval) is not ManagerApproval or type(request) is not RoleRequest:
            raise Blocked("Current Manager owner approval required")
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            require_admission(events)
            run, development = last_run(events), session_view(events)
            if (abandoned or request.generation != len(events) + 1
                    or (reviewed_head is not None and reviewed_head != tail)
                    or (run and run[-1]["state"] not in TERMINAL)
                    or (development and not development["end"])
                    or any(event.get("request_id") == approval.request_id
                           or (event["kind"] == "native_manager_intent"
                               and event["run_id"] == request.run_id)
                           for event in events)):
                raise Blocked("Manager requires idle, unused admission")
            catalog = Catalog(self.ledger).read()
            setup = catalog["setups"].get(request.project_id)
            if not setup or Path(setup["replica"]) != approval.replica:
                raise Blocked("Manager replica does not match Controller registration")
            context = {}
            if request.project_id in catalog.get("projects", {}):
                baseline = self.native_manager_context(request.project_id)
                if (request.model != baseline["model"] or request.reasoning != baseline["reasoning"]
                        or any(value > baseline["budget_ceiling"][key]
                               for key, value in asdict(request.budget).items())):
                    raise Blocked("Manager model or budget exceeds the registered contract")
                context["project_context"] = baseline
            _, _, sizing = prepare_manager(self.ledger, approval, request, include_input_receipt=True, **context)
            fields = {key: getattr(request, key) for key in (
                "project_id", "task_id", "run_id", "generation", "agent_run_id", "context_id", "role",
            )}
            fields.update(state="DIAGNOSTIC", source="WINDOWS_NATIVE_CODEX",
                          manager_backend=approval.backend, cli_sha256=approval.cli_sha256,
                          request_id=approval.request_id, request_hash=approval.request_hash,
                          model=request.model, reasoning=request.reasoning, budget=asdict(request.budget),
                          input_sizing=sizing,
                          deadline=request.deadline_utc_epoch, instruction_layers=list(approval.layers),
                          timestamp=datetime.now(timezone.utc).isoformat())
            if context:
                fields["question_development_id"] = context["project_context"]["development_id"]
            if owner_review_sha256 is not None:
                fields["owner_review_sha256"] = owner_review_sha256
            count, head = self.ledger.append(len(events), tail, {
                **fields, "kind": "native_manager_intent",
            }, admission=require_admission)
            dispatch_events = []

            def cancelled():
                current, _ = self.ledger.snapshot()
                return time() >= request.deadline_utc_epoch or shutdown_state(current)["state"] in HELD

            def record_dispatch(kind, expected_count):
                nonlocal count, head
                require_live_manager_admission(approval)
                if context and self.native_manager_context(request.project_id) != context["project_context"]:
                    raise Blocked("Manager requirements changed before dispatch")
                _, _, current_sizing = prepare_manager(
                    self.ledger, approval, request, include_input_receipt=True, **context,
                )
                if current_sizing != sizing:
                    raise Blocked("Manager wire contract changed before dispatch")

                def admit(current):
                    if (len(dispatch_events) != expected_count or time() >= request.deadline_utc_epoch
                            or shutdown_state(current)["state"] in HELD or pending_budget_stop(current)):
                        raise Blocked("Manager dispatch admission closed")

                entry = {**fields, "kind": kind}
                count, head = self.ledger.append(count, head, entry, allow_shutdown=True, admission=admit)
                dispatch_events.append(entry)

            try:
                proposal, turn_receipt = run_owned_manager(
                    self.ledger, approval, request,
                    lambda: record_dispatch("native_manager_thread_dispatch", 0),
                    lambda: record_dispatch("native_manager_dispatch", 1),
                    cancelled, **context,
                )
                if len(dispatch_events) != 2:
                    raise Blocked("Manager dispatch receipt missing")
            except ManagerTurnUnsettled as error:
                raise Blocked(f"Manager outcome UNKNOWN; no automatic retry ({error})") from None
            except Exception:
                # Keep intent unresolved, including when dispatch may have reached the server.
                raise Blocked("Manager outcome UNKNOWN; no automatic retry") from None
            result = {**fields, "kind": "native_manager_result", "owned_tree": "DRAINED",
                      "proposal_sha256": digest(proposal), "usage": "UNKNOWN",
                      "authentication": turn_receipt["authentication"], "turn_receipt": turn_receipt,
                      "next_role_admission": "BLOCKED"}
            # Decide only from the verified result inside the commit transaction.
            def settle(current, current_head):
                if current_head != head and (
                        not current[count:] or any(e["kind"] != "shutdown_scheduled" for e in current[count:])):
                    raise Blocked("Manager result fence changed")
                if current[:count] != events + [{**fields, "kind": "native_manager_intent"}, *dispatch_events]:
                    raise Blocked("Manager evidence prefix changed")
                evidence = TransitionEvidence(
                    owned_tree_drained=True, scope_released=True, receipt_committed=True,
                    admission_open=(time() < request.deadline_utc_epoch
                                    and shutdown_state(current)["state"] not in HELD),
                    next_role_and_reserve_funded=False, rework_remaining=0, repeated_failure=False,
                )
                result["transition_preview"] = decide_transition(ROLE, canonical(proposal).encode(), evidence)
                if result["transition_preview"] == "CLARIFICATION":
                    result["question"] = new_question(
                        "CLARIFICATION", proposal["task_spec"], proposal["priority_reason"],
                        "Current requirements baseline", "Provide the intended requirement in your own words",
                        "Keep the plan blocked pending a new owner decision",
                        expires=min(time() + 300, approval.expires_epoch),
                    )
                if context and proposal["disposition"] == "PROPOSED":
                    baseline = context["project_context"]
                    try:
                        candidate = confirm_task_plan(
                            canonical(proposal).encode(), allowed_files=baseline["proposed_files_ceiling"],
                            acceptance=baseline["acceptance"], budget=baseline["budget_ceiling"],
                            authority_ref=approval.request_hash,
                        )
                    except Blocked:
                        result["transition_preview"] = "BLOCKED"
                        result["task_candidate_status"] = "OUTSIDE_CONTROLLER_CONTRACT"
                    else:
                        result["task_candidate_status"] = "OWNER_SCOPE_REQUIRED"
                        result["task_candidate_ref"] = digest(candidate)
                return result

            try:
                if context and self.native_manager_context(request.project_id) != context["project_context"]:
                    raise Blocked("Manager requirements changed before result settlement")
                self.ledger.update(settle)
            except Exception:
                raise Blocked("Manager result commit unverified; no automatic retry") from None
            return result, proposal

    def inspect_native(self, approval):
        """Internal owner entry for a model-free handshake or fixed access probe."""
        from .app_server import InspectionBlocked, NativeInspectionApproval, save_preflight_diagnostic
        from .native_access_probe import NativeAccessApproval, NativeBoundaryApproval
        from .native_manager import ManagerPolicyApproval

        inspection = (approval.inspection if type(approval) in (NativeAccessApproval, NativeBoundaryApproval,
                                                                ManagerPolicyApproval)
                      else approval)
        request_id = None
        if (type(inspection) is NativeInspectionApproval and isinstance(inspection.request_id, str)
                and re.fullmatch(r"[0-9a-f]{32}", inspection.request_id)):
            request_id = inspection.request_id
        try:
            return self._inspect_native(approval, request_id)
        except (InspectionBlocked, SlotBlocked) as error:
            failure = (InspectionBlocked("EXECUTION_SLOT", error.code)
                       if isinstance(error, SlotBlocked) else error)
            try:
                save_preflight_diagnostic(self.ledger, request_id, failure)
            except (Blocked, OSError):
                raise Blocked(f"{failure.code}; PREFLIGHT_RECEIPT_WRITE_FAILED") from None
            raise failure from None

    def _inspect_native(self, approval, request_id):
        from contextlib import ExitStack
        from math import isfinite
        from .app_server import (
            InspectionBlocked, inspection_check, open_app_server, prepare_inspection,
            save_inspection_diagnostic, verify_app_server_binary,
        )
        from .mxc_launcher import CLI_HASH, CLI_VERSION
        from .native_manager import MANAGER_CONFIG, MANAGER_POLICY_METHODS, ManagerPolicyApproval, inspect_manager_policy
        from .native_access_probe import (
            NativeAccessApproval, NativeBoundaryApproval, command_request, native_boundary_contract,
            prepare_probe, probe_receipt, verify_native_boundary,
        )

        access = approval if type(approval) is NativeAccessApproval else None
        boundary = approval if type(approval) is NativeBoundaryApproval else None
        manager_policy = type(approval) is ManagerPolicyApproval
        if access or boundary or manager_policy:
            approval = approval.inspection
        if (request_id is None or type(approval.expires_epoch) not in (int, float)
                or (type(approval.expires_epoch) is float and not isfinite(approval.expires_epoch))
                or (access and (not isinstance(access.powershell_sha256, str)
                                or not re.fullmatch(r"[0-9a-f]{64}", access.powershell_sha256)))
                or (boundary and any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                                     for value in (boundary.probe_sha256, boundary.config_sha256,
                                                   boundary.request_sha256)))):
            raise InspectionBlocked("APPROVAL", "APPROVAL_INVALID")

        def check_approval():
            if time() >= approval.expires_epoch:
                raise InspectionBlocked("APPROVAL", "APPROVAL_EXPIRED")

        def admit(current):
            check_approval()
            if any(event.get("request_id") == request_id for event in current):
                raise InspectionBlocked("ADMISSION", "DUPLICATE_REQUEST")
            if shutdown_state(current)["state"] in HELD:
                raise InspectionBlocked("ADMISSION", "SHUTDOWN_ADMISSION_CLOSED")
            if native_unresolved(current):
                raise InspectionBlocked("ADMISSION", "PRIOR_NATIVE_UNRESOLVED")
            if diagnostic_unresolved(current):
                raise InspectionBlocked("ADMISSION", "PRIOR_DIAGNOSTIC_UNRESOLVED")
            if pending_budget_stop(current):
                raise InspectionBlocked("ADMISSION", "PENDING_BUDGET_STOP")
            if pending_question(current):
                raise InspectionBlocked("ADMISSION", "USER_QUESTION_PENDING")

        check_approval()
        with execution_slot() as abandoned, ExitStack() as resources:
            if abandoned:
                raise InspectionBlocked("EXECUTION_SLOT", "ABANDONED_SLOT")
            with inspection_check("LEDGER", "LEDGER_INTEGRITY_FAILED"):
                events, tail = self.ledger.snapshot(recover_journal=True)
                admit(events)
            run, development = last_run(events), session_view(events)
            if (run and run[-1]["state"] not in TERMINAL) or (development and not development["end"]):
                raise InspectionBlocked("ADMISSION", "ACTIVE_DEVELOPMENT")
            with inspection_check("SYNTHETIC_LAB", "SYNTHETIC_LAB_INVALID"):
                options = {}
                lab_parent = self.ledger.local_directory("temp")
                if manager_policy:
                    options["manager_config"] = MANAGER_CONFIG.encode("ascii")
                if boundary:
                    if approval.lab_root != lab_parent / f"app-server-{request_id}":
                        raise InspectionBlocked("SYNTHETIC_LAB", "SYNTHETIC_LAB_PARENT_MISMATCH")
                    config, candidate = native_boundary_contract(approval.lab_root / "work")
                    options["native_probe_config"] = config
                work, environment, instructions = prepare_inspection(
                    approval, lab_parent, access_probe=bool(access or boundary),
                    **options,
                )
            if boundary:
                verify_native_boundary(work, boundary, config, candidate)
                verify_app_server_binary(approval.binary)
                # An approved draft and matching executable do not establish loader compatibility.
                raise InspectionBlocked("COMMAND_POLICY", "NATIVE_LOADER_SCOPE_UNVERIFIED")
            fields = {"project_id": None, "run_id": None, "state": "DIAGNOSTIC",
                      "source": "WINDOWS_NATIVE_CODEX", "request_id": approval.request_id,
                      "instruction_sources": instructions}
            if manager_policy:
                fields.update(probe_kind="MANAGER_CONFIG_ONLY", config_ref=digest(MANAGER_CONFIG),
                              cli_sha256=CLI_HASH, cli_version=CLI_VERSION)
            if access:
                argv, recheck_probe = resources.enter_context(prepare_probe(work, access.powershell_sha256))
                command = command_request(work, argv)
                fields.update(probe_kind="RESTRICTED_FILE_ACCESS", command_hash=digest(command),
                              cli_sha256=CLI_HASH, cli_version=CLI_VERSION,
                              powershell_sha256=access.powershell_sha256)

            def cancelled():
                current, _ = self.ledger.snapshot()
                return time() >= approval.expires_epoch or shutdown_state(current)["state"] in HELD

            with inspection_check("LEDGER", "LEDGER_INTENT_WRITE_FAILED"):
                count, head = self.ledger.append(len(events), tail, {
                    **fields, "kind": "native_inspection_intent",
                }, admission=admit)
            server = None
            try:
                with inspection_check("LEDGER", "LEDGER_INTEGRITY_FAILED"):
                    current, _ = self.ledger.snapshot()
                check_approval()
                if shutdown_state(current)["state"] in HELD:
                    raise InspectionBlocked("ADMISSION", "SHUTDOWN_ADMISSION_CLOSED")
                options = {"explicit_mxc": True} if access or manager_policy else {}
                with open_app_server(approval.binary, work, environment, 15, cancelled, **options) as server:
                    if manager_policy:
                        server.restrict_outbound(MANAGER_POLICY_METHODS)
                    policy = inspect_manager_policy(server, work) if manager_policy else server.inspect_policy()
                    if access:
                        if policy != {"sandbox": "read-only", "approval": "never",
                                      "managed_requirements_present": False}:
                            raise Blocked("Access probe App Server policy mismatch")
                        recheck_probe()

                        def admit_command(current):
                            if time() >= approval.expires_epoch or shutdown_state(current)["state"] in HELD:
                                raise Blocked("Access probe admission closed")

                        count, head = self.ledger.append(count, head, {
                            **fields, "kind": "native_inspection_dispatch",
                        }, allow_shutdown=True, admission=admit_command)
                        recheck_probe()
                        wire_result = server.request(3, "command/exec", command)
                resources.close()
            except Exception as error:
                if server is None and isinstance(error, InspectionBlocked):
                    raise
                # Preserve the durable unresolved intent without raw process errors.
                try:
                    save_inspection_diagnostic(self.ledger, approval.request_id, server,
                                               "PROTOCOL_OR_CLEANUP_UNVERIFIED")
                except (Blocked, OSError):
                    raise Blocked("App Server unresolved; diagnostic write failed") from None
                raise Blocked("App Server inspection or owned drain unverified") from None
            result = {**fields, "kind": "native_inspection_result", "policy": policy,
                      "instruction_loading": "NOT_VERIFIED", "owned_tree": "DRAINED"}
            if manager_policy:
                result.update(config_check="MATCHED", tool_exposure="NOT_VERIFIED",
                              thread_policy="NOT_VERIFIED", model_dispatched=False)
            if access:
                result["access"] = probe_receipt(wire_result, work, command)
            try:
                self.ledger.append(count, head, result, allow_shutdown=True)
            except Exception:
                try:
                    save_inspection_diagnostic(self.ledger, approval.request_id, server,
                                               "RESULT_COMMIT_UNVERIFIED")
                except (Blocked, OSError):
                    raise Blocked("App Server result unresolved; diagnostic write failed") from None
                raise Blocked("App Server result commit unverified") from None
            save_inspection_diagnostic(self.ledger, approval.request_id, server, "RESULT_COMMITTED")
            return result

    def native_step(self, expected_run):
        """Explicit owner entry. It never falls back to a synthetic role."""
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            require_admission(events)
            work = _Session(self.ledger, len(events), tail, last_run(events))
            if (abandoned or work.pending_job or work.state not in PHASES
                    or work.ids["run_id"] != expected_run
                    or work.run[-1]["kind"] in OPEN_ROLE_EVENTS):
                raise Blocked("Native role admission or previous ownership unverified")
            catalog = Catalog(self.ledger).read()
            if any(event["kind"] == "instruction_baseline_pinned" for event in work.run):
                self._pinned_instructions(work.run, catalog)
            return work.step(catalog, native=WindowsNativeCodexAdapter())

    def pin_instruction_baseline(self, expected_run):
        """Freeze the owner-approved version before a role starts."""
        from pathlib import Path
        from .instructions import InstructionStore

        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            require_admission(events)
            run = last_run(events)
            if (abandoned or not run or run[0]["run_id"] != expected_run
                    or run[-1]["state"] not in (State.PRECHECK, State.NEED_APPROVAL, State.PLANNING)
                    or any(event["kind"] in OPEN_ROLE_EVENTS or event["kind"] == "role_finished"
                           for event in run)):
                raise Blocked("Instruction baseline requires a pre-role boundary")
            catalog = Catalog(self.ledger).read()
            project_id = run[0]["project_id"]
            setup = catalog["setups"].get(project_id)
            store = InstructionStore(self.ledger)
            pin = store.active(project_id)
            if not setup or not pin or any(e["kind"] == "instruction_baseline_pinned" for e in run):
                raise Blocked("A new approved instruction baseline and replica are required")
            store.verify(pin, Path(setup["replica"]))
            event = {**{key: run[0][key] for key in ("project_id", "run_id", "task_id", "generation")},
                     "kind": "instruction_baseline_pinned", "state": run[-1]["state"],
                     "source": "OWNER_INSTRUCTIONS", "instruction_pin": pin}
            self.ledger.append(len(events), tail, event, admission=require_admission)
            return event

    def _pinned_instructions(self, run, catalog):
        from pathlib import Path
        from .instructions import InstructionStore

        pin = next((event["instruction_pin"] for event in run
                    if event["kind"] == "instruction_baseline_pinned"), None)
        setup = catalog["setups"].get(run[0]["project_id"])
        if not setup or not pin or pin["project_id"] != run[0]["project_id"]:
            raise Blocked("Pinned instructions or replica missing")
        return InstructionStore(self.ledger).verify(pin, Path(setup["replica"]))

    def stage_instruction_home(self, expected_run):
        """Prepare a new private CODEX_HOME for the pinned role, without launching it."""
        from pathlib import Path
        from .instructions import InstructionStore
        from .ledger import check_path

        with execution_slot() as abandoned:
            events, _ = self.ledger.snapshot(recover_journal=True)
            require_admission(events)
            run = last_run(events)
            if (abandoned or not run or run[0]["run_id"] != expected_run
                    or run[-1]["state"] not in PHASES):
                raise Blocked("No current role for instruction staging")
            catalog = Catalog(self.ledger).read()
            self._pinned_instructions(run, catalog)
            pin = next(event["instruction_pin"] for event in run
                       if event["kind"] == "instruction_baseline_pinned")
            replica = Path(catalog["setups"][run[0]["project_id"]]["replica"])
            parent = self.ledger.local_directory("temp", create=True) / "native-homes"
            check_path(parent)
            parent.mkdir(exist_ok=True)
            return InstructionStore(self.ledger).stage_home(pin, replica, parent / uuid4().hex)

    def command(self, action, project="fixture-a", scenario=Scenario.NORMAL, expected_run=None):
        if action == "run":
            event = self.command("step", project, scenario, expected_run)
            while event["state"] in PHASES and not event.get("shutdown_reason"):
                event = self.command("step", project, scenario, expected_run)
            return event
        with execution_slot() as abandoned:
            if action == "init":
                if abandoned:
                    raise Blocked("Abandoned slot requires inspection")
                self.ledger.initialize()
                return None
            events, tail = self.ledger.snapshot(recover_journal=True)
            shutdown = shutdown_state(events)["state"]
            if shutdown in HELD and not (shutdown == "SCHEDULED" and action in ("cancel", "recover")):
                raise Blocked("Shutdown has closed admission")
            session = _Session(self.ledger, len(events), tail, last_run(events))
            if session.pending_job:
                return session.recover_job(action, expected_run)
            if action == "start":
                development = session_view(events)
                if development and not development["end"]:
                    raise Blocked("Development session owns admission")
            catalog = Catalog(self.ledger).read() if action in ("start", "step") else None
            return session.apply(action, project, scenario, expected_run, abandoned, catalog)

    def shutdown(self, action, request_id=None, *, wait_seconds=10, expected_generation=None):
        if action == "request":
            return self._reserve_shutdown(request_id, wait_seconds, expected_generation)
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            current = shutdown_state(events)
            run = last_run(events)
            if action == "resume" and any(event["kind"] == "instruction_baseline_pinned" for event in run):
                self._pinned_instructions(run, Catalog(self.ledger).read())
            unresolved = (abandoned or diagnostic_unresolved(events) or native_unresolved(events)
                          or any(event["kind"] in UNKNOWN for event in run[-1:])
                          or any(event["kind"] == "recovery" for event in run))
            if current.get("abandoned"):
                unresolved = True
            unsettled = pending_budget_stop(events)
            if action == "finalize" and current["state"] == "SCHEDULED" and unsettled:
                current = {**current, "reason": unsettled["shutdown_reason"],
                           "budget_status": unsettled.get("shutdown_budget")}
            evidence = None
            if (action == "finalize" and current["state"] == "SCHEDULED" and not unresolved
                    or action == "resume" and current["state"] == "READY"):
                # READY must be the latest event. Rebuild its exact input
                # checkpoint from the preceding, independently verified prefix.
                prefix = events
                evidence_tail = tail
                if action == "resume":
                    if events[-1] != current:
                        raise Blocked("Checkpoint is no longer the ledger head")
                    prefix = events[:-1]
                    evidence_tail = ""
                    for sequence, event in enumerate(prefix, 1):
                        evidence_tail = digest([sequence, evidence_tail, event])
                evidence = checkpoint(run, Catalog(self.ledger).read(), session_view(prefix),
                                      POLICY_HASH, len(prefix), evidence_tail)
            if abandoned and current["state"] == "READY":
                event = {**current, "kind": "shutdown_recovery_required", "state": "RECOVERY_REQUIRED",
                         "reason": "ABANDONED_SLOT", "child_drain": "UNKNOWN", "external_requests": "UNKNOWN"}
                del event["checkpoint"]
                self.ledger.append(len(events), tail, event)
                return event
            event = transition(action, current, evidence, request_id=request_id, unresolved=unresolved,
                               now=time(), wait_seconds=wait_seconds)
            if event is not current:
                self.ledger.append(len(events), tail, event)
            return event

    def _reserve_shutdown(self, request_id, wait_seconds, expected_generation):
        # Only intent can write without the execution slot. The write transaction
        # orders it against role/task admission, even across controllers.
        if request_id is not None and (not isinstance(request_id, str)
                                       or not re.fullmatch("[0-9a-f]{32}", request_id)):
            raise Blocked("Invalid shutdown request ID")
        if request_id is not None and expected_generation is None:
            raise Blocked("Explicit shutdown request requires a generation")
        result = None

        def choose(events, _tail):
            nonlocal result
            current = shutdown_state(events)
            run = last_run(events)
            generation = run[0]["generation"] if run else 0
            if expected_generation is not None and expected_generation != generation:
                raise Blocked("Stale shutdown generation")
            if request_id is not None and (
                current["state"] in HELD and current.get("request_id") != request_id
                or current["state"] not in HELD and any(
                    event.get("request_id") == request_id for event in events
                )
            ):
                raise Blocked("Conflicting or replayed shutdown request")
            result = transition("request", current, None, request_id=request_id, unresolved=False,
                                now=time(), wait_seconds=wait_seconds)
            if result is current:
                return None
            result["generation"] = generation
            result["bound_run_id"] = run[0]["run_id"] if run else None
            unsettled = pending_budget_stop(events)
            if unsettled:
                result.update(reason=unsettled["shutdown_reason"], budget_status=unsettled.get("shutdown_budget"))
            return result

        self.ledger.update(choose)
        return result

    def development(self, action, *, project="fixture-a", development_id=None, limits=None,
                    cycles=2, retries=0, task_limits=None, scenario=Scenario.NORMAL):
        """Owner-driven scheduler tick. Every task still requires its own approval."""
        from .catalog import validate_budget
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            if shutdown_state(events)["state"] in HELD:
                raise Blocked("Shutdown has closed session admission")
            work = _Session(self.ledger, len(events), tail, last_run(events))
            if abandoned or work.pending_job or work.state not in (State.IDLE, *TERMINAL):
                raise Blocked("Previous work must be resolved before a session transition")
            view = session_view(events)

            def record(kind, project_id, identity, **fields):
                event = {"kind": kind, "project_id": project_id, "development_id": identity,
                         "run_id": None, "state": fields.pop("state", "ACTIVE"), "source": "MOCK_SESSION",
                         "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
                self.ledger.append(len(events), tail, event, admission=require_admission)
                return event

            if action == "start":
                if view and not view["end"]:
                    raise Blocked("A development session already owns admission")
                validate_budget(limits)
                if task_limits is not None:
                    validate_budget(task_limits)
                if type(cycles) is not int or not 1 <= cycles <= 100 or type(retries) is not int or not 0 <= retries < cycles:
                    raise ValueError("Invalid session cycle or retry limits")
                catalog = Catalog(self.ledger).read()
                frozen = freeze_project(project, catalog)
                read_frozen_requirements(catalog, project, frozen["requirements"], self.ledger.state_boundary)
                prior = session_view(events, project)
                previous_cycle = None
                if prior and prior["end"] and prior["current"]:
                    last = prior["current"][-1]
                    if last["kind"] == "role_finished" and last["state"] == "COMPLETED":
                        previous_cycle = {"development_id": prior["start"]["development_id"],
                                          "run_id": last["run_id"], "handoff_ref": last["handoff_ref"],
                                          "evidence": "SYNTHETIC"}
                now = time()
                return record("session_started", project, uuid4().hex, frozen=frozen,
                              goal_hash=digest([frozen["goal"], frozen["acceptance"], frozen["guidance_hash"]]),
                              goal=frozen["goal"], acceptance=frozen["acceptance"], previous_cycle=previous_cycle,
                              limits=limits, task_limits=task_limits, max_cycles=cycles, max_retries=retries,
                              started_epoch=now, deadline=now + limits["seconds"])
            if not view or view["start"]["development_id"] != development_id:
                raise Blocked("Explicit latest development session required")
            start = view["start"]
            if view["end"]:
                return view["end"]

            def finish(state, reason):
                return record("session_ended", start["project_id"], development_id, state=state, reason=reason,
                              cycles=view["cycles"], completed=view["completed"], synthetic_tokens=view["tokens"],
                              synthetic_cost_microunits=view["cost"], usage_kind=view["usage_kind"])

            if action == "stop":
                return finish("CANCELLED", "OWNER_STOP")
            if action != "next":
                raise Blocked("Unknown development session action")
            previous = view["current"]
            selection = "INITIAL"
            if previous:
                last = previous[-1]
                rework = (last["kind"] == "role_finished" and last.get("handoff") == "NEW_REWORK_CYCLE"
                          and last.get("role") == "Reviewer" and last.get("scope_released") is True
                          and last.get("owned_children") == []
                          and last.get("stream_settlement", {}).get("synthetic_usage_final") is True)
                if (last["state"] != "COMPLETED" and not rework) or view["usage_kind"] == "UNKNOWN":
                    return finish("BLOCKED", last.get("reason", f"PREVIOUS_{last['state']}"))
                approval = next((e for e in reversed(previous) if e["kind"] == "owner_approved_mock"), None)
                if approval is None or not approval["issued"] <= time() < approval["expires"]:
                    return finish("BLOCKED", "PREVIOUS_APPROVAL_EXPIRED")
                if not rework and "stream_settlement" in last:
                    roles = [e for e in previous if e["kind"] == "role_finished"]
                    if (len(roles) != len(PHASES) or any(
                        not e.get("stream_settlement", {}).get("synthetic_usage_final")
                        or e.get("scope_released") is not True or e.get("owned_children") != []
                        or e.get("result") not in ("OK", "PASS") for e in roles
                    )):
                        return finish("BLOCKED", "INCOMPLETE_ROLE_EVIDENCE")
                if rework:
                    selection = "REWORK"
                    if any(event.get("review_fingerprint") == last["review_fingerprint"]
                           and event["run_id"] != last["run_id"]
                           and event.get("development_id") == development_id for event in events):
                        return finish("NO_PROGRESS", "REPEATED_REVIEW_FINDING")
                else:
                    proof = next((e["handoff_ref"] for e in previous if e["kind"] == "role_finished"
                                  and e.get("role") == "Verifier" and e.get("result") == "PASS"), None)
                    try:
                        selection = validate_proposal(last.get("manager_proposal"), start["goal_hash"], proof)
                    except Blocked:
                        return finish("BLOCKED", "INVALID_MANAGER_PROPOSAL")
                if selection not in ("NEXT", "REWORK"):
                    return finish(selection, f"MANAGER_{selection}")
                if selection == "REWORK" and view["retries"] >= start["max_retries"]:
                    return finish("NO_PROGRESS", "RETRY_LIMIT")
            progress = view["progress"]["decision"]
            if progress == "NO_PROGRESS":
                return finish("NO_PROGRESS", "THREE_CYCLES_WITHOUT_IMPLEMENTATION_PROGRESS")
            if view["cycles"] >= start["max_cycles"]:
                return finish("NO_PROGRESS", "CYCLE_LIMIT")
            now = time()
            if not start["started_epoch"] <= now < start["deadline"]:
                return finish("BLOCKED", "SESSION_TIME_LIMIT")
            remaining = dict(start["limits"], tokens=start["limits"]["tokens"] - view["tokens"],
                             cost_microunits=start["limits"]["cost_microunits"] - view["cost"])
            if remaining["tokens"] < ROLE_TOKENS or remaining["cost_microunits"] < 0:
                return finish("BLOCKED", "SESSION_BUDGET_LIMIT")
            if progress == "REPLAN" and selection != "REWORK":
                selection = "REPLAN"
            return work.start_run(start["project_id"], scenario, start["frozen"], development={
                "development_id": development_id, "selection": selection, "cycle": view["cycles"] + 1,
                "session_deadline": start["deadline"], "session_remaining": remaining,
                "task_limits": start["task_limits"], "goal_hash": start["goal_hash"],
                "selection_ref": previous[-1]["handoff_ref"] if previous else start["goal_hash"],
                "progress_review": progress,
            })

    def _probe_job(self, expected_run, fixture, cancel_requested=lambda: False):
        """Internal synthetic test seam. There is no CLI or adapter dispatch to it."""
        from .job_probe import Probe, _run_probe
        if not isinstance(fixture, Probe):
            raise Blocked("Only fixed Job fixtures are supported")
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            if shutdown_state(events)["state"] in HELD:
                raise Blocked("Shutdown has closed child admission")
            session = _Session(self.ledger, len(events), tail, last_run(events))
            if (abandoned or session.pending_job or session.state != State.PLANNING
                    or session.ids["project_id"] not in PROJECTS
                    or session.run[-1]["kind"] not in ("permission", "job_drained")
                    or session.ids["run_id"] != expected_run
                    or time() >= session.run[0]["deadline"]):
                raise Blocked("Synthetic Job preconditions unmet")
            probe_id = uuid4().hex
            evidence = {"probe_id": probe_id, "fixture": fixture.value, "phase": session.state.value}
            session.emit("job_intent", session.state, **evidence)

            def created(active):
                if active != 1:
                    raise Blocked("Synthetic child assignment unverified")
                session.emit("job_created", session.state, **evidence, active_processes=active)

            receipt = _run_probe(fixture, self.ledger.local_directory("temp", create=True),
                                 created, cancel_requested)
            if receipt["active_processes"] != 0:
                raise Blocked("Previous Job is not drained")
            return session.emit("job_drained", session.state, **evidence, **receipt)


class _Session:
    def __init__(self, ledger, count, tail, run):
        self.ledger = ledger
        self.count = count
        self.tail = tail
        self.run = run
        self.owner = uuid4().hex
        self.development_id = run[0].get("development_id") if run else None
        self.state = State(run[-1]["state"]) if run else State.IDLE
        self.ids = {key: run[0][key] for key in ("project_id", "run_id", "task_id", "generation")} if run else {}

    @property
    def pending_job(self):
        return self.run[-1] if self.run and self.run[-1]["kind"] in (
            "job_intent", "job_created", "job_recovery_blocked") else None

    def recover_job(self, action, expected_run):
        previous = self.pending_job
        if action != "recover" or self.ids["run_id"] != expected_run:
            raise Blocked("Owned Job drain unverified; explicit recovery required")
        if previous["kind"] == "job_recovery_blocked":
            return previous
        return self.emit("job_recovery_blocked", State.RECOVERING,
                         probe_id=previous["probe_id"], phase=previous["phase"],
                         result="UNKNOWN", reason="Owned Job drain evidence missing")

    def emit(self, kind, state, *, admission_check=None, **details):
        event = {
            **self.ids, **details, "kind": kind, "state": state.value,
            "owner": self.owner, "source": (
                "WINDOWS_NATIVE_CODEX" if kind.startswith("native_") else
                "WINDOWS_SYNTHETIC_JOB" if kind.startswith("job_") else "MOCK"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.development_id:
            event["development_id"] = self.development_id
        admission = require_admission if kind in (
            "run_started", "role_started", "job_intent", "native_role_intent") else None
        if admission_check is not None:
            admission = admission_check
        self.count, self.tail = self.ledger.append(
            self.count, self.tail, event, allow_shutdown=True, admission=admission,
        )
        self.run.append(event)
        self.state = state
        return event

    def start_run(self, project_id, scenario, frozen, development=None):
        budget = dict(frozen["budget"])
        now = time()
        deadline = now + (0 if scenario is Scenario.TIME_LIMIT else budget["seconds"])
        if development:
            for limits in (development["session_remaining"], development["task_limits"]):
                if limits is not None:
                    budget = {key: min(amount, limits[key]) for key, amount in budget.items()}
            deadline = min(deadline, now + budget["seconds"], development["session_deadline"])
        self.run = []
        self.development_id = development["development_id"] if development else None
        self.ids = {"project_id": project_id, "task_id": uuid4().hex,
                    "run_id": uuid4().hex, "generation": self.count + 1}
        return self.emit(
            "run_started", State.PRECHECK, **{**frozen, "budget": budget}, **(development or {}),
            scenario=scenario.value, deadline=deadline,
            token_limit=0 if scenario is Scenario.TOKEN_LIMIT else budget["tokens"],
            instruction=INSTRUCTION_VERSION, policy_hash=POLICY_HASH,
            git_before="NOT_APPLICABLE", git_after="NOT_APPLICABLE", owned_children=[],
        )

    def apply(self, action, project_id, scenario, expected_run, abandoned, catalog=None):
        if action == "start":
            if abandoned or (self.state != State.IDLE and self.state not in TERMINAL):
                raise Blocked("Previous run requires completion or recovery")
            return self.start_run(project_id, scenario, freeze_project(project_id, catalog))
        if not self.run or expected_run != self.ids["run_id"]:
            raise Blocked("Explicit current run ID required")
        latest = self.run[-1]
        interrupted = latest["kind"] in OPEN_ROLE_EVENTS or (
            abandoned and self.state not in TERMINAL and self.state != State.RECOVERING
        )
        if interrupted:
            if action != "recover":
                raise Blocked("USAGE_UNVERIFIED: unfinished execution requires explicit recovery")
            if latest["kind"].startswith("native_"):
                return self.emit("recovery", State.RECOVERING,
                                 agent_run_id=latest["agent_run_id"], role=latest["role"],
                                 result="UNKNOWN", reason="NATIVE_OWNERSHIP_UNVERIFIED",
                                 owned_tree="UNKNOWN")
            return self.emit("recovery", State.RECOVERING,
                             agent_run_id=latest.get("agent_run_id"), role=latest.get("role", latest.get("stream_role")),
                             result="UNKNOWN", reason="USAGE_UNVERIFIED", owned_children=[])
        if self.state in TERMINAL:
            raise Blocked("Run already terminal")
        if action == "cancel":
            return self.emit("cancelled", State.CANCELLED, result="CANCELLED")
        if action == "pause" and self.state in (State.PRECHECK, *PHASES):
            return self.emit("paused", State.PAUSED, resume_phase=self.state.value)
        if action == "resume" and self.state == State.PAUSED:
            return self.emit("resumed", State(latest["resume_phase"]))
        if action == "approve" and self.state == State.NEED_APPROVAL:
            return self.approve()
        if action == "step" and self.state in (State.PRECHECK, *PHASES):
            return self.step(catalog)
        raise Blocked("Action unavailable in current state")

    def request(self):
        return Request(self.ids["project_id"], self.ids["task_id"], self.ids["run_id"],
                       "Manager", Capability.READ, "mock.inspect",
                       f"mock://{self.ids['project_id']}/fixture.txt")

    def approve(self):
        now = time()
        expires = min(now + 300, self.run[0]["deadline"])
        if expires <= now:
            return self.emit("budget", State.BLOCKED, reason="TIME_LIMIT")
        return self.emit("owner_approved_mock", State.PRECHECK, request=asdict(self.request()),
                         issued=now, expires=expires, approval_id=uuid4().hex)

    def schedule_budget_stop(self, reason, status=None):
        result = None

        def choose(events, _tail):
            nonlocal result
            current = shutdown_state(events)
            result = transition("request", current, None, request_id=None, unresolved=False,
                                now=time(), wait_seconds=60)
            if result is current:
                return None
            result.update(reason=reason, budget_status=status, generation=self.ids["generation"],
                          bound_run_id=self.ids["run_id"])
            return result

        self.count, self.tail = self.ledger.update(choose)
        return result

    def execute_native(self, adapter, request, phase, evidence):
        adapter.preflight(request)
        intent = {key: evidence[key] for key in (
            "agent_run_id", "context_id", "role", "input_contract_hash", "role_budget", "approval_id")}
        self.emit("native_role_intent", phase, **intent)
        dispatched = False

        def cancellation_requested():
            events, _ = self.ledger.snapshot()
            return time() >= request.deadline_utc_epoch or shutdown_state(events)["state"] in HELD

        def before_send():
            nonlocal dispatched
            def check_dispatch(events):
                if (dispatched or time() >= request.deadline_utc_epoch
                        or shutdown_state(events)["state"] in HELD or pending_budget_stop(events)):
                    raise Blocked("Native dispatch is closed")

            self.emit("native_dispatch_intent", phase, admission_check=check_dispatch, **intent)
            dispatched = True

        try:
            receipt = adapter.execute_owned(request, before_send, cancellation_requested)
            if (not dispatched or type(receipt) is not NativeReceipt
                    or receipt.request_hash != intent["input_contract_hash"]
                    or receipt.owned_tree_drained is not True
                    or type(receipt.outcome) is not Outcome
                    or receipt.outcome not in (Outcome.COMPLETED, Outcome.FAILED, Outcome.CANCELLED)):
                raise Blocked("Native result or resource ownership unverified")
        except Exception:
            self.emit("native_role_unknown", State.RECOVERING, **intent,
                      external_request="UNKNOWN" if dispatched else "NOT_DISPATCHED",
                      owned_tree="UNKNOWN")
            raise Blocked("Native execution requires explicit recovery") from None

        tokens, cost = receipt.measured_tokens, receipt.cost_microunits
        usage_known = all(type(value) is int and value >= 0 for value in (tokens, cost))
        reason = "NATIVE_VERIFIER_UNWIRED"
        if not usage_known:
            reason = "USAGE_UNVERIFIED"
        elif tokens >= request.budget.tokens:
            reason = "TOKEN_LIMIT"
        elif cost > request.budget.cost_microunits:
            reason = "COST_LIMIT"
        elif time() >= request.deadline_utc_epoch:
            reason = "TIME_LIMIT"
        # A drained transport receipt does not authorize a role transition.
        # The deterministic verifier and native usage settlement are separate gates.
        result = self.emit("native_role_result", State.BLOCKED, **intent,
                           outcome=receipt.outcome.value, owned_tree="DRAINED",
                           measured_tokens=tokens if usage_known else None,
                           cost_microunits=cost if usage_known else None, reason=reason,
                           shutdown_reason={"TOKEN_LIMIT": "TOKEN_RESERVE_LOW",
                                            "COST_LIMIT": "COST_LIMIT",
                                            "TIME_LIMIT": "TIME_LIMIT"}.get(reason))
        if result["shutdown_reason"]:
            self.schedule_budget_stop(result["shutdown_reason"])
        return result

    def step(self, catalog, *, native=None):
        phase = self.state
        start = self.run[0]
        now = time()
        if now >= start["deadline"]:
            self.schedule_budget_stop("TIME_LIMIT")
            return self.emit("budget", State.BLOCKED, reason="TIME_LIMIT")
        if phase == State.PRECHECK:
            request = self.request()
            approval = next((e for e in reversed(self.run)
                             if e["kind"] == "owner_approved_mock"), None)
            grant = None
            if approval:
                fields = approval["request"]
                recorded = Request(**{**fields, "capability": Capability(fields["capability"])})
                grant = Grant(recorded, approval["issued"], approval["expires"])
            allowed = grant is not None and grant.permits(request, now)
            return self.emit(
                "permission", State.PLANNING if allowed else State.NEED_APPROVAL,
                request=asdict(request), decision="ALLOW_MOCK" if allowed else "DENY",
                safe_args_hash=digest(asdict(request)),
                approval_id=approval["approval_id"] if allowed else None,
            )
        used, spent = usage_totals(self.run)
        approval = next((e for e in reversed(self.run) if e["kind"] == "owner_approved_mock"), None)
        if approval is None or not approval["issued"] <= now < approval["expires"]:
            return self.emit("permission_expired", State.BLOCKED, reason="APPROVAL_EXPIRED")
        role = PHASES[phase]
        preference = start["project"].get("roles", {}).get(role)
        task_budget = start.get("budget", {"seconds": start["project"]["seconds"],
                                          "tokens": start["token_limit"], "cost_microunits": 0})
        role_limits = preference["budget"] if preference else task_budget
        role_budget = {key: min(value, task_budget[key]) for key, value in role_limits.items()}
        live = freeze_project(self.ids["project_id"], catalog)
        limit = min(start["token_limit"], live["budget"]["tokens"])
        current_role = live["project"].get("roles", {}).get(role, {}).get("budget", live["budget"])
        role_budget = {key: min(amount, current_role[key], live["budget"][key])
                       for key, amount in role_budget.items()}
        reservation = role_budget["tokens"] if native is not None else ROLE_TOKENS
        status = reserve_status(limit, used, reservation, catalog.get("token_reserve", DEFAULT_RESERVE))
        role_budget["cost_microunits"] = min(role_budget["cost_microunits"],
                                            min(task_budget["cost_microunits"], live["budget"]["cost_microunits"]) - spent)
        if role_budget["cost_microunits"] < 0:
            self.schedule_budget_stop("COST_LIMIT", status)
            return self.emit("budget", State.BLOCKED, reason="COST_LIMIT")
        if reservation > min(role_budget["tokens"], limit - used):
            self.schedule_budget_stop("TOKEN_RESERVE_LOW", status)
            return self.emit("budget", State.BLOCKED, reason="TOKEN_LIMIT")
        if status["available"] < reservation:
            return self.schedule_budget_stop("TOKEN_RESERVE_LOW", status)
        role_budget["tokens"] = reservation
        live_deadline = datetime.fromisoformat(start["timestamp"]).timestamp() + live["budget"]["seconds"]
        if now >= live_deadline:
            self.schedule_budget_stop("TIME_LIMIT")
            return self.emit("budget", State.BLOCKED, reason="TIME_LIMIT")
        # Persist intent before role entry. A crash before the finish event leaves UNKNOWN.
        evidence = {
            "agent_run_id": uuid4().hex, "context_id": uuid4().hex, "role": role, "phase": phase.value,
            "model": "deterministic" if phase == State.VERIFYING else start["project"]["model"],
            "reasoning": start["project"]["reasoning"], "instruction": start["instruction"],
            "policy_hash": start["policy_hash"], "goal": start["goal"],
            "git_before": start["git_before"], "git_after": start["git_after"],
            "owned_children": [],
            "requested_model": preference["model"] if preference else None,
            "requested_reasoning": preference["reasoning"] if preference else None,
            "role_budget": role_budget, "approval_id": approval["approval_id"],
            "token_reservation": dict(status, active_reservation=reservation,
                                      available=status["available"] - reservation),
        }
        prior = next((event for event in reversed(self.run) if event["kind"] == "role_finished"), None)
        if prior and "input_contract_hash" in prior and (prior.get("scope_released") is not True
                                                          or prior.get("owned_children") != []):
            raise Blocked("Previous synthetic role resources were not released")
        refs = (prior["handoff_ref"],) if prior and "handoff_ref" in prior else ()
        refs += tuple(digest([event["question_id"], event["answer"]]) for event in self.run
                      if event["kind"] == "question_responded" and event["decision"] == "ANSWER")
        request = RoleRequest(**self.ids, agent_run_id=evidence["agent_run_id"], role=role,
                              model=evidence["model"], reasoning=evidence["reasoning"],
                              context_id=evidence["context_id"], budget=Budget(**role_budget),
                              scope=Scope(f"mock://{self.ids['project_id']}", (approval["approval_id"],)),
                              verified_input_refs=refs, goal=start["goal"],
                              deadline_utc_epoch=min(start["deadline"], live_deadline, approval["expires"],
                                                     now + role_budget["seconds"]))
        evidence.update(input_contract_hash=digest(asdict(request)), input_refs=refs)
        if native is not None:
            return self.execute_native(native, request, phase, evidence)
        started = monotonic()
        self.emit("role_started", phase, **evidence)
        binding = Binding(self.ids["project_id"], self.development_id, self.ids["run_id"],
                          self.ids["generation"], evidence["agent_run_id"], evidence["context_id"],
                          evidence["context_id"], evidence["agent_run_id"])

        def commit_stream(kind, receipt):
            return self.emit(kind, phase, agent_run_id=binding.agent_run_id, stream_role=role,
                             phase=phase.value, stream_receipt=receipt)

        journal = EventJournal(binding, commit_stream, fixture_usage=True)
        journal.receive(Packet(binding, 1, uuid4().hex, {"method": "turn/started", "params": {
            "turn": {"id": binding.turn_id, "status": "inProgress"}}}))
        adapter = MockAdapter(lambda: mock_role(phase, Scenario(start["scenario"]), self.run))
        try:
            result, tool_audit = normalize_mock(adapter.stream(request))
        except (Exception, KeyboardInterrupt):
            journal.disconnect()
            raise
        status = "failed" if result["result"] == "FAIL" else "completed"
        context = {"threadId": binding.thread_id, "turnId": binding.turn_id}
        item = {"id": "fixture-command", "type": "commandExecution"}
        messages = (
            {"method": "item/started", "params": {**context, "item": {**item, "status": "inProgress"}}},
            {"method": "item/commandExecution/requestApproval", "id": 1,
             "params": {**context, "itemId": item["id"]}},
            {"method": "serverRequest/resolved", "params": {"threadId": binding.thread_id, "requestId": 1}},
            {"method": "item/completed", "params": {**context, "item": {**item, "status": "declined"}}},
            {"method": FIXTURE_USAGE, "params": {**context, "tokens": ROLE_TOKENS,
                                                 "cost_microunits": 0, "final": True}},
            {"method": "turn/completed", "params": {"turn": {"id": binding.turn_id, "status": status}}},
        )
        for sequence, message in enumerate(messages, 2):
            journal.receive(Packet(binding, sequence, uuid4().hex, message))
        settlement = journal.fixture_settlement(expected_status=status, resources_released=True)
        elapsed = monotonic() - started
        if (settlement["synthetic_tokens"] > role_budget["tokens"]
                or settlement["synthetic_cost_microunits"] > role_budget["cost_microunits"]):
            next_state = State.BLOCKED
            result["reason"] = "USAGE_LIMIT"
        elif result["result"] == "FAIL":
            next_state = State.FAILED
        elif (time() >= min(start["deadline"], live_deadline)
              or elapsed >= min(start["deadline"] - now, live_deadline - now, role_budget["seconds"])):
            next_state = State.BLOCKED
            result["reason"] = "TIME_LIMIT"
        else:
            next_state = PIPELINE[PIPELINE.index(phase) + 1]
        if time() >= approval["expires"] and next_state != State.FAILED:
            next_state = State.BLOCKED
            result = {**result, "reason": "APPROVAL_EXPIRED"}
        if self.development_id and phase is State.PLANNING and next_state is State.DEVELOPING:
            from .role_contract import confirm_task_plan
            # The synthetic plan uses only fixture authority, including for registered projects.
            plan = {"disposition": "PROPOSED", "priority_reason": "Synthetic workflow",
                    "work_kind": "PRODUCT", "product_outcome": "Fixture reaches ready",
                    "validation_reason": "Compare the changed fixture with the expected value",
                    "approach": "Apply the fixed fixture transition",
                    "task_spec": result["decision"], "proposed_paths": ["fixture.txt"],
                    "acceptance_checks": ["fixture-transition"], "stop_conditions": ["Fixture failure"]}
            task = confirm_task_plan(canonical(plan).encode(), allowed_files=("fixture.txt",),
                                     acceptance=("fixture-transition",), budget=task_budget,
                                     authority_ref=approval["approval_id"])
            result["selected_task"] = {"goal_hash": start["goal_hash"], "cycle": start["cycle"],
                                       "acceptance": start["acceptance"], "basis": start["selection_ref"],
                                       "fixture_contract": task}
        if self.development_id and phase is State.DEVELOPING and next_state is State.REVIEWING:
            task = prior.get("selected_task", {}).get("fixture_contract") if prior else None
            # Older mock runs only ever had authority over this same fixed fixture.
            paths = task["paths"] if task else ("fixture.txt",)
            if result.get("diff", {}).get("path") not in paths:
                next_state = State.BLOCKED
                result["reason"] = "FIXTURE_CHANGE_OUTSIDE_TASK_CONTRACT"
        transition_handoff = next_state.value
        if self.development_id and phase is State.REVIEWING and next_state is State.VERIFYING and "review_output" in result:
            from .role_contract import TransitionEvidence, decide_transition
            # The scheduler acts on this preview only after the finish event commits.
            transition_handoff = decide_transition(
                "reviewer", canonical(result["review_output"]).encode(),
                TransitionEvidence(True, True, True, True, True, 1, False),
            )
            next_state = State.FAILED
            result["review_fingerprint"] = digest(result.pop("review_output"))
        if self.development_id and next_state is State.COMPLETED:
            proof = prior["handoff_ref"] if (prior and prior.get("role") == "Verifier"
                                              and prior.get("result") == "PASS") else None
            proposal = mock_next(start["goal_hash"], proof)
            try:
                validate_proposal(proposal, start["goal_hash"], proof)
            except Blocked:
                next_state = State.BLOCKED
                result["reason"] = "INVALID_MANAGER_PROPOSAL"
            else:
                result["manager_proposal"] = proposal
        handoff_ref = digest([evidence["input_contract_hash"], result, next_state.value])
        next_tokens = reserve_status(limit, used + settlement["synthetic_tokens"], ROLE_TOKENS,
                                     catalog.get("token_reserve", DEFAULT_RESERVE))
        stop_reason = None
        if result.get("reason") == "TIME_LIMIT":
            stop_reason = "TIME_LIMIT"
        elif result.get("reason") == "USAGE_LIMIT":
            stop_reason = "TOKEN_RESERVE_LOW" if settlement["synthetic_tokens"] > role_budget["tokens"] else "COST_LIMIT"
        elif next_state in PHASES and next_tokens["available"] < ROLE_TOKENS:
            stop_reason = "TOKEN_RESERVE_LOW"
        finished = self.emit(
            "role_finished", next_state, **evidence, **result, elapsed_seconds=elapsed,
            commands=[], exit_code="NOT_APPLICABLE", resource_peaks="UNKNOWN",
            measured_tokens="NOT_APPLICABLE", synthetic_tokens=settlement["synthetic_tokens"],
            cost="NOT_APPLICABLE", retries=0, handoff=transition_handoff if phase is State.REVIEWING else next_state.value,
            usage_kind="SYNTHETIC", estimated_tokens="UNKNOWN",
            synthetic_cost_microunits=settlement["synthetic_cost_microunits"],
            tool_audit=tool_audit, diff_ref=digest(result["diff"]) if "diff" in result else None,
            handoff_ref=handoff_ref, scope_released=True,
            stream_settlement=settlement,
            **({"shutdown_reason": stop_reason, "shutdown_budget": next_tokens
                if stop_reason == "TOKEN_RESERVE_LOW" else None} if stop_reason else {}),
        )
        if stop_reason:
            self.schedule_budget_stop(stop_reason, next_tokens if stop_reason == "TOKEN_RESERVE_LOW" else None)
        return finished
