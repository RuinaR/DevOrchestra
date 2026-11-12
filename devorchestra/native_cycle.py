"""One approval for a bounded, sequential native Codex fixture session."""
from difflib import unified_diff
import json
from pathlib import Path
import re
from time import monotonic, time

from . import cycle_progress
from .default_instructions import public_instruction
from .job_probe import CODEX_DEVELOPER_PROCESS_LIMIT
from .lease import Blocked, execution_slot
from .ledger import check_path, digest
from .mxc_host_probe import OwnedMxcStopped, _observe
from .mxc_launcher import _known_folder
from .native_command_probe import (
    ARCHIVE_SHA256, CODE_MODE_HOST_SHA256, RELEASE_SHA256, SOURCE_NAMES, SUBTOTAL, TOTAL,
    _fixture_git_binary, _prepare_git_fixture, _release_fixture_git_objects, _stage_release,
    _staging, _verify_code_mode_host,
)
from .native_developer_trial import (
    DEFAULT_MODEL, _check_cached_model, _check_dedicated_config, _cli_evidence, _diagnostic,
    _exec_argv, _failure_evidence, _read_observation, _verify_output, trial_flags,
)
from .policy import live_preflight
from .replica_evidence import changes, snapshot
from .shutdown import HELD, shutdown_state


def _live_activity(kind, item):
    """Project public event types into fixed UI text without retaining model content."""
    if kind == "turn.started":
        return "모델 응답 처리 중"
    if kind in {"turn.completed", "turn.failed"}:
        return "모델 응답 종료 · 결과 확인 중"
    if kind not in {"item.started", "item.completed"} or not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if item_type == "command_execution":
        command = item.get("command")
        tool = re.match(r"(?i)^\s*[\x22']?(pytest|python|dotnet|git|rg|npm|cargo|go|godot|cmake|msbuild)"
                        r"(?:\.exe)?(?=\s|[\x22']|$)", command) if isinstance(command, str) else None
        suffix = f" · {tool[1].lower()}" if tool else ""
        if kind == "item.started":
            return "도구 명령 실행 중" + suffix
        code = item.get("exit_code")
        status = ("도구 명령 완료" if code == 0 else "도구 명령 종료 · 오류"
                  if type(code) is int else "도구 명령 종료")
        return status + suffix
    if item_type == "file_change":
        return "파일 변경 적용 중" if kind == "item.started" else "파일 변경 적용 결과 수신"
    if item_type == "todo_list":
        return "작업 목록 갱신"
    if item_type == "agent_message":
        return "응답 작성 중" if kind == "item.started" else "응답 수신"
    return None


ROLES = ("manager-planning", "developer", "reviewer", "verifier", "manager-final")
ROLE_TOKENS = {"manager-planning": 30000, "developer": 60000, "reviewer": 55000,
               "manager-final": 30000}
GOAL = "Set the subtotal multiplier to 3 and the independent fixed fee to 2."
TASKS = {
    "A": {"file": SOURCE_NAMES[0], "text": "Subtotal multiplies by 3",
          "old": SUBTOTAL.format(factor=2), "new": SUBTOTAL.format(factor=3),
          "command": "call subtotal.cmd 3 & set subtotal", "expected": "subtotal=9"},
    "B": {"file": SOURCE_NAMES[1], "text": "Total adds a fixed fee of 2",
          "old": TOTAL.format(fee=3), "new": TOTAL.format(fee=2),
          "command": "call total.cmd 3", "expected": "11"},
}
CRITERIA = cycle_progress.freeze(
    [task["text"] for task in TASKS.values()],
    {f"C{index}-{digest(task['text'])[:8]}": [{"title": name, "input": 3}]
     for index, (name, task) in enumerate(TASKS.items(), 1)},
)
KEYS = dict(zip(TASKS, CRITERIA))


