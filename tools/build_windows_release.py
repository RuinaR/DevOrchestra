"""Build a windowed x64 release with an already installed PyInstaller."""
from hashlib import file_digest, sha256
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
from uuid import uuid4
from time import monotonic
from zipfile import ZIP_DEFLATED, ZipFile


def _package(root, output, argv):
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt
    import PyInstaller
    from devorchestra.job_probe import (
        ExtendedLimits, ProcessInfo, _api, _cleanup, _create_owned, _require, _wait_empty,
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    )
    api = _api()
    job = _require(api.CreateJobObjectW(None, None))
    info = ProcessInfo()
    drained = False
    try:
        limits = ExtendedLimits()
        limits.basic.flags = (JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
                              | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.basic.active_limit = 8
        limits.job_memory = 1024 * 1024 * 1024
        _require(api.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        environment = {key: os.environ[key] for key in
                       ("SystemRoot", "WINDIR", "PATH", "TCL_LIBRARY", "TK_LIBRARY") if key in os.environ}
        build_home = output / "home"
        build_home.mkdir()
        environment.update(TEMP=str(output), TMP=str(output), USERPROFILE=str(build_home),
                           PYTHONPATH=str(Path(PyInstaller.__file__).resolve().parent.parent),
                           PYINSTALLER_CONFIG_DIR=str(output / "cache"))
        with (open(os.devnull, "rb") as stdin, (output / "build.stdout.log").open("xb") as stdout,
              (output / "build.log").open("xb") as stderr):
            stdio = tuple(msvcrt.get_osfhandle(file.fileno()) for file in (stdin, stdout, stderr))
            for handle in stdio:
                os.set_handle_inheritable(handle, True)
            _create_owned(api, job, Path(sys.executable), argv, root, environment, info, stdio)
        if api.ResumeThread(info.thread) != 1 or _wait_empty(api, job, monotonic() + 600).active:
            raise SystemExit("PACKAGE_JOB_TIMEOUT_OR_START_FAILED")
        if api.WaitForSingleObject(info.process, 0) != 0:
            raise SystemExit("PACKAGE_JOB_EXIT_UNVERIFIED")
        drained = True
        code = w.DWORD()
        _require(api.GetExitCodeProcess(info.process, c.byref(code)))
        if code.value:
            raise SystemExit(f"PACKAGE_EXIT_{code.value}")
    finally:
        _cleanup(api, job, info, drained)


def main():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from devorchestra.lease import execution_slot
    from devorchestra.ledger import check_path
    from devorchestra.owned_artifacts import _remove_owned_tree

    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64"):
        raise SystemExit("WINDOWS_X64_REQUIRED")
    if struct.calcsize("P") != 8:
        raise SystemExit("PYTHON_X64_REQUIRED")
    try:
        tool = version("pyinstaller")
    except PackageNotFoundError:
        raise SystemExit("PYINSTALLER_NOT_INSTALLED") from None
    release = sys.argv[1] if len(sys.argv) == 2 else ""
    if not re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", release) or any(
            int(part) > 65535 for part in release.split(".")):
        raise SystemExit("Usage: python tools/build_windows_release.py MAJOR.MINOR.PATCH")
    with execution_slot() as abandoned:
        if abandoned:
            raise SystemExit("EXECUTION_SLOT_RECOVERY_REQUIRED")
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=root, text=True, timeout=15).strip()
        if git("status", "--porcelain", "--untracked-files=normal"):
            raise SystemExit("CLEAN_SOURCE_REQUIRED")
        sha = git("rev-parse", "HEAD")
        output = root / ".ai-local" / "release" / f"{release}-{sha[:12]}-{uuid4().hex[:8]}"
        check_path(output)
        output.mkdir(parents=True)
        staging = output / "staging"
        staging.mkdir()
        identity = staging.stat().st_ino
        info = {"version": release, "git_sha": sha, "pyinstaller": tool}
        metadata = staging / "release.json"
        metadata.write_text(json.dumps(info), encoding="utf-8")
        launcher = staging / "launcher.py"
        launcher.write_text("from devorchestra.gui import main\nmain()\n", encoding="utf-8")
        resource = staging / "version.txt"
        numbers = tuple(map(int, release.split("."))) + (0,)
        resource.write_text(
            f"VSVersionInfo(ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, "
            "mask=63, flags=0, OS=262148, fileType=1, subtype=0, date=(0,0)), kids=["
            "StringFileInfo([StringTable('040904B0', [StringStruct('ProductName','DevOrchestra'), "
            f"StringStruct('FileVersion','{release}'), StringStruct('Comments','Git {sha}')])]), "
            "VarFileInfo([VarStruct('Translation',[1033,1200])])])", encoding="utf-8",
        )
        _package(root, staging, [
            "-m", "PyInstaller", "--onefile", "--windowed", "--noupx",
            "--name", "DevOrchestra", "--paths", str(root), "--specpath", str(staging),
            "--workpath", str(staging / "work"), "--distpath", str(output / "dist"),
            "--add-data", f"{metadata};devorchestra", "--add-data", f"{root / 'defaults'};defaults",
            "--add-data", f"{root / 'devorchestra' / 'ui_assets'};devorchestra/ui_assets",
            "--version-file", str(resource), str(launcher),
        ])
        if git("rev-parse", "HEAD") != sha or git("status", "--porcelain", "--untracked-files=normal"):
            raise SystemExit("SOURCE_CHANGED_DURING_BUILD")
        executable = output / "dist" / "DevOrchestra.exe"
        with executable.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise SystemExit("PE_HEADER_INVALID")
            stream.seek(60)
            stream.seek(int.from_bytes(stream.read(4), "little"))
            if stream.read(6) != b"PE\0\0\x64\x86":
                raise SystemExit("PE_AMD64_REQUIRED")
            stream.seek(18, 1)
            optional = stream.read(70)
            if optional[:2] != b"\x0b\x02" or optional[68:70] != b"\x02\0":
                raise SystemExit("PE32_PLUS_GUI_REQUIRED")
            stream.seek(0)
            checksum = sha256()
            while chunk := stream.read(1024 * 1024):
                checksum.update(chunk)
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(executable))
        names = {name.replace("\\", "/") for name in archive.toc}
        from devorchestra.default_instructions import SOURCES
        from devorchestra.ui_design import ASSET_FILES
        if ("devorchestra/release.json" not in names or not {f"defaults/{path}" for path in SOURCES.values()} <= names
                or not {f"devorchestra/ui_assets/{path}" for path in ASSET_FILES} <= names
                or not any(name.startswith("_tcl_data/") for name in names)
                or not any(name.startswith("_tk_data/") for name in names)):
            raise SystemExit("BUNDLED_RESOURCES_UNVERIFIED")
        for log in ("build.log", "build.stdout.log"):
            source = staging / log
            check_path(source)
            source.rename(output / log)
        removal = _remove_owned_tree(staging, identity)
        info.update(sha256=checksum.hexdigest(), bytes=executable.stat().st_size, gui_runtime="NOT_VERIFIED",
                    cleanup=removal)
        executable.with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        if removal["status"] != "REMOVED":
            raise SystemExit("PACKAGE_CLEANUP_UNCONFIRMED")
        bundle = output / f"DevOrchestra-{release}-windows-x64.zip"
        with ZipFile(bundle, "x", compression=ZIP_DEFLATED) as archive:
            archive.write(executable, "DevOrchestra.exe")
            archive.write(executable.with_suffix(".json"), "DevOrchestra.json")
            archive.write(root / "README.md", "README.md")
            archive.write(root / "docs" / "windows-codex-setup.png", "docs/windows-codex-setup.png")
        with ZipFile(bundle) as archive:
            if archive.testzip() is not None:
                raise SystemExit("PORTABLE_ZIP_INVALID")
        with bundle.open("rb") as stream:
            archive_hash = file_digest(stream, "sha256").hexdigest()
        bundle.with_suffix(".zip.sha256").write_text(f"{archive_hash}  {bundle.name}\n", encoding="ascii")
        print(bundle)
        print(json.dumps(info))


if __name__ == "__main__":
    main()
