import unittest

from devorchestra.lease import Blocked
from devorchestra.mxc_sdk_project import _project


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
