import json
import unittest

from devorchestra.mxc_diagnostic import MAX_STREAM_BYTES, native_probe_receipt


def receipt(**changes):
    values = dict(preflight=True, cli_started=True, cli_exit=1, streams_complete=True)
    return native_probe_receipt(**(values | changes))


class NativeDiagnosticTests(unittest.TestCase):
    def test_exit_one_without_recognized_stderr_does_not_infer_inner_start(self):
        result = receipt(stderr=b"private detail from C:\\Users\\synthetic\\auth.json")
        self.assertEqual("UNRECOGNIZED_STDERR", result["error_type"])
        self.assertEqual("CONFIRMED", result["stages"]["CLI_EXIT"])
        for stage in ("MXC_SETUP", "IMAGE_PREPARATION", "COMMAND_START", "COMMAND_RESULT"):
            self.assertEqual("UNKNOWN", result["stages"][stage])
        self.assertNotIn("synthetic", json.dumps(result))

    def test_known_failures_are_scoped_and_numeric_codes_allowlisted(self):
        cases = (
            (b"Error: failed to prepare MXC sandbox: MXC requires nonempty Unicode host SystemDrive",
             "MXC_SETUP", "HOST_SYSTEMDRIVE_REQUIRED", None),
            (b"cannot locate required Windows platform directory: HRESULT 0x80070003",
             "MXC_SETUP", "PLATFORM_DIRECTORY_FAILED", "0x80070003"),
            (b"Error: MXC launcher: failed to create the process security environment: "
             b"CreateProcessSecurityEnvironment failed (HRESULT = 0x80070020)",
             "MXC_SETUP", "SECURITY_ENVIRONMENT_FAILED", "0x80070020"),
            (b"CreateProcessInSandbox failed: error 203 (0xCB)",
             "COMMAND_START", "SANDBOX_PROCESS_CREATE_FAILED", 203),
            (b"CreateProcessInSandbox failed: error 9999 (0x270F)",
             "COMMAND_START", "SANDBOX_PROCESS_CREATE_FAILED", None),
            (b"waiting for the MXC command", "COMMAND_RESULT", "MXC_WAIT_FAILED", None),
        )
        for stderr, stage, error, code in cases:
            with self.subTest(error=error):
                result = receipt(stderr=stderr)
                self.assertEqual("REPORTED_FAILURE", result["stages"][stage])
                self.assertEqual((error, code), (result["error_type"], result["reported_error_code"]))

    def test_conflicting_oversized_and_spoofed_text_fail_closed(self):
        for stderr, category in (
            (b"native MXC is unavailable on this Windows build\nwaiting for the MXC command",
             "CONFLICTING_REPORTS"),
            (b"x" * (MAX_STREAM_BYTES + 1), "STDERR_LIMIT_EXCEEDED"),
            (b"credential=waiting for the MXC command", "UNRECOGNIZED_STDERR"),
        ):
            result = receipt(stderr=stderr, cli_exit=0, stdout=b"MXC_NATIVE_OK\n")
            self.assertEqual((category, "FAIL"), (result["error_type"], result["result"]))
            self.assertEqual("UNKNOWN", result["stages"]["COMMAND_RESULT"])

    def test_marker_scope_and_owned_cli_exit_are_independent(self):
        result = receipt(cli_exit=0, stdout=b"MXC_NATIVE_OK\r\n")
        self.assertEqual(("PASS", "NATIVE_SELF_TEST_ONLY"), (result["result"], result["scope"]))
        self.assertEqual("UNKNOWN", result["stages"]["IMAGE_PREPARATION"])
        self.assertEqual("UNKNOWN", result["stages"]["MXC_SETUP"])
        self.assertEqual("FAIL", receipt(stdout=b"MXC_NATIVE_OK\n")["result"])
        incomplete = receipt(cli_exit=0, streams_complete=False, stdout=b"MXC_NATIVE_OK\n")
        self.assertEqual("NOT_VERIFIED", incomplete["result"])
        self.assertEqual("UNKNOWN", receipt(cli_exit=None)["stages"]["CLI_EXIT"])
        with self.assertRaises(ValueError):
            receipt(cli_started=False)
        launch_failure = receipt(cli_started=False, cli_exit=None, streams_complete=False)
        self.assertEqual("CLI_START_FAILED", launch_failure["error_type"])
