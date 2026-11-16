import json
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import unittest

from devorchestra import development_profiles, preview_play
from devorchestra.ledger import digest
from devorchestra.lease import Blocked


LIMITS = dict(steps=6, seconds=60, hold=1000, fps=30, evidence=4)


class PreviewPlayTests(unittest.TestCase):
    def test_bounded_sequence_accepts_held_keys_click_and_observation(self):
        actions = [{"action": "keys", "keys": ["D"], "ms": 1000},
                   {"action": "wait", "ms": 250},
                   {"action": "keys", "keys": ["RIGHT", "SPACE"], "ms": 120},
                   {"action": "click", "x": 220, "y": 300}]
        self.assertEqual(actions, preview_play.input_plan(json.dumps(actions), **LIMITS))

    def test_reject_unapproved_or_overlong_actions(self):
        cases = [{"action": "keys", "keys": ["LWIN"], "ms": 100},
                 {"action": "keys", "keys": ["D"], "ms": 1001},
                 {"action": "keys", "keys": ["D", "D"], "ms": 100},
                 {"action": "click", "x": 2048, "y": 0},
                 {"action": "shell", "command": "tool"}]
        for action in cases:
            with self.subTest(action=action), self.assertRaises(Blocked):
                preview_play.input_plan(json.dumps([action]), **LIMITS)
        with self.assertRaises(Blocked):
            preview_play.input_plan(json.dumps([{"action": "wait", "ms": 20000}] * 3), **LIMITS)

    def test_modified_scope_cannot_reuse_approval(self):
        scope = {"project_id": "sample", "hold_ms": 500}
        approval = digest(scope)
        scope["hold_ms"] = 1000
        with self.assertRaisesRegex(Blocked, "approval changed"):
            preview_play.run(SimpleNamespace(ledger=None), scope, approval, cancelled=lambda: False)

    def test_saved_preference_only_round_trips_as_project_data(self):
        with TemporaryDirectory() as tmp:
            ledger = SimpleNamespace(local_directory=lambda *args, **kw: Path(tmp))
            preferences = {"approved_criterion": "C1", "desktop_hold": "500",
                           "desktop_preference": True, "allow_documentation": False}
            development_profiles.save(ledger, "sample", preferences)
            self.assertEqual(preferences, development_profiles.read(ledger, "sample"))
            self.assertIsNone(development_profiles.read(ledger, "another"))
