"""Read-only Git onboarding and construction of an isolated development baseline."""
from contextlib import contextmanager
from hashlib import new as hash_new, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
from shutil import which
from tempfile import mkdtemp
from urllib.parse import urlsplit
from uuid import uuid4

from .app_server import verify_app_server_binary
from .catalog import Catalog, latest_requirements
from .default_instructions import public_instruction
from .instructions import InstructionStore
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import LAUNCHER_PIN, PROFILE, _args, _observe, _profile
from .mxc_launcher import _known_folder, _signed_offline
from .replica_evidence import _git_identity, fingerprint, snapshot
from .setup import validate_setup

DOCUMENT = re.compile(r"(?:readme[^/]*|agents(?:\.override)?|project-definition|current-status|ai-handoff|handoff|worklog)\.md", re.I)
PROTECTED = {".ai-local", ".codex", ".agents", ".vs", "node_modules", "venv", ".venv", "bin", "obj",
             "secrets", "credentials", "private", "models", "checkpoints", "__pycache__", ".git"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".pem", ".key", ".pfx", ".dpapi",
                    ".safetensors", ".gguf", ".onnx", ".pt", ".pth", ".ckpt", ".log", ".bin", ".pkl"}


def _relative(value):
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or not path.parts or any(
            part in (".", "..") or part.endswith((" ", ".")) or
            any(ord(ch) < 32 or ch in '\\:<>|?*"' for ch in part)
            or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I)
            for part in path.parts)):
        raise Blocked("Git returned an unsupported worktree path")
    return path


def _protected(value):
    path = _relative(value.rstrip("/"))
    name = path.name.casefold()
    return (bool(PROTECTED & {part.casefold() for part in path.parts}) or path.suffix.casefold() in PRIVATE_SUFFIXES
            or name.startswith((".env", "credentials", "secrets", "auth.", "id_rsa", "id_ed25519"))
            or re.search(r"\.(?:db|sqlite3?)-(?:wal|shm|journal)$", name) is not None
            or name in {"appsettings.json", "local.settings.json", "settings.local.json"})


@contextmanager
def _git_scratch(ledger):
    from .owned_artifacts import _remove_owned_tree

    scratch = Path(mkdtemp(prefix="onboard-git-", dir=ledger.local_directory("temp", create=True)))
    identity = scratch.stat().st_ino
    yield scratch
    if _remove_owned_tree(scratch, identity)["status"] != "REMOVED":
        raise Blocked("Onboarding Git scratch cleanup is unconfirmed")


def _git_tools():
    programs = _known_folder(0x26)
    packaged = Path(which("git.exe") or "")
    roots = [programs / "Git", _known_folder(0x1C) / "Programs" / "Git"]
    if packaged.is_relative_to(programs / "WindowsApps") and packaged.parent.name.casefold() == "cmd":
        roots.insert(0, packaged.parent.parent)
    # Explorer-launched executables do not inherit the Codex terminal's PATH.
    import winreg

    repository = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, repository) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                package = winreg.EnumKey(key, index)
                if re.fullmatch(r"OpenAI\.CodexPrimaryRuntime\.[A-Za-z0-9.-]+_[0-9.]+_x64__3k8sg7r9htsxt", package):
                    roots.append(programs / "WindowsApps" / package / "dependencies" / "native" / "git")
    except OSError:
        pass
    for root in roots:
        entry = root / "cmd" / "git.exe"
        for runtime in ("ucrt64", "mingw64"):
            executable = root / runtime / "bin" / "git.exe"
            if entry.is_file() and executable.is_file() and _signed_offline(entry) and _signed_offline(executable):
                return entry, executable
    raise Blocked("서명된 Git for Windows를 설치한 뒤 다시 실행하세요.")


