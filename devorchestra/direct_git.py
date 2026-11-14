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


def _command(root, scratch, check, *args, codes=(0,)):
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
    check()
    if result["job_active"] or result["exitCode"] not in codes:
        raise Blocked(f"Git {args[0]} failed (exit {result['exitCode']})")
    return result["stdout"]


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


def publish(root, scratch, check, approved, image, paths, message, record):
    """Commit only the attested paths, then push the exact commit without force."""
    git = lambda *args, **kw: _command(root, scratch, check, *args, **kw)
    if binding(root, scratch, check) != approved or snapshot(root) != image:
        raise Blocked("Approved Git checkout or candidate changed")
    ref, url, head = (approved[key] for key in ("ref", "url", "head"))
    remote = _remote_head(git, url, ref)
    if remote != head:
        raise Blocked("Remote Git HEAD differs from the approved local HEAD")
    if git("diff", "--cached", "--name-only", "-z"):
        raise Blocked("Existing staged changes prevent automatic commit")
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    dirty = {entry[3:] for entry in status.split("\0") if entry}
    if dirty != set(paths):
        raise Blocked("Working tree changes differ from the approved publication paths")
    if not dirty:
        return head
    if any(Path(name).name.casefold() in {".gitattributes", ".gitmodules"} for name in image["files"]):
        raise Blocked("Git attribute and submodule execution are outside the direct publication contract")
    for name in sorted(dirty):
        if name in image["files"]:
            _safe_file(root, name, image["files"][name])
        elif _protected(name):
            raise Blocked(f"Protected Git deletion: {name}")
    record("direct_git_intent", before=head, branch=ref, origin=url, candidate_sha256=digest(image),
           paths=sorted(dirty), files_sha256=digest(image["files"]))
    # Batches keep Windows command lines bounded and preserve literal path semantics.
    ordered = sorted(dirty)
    for start in range(0, len(ordered), 16):
        git("add", "-A", "--", *ordered[start:start + 16])
    if set(git("diff", "--cached", "--name-only", "-z").rstrip("\0").split("\0")) != dirty:
        raise Blocked("Git staged paths changed")
    if git("diff", "--name-only", "-z") or snapshot(root)["files"] != image["files"] or _head(git) != head:
        raise Blocked("Working tree changed during Git staging")
    for name in sorted(dirty & image["files"].keys()):
        expected = git("hash-object", "--no-filters", "--", name).strip()
        if git("rev-parse", f":{name}").strip() != expected:
            raise Blocked(f"Git filters changed staged bytes: {name}")
    tree = git("write-tree").strip()
    if not re.fullmatch(OID, tree):
        raise Blocked("Invalid staged Git tree")
    git("commit", "--no-gpg-sign", "-m", message)
    commit = _head(git)
    if (commit == head or git("rev-list", "--parents", "-n", "1", commit).split() !=
            [commit, *([head] if head else [])] or git("status", "--porcelain=v1", "-z")
            or git("rev-parse", "HEAD^{tree}").strip() != tree):
        raise Blocked("Git commit could not be verified")
    record("direct_git_commit", before=head, commit=commit, branch=ref, origin=url)
    push_pending(root, scratch, check, approved, commit, record)
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
    return checkpoint


def retry(runner, project):
    """Retry only an already recorded local commit under the original branch grant."""
    from .lease import execution_slot
    from .replica_readiness import baseline

    with execution_slot() as abandoned:
        events, fence = runner._idle_diagnostic(abandoned)
        commit = pending(events, project)
        if not commit or commit["kind"] != "direct_git_commit":
            raise Blocked("No recorded local commit is eligible for a push retry")
        source, _, _, _ = baseline(runner.ledger, project)
        intent = next((row for row in reversed(events) if row["kind"] == "direct_git_intent"
                       and row.get("project_id") == project and row.get("run_id") == commit.get("run_id")), None)
        if (not intent or any(intent[key] != commit[key] for key in ("origin", "branch", "before"))
                or digest(snapshot(source)["files"]) != intent["files_sha256"]):
            raise Blocked("Pending Git candidate evidence changed")
        count = len(events)

        def check():
            runner._check_diagnostic_fence(count, fence)

        def record(kind, **facts):
            nonlocal count, fence
            count, fence = runner.ledger.append(count, fence, {
                "kind": kind, "project_id": project, "run_id": commit["run_id"], **facts})

        approved = {"ref": commit["branch"], "url": commit["origin"], "head": commit["before"]}
        push_pending(source, runner.ledger.local_directory("temp"), check, approved, commit["commit"], record)
        migration = next((row for row in reversed(events) if row["kind"] == "direct_git_migration_intent"
                          and row.get("run_id") == commit["run_id"]), None)
        if migration:
            record("direct_git_migrated", scope=migration["scope"], commit=commit["commit"],
                   source_sha256=digest(snapshot(source)))
        return commit["commit"]