def _review(ledger, events, head, archive, request, expires, seconds, tokens, cycles):
    if (not re.fullmatch(r"[a-f0-9]{32}", request) or not time() < expires <= time() + 1800
            or not 60 <= seconds <= 1500 or not max(ROLE_TOKENS.values()) <= tokens <= 400000
            or not 2 <= cycles <= 3):
        raise Blocked("Invalid fixture session ID, expiry, time, token or cycle budget")
    if any(event.get("request_id") == request for event in events):
        raise Blocked("Native cycle request already used")
    if live_preflight()["reason"] != "GENERAL_ADMISSION_UNVERIFIED":
        raise Blocked("Current general policy does not allow a native fixture trial")
    archive = Path(archive).absolute()
    check_path(archive)
    if not archive.is_file():
        raise Blocked("Official Codex archive not found")
    diagnostic = _diagnostic(events)
    auth = ledger.local_directory("state") / "codex-developer-home"
    check_path(auth)
    if not auth.is_dir():
        raise Blocked("Dedicated Codex home requires official ChatGPT sign-in")
    _check_cached_model(auth, DEFAULT_MODEL)
    return {
        "request_id": request, "ledger_head": head, "archive": str(archive), "auth_home": str(auth),
        "archive_sha256": ARCHIVE_SHA256, "binary_sha256": RELEASE_SHA256,
        "code_mode_host_sha256": CODE_MODE_HOST_SHA256, "diagnostic": diagnostic["request_id"],
        "project": "DISPOSABLE_NATIVE_CYCLE_FIXTURE", "replica": "FRESH_LOCAL_GIT_CLONE_NO_REMOTE",
        "goal": GOAL, "criteria": CRITERIA, "fixture_contract_sha256": digest(TASKS),
        "files": list(SOURCE_NAMES), "approved_commands": [task["command"] for task in TASKS.values()],
        "roles": list(ROLES), "model": DEFAULT_MODEL, "auth": "CHATGPT_ONLY",
        "instructions": {role: public_instruction(role)["sha256"] for role in ROLES},
        "sandbox": "MXC_REPLICA_WRITE_DEVELOPER_ONLY_GIT_READ_COMMAND_NETWORK_OFF",
        "developer_file_scope": "CHECKED_AFTER_TOOL_EXECUTION",
        "command_scope": "MXC_SHELL_WITHIN_REPLICA_FIXED_TESTS_REQUIRED",
        "install": False, "original_apply": False,
        "flags_sha256": {access: digest(trial_flags(Path("APPROVED_REPLICA_ROOT"),
                                                   writable=access == "write", code_mode=True))
                         for access in ("read", "write")},
        "expires": expires, "seconds": seconds, "tokens": tokens, "cycles": cycles,
        "role_tokens": ROLE_TOKENS, "role_seconds": 120, "model_calls_max": cycles * 4,
        "token_control": "RESERVE_NEXT_ROLE_ALLOWANCE_THEN_CHECK_REPORTED_USAGE",
    }


