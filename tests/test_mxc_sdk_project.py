import unittest

from devorchestra.lease import Blocked
from devorchestra.mxc_sdk_project import _project
from devorchestra.mxc_owned_gui import validate


class SdkProjectTests(unittest.TestCase):
    def project(self, fragment):
        return _project(f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                        f'<TargetFramework>net7.0</TargetFramework></PropertyGroup>{fragment}</Project>'.encode(),
                        "tests/Checks.csproj", "net7.0")

    def test_accepts_literal_project_reference(self):
        self.assertEqual({"output": "Library", "references": ["src/App.csproj"]},
                         self.project('<ItemGroup><ProjectReference Include="../src/App.csproj"/></ItemGroup>'))

    def test_rejects_imports_tasks_packages_and_outside_references(self):
        for fragment in ('<Import Project="custom.targets"/>', '<Target Name="Build"><Exec Command="x"/></Target>',
                         '<ItemGroup><PackageReference Include="Example" Version="1.0"/></ItemGroup>',
                         '<ItemGroup><ProjectReference Include="../../Other.csproj"/></ItemGroup>',
                         '<PropertyGroup><BaseOutputPath>C:/output</BaseOutputPath></PropertyGroup>'):
            with self.subTest(fragment=fragment), self.assertRaises(Blocked):
                self.project(fragment)

    def test_winforms_allows_only_literal_text_resources(self):
        project = ('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                   '<TargetFramework>net7.0-windows</TargetFramework><UseWindowsForms>true</UseWindowsForms>'
                   '<OutputType>WinExe</OutputType></PropertyGroup><ItemGroup>'
                   '<EmbeddedResource Include="Resources/Caption.txt"/></ItemGroup></Project>')
        self.assertEqual(["app/Resources/Caption.txt"],
                         _project(project.encode(), "app/App.csproj", "net7.0-windows", winforms=True)["resources"])
        with self.assertRaises(Blocked):
            _project(project.replace("Resources/Caption.txt", "../outside.txt").encode(),
                     "app/App.csproj", "net7.0-windows", winforms=True)

    def test_gui_checkpoint_targets_are_limited(self):
        scenario = {"controls": {name: name for name in ("status", "open", "apply", "entry", "list")},
                    "scenes": [{"title": "Ready", "expected": "Ready", "steps": [
                        {"op": "expect-text", "target": "root", "value": "Owned"}]},
                        {"title": "Search", "expected": "Found", "steps": [
                            {"op": "expect-selection", "target": "list", "value": 20}]}]}
        self.assertEqual(scenario, validate(scenario))
        scenario["scenes"][1]["steps"][0]["target"] = "entry"
        with self.assertRaises(Blocked):
            validate(scenario)