@contextmanager
def _git(ledger, source, staging=None):
    git, executable = _git_tools()
    tool = git.parent.parent
    identity = {}
    for exe in (git, executable):
        identity[exe.relative_to(tool).as_posix()] = fingerprint(exe, installed_executable=True)["sha256"]
    with _git_scratch(ledger) as scratch:
        from .codex_setup import archive_path
        from .native_command_probe import _stage_release

        archive = archive_path(ledger)
        if archive.is_file():
            launcher = _stage_release(archive, scratch / "codex")
        else:
            launcher = Path(which("codex.exe") or "")
            verify_app_server_binary(launcher, approved_sha256=LAUNCHER_PIN)
        home, output = scratch / "home", scratch / "output"
        home.mkdir()
        output.mkdir()
        grants = [(tool, "read")]
        if staging is not None:
            grants.append((staging, "write"))
        extra = "".join(f'{json.dumps(str(path))} = "{access}"\n' for path, access in grants)
        profile = _profile(source, home, output).replace(f"[permissions.{PROFILE}.network]",
                                                        extra + f"[permissions.{PROFILE}.network]")
        (home / "config.toml").write_text(profile, encoding="ascii")
        windows = _known_folder(0x24)
        env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
               "LOCALAPPDATA": str(_known_folder(0x1C)), "ProgramData": str(_known_folder(0x23)),
               "PATH": str(windows / "System32"), "CODEX_HOME": str(home), "HOME": str(home),
               "TEMP": str(output), "TMP": str(output), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "NUL", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
               "GIT_NO_LAZY_FETCH": "1", "GIT_ATTR_NOSYSTEM": "1"}
        def command(*args, cwd=source, allowed=(0,)):
            logs = output / uuid4().hex
            logs.mkdir()
            roots = (f"--git-dir={source / '.git'}", f"--work-tree={source}") if cwd == source else ()
            result = _observe(launcher, _args(git, "--no-optional-locks", "--no-replace-objects",
                                                "-c", "core.hooksPath=NUL", "-c", "core.excludesFile=NUL",
                                                "-c", "core.attributesFile=NUL", "-c", "core.fsmonitor=false",
                                                "-c", "core.autocrlf=false", "-c", "maintenance.auto=false",
                                                "-c", "gc.auto=0", *roots, *args),
                              cwd, logs, env, seconds=20, check=lambda: None, output_limit=1024 * 1024, encoding="utf-8")
            if result["exitCode"] not in allowed or result["stderr"] or result["job_active"]:
                raise Blocked("Offline Git onboarding command could not be verified")
            return result["stdout"]
        yield command, identity


def _records(raw, *, tree=False):
    result = {}
    for record in raw.split("\0"):
        if not record:
            continue
        header, path = record.split("\t", 1)
        mode, kind, oid = header.split() if tree else (header.split()[0], header.split()[2], header.split()[1])
        if mode not in {"100644", "100755", "120000", "160000"} or kind not in ({"blob", "commit"} if tree else {"0"}):
            raise Blocked("Git index has unresolved stages or unsupported entries")
        _relative(path)
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) or path in result or len(result) >= 4096:
            raise Blocked("Git inventory exceeds limits or has ambiguous entries")
        result[path] = (mode, oid)
    return result


def _blob_identity(path, info, oid):
    algorithm = "sha1" if len(oid) == 40 else "sha256"
    checksum = hash_new(algorithm, f"blob {info['bytes']}\0".encode("ascii"))
    before = path.lstat()
    with path.open("rb") as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise Blocked("Worktree file identity changed during Git comparison")
        remaining = info["bytes"]
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise Blocked("Worktree file changed during Git comparison")
            checksum.update(chunk)
            remaining -= len(chunk)
    if fingerprint(path) != info:
        raise Blocked("Worktree file changed during Git comparison")
    return checksum.hexdigest()


