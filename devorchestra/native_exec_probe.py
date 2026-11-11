"""Credential-free CLI startup and Code Mode checks against a local fixture."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

from .job_probe import CODEX_DEVELOPER_PROCESS_LIMIT

from .lease import execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import OwnedMxcStopped, _observe
from .mxc_launcher import _known_folder
from .native_command_probe import (
    CODE_MODE_HOST_SHA256, GENERAL_MODEL, RELEASE_SHA256, SOURCE_NAMES, SUBTOTAL, TOTAL, _prepare_git_fixture,
    _release_fixture_git_objects, _stage_release, _staging, _verify_code_mode_host,
)
from .replica_evidence import snapshot


PROMPT = "Synthetic DevOrchestra CLI startup check. Do not make any edits."


def _observe_exec(binary, work, scratch, environment, check, *, code_mode=False):
    from .native_developer_trial import trial_flags

    observed = []
    marker = uuid4().hex
    command = 'cmd.exe /d /q /c "call total.cmd 3"'
    tool = {"type": "custom_tool_call", "id": "tool_1", "call_id": "call_1", "name": "exec",
            "input": f"text({json.dumps(marker)}); text(await tools.exec_command({json.dumps({'cmd': command, 'login': False})}));",
            "status": "completed"}
    fixture = {"exec_advertised": False, "tool_output_observed": False}

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(2)
            length = self.headers.get("Content-Length", "")
            if length.isascii() and length.isdecimal() and len(length) <= 6 and int(length) <= 262144:
                raw = self.rfile.read(int(length))
                observed.append(self.path == "/v1/responses")
                if code_mode and observed[-1] and len(observed) <= 2:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        self.send_error(400)
                        return
                    if len(observed) == 1:
                        fixture["exec_advertised"] = any(
                            row.get("type") == "custom" and row.get("name") == "exec"
                            for row in body.get("tools", []))
                        if fixture["exec_advertised"]:
                            self.respond(tool, 1)
                            return
                    elif any(row.get("type") == "custom_tool_call_output" and row.get("call_id") == "call_1"
                             and marker in json.dumps(row.get("output")) for row in body.get("input", [])):
                        fixture["tool_output_observed"] = True
                        self.respond({"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                                      "content": [{"type": "output_text", "text": "Fixture completed.", "annotations": []}]},
                                     2)
                        return
            self.send_error(418, "Synthetic sink rejects inference")

        def respond(self, item, index):
            response = {"id": f"resp_{index}", "status": "completed", "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 0}}}
            rows = [{"type": "response.created", "response": {"id": response["id"]}},
                    {"type": "response.output_item.added", "output_index": 0, "item": item},
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": response}]
            payload = "".join(f"data: {json.dumps(row)}\n\n" for row in rows).encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with HTTPServer(("127.0.0.1", 0), Sink) as sink:
        url = f"http://127.0.0.1:{sink.server_port}/v1"
        thread = Thread(target=sink.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            flags = trial_flags(work, code_mode=code_mode)
            for override in ('forced_login_method="chatgpt"', 'model_provider="openai"'):
                index = flags.index(override)
                del flags[index - 1:index + 1]
            provider = ('model_providers.fixture={ name="Local fixture only", '
                        f'base_url="{url}", wire_api="responses", request_max_retries=0, stream_max_retries=0 }}')
            argv = [*flags, "-c", 'model_provider="fixture"', "-c", provider, "exec",
                    "--ignore-user-config", "--json", "--ephemeral",
                    "--model", GENERAL_MODEL, "--cd", str(work), PROMPT]
            result = _observe(binary, argv, work, scratch, {**environment, "OPENAI_BASE_URL": url},
                              seconds=45, check=check, output_limit=65536, encoding="utf-8",
                              process_limit=CODEX_DEVELOPER_PROCESS_LIMIT)
        finally:
            sink.shutdown()
            thread.join()
    events = set()
    command_observed = False
    for line in result["stdout"].splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if type(record) is dict and record.get("type") in ("thread.started", "turn.started", "turn.failed", "turn.completed"):
            events.add(record["type"])
        if type(record) is dict and record.get("type") == "item.completed":
            item = record.get("item", {})
            if (item.get("type") == "command_execution" and item.get("exit_code") == 0
                    and command in item.get("command", "") and "9" in item.get("aggregated_output", "").splitlines()):
                command_observed = True
    stderr = scratch / "probe.stderr"
    check_path(stderr)
    with stderr.open("rb") as stream:
        stderr_prefix = stream.read(65536)
    return {"loopback_requests": len(observed), "responses_path": bool(observed) and all(observed),
            **({**fixture, "fixture_command_observed": command_observed} if code_mode else {}),
            "events": sorted(events), "exit_code": result["exitCode"], "job_active": result["job_active"],
            "job_total_processes": result["job_total"],
            "mxc_error_1816": b"MXC launcher: CreateProcessW failed with error code 1816" in stderr_prefix}


def run_native_exec_probe(ledger, archive, *, code_mode=False):
    from .native_developer_trial import _verify_output, code_mode_flags_sha256
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        runner = Runner(ledger)
        events, head = runner._idle_diagnostic(abandoned)
        fields = {"state": "DIAGNOSTIC", "request_id": uuid4().hex, "project_id": None, "run_id": None,
                  "probe_kind": "GENERAL_CODEX_CODE_MODE" if code_mode else "GENERAL_CODEX_EXEC_STARTUP",
                  "development_mode": "general",
                  "candidate_sha256": RELEASE_SHA256, "prompt_ref": digest(PROMPT),
                  "job_process_limit": CODEX_DEVELOPER_PROCESS_LIMIT,
                  "provider_model_turn": "NOT_STARTED", "usage": "UNKNOWN"}
        with _staging(ledger) as (lab, cleanup):
            binary = _stage_release(Path(archive), lab / "package")
            if code_mode:
                _verify_code_mode_host(binary)
                fields["code_mode_host_sha256"] = CODE_MODE_HOST_SHA256
                fields["flags_sha256"] = code_mode_flags_sha256()
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
            try:
                verified = _verify_output(binary, work, test_logs, environment, check)
                result = _observe_exec(binary, work, scratch, environment, check, code_mode=code_mode)
                result["independent_baseline_observed"] = (
                    verified["exitCode"] == 0 and verified["stdout"].strip() == "9" and not verified["stderr"])
            except OwnedMxcStopped:
                check()
                result = {"process_result": "TERMINATED_AND_DRAINED", "job_active": 0,
                          "independent_baseline_observed": False}
            unchanged = snapshot(work) == before and snapshot(original) == baseline
            _release_fixture_git_objects(work, original)
            cleanup["preserve"] = False
        ready = (unchanged and result["independent_baseline_observed"] and result["responses_path"]
                 and "thread.started" in result["events"] and result["job_active"] == 0
                 and cleanup.get("status") == "REMOVED")
        if not ready:
            passed = False
        elif code_mode:
            passed = (ready and result["exit_code"] == 0 and result["loopback_requests"] == 2
                      and result["exec_advertised"] and result["tool_output_observed"] and result["fixture_command_observed"]
                      and "turn.completed" in result["events"])
        else:
            passed = (ready and result["exit_code"] != 0 and result["loopback_requests"] == 1
                      and "turn.completed" not in result["events"])
        receipt = {**fields, **result, "kind": "native_inspection_result", "owned_tree": "DRAINED",
                   "fixture_unchanged": unchanged, "staging_cleanup": cleanup,
                   "status": (("SYNTHETIC_PASS" if passed else "CODE_MODE_UNVERIFIED") if code_mode
                              else ("CLI_STARTUP_OBSERVED" if passed else "CLI_STARTUP_UNVERIFIED"))}
        ledger.append(count, fence, receipt, allow_shutdown=True)
        return receipt
