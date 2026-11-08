"""Trusted state transitions. Roles are synchronous, fixed Mock functions."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from time import monotonic, time
from uuid import uuid4

from .adapter import Budget, MockAdapter, RoleRequest, Scope, normalize_mock
from .catalog import Catalog
from .development import session_view, validate_proposal
from .event_audit import Binding, FIXTURE_USAGE, Packet
from .event_journal import EventJournal, OPEN_ROLE_EVENTS

from .lease import Blocked, execution_slot
from .ledger import digest
from .mxc_diagnostic import diagnostic_unresolved
from .policy import Capability, Grant, Request
from .shutdown import HELD, UNKNOWN, checkpoint, shutdown_state, transition


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
                    if not e["kind"].startswith(("session_", "shutdown_", "mxc_diagnostic_"))]
    return []


def require_admission(events):
    if shutdown_state(events)["state"] in HELD:
        raise Blocked("Shutdown has closed admission")
    if diagnostic_unresolved(events):
        raise Blocked("Diagnostic process ownership requires recovery")


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
    return {"project": project, "goal": goal, "acceptance": acceptance, "budget": budget,
            "project_budget": project_budget, "global_budget": global_budget,
            "guidance_hash": digest([INSTRUCTION_VERSION, POLICY_HASH, guidance])}


class Runner:
    def __init__(self, ledger):
        self.ledger = ledger

    def command(self, action, project="fixture-a", scenario=Scenario.NORMAL, expected_run=None):
        if action == "run":
            event = self.command("step", project, scenario, expected_run)
            while event["state"] in PHASES:
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
            catalog = Catalog(self.ledger).read() if action == "start" else None
            return session.apply(action, project, scenario, expected_run, abandoned, catalog)

    def shutdown(self, action, request_id=None, *, wait_seconds=10, expected_generation=None):
        if action == "request":
            return self._reserve_shutdown(request_id, wait_seconds, expected_generation)
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            current = shutdown_state(events)
            run = last_run(events)
            unresolved = (abandoned or diagnostic_unresolved(events) or any(event["kind"] in UNKNOWN for event in run[-1:])
                          or any(event["kind"] == "recovery" for event in run))
            if current.get("abandoned"):
                unresolved = True
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
                frozen = freeze_project(project, Catalog(self.ledger).read())
                now = time()
                return record("session_started", project, uuid4().hex, frozen=frozen,
                              goal_hash=digest([frozen["goal"], frozen["acceptance"], frozen["guidance_hash"]]),
                              goal=frozen["goal"], acceptance=frozen["acceptance"],
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
                if last["state"] != "COMPLETED" or view["usage_kind"] == "UNKNOWN":
                    return finish("BLOCKED", last.get("reason", f"PREVIOUS_{last['state']}"))
                approval = next((e for e in reversed(previous) if e["kind"] == "owner_approved_mock"), None)
                if approval is None or not approval["issued"] <= time() < approval["expires"]:
                    return finish("BLOCKED", "PREVIOUS_APPROVAL_EXPIRED")
                if "stream_settlement" in last:
                    roles = [e for e in previous if e["kind"] == "role_finished"]
                    if (len(roles) != len(PHASES) or any(
                        not e.get("stream_settlement", {}).get("synthetic_usage_final")
                        or e.get("scope_released") is not True or e.get("owned_children") != []
                        or e.get("result") not in ("OK", "PASS") for e in roles
                    )):
                        return finish("BLOCKED", "INCOMPLETE_ROLE_EVIDENCE")
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
            if view["cycles"] >= start["max_cycles"]:
                return finish("NO_PROGRESS", "CYCLE_LIMIT")
            now = time()
            if not start["started_epoch"] <= now < start["deadline"]:
                return finish("BLOCKED", "SESSION_TIME_LIMIT")
            remaining = dict(start["limits"], tokens=start["limits"]["tokens"] - view["tokens"],
                             cost_microunits=start["limits"]["cost_microunits"] - view["cost"])
            if remaining["tokens"] < ROLE_TOKENS or remaining["cost_microunits"] < 0:
                return finish("BLOCKED", "SESSION_BUDGET_LIMIT")
            return work.start_run(start["project_id"], scenario, start["frozen"], development={
                "development_id": development_id, "selection": selection, "cycle": view["cycles"] + 1,
                "session_deadline": start["deadline"], "session_remaining": remaining,
                "task_limits": start["task_limits"], "goal_hash": start["goal_hash"],
                "selection_ref": previous[-1]["handoff_ref"] if previous else start["goal_hash"],
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

            receipt = _run_probe(fixture, self.ledger.path.parent, created, cancel_requested)
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

    def emit(self, kind, state, **details):
        event = {
            **self.ids, **details, "kind": kind, "state": state.value,
            "owner": self.owner, "source": "WINDOWS_SYNTHETIC_JOB" if kind.startswith("job_") else "MOCK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.development_id:
            event["development_id"] = self.development_id
        admission = require_admission if kind in ("run_started", "role_started", "job_intent") else None
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
                raise Blocked("Unfinished execution requires explicit recovery")
            return self.emit("recovery", State.RECOVERING,
                             agent_run_id=latest.get("agent_run_id"), role=latest.get("role", latest.get("stream_role")),
                             result="UNKNOWN", reason="Interrupted owner", owned_children=[])
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
            return self.step()
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

    def step(self):
        phase = self.state
        start = self.run[0]
        now = time()
        if now >= start["deadline"]:
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
        used = sum(e.get("synthetic_tokens", 0) for e in self.run)
        approval = next((e for e in reversed(self.run) if e["kind"] == "owner_approved_mock"), None)
        if approval is None or not approval["issued"] <= now < approval["expires"]:
            return self.emit("permission_expired", State.BLOCKED, reason="APPROVAL_EXPIRED")
        role = PHASES[phase]
        preference = start["project"].get("roles", {}).get(role)
        task_budget = start.get("budget", {"seconds": start["project"]["seconds"],
                                          "tokens": start["token_limit"], "cost_microunits": 0})
        role_limits = preference["budget"] if preference else task_budget
        role_budget = {key: min(value, task_budget[key]) for key, value in role_limits.items()}
        role_budget["tokens"] = min(role_budget["tokens"], start["token_limit"] - used)
        spent = sum(e.get("synthetic_cost_microunits", 0) for e in self.run)
        role_budget["cost_microunits"] = min(role_budget["cost_microunits"], task_budget["cost_microunits"] - spent)
        if ROLE_TOKENS > role_budget["tokens"]:
            return self.emit("budget", State.BLOCKED, reason="TOKEN_LIMIT")
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
        }
        prior = next((event for event in reversed(self.run) if event["kind"] == "role_finished"), None)
        if prior and "input_contract_hash" in prior and (prior.get("scope_released") is not True
                                                          or prior.get("owned_children") != []):
            raise Blocked("Previous synthetic role resources were not released")
        refs = (prior["handoff_ref"],) if prior and "handoff_ref" in prior else ()
        request = RoleRequest(**self.ids, agent_run_id=evidence["agent_run_id"], role=role,
                              model=evidence["model"], reasoning=evidence["reasoning"],
                              context_id=evidence["context_id"], budget=Budget(**role_budget),
                              scope=Scope(f"mock://{self.ids['project_id']}", (approval["approval_id"],)),
                              verified_input_refs=refs, goal=start["goal"],
                              deadline_utc_epoch=min(start["deadline"], approval["expires"],
                                                     now + role_budget["seconds"]))
        evidence.update(input_contract_hash=digest(asdict(request)), input_refs=refs)
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
        elif (time() >= start["deadline"] or elapsed >= min(start["deadline"] - now, role_budget["seconds"])):
            next_state = State.BLOCKED
            result["reason"] = "TIME_LIMIT"
        else:
            next_state = PIPELINE[PIPELINE.index(phase) + 1]
        if time() >= approval["expires"] and next_state != State.FAILED:
            next_state = State.BLOCKED
            result = {**result, "reason": "APPROVAL_EXPIRED"}
        if self.development_id and phase is State.PLANNING and next_state is State.DEVELOPING:
            result["selected_task"] = {"goal_hash": start["goal_hash"], "cycle": start["cycle"],
                                       "acceptance": start["acceptance"], "basis": start["selection_ref"]}
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
        return self.emit(
            "role_finished", next_state, **evidence, **result, elapsed_seconds=elapsed,
            commands=[], exit_code="NOT_APPLICABLE", resource_peaks="UNKNOWN",
            measured_tokens="NOT_APPLICABLE", synthetic_tokens=settlement["synthetic_tokens"],
            cost="NOT_APPLICABLE", retries=0, handoff=next_state.value,
            usage_kind="SYNTHETIC", estimated_tokens="UNKNOWN",
            synthetic_cost_microunits=settlement["synthetic_cost_microunits"],
            tool_audit=tool_audit, diff_ref=digest(result["diff"]) if "diff" in result else None,
            handoff_ref=handoff_ref, scope_released=True,
            stream_settlement=settlement,
        )
