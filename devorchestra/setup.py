"""Portable setup validation. No project paths are opened and no CLI is run."""
import os
import platform
import re
from pathlib import PureWindowsPath
from shutil import which

from .catalog import validate_project


def environment_status():
    return {
        "windows_x64": os.name == "nt" and platform.machine().upper() in ("AMD64", "X86_64"),
        "codex_found": bool(which("codex.exe")) if os.name == "nt" else False,
        "cli_signature_version": "NOT_VERIFIED",
        "mxc": "NOT_VERIFIED",
        "live_execution": "BLOCKED",
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
    paths = [_path(project["local_path"]), _path(data["replica"])]
    if state_path is not None:
        paths.append(_path(str(state_path)))
    if any(a == b or a in b.parents or b in a.parents
           for index, a in enumerate(paths) for b in paths[index + 1:]):
        raise ValueError("Original, replica and local state must be separate")
    if type(data["wait_seconds"]) is not int or not 1 <= data["wait_seconds"] <= 60:
        raise ValueError("Shutdown wait must be 1–60 seconds")
    if any(project["requested_scope"][key] for key in ("network", "tools", "files")):
        raise ValueError("Setup requires deny-by-default scope")
    if any(value != "disabled" for value in project["git_policy"].values()):
        raise ValueError("Setup requires Git commit and push disabled")
