"""Trusted, revisioned project metadata. Registration grants no capabilities."""
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path, PureWindowsPath
import re

from .lease import Blocked, execution_slot
from .ledger import Ledger, check_path, digest
from .token_reserve import validate_reserve

ROLES = ("Manager", "Developer", "Reviewer", "Verifier")
FIXTURE_IDS = frozenset(("fixture-a", "fixture-b"))
BUDGET_FIELDS = ("seconds", "tokens", "cost_microunits")
DEFAULT_BUDGET = {"seconds": 600, "tokens": 10, "cost_microunits": 0}
TEXT_FIELDS = ("name", "goal", "stage", "block_reason", "handoff")
LIST_FIELDS = ("acceptance", "priorities", "roadmap", "checks")
SCOPE_FIELDS = ("files", "network", "tools")
FIELDS = frozenset(("project_id", "repository", "local_path", "documents", "git_sha",
                    "roles", "budget", "requested_scope", "git_policy", *TEXT_FIELDS, *LIST_FIELDS))
REQUIREMENT_ROLES = ("GOAL", "FEATURE", "DESIGN", "BUG", "IMPROVEMENT")
MAX_REQUIREMENT_BYTES = 256 * 1024
MAX_MANAGER_REQUIREMENT_BYTES = 512 * 1024


def _requirement_path(relative):
    if (not _text(relative) or any(char in relative for char in ':\\<>|?*"')
            or Path(relative).suffix.lower() not in (".md", ".txt")
            or any(part.casefold() in ("", ".", "..", ".ai-local", ".git")
                   or part.endswith((".", " ")) for part in relative.split("/"))):
        raise ValueError("Use a project-relative TXT or Markdown requirement path")
    return relative