def _scan(source, git, include):
    initial = _git_identity(source)
    if not initial:
        raise Blocked("A local, committed Git checkout is required")
    for path in ("commondir", "objects/info/alternates", "objects/info/http-alternates"):
        metadata = source / ".git" / path
        check_path(metadata)
        if metadata.exists():
            raise Blocked("Shared Git metadata is outside this onboarding scope")
    head = git("rev-parse", "--verify", "HEAD").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise Blocked("Git HEAD is unavailable")
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).strip() or None
    tree = _records(git("ls-tree", "-rz", head), tree=True)
    index = _records(git("ls-files", "--stage", "-z"))
    untracked = sorted(set(git("ls-files", "-z", "--others", "--exclude-standard").strip("\0").split("\0")) - {""})
    ignored = sorted(set(git("ls-files", "-z", "--others", "--ignored", "--exclude-standard",
                             "--directory").strip("\0").split("\0")) - {""})
    if len(set(tree) | set(index) | set(untracked) | set(ignored)) > 4096:
        raise Blocked("Git inventory exceeds onboarding limits")
    for path in [*untracked, *ignored]:
        _relative(path.rstrip("/"))
    remotes = []
    raw = git("config", "--local", "--no-includes", "--get-regexp", r"^remote\..*\.url$", allowed=(0, 1))
    for row in raw.splitlines():
        key, _, value = row.partition(" ")
        try:
            hostname = urlsplit(value).hostname if "://" in value else None
        except ValueError:
            hostname = None
        remotes.append({"key": key, "host": hostname, "url_sha256": sha256(value.encode()).hexdigest()})
    if any(path not in untracked and path not in ignored and not any(
            folder.endswith("/") and path.startswith(folder) for folder in ignored) for path in include):
        raise Blocked("Extra copy paths must be listed untracked or ignored files")
    files, modified, deleted, protected, docs = {}, [], [], [], {}
    staged = sorted(path for path in tree.keys() | index.keys() if tree.get(path) != index.get(path))
    for name in sorted(set(index) | set(include)):
        path = source / _relative(name)
        if (_protected(name) or index.get(name, ("100644",))[0] in {"120000", "160000"}
                or (name in include and not name.casefold().endswith((".example.json", ".sample.json")))):
            if name in include:
                raise Blocked("Extra files require an exact non-sensitive .example.json or .sample.json path")
            protected.append(name)
            continue
        check_path(path)
        if not path.exists():
            if name in include:
                raise Blocked("Selected local example does not exist")
            deleted.append(name)
            continue
        info = fingerprint(path, 8 * 1024 * 1024)
        if name in include:
            if info["bytes"] > 65536:
                raise Blocked("Local example exceeds 64 KiB")
            data = path.read_bytes()
            if (sha256(data).hexdigest() != info["sha256"] or re.search(
                    rb"(?i)password|secret|token|api[_-]?key|credential|private[_ -]?key", data)):
                raise Blocked("Local example contains sensitive markers or changed during review")
            json.loads(data)
        files[name] = info
        if name in index and _blob_identity(path, info, index[name][1]) != index[name][1]:
            modified.append(name)
        if DOCUMENT.fullmatch(path.name) or path.name.casefold() == ".gitignore":
            read = "HASH_ONLY"
            if info["bytes"] <= 65536:
                with path.open("rb") as stream:
                    data = stream.read(65537)
                if sha256(data).hexdigest() != info["sha256"]:
                    raise Blocked("Context document changed during review")
                try:
                    data.decode("utf-8-sig")
                except UnicodeError:
                    read = "ENCODING_NOT_VALIDATED"
                else:
                    read = "UTF8_READ_UNTRUSTED"
            docs[name] = {**info, "read": read}
    if sum(info["bytes"] for info in files.values()) > 128 * 1024 * 1024:
        raise Blocked("Copy exceeds the onboarding byte limit")
    if _git_identity(source) != initial or git("rev-parse", "--verify", "HEAD").strip() != head:
        raise Blocked("Original Git baseline changed during onboarding")
    return {"head": head, "branch": branch, "git": initial, "remotes": remotes, "staged": staged,
            "raw_worktree_changed": modified, "deleted": deleted, "untracked": untracked, "ignored": ignored,
            "protected": sorted(set(protected) | {p for p in untracked + ignored if _protected(p)}),
            "files": files, "documents": docs, "local_examples": include}


def _registered(ledger, project):
    catalog = Catalog(ledger).read()
    row = catalog["projects"].get(project)
    if not row or not row["data"]["local_path"]:
        raise Blocked("Register the owner-selected original project first")
    source = Path(row["data"]["local_path"])
    check_path(source)
    profile = os.environ.get("USERPROFILE")
    if source == source.parent or not source.is_dir() or (profile and Path(profile).is_relative_to(source)):
        raise Blocked("Select a local Git checkout folder")
    return catalog, row, source


