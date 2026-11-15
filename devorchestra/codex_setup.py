"""Official Codex setup for the same private home used by registered sessions."""
from hashlib import file_digest
from pathlib import Path
import re
from shutil import copyfile
from time import time

from .ai_usage import codex_snapshot, subscription_quota
from .app_server import open_app_server
from .lease import Blocked, execution_slot
from .ledger import check_path
from .mxc_host_probe import OwnedMxcStopped, _observe
from .mxc_launcher import _known_folder
from .native_command_probe import ARCHIVE_SHA256, RELEASE_SHA256, _stage_release, _staging, _verify_code_mode_host
from .native_developer_trial import DEFAULT_MODEL, _check_dedicated_config, _diagnostic, _read_observation
from .native_exec_probe import recover_undispatched_exec_probe, run_native_exec_probe


RELEASE_URL = "https://github.com/openai/codex/releases/tag/rust-v0.167.1"
AUTH_DOCS = "https://learn.chatgpt.com/docs/auth?surface=cli"


class QuotaAdmissionBlocked(Blocked):
    """The usage-only host probe could not acquire a safe idle boundary."""


def archive_path(ledger):
    path = ledger.local_directory("state") / "codex-release.zip"
    check_path(path)
    return path


def _checksum(path):
    check_path(path)
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _provider(binary, lab, environment, cancelled):
    result = {"plan": "UNKNOWN", "model": "UNKNOWN", "limits": [], "windows": []}
    try:
        with open_app_server(binary, lab, environment, 25, cancelled, approved_sha256=RELEASE_SHA256) as server:
            server.restrict_outbound(frozenset({"initialize", "initialized", "account/read", "model/list",
                                                "account/rateLimits/read"}))
            server.initialize()
            account = server.request(1, "account/read", {"refreshToken": False},
                                     discard_account_updates=True).get("account")
            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                return result
            plan = account.get("planType")
            if isinstance(plan, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,23}", plan):
                result["plan"] = plan
            try:
                payload = server.request(2, "account/rateLimits/read", {}, discard_account_updates=True)
                result["windows"] = codex_snapshot(payload)["windows"]
                result["limits"] = [(row["minutes"], row["used"])
                                    for row in result["windows"] if row["used"] is not None]
            except (Blocked, ValueError):
                pass
            page = server.request(3, "model/list", {"limit": 100}, discard_account_updates=True)
            data = page.get("data")
            if isinstance(data, list) and all(isinstance(item, dict) and isinstance(item.get("id"), str)
                                             for item in data):
                if any(item["id"] == DEFAULT_MODEL for item in data):
                    result["model"] = "LISTED"
                elif not page.get("nextCursor"):
                    result["model"] = "NOT_LISTED"
    except (Blocked, OSError, ValueError):
        pass
    return result


def read_subscription_quota(binary, lab, environment, check):
    """Read ordinary ChatGPT allowance in the session's existing execution slot."""
    for attempt in range(2):
        check()
        server = None
        try:
            with open_app_server(binary, lab, environment, 15, check,
                                 approved_sha256=RELEASE_SHA256) as server:
                server.restrict_outbound(frozenset({"initialize", "initialized", "account/read",
                                                    "account/rateLimits/read"}))
                server.initialize()
                account = server.request(1, "account/read", {"refreshToken": False},
                                         discard_account_updates=True).get("account")
                if not isinstance(account, dict) or account.get("type") != "chatgpt":
                    return {**subscription_quota(None), "status": "AUTH_REQUIRED"}
                payload = server.request(2, "account/rateLimits/read", {}, discard_account_updates=True)
                quota = subscription_quota(payload)
                if (not isinstance(payload, dict) or not isinstance(payload.get("rateLimits"), dict)
                        or (payload.get("rateLimitsByLimitId") is not None
                            and not isinstance(payload["rateLimitsByLimitId"], dict))
                        or any(window["used"] is None for window in quota["windows"])):
                    quota = {**quota, "status": "RESPONSE_INVALID", "remaining": None}
        except (Blocked, OSError, ValueError) as error:
            receipt = getattr(server, "cleanup_receipt", {})
            if (isinstance(error, OSError) or server is None
                    or receipt.get("job") not in {"DRAINED_NORMALLY", "TERMINATED_AND_DRAINED"}
                    or receipt.get("pipes") != "CLOSED"):
                raise
            check()
            diagnostics = getattr(server, "diagnostics", ())
            errors = {row.get("error") for row in diagnostics}
            details = {(row.get("shape") or {}).get("detail") for row in diagnostics}
            status = ("UNSUPPORTED" if errors & {"METHOD_NOT_FOUND", "INVALID_PARAMS"}
                      else "AUTH_REQUIRED" if "USAGE_AUTH_REQUIRED" in details
                      else "NETWORK_FAILED" if "USAGE_NETWORK_FAILED" in details
                      else "RESPONSE_INVALID" if errors & {"PARSE_ERROR"}
                      or getattr(server, "failure_code", "") in {"INVALID_JSON", "INVALID_RESULT"}
                      else "QUERY_FAILED")
            quota = {**subscription_quota(None), "status": status}
        if quota["status"] not in {"QUERY_FAILED", "NETWORK_FAILED", "STALE", "CONFLICT", "UNKNOWN"} or attempt:
            return quota


