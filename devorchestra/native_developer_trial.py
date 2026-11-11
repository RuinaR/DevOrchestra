"""Single approved Codex CLI Developer turn against a generated Git fixture."""
import json
from pathlib import Path
import re
from time import time
import tomllib

from .job_probe import CODEX_DEVELOPER_PROCESS_LIMIT
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import OwnedMxcStopped, _args, _observe
from .mxc_launcher import _known_folder
from .native_command_probe import (
    ARCHIVE_SHA256, RELEASE_SHA256, SOURCE_NAMES, SUBTOTAL, TOTAL,
    _fixture_git_binary, _prepare_git_fixture, _release_fixture_git_objects, _stage_release, _staging,
    _verify_code_mode_host, CODE_MODE_HOST_SHA256,
)
from .native_manager import DISABLED_FEATURES
from .policy import live_preflight
from .replica_evidence import snapshot


PROMPT = (
    "Modify both subtotal.cmd and total.cmd in this synthetic Git replica. "
    "Make subtotal multiply its numeric argument by 3 and make total add a fixed fee of 2. "
    "Run the actual test using cmd.exe /d /q /c \"call total.cmd 3\" and verify output 11. "
    "Do not modify any other paths, contact a service, or make a Git commit."
)
SECONDS = 120
TOKEN_ALLOWANCE = 12000
PROFILE = "developer-trial"
DEFAULT_MODEL = "gpt-6.1-sol"


def trial_flags(work, *, writable=True, code_mode=False, tool_root=None, shell_tools=True):
    """Keep every temporary override on the owned process command line."""
    rules = ((":root", "deny"), (":minimal", "read"), (str(work), "write" if writable else "read"),
             (str(work / ".git"), "read"))
    if tool_root is not None:
        rules += ((str(tool_root), "read"),)
    filesystem = "{ " + ", ".join(f"{json.dumps(path)} = {json.dumps(access)}" for path, access in rules) + " }"
    enabled = {"code_mode", "code_mode_host"} if code_mode else set()
    if shell_tools:
        enabled.add("shell_tool")
    values = [
        'windows.sandbox="mxc"', 'approval_policy="never"', f'default_permissions="{PROFILE}"',
        f"permissions.{PROFILE}.filesystem={filesystem}", f"permissions.{PROFILE}.network.enabled=false",
        'model_provider="openai"', 'forced_login_method="chatgpt"', 'cli_auth_credentials_store="file"',
        'web_search="disabled"', "analytics.enabled=false", "mcp_servers={}",
        "tools.update_plan.enabled=false", "tools.experimental_request_user_input.enabled=false",
        *(f"features.{feature}={'true' if feature in enabled else 'false'}" for feature in DISABLED_FEATURES),
    ]
    return [arg for value in values for arg in ("-c", value)]


def code_mode_flags_sha256():
    return digest(trial_flags(Path("APPROVED_REPLICA_ROOT"), code_mode=True))


def _scope(archive, home, request, expires, ledger_head, model, role_tokens, session_tokens, diagnostic):
    if not re.fullmatch(r"[a-f0-9]{32}", request) or not time() < expires <= time() + 900:
        raise Blocked("Use a new 32-hex request ID and an expiry within 15 minutes")
    if not all(type(value) is int and 1 <= value <= 100000 for value in (role_tokens, session_tokens)):
        raise Blocked("Role and session token allowances must be between 1 and 100000")
    return {
        "request_id": request, "expires": expires, "archive": str(archive), "archive_sha256": ARCHIVE_SHA256,
        "binary_sha256": RELEASE_SHA256, "auth_home": str(home), "auth": "CHATGPT_ONLY",
        "code_mode_host_sha256": CODE_MODE_HOST_SHA256, "code_mode_transport": "LOCAL_COMPANION",
        "backend": "CODEX_EXEC", "model": model, "mode": "general",
        "temporary_exception": "GENERAL_ADMISSION_UNVERIFIED_FOR_THIS_SYNTHETIC_TURN_ONLY",
        "sandbox": "MXC_REPLICA_WRITE_GIT_READ_COMMAND_NETWORK_OFF", "approval_policy": "never",
        "files": list(SOURCE_NAMES), "prompt_sha256": digest(PROMPT),
        "file_scope_verification": "REJECT_RESULT_IF_ANY_OTHER_REPLICA_PATH_CHANGED",
        "fixture_sha256": digest([SUBTOTAL, TOTAL, 2, 3]),
        "flags_sha256": code_mode_flags_sha256(), "ledger_head": ledger_head,
        "local_command_diagnostic_request_id": diagnostic["request_id"],
        "timeout_seconds": SECONDS, "role_token_allowance": role_tokens, "session_token_allowance": session_tokens,
        "usage_limit": "CHECK_PROVIDER_REPORTED_USAGE_AFTER_TURN", "job_process_limit": CODEX_DEVELOPER_PROCESS_LIMIT,
    }


