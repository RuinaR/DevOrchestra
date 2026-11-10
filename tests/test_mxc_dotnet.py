from pathlib import Path
import unittest
from unittest.mock import patch

from devorchestra import mxc_dotnet
from devorchestra.lease import Blocked
from devorchestra.runtime import native_unresolved


class DotnetVerifierTests(unittest.TestCase):
    def test_only_reviewed_sources_can_reach_tool_preflight(self):
        with patch.object(mxc_dotnet, "snapshot", return_value={"files": {
            "Entry.cs": {"sha256": "1" * 64}, "Logic.cs": {"sha256": "2" * 64},
        }}), patch.object(mxc_dotnet, "_toolchain") as toolchain:
            with self.assertRaisesRegex(Blocked, "Only the reviewed"):
                mxc_dotnet.review(Path("fixture"), "codex.exe", ["Entry.cs", "Logic.cs"], [[9, 18]], 20)
        toolchain.assert_not_called()

    def test_source_paths_cannot_supply_compiler_switches(self):
        with patch.object(mxc_dotnet, "snapshot") as snapshot:
            for name in ("../Logic.cs", "/out:Logic.cs", "@Logic.cs", "entry.cs"):
                with self.subTest(name=name), self.assertRaisesRegex(Blocked, "root C# source paths"):
                    mxc_dotnet.review(Path("fixture"), "codex.exe", ["Entry.cs", name], [[9, 18]], 20)
        snapshot.assert_not_called()

    def test_unsettled_build_intent_blocks_following_work(self):
        intent = {"kind": "build_verifier_intent", "run_id": "approved"}
        foreign_result = {"kind": "build_verifier_result", "run_id": "different"}
        self.assertTrue(native_unresolved([intent, foreign_result]))
        self.assertFalse(native_unresolved([intent, {**foreign_result, "run_id": "approved"}]))
