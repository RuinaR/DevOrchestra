"""Allowlisted receipt for the fixed native probe. Does not launch anything."""
import re


MAX_STREAM_BYTES = 16_384
MARKER = b"MXC_NATIVE_OK"


def diagnostic_unresolved(events):
    latest = next((event for event in reversed(events) if event["kind"] in (
        "mxc_diagnostic_intent", "mxc_diagnostic_result",
    )), None)
    return latest is not None and (
        latest["kind"] == "mxc_diagnostic_intent" or latest.get("owned_tree") != "NOT_CREATED"
    )

# Exact phrases from prior observations or upstream source. A match is a
# reported failure, not independently verified backend telemetry.
KNOWN_LINES = {
    b"MXC requires nonempty Unicode host SystemDrive": ("MXC_SETUP", "HOST_SYSTEMDRIVE_REQUIRED"),
    b"MXC requires nonempty Unicode host LOCALAPPDATA": ("MXC_SETUP", "HOST_LOCALAPPDATA_REQUIRED"),
    b"native MXC is unavailable on this Windows build": ("MXC_SETUP", "MXC_UNAVAILABLE"),
    b"invalid MXC launcher request": ("MXC_SETUP", "INVALID_LAUNCH_REQUEST"),
    b"this Windows build cannot enforce native MXC deny paths": ("MXC_SETUP", "DENY_PATHS_UNSUPPORTED"),
    b"waiting for the MXC command": ("COMMAND_RESULT", "MXC_WAIT_FAILED"),
}
KNOWN_PREFIXES = {
    b"failed to prepare MXC sandbox: ": ("MXC_SETUP", "MXC_PREPARATION_FAILED"),
    b"cannot locate required Windows platform directory: HRESULT ": ("MXC_SETUP", "PLATFORM_DIRECTORY_FAILED"),
}
HRESULTS = {b"0x80070002", b"0x80070003", b"0x80070005", b"0x80070020"}
PLATFORM_ERROR = re.compile(rb"cannot locate required Windows platform directory: HRESULT (0x[0-9a-fA-F]{8})")
SECURITY_ENV_ERROR = re.compile(
    rb"(?:MXC launcher: failed to create the process security environment: )?"
    rb"CreateProcessSecurityEnvironment failed \(HRESULT = (0x[0-9a-fA-F]{8})\)"
)
CREATE_ERROR = re.compile(rb"CreateProcessInSandbox failed: error ([0-9]{1,5}) \(0x([0-9a-fA-F]{1,8})\)")
WIN32_ERRORS = {2, 3, 5, 32, 87, 193, 203, 577, 1260, 1314}


def _classify(stderr):
    if not stderr:
        return None, "NO_STDERR", None
    if len(stderr) > MAX_STREAM_BYTES:
        return None, "STDERR_LIMIT_EXCEEDED", None
    hits = set()
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith(b"Error: "):
            line = line[len(b"Error: "):]
        wrapper = b"failed to prepare MXC sandbox: "
        inner = line[len(wrapper):] if line.startswith(wrapper) else line
        if inner in KNOWN_LINES:
            hits.add((*KNOWN_LINES[inner], None))
            continue
        match = PLATFORM_ERROR.fullmatch(inner)
        if match:
            code = match[1].lower()
            hits.add(("MXC_SETUP", "PLATFORM_DIRECTORY_FAILED",
                      code.decode("ascii") if code in HRESULTS else None))
            continue
        match = SECURITY_ENV_ERROR.fullmatch(inner)
        if match:
            code = match[1].lower()
            hits.add(("MXC_SETUP", "SECURITY_ENVIRONMENT_FAILED",
                      code.decode("ascii") if code in HRESULTS else None))
            continue
        match = CREATE_ERROR.fullmatch(inner)
        if match and int(match[1]) == int(match[2], 16):
            code = int(match[1])
            hits.add(("COMMAND_START", "SANDBOX_PROCESS_CREATE_FAILED", code if code in WIN32_ERRORS else None))
            continue
        for prefix, (stage, category) in KNOWN_PREFIXES.items():
            if line.startswith(prefix):
                hits.add((stage, category, None))
    if len(hits) == 1:
        return hits.pop()
    return None, "CONFLICTING_REPORTS" if hits else "UNRECOGNIZED_STDERR", None


def native_probe_receipt(*, preflight, cli_started, cli_exit, streams_complete, stdout=b"", stderr=b""):
    """Accept observations from the trusted host of the *owned* CLI only.

    The outer exit code is not a Win32 error from opening a file, or evidence
    that the inner command started. No input strings are emitted verbatim.
    """
    if any(value is not None and type(value) is not bool for value in (preflight, cli_started)):
        raise ValueError("Invalid diagnostic observation")
    if type(streams_complete) is not bool or type(stdout) is not bytes or type(stderr) is not bytes:
        raise ValueError("Invalid diagnostic capture")
    if cli_exit is not None and (type(cli_exit) is not int or not -(2**31) <= cli_exit < 2**32):
        raise ValueError("Invalid CLI exit code")
    if ((cli_started is True and preflight is not True)
            or (cli_started is not True and (cli_exit is not None or streams_complete or stdout or stderr))):
        raise ValueError("Inconsistent diagnostic observations")

    stages = dict.fromkeys(("PREFLIGHT", "CLI_START", "MXC_SETUP", "IMAGE_PREPARATION",
                            "COMMAND_START", "COMMAND_RESULT", "CLI_EXIT"), "UNKNOWN")
    for stage, observed in (("PREFLIGHT", preflight), ("CLI_START", cli_started)):
        if observed is not None:
            stages[stage] = "CONFIRMED" if observed else "FAILED"
    if cli_exit is not None:
        stages["CLI_EXIT"] = "CONFIRMED"
    if preflight is False:
        failure_stage, error, code = None, "PREFLIGHT_FAILED", None
    elif cli_started is False:
        failure_stage, error, code = None, "CLI_START_FAILED", None
    elif streams_complete:
        failure_stage, error, code = _classify(stderr)
    else:
        failure_stage, error, code = None, "CAPTURE_INCOMPLETE", None
    if failure_stage:
        stages[failure_stage] = "REPORTED_FAILURE"
    marker = streams_complete and stdout in (MARKER + b"\n", MARKER + b"\r\n")
    if marker:
        stages["COMMAND_START"] = "MARKER_OBSERVED"
    passed = marker and cli_exit == 0 and not stderr
    if passed:
        stages["COMMAND_RESULT"] = "SELF_TEST_CONFIRMED"
    result = "PASS" if passed else "FAIL" if (
        preflight is False or cli_started is False or cli_exit not in (None, 0)
        or (cli_exit == 0 and streams_complete)
    ) else "NOT_VERIFIED"
    return {
        "schema_version": 1, "scope": "NATIVE_SELF_TEST_ONLY", "result": result,
        "stages": stages, "cli_exit_code": cli_exit, "marker_observed": marker,
        "error_type": error, "reported_error_code": code, "capture_complete": streams_complete,
    }
