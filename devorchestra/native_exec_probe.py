"""Credential-free CLI startup check against a fixed local HTTP sink."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

from .job_probe import CODEX_DEVELOPER_PROCESS_LIMIT

from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import _observe
from .mxc_launcher import _known_folder
from .native_command_probe import (
    GENERAL_MODEL, RELEASE_SHA256, SOURCE_NAMES, SUBTOTAL, TOTAL, _prepare_git_fixture,
    _release_fixture_git_objects, _stage_release, _staging,
)
from .replica_evidence import snapshot


PROMPT = "Synthetic DevOrchestra CLI startup check. Do not make any edits or run commands."


def _observe_exec(binary, work, scratch, environment, check):
    from .native_developer_trial import trial_flags

    observed = []

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(2)
            length = self.headers.get("Content-Length", "")
            if length.isascii() and length.isdecimal() and len(length) <= 6 and int(length) <= 262144:
                self.rfile.read(int(length))
                observed.append(self.path == "/v1/responses")
            self.send_error(418, "Synthetic sink rejects inference")

    with HTTPServer(("127.0.0.1", 0), Sink) as sink:
        url = f"http://127.0.0.1:{sink.server_port}/v1"
        thread = Thread(target=sink.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            flags = trial_flags(work)
            for override in ('forced_login_method="chatgpt"', 'model_provider="openai"'):
                index = flags.index(override)
                del flags[index - 1:index + 1]
            provider = ('model_providers.fixture={ name="Local fixture only", '
                        f'base_url="{url}", wire_api="responses", request_max_retries=0, stream_max_retries=0 }}')
            argv = [*flags, "-c", 'model_provider="fixture"', "-c", provider, "exec",
                    "--ignore-user-config", "--json", "--ephemeral",
                    "--model", GENERAL_MODEL, "--cd", str(work), PROMPT]
            result = _observe(binary, argv, work, scratch, {**environment, "OPENAI_BASE_URL": url},
                              seconds=20, check=check, output_limit=65536, encoding="utf-8",
                              process_limit=CODEX_DEVELOPER_PROCESS_LIMIT)
        finally:
            sink.shutdown()
            thread.join()
    events = set()
    for line in result["stdout"].splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if type(record) is dict and record.get("type") in ("thread.started", "turn.started", "turn.failed", "turn.completed"):
            events.add(record["type"])
    stderr = scratch / "probe.stderr"
    check_path(stderr)
    with stderr.open("rb") as stream:
        stderr_prefix = stream.read(65536)
    return {"loopback_requests": len(observed), "responses_path": observed == [True],
            "events": sorted(events), "exit_code": result["exitCode"], "job_active": result["job_active"],
            "job_total_processes": result["job_total"],
            "mxc_error_1816": b"MXC launcher: CreateProcessW failed with error code 1816" in stderr_prefix}


def run_native_exec_probe(ledger, archive):
    from .native_developer_trial import _verify_output
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        runner = Runner(ledger)
        events, head = runner._idle_diagnostic(abandoned)
        fields = {"state": "DIAGNOSTIC", "request_id": uuid4().hex, "project_id": None, "run_id": None,
                  "probe_kind": "GENERAL_CODEX_EXEC_STARTUP", "development_mode": "general",
                  "candidate_sha256": RELEASE_SHA256, "prompt_ref": digest(PROMPT),
                  "job_process_limit": CODEX_DEVELOPER_PROCESS_LIMIT,
                  "provider_model_turn": "NOT_STARTED", "usage": "UNKNOWN"}
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(Path(archive), lab / "package")
            work, original, home, scratch = (lab / name for name in ("work", "original", "home", "tmp"))
            for folder in (work, original, home, scratch):
                folder.mkdir()
            for name, data in zip(SOURCE_NAMES, (SUBTOTAL.format(factor=2), TOTAL.format(fee=3))):
                (original / name).write_text(data, encoding="ascii")
            windows = _known_folder(0x24)
            environment = {
                "SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                "LOCALAPPDATA": str(home), "APPDATA": str(home), "USERPROFILE": str(home), "HOME": str(home),
                "ProgramData": str(_known_folder(0x23)), "PATH": str(windows / "System32"),
                "CODEX_HOME": str(home), "TEMP": str(scratch), "TMP": str(scratch),
            }
            count, fence = ledger.append(len(events), head, {**fields, "kind": "native_inspection_intent"},
                                         admission=require_admission)
            cleanup["preserve"] = True

            def check():
                runner._check_diagnostic_fence(count, fence)
                return False

            baseline = _prepare_git_fixture(work, original, scratch, environment, check)
            before = snapshot(work)
            test_logs = scratch / "baseline"
            test_logs.mkdir()
            verified = _verify_output(binary, work, test_logs, environment, check)
            result = _observe_exec(binary, work, scratch, environment, check)
            result["independent_baseline_observed"] = (
                verified["exitCode"] == 0 and verified["stdout"].strip() == "9" and not verified["stderr"])
            unchanged = snapshot(work) == before and snapshot(original) == baseline
            _release_fixture_git_objects(work, original)
            cleanup["preserve"] = False
        passed = (unchanged and result["independent_baseline_observed"]
                  and result["responses_path"] and result["exit_code"] != 0
                  and "thread.started" in result["events"] and "turn.completed" not in result["events"]
                  and result["job_active"] == 0 and cleanup.get("status") == "REMOVED")
        receipt = {**fields, **result, "kind": "native_inspection_result", "owned_tree": "DRAINED",
                   "fixture_unchanged": unchanged, "staging_cleanup": cleanup,
                   "status": "CLI_STARTUP_OBSERVED" if passed else "CLI_STARTUP_UNVERIFIED"}
        ledger.append(count, fence, receipt, allow_shutdown=True)
        return receipt