def _read_requirement(root, relative, state_boundary, *, local=False):
    target = root / _requirement_path(relative)
    check_path(target)
    if not target.is_file():
        raise Blocked("Requirement is missing")
    actual = target.resolve(strict=True)
    if (not actual.is_relative_to(root.resolve(strict=True))
            or actual.is_relative_to(state_boundary.resolve()) != local):
        raise Blocked("Requirement is outside the document boundary")
    check_path(actual)
    before = actual.lstat()
    with actual.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not opened.st_ino or not os.path.samestat(before, opened):
            raise Blocked("Requirement file identity changed")
        content = stream.read(MAX_REQUIREMENT_BYTES + 1)
        after = os.fstat(stream.fileno())
        check_path(actual)
        if (not os.path.samestat(after, actual.lstat())
                or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
            raise Blocked("Requirement file changed during read")
    if len(content) > MAX_REQUIREMENT_BYTES or not content.decode("utf-8-sig").strip():
        raise ValueError("Use a nonempty UTF-8 document of at most 256 KiB")
    return content


def _local_requirement_root(state_boundary, project_id):
    return state_boundary / "requirements" / digest(project_id)


def read_frozen_requirements(catalog, project_id, references, state_boundary):
    """Read only registered, frozen revisions. A changed file needs a new revision."""
    if not references:
        return []
    project = catalog["projects"].get(project_id)
    if not project or not project["data"]["local_path"]:
        raise Blocked("Requirement project is unregistered")
    root = Path(project["data"]["local_path"])
    registered = {item["id"]: item for item in catalog.get("requirements", {}).get(project_id, ())}
    documents, seen, total = [], set(), 0
    for ref in references:
        if registered.get(ref["id"]) != ref or ref["id"] in seen:
            raise Blocked("Requirement reference is not a unique registered revision")
        seen.add(ref["id"])
        try:
            if ref.get("storage") == "local":
                local_root = _local_requirement_root(state_boundary, project_id)
                content = _read_requirement(local_root, ref["sha256"] + ".md",
                                            state_boundary, local=True)
            else:
                content = _read_requirement(root, ref["path"], state_boundary)
        except (OSError, ValueError):
            raise Blocked("Frozen requirement is unreadable") from None
        if sha256(content).hexdigest() != ref["sha256"]:
            raise Blocked("Frozen requirement changed; register a revision for a new session")
        total += len(content)
        if total > MAX_MANAGER_REQUIREMENT_BYTES:
            raise Blocked("Manager requirement input exceeds its size limit")
        documents.append({"ref": ref, "text": content.decode("utf-8-sig")})
    return documents


def _text(value, limit=512):
    return (isinstance(value, str) and len(value) <= limit
            and all(char.isprintable() for char in value))


def _strings(values):
    return isinstance(values, list) and len(values) <= 32 and all(_text(value) for value in values)


def validate_budget(value):
    if (not isinstance(value, dict) or set(value) != set(BUDGET_FIELDS)
            or any(type(amount) is not int or not 0 <= amount <= 10**12 for amount in value.values())
            or not 1 <= value["seconds"] <= 86_400):
        raise ValueError("Invalid budget: integer seconds, tokens and cost_microunits required")


def validate_project(data):
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("Unexpected project registration fields")
    project_id = data["project_id"]
    if (not _text(project_id, 48) or not re.fullmatch("[a-z0-9][a-z0-9-]*", project_id)
            or project_id in FIXTURE_IDS):
        raise ValueError("Invalid or reserved project ID")
    if (any(not _text(data[key]) for key in TEXT_FIELDS if key != "goal")
            or not isinstance(data["goal"], str) or not 0 < len(data["goal"].strip()) <= 8000
            or len(data["goal"]) > 8000
            or any(not (char.isprintable() or char in "\r\n\t") for char in data["goal"])
            or not data["name"].strip()):
        raise ValueError("Invalid project text")
    criteria = data["acceptance"]
    if (any(not _strings(data[key]) for key in LIST_FIELDS if key != "acceptance")
            or not isinstance(criteria, list) or not 1 <= len(criteria) <= 32
            or any(not _text(item, 2000) or not item.strip() for item in criteria)):
        raise ValueError("Invalid project criteria or lists")
    repo = data["repository"]
    if not _text(repo, 256) or (repo and not re.fullmatch(
            r"https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repo)):
        raise ValueError("Use a GitHub repository URL without credentials or parameters")
    path = data["local_path"]
    if not _text(path) or (path and (not PureWindowsPath(path).is_absolute()
                                     or not re.match(r"^[A-Za-z]:[\\/]", path)
                                     or ".." in PureWindowsPath(path).parts or ":" in path[2:])):
        raise ValueError("Use a local absolute Windows path")
    docs = data["documents"]
    if (not isinstance(docs, dict) or set(docs) - {"vision", "agents", "definition", "architecture"}
            or any(not _text(doc) or not doc.endswith(".md") or "\\" in doc or ":" in doc
                   or any(part in ("", ".", "..") for part in doc.split("/"))
                   for doc in docs.values())):
        raise ValueError("Documents must be project-relative Markdown paths")
    sha = data["git_sha"]
    if sha is not None and (not _text(sha, 40) or not re.fullmatch("[0-9a-f]{40}", sha)):
        raise ValueError("Invalid Git SHA")
    validate_budget(data["budget"])
    scope, git = data["requested_scope"], data["git_policy"]
    if not isinstance(scope, dict) or set(scope) != set(SCOPE_FIELDS) or not all(map(_strings, scope.values())):
        raise ValueError("Invalid requested scope")
    if not isinstance(git, dict) or set(git) != {"commit", "push"} or any(
            value not in ("disabled", "owner_approval") for value in git.values()):
        raise ValueError("Invalid Git policy")
    roles = data["roles"]
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise ValueError("All four role settings are required")
    for role, config in roles.items():
        if not isinstance(config, dict) or set(config) != {"model", "reasoning", "budget"}:
            raise ValueError("Invalid role configuration")
        model, effort = config["model"], config["reasoning"]
        if (not _text(model, 100) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", model)
                or effort not in ("default", "minimal", "low", "medium", "high", "xhigh")
                or (role == "Verifier" and (model, effort) != ("deterministic", "default"))
                or (role != "Verifier" and model == "deterministic")):
            raise ValueError("Invalid model or reasoning preference")
        validate_budget(config["budget"])


def new_project(project_id, name, goal):
    return {
        "project_id": project_id, "name": name, "goal": goal, "stage": "REGISTERED",
        "block_reason": "OS isolation unverified", "handoff": "", "git_sha": None,
        "repository": "", "local_path": "", "documents": {},
        "acceptance": ["Verify synthetic workflow"], "priorities": [], "roadmap": [], "checks": [],
        "requested_scope": {key: [] for key in SCOPE_FIELDS},
        "git_policy": {"commit": "disabled", "push": "disabled"},
        "budget": dict(DEFAULT_BUDGET),
        "roles": {role: {"model": "deterministic" if role == "Verifier" else "mock-v1",
                         "reasoning": "default", "budget": dict(DEFAULT_BUDGET)} for role in ROLES},
    }


class Catalog:
    """Owner configuration controller with a separate hash-chained local ledger."""
    def __init__(self, runtime_ledger):
        self.ledger = Ledger(runtime_ledger.path.with_name(runtime_ledger.path.stem + "-catalog.sqlite3"),
                             local_root=runtime_ledger.local_root)

    def _snapshot(self):
        check_path(self.ledger.path)
        try:
            self.ledger.path.lstat()
        except FileNotFoundError:
            return None
        return self.ledger.snapshot()

    @staticmethod
    def _project(events):
        projects, setups, requirements, limits = {}, {}, {}, dict(DEFAULT_BUDGET)
        reserve = None
        for index, event in enumerate(events, 1):
            if event["kind"] in ("project_saved", "setup_saved"):
                data = event["data"]
                if event["kind"] == "setup_saved":
                    from .setup import validate_setup
                    validate_setup(data, allow_file_proposals=data["project"]["project_id"] in projects)
                    setups[data["project"]["project_id"]] = {
                        "replica": data["replica"], "wait_seconds": data["wait_seconds"],
                    }
                    data = data["project"]
                validate_project(data)
                projects[data["project_id"]] = {"revision": index, "data": data}
            elif event["kind"] == "limits_saved":
                validate_budget(event["data"])
                limits = event["data"]
            elif event["kind"] == "token_reserve_saved":
                validate_reserve(event["data"])
                reserve = event["data"]
            elif event["kind"] == "requirement_registered":
                requirement = event["data"]
                requirements.setdefault(requirement["project_id"], []).append(requirement)
            else:
                raise Blocked("Unknown catalog event")
        view = {"projects": projects, "setups": setups, "global_budget": limits, "version": len(events)}
        if requirements:
            view["requirements"] = requirements
        if reserve is not None:
            view["token_reserve"] = reserve
        return view

    def read(self):
        snapshot = self._snapshot()
        return self._project(snapshot[0] if snapshot else [])

    def save_project(self, data, revision=0):
        validate_project(data)
        return self._save("project_saved", data, revision)

    def save_limits(self, data, revision):
        validate_budget(data)
        return self._save("limits_saved", data, revision)

    def save_token_reserve(self, data, revision):
        validate_reserve(data)
        return self._save("token_reserve_saved", data, revision)

    def save_setup(self, data, revision=0, *, held=False):
        from .setup import environment_status, validate_setup
        if not environment_status()["windows_x64"]:
            raise Blocked("Setup requires Windows x64")
        validate_setup(data, self.ledger.state_boundary, allow_file_proposals=revision != 0)
        return self._save("setup_saved", data, revision, held=held)

    def register_requirement(self, project_id, relative, role, *, related=None):
        """Owner-selected read of one document. Store references and hashes only."""
        relative = _requirement_path(relative)
        if role not in REQUIREMENT_ROLES:
            raise ValueError("Unknown requirement role")
        with execution_slot() as abandoned:
            if abandoned:
                raise Blocked("Abandoned slot requires inspection")
            snapshot = self._snapshot()
            events, tail = snapshot if snapshot is not None else ([], "")
            view = self._project(events)
            project = view["projects"].get(project_id)
            if not project or not project["data"]["local_path"]:
                raise Blocked("Register the original project folder first")
            root = Path(project["data"]["local_path"])
            history = view.get("requirements", {}).get(project_id, [])
            latest = next((item for item in reversed(history)
                           if item["path"].casefold() == relative.casefold()), None)
            if related is not None and not any(item["id"] == related for item in history):
                raise Blocked("Related requirement does not belong to this project")
            content = _read_requirement(root, relative, self.ledger.state_boundary)
            checksum = sha256(content).hexdigest()
            if (latest and latest.get("storage") != "local"
                    and (latest["sha256"], latest["role"], latest["related"]) == (checksum, role, related)):
                return latest
            return self._append_requirement(events, tail, project_id, relative, role, checksum, related, latest)

    def _append_requirement(self, events, tail, project_id, relative, role, checksum, related, latest,
                            *, local=False):
        now = datetime.now(timezone.utc).isoformat()
        item = {"project_id": project_id, "path": relative, "role": role, "sha256": checksum,
                "version": latest["version"] + 1 if latest else 1,
                "supersedes": latest["id"] if latest else None, "related": related,
                "registered_at": now}
        if local:
            item["storage"] = "local"
        item["id"] = digest(item)
        self.ledger.append(len(events), tail, {"kind": "requirement_registered",
                                              "data": item, "timestamp": now})
        return item

    def write_local_requirement(self, project_id, relative, text, role, *, related=None):
        """Save an owner-authored revision without changing either Git worktree."""
        relative = _requirement_path(relative)
        if not relative.lower().endswith(".md") or role not in REQUIREMENT_ROLES or not text.strip():
            raise ValueError("Markdown 문서 이름과 내용을 입력하세요.")
        data = text.encode("utf-8")
        if len(data) > MAX_REQUIREMENT_BYTES:
            raise ValueError("문서는 UTF-8 기준 256 KiB까지 저장할 수 있습니다.")
        with execution_slot() as abandoned:
            if abandoned:
                raise Blocked("Abandoned slot requires inspection")
            snapshot = self._snapshot()
            events, tail = snapshot if snapshot is not None else ([], "")
            view = self._project(events)
            project = view["projects"].get(project_id)
            if not project or not project["data"]["local_path"]:
                raise Blocked("Register the original project folder first")
            history = view.get("requirements", {}).get(project_id, [])
            latest = next((item for item in reversed(history)
                           if item["path"].casefold() == relative.casefold()), None)
            if related is not None and not any(item["id"] == related for item in history):
                raise Blocked("Related requirement does not belong to this project")
            if latest and latest.get("storage") != "local":
                raise FileExistsError(relative)
            checksum = sha256(data).hexdigest()
            root = _local_requirement_root(self.ledger.state_boundary, project_id)
            target = root / (checksum + ".md")
            check_path(target)
            root.mkdir(parents=True, exist_ok=True)
            check_path(target)
            if not target.exists():
                with target.open("xb") as stream:
                    stream.write(data)
            if _read_requirement(root, target.name, self.ledger.state_boundary, local=True) != data:
                raise Blocked("Stored requirement does not match the authored revision")
            if latest and (latest["sha256"], latest["role"], latest["related"]) == (checksum, role, related):
                return latest
            return self._append_requirement(events, tail, project_id, relative, role, checksum, related, latest,
                                            local=True)

    def write_requirement(self, project_id, relative, text, role, *, related=None):
        """Explicit owner action. Create a Markdown file exclusively, then register it."""
        relative = _requirement_path(relative)
        if not relative.lower().endswith(".md") or role not in REQUIREMENT_ROLES or not text.strip():
            raise ValueError("Markdown 문서 이름과 내용을 입력하세요.")
        data = text.encode("utf-8")
        if len(data) > MAX_REQUIREMENT_BYTES:
            raise ValueError("문서는 UTF-8 기준 256 KiB까지 저장할 수 있습니다.")
        current = self.read()
        if related is not None and not any(item["id"] == related for item in
                                           current.get("requirements", {}).get(project_id, ())):
            raise Blocked("관련 요구사항을 다시 선택하세요.")
        root = Path(current["projects"][project_id]["data"]["local_path"])
        target = root / relative
        check_path(target)
        if not target.parent.is_dir() or target.is_relative_to(self.ledger.state_boundary):
            raise Blocked("프로젝트 안의 기존 문서 폴더를 선택하세요.")
        with target.open("xb") as stream:
            stream.write(data)
        return self.register_requirement(project_id, relative, role, related=related)

    def _save(self, kind, data, revision, *, held=False):
        from .lease import held_execution_slot

        with (held_execution_slot() if held else execution_slot()) as abandoned:
            if abandoned:
                raise Blocked("Abandoned slot requires inspection")
            snapshot = self._snapshot()
            if snapshot is None:
                if revision != 0:
                    raise Blocked("Catalog revision no longer exists")
                self.ledger.initialize()
                snapshot = ([], "")
            events, tail = snapshot
            current = self._project(events)
            if kind in ("project_saved", "setup_saved"):
                project = data["project"] if kind == "setup_saved" else data
                latest = current["projects"].get(project["project_id"], {"revision": 0})["revision"]
                if (current.get("requirements", {}).get(project["project_id"])
                        and PureWindowsPath(project["local_path"]) != PureWindowsPath(
                            current["projects"][project["project_id"]]["data"]["local_path"])):
                    raise Blocked("Requirement references are bound to the original project folder")
                if kind == "project_saved" and project["project_id"] in current["setups"]:
                    from .setup import validate_setup
                    validate_setup({"project": project, **current["setups"][project["project_id"]]},
                                   self.ledger.state_boundary, allow_file_proposals=True)
            else:
                latest = current["version"]
            if type(revision) is not int or latest != revision:
                raise Blocked("Stale catalog revision")
            event = {"kind": kind, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
            self.ledger.append(len(events), tail, event)
            return len(events) + 1


def latest_requirements(catalog, project_id):
    latest = {}
    for item in catalog.get("requirements", {}).get(project_id, ()):
        latest[item["path"].casefold()] = item
    return list(latest.values())
