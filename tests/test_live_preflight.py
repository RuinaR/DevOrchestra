"""Preflight rejects requests without consulting the host or an execution adapter."""
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from devorchestra.__main__ import main
from devorchestra.policy import FORBIDDEN_LIVE_CAPABILITIES, live_preflight


class LivePreflightTests(unittest.TestCase):
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