def _cli_evidence(output, *, command_pattern=r"\bcall\s+total\.cmd\s+3\b", expected="11", retain_message=False):
    """Retain only bounded event classes, command hashes and reported counts."""
    thread = started = terminal = message = False
    usage, tests, commands, final_message = None, [], [], None
    for line in output.splitlines():
        row = json.loads(line)
        if type(row) is not dict or terminal:
            raise ValueError("Unexpected event after terminal event")
        kind = row.get("type")
        if kind == "thread.started":
            if thread or not isinstance(row.get("thread_id"), str):
                raise ValueError("Unexpected thread")
            thread = True
        elif kind == "turn.started":
            if not thread or started:
                raise ValueError("Unexpected turn")
            started = True
        elif kind == "item.completed" and started:
            item = row["item"]
            if type(item) is not dict or item.get("type") not in (
                "agent_message", "reasoning", "command_execution", "file_change", "todo_list",
            ):
                raise ValueError("Unexpected completed tool item")
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str) and item["text"]:
                message = True
                final_message = item["text"]
            if item.get("type") == "command_execution":
                command = item.get("command")
                observed = item.get("aggregated_output")
                if retain_message and isinstance(command, str) and len(commands) < 64:
                    code = item.get("exit_code")
                    commands.append({"sha256": digest(command), "exit_code": code if type(code) is int else None})
                if (isinstance(command, str) and item.get("exit_code") == 0
                        and re.search(command_pattern, command, re.I)
                        and isinstance(observed, str) and expected in observed.splitlines()):
                    tests.append(digest(command))
        elif kind in ("turn.completed", "turn.failed", "error"):
            if kind != "turn.completed" or not started:
                raise ValueError("Turn did not complete")
            terminal = True
            reported = row.get("usage")
            if type(reported) is dict and all(type(reported.get(key)) is int and reported[key] >= 0
                                             for key in ("input_tokens", "output_tokens")):
                usage = {key: value for key, value in reported.items()
                         if key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")
                         and type(value) is int and value >= 0}
    if not terminal or not message:
        raise ValueError("Missing completed model response")
    result = {"provider_response": "COMPLETED", "provider_usage": usage or "UNKNOWN",
              "successful_fixture_command_sha256": tests}
    if retain_message:
        result["message"] = final_message
        result["command_events"] = commands
    return result


def _read_observation(path, limit):
    check_path(path)
    with path.open("rb") as stream:
        return stream.read(limit)


def _failure_evidence(stdout, stderr):
    event_types = set()
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if type(row) is dict and row.get("type") in ("thread.started", "turn.started", "turn.failed", "turn.completed"):
            event_types.add(row["type"])
    output = (stdout + "\n" + stderr.decode("utf-8", errors="replace")).casefold()
    signals = {
        "MXC_PROCESS_QUOTA_1816": r"createprocessw failed with error code 1816",
        "CODE_MODE_HOST_DISABLED": r"code mode is unavailable because code-mode host is disabled",
        "MODEL_UNAVAILABLE": r"model_not_found|model.{0,120}(?:not supported|not available|does not exist)",
        "PROVIDER_RATE_LIMIT": r"429 too many requests|rate limit exceeded|usage limit",
        "AUTH_REJECTED": r"401 unauthorized|403 forbidden|token refresh failed|not logged in",
        "TRANSPORT_FAILURE": r"error sending request|stream disconnected|certificate verify failed",
    }
    return {"cli_events": sorted(event_types),
            "cli_failure_signals": [label for label, pattern in signals.items() if re.search(pattern, output)]}


def _check_dedicated_config(home, temporary, *, approved_replicas=()):
    config = home / "config.toml"
    check_path(config)
    if config.exists():
        data = tomllib.loads(_read_observation(config, 16384).decode("utf-8"))
        # Codex itself can remember trust for its disposable replica.
        if (set(data) != {"projects"} or type(data["projects"]) is not dict
                or any((not Path(path).is_relative_to(temporary) and Path(path) not in approved_replicas)
                       or entry != {"trust_level": "trusted"} for path, entry in data["projects"].items())):
            raise Blocked("Dedicated Codex home contains settings outside trial-generated trust entries")


def _check_cached_model(home, model):
    if not re.fullmatch(r"[a-z0-9_.-]{1,64}", model):
        raise Blocked("Invalid Codex model identifier")
    path = home / "models_cache.json"
    check_path(path)
    if path.exists():
        cache = json.loads(_read_observation(path, 2 * 1024 * 1024))
        if not any(row.get("slug") == model and row.get("visibility") == "list"
                   for row in cache.get("models", []) if type(row) is dict):
            raise Blocked("Approved model is absent from the dedicated CLI model catalog")


