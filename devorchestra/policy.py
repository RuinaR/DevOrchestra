"""Exact-match simulated permissions. This module opens no project resources."""
from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    READ = "project-read"
    WRITE = "file-write"
    BUILD = "build"
    TEST = "test"
    NETWORK = "network"
    COMMIT = "git-commit"
    PUSH = "git-push"
    GPU = "gpu"
    SERVICE = "external-service"


class ExecutionBackend(str, Enum):
    WINDOWS_NATIVE_CODEX = "windows-native-codex"
    VIRTUALBOX = "virtualbox"
    EXPLICIT_MXC = "explicit-mxc"


DEFAULT_BACKEND = ExecutionBackend.WINDOWS_NATIVE_CODEX


class DevelopmentMode(str, Enum):
    GENERAL = "general"
    ENHANCED = "enhanced"


FORBIDDEN_LIVE_CAPABILITIES = (
    "external-process-access", "global-input", "screen-capture", "external-window-control",
    "outside-workspace-files", "privilege-escalation", "system-settings-change",
)


def live_preflight(required_capabilities=(), *, backend=DEFAULT_BACKEND, mode=DevelopmentMode.GENERAL):
    """Assess declared requirements without opening files or authorizing execution.

    A declaration is not evidence about what generated code actually does.
    This release has no verified live admission path, even for an empty plan.
    """
    try:
        selected = ExecutionBackend(backend)
    except (TypeError, ValueError):
        selected = None
    try:
        selected_mode = DevelopmentMode(mode)
    except (TypeError, ValueError):
        selected_mode = None
    required = tuple(required_capabilities)
    forbidden = tuple(name for name in FORBIDDEN_LIVE_CAPABILITIES if name in required)
    known = (*FORBIDDEN_LIVE_CAPABILITIES, *(item.value for item in Capability))
    unknown = any(name not in known for name in required)
    if forbidden:
        reason = "FORBIDDEN_CAPABILITY"
    elif unknown:
        reason = "UNKNOWN_CAPABILITY"
    elif selected is None:
        reason = "UNKNOWN_BACKEND"
    elif selected_mode is None:
        reason = "UNKNOWN_DEVELOPMENT_MODE"
    elif selected_mode is DevelopmentMode.GENERAL and selected is ExecutionBackend.WINDOWS_NATIVE_CODEX:
        reason = "GENERAL_ADMISSION_UNVERIFIED"
    else:
        reason = "ISOLATION_UNVERIFIED"
    return {
        "status": "BLOCKED",
        "reason": reason,
        "forbidden_capabilities": forbidden,
        "backend": selected.value if selected is not None else None,
        "development_mode": selected_mode.value if selected_mode is not None else None,
        "isolation_candidate": {
            ExecutionBackend.WINDOWS_NATIVE_CODEX: "Windows Native Codex",
            ExecutionBackend.EXPLICIT_MXC: "MXC",
            ExecutionBackend.VIRTUALBOX: "VirtualBox",
        }.get(selected),
        "automatic_fallback": False,
        "noninterference": "NOT_VERIFIED",
    }


@dataclass(frozen=True)
class Request:
    project: str
    task: str
    run: str
    role: str
    capability: Capability
    tool: str
    target: str


@dataclass(frozen=True)
class Grant:
    request: Request
    issued: float
    expires: float

    def permits(self, request: Request, now: float) -> bool:
        # Only a built-in simulated operation is eligible, even with a grant.
        return (
            request.capability is Capability.READ
            and request.role == "Manager"
            and request.tool == "mock.inspect"
            and request.target == f"mock://{request.project}/fixture.txt"
            and self.request == request
            and self.issued <= now < self.expires
        )
