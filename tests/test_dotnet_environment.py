import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from devorchestra.dotnet_environment import select
from devorchestra.lease import Blocked


class DotnetEnvironmentTests(unittest.TestCase):
    def test_project_policy_selects_sdk_and_independent_runtime(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            replica = root / "replica"
            replica.mkdir()
            for sdk, runtime in (("9.0.103", "9.0.1"), ("10.0.202", "10.0.2")):
                compiler = root / "sdk" / sdk / "Roslyn" / "bincore"
                compiler.mkdir(parents=True)
                (compiler / "csc.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
                    "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": runtime},
                }}), encoding="ascii")
                (root / "shared" / "Microsoft.NETCore.App" / runtime).mkdir(parents=True)
                (root / "packs" / "Microsoft.NETCore.App.Ref" / runtime).mkdir(parents=True)
                (root / "host" / "fxr" / runtime).mkdir(parents=True)
            self.assertEqual("10.0.202", select(root, replica)["sdk"])
            global_json = replica / "global.json"
            global_json.write_text('{"sdk":{"version":"9.0.101","rollForward":"latestPatch"}}', encoding="ascii")
            selected = select(root, replica)
            self.assertEqual(("9.0.103", "9.0.1", "net9.0"),
                             (selected["sdk"], selected["runtime"].name, selected["target"]))
            global_json.write_text('{"sdk":{"version":"9.0.101","rollForward":"disable"}}', encoding="ascii")
            with self.assertRaisesRegex(Blocked, "No installed"):
                select(root, replica)