def _verify_output(binary, work, output, environment, check, *, test_command="call total.cmd 3"):
    config = trial_flags(work, writable=False)
    command = Path(environment["SystemRoot"]) / "System32" / "cmd.exe"
    argv = [*config, *_args(command, "/d", "/q", "/c", test_command, profile=PROFILE)]
    return _observe(binary, argv, work, output, environment, seconds=10, check=check, output_limit=4096,
                    encoding="utf-8", process_limit=CODEX_DEVELOPER_PROCESS_LIMIT)


def _exec_argv(work, model, prompt, *, writable=True, tool_root=None, shell_tools=True):
    return [*trial_flags(work, writable=writable, code_mode=True, tool_root=tool_root, shell_tools=shell_tools),
            "exec", "--ignore-user-config", "--json",
            "--ephemeral", "--model", model, "--cd", str(work), prompt]


def _diagnostic(events):
    diagnostic = next((event for event in reversed(events)
                       if event.get("kind") == "native_inspection_result"
                       and event.get("probe_kind") == "GENERAL_CODEX_CODE_MODE"
                       and event.get("candidate_sha256") == RELEASE_SHA256
                       and event.get("code_mode_host_sha256") == CODE_MODE_HOST_SHA256
                       and event.get("flags_sha256") == code_mode_flags_sha256()), None)
    if (diagnostic is None or diagnostic.get("status") != "SYNTHETIC_PASS"
            or diagnostic.get("fixture_command_observed") is not True):
        raise Blocked("Run the local Code Mode command diagnostic successfully before reviewing an online trial")
    return diagnostic


