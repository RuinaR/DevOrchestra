"""Bind a reviewed data-only WinForms build to one subscription cycle change."""
from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from . import gui_exe, gui_project, gui_verification
from .lease import Blocked
from .ledger import check_path, digest
from .system_powershell import hold_system_powershell

TOOL = "embedded-winforms-v1"
SPEC = {"gui": TOOL, "mutable": "opened"}


def immutable_form(form):
    return digest({key: value for key, value in form.items() if key != "opened"})


def contract(spec, project, replica, revision, source):
    if spec != SPEC:
        raise Blocked("Unsupported GUI session spec")
    form = gui_project.parse(source)
    # Require an independent click observation of the one mutable property.
    opened = [scene for scene in form["scenes"] if any(
        step == {"op": "click", "target": form["controls"]["open"]} for step in scene["steps"]
    )]
    if len(opened) != 1:
        raise Blocked("GUI approval requires one frozen opened-state observation")
    descriptor = {"replica": str(replica), "descriptor": gui_project.NAME,
                  "descriptor_sha256": sha256(source).hexdigest(), "form_sha256": digest(form)}
    scope = gui_verification.scope(project, revision, 20, "Verifier", descriptor)
    return {"tool": TOOL, "immutable_form_sha256": immutable_form(form), "scenario_sha256": digest(form["scenes"]),
            "compiler_sha256": scope["exe_sha256"], "host_source_sha256": gui_exe.SOURCE_SHA256,
            "build_template_sha256": gui_exe.BUILD_SHA256, "mutable": "opened"}


def validate_change(data, frozen):
    form = gui_project.parse(data)
    if (immutable_form(form) != frozen["immutable_form_sha256"]
            or digest(form["scenes"]) != frozen["scenario_sha256"]):
        raise Blocked("GUI change modifies the owner-approved tests or immutable form fields")
    return form


def failure_feedback(ledger, receipt, developer, binding):
    provenance = receipt.get("provenance", {})
    if (receipt.get("gui") != "FAIL" or receipt.get("job_active") != 0
            or receipt.get("task_id") != developer["task_id"]
            or provenance.get("changed_file_sha256") != developer["after_sha256"]
            or provenance.get("replica_sha256") != developer["after_replica_sha256"]
            or any(receipt.get(key) != value for key, value in binding.items())):
        raise Blocked("GUI failure is not bound to the current development change")
    observations = []
    for scene in receipt["scenes"]:
        if scene["expected"] == scene["observed"]:
            continue
        ref = "gui-" + receipt["run_id"]
        if receipt.get("artifact_dir") != ref or not re.fullmatch(r"gui-[a-f0-9]{32}", ref):
            raise Blocked("GUI failure artifact directory is invalid")
        folder = ledger.local_directory("logs") / ref
        for image in (scene["original"], scene["processed"]):
            if not re.fullmatch(r"(?:scene|report)-[1-3]\.png", image["file"]):
                raise Blocked("GUI failure artifact name is invalid")
            gui_verification.verified_image(folder / image["file"], image["sha256"])
        observations.append({"title": scene["title"], "expected": scene["expected"], "actual": scene["observed"],
                             "input": scene["input"], "original_sha256": scene["original"]["sha256"],
                             "processed_sha256": scene["processed"]["sha256"]})
    if not observations:
        raise Blocked("GUI failure has no verified state mismatch to repair")
    return {"observations": observations, "reason": receipt["reason"], "provenance": provenance,
            "task_id": receipt["task_id"], "git_head": receipt["git_head"], "receipt_sha256": digest(receipt)}


def run_bound(ledger, developer, source, replica, frozen, run_id, check, cancelled, seconds):
    if (sha256(source).hexdigest() != developer["after_sha256"]
            or frozen["host_source_sha256"] != gui_exe.SOURCE_SHA256
            or frozen["build_template_sha256"] != gui_exe.BUILD_SHA256):
        raise Blocked("GUI build provenance changed")
    form = validate_change(source, frozen)
    _, embedded_source, build_hash = gui_exe.embedded_build(form)
    descriptor = {"replica": str(replica), "descriptor": gui_project.NAME,
                  "descriptor_sha256": developer["after_sha256"], "form_sha256": digest(form)}
    limit = min(seconds, 20)
    scope = gui_verification.scope(developer["project_id"], developer["project_revision"],
                                   limit, "Verifier", descriptor)
    if scope["exe_sha256"] != frozen["compiler_sha256"]:
        raise Blocked("GUI compiler identity changed")
    with TemporaryDirectory(prefix="cycle-gui-", dir=ledger.local_directory("temp", create=True)) as temp:
        staging = Path(temp)
        with hold_system_powershell(frozen["compiler_sha256"]) as compiler:
            executable = gui_exe.compile_exe(staging, compiler, check, limit, form=form)
        check()
        scope.update(embedded_form=True, exe="PRIVATE_STAGING/approved-app.exe",
                     exe_sha256=sha256(executable).hexdigest(), fixture_sha256=embedded_source,
                     build_sha256=build_hash, trust="CONTROLLER_BUILT_TEST_EXE", arguments=[],
                     cwd="PRIVATE_STAGING", memory_bytes=gui_exe.EXE_MEMORY)
        receipt = gui_verification.run_fixture(scope, staging, cancelled, check, None, form, executable)
        if receipt["job_active"] != 0:
            raise Blocked("GUI Job drain was not verified")
        if receipt["scenes"]:
            artifacts = ledger.local_directory("logs", create=True) / f"gui-{run_id}"
            check_path(artifacts)
            artifacts.mkdir()
            images = [scene[key] for scene in receipt["scenes"] for key in ("original", "processed")]
            if receipt["comparison"]:
                images.append(receipt["comparison"])
            for image in images:
                (staging / image["file"]).replace(artifacts / image["file"])
            receipt["artifact_dir"] = artifacts.name
    observations = [{"title": scene["title"], "actual": scene["observed"], "expected": scene["expected"],
                     "image_sha256": scene["original"]["sha256"]} for scene in receipt["scenes"]]
    return {**receipt, "scope": scope, "verdict": receipt["result"], "observations": observations,
            "gui": "GUI_PASS" if receipt["result"] == "PASS" else receipt["result"],
            "provenance": {"exe_sha256": scope["exe_sha256"], "embedded_source_sha256": embedded_source,
                           "build_sha256": build_hash, "host_source_sha256": gui_exe.SOURCE_SHA256,
                           "compiler_sha256": frozen["compiler_sha256"], "changed_file_sha256": developer["after_sha256"],
                           "replica_sha256": developer["after_replica_sha256"],
                           "scenario_sha256": frozen["scenario_sha256"]}}