def _execute_cycles(scope, role, verify, source, evidence):
    """Apply the frozen criteria to every verified revision before continuing."""
    previous = {key: "NOT_STARTED" for key in CRITERIA}
    seen = {source()}
    prior_failures = set()
    retry = None
    retry_task = None
    cycles = 0
    outcome = "CYCLE_LIMIT"
    for number in range(1, scope["cycles"] + 1):
        remaining = [name for name, key in KEYS.items() if previous[key] != "VERIFIED"]
        next_task = retry_task or (remaining[0] if remaining else None)
        if next_task is None:
            outcome = "BLOCKED"
            break
        task = TASKS[next_task]
        replan = retry_task is not None
        format_instruction = (f"Reply PLAN {next_task}: followed by a different diagnosis on one line."
                              if replan else f"Reply exactly PLAN {next_task}.")
        plan = role(number, "manager-planning", next_task,
                    f"Goal: {GOAL} Criteria: {previous}. Next approved feature: {task['text']}. "
                    f"Prior evidence: {retry or 'baseline'}. {format_instruction}")
        if replan:
            proposal = re.fullmatch(rf"PLAN {next_task}: ([^\r\n]{{1,220}})", plan["message"])
            if proposal is None:
                raise Blocked("NO_PROGRESS")
            approach = " ".join(proposal[1].casefold().split())
            if approach == retry["previous_approach"]:
                raise Blocked("NO_PROGRESS")
        elif plan["message"] != f"PLAN {next_task}":
            raise Blocked("Manager plan did not select the approved feature")
        else:
            approach = "change the approved fixture line and run its fixed test"
        developed = role(number, "developer", next_task,
                         f"Implement {task['text']}. Change only {task['file']}, preserving all other lines. "
                         f"Use the repository tools to edit it. Run only this fixture test with cmd.exe /d /q /c: "
                         f"{task['command']}. Expected line: {task['expected']}. "
                         f"Plan: {plan['message']}. Prior evidence: {retry or 'baseline'}. "
                         "Do not install, use Git write commands or use network.")
        current = source()
        reviewed = role(number, "reviewer", next_task,
                        f"Independently review {task['text']} using the diff from this cycle's starting revision. "
                        f"Criteria at cycle entry: {previous}. Earlier verified changes are part of that baseline.\n"
                        f"{developed['diff']}\n"
                        "Use the supplied diff directly when it contains enough evidence. "
                        "Reply exactly ACCEPT, or REWORK followed by a concrete finding on one line.")
        decision = reviewed["message"]
        if decision != "ACCEPT" and not re.fullmatch(r"REWORK [^\r\n]{1,220}", decision):
            raise Blocked("Invalid independent Reviewer decision")
        checked = verify(number, next_task)
        comparison = cycle_progress.compare(CRITERIA, previous, checked)
        for key, state in comparison["criteria"].items():
            if key != KEYS[next_task] and previous[key] == "NOT_STARTED" and state == "BLOCKED":
                # A known baseline miss on future work is not a defect in this cycle.
                comparison["criteria"][key] = "NOT_STARTED"
        previous = comparison["criteria"]
        complete = all(state == "VERIFIED" for state in previous.values())
        target_passed = previous[KEYS[next_task]] == "VERIFIED"
        failed = decision != "ACCEPT" or not developed["successful_fixture_command_sha256"] or not target_passed
        repeated = current in seen
        seen.add(current)
        expected = ("REWORK" if failed or comparison["regressed"] or repeated else
                    "COMPLETED" if complete else "CONTINUE")
        evidence(number, {**comparison, "source_sha256": current, "target": next_task,
                          "review": decision.split()[0], "repeated_source": repeated,
                          "controller_decision": expected})
        outstanding = [name for name, key in KEYS.items() if name != next_task and previous[key] != "VERIFIED"]
        final = role(number, "manager-final", next_task,
                     f"Overall goal: {GOAL}\n"
                     f"Current approved feature: {next_task}. Independent check passed for this feature: {target_passed}. "
                     f"Other features pending a later cycle: {outstanding}. Frozen criteria: {previous}. "
                     f"Independent review: {decision.split()[0]}. Changed-source repetition: {repeated}. "
                     f"Developer test observed: {bool(developed['successful_fixture_command_sha256'])}. "
                     f"Regressed criteria: {comparison['regressed']}.\n"
                     "Reply with exactly one decision. Use REWORK if the current feature failed its check, "
                     "the review rejected it, the developer test was missing, the source repeated, "
                     "or a previously verified criterion regressed. Otherwise use CONTINUE if another feature "
                     "is pending, or COMPLETED if every criterion is verified. "
                     "For example, a verified A and a B that has not started means CONTINUE.")
        if final["message"] != expected:
            raise Blocked("Manager decision disagrees with Controller evidence")
        cycles = number
        if expected == "COMPLETED":
            outcome = "COMPLETED"
            break
        if expected == "CONTINUE":
            retry = {"criteria": previous, "verifier_sha256": digest(checked)}
            retry_task = None
            continue
        fingerprint = cycle_progress.failure_fingerprint(
            "reviewer" if decision != "ACCEPT" else "verifier",
            [decision] if decision != "ACCEPT" else checked["observations"], digest([next_task, RELEASE_SHA256]))
        if fingerprint in prior_failures:
            outcome = "NO_PROGRESS"
            break
        prior_failures.add(fingerprint)
        retry_task = next_task
        retry = {"replan": True, "criteria": previous, "review": decision,
                 "same_revision": repeated, "observations": checked["observations"],
                 "previous_approach": approach}
    return {"outcome": outcome, "cycle_count": cycles, "criteria_status": previous}


