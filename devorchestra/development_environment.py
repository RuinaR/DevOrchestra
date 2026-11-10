"""Inspect a registered replica for development-tool requirements without running it."""
from pathlib import Path

from .catalog import Catalog
from .lease import Blocked
from .ledger import digest
from .replica_evidence import snapshot


def review(ledger, project_id):
    from .dotnet_install import registered
    from .mxc_dotnet import _toolchain

    catalog = Catalog(ledger).read()
    setup = catalog["setups"].get(project_id)
    if not setup:
        raise Blocked("Environment review requires a registered replica")
    replica = Path(setup["replica"])
    image = snapshot(replica)
    files = image["files"]
    markers = {
        ".NET": ("global.json", ".csproj", ".sln", ".cs", "packages.lock.json"),
        "Python": (".py", "pyproject.toml", "requirements.txt", "uv.lock", ".python-version"),
        "C++": (".cpp", ".cxx", ".hpp", "cmakelists.txt", "cmakepresets.json", "vcpkg.json"),
        "Unity": ("projectsettings/projectversion.txt", "packages/manifest.json", "packages/packages-lock.json"),
    }
    detected = {}
    for name, patterns in markers.items():
        matches = [path for path in files if any(
            path.casefold().endswith(pattern) if pattern.startswith(".") and pattern.count(".") == 1
            else path.casefold() == pattern for pattern in patterns
        )]
        if matches:
            detected[name] = {"evidence": {path: files[path]["sha256"] for path in sorted(matches)},
                              "status": "DETECTED"}
    if ".NET" in detected:
        try:
            local = registered(ledger, project_id, replica) if ledger.path.exists() else None
            root, tool, _, _, identity, selection = _toolchain(replica, local)
        except Blocked as error:
            detected[".NET"].update(status="BLOCKED", reason=str(error))
        else:
            detected[".NET"].update(status="SIGNED_TOOLCHAIN_FOUND",
                                    source="REGISTERED_PROJECT_SDK" if local else "INSTALLED_TOOLCHAIN",
                                    root=str(root), executable=str(tool), architecture="win-x64",
                                    sdk=selection["sdk"], runtime=selection["runtime"].name,
                                    reference_pack=selection["ref_pack"].name, console_target=selection["target"],
                                    sdk_policy=selection["sdk_policy"], files=identity)
    if snapshot(replica) != image:
        raise Blocked("Replica changed during environment review")
    return {"project_id": project_id, "replica_sha256": digest(image), "requirements": detected,
            "install": "NOT_ADMITTED"}
