import unittest
from unittest.mock import Mock, patch

from devorchestra.lease import Blocked
from devorchestra import mxc_python


class MxcPythonTests(unittest.TestCase):
    def test_manifest_rejects_arbitrary_arguments_before_binary_inspection(self):
        config = {"kind": mxc_python.TOOL, "launcher": "codex.exe", "python": "python.exe",
                  "python_sha256": "a" * 64, "arguments": ["-c", "arbitrary"]}
        with patch.object(mxc_python, "verify_app_server_binary") as verify:
            with self.assertRaisesRegex(Blocked, "Invalid fixed MXC Python manifest"):
                mxc_python.manifest(config)
        verify.assert_not_called()

    def test_unapproved_python_call_is_rejected_before_owned_launch(self):
        approved = {"kind": mxc_python.TOOL, "launcher": "fixture", "python": "fixture",
                    "python_sha256": "a" * 64}
        sources = {"fixture.py": b"def value(x):\n    return abs(x)\n"}
        tests = {"fixture.py": {"function": "value", "vectors": [[1, 1]]}}
        with patch.object(mxc_python, "manifest", return_value=approved), patch.object(mxc_python, "_observe") as launch:
            with self.assertRaisesRegex(Blocked, "unsupported Python expression"):
                mxc_python.run(Mock(), approved, sources, tests, lambda: None, 20)
        launch.assert_not_called()
