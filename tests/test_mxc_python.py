import unittest
from unittest.mock import Mock, patch

from devorchestra.lease import Blocked
from devorchestra import mxc_python, python_modules


class MxcPythonTests(unittest.TestCase):
    def test_local_import_requires_an_approved_direct_call(self):
        graph = {"entry.py": ["helper.py"], "helper.py": []}
        specs = {"entry.py": {"function": "entry"}, "helper.py": {"function": "helper"}}
        sources = {"helper.py": b"def helper(value):\n    return value\n",
                   "entry.py": b"from helper import helper\ndef entry(value):\n    return helper(value)\n"}
        python_modules.validate_sources(sources, specs, graph, "entry.py")
        for source in (
            b"from helper import helper\nprint(0)\ndef entry(value):\n    return helper(value)\n",
            b"from helper import helper as call\ndef entry(value):\n    return call(value)\n",
            b"def entry(value):\n    return __import__('helper').helper(value)\n",
            b"from helper import helper\ndef entry(value):\n    return value\n",
        ):
            with self.subTest(source=source), self.assertRaises(Blocked):
                python_modules.validate_sources({**sources, "entry.py": source}, specs, graph, "entry.py")

    def test_import_contract_rejects_cycles_and_standard_library_shadowing(self):
        for graph in ({"entry.py": ["helper.py"], "helper.py": ["entry.py"]},
                      {"entry.py": ["json.py"], "json.py": []},
                      {"entry.py": [], "helper.py": []}):
            with self.subTest(graph=graph), self.assertRaises(Blocked):
                python_modules.validate_graph(graph, "entry.py", graph)

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