class _FixtureSession:
    def __init__(self, runner, scope, lab, count, fence, fields, cancelled):
        self.runner, self.scope, self.fields = runner, scope, fields
        self.count, self.fence, self.cancelled = count, fence, cancelled
        self.started = monotonic()
        self.work = Path(scope["replica"]) if "replica_sha256" in scope else lab / "work"
        self.original = Path(scope["original"]) if "replica_sha256" in scope else lab / "original"
        self.scratch, home = lab / "tmp", lab / "verify"
        paths = (self.scratch, home) if "replica_sha256" in scope else (self.work, self.original, self.scratch, home)
        for path in paths:
            path.mkdir()
        windows = _known_folder(0x24)
        self.env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
                    "LOCALAPPDATA": str(self.scratch), "APPDATA": str(self.scratch),
                    "USERPROFILE": str(self.scratch), "HOME": str(self.scratch),
                    "ProgramData": str(_known_folder(0x23)), "PATH": str(windows / "System32"),
                    "CODEX_HOME": scope["auth_home"], "TEMP": str(self.scratch), "TMP": str(self.scratch)}
        self.verify_env = {**self.env, "CODEX_HOME": str(home)}
        self.binary = _stage_release(Path(scope["archive"]), lab / "package")
        _verify_code_mode_host(self.binary)
        self.usage, self.calls, self.phases = 0, 0, []
        self.usage_complete = True
        self.owned_verified = True
        self.progress = {key: "NOT_STARTED" for key in scope["criteria"]}
        self.cycles_checked = 0
        self.stop_reason = None
        self.signoff_started = None
        self.signoff_tokens = 0

    def record(self, kind, **facts):
        self.count, self.fence = self.runner.ledger.append(
            self.count, self.fence, {**self.fields, **facts, "kind": kind}, allow_shutdown=True)

    def check(self):
        events, tail = self.runner.ledger.snapshot()
        if shutdown_state(events)["state"] in HELD or self.cancelled():
            raise Blocked("CANCELLED")
        if (len(events), tail) != (self.count, self.fence):
            raise Blocked("Execution fence changed")
        if "graceful_stop" not in self.scope:
            self.seconds()
        elif time() >= self.scope["expires"]:
            raise Blocked("APPROVAL_EXPIRED")
        return False

    def request_stop(self, reason, phase):
        if self.stop_reason is None:
            self.stop_reason = reason
            self.record("native_inspection_stop_requested", outcome="STOP_REQUESTED", reason=reason,
                        phase=phase, known_reported_tokens=self.usage)

    def soft_boundary(self, phase):
        if monotonic() - self.started >= self.scope["seconds"]:
            self.request_stop("SESSION_TIME", phase)
        elif self.usage >= self.scope["tokens"]:
            self.request_stop("SESSION_TOKENS", phase)

    def admit_graceful(self, phase, reserve=0):
        policy = self.scope["graceful_stop"]
        self.soft_boundary(phase)
        finishing = phase in {"reviewer", "verifier", "manager-final"}
        if self.stop_reason is None and self.scope["tokens"] - self.usage < reserve:
            self.request_stop("TOKEN_RESERVATION", phase)
        if self.stop_reason is None:
            return False
        if not finishing:
            raise Blocked("GRACEFUL_STOP")
        if self.signoff_started is None:
            self.signoff_started = monotonic()
        remaining = policy["signoff_tokens"] - self.signoff_tokens
        if policy["signoff_within_session_tokens"]:
            remaining = min(remaining, self.scope["tokens"] - self.usage)
        if (remaining < reserve or monotonic() - self.signoff_started >= policy["signoff_seconds"]):
            raise Blocked("SIGNOFF_PENDING")
        return True

    def progress_check(self, phase, started, role_log, output_limit, *, report=None):
        policy = self.scope.get("graceful_stop")
        role_soft = self.scope["role_deadlines"][phase] if policy else None
        last_size, last_progress = 0, started
        complete_lines = 0
        active = set()
        path = role_log / "probe.stdout"

        def check():
            nonlocal last_size, last_progress, complete_lines
            self.check()
            now = monotonic()
            if policy:
                self.soft_boundary(phase)
                if now - started >= role_soft:
                    self.request_stop("ROLE_TIME", phase)
            size = path.stat().st_size if path.is_file() else 0
            if size > output_limit:
                raise Blocked("MODEL_EVENT_LIMIT")
            if size != last_size:
                last_size = size
                lines = _read_observation(path, output_limit + 1).split(b"\n")
                for line in lines[complete_lines:-1]:
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type")
                    item = event.get("item", {})
                    if kind in {"thread.started", "turn.started", "turn.completed", "turn.failed",
                                "item.started", "item.updated", "item.completed"}:
                        last_progress = now
                    if isinstance(item, dict) and item.get("type") == "command_execution":
                        if kind == "item.started":
                            active.add(item.get("id"))
                        elif kind == "item.completed":
                            active.discard(item.get("id"))
                    if report:
                        activity = _live_activity(kind, item)
                        if activity:
                            report(activity)
                complete_lines = len(lines) - 1
            if policy and now - last_progress >= policy["tool_idle_seconds" if active else "idle_seconds"]:
                raise Blocked("MODEL_IDLE_TIMEOUT")
        return check

    def seconds(self, role=None):
        remaining = min(self.scope["seconds"] - (monotonic() - self.started), self.scope["expires"] - time())
        if remaining <= 0:
            raise Blocked("TIME_LIMIT")
        limit = self.scope.get("role_deadlines", {}).get(role, self.scope["role_seconds"])
        if role == "developer" and "role_deadlines" not in self.scope:
            limit = self.scope.get("developer_seconds", limit)
        return min(limit, remaining)

    def verification_seconds(self):
        if "graceful_stop" not in self.scope:
            return self.seconds()
        self.admit_graceful("verifier")
        remaining = self.scope["expires"] - time()
        if self.signoff_started is not None:
            remaining = min(remaining, self.scope["graceful_stop"]["signoff_seconds"]
                            - (monotonic() - self.signoff_started))
        if remaining <= 0:
            raise Blocked("SIGNOFF_PENDING")
        return min(self.scope["role_seconds"], remaining)

    def logs(self, label):
        path = self.scratch / label
        path.mkdir()
        return path

    def owned(self, invoke):
        self.owned_verified = False
        try:
            result = invoke()
        except OwnedMxcStopped:
            self.owned_verified = True
            raise
        self.owned_verified = True
        return result

    def prepare(self):
        self.check()
        for task in TASKS.values():
            (self.original / task["file"]).write_text(task["old"], encoding="ascii")
        self.original_image = self.owned(
            lambda: _prepare_git_fixture(self.work, self.original, self.scratch, self.env, self.check))
        self.image = snapshot(self.work)
        denied = self.owned(lambda: _verify_output(
            self.binary, self.work, self.logs("readonly-control"), self.verify_env, self.check,
            test_command="echo unapproved>>subtotal.cmd"))
        if denied["exitCode"] != 1 or denied["job_active"] or not denied["stderr"]:
            raise Blocked("Read-only MXC fixture write denial could not be verified")
        self.check_image()
        self.check_login()
        baseline = self.observe("baseline")
        if [row["actual"] for row in baseline] != [6, 3]:
            raise Blocked("Both independent fixture criteria must start unmet")
        self.record("native_inspection_fixture_baseline", source_sha256=self.source(), observations=baseline,
                    readonly_write_denied=True)

    def check_login(self):
        auth_log = self.logs("auth")
        auth = self.owned(lambda: _observe(
            self.binary, [*trial_flags(self.work, writable=False), "login", "status"],
            self.work, auth_log, self.env, seconds=min(10, self.seconds()), check=self.check,
            output_limit=4096, encoding="utf-8"))
        stderr = _read_observation(auth_log / "probe.stderr", 4096)
        if auth["exitCode"] != 0 or b"Logged in using ChatGPT" not in (
                auth["stdout"].encode("utf-8") + b"\n" + stderr).splitlines():
            raise Blocked("Official ChatGPT sign-in is unavailable")

    def source(self):
        return digest(self.image)

    def check_image(self):
        if snapshot(self.work) != self.image or snapshot(self.original) != self.original_image:
            raise Blocked("Replica or protected fixture changed outside the Developer role")

    def observe(self, label):
        self.check_image()
        # Run only the pinned arithmetic fixture. Reject any other executable source.
        for task in TASKS.values():
            content = _read_observation(self.work / task["file"], 512).decode("ascii").replace("\r\n", "\n")
            if content not in (task["old"], task["new"]):
                raise Blocked("Source is outside the fixed executable fixture contract")
        outputs = []
        for name, task in TASKS.items():
            self.check()
            result = self.owned(lambda: _verify_output(self.binary, self.work, self.logs(f"{label}-{name}"),
                                                       self.verify_env, self.check, test_command=task["command"]))
            if result["exitCode"] != 0 or result["job_active"] or result["stderr"]:
                raise Blocked("Independent fixture test did not settle successfully")
            outputs.append(result["stdout"].strip())
        self.check_image()
        if not re.fullmatch(r"subtotal=[0-9]{1,3}", outputs[0]) or not re.fullmatch(r"[0-9]{1,3}", outputs[1]):
            raise Blocked("Independent fixture test returned invalid output")
        subtotal = int(outputs[0].split("=")[1])
        return [{"title": "A", "input": 3, "expected": 9, "actual": subtotal, "exit_code": 0},
                {"title": "B", "input": 3, "expected": 2, "actual": int(outputs[1]) - subtotal, "exit_code": 0}]

    def verify(self, cycle, target):
        self.check()
        self.record("native_inspection_role_intent", cycle=cycle, phase="verifier", target=target,
                    source_sha256=self.source())
        observations = self.observe(f"{cycle}-verifier")
        current = next(row for row in observations if row["title"] == target)
        receipt = {"phase": "verifier", "outcome": "COMPLETED", "owned_tree": "DRAINED",
                   "verdict": "PASS" if all(row["actual"] == row["expected"] for row in observations) else "FAIL",
                   "target_verdict": "PASS" if current["actual"] == current["expected"] else "FAIL",
                   "observations": observations, "source_sha256": self.source()}
        self.record("native_inspection_role_result", cycle=cycle, target=target, **receipt)
        self.phases.append({"cycle": cycle, "phase": "verifier", "outcome": receipt["verdict"]})
        return receipt

    def evidence(self, cycle, comparison):
        self.progress = comparison["criteria"]
        self.cycles_checked = cycle
        self.record("native_inspection_cycle_progress", cycle=cycle, **comparison)

    def role(self, cycle, phase, target, prompt):
        task = TASKS[target]
        before = self.fixture_lines(task["file"]) if phase == "developer" else None
        response, recovered = self.dispatch(cycle, phase, target, prompt,
                                            test_pattern=rf"\bcall\s+{re.escape(task['file'])}\s+3\b",
                                            expected=task["expected"])
        if phase == "developer":
            response["diff"] = self.developer_diff(cycle, task, before, require_change=recovered)
        return response

    def dispatch(self, cycle, phase, target, prompt, *, test_pattern=None, expected="", tool_root=None, shell_tools=True,
                 decision_parser=None, raw_decision=False,
                 context="This is an isolated arithmetic fixture.", extra_read_roots=(), scratch_root=None,
                 images=(), image_evidence=()):
        self.check()
        self.check_image()
        if images and (phase != "reviewer" or shell_tools or not self.scope.get("visual_review")
                       or len(images) != len(image_evidence)):
            raise Blocked("Image attachments require the approved read-only Reviewer")
        limit = self.scope["role_tokens"][phase]
        graceful = "graceful_stop" in self.scope
        if self.calls >= self.scope["model_calls_max"]:
            raise Blocked("SIGNOFF_PENDING" if graceful and phase in {"reviewer", "manager-final"} else "TOKEN_LIMIT")
        if graceful:
            finishing = self.admit_graceful(phase, limit)
        elif self.scope["tokens"] - self.usage < limit:
            raise Blocked("TOKEN_LIMIT")
        writable = phase == "developer"
        instructions = public_instruction(phase)
        if instructions["sha256"] != self.scope["instructions"][phase]:
            raise Blocked("Approved role instructions changed")
        output_schema = None
        if images:
            from .visual_review import write_schema

            output_schema = write_schema(self.logs(f"{cycle}-visual-schema") / "review.json",
                                         [frame["id"] for frame in image_evidence])
        prompt = (f"{instructions['text']}\n{context} "
                  "Use only the approved fixture scope. No external actions.\n" + prompt)
        self.calls += 1
        self.record("native_inspection_role_intent", cycle=cycle, phase=phase, target=target,
                    role_call=self.calls, source_sha256=self.source(), prompt_sha256=digest(prompt),
                    **({"image_evidence": list(image_evidence)} if images else {}))
        role_log = self.logs(f"{cycle}-{phase}")
        self.check()
        self.owned_verified = False
        self.usage_complete = False
        process_result = "EXITED"
        output_limit = self.scope.get("model_output_bytes", 262144)
        role_started = monotonic()
        if graceful:
            policy = self.scope["graceful_stop"]
            timeout = min(self.scope["role_deadlines"][phase] + policy["role_grace_seconds"],
                          self.scope["expires"] - time())
            if finishing:
                timeout = min(timeout, policy["signoff_seconds"] - (role_started - self.signoff_started)
                              + policy["role_grace_seconds"])
        else:
            timeout = self.seconds(phase)
        activity = getattr(self, "activity", None)
        report = (lambda label: activity(cycle, phase, label)) if activity else None
        observed_check = (self.progress_check(phase, role_started, role_log, output_limit, report=report)
                          if graceful or report else self.check)
        stop_cause = None
        try:
            result = _observe(self.binary, _exec_argv(self.work, self.scope["model"], prompt, writable=writable,
                                                      tool_root=tool_root, shell_tools=shell_tools,
                                                      extra_read_roots=extra_read_roots, scratch_root=scratch_root,
                                                      images=images, output_schema=output_schema),
                              self.work, role_log, self.env, seconds=timeout, check=observed_check,
                              output_limit=output_limit, encoding="utf-8", process_limit=CODEX_DEVELOPER_PROCESS_LIMIT,
                              memory=self.scope.get("developer_memory", 512 * 1024**2) if writable else 512 * 1024**2)
        except OwnedMxcStopped as stopped:
            stop_cause = str(stopped)
            self.owned_verified = True
            process_result = "TERMINATED_AND_DRAINED"
            result = {"stdout": "", "exitCode": None, "job_active": 0}
            if stop_cause == "MXC probe timeout" or (graceful and stop_cause == "MODEL_IDLE_TIMEOUT"):
                # _observe raises this only after proving that the owned Job is empty.
                try:
                    self.check()
                    if not writable:
                        self.check_image()
                    raw = _read_observation(role_log / "probe.stdout", output_limit + 1)
                    if len(raw) <= output_limit:
                        result["stdout"] = raw.decode("utf-8")
                except (Blocked, OSError, UnicodeError):
                    pass
        self.owned_verified = result["job_active"] == 0
        if not self.owned_verified:
            raise Blocked("Owned Job is unresolved")
        stderr = role_log / "probe.stderr"
        details = _failure_evidence(result["stdout"], _read_observation(stderr, 65536) if stderr.is_file() else b"")
        try:
            response = _cli_evidence(result["stdout"], command_pattern=test_pattern or r"(?!)",
                                     expected=expected, retain_message=True)
        except (ValueError, TypeError, KeyError):
            response = {"provider_usage": "UNKNOWN"}
        usage = response["provider_usage"]
        self.usage_complete = type(usage) is dict
        tokens = usage["input_tokens"] + usage["output_tokens"] if self.usage_complete else None
        if tokens is not None:
            self.usage += tokens
            if graceful and finishing:
                self.signoff_tokens += tokens
        settled_process = result["exitCode"] == 0 or process_result == "TERMINATED_AND_DRAINED"
        recovered_developer = writable and process_result == "TERMINATED_AND_DRAINED"
        if recovered_developer and test_pattern and not response.get("successful_fixture_command_sha256"):
            settled_process = False
        outcome = ("REQUEST_UNKNOWN" if not settled_process or "message" not in response else
                   "USAGE_UNKNOWN" if tokens is None else "TOKEN_LIMIT" if tokens > limit and not graceful else "COMPLETED")
        message = response.get("message")
        receipt = {"outcome": outcome, "exit_code": result["exitCode"], "owned_tree": "DRAINED",
                   "process_result": process_result,
                   "provider_usage": usage, "message_sha256": digest(message),
                   "test_commands": response.get("successful_fixture_command_sha256", []),
                   "command_events": response.get("command_events", []), **details}
        if tokens is not None and tokens > limit:
            receipt["role_token_limit"] = limit
            receipt["role_reported_tokens"] = tokens
            receipt["role_token_overage"] = tokens - limit
        if stop_cause:
            receipt["process_stop_reason"] = stop_cause
        if phase == "manager-final" and isinstance(message, str):
            decision = message.strip()
            if decision_parser:
                try:
                    decision = decision_parser(decision)
                except Blocked:
                    decision = "INVALID"
            receipt["manager_decision"] = decision if decision in {"CONTINUE", "REWORK", "COMPLETED", "USER_TEST_REQUIRED"} else "INVALID"
        self.phases.append({"cycle": cycle, "phase": phase, **receipt})
        self.record("native_inspection_role_result", cycle=cycle, phase=phase, target=target, **receipt)
        if graceful:
            if tokens is not None and tokens >= limit:
                self.request_stop("ROLE_TOKENS", phase)
            if monotonic() - role_started >= self.scope["role_deadlines"][phase]:
                self.request_stop("ROLE_TIME", phase)
            self.soft_boundary(phase)
        if not shell_tools and receipt["command_events"]:
            raise Blocked("A role with shell disabled returned command execution events")
        if outcome != "COMPLETED":
            raise Blocked(outcome)
        self.check()
        if not writable:
            self.check_image()
        response["message"] = receipt["manager_decision"] if decision_parser and not raw_decision else message.strip()
        return response, recovered_developer

    def fixture_lines(self, filename):
        raw = _read_observation(self.work / filename, 513)
        if len(raw) > 512 or not raw.isascii():
            raise Blocked("Fixture source is outside the bounded text contract")
        return raw.decode("ascii").replace("\r\n", "\n").splitlines(keepends=True)

    def developer_diff(self, cycle, task, before, *, require_change=False):
        updated = snapshot(self.work)
        if snapshot(self.original) != self.original_image:
            raise Blocked("Protected fixture changed")
        changed = changes(self.image, updated, {task["file"]}) if self.image != updated else {}
        if require_change and not changed:
            raise Blocked("Recovered Developer did not change the approved file")
        filename = task["file"]
        cycle_diff = "".join(unified_diff(before, self.fixture_lines(filename),
                                          fromfile=f"a/{filename}", tofile=f"b/{filename}"))
        env = {**self.verify_env, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL",
               "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
        self.check()
        result = self.owned(lambda: _observe(
            _fixture_git_binary(), ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--", *SOURCE_NAMES],
            self.work, self.logs(f"{cycle}-diff"), env, seconds=min(5, self.seconds()),
            check=self.check, encoding="utf-8", output_limit=8192))
        if result["exitCode"] or result["job_active"] or snapshot(self.work) != updated:
            raise Blocked("Cannot bind Git diff to the observed revision")
        self.image = updated
        self.record("native_inspection_developer_diff", cycle=cycle, changed=changed,
                    source_sha256=self.source(), git_diff_sha256=digest(result["stdout"]),
                    cycle_diff_sha256=digest(cycle_diff))
        return cycle_diff


def run(ledger, archive, request, expires, seconds, tokens, cycles, approval=None, *, cancelled=lambda: False):
    from .runtime import Runner, require_admission

    with execution_slot() as abandoned:
        runner = Runner(ledger)
        events, head = runner._idle_diagnostic(abandoned)
        scope = _review(ledger, events, head, archive, request, expires, seconds, tokens, cycles)
        pin = digest(scope)
        if approval is None:
            return {"status": "NEEDS_OWNER_APPROVAL", "scope": scope, "approval_sha256": pin}
        if pin != approval:
            raise Blocked("Native cycle approval changed")
        auth = Path(scope["auth_home"])
        check_path(auth / "auth.json")
        _check_dedicated_config(auth, ledger.local_directory("temp"))
        for name in ("AGENTS.md", "AGENTS.override.md", "rules"):
            path = auth / name
            check_path(path)
            if path.exists():
                raise Blocked("Unexpected instructions or rules in the dedicated Codex home")

        fields = {"state": "DIAGNOSTIC", "request_id": request, "run_id": request,
                  "project_id": "native-cycle-fixture", "probe_kind": "GENERAL_CODEX_NATIVE_CYCLE",
                  "approval_sha256": pin}
        with _staging(ledger) as (lab, cleanup):
            session = _FixtureSession(runner, scope, lab, len(events), head, fields, cancelled)
            session.count, session.fence = ledger.append(
                session.count, session.fence, {**fields, "kind": "native_inspection_intent", "scope": scope,
                                              "workspace": lab.name}, admission=require_admission)
            cleanup["preserve"] = True
            try:
                session.prepare()
                result = _execute_cycles(scope, session.role, session.verify, session.source, session.evidence)
            except (Blocked, KeyboardInterrupt) as error:
                reason = "CANCELLED" if isinstance(error, KeyboardInterrupt) else str(error)
                outcome = reason if reason in {"CANCELLED", "TIME_LIMIT", "TOKEN_LIMIT", "REQUEST_UNKNOWN",
                                                "USAGE_UNKNOWN", "NO_PROGRESS"} else "BLOCKED"
                result = {"outcome": outcome, "reason": reason, "cycle_count": session.cycles_checked,
                          "criteria_status": session.progress}
            if not session.owned_verified:
                # Keep the intent unresolved so other entries require owner recovery.
                return {**fields, **result, "status": "RECOVERY_REQUIRED", "owned_tree": "UNRESOLVED"}
            preserve = (not session.usage_complete or not hasattr(session, "original_image")
                        or (session.calls > 0 and result["outcome"] in {"CANCELLED", "TIME_LIMIT", "REQUEST_UNKNOWN"}))
            if not preserve:
                _release_fixture_git_objects(session.work, session.original)
                cleanup["preserve"] = False
        if preserve:
            cleanup = {"status": "RETAINED_FOR_RECOVERY", "workspace": lab.name}
        if result["outcome"] == "COMPLETED" and cleanup.get("status") != "REMOVED":
            result["outcome"] = "CLEANUP_UNVERIFIED"
        receipt = {**fields, **result, "kind": "native_inspection_result", "status": result["outcome"],
                   "owned_tree": "DRAINED", "approval_count": 1, "roles": session.phases,
                   "model_turns_dispatched": session.calls, "known_reported_tokens": session.usage,
                   "usage_complete": session.usage_complete, "staging_cleanup": cleanup}
        ledger.append(session.count, session.fence, receipt, allow_shutdown=True)
        return receipt
