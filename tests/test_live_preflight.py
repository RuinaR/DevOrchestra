"""Preflight rejects requests without consulting the host or an execution adapter."""
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from devorchestra.__main__ import main
from devorchestra.adapter import RoleRequest, WindowsNativeCodexAdapter
from devorchestra.lease import Blocked
from devorchestra.policy import FORBIDDEN_LIVE_CAPABILITIES, live_preflight


class LivePreflightTests(unittest.TestCase):
    def test_native_default_and_explicit_backends_cannot_fall_back(self):
        self.assertEqual("windows-native-codex", live_preflight()["backend"])
        for backend in ("windows-native-codex", "virtualbox", "explicit-mxc", "invalid"):
            with self.subTest(backend=backend):
                result = live_preflight(backend=backend)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIs(False, result["automatic_fallback"])
                self.assertEqual(None if backend == "invalid" else backend, result["backend"])
                self.assertEqual("UNKNOWN_BACKEND" if backend == "invalid"
                                 else "ISOLATION_UNVERIFIED", result["reason"])

    def test_native_role_adapter_denies_before_using_request(self):
        request = Mock(spec=RoleRequest)
        with self.assertRaisesRegex(Blocked, "ISOLATION_UNVERIFIED"):
            WindowsNativeCodexAdapter().execute(request)
        self.assertEqual([], request.mock_calls)

    def test_prohibited_requirements_take_precedence(self):
        for capability in FORBIDDEN_LIVE_CAPABILITIES:
            with self.subTest(capability=capability):
                decision = live_preflight(("build", "unrecognized", capability, capability))
                self.assertEqual("BLOCKED", decision["status"])
                self.assertEqual("FORBIDDEN_CAPABILITY", decision["reason"])
                self.assertEqual((capability,), decision["forbidden_capabilities"])

    def test_unknown_and_clean_declarations_cannot_unlock_execution(self):
        self.assertEqual("UNKNOWN_CAPABILITY", live_preflight(("unrecognized",))["reason"])
        for required in ((), ("project-read", "build", "test")):
            decision = live_preflight(required)
            self.assertEqual("BLOCKED", decision["status"])
            self.assertEqual("ISOLATION_UNVERIFIED", decision["reason"])
            self.assertEqual("NOT_VERIFIED", decision["noninterference"])

    def test_cli_reports_denial_without_opening_ledger(self):
        output = StringIO()
        with patch("sys.argv", ["devorchestra", "live-preflight", "--requires", "screen-capture"]), \
                patch("devorchestra.__main__.Ledger", side_effect=AssertionError("opened ledger")), \
                redirect_stdout(output):
            self.assertEqual(2, main())
        self.assertEqual(["screen-capture"], json.loads(output.getvalue())["forbidden_capabilities"])
