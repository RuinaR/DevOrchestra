"""Role contract and bounded synthetic event normalization."""
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from .lease import Blocked
from .policy import ExecutionBackend, live_preflight


class ModelBackend(str, Enum):
    SDK = "codex-sdk"
    APP_SERVER = "codex-app-server"
    EXEC = "codex-exec"
    CHATGPT_TEXT = "chatgpt-subscription-text"


def manager_backend_status(backend: ModelBackend) -> dict:
    """Classify the selected model transport before touching a project or launching a process."""
    backend = ModelBackend(backend)
    if backend is ModelBackend.APP_SERVER:
        from .native_manager import manager_admission

        decision = manager_admission()
    else:
        reason, message = {
            ModelBackend.SDK: ("SDK_TRANSPORT_UNADMITTED", "SDK의 하위 Codex 실행 경계가 입증되지 않았습니다."),
            ModelBackend.EXEC: ("EXEC_TRANSPORT_UNADMITTED", "CLI 실행의 송신 및 내장 도구 경계가 입증되지 않았습니다."),
            ModelBackend.CHATGPT_TEXT: ("TEXT_TRANSPORT_REQUIRES_ACCOUNT_AND_REVIEW",
                                        "DevOrchestra 전용 OAuth 연결 및 별도 Manager 요청 승인이 필요합니다."),
        }[backend]
        decision = {"state": "BLOCKED", "reason": reason, "message": message}
    return {"backend": backend.value, **decision}


def require_manager_backend(backend: ModelBackend) -> None:
    if backend != ModelBackend.APP_SERVER:
        raise Blocked(manager_backend_status(backend)["reason"])


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


@dataclass(frozen=True)
class NativeReceipt:
    request_hash: str
    outcome: Outcome
    owned_tree_drained: bool
    measured_tokens: int | None
    cost_microunits: int | None


class OwnedNativeTransport(Protocol):
    def __call__(self, request: RoleRequest, before_send: Callable[[], None],
                 cancellation_requested: Callable[[], bool]) -> NativeReceipt:
        """Collect the request-bound receipt after verifying owned resource drain."""
        ...


class WindowsNativeCodexAdapter:
    """Live role boundary. Admission stays closed without trusted OS evidence."""

    def __init__(self, transport: OwnedNativeTransport | None = None):
        self._transport = transport

    def preflight(self, request: RoleRequest) -> None:
        decision = live_preflight(backend=ExecutionBackend.WINDOWS_NATIVE_CODEX)
        raise Blocked(decision["reason"])

    def execute(self, request: RoleRequest) -> RoleResult:
        self.preflight(request)
        raise Blocked("Native transport is not configured")

    def execute_owned(self, request: RoleRequest, before_send, cancellation_requested):
        """An owned transport must call before_send before dispatching once.

        The return value is a NativeReceipt assembled by the trusted collector
        after waiting for process drain, never from model-supplied fields.
        """
        self.preflight(request)
        if self._transport is None:
            raise Blocked("Native owned transport is not configured")
        return self._transport(request, before_send, cancellation_requested)
