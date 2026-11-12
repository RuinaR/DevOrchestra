"""Regression tests for the new registered execution capabilities."""
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from devorchestra import project_tools
from devorchestra.lease import Blocked
from devorchestra.native_developer_trial import _cli_evidence, trial_flags
from devorchestra.registered_cycle import RegisteredSession
from devorchestra.replica_evidence import snapshot


class ProjectToolsTests(unittest.TestCase):
    def test_portable_tools_are_exposed_only_during_developer_and_reuse_one_download(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            work, scratch = base / "replica", base / "scratch"
            work.mkdir()
            scratch.mkdir()
            (work / "source.txt").write_text("original", encoding="utf-8")
            archive = base / "fixture.zip"
            with ZipFile(archive, "w") as package:
                package.writestr("bin/tool.cmd", "@echo fixture")
            tool = {"name": "fixture", "url": "https://example.com/fixture.zip",
                    "sha256": sha256(archive.read_bytes()).hexdigest()}
            image = snapshot(work)
            scope = {"permissions": ["portable-tools"], "install": True, "tool_packages": [tool],
                     "model_calls_max": 4, "role_tokens": {"developer": 1}, "tokens": 4}
            session = SimpleNamespace(work=work, scratch=scratch, scope=scope, calls=0, usage=0,
                                      owned_verified=True, check=Mock(), check_image=Mock(), record=Mock())

            def downloaded(package, path, check):
                self.assertEqual(package, tool)
                check()
                copyfile(archive, path)
                return path.stat().st_size

            with patch.object(project_tools, "download", side_effect=downloaded) as transfer:
                with RegisteredSession.portable_tools(session, 1, "manager-planning") as root:
                    self.assertIsNone(root)
                for cycle in (1, 2):
                    with RegisteredSession.portable_tools(session, cycle, "developer") as root:
                        self.assertEqual(root.parent, work)
                        self.assertEqual((root / "fixture/bin/tool.cmd").read_text(), "@echo fixture")
                        self.assertEqual(snapshot(work, owned_staging=root), image)
                    self.assertFalse(root.exists())
                    self.assertEqual(snapshot(work), image)
                transfer.assert_called_once()
            self.assertEqual(len([c for c in session.record.call_args_list
                                  if c.args[0] == "native_inspection_project_tools_cleanup"]), 2)

    def test_zip_traversal_cannot_write_outside_the_replica_tools_directory(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            archive, root = base / "fixture.zip", base / "tools"
            root.mkdir()
            with ZipFile(archive, "w") as package:
                package.writestr("../escaped.txt", "unexpected")
            with self.assertRaises(Blocked):
                project_tools.extract(archive, root, lambda: None)
            self.assertFalse((base / "escaped.txt").exists())

    def test_research_events_require_grant_without_opening_shell_network(self):
        search = {"type": "web_search", "id": "item_1", "query": "public fixture documentation"}
        lines = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"},
                 {"type": "item.completed", "item": search},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "Plan"}},
                 {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}]
        stdout = "\n".join(map(json.dumps, lines))
        rejected = _cli_evidence(stdout, retain_settled=True)
        self.assertIn("tool_evidence_error", rejected)
        accepted = _cli_evidence(stdout, web_research=True)
        self.assertEqual(len(accepted["web_search_events"]), 1)
        flags = trial_flags(Path("replica"), web_research=True, shell_tools=False)
        self.assertIn('web_search="live"', flags)
        self.assertIn("permissions.developer-trial.network.enabled=false", flags)
        self.assertIn("features.shell_tool=false", flags)
