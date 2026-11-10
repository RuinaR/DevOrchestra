"""Pinned offline MXC runtime for a reviewed, deliberately narrow Python harness."""
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory

from .app_server import verify_app_server_binary
from .lease import Blocked
from .ledger import canonical, check_path, digest
from .mxc_host_probe import LAUNCHER_PIN, _args, _observe, _profile
from .mxc_launcher import _known_folder, _pin, _signed_offline
from .python_vectors import run_vectors
from .replica_evidence import fingerprint

TOOL = "mxc-python-runtime-v1"
MEMORY = 512 * 1024 * 1024
HARNESS = r"""
import errno, json, py_compile, socket, subprocess, sys
from importlib.machinery import SourcelessFileLoader
from pathlib import Path

work = Path.cwd()
root = work.parent
output = root / "output"
private = root / "protected" / "marker"
spec = json.loads((work / "vectors.json").read_bytes())
if ".".join(map(str, sys.version_info[:3])) != sys.argv[2]:
    sys.exit(3)

def denied(operation):
    try:
        operation()
    except PermissionError as error:
        return error.errno == errno.EACCES
    return False

first = work / sorted(spec)[0]
proof = {
    "source_write_denied": denied(lambda: first.open("r+b").close()),
    "outside_read_denied": denied(private.read_bytes),
    "outside_write_denied": denied(lambda: private.open("r+b").close()),
}
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1):
        proof["loopback_error"] = 0
except OSError as error:
    proof["loopback_error"] = error.winerror
if proof != {"source_write_denied": True, "outside_read_denied": True,
             "outside_write_denied": True, "loopback_error": 10013}:
    print(json.dumps({"proof": proof}))
    sys.exit(3)
child_code = "from pathlib import Path\nimport sys\ntry: Path(sys.argv[1]).read_bytes()\nexcept PermissionError: print('DENIED')"
child = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", child_code, str(private)],
                       capture_output=True, timeout=3)
proof["child_inherited_read_denial"] = child.returncode == 0 and child.stdout.strip() == b"DENIED"
if not proof["child_inherited_read_denial"]:
    print(json.dumps({"proof": proof}))
    sys.exit(3)
rows = []
for index, (name, test) in enumerate(sorted(spec.items())):
    target = output / (name + "c")
    py_compile.compile(str(work / name), cfile=str(target), doraise=True)
    module = SourcelessFileLoader("_devorchestra_" + str(index), str(target)).load_module()
    function = getattr(module, test["function"])
    for value, expected in test["vectors"]:
        rows.append({"file": name, "input": value, "expected": expected, "actual": function(value)})
print(json.dumps({"proof": proof, "observations": rows}))
sys.exit(0 if all(type(row["actual"]) is int and row["actual"] == row["expected"] for row in rows) else 1)
"""


def manifest(config):
    if (type(config) is not dict or set(config) != {"kind", "launcher", "python", "python_sha256"}
            or config["kind"] != TOOL or any(type(config[key]) is not str for key in config)):
        raise Blocked("Invalid fixed MXC Python manifest")
    launcher, python = Path(config["launcher"]), Path(config["python"])
    trusted = _known_folder(0x26) / "WindowsApps"
    if (launcher.name.casefold() != "codex.exe" or python.name.casefold() != "python.exe"
            or not python.is_relative_to(trusted)):
        raise Blocked("MXC runtime requires a pinned Codex launcher and packaged Python")
    verify_app_server_binary(launcher, approved_sha256=LAUNCHER_PIN)
    _pin(python, config["python_sha256"])
    if not _signed_offline(python):
        raise Blocked("Packaged Python signature is unverified")
    return {**config, "launcher_sha256": LAUNCHER_PIN, "harness_sha256": sha256(HARNESS.encode()).hexdigest(),
            "arguments": ["-I", "-S", "-B", "-c", "FIXED_HARNESS", "OWNED_LOOPBACK_PORT", "EXPECTED_VERSION"],
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "workspace": "READ_ONLY_HASHED_COPY", "outputs": "PRIVATE_PYC_AND_JSON",
            "environment": ["SystemRoot", "WINDIR", "SystemDrive", "LOCALAPPDATA", "ProgramData",
                            "PATH", "CODEX_HOME", "TEMP", "TMP"],
            "network": "OFF", "memory_bytes": MEMORY, "max_seconds": 20, "expected_exit_codes": [0, 1]}


