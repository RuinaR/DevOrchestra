from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from devorchestra import csharp_sources, dotnet_install
from devorchestra.lease import Blocked


class P2102Tests(unittest.TestCase):
    def test_csharp_arithmetic_rejects_additional_code(self):
        sources = {
            "Scale.cs": b"static class Scale { public static int Apply(int value) => checked(value * 3); }",
            "Calculation.cs": b"static class Calculation { public static int Twice(int value) => checked(Scale.Apply(value) + 4); }",
        }
        csharp_sources.validate(sources)
        with self.assertRaisesRegex(Blocked, "arithmetic grammar"):
            csharp_sources.validate({**sources, "Scale.cs": sources["Scale.cs"] + b" class Extra {}"})

    def test_sdk_zip_rejects_paths_outside_extraction_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            archive = root / "fixture.zip"
            with ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside.txt", "outside")
            with self.assertRaisesRegex(Blocked, "Unsafe path"):
                dotnet_install._extract(archive, output, lambda: None)
            self.assertFalse((root / "outside.txt").exists())
