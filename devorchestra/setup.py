"""Portable setup validation. No project paths are opened and no CLI is run."""
import os
import platform
import re
from pathlib import Path, PureWindowsPath
from shutil import which

from .catalog import validate_project
from .policy import live_preflight
from .lease import Blocked, execution_slot
from .ledger import check_path


def environment_status():
    decision = live_preflight()
    return {
        "windows_x64": os.name == "nt" and platform.machine().upper() in ("AMD64", "X86_64"),
        "codex_found": bool(which("codex.exe")) if os.name == "nt" else False,
        "cli_signature_version": "NOT_VERIFIED",
        "mxc": "NOT_VERIFIED",
        "backend": decision["backend"],
        "backend_label": decision["isolation_candidate"],
        "live_execution": decision["status"],
    }


def _path(value):
    if (not isinstance(value, str) or len(value) > 512
            or not re.match(r"^[A-Za-z]:\\", value) or "/" in value):
        raise ValueError("Use a local absolute Windows path")
    parts = value[3:].split("\\")
    if any(not part or part in (".", "..") or part.endswith((" ", ".")) or
           any(ord(char) < 32 or char in ':<>|?*"' for char in part) for part in parts):
        raise ValueError("Invalid or ambiguous setup path")
    return PureWindowsPath(value)


def validate_setup(data, state_path=None):
    if not isinstance(data, dict) or set(data) != {"project", "replica", "wait_seconds"}:
        raise ValueError("Unexpected setup fields; credentials cannot be supplied")
    project = data["project"]
    validate_project(project)
    # Reject familiar credential material without logging the submitted value.
    if re.search(r"(?i)(?:\bsk-[a-z0-9_-]{12,}|\bgh[pousr]_[a-z0-9]{12,}|"
                 r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bbearer\s+[a-z0-9._-]{12,})",
                 str(project)):
        raise ValueError("Credential material cannot be saved by setup")
    original, replica = _path(project["local_path"]), _path(data["replica"])
    if original == replica or original in replica.parents or replica in original.parents:
        raise ValueError("Original and replica must be separate")
    if state_path is not None:
        state = _path(str(state_path))
        # The original checkout may contain its own .ai-local directory.
        if (state in (original, replica) or state in original.parents or state in replica.parents
                or replica in state.parents
                or (original in state.parents and state.name.casefold() != ".ai-local")):
            raise ValueError("Replica and private local state must be separate")
    if type(data["wait_seconds"]) is not int or not 1 <= data["wait_seconds"] <= 60:
        raise ValueError("Shutdown wait must be 1–60 seconds")
    if any(project["requested_scope"][key] for key in ("network", "tools", "files")):
        raise ValueError("Setup requires deny-by-default scope")
    if any(value != "disabled" for value in project["git_policy"].values()):
        raise ValueError("Setup requires Git commit and push disabled")


def create_work_copy(source, destination):
    """Copy into a new owner-selected folder without copying Git or app-private state."""
    from .replica_evidence import fingerprint

    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("이전 작업 상태를 먼저 확인하세요.")
        check_path(source)
        check_path(destination)
        profile = os.environ.get("USERPROFILE")
        if (destination.exists() or not destination.parent.is_dir()
                or source == source.parent or (profile and Path(profile).is_relative_to(source))
                or destination.is_relative_to(source) or source.is_relative_to(destination)):
            raise Blocked("원본과 겹치지 않는 새 작업 폴더를 지정하세요.")
        stack, files, total, entries = [source], [], 0, 0
        while stack:
            directory = stack.pop()
            check_path(directory)
            for path in directory.iterdir():
                entries += 1
                if entries > 4096:
                    raise Blocked("자동 복사 범위를 초과했습니다. 작업 사본을 직접 준비하세요.")
                name = path.name.casefold()
                if (name in (".git", ".ai-local", ".codex", ".agents", "auth.json", "credentials.json")
                        or name.startswith(".env") or path.suffix.lower() in (".pem", ".key", ".sqlite", ".sqlite3", ".db")):
                    continue
                check_path(path)
                if path.is_dir():
                    stack.append(path)
                    continue
                info = fingerprint(path, min(64 * 1024 * 1024, 256 * 1024 * 1024 - total))
                total += info["bytes"]
                files.append((path, info))
        destination.mkdir()
        for path, info in files:
            target = destination / path.relative_to(source)
            check_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            check_path(path)
            before = path.lstat()
            with path.open("rb") as stream:
                if not os.path.samestat(before, os.fstat(stream.fileno())):
                    raise Blocked("복사 중 원본 파일이 바뀌었습니다.")
                with target.open("xb") as copy:
                    remaining = info["bytes"]
                    while remaining:
                        chunk = stream.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise Blocked("복사 중 원본 파일 크기가 바뀌었습니다.")
                        copy.write(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise Blocked("복사 중 원본 파일 크기가 바뀌었습니다.")
            if fingerprint(target) != info:
                raise Blocked("복사본 확인에 실패했습니다. 생성된 작업 폴더를 확인하세요.")
