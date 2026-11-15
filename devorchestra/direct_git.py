"""Trusted Git operations at settled registered development boundaries."""
import json
from hashlib import sha256
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from .lease import Blocked
from .ledger import check_path, digest
from .mxc_host_probe import _observe
from .project_onboarding import _git_tools, _protected, _relative
from .replica_evidence import fingerprint, snapshot


MODE = "DIRECT_GIT_WORKTREE"
OID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
SECRET = re.compile(
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|"
    rb"github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|"
    rb"(?i:bearer )[A-Za-z0-9_.-]{24,}"
)


def _command(root, scratch, check, *args, codes=(0,), raw=False):
    check()
    git, _ = _git_tools()
    # Git may use the owner's existing credential helper. Never echo command output on failure.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_LAZY_FETCH="1", GIT_LITERAL_PATHSPECS="1")
    with TemporaryDirectory(prefix="direct-git-", dir=scratch) as logs:
        result = _observe(git, ["--no-replace-objects", "-c", "core.hooksPath=NUL",
                               "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
                               "-c", "maintenance.auto=false", "-c", "gc.auto=0",
                               "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                               "-c", "http.followRedirects=false",
                               *args], root, Path(logs), env, seconds=60, check=check,
                          memory=512 * 1024**2, output_limit=1024**2, encoding="utf-8")
        data = (Path(logs) / "probe.stdout").read_bytes() if raw else None
    check()
    if result["job_active"] or result["exitCode"] not in codes:
        raise Blocked(f"Git {args[0]} failed (exit {result['exitCode']})")
    return data if raw else result["stdout"]


def _head(git):
    sha = git("rev-parse", "--verify", "--quiet", "HEAD", codes=(0, 1)).strip()
    if sha and not re.fullmatch(OID, sha):
        raise Blocked("Invalid local Git HEAD")
    return sha or None


def _remote_head(git, url, ref):
    row = git("ls-remote", "--refs", url, ref).strip()
    if not row:
        return None
    match = re.fullmatch(rf"({OID})\t{re.escape(ref)}", row)
    if not match:
        raise Blocked("Invalid remote Git branch response")
    return match[1]