def _review(ledger, project, destination, include, git, identity, catalog, row, source):
    if type(include) is not list or len(include) > 16 or any(type(path) is not str for path in include):
        raise Blocked("Specify up to sixteen exact local example paths")
    if len({path.casefold() for path in include}) != len(include):
        raise Blocked("Duplicate local example paths")
    for path in include:
        _relative(path)
    setup = {"project": row["data"], "replica": str(destination), "wait_seconds": 10}
    validate_setup(setup, ledger.state_boundary, allow_file_proposals=True)
    check_path(destination)
    known = [item["data"]["local_path"] for key, item in catalog["projects"].items() if key != project]
    known.extend(item["replica"] for item in catalog["setups"].values())
    if any(destination.is_relative_to(Path(path)) or Path(path).is_relative_to(destination)
           for path in known if path):
        raise Blocked("Destination overlaps a registered original or replica")
    if destination.exists() or not destination.parent.is_dir():
        raise Blocked("Choose a new worktree path within an existing parent")
    evidence = _scan(source, git, sorted(include))
    from .development_environment import detect

    store = InstructionStore(ledger)
    pin = store.active(project)
    private = {"status": "NOT_VALIDATED"}
    if pin and project in catalog["setups"]:
        try:
            store.verify(pin, Path(catalog["setups"][project]["replica"]))
        except Blocked:
            private["status"] = "STALE"
        else:
            private = {"status": "VERIFIED_FOR_PREVIOUS_REPLICA", "sha256": pin["sha256"],
                       "version": pin["version"], "expires": pin["expires"]}
    drafts = store.active_role_drafts(project)
    detected = detect(evidence["files"])
    build_inputs = sorted({name for item in detected.values() for name in item["evidence"]
                           if not name.casefold().endswith((".py", ".cs", ".cpp", ".cxx", ".hpp"))})
    test_sources = sorted(name for name in evidence["files"]
                          if any(part.casefold() in {"test", "tests", "spec", "specs"}
                                 for part in PurePosixPath(name).parts[:-1]))
    return {"contract": "existing-git-onboarding-v1", "status": "CONTEXT_READY",
            "project_id": project, "project_revision": row["revision"], "source": str(source),
            "destination": str(destination), "git_tool_sha256": identity, "baseline": evidence,
            "context": {"goal_sha256": digest(row["data"]["goal"]), "acceptance_sha256": digest(row["data"]["acceptance"]),
                        "requirements": [{k: item[k] for k in ("path", "role", "version", "sha256")}
                                         for item in latest_requirements(catalog, project)],
                        "private_instructions": private, "approved_role_drafts_sha256": digest(drafts) if drafts else None,
                        "defaults": {key: public_instruction(key)["sha256"] for key in ("global", "project")},
                        "detected_environment": detected, "build_inputs": build_inputs, "test_sources": test_sources,
                        "documents": "LOCAL_READ_AND_HASH_UNTRUSTED_CONTENT", "build": "NOT_VALIDATED"},
            "policy": {"copy": "TRACKED_WORKTREE_AND_EXACT_APPROVED_LOCAL_EXAMPLES", "history": "NEW_LOCAL_BASELINE",
                       "original_write": False, "network": "OFF", "development_authority": False}}


def review(runner, project, destination, include):
    with execution_slot() as abandoned:
        runner._idle_diagnostic(abandoned)
        catalog, row, source = _registered(runner.ledger, project)
        with _git(runner.ledger, source) as (git, identity):
            return _review(runner.ledger, project, Path(destination), include, git, identity, catalog, row, source)


