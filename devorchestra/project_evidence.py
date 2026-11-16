"""Project hints and bounded, disposable Windows preview handoff."""
import json
from pathlib import Path, PurePosixPath
from shutil import copyfile

from .lease import Blocked
from .ledger import check_path
from .project_onboarding import _relative
from .role_contract import _unique_object
from .replica_evidence import fingerprint, snapshot

PREVIEW_MANIFEST = "preview.json"
GUI_BUNDLE_LIMIT = 256 * 1024**2
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


def approved_preview(directory, checkout, *, identity, args=()):
    """Bind a prebuilt Windows GUI to the approved Git head or local snapshot."""
    if not directory.is_absolute():
        raise Blocked("Select an absolute preview bundle folder")
    check_path(directory)
    if (directory.resolve() != directory or directory.is_relative_to(checkout)
            or checkout.is_relative_to(directory)):
        raise Blocked("Select a separate preview folder outside the registered checkout")
    if (type(args) not in (list, tuple) or len(args) > 16 or any(type(arg) is not str
            or not 1 <= len(arg) <= 256 or any(ord(ch) < 32 for ch in arg) for arg in args)):
        raise Blocked("Preview launch arguments are invalid")
    bundle = snapshot(directory, max_file_bytes=GUI_BUNDLE_LIMIT, long_local=True)
    name = preview_entry(directory, bundle["files"])
    if not name or bundle["git"] or sum(file["bytes"] for file in bundle["files"].values()) > GUI_BUNDLE_LIMIT:
        raise Blocked("Invalid or oversized Windows preview bundle")
    metadata = directory.parent / "build.json"
    fingerprint(metadata, 65536)
    build = json.loads(metadata.read_bytes(), object_pairs_hook=_unique_object)
    if not isinstance(build, dict) or any(build.get(key) != value for key, value in identity.items()) \
            or build.get("files") != bundle["files"]:
        raise Blocked("The preview build record does not match the approved source")
    return {"bundle": str(directory), "inventory": bundle, "entry": name, "args": list(args)}


def stage_preview(target, destination, check=lambda: None):
    for name in target["inventory"]["files"]:
        relative = _relative(name)
        source, output = Path(target["bundle"]) / relative, destination / relative
        check()
        check_path(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, output)
    if snapshot(destination, max_file_bytes=GUI_BUNDLE_LIMIT, long_local=True)["files"] != target["inventory"]["files"]:
        raise Blocked("Staged preview differs from the approved bundle")
    return destination / _relative(target["entry"])
