"""Discover compatible installed SDKs without executing project configuration."""
from hashlib import sha256
import json
import re

from .lease import Blocked
from .ledger import check_path
from .replica_evidence import fingerprint


def _version(value):
    if type(value) is str and re.fullmatch(r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", value):
        return tuple(map(int, value.split(".")))
    return None


def _json(path):
    try:
        identity = fingerprint(path, 65536)
        with path.open("rb") as stream:
            data = stream.read(65537)
        if sha256(data).hexdigest() != identity["sha256"] or fingerprint(path, 65536) != identity:
            raise Blocked(".NET configuration changed during inspection")
        value = json.loads(data)
        if type(value) is dict:
            return value, identity["sha256"]
    except (OSError, ValueError, RecursionError):
        pass
    raise Blocked("Invalid .NET configuration")


def _versions(path):
    check_path(path)
    if not path.is_dir():
        return []
    versions = sorted(((version, child) for child in path.iterdir() if (version := _version(child.name))),
                      reverse=True)
    for _, child in versions:
        check_path(child)
    return [(version, child) for version, child in versions if child.is_dir()]


def select(root, replica):
    """Select from a trusted local SDK root and interpret only narrow global.json settings."""
    config = replica / "global.json"
    check_path(config)
    minimum, policy, config_hash = None, "latestMajor", None
    if config.exists():
        value, config_hash = _json(config)
        sdk = value.get("sdk")
        if (set(value) != {"sdk"} or type(sdk) is not dict
                or set(sdk) - {"version", "rollForward", "allowPrerelease"}
                or sdk.get("allowPrerelease", False) is not False
                or not (minimum := _version(sdk.get("version")))):
            raise Blocked("Unsupported global.json SDK policy")
        policy = sdk.get("rollForward", "patch")
        if policy not in ("disable", "patch", "latestPatch", "latestFeature", "latestMinor", "latestMajor"):
            raise Blocked("Unsupported global.json roll-forward policy")
    candidates = _versions(root / "sdk")
    if minimum:
        def compatible(version):
            if version < minimum:
                return False
            if policy == "disable":
                return version == minimum
            if policy in ("patch", "latestPatch"):
                return version[:2] == minimum[:2] and version[2] // 100 == minimum[2] // 100
            if policy == "latestFeature":
                return version[:2] == minimum[:2]
            if policy == "latestMinor":
                return version[0] == minimum[0]
            return True
        candidates = [(version, path) for version, path in candidates if compatible(version)]
        if policy == "patch":
            candidates.sort(key=lambda item: item[0] == minimum, reverse=True)
    if not candidates:
        raise Blocked("No installed .NET SDK satisfies the project policy")
    _, sdk = candidates[0]
    compiler = sdk / "Roslyn" / "bincore"
    config, _ = _json(compiler / "csc.runtimeconfig.json")
    options = config.get("runtimeOptions")
    if type(options) is not dict or type(options.get("framework")) is not dict:
        raise Blocked("Unsupported Roslyn runtime contract")
    framework = options["framework"]
    requested = _version(framework.get("version"))
    if framework.get("name") != "Microsoft.NETCore.App" or not requested:
        raise Blocked("Unsupported Roslyn target framework")
    # Roslyn's own compile-time TFM can differ from the framework it runs on.
    target = f"net{requested[0]}.{requested[1]}"
    runtimes = _versions(root / "shared" / "Microsoft.NETCore.App")
    packs = _versions(root / "packs" / "Microsoft.NETCore.App.Ref")
    runtime = next((path for version, path in runtimes if version[:2] == requested[:2] and version >= requested), None)
    pack = next((path for version, path in packs if version[:2] == requested[:2]), None)
    hosts = _versions(root / "host" / "fxr")
    if runtime is None or pack is None or not hosts:
        raise Blocked("Compatible .NET runtime, reference pack, or host is missing")
    return {"sdk": sdk.name, "compiler": compiler, "runtime": runtime, "ref_pack": pack,
            "target": target, "host": hosts[0][1], "sdk_policy": policy, "global_json_sha256": config_hash}