def create(runner, project, destination, include, approval):
    from .runtime import require_admission

    ledger = runner.ledger
    destination = Path(destination)
    with execution_slot() as abandoned:
        events, tail = runner._idle_diagnostic(abandoned)
        catalog, row, source = _registered(ledger, project)
        with _git(ledger, source) as (git, identity):
            scope = _review(ledger, project, destination, include, git, identity, catalog, row, source)
        if digest(scope) != approval:
            raise Blocked("Git onboarding preview or approval changed")
        run_id = uuid4().hex
        staging = destination.with_name(".devorchestra-onboard-" + run_id[:12])
        check_path(staging)
        binding = {"run_id": run_id, "project_id": project, "state": "DIAGNOSTIC",
                   "scope_sha256": approval, "staging": str(staging)}
        count, fence = ledger.append(len(events), tail, {**binding, "kind": "onboarding_intent", "scope": scope},
                                     admission=require_admission)
        staging.mkdir()
        baseline = scope["baseline"]
        for name, info in baseline["files"].items():
            original = source / _relative(name)
            copied = staging / _relative(name)
            check_path(original)
            before = original.lstat()
            copied.parent.mkdir(parents=True, exist_ok=True)
            with original.open("rb") as stream, copied.open("xb") as target:
                if not os.path.samestat(before, os.fstat(stream.fileno())):
                    raise Blocked("Original file identity changed during verified copy")
                remaining = info["bytes"]
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise Blocked("Original changed during verified copy")
                    target.write(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise Blocked("Original changed during verified copy")
            if fingerprint(copied) != info:
                raise Blocked("Copied bytes do not match the approved Git baseline")
        with _git(ledger, source, staging) as (git, new_identity):
            if new_identity != identity:
                raise Blocked("Git tool changed before baseline creation")
            metadata = staging / ".devorchestra-git-baseline"
            commands = (f"--git-dir={metadata}", f"--work-tree={staging}", "--literal-pathspecs")
            git("init", "--bare", "--template=", "--initial-branch=devorchestra-baseline", str(metadata), cwd=staging)
            git(*commands, "config", "--local", "core.bare", "false", cwd=staging)
            # Preserve exact approved bytes in the local baseline, including Windows line endings.
            (metadata / "info").mkdir(exist_ok=True)
            (metadata / "info" / "attributes").write_text(
                "* -text -filter -working-tree-encoding -ident\n", encoding="ascii",
            )
            if baseline["files"]:
                paths = staging / ".devorchestra-pathspec"
                with paths.open("xb") as stream:
                    stream.write("".join(name + "\0" for name in baseline["files"]).encode("utf-8"))
                git(*commands, "add", "-f", f"--pathspec-from-file={paths}", "--pathspec-file-nul", cwd=staging)
                paths.unlink()
            git(*commands, "-c", "user.name=DevOrchestra", "-c", "user.email=local@devorchestra.invalid",
                "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m",
                "Isolated baseline from " + baseline["head"], cwd=staging)
            replica_head = git(*commands, "rev-parse", "--verify", "HEAD", cwd=staging).strip()
            indexed = _records(git(*commands, "ls-files", "--stage", "-z", cwd=staging))
            if set(indexed) != set(baseline["files"]) or any(
                    _blob_identity(staging / name, info, indexed[name][1]) != indexed[name][1]
                    for name, info in baseline["files"].items()):
                raise Blocked("Replica Git index does not hold the approved bytes")
            if _scan(source, git, baseline["local_examples"]) != baseline:
                raise Blocked("Original Git, files, or inventory changed during onboarding")
        metadata.rename(staging / ".git")
        image = snapshot(staging)
        if image["files"] != baseline["files"] or not image["git"] or not re.fullmatch("[a-f0-9]{40}", replica_head):
            raise Blocked("Isolated Git replica did not match the approved byte manifest")
        runner._check_diagnostic_fence(count, fence)
        check_path(destination)
        staging.rename(destination)
        saved = Catalog(ledger).save_setup({"project": row["data"], "replica": str(destination), "wait_seconds": 10},
                                           row["revision"], held=True)
        result = {**binding, "kind": "onboarding_result", "status": "REPLICA_READY",
                  "project_revision": saved, "replica": str(destination), "original_head": baseline["head"],
                  "replica_head": replica_head, "replica_sha256": digest(snapshot(destination)),
                  "environment": "NOT_VALIDATED", "development": "NOT_VALIDATED",
                  "original_rechecked": True, "job_active": 0}
        ledger.append(count, fence, result, allow_shutdown=True)
        return result


def status(ledger, project):
    catalog, row, source = _registered(ledger, project)
    results = [e for e in ledger.read() if e.get("project_id") == project
               and e["kind"] in ("onboarding_intent", "onboarding_result")]
    if not results:
        return {"status": "REGISTERED", "context": "NOT_VALIDATED", "environment": "NOT_VALIDATED",
                "development": "NOT_VALIDATED"}
    last = results[-1]
    if last["kind"] == "onboarding_intent":
        return {"status": "BLOCKED", "reason": "Unsettled onboarding; preserve the staging and audit"}
    try:
        replica = Path(last["replica"])
        intent = next(e for e in reversed(results) if e["kind"] == "onboarding_intent"
                      and e["run_id"] == last["run_id"])
        baseline = intent["scope"]["baseline"]
        if (last["project_revision"] != row["revision"] or catalog["setups"][project]["replica"] != str(replica)
                or digest(snapshot(replica)) != last["replica_sha256"] or _git_identity(source) != baseline["git"]
                or any(fingerprint(source / name) != info for name, info in baseline["files"].items())):
            raise Blocked("Catalog or replica changed after onboarding")
    except (OSError, Blocked, KeyError, StopIteration):
        return {"status": "BLOCKED", "reason": "Catalog or replica baseline changed; review again"}
    from .replica_readiness import project_status

    result = {key: last[key] for key in ("status", "replica", "original_head", "replica_head",
                                        "environment", "development", "original_rechecked")}
    result["context_documents"] = list(baseline["documents"])
    return {**result, **project_status(ledger, project, last)}
