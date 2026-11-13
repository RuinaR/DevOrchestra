"""Project hints and bounded, disposable Windows preview handoff."""
import json
from pathlib import PurePosixPath

from .lease import Blocked
from .ledger import check_path
from .project_onboarding import _relative
from .role_contract import _unique_object

PREVIEW_MANIFEST = "preview.json"
BUILD_FILES = {
    "project.godot": "Godot", "pyproject.toml": "Python", "requirements.txt": "Python",
    "package.json": "Node.js", "package-lock.json": "npm", "pnpm-lock.yaml": "pnpm",
    "uv.lock": "uv", "Cargo.toml": "Rust", "Cargo.lock": "Cargo", "go.mod": "Go",
    "CMakeLists.txt": "CMake", "Makefile": "Make", "global.json": ".NET SDK",
    "Directory.Build.props": "MSBuild", "packages.lock.json": "NuGet",
}
BUILD_SUFFIXES = {".csproj": ".NET", ".fsproj": ".NET", ".vbproj": ".NET", ".sln": "MSBuild",
                  ".slnx": "MSBuild", ".vcxproj": "MSBuild"}


def discovery(files):
    """Return bounded hints. Repository configuration never grants execution."""
    found = []
    for name in sorted(files, key=lambda name: (name.count("/"), name)):
        path = PurePosixPath(name)
        family = BUILD_FILES.get(path.name) or BUILD_SUFFIXES.get(path.suffix.lower())
        if family:
            found.append({"file": name, "toolchain_hint": family})
            if len(found) == 24:
                break
    return found


def preview_entry(directory, files, *, legacy=False):
    if legacy and PREVIEW_MANIFEST not in files:
        return "app.exe" if "app.exe" in files else None
    if PREVIEW_MANIFEST not in files:
        return None
    manifest = directory / PREVIEW_MANIFEST
    check_path(manifest)
    try:
        with manifest.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("Manifest too large")
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if type(data) is not dict or data.keys() != {"kind", "entry"} or data["kind"] != "windows-gui":
            raise ValueError("Invalid manifest")
        name = data["entry"]
        if type(name) is not str or len(name) > 240 or _relative(name).suffix.lower() != ".exe" or name not in files:
            raise ValueError("Invalid executable")
        executable = directory / _relative(name)
        check_path(executable)
        with executable.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise ValueError("Invalid Windows executable header")
            offset = int.from_bytes(header[60:64], "little")
            if not 64 <= offset <= 1024 * 1024:
                raise ValueError("Invalid PE header offset")
            stream.seek(offset)
            if stream.read(4) != b"PE\0\0":
                raise ValueError("Invalid PE signature")
        return name
    except (Blocked, ValueError, RecursionError) as error:
        raise Blocked("Invalid Windows preview manifest") from error