def _quota_context(runner, abandoned):
    from .runtime import native_unresolved

    events, head = runner.ledger.snapshot()
    if abandoned or not native_unresolved(events):
        runner._check_idle_diagnostic(events, abandoned)
        return events, head
    # A full reboot makes this fixed RPC read safe for the eligible interrupted
    # planner. Normal development still requires a separately approved intent.
    from .registered_recovery import reboot_scope, reboot_target, windows_boot_boundary

    last = events[-1]
    project, request = last.get("project_id"), last.get("request_id")
    target = reboot_target(events, project, request)
    boot = windows_boot_boundary(request, target["workspace"])
    now = int(time())
    boundary = reboot_scope(events, head, project, request, now + 60, boot, now)
    runner._check_idle_diagnostic(events, abandoned, reboot_boundary=boundary)
    return events, head


def read_limits(runner, *, cancelled=lambda: False):
    """Read official allowance without changing the ledger or dedicated login."""
    ledger = runner.ledger
    saved = archive_path(ledger)
    home = ledger.local_directory("state") / "codex-developer-home"
    credentials = home / "auth.json"
    check_path(credentials)
    if not saved.is_file() or not credentials.is_file():
        return {**subscription_quota(None), "status": "AUTH_REQUIRED"}
    with execution_slot() as abandoned:
        try:
            events, head = _quota_context(runner, abandoned)
        except Blocked as error:
            raise QuotaAdmissionBlocked(str(error)) from error

        def check():
            runner._check_diagnostic_fence(len(events), head)
            if cancelled():
                raise Blocked("CANCELLED")

        replicas = {Path(row["scope"]["replica"]) for row in events
                    if row["kind"] == "native_inspection_intent"
                    and row.get("scope", {}).get("auth_home") == str(home)
                    and isinstance(row["scope"].get("replica"), str)}
        _check_dedicated_config(home, ledger.local_directory("temp"), approved_replicas=replicas)
        check()
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(saved, lab / "package")
            _verify_code_mode_host(binary)
            account = lab / "quota-account"
            account.mkdir()
            # A provider refresh must never rewrite the shared session used by
            # registered development. The copy stays inside owned staging.
            copyfile(credentials, account / "auth.json")
            (account / "config.toml").write_text('forced_login_method = "chatgpt"\n'
                                               'cli_auth_credentials_store = "file"\n', encoding="ascii")
            windows = _known_folder(0x24)
            environment = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                           "PATH": str(windows / "System32"), "ProgramData": str(_known_folder(0x23)),
                           "LOCALAPPDATA": str(lab), "APPDATA": str(lab), "USERPROFILE": str(lab),
                           "CODEX_HOME": str(account), "TEMP": str(lab), "TMP": str(lab)}
            try:
                quota = read_subscription_quota(binary, lab, environment, check)
            finally:
                (account / "auth.json").unlink(missing_ok=True)
        if cleanup.get("status") != "REMOVED":
            raise Blocked("Codex quota staging cleanup unconfirmed")
        check()
        return quota


