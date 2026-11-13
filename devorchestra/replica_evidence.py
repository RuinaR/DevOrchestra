"""Bounded, read-only evidence from one owner-selected replica."""
from hashlib import sha256
import os
from pathlib import Path
import re
import stat

from .lease import Blocked
from .ledger import check_path, digest


def _native(path, long_local):
    return Path("\\\\?\\" + str(path)) if long_local else path


def fingerprint(path, limit=64 * 1024 * 1024, *, installed_executable=False, long_local=False):
    check_path(path, installed_executable=installed_executable, long_local=long_local)
    io_path = _native(path, long_local)
    before = io_path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise Blocked("Replica file is unsupported or exceeds the evidence limit")
    checksum = sha256()
    size = 0
    with io_path.open("rb") as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise Blocked("Replica file identity changed")
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise Blocked("Replica file exceeded the evidence limit")
            checksum.update(chunk)
        after = os.fstat(stream.fileno())
    if (not os.path.samestat(after, io_path.lstat()) or size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns):
        raise Blocked("Replica file changed during inspection")
    return {"sha256": checksum.hexdigest(), "bytes": size}


def _git_identity(root):
    git = root / ".git"
    check_path(git)
    if not git.exists():
        return None
    if not git.is_dir():
        raise Blocked("Linked Git metadata is outside the supported replica boundary")
    head = git / "HEAD"
    head_before = fingerprint(head, 4096)
    value = head.read_bytes().decode("ascii").strip()
    result = {"head": head_before}
    if value.startswith("ref: "):
        ref = value[5:]
        if not re.fullmatch(r"refs/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+", ref) or ".." in ref:
            raise Blocked("Unsupported Git HEAD reference")
        loose = git / Path(ref)
        check_path(loose)
        if loose.exists():
            result["ref"] = fingerprint(loose, 4096)
        else:
            packed = git / "packed-refs"
            check_path(packed)
            result["packed_refs"] = fingerprint(packed) if packed.exists() else None
    elif not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise Blocked("Unsupported Git HEAD")
    if fingerprint(head, 4096) != head_before:
        raise Blocked("Git HEAD changed during inspection")
    index = git / "index"
    check_path(index)
    result["index"] = fingerprint(index) if index.exists() else None
    return result


def snapshot(root, *, owned_staging=None, max_file_bytes=64 * 1024**2, long_local=False):
    """Capture files without following links or invoking Git or a build tool."""
    if owned_staging is not None:
        check_path(owned_staging)
        if (owned_staging.parent != root or not owned_staging.is_dir()
                or not owned_staging.name.startswith((".devorchestra-batch-", ".devorchestra-tools-"))):
            raise Blocked("Unrecognized owned replica staging")
    files, keys = {}, set()
    total = count = 0
    directories = [root]
    git = _git_identity(root)
    while directories:
        directory = directories.pop()
        check_path(directory, long_local=long_local)
        with os.scandir(_native(directory, long_local)) as entries:
            for item in entries:
                count += 1
                if count > 4096:
                    raise Blocked("Replica exceeds the evidence entry limit")
                path = directory / item.name
                if path == owned_staging:
                    continue
                relative = path.relative_to(root).as_posix()
                folded = relative.casefold()
                if folded in keys:
                    raise Blocked("Ambiguous replica path identity")
                keys.add(folded)
                if relative == ".git":
                    continue
                if item.name.casefold() in (".git", ".ai-local"):
                    raise Blocked("Nested Git or private data in the replica")
                check_path(path, long_local=long_local)
                if item.is_dir(follow_symlinks=False):
                    directories.append(path)
                    continue
                file = fingerprint(path, min(max_file_bytes, 256 * 1024**2 - total), long_local=long_local)
                total += file["bytes"]
                files[relative] = file
    if _git_identity(root) != git:
        raise Blocked("Git identity changed during inspection")
    info = root.stat()
    return {"directory_id": [info.st_dev, info.st_ino], "git": git, "files": files}


def changes(before, after, allowed):
    if (before["directory_id"] != after["directory_id"] or before["git"] != after["git"]):
        raise Blocked("Replica identity, Git HEAD, or index changed")
    old, new = before["files"], after["files"]
    paths = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    if not paths or any(path not in allowed for path in paths):
        raise Blocked("No observed change, or a file changed outside the approved scope")
    return {path: {"before": old.get(path), "after": new.get(path)} for path in paths}


def revision(image):
    return digest(image)


def verify_pe(path, expected):
    check_path(path)
    with path.open("rb") as stream:
        header = stream.read(64)
        offset = int.from_bytes(header[60:64], "little")
        if len(header) != 64 or header[:2] != b"MZ" or not 64 <= offset <= expected["bytes"] - 24:
            raise Blocked("Declared EXE does not have a valid PE header")
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise Blocked("Declared EXE does not have a valid PE header")
    if fingerprint(path) != expected:
        raise Blocked("EXE identity changed during inspection")