def binding(root, scratch, check):
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    check_path(root / ".git")
    if not (root / ".git").is_dir():
        raise Blocked("Direct Git requires a standalone Git checkout")
    local = git("config", "--local", "--no-includes", "--name-only", "--list").splitlines()
    if any(re.match(r"(?i)(include|url\.|alias\.|filter\.(?!lfs\.)|credential\.|core\.(worktree|sshcommand)|"
                    r"remote\..*\.(receivepack|uploadpack|proxy)|http\.)", key) for key in local):
        raise Blocked("Unsupported local Git configuration for direct publication")
    if git("config", "--get-regexp", r"^url\.", codes=(0, 1)):
        raise Blocked("Git URL rewriting prevents binding the approved destination")
    ref = git("symbolic-ref", "HEAD").strip()
    if not re.fullmatch(r"refs/heads/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+", ref):
        raise Blocked("Direct Git requires a checked-out branch")
    fetch = git("remote", "get-url", "--all", "origin").splitlines()
    push = git("remote", "get-url", "--push", "--all", "origin").splitlines()
    if (fetch != push or len(push) != 1
            or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", push[0])):
        raise Blocked("Direct Git requires one matching GitHub HTTPS fetch and push URL")
    return {"ref": ref, "url": push[0], "head": _head(git)}


def admit(root, scratch, check, branch):
    approved = binding(root, scratch, check)
    if approved["ref"] != f"refs/heads/{branch}":
        raise Blocked("Check out the approved direct Git branch first")
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    if git("status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise Blocked("Direct Git requires a clean working tree and index")
    if _remote_head(git, approved["url"], approved["ref"]) != approved["head"]:
        raise Blocked("Direct Git requires matching local and remote HEADs")
    return approved


def _check_worktree(git, paths):
    if git("diff", "--cached", "--name-only", "-z"):
        raise Blocked("Existing staged changes prevent automatic commit")
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    dirty = {entry[3:] for entry in status.split("\0") if entry}
    if dirty != set(paths):
        raise Blocked("Working tree changes differ from the approved publication paths")
    return dirty


def resume_candidate(root, scratch, check, events, start, end, cycle, paths):
    """Reconcile the frozen start HEAD with completed publications of earlier cycles."""
    scope = start["scope"]
    approved = scope["direct_git"]
    ref, url, expected = (approved[key] for key in ("ref", "url", "head"))
    project = scope["project_id"]
    if pending(events, project):
        raise Blocked("Resolve the pending direct Git publication before resuming")
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    receipts = []
    previous_cycle = 0
    for row in events[events.index(start) + 1:events.index(end)]:
        if (row.get("run_id") != start["run_id"] or row.get("project_id") != project
                or row["kind"] not in {"direct_git_intent", "direct_git_commit", "direct_git_pushed"}):
            continue
        receipts.append(row)
        if row["kind"] != "direct_git_pushed":
            continue
        if len(receipts) != 3:
            raise Blocked("Incomplete direct Git publication receipts")
        intent, commit, pushed = receipts
        number, sha = commit.get("cycle"), commit.get("commit")
        if (intent["kind"] != "direct_git_intent" or commit["kind"] != "direct_git_commit"
                or type(number) is not int or not previous_cycle < number < cycle
                or not isinstance(sha, str) or not re.fullmatch(OID, sha)
                or any(item.get("approval_sha256") != start["approval_sha256"]
                       or item.get("origin") != url or item.get("before") != expected
                       for item in (intent, commit))
                or intent.get("cycle") != number or any(item.get("branch") != ref for item in receipts)
                or (pushed.get("approval_sha256", start["approval_sha256"]) != start["approval_sha256"])
                or pushed.get("cycle", number) != number
                or pushed.get("commit") != sha or pushed.get("remote_head") != sha
                or git("rev-list", "--parents", "-n", "1", sha).split() != [sha, *([expected] if expected else [])]):
            raise Blocked("Direct Git publication receipts do not match the approved history")
        expected, previous_cycle = sha, number
        receipts.clear()
    current = {**approved, "head": expected}
    if receipts or binding(root, scratch, check) != current or _remote_head(git, url, ref) != expected:
        raise Blocked("Direct Git HEAD or origin differs from the checkpoint publication history")
    _check_worktree(git, paths)
    return current


def partial_candidate(root, scratch, check, events, start, end, cycle, image, staged, *,
                      validate_paths, tool_log=None, log_limit=0, transcript_sha=None):
    """Reconstruct the clean dispatch image and corroborate each edit with owned staging."""
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    entries = status.rstrip("\0").split("\0")
    if any(not entry.startswith((" M ", "?? ")) for entry in entries):
        raise Blocked("Partial recovery requires unstaged modifications or attested new files")
    paths = sorted(entry[3:] for entry in entries)
    added = {entry[3:] for entry in entries if entry.startswith("?? ")}
    validate_paths(paths)
    current = resume_candidate(root, scratch, check, events, start, end, cycle, paths)
    before = {**image, "files": dict(image["files"])}
    old_text = {}
    for name in paths:
        _safe_file(root, name, image["files"][name])
        if name in added:
            del before["files"][name]
            continue
        data = git("show", f"HEAD:{name}", raw=True)
        before["files"][name] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
        old_text[name] = data.decode("utf-8")
    if digest(before) != end["replica_sha256"]:
        raise Blocked("Clean Git baseline does not match the frozen dispatch snapshot")
    new_evidence = None
    if added:
        from .recovery_evidence import attest_new_files, read_commands

        if not transcript_sha or tool_log is None or not tool_log.is_file():
            raise Blocked("New files require a transcript sealed in the Developer role receipt")
        baseline_names = {name.casefold() for name in before["files"]}
        if any(name.casefold() in baseline_names for name in added):
            raise Blocked("New file collides with a baseline path")
        new_evidence = attest_new_files(root, added, read_commands(tool_log, log_limit, transcript_sha))
    check_path(staged)
    if not staged.is_dir():
        raise Blocked("Partial candidate has no retained owned staging evidence")
    folders = [staged]
    for folder in folders:
        check_path(folder)
        if all((folder / name).is_file() and fingerprint(folder / name, long_local=True) == image["files"][name]
               for name in paths):
            for name in added:
                staged_path = Path("\\\\?\\" + str(folder / name))
                with staged_path.open("rb") as stream:
                    retained = stream.read(image["files"][name]["bytes"] + 1)
                if retained != (root / name).read_bytes():
                    raise Blocked("Retained new file bytes changed during review")
            if snapshot(root) != image:
                raise Blocked("Partial candidate changed during review")
            location = folder.relative_to(staged).as_posix()
            if new_evidence is not None:
                return current, paths, before["files"], old_text, {
                    "folder": location, **new_evidence, "modified": sorted(set(paths) - added),
                    "retained_files_sha256": digest({name: image["files"][name] for name in paths}), "edits": []}
            return current, paths, before["files"], old_text, location
        if len(folder.relative_to(staged).parts) < 2:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if len(folders) >= 128:
                            raise Blocked("Partial candidate staging exceeds the search bound")
                        folders.append(Path(entry.path))
    if not added and tool_log is not None and tool_log.is_file():
        from .recovery_evidence import read_commands, reconstruct

        records = read_commands(tool_log, log_limit, transcript_sha)
        for folder in folders:
            evidence = reconstruct(folder, root, paths, image["files"], records)
            if evidence is not None:
                check()
                if snapshot(root) != image:
                    raise Blocked("Partial candidate changed during review")
                return current, paths, before["files"], old_text, {
                    "folder": folder.relative_to(staged).as_posix(), **evidence}
    raise Blocked("Edited files do not match a single retained owned staging tree")


def _migration_plan(runner, events, fence, project, branch, excluded, redact):
    from .replica_readiness import baseline, original_matches
    from .registered_cycle import history

    source, replica, frozen, onboarding = baseline(runner.ledger, project)
    rows = history(runner.ledger, project, current_replica=True)
    image = snapshot(replica)
    if not rows or not original_matches(source, frozen):
        raise Blocked("Original checkout is not at its registered migration baseline")
    end = rows[-1]
    if (end["kind"] != "native_inspection_result" or end.get("outcome") != "CHECKPOINT_SAVED"
            or end.get("owned_tree") != "DRAINED" or end.get("usage_complete") is not True
            or end.get("replica_sha256") != digest(image)
            or rows[0]["scope"].get("replica") != str(replica)
            or rows[0]["scope"].get("onboarding_sha256") != digest(onboarding)
            or pending(events, project)):
        raise Blocked("A matching, settled replica checkpoint is required")
    if (type(excluded) is not tuple or type(redact) is not tuple
            or len(set((*excluded, *redact))) != len(excluded) + len(redact)
            or not set((*excluded, *redact)) <= set(end["changed"])):
        raise Blocked("Migration exclusions do not match checkpoint paths")
    paths = sorted(set(end["changed"]) - set(excluded))
    originals = snapshot(source)
    for name in paths:
        if name not in image["files"] or name in originals["files"]:
            raise Blocked(f"Migration would replace an existing file or apply a deletion: {name}")
        if name in redact and _relative(name).suffix.lower() != ".md":
            raise Blocked("Only reviewed Markdown paths can be redacted")
        _safe_file(replica, name, image["files"][name], redact=name in redact)
    approved = admit(source, runner.ledger.local_directory("temp"),
                     lambda: runner._check_diagnostic_fence(len(events), fence), branch)
    return {"project_id": project, "source": str(source), "replica": str(replica), "git": approved,
            "source_sha256": digest(originals), "replica_sha256": digest(image), "checkpoint_sha256": digest(end),
            "parent_run_id": end["run_id"], "cycle": end["cycle_count"], "paths": paths,
            "excluded": list(excluded), "redact": list(redact), "ledger_head": fence}, originals, image


def review_migration(runner, project, branch, excluded=(), *, redact=()):
    from .lease import execution_slot

    with execution_slot() as abandoned:
        events, fence = runner._idle_diagnostic(abandoned)
        return _migration_plan(runner, events, fence, project, branch, excluded, redact)[0]


def migrate(runner, project, branch, excluded, approval, *, verify, redact=()):
    """Import only new, reviewed paths and retain a private recovery snapshot."""
    from .lease import execution_slot
    from .project_onboarding import _git_scratch

    with execution_slot() as abandoned:
        events, fence = runner._idle_diagnostic(abandoned)
        scope, original, candidate = _migration_plan(runner, events, fence, project, branch, excluded, redact)
        if digest(scope) != approval:
            raise Blocked("Direct Git migration approval changed")
        count = len(events)
        run = uuid4().hex
        root, replica = Path(scope["source"]), Path(scope["replica"])
        archive = runner.ledger.local_directory("recovery", create=True) / f"direct-git-migration-{run}"
        check_path(archive)
        archive.mkdir()
        (archive / "original.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
        (archive / "scope.json").write_text(json.dumps(scope, indent=2), encoding="utf-8")

        def check():
            runner._check_diagnostic_fence(count, fence)

        def record(kind, **facts):
            nonlocal count, fence
            count, fence = runner.ledger.append(count, fence, {
                "kind": kind, "project_id": project, "run_id": run, "approval_sha256": approval, **facts})

        record("direct_git_migration_intent", scope=scope, recovery=archive.name)
        imported = {}
        with _git_scratch(runner.ledger) as scratch:
            staged = scratch / "candidate"
            staged.mkdir()
            for name in scope["paths"]:
                check()
                data = _safe_file(replica, name, candidate["files"][name], redact=name in redact)
                imported[name] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
                _write_new(staged, name, data)
            check()
            validation = verify(staged, scratch, check)
            if snapshot(replica) != candidate or snapshot(root) != original:
                raise Blocked("Original or replica changed during migration validation")
            for name in scope["paths"]:
                check()
                data = _safe_file(staged, name, imported[name])
                _write_new(root, name, data)
            image = snapshot(root)
            if image["files"] != {**original["files"], **imported}:
                raise Blocked("Imported worktree differs from the reviewed migration")
            record("direct_git_migration_checked", candidate_sha256=digest(image), validation=validation)
            commit = publish(root, scratch, check, scope["git"], image, scope["paths"],
                             f"{project}: import cycle {scope['cycle']} development snapshot\n\n"
                             f"Ledger Seq {len(events)}; CHECKPOINT_SAVED; functional validation pending", record)
        record("direct_git_migrated", scope=scope, commit=commit, source_sha256=digest(snapshot(root)))
        return commit


def _write_new(root, name, data):
    path = root / _relative(name)
    check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    check_path(path)
    with path.open("xb") as stream:
        stream.write(data)


PRIVATE_PATH = re.compile(rb"(?i)[a-z]:[/\\]users[/\\]|\.ai-local[/\\]")


def _safe_file(root, name, expected, *, redact=False):
    relative = _relative(name)
    if (_protected(name) or relative.name.casefold() in {"moduleanalysiscache", ".gitattributes", ".gitmodules"}
            or relative.suffix.casefold() in {".lnk", ".exe", ".pck", ".dll", ".zip"}):
        raise Blocked(f"Protected Git publication path: {name}")
    path = root / relative
    if fingerprint(path) != expected:
        raise Blocked(f"Git publication file changed: {name}")
    data = path.read_bytes()
    if redact:
        data = b"".join(line for line in data.splitlines(keepends=True) if not PRIVATE_PATH.search(line))
    if fingerprint(path) != expected or SECRET.search(data) or (relative.suffix.lower() == ".md"
                               and PRIVATE_PATH.search(data)):
        raise Blocked(f"Private material in Git publication path: {name}")
    return data


def publish(root, scratch, check, approved, image, paths, message, record, *, report=None):
    """Commit only the attested paths, then push the exact commit without force."""
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    if binding(root, scratch, check) != approved or snapshot(root) != image:
        raise Blocked("Approved Git checkout or candidate changed")
    ref, url, head = (approved[key] for key in ("ref", "url", "head"))
    remote = _remote_head(git, url, ref)
    if remote != head:
        raise Blocked("Remote Git HEAD differs from the approved local HEAD")
    dirty = _check_worktree(git, paths)
    if not dirty:
        return head
    if any(Path(name).name.casefold() in {".gitattributes", ".gitmodules"} for name in image["files"]):
        raise Blocked("Git attribute and submodule execution are outside the direct publication contract")
    for name in sorted(dirty):
        if name in image["files"]:
            _safe_file(root, name, image["files"][name])
        elif _protected(name):
            raise Blocked(f"Protected Git deletion: {name}")
    identity = {"before": head, "branch": ref, "origin": url}
    facts = {**identity, "candidate_sha256": digest(image), "paths": sorted(dirty),
             "files_sha256": digest(image["files"])}
    if report is None:
        record("direct_git_intent", **facts)
        commit = _commit_publication(git, root, head, image["files"], dirty, message)
    else:
        commit = _commit_with_record(git, root, scratch, image["files"], dirty, message, record, facts, report)
    record("direct_git_commit", **identity, commit=commit)
    push_pending(root, scratch, check, approved, commit, record)
    return commit


def _commit_with_record(git, root, scratch, files, dirty, message, record, facts, report):
    from .development_record import patch, reserved

    if any(reserved(name) for name in dirty):
        raise Blocked("Candidate overlaps Controller development record paths")
    if not report[0].endswith(f" {facts['branch']} -->\n"):
        raise Blocked("Development record does not match the approved branch")
    outputs, payload = patch(root, files, report)
    if any(SECRET.search(data) or PRIVATE_PATH.search(data) for data in outputs.values()):
        raise Blocked("Development record contains protected material")
    names = sorted(outputs)
    if git("check-ignore", "--no-index", "--", *names, codes=(0, 1)):
        raise Blocked("Git ignore policy excludes a Controller development record")
    manifest = _record_manifest(outputs)
    combined = {**files, **manifest}
    paths = dirty | outputs.keys()
    head = facts["before"]
    with TemporaryDirectory(prefix="publication-record-", dir=scratch) as folder:
        delta = Path(folder) / "record.patch"
        delta.write_bytes(payload)
        apply = ("apply", "--index", "--whitespace=nowarn")
        git(*apply, "--check", str(delta))
        details = {**facts, "paths": sorted(paths), "files_sha256": digest(combined),
                   "development_record": report[3], "record_files_sha256": digest(manifest)}
        record("direct_git_intent", **details)
        try:
            git(*apply, str(delta))
            if snapshot(root)["files"] != combined:
                raise Blocked("Generated development record did not match the prepared bytes")
            return _commit_publication(git, root, head, combined, paths, message, ref=facts["branch"],
                                       prepared=lambda tree, commit: record("direct_git_prepared", **details,
                                                                            tree=tree, commit=commit))
        except (Blocked, OSError, ValueError, KeyboardInterrupt):
            if _head(git) == head and git("symbolic-ref", "HEAD").strip() == facts["branch"]:
                current = snapshot(root)["files"]
                if current == combined:
                    git(*apply, "--reverse", str(delta))
                if snapshot(root)["files"] != files:
                    raise Blocked("Development record rollback could not be verified")
                staged = set(filter(None, git("diff", "--cached", "--name-only", "-z").split("\0")))
                if not staged <= dirty:
                    raise Blocked("Publication rollback found unexpected staged paths")
                for name in sorted(staged):
                    if head:
                        git("restore", "--staged", "--source=HEAD", "--", name)
                    else:
                        git("update-index", "--force-remove", "--", name)
                _check_worktree(git, sorted(dirty))
                record("direct_git_aborted", before=head, branch=facts["branch"], origin=facts["origin"])
            raise


def _record_manifest(outputs):
    return {name: {"sha256": sha256(data).hexdigest(), "bytes": len(data)} for name, data in outputs.items()}


def _commit_publication(git, root, head, files, dirty, message, *, ref=None, prepared=None):
    # Batches keep Windows command lines bounded and preserve literal path semantics.
    ordered = sorted(dirty)
    for start in range(0, len(ordered), 16):
        git("add", "-A", "--", *ordered[start:start + 16])
    if set(git("diff", "--cached", "--name-only", "-z").rstrip("\0").split("\0")) != dirty:
        raise Blocked("Git staged paths changed")
    if git("diff", "--name-only", "-z") or snapshot(root)["files"] != files or _head(git) != head:
        raise Blocked("Working tree changed during Git staging")
    for name in sorted(dirty & files.keys()):
        expected = git("hash-object", "--no-filters", "--", name).strip()
        if git("rev-parse", f":{name}").strip() != expected:
            raise Blocked(f"Git filters changed staged bytes: {name}")
    tree = git("write-tree").strip()
    if not re.fullmatch(OID, tree):
        raise Blocked("Invalid staged Git tree")
    if prepared:
        # Journal the exact object before moving the ref. A later retry must find this object, not just a similar tree.
        commit = git("commit-tree", tree, *(["-p", head] if head else []), "--no-gpg-sign", "-m", message).strip()
        if not re.fullmatch(OID, commit):
            raise Blocked("Invalid prepared Git commit")
        prepared(tree, commit)
        if git("symbolic-ref", "HEAD").strip() != ref:
            raise Blocked("Approved Git branch changed before publication")
        git("update-ref", "-m", "DevOrchestra cycle publication", ref, commit, head or "0" * len(tree))
    else:
        git("commit", "--no-gpg-sign", "-m", message)
        commit = _head(git)
    if (commit == head or _head(git) != commit or git("rev-list", "--parents", "-n", "1", commit).split() !=
            [commit, *([head] if head else [])] or git("status", "--porcelain=v1", "-z")
            or git("rev-parse", "HEAD^{tree}").strip() != tree):
        raise Blocked("Git commit could not be verified")
    return commit


def push_pending(root, scratch, check, approved, commit, record):
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    ref, url, before = (approved[key] for key in ("ref", "url", "head"))
    if binding(root, scratch, check) != {**approved, "head": commit}:
        raise Blocked("Pending Git commit or origin changed")
    try:
        remote = _remote_head(git, url, ref)
        if remote != commit:
            if remote != before:
                raise Blocked("Remote branch changed while push was pending")
            git("push", "--porcelain", url, f"{commit}:{ref}")
        if _remote_head(git, url, ref) != commit:
            raise Blocked("Remote Git HEAD verification failed")
    except (Blocked, OSError) as error:
        record("direct_git_push_pending", branch=ref, commit=commit, reason=str(error))
        raise
    record("direct_git_pushed", branch=ref, commit=commit, remote_head=commit)


def pending(events, project):
    checkpoint = None
    for event in events:
        if event.get("project_id") != project:
            continue
        if event["kind"] in {"direct_git_intent", "direct_git_commit"}:
            checkpoint = event
        elif event["kind"] == "direct_git_pushed":
            checkpoint = None
        elif event["kind"] == "direct_git_aborted" and checkpoint and checkpoint["kind"] == "direct_git_intent":
            if all(event.get(key) == checkpoint.get(key) for key in ("run_id", "cycle", "branch", "origin", "before")):
                checkpoint = None
    return checkpoint


def retry(runner, project):
    """Retry only an already recorded local commit under the original branch grant."""
    from .lease import execution_slot
    from .replica_readiness import baseline

    with execution_slot() as abandoned:
        events, fence = runner._idle_diagnostic(abandoned)
        checkpoint = pending(events, project)
        if not checkpoint:
            raise Blocked("No recorded local commit is eligible for a push retry")
        source, _, _, _ = baseline(runner.ledger, project)
        identity = ("project_id", "run_id", "cycle", "approval_sha256", "origin", "branch", "before")

        def matching(kind, rows=events):
            return next((row for row in reversed(rows) if row["kind"] == kind
                         and all(row.get(key) == checkpoint.get(key) for key in identity)), None)

        intent = matching("direct_git_intent")
        if (not intent
                or digest(snapshot(source)["files"]) != intent["files_sha256"]):
            raise Blocked("Pending Git candidate evidence changed")
        count = len(events)

        def check():
            runner._check_diagnostic_fence(count, fence)

        def record(kind, **facts):
            nonlocal count, fence
            count, fence = runner.ledger.append(count, fence, {"kind": kind, "project_id": project,
                "run_id": checkpoint["run_id"], **{key: checkpoint[key] for key in ("approval_sha256", "cycle", "criterion", "verdict")
                                                   if key in checkpoint}, **facts})

        scratch = runner.ledger.local_directory("temp")
        approved = {"ref": intent["branch"], "url": intent["origin"], "head": intent["before"]}
        if checkpoint["kind"] == "direct_git_commit":
            commit = checkpoint["commit"]
        else:
            prepared = matching("direct_git_prepared", events[events.index(intent) + 1:])
            if (not prepared or not intent.get("development_record") or any(prepared.get(key) != intent.get(key)
                    for key in ("candidate_sha256", "paths", "files_sha256", "record_files_sha256", "development_record"))):
                raise Blocked("No prepared development record is eligible for a push retry")
            git = lambda *args: _command(source, scratch, check, *args)
            commit = binding(source, scratch, check)["head"]
            if (not commit or commit != prepared.get("commit") or commit == approved["head"]
                    or git("status", "--porcelain", "-z")
                    or git("rev-parse", "HEAD^{tree}").strip() != prepared.get("tree")
                    or git("rev-list", "--parents", "-n", "1", commit).split() !=
                    [commit, *([approved["head"]] if approved["head"] else [])]):
                raise Blocked("Prepared Git commit could not be verified")
            if binding(source, scratch, check) != {**approved, "head": commit}:
                raise Blocked("Prepared Git destination changed")
            record("direct_git_commit", before=approved["head"], origin=approved["url"], branch=approved["ref"], commit=commit)
        push_pending(source, scratch, check, approved, commit, record)
        migration = next((row for row in reversed(events) if row["kind"] == "direct_git_migration_intent"
                          and row.get("run_id") == checkpoint["run_id"]), None)
        if migration:
            record("direct_git_migrated", scope=migration["scope"], commit=commit,
                   source_sha256=digest(snapshot(source)))
        return commit