def run(ledger, approved, sources, tests, check, seconds):
    config = {key: approved[key] for key in ("kind", "launcher", "python", "python_sha256")}
    if manifest(config) != approved:
        raise Blocked("MXC runtime approval changed")
    # The existing AST gate validates the complete function grammar, including
    # branches not exercised by test vectors. Its values are not runtime evidence.
    for name, source in sources.items():
        if run_vectors(source, tests[name])["syntax"] != "PASS":
            raise Blocked("Python source is outside the runtime harness grammar")
    with TemporaryDirectory(prefix="mxc-python-", dir=ledger.local_directory("temp", create=True)) as directory:
        root = Path(directory)
        work, home, output, private = (root / name for name in ("work", "home", "output", "protected"))
        for folder in (work, home, output, private):
            folder.mkdir()
        for name, source in sources.items():
            if Path(name).name != name or not name.endswith(".py"):
                raise Blocked("Runtime source path is outside the staged root")
            (work / name).write_bytes(source)
        (work / "vectors.json").write_text(canonical(tests), encoding="ascii")
        (private / "marker").write_bytes(b"Controller-owned boundary fixture")
        python = Path(approved["python"])
        profile = _profile(work, home, output)
        network = "[permissions.manager-host-offline.network]"
        profile = profile.replace(network, f'{json.dumps(str(python.parent))} = "read"\n{network}')
        (home / "config.toml").write_text(profile, encoding="ascii")
        windows, appdata = (_known_folder(number) for number in (0x24, 0x1C))
        env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
               "LOCALAPPDATA": str(appdata), "ProgramData": str(_known_folder(0x23)),
               "PATH": str(windows / "System32"), "CODEX_HOME": str(home),
               "TEMP": str(output), "TMP": str(output)}
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(0.25)
            address = listener.getsockname()
            with socket.create_connection(address, timeout=1):
                peer, _ = listener.accept()
                peer.close()
            check()
            argv = _args(python, "-I", "-S", "-B", "-c", HARNESS, str(address[1]), approved["python_version"])
            result = _observe(Path(approved["launcher"]), argv,
                              work, output, env, seconds=min(seconds, approved["max_seconds"]),
                              memory=approved["memory_bytes"], check=check, output_limit=16384)
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                pass
            else:
                peer.close()
                raise Blocked("MXC Python reached a denied loopback listener")
        try:
            report = json.loads(result["stdout"])
        except (ValueError, RecursionError):
            raise Blocked("MXC Python produced no valid runtime receipt") from None
        proof = {"source_write_denied": True, "outside_read_denied": True, "outside_write_denied": True,
                 "loopback_error": 10013, "child_inherited_read_denial": True}
        if (type(report) is not dict or report.get("proof") != proof or result["stderr"]
                or result["exitCode"] not in approved["expected_exit_codes"] or result["job_active"] != 0):
            raise Blocked("MXC Python boundaries or process result were not verified")
        rows = report.get("observations")
        expected = [(name, pair[0], pair[1]) for name, spec in sorted(tests.items()) for pair in spec["vectors"]]
        if (type(rows) is not list or len(rows) != len(expected) or any(
                type(row) is not dict or set(row) != {"file", "input", "expected", "actual"}
                or (row["file"], row["input"], row["expected"]) != wanted
                or type(row["actual"]) not in (int, bool) for row, wanted in zip(rows, expected))):
            raise Blocked("MXC Python observations do not match the approved vectors")
        passed = all(type(row["actual"]) is int and row["actual"] == row["expected"] for row in rows)
        if result["exitCode"] != (0 if passed else 1):
            raise Blocked("MXC Python exit code disagrees with observations")
        artifacts = {name + "c": fingerprint(output / (name + "c"), 65536) for name in sources}
        if any(fingerprint(work / name)["sha256"] != sha256(data).hexdigest() for name, data in sources.items()):
            raise Blocked("MXC Python input changed")
        return {"verdict": "PASS" if passed else "FAIL", "exit_code": result["exitCode"], "syntax": "PASS",
                "observations": rows, "runtime": TOOL, "runtime_manifest_sha256": digest(approved),
                "profile_sha256": sha256(profile.encode()).hexdigest(), "boundary_observations": proof,
                "artifacts": artifacts, "job_active": 0, "owned_processes_started": result["job_total"]}
