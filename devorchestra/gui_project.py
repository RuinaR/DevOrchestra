"""Reviewed data-only GUI projects for the trusted Windows form host."""
from hashlib import sha256
import json
from pathlib import Path
import re

from .gui_verification import _small_file
from .lease import Blocked
from .ledger import check_path, digest
from .setup import validate_setup

NAME = "gui-test.json"
LAYOUT = "native-form-v1"
FIELDS = {"format", "data", "title", "initial", "opened", "prefix", "open_button",
          "apply_button", "row_label", "controls", "scenes"}
CONTROL_SLOTS = ("status", "open", "apply", "entry", "list")


def _text(value, limit=80):
    if (not isinstance(value, str) or not 1 <= len(value) <= limit
            or not value.isprintable()):
        raise Blocked("Invalid GUI fixture text")
    return value


def parse(data):
    from .role_contract import _unique_object

    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        if isinstance(value, dict) and value.get("format") != LAYOUT:
            raise Blocked("NOT_VERIFIED: project GUI format has no verified execution backend")
        if (not isinstance(value, dict) or set(value) != FIELDS or value["data"] != "SYNTHETIC_ONLY"):
            raise Blocked("GUI fixture requires the bounded synthetic data format")
        for key in ("title", "initial", "opened", "prefix", "open_button", "apply_button", "row_label"):
            _text(value[key], 18 if key.endswith("_button") else 50)
        controls = value["controls"]
        if (not isinstance(controls, dict) or set(controls) != set(CONTROL_SLOTS)
                or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name)
                       or name in ("root", "capture") for name in controls.values())
                or len(set(controls.values())) != len(CONTROL_SLOTS)):
            raise Blocked("Invalid project control mapping")
        scenes = value["scenes"]
        if not isinstance(scenes, list) or len(scenes) != 3:
            raise Blocked("Specify exactly three capture scenes")
        for scene in scenes:
            if not isinstance(scene, dict) or set(scene) != {"title", "expected", "steps"}:
                raise Blocked("Invalid capture scene")
            _text(scene["title"], 45)
            _text(scene["expected"], 120)
            if not isinstance(scene["steps"], list) or len(scene["steps"]) > 8:
                raise Blocked("A scene may contain at most eight steps")
            for step in scene["steps"]:
                _validate_step(step, controls)
        if len({scene["title"] for scene in scenes}) != len(scenes):
            raise Blocked("Scene titles must be unique")
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Blocked("Invalid GUI scenario JSON") from error


def _validate_step(step, controls):
    if not isinstance(step, dict):
        raise Blocked("Invalid GUI step")
    operation = step.get("op")
    fields = {"click": {"op", "target"}, "text": {"op", "target", "value"},
              "scroll": {"op", "target", "value"}, "resize": {"op", "width", "height"}}
    if not isinstance(operation, str) or operation not in fields or set(step) != fields[operation]:
        raise Blocked("Unsupported GUI step")
    targets = {"click": (controls["open"], controls["apply"]), "text": (controls["entry"],),
               "scroll": (controls["list"],)}
    if operation in targets and step["target"] not in targets[operation]:
        raise Blocked("GUI step target does not support the operation")
    if operation == "text":
        _text(step["value"], 60)
    if operation == "scroll" and (type(step["value"]) is not int or not 1 <= step["value"] <= 40):
        raise Blocked("Scroll index outside fixture range")
    if operation == "resize" and (type(step["width"]) is not int or type(step["height"]) is not int
                                   or not 620 <= step["width"] <= 900 or not 450 <= step["height"] <= 650):
        raise Blocked("Window size outside fixture range")


def load(catalog, project_id, state_boundary):
    record = catalog["projects"].get(project_id)
    setup = catalog["setups"].get(project_id)
    if not record or not setup:
        raise Blocked("Register a project and its separate work copy first")
    validate_setup({**setup, "project": record["data"]}, state_boundary, allow_file_proposals=True)
    replica = Path(setup["replica"])
    check_path(replica)
    path = replica / NAME
    check_path(path)
    if not path.is_file():
        raise Blocked("NOT_VERIFIED: the registered work copy has no gui-test.json")
    data = _small_file(path, 24 * 1024)
    form = parse(data)
    return form, {"replica": str(replica), "descriptor": NAME,
                  "descriptor_sha256": sha256(data).hexdigest(), "form_sha256": digest(form)}
