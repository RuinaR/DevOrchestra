"""Role contract and bounded synthetic event normalization."""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .lease import Blocked
from .policy import ExecutionBackend, live_preflight


class Outcome(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Scope:
    workspace_id: str
    approval_receipts: tuple[str, ...]
    readable: tuple[str, ...] = ()
    writable: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    network: tuple[str, ...] = ()


@dataclass(frozen=True)
class Budget:
    seconds: int
    tokens: int
    cost_microunits: int


@dataclass(frozen=True)
class RoleRequest:
    project_id: str
    task_id: str
    run_id: str
    generation: int
    agent_run_id: str
    role: str
    model: str
    reasoning: str
    context_id: str
    scope: Scope
    budget: Budget
    verified_input_refs: tuple[str, ...]
    goal: str = ""
    deadline_utc_epoch: float | None = None


@dataclass(frozen=True)
class RoleResult:
    outcome: Outcome
    receipt_refs: tuple[str, ...]
    handoff_candidates: tuple[str, ...]
    measured_tokens: int | None = None
    cost_microunits: int | None = None


@dataclass(frozen=True)
class MockEvent:
    sequence: int
    kind: str
    data: dict


def normalize_mock(events):
    """Missing, duplicated or unordered terminal records never imply completion."""
    if [(event.sequence, event.kind) for event in events] != [(1, "tool_denied"), (2, "terminal")]:
        raise Blocked("Incomplete or unordered synthetic adapter stream")
    decision, result = (event.data for event in events)
    if decision != {"tool": "synthetic.shell", "outcome": "DENIED", "executed": False}:
        raise Blocked("Unexpected synthetic tool outcome")
    if result.get("result") not in ("OK", "PASS", "FAIL"):
        raise Blocked("Synthetic terminal result unknown")
    return result, [dict(sequence=event.sequence, kind=event.kind, **event.data) for event in events[:1]]


class MockAdapter:
    def __init__(self, fixture):
        self.fixture = fixture

    def stream(self, request):
        if (request.model not in ("mock-v1", "deterministic") or request.scope.tools
                or request.scope.writable or request.scope.readable or request.scope.network):
            raise Blocked("Mock adapter cannot accept real capabilities")
        return (
            MockEvent(1, "tool_denied", {"tool": "synthetic.shell", "outcome": "DENIED", "executed": False}),
            MockEvent(2, "terminal", self.fixture()),
        )


class RoleAdapter(Protocol):
    def execute(self, request: RoleRequest) -> RoleResult:
        """Report scoped tool receipt IDs and candidate handoff evidence.

        A future trusted runner must independently check permissions, process
        drain, receipt provenance, and the handoff before admitting another role.
        """
        ...


class WindowsNativeCodexAdapter:
    """Reserved live role boundary. No transport or process can start yet."""

    def execute(self, request: RoleRequest) -> RoleResult:
        decision = live_preflight(backend=ExecutionBackend.WINDOWS_NATIVE_CODEX)
        raise Blocked(decision["reason"])
