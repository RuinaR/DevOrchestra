"""Bounded Developer batch staging, rollback and change verification."""
from base64 import b64encode
from difflib import unified_diff
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .chatgpt_subscription import write_protected
from .lease import Blocked
from .ledger import check_path
from .replica_evidence import changes, snapshot
from .role_contract import _unique_object

FILE_LIMIT = 8192
BATCH_LIMIT = 16384


def check_names(names, create, extension="py"):
    from .project_onboarding import _protected
    from .supervised import _path_allowed

    if (type(create) is not list or not 2 <= len(names) <= 4
            or len({name.casefold() for name in names}) != len(names)
            or any(not re.fullmatch(rf"(?:[A-Za-z0-9_-]{{1,64}}/){{0,6}}[A-Za-z0-9_-]{{1,64}}\.{extension}", name)
                   or not _path_allowed(name) or _protected(name) for name in names)
            or any(type(name) is not str or name not in names for name in create)
            or len(set(create)) != len(create)):
        raise Blocked(f"Batch requires two to four exact .{extension} paths and explicit creation paths")


def replacement(output, names, *, csharp=False, sdk_project=False):
    try:
        if len(output) > BATCH_LIMIT * 2:
            raise ValueError
        edits = json.loads(output, object_pairs_hook=_unique_object)
        if type(edits) is not dict or set(edits) != set(names):
            raise ValueError
        result = {}
        for name, text in edits.items():
            if type(text) is not str:
                raise ValueError
            data = text.encode("utf-8")
            if not 0 < len(data) <= FILE_LIMIT or b"\0" in data:
                raise ValueError
            if not csharp:
                compile(data, name, "exec", dont_inherit=True)
            result[name] = data
        if sum(map(len, result.values())) > BATCH_LIMIT:
            raise ValueError
        if csharp and not sdk_project:
            from .csharp_sources import validate

            if len({Path(name).name.casefold() for name in result}) != len(result):
                raise Blocked("Ambiguous C# source basename")
            validate({Path(name).name: data for name, data in result.items()})
        return result
    except (ValueError, UnicodeError, SyntaxError, RecursionError):
        raise Blocked("Invalid batch replacement for the approved files") from None


def _encode(data):
    return None if data is None else b64encode(data).decode("ascii")


def _matches(path, expected):
    check_path(path)
    if expected is None:
        return not path.exists()
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(len(expected) + 1) == expected


def apply(replica, before, original, replacement, journal, check):
    modified = [name for name in sorted(original) if original[name] != replacement[name]]
    if not modified:
        raise Blocked("Developer proposed no file change")
    if sum(len(original[name] or b"") + len(replacement[name]) for name in modified) > BATCH_LIMIT:
        raise Blocked("Developer batch change exceeds the approved byte limit")
    check_path(replica)
    with TemporaryDirectory(prefix=".devorchestra-batch-", dir=replica) as directory:
        stage = Path(directory)
        for index, name in enumerate(modified):
            for label, content in (("next", replacement[name]), ("old", original[name])):
                if content is not None:
                    with (stage / f"{label}-{index}").open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
        check()
        if snapshot(replica, owned_staging=stage) != before:
            raise Blocked("Developer replica changed before batch apply")
        write_protected(journal, {"replica": str(replica), "files": {
            name: {"before": _encode(original[name]), "after_sha256": sha256(replacement[name]).hexdigest()}
            for name in modified
        }})
        applied = []
        try:
            for index, name in enumerate(modified):
                check()
                target = replica / name
                check_path(target)
                if not _matches(target, original[name]):
                    raise Blocked("Developer batch target changed")
                if original[name] is None:
                    # Windows rename fails if a destination appeared after review.
                    os.rename(stage / f"next-{index}", target)
                else:
                    os.replace(stage / f"next-{index}", target)
                applied.append((index, name))
            after = snapshot(replica, owned_staging=stage)
            observed = changes(before, after, set(modified))
            if set(observed) != set(modified) or any(
                    observed[name]["after"]["sha256"] != sha256(replacement[name]).hexdigest() for name in modified):
                raise Blocked("Developer batch evidence is incomplete")
        except BaseException:
            for index, name in reversed(applied):
                target = replica / name
                check_path(target)
                if not _matches(target, replacement[name]):
                    raise OSError("Batch rollback ownership is unverified; preserve recovery journal")
                if original[name] is None:
                    target.unlink()
                else:
                    os.replace(stage / f"old-{index}", target)
            if snapshot(replica, owned_staging=stage) != before:
                raise OSError("Batch rollback is unverified; preserve recovery journal")
            journal.unlink()
            raise Blocked("Developer batch application failed and was rolled back") from None
    if snapshot(replica) != after:
        raise OSError("Batch final state is unverified; preserve recovery journal")
    return after, observed


def diff_text(original, replacement, changed):
    return "".join(line for name in sorted(changed) for line in unified_diff(
        (original[name] or b"").decode().splitlines(keepends=True),
        replacement[name].decode().splitlines(keepends=True),
        f"a/{name}" if original[name] is not None else "/dev/null", f"b/{name}",
    ))
