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
from devorchestra.registered_cycle import PlanFormatError, RegisteredSession, _parse_plan
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
            item = {"repository": "example/tool", "ref": "v1", "asset": "tool.zip"}
            tool = {"url": "https://github.com/example/tool/releases/download/v1/tool.zip",
                    "sha256": sha256(archive.read_bytes()).hexdigest()}
            image = snapshot(work)
            scope = {"permissions": ["portable-tools"], "install": True,
                     "portable_source_policy": project_tools.GITHUB_POLICY,
                     "model_calls_max": 4, "role_tokens": {"developer": 1}, "tokens": 4}
            session = SimpleNamespace(work=work, scratch=scratch, scope=scope, calls=0, usage=0,
                                      owned_verified=True, check=Mock(), check_image=Mock(), record=Mock())

            def downloaded(package, path, check):
                self.assertEqual(package, tool)
                check()
                copyfile(archive, path)
                return {"bytes": path.stat().st_size, "sha256": tool["sha256"]}

            with (patch.object(project_tools, "github_package", return_value=tool) as resolve,
                  patch.object(project_tools, "download_github", side_effect=downloaded) as transfer):
                with RegisteredSession.portable_tools(session, 1, "manager-planning", [item]) as root:
                    self.assertIsNone(root)
                with RegisteredSession.portable_tools(session, 1, "developer", []) as root:
                    self.assertIsNone(root)
                for cycle in (1, 2):
                    with RegisteredSession.portable_tools(session, cycle, "developer", [item]) as root:
                        self.assertEqual(root.parent, work)
                        installed, = root.glob("github-*/bin/tool.cmd")
                        self.assertEqual(installed.read_text(), "@echo fixture")
                        self.assertEqual(snapshot(work, owned_staging=root), image)
                    self.assertFalse(root.exists())
                    self.assertEqual(snapshot(work), image)
                transfer.assert_called_once()
                resolve.assert_called_once()
            self.assertEqual(len([c for c in session.record.call_args_list
                                  if c.args[0] == "native_inspection_project_tools_cleanup"]), 2)

    def test_planner_chooses_public_github_refs_without_a_static_tool_list(self):
        item = {"repository": "example/tool", "ref": "v1", "asset": "tool.zip"}
        base = {"criterion": "C1", "mode": "edit", "plan": "Run a local script"}
        for selection in ([], [item], [{**item, "ref": "a" * 40, "asset": ""}]):
            self.assertEqual(_parse_plan(json.dumps({**base, "tools": selection}), ["C1"],
                                         github_tools=True)["tools"], selection)
        for selection in (["https://example.com/file.zip"], [item, item], [{**item, "asset": "../tool.zip"}],
                          [{**item, "ref": "main", "asset": ""}]):
            with self.subTest(selection=selection), self.assertRaises(PlanFormatError):
                _parse_plan(json.dumps({**base, "tools": selection}), ["C1"], github_tools=True)
        with self.assertRaises(PlanFormatError):
            _parse_plan(json.dumps({**base, "tools": [item]}), ["C1"])

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