def run_trial(ledger, archive, request, expires, approval=None, *, model=DEFAULT_MODEL,
              role_tokens=TOKEN_ALLOWANCE, session_tokens=TOKEN_ALLOWANCE):
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        runner = Runner(ledger)
        events, head = runner._idle_diagnostic(abandoned)
        archive = Path(archive).absolute()
        check_path(archive)
        home = ledger.local_directory("state") / "codex-developer-home"
        check_path(home)
        _check_cached_model(home, model)
        diagnostic = _diagnostic(events)
        scope = _scope(archive, home, request, expires, head, model, role_tokens, session_tokens, diagnostic)
        pin = digest(scope)
        standard = live_preflight()
        if approval is None:
            return {"status": "NEEDS_OWNER_APPROVAL", "scope": scope, "approval_sha256": pin,
                    "standard_mode": standard}
        if approval != pin or any(event.get("request_id") == request for event in events):
            raise Blocked("Trial approval mismatch or already used")
        if standard["reason"] != "GENERAL_ADMISSION_UNVERIFIED":
            raise Blocked("Trial cannot relax this general policy decision")
        if not home.is_dir():
            raise Blocked("Dedicated Codex home requires official ChatGPT sign-in")
        check_path(home / "auth.json")  # Identity only. The official CLI reads its login.
        _check_dedicated_config(home, ledger.local_directory("temp"))
        for name in ("AGENTS.md", "AGENTS.override.md", "rules"):
            target = home / name
            check_path(target)
            if target.exists():
                raise Blocked("Unexpected instructions or rules in the dedicated Codex home")
        fields = {"state": "DIAGNOSTIC", "request_id": request, "project_id": None, "run_id": None,
                  "probe_kind": "GENERAL_CODEX_DEVELOPER_TRIAL", "approval_sha256": pin, "scope": scope}
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(archive, lab / "package")
            _verify_code_mode_host(binary)
            work, original, scratch = (lab / name for name in ("work", "original", "tmp"))
            for folder in (work, original, scratch):
                folder.mkdir()
            for name, data in zip(SOURCE_NAMES, (SUBTOTAL.format(factor=2), TOTAL.format(fee=3))):
                (original / name).write_text(data, encoding="ascii")
            windows = _known_folder(0x24)
            environment = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                           "LOCALAPPDATA": str(scratch), "APPDATA": str(scratch), "USERPROFILE": str(scratch),
                           "HOME": str(scratch), "ProgramData": str(_known_folder(0x23)),
                           "PATH": str(windows / "System32"), "CODEX_HOME": str(home),
                           "TEMP": str(scratch), "TMP": str(scratch)}
            verification_home = lab / "verification-home"
            verification_home.mkdir()
            verification_env = {**environment, "CODEX_HOME": str(verification_home)}
            count, fence = ledger.append(len(events), head, {**fields, "kind": "native_inspection_intent"},
                                         admission=require_admission)
            cleanup["preserve"] = True

            def check():
                runner._check_diagnostic_fence(count, fence)
                return False

            def logs(name):
                path = scratch / name
                path.mkdir()
                return path

            baseline = _prepare_git_fixture(work, original, scratch, environment, check)
            before = snapshot(work)
            auth_logs = logs("auth")
            auth = _observe(binary, [*trial_flags(work), "login", "status"], work, auth_logs, environment,
                            seconds=10, check=check, output_limit=4096, encoding="utf-8")
            stderr = auth_logs / "probe.stderr"
            check_path(stderr)
            with stderr.open("rb") as stream:
                status_text = auth["stdout"].encode("utf-8") + b"\n" + stream.read(4096)
            result = {"status": "BLOCKED_AUTH", "provider_response": "NOT_STARTED", "provider_usage": "UNKNOWN"}
            signed_in = auth["exitCode"] == 0 and b"Logged in using ChatGPT" in status_text.splitlines()
            if signed_in:
                negative = _verify_output(binary, work, logs("baseline"), verification_env, check)
                result["status"] = "BASELINE_UNVERIFIED"
            if signed_in and negative["exitCode"] == 0 and negative["stdout"].strip() == "9" and not negative["stderr"]:
                check()
                remaining = expires - time()
                if remaining <= 0:
                    raise Blocked("Trial approval expired before dispatch")
                count, fence = ledger.append(count, fence, {**fields, "kind": "native_inspection_dispatch_intent",
                                                             "baseline_sha256": digest(before)})
                argv = _exec_argv(work, model, PROMPT)
                exec_logs = logs("exec")
                process_result = "EXITED"
                try:
                    observed = _observe(binary, argv, work, exec_logs, environment, seconds=min(SECONDS, remaining),
                                        check=check, output_limit=262144, encoding="utf-8",
                                        process_limit=CODEX_DEVELOPER_PROCESS_LIMIT)
                except OwnedMxcStopped:
                    # This exception is raised only after the owned Job was drained.
                    check()
                    raw = _read_observation(exec_logs / "probe.stdout", 262145)
                    observed = {"stdout": raw.decode("utf-8", errors="replace") if len(raw) <= 262144 else "",
                                "exitCode": "UNKNOWN", "job_active": 0}
                    process_result = "TERMINATED_AND_DRAINED"
                result = {"status": "UNVERIFIED", "provider_response": "UNKNOWN", "provider_usage": "UNKNOWN",
                          "exit_code": observed["exitCode"], "job_active": observed["job_active"],
                          "process_result": process_result,
                          **_failure_evidence(observed["stdout"],
                                              _read_observation(exec_logs / "probe.stderr", 65536))}
                try:
                    result.update(_cli_evidence(observed["stdout"]))
                except (KeyError, TypeError, ValueError):
                    pass
                after = snapshot(work)
                changed = sorted(name for name in before["files"].keys() | after["files"].keys()
                                 if before["files"].get(name) != after["files"].get(name))
                intact = before["git"] == after["git"] and snapshot(original) == baseline
                result.update(changed_paths_sha256=digest(changed), baseline_sha256=digest(before),
                              final_sha256=digest(after), original_and_git_unchanged=intact)
                if intact and changed == sorted(SOURCE_NAMES) and process_result == "EXITED":
                    git_env = {**verification_env, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL",
                               "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
                    diff = _observe(_fixture_git_binary(),
                                    ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--", *SOURCE_NAMES],
                                    work, logs("diff"), git_env, seconds=5, check=check, encoding="utf-8",
                                    output_limit=65536)
                    if diff["exitCode"] == 0 and diff["stdout"]:
                        result["git_diff_sha256"] = digest(diff["stdout"])
                    verified = _verify_output(binary, work, logs("verify"), verification_env, check)
                    result["independent_test_pass"] = (verified["exitCode"] == 0 and verified["stdout"].strip() == "11"
                                                       and not verified["stderr"] and snapshot(work) == after)
                usage = result["provider_usage"]
                if result["provider_response"] == "COMPLETED":
                    result["status"] = "MODEL_RESPONSE_ONLY"
                if type(usage) is dict:
                    tokens = usage["input_tokens"] + usage["output_tokens"]
                    result.update(provider_total_tokens=tokens,
                                  token_allowance_exceeded=tokens > min(role_tokens, session_tokens))
                if (result.get("independent_test_pass") and result.get("successful_fixture_command_sha256")
                        and result.get("git_diff_sha256")
                        and observed["exitCode"] == 0 and observed["job_active"] == 0 and type(usage) is dict
                        and not result["token_allowance_exceeded"]):
                    result["status"] = "REAL_PASS"
            _release_fixture_git_objects(work, original)
            cleanup["preserve"] = False
        if cleanup.get("status") != "REMOVED":
            result["status"] = "CLEANUP_UNVERIFIED"
        receipt = {**fields, **result, "kind": "native_inspection_result",
                   "owned_tree": "DRAINED", "staging_cleanup": cleanup}
        ledger.append(count, fence, receipt, allow_shutdown=True)
        return receipt
