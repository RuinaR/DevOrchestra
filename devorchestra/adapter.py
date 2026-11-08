"""Future role adapter contract. No registration, implementation, or dispatch."""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


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


@dataclass(frozen=True)
class RoleResult:
    outcome: Outcome
    receipt_refs: tuple[str, ...]
    handoff_candidates: tuple[str, ...]
    measured_tokens: int | None = None
    cost_microunits: int | None = None


class RoleAdapter(Protocol):
    def execute(self, request: RoleRequest) -> RoleResult:
        """Report scoped tool receipt IDs and candidate handoff evidence.

        A future trusted runner must independently check permissions, process
        drain, receipt provenance, and the handoff before admitting another role.
        """
        ...