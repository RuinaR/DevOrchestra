"""Approved, file-only onboarding for ordinary Windows project folders."""
import os
from pathlib import Path
from uuid import uuid4

from .catalog import Catalog
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .owned_artifacts import _remove_owned_tree
from .project_onboarding import _overlaps_registered_path, _protected, _registered, _relative
from .replica_evidence import fingerprint, snapshot
from .setup import validate_setup


MODE = "LOCAL_FOLDER"
PRIVATE_NAMES = frozenset({".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".npmrc",
                           ".pypirc", ".netrc", ".git-credentials", ".authinfo", ".credentials",
                           "__pycache__", ".venv", ".ruff_cache"})


def git_entry(root):
    """Detect a Git root or a selected child of one without invoking Git."""
    for directory in (root, *root.parents):
        marker = directory / ".git"
        if os.path.lexists(marker):
            return marker
        if (directory / "HEAD").is_file() and (directory / "objects").is_dir() and (directory / "refs").is_dir():
            return directory
    return None


def protected(name):
    return (_protected(name) or any(
        part.casefold() in PRIVATE_NAMES or part.casefold().startswith((".devorchestra-", ".cache", ".pytest_cache"))
        for part in _relative(name).parts))


def inventory(root):
    check_path(root)
    if git_entry(root) is not None:
        raise Blocked("일반 폴더는 Git 작업 트리와 겹칠 수 없습니다.")

    image = snapshot(root, excluded=protected)
    if image["git"] is not None or git_entry(root) is not None:
        raise Blocked("일반 폴더의 Git 상태가 변경됐습니다.")
    return {"mode": MODE, **image, "head": None, "staged": [], "raw_worktree_changed": [],
            "local_examples": [], "documents": []}


def require_candidate(frozen, image):
    files = image["files"]
    old = frozen["files"]
    if (image["git"] is not None or old.keys() - files.keys()
            or any(protected(name) for name in files if files[name] != old.get(name))):
        raise Blocked("일반 폴더 후보의 파일 삭제, 보호 경로 변경 또는 Git 메타데이터 추가가 확인됐습니다.")


def _scope(ledger, project, destination, tail):
    catalog, row, source = _registered(ledger, project)
    setup = {"project": row["data"], "replica": str(destination), "wait_seconds": 10}
    validate_setup(setup, ledger.state_boundary, allow_file_proposals=True)
    check_path(destination)
    if (destination.exists() or not destination.parent.is_dir() or git_entry(destination.parent) is not None
            or _overlaps_registered_path(catalog, project, destination)):
        raise Blocked("다른 프로젝트나 Git 작업 트리와 겹치지 않는 새 개발 사본 경로를 선택하세요.")
    return {"project_id": project, "source": str(source), "destination": str(destination),
            "contract": MODE, "project_revision": row["revision"], "ledger_head": tail,
            "baseline": inventory(source)}, setup


def review(runner, project, destination):
    with execution_slot() as abandoned:
        _, tail = runner._idle_diagnostic(abandoned)
        return _scope(runner.ledger, project, Path(destination), tail)[0]


def create(runner, project, destination, approved):
    from .runtime import require_admission

    ledger = runner.ledger
    destination = Path(destination)
    with execution_slot() as abandoned:
        events, tail = runner._idle_diagnostic(abandoned)
        scope, setup = _scope(ledger, project, destination, tail)
        if digest(scope) != approved:
            raise Blocked("파일 기준선이나 개발 사본 승인이 변경됐습니다.")
        original = Path(scope["source"])
        stage = destination.with_name(".devorchestra-folder-" + uuid4().hex)
        check_path(stage)
        binding = {"project_id": project, "run_id": uuid4().hex, "state": "DIAGNOSTIC",
                   "scope_sha256": approved, "staging": str(stage)}
        count, fence = ledger.append(len(events), tail, {**binding, "kind": "onboarding_intent", "scope": scope},
                                     admission=require_admission)
        moved = False
        identity = None
        try:
            stage.mkdir()
            identity = stage.stat().st_ino
            for name, info in scope["baseline"]["files"].items():
                runner._check_diagnostic_fence(count, fence)
                src, dst = original / _relative(name), stage / _relative(name)
                check_path(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                with src.open("rb") as stream, dst.open("xb") as out:
                    remaining = info["bytes"]
                    while remaining:
                        block = stream.read(min(remaining, 1024**2))
                        if not block:
                            raise Blocked("복사 중 원본 크기가 변경됐습니다.")
                        out.write(block)
                        remaining -= len(block)
                    if stream.read(1):
                        raise Blocked("복사 중 원본 크기가 변경됐습니다.")
                if fingerprint(dst) != info:
                    raise Blocked("복사한 파일이 승인한 해시와 다릅니다.")
            image = snapshot(stage)
            if image["git"] is not None or image["files"] != scope["baseline"]["files"]:
                raise Blocked("개발 사본의 매니페스트가 승인 범위와 다릅니다.")
            if inventory(original) != scope["baseline"]:
                raise Blocked("복사 중 원본이 변경됐습니다.")
            runner._check_diagnostic_fence(count, fence)
            check_path(destination)
            stage.rename(destination)
            moved = True
            revision = Catalog(ledger).save_setup(setup, scope["project_revision"], held=True)
        except (OSError, Blocked, ValueError, KeyboardInterrupt):
            if not moved and (identity is None or _remove_owned_tree(stage, identity)["status"] == "REMOVED"):
                ledger.append(count, fence, {**binding, "kind": "onboarding_result", "status": "BLOCKED"},
                              allow_shutdown=True)
            raise
        result = {**binding, "kind": "onboarding_result", "status": "REPLICA_READY", "mode": MODE,
                  "project_revision": revision, "replica": str(destination), "original_head": None,
                  "replica_head": None, "replica_sha256": digest(image), "environment": "NOT_VALIDATED",
                  "development": "NOT_VALIDATED", "original_rechecked": True, "job_active": 0}
        ledger.append(count, fence, result, allow_shutdown=True)
        return result
