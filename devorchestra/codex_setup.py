"""Official Codex setup for the same private home used by registered sessions."""
from hashlib import file_digest
from pathlib import Path
import re
from shutil import copyfile

from .app_server import open_app_server
from .lease import Blocked, execution_slot
from .ledger import check_path
from .mxc_host_probe import OwnedMxcStopped, _observe
from .mxc_launcher import _known_folder
from .native_command_probe import ARCHIVE_SHA256, RELEASE_SHA256, _stage_release, _staging, _verify_code_mode_host
from .native_developer_trial import DEFAULT_MODEL, _check_dedicated_config, _diagnostic, _read_observation
from .native_exec_probe import run_native_exec_probe


RELEASE_URL = "https://github.com/openai/codex/releases/tag/rust-v0.167.1"
AUTH_DOCS = "https://learn.chatgpt.com/docs/auth?surface=cli"


def archive_path(ledger):
    path = ledger.local_directory("state") / "codex-release.zip"
    check_path(path)
    return path


def _checksum(path):
    check_path(path)
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _provider(binary, lab, environment, cancelled):
    result = {"plan": "UNKNOWN", "model": "UNKNOWN", "limits": []}
    try:
        with open_app_server(binary, lab, environment, 25, cancelled, approved_sha256=RELEASE_SHA256) as server:
            server.restrict_outbound({"initialize", "initialized", "account/read", "model/list",
                                      "account/rateLimits/read"})
            server.initialize()
            account = server.request(1, "account/read", {"refreshToken": False}).get("account")
            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                return result
            plan = account.get("planType")
            if isinstance(plan, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,23}", plan):
                result["plan"] = plan
            page = server.request(2, "model/list", {"limit": 100})
            data = page.get("data")
            if isinstance(data, list) and all(isinstance(item, dict) and isinstance(item.get("id"), str)
                                             for item in data):
                if any(item["id"] == DEFAULT_MODEL for item in data):
                    result["model"] = "LISTED"
                elif not page.get("nextCursor"):
                    result["model"] = "NOT_LISTED"
            limits = server.request(3, "account/rateLimits/read", {}).get("rateLimits")
            if isinstance(limits, dict):
                for key in ("primary", "secondary"):
                    bucket = limits.get(key)
                    if isinstance(bucket, dict) and type(bucket.get("usedPercent")) in (int, float):
                        used = bucket["usedPercent"]
                        if 0 <= used <= 100:
                            minutes = bucket.get("windowDurationMins")
                            result["limits"].append((minutes if type(minutes) is int and minutes > 0 else None, used))
    except (Blocked, OSError, ValueError):
        pass
    return result


def run(runner, action, *, archive=None, cancelled=lambda: False):
    if action not in {"install", "status", "login", "logout", "provider", "verify"}:
        raise ValueError("Unknown Codex setup action")
    ledger = runner.ledger
    saved = archive_path(ledger)
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
