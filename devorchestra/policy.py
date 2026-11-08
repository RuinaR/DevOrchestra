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
