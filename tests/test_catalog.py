from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from devorchestra.adapter import MockEvent
from devorchestra.catalog import Catalog, latest_requirements, new_project, read_frozen_requirements
from devorchestra.gui_workspace import inspect_folder
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger
from devorchestra.projection import ProjectionReader
from devorchestra.runtime import Runner


@unittest.skipUnless(os.name == "nt", "Windows controller contract")
class CatalogContracts(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(tmp.cleanup)
        self.ledger = Ledger(Path(tmp.name) / "run.sqlite3")
        self.runner = Runner(self.ledger)
        self.runner.command("init")
        self.catalog = Catalog(self.ledger)
        self.alpha = new_project("alpha", "Alpha", "Alpha fixture goal")
        self.alpha["local_path"] = r"Z:\unavailable\synthetic-alpha"
        self.alpha["requested_scope"]["tools"] = ["future-build"]
        self.alpha["roles"]["Developer"]["model"] = "unverified-model"

    def start(self, project="alpha"):
        self.run = self.runner.command("start", project=project)
        self.run_id = self.run["run_id"]
        self.assertEqual("NEED_APPROVAL", self.command("step")["state"])

    def test_quick_requirement_revision_retains_original_and_orders_new_scope(self):
        folder = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(folder.cleanup)
        original = Path(folder.name)
        external = original / "feature.txt"
        external.write_text("Old evidence", encoding="utf-8")
        self.catalog.save_project({**self.alpha, "local_path": str(original)})
        external_ref = self.catalog.register_requirement("alpha", "feature.txt", "FEATURE")
        self.catalog.write_local_requirement("alpha", "other.md", "Other", "BUG", title="Other")
        revision = self.catalog.write_local_requirement(
            "alpha", "feature.txt", "Updated", "FEATURE", title="A feature",
            priority="high", expected_revision=external_ref["id"],
        )
        view = self.catalog.read()
        self.assertEqual(external_ref["id"], revision["supersedes"])
        self.assertEqual(2, revision["version"])
        self.assertEqual(revision, latest_requirements(view, "alpha")[0])
        self.assertEqual(["Old evidence", "Updated"], [doc["text"] for doc in read_frozen_requirements(
            view, "alpha", [external_ref, revision], self.ledger.state_boundary)])
        with self.assertRaisesRegex(Blocked, "최신 개정"):
            self.catalog.write_local_requirement(
                "alpha", "feature.txt", "Stale", "FEATURE", expected_revision=external_ref["id"])
        self.assertEqual("Old evidence", external.read_text(encoding="utf-8"))

    def test_folder_hints_do_not_contact_remote_or_accept_conflicting_config(self):
        root = self.ledger.path.parent / "original"
        root.mkdir()
        (root / "pyproject.toml").touch()
        self.assertEqual(("folder", "", "", ["Python · pyproject.toml"]), inspect_folder(root))
        git = root / ".git"
        git.mkdir()
        (git / "HEAD").write_text("ref: refs/heads/work/pilot\n", encoding="ascii")
        local = git / "config"
        url = "https://github.com/sample/example.git"
        remote = f'[remote "origin"]\n\turl = {url}\n'
        local.write_text(remote, encoding="utf-8")
        self.assertEqual(("github", url, "work/pilot"), inspect_folder(root)[:3])
        local.write_text(remote + "\tpushurl = https://example.org/test.git\n", encoding="utf-8")
        self.assertEqual(("git", "", ""), inspect_folder(root)[:3])

    def command(self, action):
        return self.runner.command(action, expected_run=self.run_id)

    def approve(self):
        approval = self.command("approve")
        self.command("step")
        return approval

    def test_catalog_revisions_and_registered_history_do_not_mix(self):
        revision = self.catalog.save_project(self.alpha)
        beta = new_project("beta", "Beta", "Beta fixture goal")
        self.catalog.save_project(beta)
        self.start()
        with self.assertRaises(Blocked):
            self.runner.command("start", project="beta")
        updated = {**self.alpha, "goal": "Changed for next run"}
        self.catalog.save_project(updated, revision)
        with self.assertRaisesRegex(Blocked, "Stale"):
            self.catalog.save_project(self.alpha, revision)
        self.approve()
        self.assertEqual("COMPLETED", self.command("run")["state"])
        reader = ProjectionReader(self.ledger)
        alpha = reader.read("alpha")
        self.assertEqual("Alpha fixture goal", alpha["run"]["goal"])
        self.assertEqual("Changed for next run", alpha["registration"]["data"]["goal"])
        self.assertNotIn("unavailable", str(alpha["events"]))
        developer = alpha["role_cards"]["Developer"]["finish"]
        self.assertEqual(("mock-v1", "unverified-model"), (developer["model"], developer["requested_model"]))
        self.assertTrue(developer["scope_released"])
        self.assertEqual("DENIED", developer["tool_audit"][0]["outcome"])
        self.assertEqual("SYNTHETIC", developer["usage_kind"])
        self.assertNotIn("future-build", str(alpha["events"]))
        began = [e for e in alpha["events"] if e["kind"] == "role_started"]
        ended = [e for e in alpha["events"] if e["kind"] == "role_finished"]
        self.assertEqual(5, len({e["context_id"] for e in began}))
        self.assertEqual([e["handoff_ref"] for e in ended[:-1]],
                         [e["input_refs"][0] for e in began[1:]])
        view = reader.read("beta")
        self.assertEqual(("IDLE", [], None), (view["project_state"], view["events"], view["active_role"]))
        self.assertIsNone(view["role_cards"]["Developer"]["finish"])

    def test_invalid_settings_and_role_budget(self):
        for data in (
            {**self.alpha, "budget": {"seconds": True, "tokens": 10, "cost_microunits": 0}},
            {**self.alpha, "roles": {**self.alpha["roles"], "Verifier": self.alpha["roles"]["Developer"]}},
            {**self.alpha, "repository": "https://account@github.com/owner/repo"},
        ):
            with self.assertRaises(ValueError):
                self.catalog.save_project(data)
        self.alpha["roles"]["Manager"]["budget"]["tokens"] = 0
        self.catalog.save_project(self.alpha)
        self.start()
        self.approve()
        self.assertEqual("TOKEN_LIMIT", self.command("step")["reason"])
        self.assertFalse(any(e["kind"] == "role_started" for e in self.ledger.read()))

    def test_effective_global_budget_and_approval_expiry(self):
        self.catalog.save_project(self.alpha)
        self.catalog.save_limits({"seconds": 600, "tokens": 2, "cost_microunits": 0}, 1)
        self.start()
        self.assertEqual(2, self.run["token_limit"])
        approval = self.approve()
        with patch("devorchestra.runtime.time", return_value=approval["expires"] + 1):
            self.assertEqual("APPROVAL_EXPIRED", self.command("step")["reason"])
        with self.assertRaises(Blocked):
            self.command("run")

    def test_role_elapsed_budget_blocks_handoff(self):
        self.alpha["roles"]["Manager"]["budget"]["seconds"] = 1
        self.catalog.save_project(self.alpha)
        self.start()
        self.approve()
        with patch("devorchestra.runtime.monotonic", side_effect=(100, 101.5)):
            event = self.command("step")
        self.assertEqual(("BLOCKED", "TIME_LIMIT"), (event["state"], event["reason"]))
        with self.assertRaises(Blocked):
            self.command("step")

    def test_incomplete_stream_preserves_unknown_and_blocks_restart(self):
        self.catalog.save_project(self.alpha)
        self.start()
        self.approve()
        with patch("devorchestra.adapter.MockAdapter.stream",
                   return_value=(MockEvent(1, "tool_denied", {}),)):
            with self.assertRaisesRegex(Blocked, "unordered"):
                self.command("step")
        self.runner = Runner(Ledger(self.ledger.path))
        with self.assertRaises(Blocked):
            self.command("step")
        view = ProjectionReader(self.ledger).read("alpha")
        self.assertIsNone(view["active_role"])
        self.assertEqual("Manager", view["unconfirmed_role"])
        self.assertEqual("UNKNOWN", self.command("recover")["result"])

    def test_corrupt_catalog_blocks_run_admission(self):
        self.catalog.save_project(self.alpha)
        with closing(sqlite3.connect(self.catalog.ledger.path)) as db, db:
            db.execute("DELETE FROM events WHERE seq=1")
        with self.assertRaises(Blocked):
            self.runner.command("start", project="alpha")
        self.assertEqual([], self.ledger.read())
