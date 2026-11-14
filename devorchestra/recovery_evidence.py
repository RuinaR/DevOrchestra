"""Reconstruct retained candidates from a bounded subset of recorded literal edits."""
from hashlib import sha256
import json
from pathlib import Path
import re
import shlex

from .lease import Blocked
from .ledger import check_path, digest
from .project_onboarding import _relative
from .replica_evidence import fingerprint
from .role_contract import _unique_object


_LITERAL = r"'([^'\r\n]*)'"
_REPLACE = re.compile(
    r"\$p=Join-Path \(Get-Location\) " + _LITERAL
    + r"\n\$s=\[IO\.File\]::ReadAllText\(\$p\)\.Replace\(" + _LITERAL + "," + _LITERAL
    + r"\)\n\[IO\.File\]::WriteAllText\(\$p,\$s,\(New-Object System\.Text\.UTF8Encoding\(\$false\)\)\)"
    + r'(?:\n\$e=' + _LITERAL + r'\nSelect-String -Path "\$e\\([\w.-]+)"'
    + r" -Pattern " + _LITERAL + r" \| ForEach-Object \{\$_\.Line\})?"
)
_MAX_BYTES = 8 * 1024**2


class _Pairs(list):
    pass


def read_commands(log, limit, expected_sha=None):
    check_path(log)
    with log.open("rb") as stream:
        raw = stream.read(limit + 1)
    log_sha = sha256(raw).hexdigest()
    if len(raw) > limit:
        raise Blocked("Retained Developer transcript exceeds the evidence bound")
    if expected_sha is not None and log_sha != expected_sha:
        raise Blocked("Retained Developer transcript differs from the role receipt")
    started, completed = {}, []
    try:
        for line in raw.decode("utf-8").split("\n"):
            if not line.strip():
                continue
            row = _unique_object(json.loads(line, object_pairs_hook=_Pairs))
            fields = row.get("item", ())
            item = dict(fields)
            if item.get("type") != "command_execution":
                continue
            item = _unique_object(fields)
            kind, key, command = row["type"], item["id"], item["command"]
            if kind == "item.started":
                if key in started:
                    raise ValueError("Duplicate command start")
                started[key] = (command, len(started), False)
            elif kind == "item.completed":
                initial, order, settled = started[key]
                if initial != command or settled:
                    raise ValueError("Unpaired command receipt")
                started[key] = (initial, order, True)
                if item.get("status") == "completed" and type(item.get("exit_code")) is int and item["exit_code"] == 0:
                    completed.append((key, command, order, len(started)))
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise Blocked("Invalid retained Developer command receipts") from error
    return started, completed, log_sha


def _powershell(command):
    try:
        argv = shlex.split(command)
    except ValueError:
        return ""
    if (len(argv) == 3 and Path(argv[0]).name.casefold() == "powershell.exe"
            and argv[1].casefold() == "-command"):
        return argv[2].replace("\r\n", "\n").strip()
    return ""


def _read_only(script):
    unquoted = re.sub(_LITERAL, "''", script)
    return bool(unquoted) and all(
        re.fullmatch(r"(?:Get-Content|Get-ChildItem|Select-String|Select-Object)(?: [\w .,:/\\'*-]+)?",
                     stage.strip(), re.I)
        for stage in re.split(r"[;|]", unquoted)
    )


def _witnesses_staging(script, folder):
    prefix = f"$e='{folder / 'evidence'}'; "
    if not script.casefold().startswith(prefix.casefold()):
        return False
    reads = re.sub(r'"\$e\\[\w.-]+"', "file", script[len(prefix):])
    return reads.startswith("Get-ChildItem $e |") and _read_only(reads.replace("$e", "directory"))


def reconstruct(folder, root, paths, files, records):
    """Treat tool records as data. Never invoke their commands or modify the checkout."""
    started, completed, log_sha = records
    # A subsequent successful read witnesses the retained output directory. The producer
    # may have failed after staging, so its command is only an ordering marker.
    output = str(folder.parent)
    witnesses = [(order, end) for _, command, order, end in completed
                 if _witnesses_staging(_powershell(command), folder.parent)]
    producers = [order for command, order, _ in started.values() if re.search(
        r" -ProjectRoot '" + re.escape(str(root)) + r"' -OutputDirectory '"
        + re.escape(output) + r"'$", _powershell(command), re.I)]
    if not producers or not witnesses or max(producers) >= min(witnesses)[0]:
        return None
    witness, previous_end = min(witnesses)
    manifest, contents, edits = {}, {}, []
    remaining = _MAX_BYTES
    for name in paths:
        path = folder / name
        if not path.is_file():
            return None
        manifest[name] = fingerprint(path, limit=remaining, long_local=True)
        with Path("\\\\?\\" + str(path)).open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
        if {"sha256": sha256(data).hexdigest(), "bytes": len(data)} != manifest[name]:
            raise Blocked("Retained staging changed during reconstruction")
        contents[name] = data
        remaining -= len(data)
    accepted = set()
    for key, command, order, end in completed:
        if order <= witness:
            continue
        match = _REPLACE.fullmatch(_powershell(command))
        if match is None:
            continue
        name, old, new, read_dir, _, _ = match.groups()
        if (_relative(name).as_posix() not in contents or not old or old == new
                or order < previous_end
                or (read_dir is not None and Path(read_dir) != folder.parent / "evidence")):
            return None
        try:
            before = contents[name].decode("utf-8-sig")
        except UnicodeError:
            return None
        if old not in before:
            return None
        if len(new.encode("utf-8")) * before.count(old) > _MAX_BYTES:
            return None
        replacement = before.replace(old, new).encode("utf-8")
        remaining += len(contents[name]) - len(replacement)
        if remaining < 0:
            return None
        contents[name] = replacement
        previous_end = end
        edits.append({"item": key, "path": name, "command_sha256": digest(command)})
        accepted.add(key)
    if any(order > witness and key not in accepted and not _read_only(_powershell(command))
           for key, (command, order, _) in started.items()):
        return None
    if edits and all(
        {"sha256": sha256(data).hexdigest(), "bytes": len(data)} == files[name]
        and fingerprint(root / name, limit=_MAX_BYTES) == files[name]
        and (root / name).read_bytes() == data for name, data in contents.items()
    ):
        return {"retained_files_sha256": digest(manifest), "transcript_sha256": log_sha, "edits": edits}
    return None
