from pathlib import Path
import unittest

from devorchestra.gui_exe import embedded_build
from devorchestra.gui_project import parse
from devorchestra.lease import Blocked
from devorchestra.ledger import canonical, digest
from devorchestra.subscription_gui import immutable_form, validate_change


class SubscriptionGuiTests(unittest.TestCase):
    def test_gui_model_can_change_only_opened_and_build_changes_with_data(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "native-form" / "gui-test.json"
        original = parse(example.read_bytes())
        frozen = {"immutable_form_sha256": immutable_form(original), "scenario_sha256": digest(original["scenes"])}
        replacement = {**original, "opened": 'New status "quoted"'}
        self.assertEqual(replacement, validate_change(canonical(replacement).encode(), frozen))
        self.assertNotEqual(embedded_build(original)[1:], embedded_build(replacement)[1:])
        replacement["scenes"] = [{**item, "expected": "A forged observation"} for item in original["scenes"]]
        with self.assertRaisesRegex(Blocked, "owner-approved tests"):
            validate_change(canonical(replacement).encode(), frozen)