def run(runner, action, *, archive=None, cancelled=lambda: False):
    if action not in {"install", "status", "login", "logout", "provider", "limits", "verify", "recover"}:
        raise ValueError("Unknown Codex setup action")
    ledger = runner.ledger
    if action == "recover":
        recover_undispatched_exec_probe(ledger)
        return {"status": "RECOVERED"}
    saved = archive_path(ledger)
    if action == "limits":
        return read_limits(runner, cancelled=cancelled)
    if action == "verify":
        return {"verification": run_native_exec_probe(ledger, saved, code_mode=True)["status"]}
    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        source = Path(archive) if action == "install" else saved
        if not source.is_file():
            raise Blocked("공식 Codex Windows ZIP을 먼저 선택하세요.")
        try:
            _diagnostic(events)
            verified = True
        except Blocked:
            verified = False
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(source, lab / "package")
            _verify_code_mode_host(binary)
            account_home = ledger.local_directory("state") / "codex-developer-home"
            check_path(account_home / "auth.json")
            replicas = {Path(e["scope"]["replica"]) for e in events
                        if e.get("kind") == "native_inspection_intent"
                        and e.get("scope", {}).get("auth_home") == str(account_home)
                        and isinstance(e["scope"].get("replica"), str)}
            _check_dedicated_config(account_home, ledger.local_directory("temp"), approved_replicas=replicas)
            for name in ("AGENTS.md", "AGENTS.override.md", "rules"):
                path = account_home / name
                check_path(path)
                if path.exists():
                    raise Blocked("전용 Codex 홈에서 예상하지 않은 지침을 발견했습니다.")
            account_home.mkdir(exist_ok=True)
            windows = _known_folder(0x24)
            env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                   "PATH": str(windows / "System32"), "LOCALAPPDATA": str(_known_folder(0x1C)),
                   "APPDATA": str(_known_folder(0x1A)), "USERPROFILE": str(_known_folder(0x28)),
                   "ProgramData": str(_known_folder(0x23)), "CODEX_HOME": str(account_home),
                   "TEMP": str(lab), "TMP": str(lab)}

            def check():
                runner._check_diagnostic_fence(len(events), head)
                if cancelled():
                    raise Blocked("CANCELLED")

            def command(name, args, seconds=12):
                check()
                logs = lab / name
                logs.mkdir()
                response = _observe(binary, args, lab, logs, env, seconds=seconds,
                                    check=check, encoding="utf-8", output_limit=8192)
                return response, response["stdout"] + "\n" + _read_observation(
                    logs / "probe.stderr", 8192).decode("utf-8", errors="replace")

            response, output = command("version", ["--version"])
            if response["exitCode"] or "codex-cli 0.167.1" not in output.splitlines():
                raise Blocked("지원하는 Codex CLI 버전을 확인할 수 없습니다.")
            if action == "install" and source != saved:
                pending = lab / "checked.zip"
                copyfile(source, pending)
                if _checksum(pending) != ARCHIVE_SHA256:
                    raise Blocked("Codex ZIP이 확인 중 변경됐습니다.")
                check()
                pending.replace(saved)
            status = None
            if action in {"login", "logout"}:
                args = ['-c', 'forced_login_method="chatgpt"', 'login'] if action == "login" else ["logout"]
                try:
                    response, _ = command(action, args, seconds=180 if action == "login" else 12)
                except OwnedMxcStopped:
                    status = "CANCELLED" if cancelled() else "TIMEOUT"
                else:
                    if response["exitCode"]:
                        status = "AUTH_FAILED"
            if status is None:
                response, output = command("status", ["login", "status"])
                status = ("CONNECTED" if response["exitCode"] == 0 and "Logged in using ChatGPT" in output.splitlines()
                          else "OTHER_AUTH" if response["exitCode"] == 0
                          else "SIGNED_OUT" if "not logged in" in output.casefold() else "STATUS_UNKNOWN")
            result = {"status": status, "verification": verified, "version": "0.167.1"}
            if action == "provider" and status == "CONNECTED":
                check()
                result["provider"] = _provider(binary, lab, env, cancelled)
        if cleanup.get("status") != "REMOVED":
            raise Blocked("Codex 임시 실행 폴더의 정리를 확인할 수 없습니다.")
        return result
