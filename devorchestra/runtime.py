"""Trusted state transitions. Roles are synchronous, fixed Mock functions."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from time import monotonic, time
from uuid import uuid4

from .lease import Blocked, execution_slot
from .ledger import digest
from .policy import Capability, Grant, Request


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
            return events[index:]
    return []


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


class Runner:
    def __init__(self, ledger):
        self.ledger = ledger

    def command(self, action, project="fixture-a", scenario=Scenario.NORMAL, expected_run=None):
        with execution_slot() as abandoned:
            if action == "init":
                if abandoned:
                    raise Blocked("Abandoned slot requires inspection")
                self.ledger.initialize()
                return None
            events, tail = self.ledger.snapshot(recover_journal=True)
            session = _Session(self.ledger, len(events), tail, last_run(events))
            if session.pending_job:
                return session.recover_job(action, expected_run)
            if action == "run":
                event = session.apply("step", project, scenario, expected_run, abandoned)
                while session.state in PHASES:
                    event = session.apply("step", project, scenario, expected_run, False)
                return event
            return session.apply(action, project, scenario, expected_run, abandoned)

    def _probe_job(self, expected_run, fixture, cancel_requested=lambda: False):
        """Internal synthetic test seam. There is no CLI or adapter dispatch to it."""
        from .job_probe import Probe, _run_probe
        if not isinstance(fixture, Probe):
            raise Blocked("Only fixed Job fixtures are supported")
        with execution_slot() as abandoned:
            events, tail = self.ledger.snapshot(recover_journal=True)
            session = _Session(self.ledger, len(events), tail, last_run(events))
            if (abandoned or session.pending_job or session.state != State.PLANNING
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
        self.ledger.append(self.count, self.tail, event)
        self.count += 1
        self.tail = digest([self.count, self.tail, event])
        self.run.append(event)
        self.state = state
        return event

    def apply(self, action, project_id, scenario, expected_run, abandoned):
        if action == "start":
            if abandoned or (self.state != State.IDLE and self.state not in TERMINAL):
                raise Blocked("Previous run requires completion or recovery")
            project = PROJECTS[project_id]
            self.run = []
            self.ids = {"project_id": project_id, "task_id": uuid4().hex,
                        "run_id": uuid4().hex, "generation": self.count + 1}
            return self.emit(
                "run_started", State.PRECHECK, project=asdict(project),
                scenario=scenario.value, deadline=time() + (
                    0 if scenario is Scenario.TIME_LIMIT else project.seconds),
                token_limit=0 if scenario is Scenario.TOKEN_LIMIT else project.tokens,
                goal=project.priority, instruction=INSTRUCTION_VERSION, policy_hash=POLICY_HASH,
                git_before="NOT_APPLICABLE", git_after="NOT_APPLICABLE", owned_children=[],
            )
        if not self.run or expected_run != self.ids["run_id"]:
            raise Blocked("Explicit current run ID required")
        latest = self.run[-1]
        interrupted = latest["kind"] == "role_started" or (
            abandoned and self.state not in TERMINAL and self.state != State.RECOVERING
        )
        if interrupted:
            if action != "recover":
                raise Blocked("Unfinished execution requires explicit recovery")
            return self.emit("recovery", State.RECOVERING,
                             agent_run_id=latest.get("agent_run_id"), role=latest.get("role"),
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
        if used + ROLE_TOKENS > start["token_limit"]:
            return self.emit("budget", State.BLOCKED, reason="TOKEN_LIMIT")
        # Persist intent before role entry. A crash before the finish event leaves UNKNOWN.
        evidence = {
            "agent_run_id": uuid4().hex, "role": PHASES[phase], "phase": phase.value,
            "model": "deterministic" if phase == State.VERIFYING else start["project"]["model"],
            "reasoning": start["project"]["reasoning"], "instruction": start["instruction"],
            "policy_hash": start["policy_hash"], "goal": start["goal"],
            "git_before": start["git_before"], "git_after": start["git_after"],
            "owned_children": [],
        }
        started = monotonic()
        self.emit("role_started", phase, **evidence)
        result = mock_role(phase, Scenario(start["scenario"]), self.run)
        elapsed = monotonic() - started
        if result["result"] == "FAIL":
            next_state = State.FAILED
        elif time() >= start["deadline"] or elapsed >= start["deadline"] - now:
            next_state = State.BLOCKED
            result["reason"] = "TIME_LIMIT"
        else:
            next_state = PIPELINE[PIPELINE.index(phase) + 1]
        return self.emit(
            "role_finished", next_state, **evidence, **result, elapsed_seconds=elapsed,
            commands=[], exit_code="NOT_APPLICABLE", resource_peaks="UNKNOWN",
            measured_tokens="NOT_APPLICABLE", synthetic_tokens=ROLE_TOKENS,
            cost="NOT_APPLICABLE", retries=0, handoff=next_state.value,
        )
