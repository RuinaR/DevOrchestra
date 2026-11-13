"""Bind owned GUI evidence to a bounded, independent Codex review."""
import json
from hashlib import sha256
from pathlib import Path
import re
from struct import error as StructError, unpack_from
from zlib import crc32, decompressobj, error as ZlibError

from .gui_verification import verified_image
from .ledger import check_path, digest
from .lease import Blocked
from .replica_evidence import fingerprint
from .role_contract import _unique_object


MAX_FRAMES = 4
MAX_FINDINGS = 4
MAX_NOT_VERIFIED = 8
MAX_DESCRIPTION_CHARS = 240
SENSITIVE = re.compile(r"https?://|\b(?:bearer|password|secret|credential|api[ _-]?key|token|cookie)\b"
                       r"|\b[A-Za-z0-9_+/=-]{32,}\b", re.I)


def observation_provenance(evidence):
    return {"observation_source": "CONTROLLER_OWNED_CAPTURE" if evidence else "NO_CAPTURE",
            "input_source": ("CONTROLLER_SCENARIO" if any(frame["step"] == "after-input" for frame in evidence)
                             else "NO_INPUT_EVIDENCE"),
            "review_backend": "CODEX_CLI_IMAGE" if evidence else "CODEX_CLI_TEXT",
            "autonomous_play": "NOT_VERIFIED"}


def write_schema(path, frame_ids):
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    def choices(*values):
        return {"type": "string", "enum": list(values)}

    string = {"type": "string"}
    frame = choices(*frame_ids)
    finding = obj({"id": choices("V1", "V2", "V3", "V4"), "kind": choices("defect", "suggestion"),
                   "severity": choices("low", "medium", "high", "critical"), "frame": frame,
                   **{key: string for key in ("observed", "expected", "reproduce", "fix", "basis")},
                   "uncertainty": choices("low", "medium", "high")})
    schema = obj({"verdict": choices("ACCEPT", "REWORK", "UNAVAILABLE"),
                  "observations": {"type": "array", "items": obj({"frame": frame, "visible": string})},
                  "findings": {"type": "array", "items": finding},
                  "not_verified": {"type": "array", "items": string},
                  "code_finding": {"anyOf": [{"type": "null"}, obj({"file": string, "observed": string})]}})
    check_path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(schema, stream, separators=(",", ":"))
    return path


def _safe_text(value):
    return "[omitted: possible sensitive text]" if SENSITIVE.search(value) else value


def safe_findings(findings):
    return [{key: _safe_text(value) for key, value in finding.items()} for finding in findings]


def safe_unverified(items):
    return [_safe_text(item) for item in items]


def frames(ledger, receipt, run_id, cycle, source, *, allow_empty=False):
    if (not re.fullmatch(r"[a-f0-9]{32}", run_id) or receipt.get("source_sha256") != source
            or receipt.get("phase") not in {"verifier", "review-capture"} or receipt.get("outcome") != "COMPLETED"
            or receipt.get("owned_tree") != "DRAINED"):
        raise Blocked("Visual evidence does not match the owned candidate")
    captures = []
    suffix = "-review" if receipt["phase"] == "review-capture" else ""
    game = receipt.get("game")
    if game and game.get("artifact_directory"):
        expected = f"godot-windows-export-{run_id}-cycle-{cycle}{suffix}"
        captures.append((expected, game["artifact_directory"], game["window_size"], game["window_image"],
                         game["executable"]["sha256"], "Windows export", "GamePilot.exe"))
    for row in receipt.get("observations", ()):
        if "gui" in row:
            if len(captures) >= MAX_FRAMES // 2:
                break
            gui = row["gui"]
            captures.append((f"gui-{run_id}-{cycle}-{row['title']}{suffix}", gui["artifact_directory"],
                             gui, gui["image"], row.get("executable_sha256"), row["title"], None))
    preview = receipt.get("visual_preview")
    if preview and preview.get("artifact_directory"):
        captures.append((f"gui-{run_id}-{cycle}-preview", preview["artifact_directory"], None,
                         preview["image"], preview["executable_sha256"], "Developer GUI preview", None))
    if not captures and allow_empty:
        return []
    frame_count = sum(2 if detail is not None else 1 for _, _, detail, *_ in captures)
    if not 1 <= frame_count <= MAX_FRAMES:
        raise Blocked("Visual Reviewer requires one to four approved owned-window captures")
    result = []
    for expected, folder, detail, after, executable, scenario, executable_file in captures:
        if folder != expected or Path(folder).name != folder or not executable:
            raise Blocked("Visual evidence artifact identity does not match")
        images = (("before-input", detail.get("before_input")), ("after-input", after)) if detail is not None else (
            ("startup", after),)
        for step, image in images:
            if not isinstance(image, dict) or image.get("file") not in {
                    "before-input.png", "after-input.png", "exported-window.png", "preview.png"}:
                raise Blocked("Missing owned GUI capture")
            result.append({**image, "id": f"F{len(result) + 1}", "step": step, "folder": folder,
                           "scenario": scenario, "source_sha256": source, "executable_sha256": executable,
                           "executable_file": executable_file})
    read_frames(ledger, result)
    return result


def read_frames(ledger, evidence):
    if not 1 <= len(evidence) <= MAX_FRAMES:
        raise Blocked("Invalid number of visual frames")
    recovery = ledger.local_directory("recovery")
    result = []
    verified_executables = set()
    for frame in evidence:
        if any(Path(frame[key]).name != frame[key] for key in ("folder", "file")):
            raise Blocked("Invalid visual artifact path")
        path = recovery / frame["folder"] / frame["file"]
        if executable := frame.get("executable_file"):
            if executable != "GamePilot.exe":
                raise Blocked("Unexpected visual evidence executable")
            binding = (path.parent / executable, frame["executable_sha256"])
            if binding not in verified_executables:
                if fingerprint(binding[0], 256 * 1024**2)["sha256"] != binding[1]:
                    raise Blocked("Visual evidence executable changed")
                verified_executables.add(binding)
        data, observed = verified_image(path, frame["sha256"])
        if any(observed[key] != frame[key] for key in ("width", "height", "file")):
            raise Blocked("Visual artifact dimensions changed")
        pixels = _verify_pixels(data, frame["width"], frame["height"])
        if "client_sha256" in frame:
            _verify_client(frame, pixels)
        result.append(data)
    return result


def _verify_pixels(data, width, height):
    # The owned-window encoder writes RGB8 scanlines with filter zero.
    cursor = 8
    try:
        for kind in (b"IHDR", b"IDAT", b"IEND"):
            size, tag = unpack_from(">I4s", data, cursor)
            stop = cursor + 8 + size
            if tag != kind or stop + 4 > len(data):
                raise ValueError
            payload = data[cursor + 8:stop]
            if crc32(tag + payload) != unpack_from(">I", data, stop)[0]:
                raise ValueError
            if kind == b"IHDR" and (size != 13 or payload[8:] != b"\x08\x02\x00\x00\x00"):
                raise ValueError
            if kind == b"IDAT":
                row_size = width * 3 + 1
                decoder = decompressobj()
                pixels = decoder.decompress(payload, row_size * height + 1)
                if (not decoder.eof or decoder.unused_data or len(pixels) != row_size * height
                        or any(pixels[::row_size])):
                    raise ValueError
            if kind == b"IEND" and size:
                raise ValueError
            cursor = stop + 4
        if cursor != len(data):
            raise ValueError
    except (ValueError, ZlibError, StructError):
        raise Blocked("Owned GUI capture contains invalid PNG pixels") from None
    return pixels


def _verify_client(frame, pixels):
    width, height = frame["width"], frame["height"]
    client = frame.get("client")
    if not isinstance(client, list) or len(client) != 4 or any(type(value) is not int for value in client):
        raise Blocked("Owned GUI client geometry is invalid")
    left, top, client_width, client_height = client
    x0, x1 = max(0, left), min(width, left + client_width)
    y0, y1 = max(0, top), min(height, top + client_height)
    if x0 >= x1 or y0 >= y1:
        raise Blocked("Owned GUI client geometry is invalid")
    checksum = sha256()
    stride = width * 3 + 1
    for y in range(y0, y1):
        line = pixels[y * stride + 1 + x0 * 3:y * stride + 1 + x1 * 3]
        for channel in (2, 1, 0):
            checksum.update(line[channel::3])
    if checksum.hexdigest() != frame["client_sha256"]:
        raise Blocked("Owned GUI client pixels changed")


def stage(ledger, evidence, directory):
    paths = []
    for frame, data in zip(evidence, read_frames(ledger, evidence), strict=True):
        path = directory / f"{frame['id']}.png"
        check_path(path)
        if "," in str(path):
            raise Blocked("Codex image staging path contains a comma")
        with path.open("xb") as stream:
            stream.write(data)
        paths.append(path)
    return tuple(paths)


def parse(message, frame_ids, changed_files=()):
    try:
        result = json.loads(message, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError, Blocked):
        raise Blocked("Visual Reviewer did not return a unique JSON object") from None
    try:
        if type(result) is dict and result.get("verdict") == "UNAVAILABLE":
            raise Blocked("Visual Reviewer reported unavailable images")
        if (type(result) is not dict or set(result) != {"verdict", "observations", "findings", "not_verified", "code_finding"}
                or result["verdict"] not in ("ACCEPT", "REWORK", "UNAVAILABLE")
                or type(result["observations"]) is not list or len(result["observations"]) != len(frame_ids)
                or type(result["findings"]) is not list or len(result["findings"]) > MAX_FINDINGS
                or type(result["not_verified"]) is not list or len(result["not_verified"]) > MAX_NOT_VERIFIED
                or not all(_description(item) for item in result["not_verified"])):
            raise ValueError
        code = result["code_finding"]
        if code is not None and (type(code) is not dict or set(code) != {"file", "observed"}
                                 or code["file"] not in changed_files or not _description(code["observed"])):
            raise ValueError
        for frame_id, observation in zip(frame_ids, result["observations"], strict=True):
            if (type(observation) is not dict or set(observation) != {"frame", "visible"}
                    or observation["frame"] != frame_id or not _description(observation["visible"])):
                raise ValueError
        fields = {"id", "kind", "severity", "frame", "observed", "expected", "reproduce", "fix",
                  "basis", "uncertainty"}
        seen = set()
        for item in result["findings"]:
            if (type(item) is not dict or item.keys() != fields
                    or not all(_description(value) for value in item.values())
                    or item["id"] in seen or not re.fullmatch(r"V[1-4]", item["id"])
                    or item["kind"] not in ("defect", "suggestion")
                    or item["severity"] not in ("low", "medium", "high", "critical")
                    or item["frame"] not in frame_ids or item["uncertainty"] not in ("low", "medium", "high")):
                raise ValueError
            seen.add(item["id"])
        blockers = [item for item in result["findings"] if item["kind"] == "defect"
                    and item["severity"] in ("medium", "high", "critical") and item["uncertainty"] == "low"]
        if (result["verdict"] == "REWORK") != bool(blockers or code):
            raise Blocked("Visual Reviewer verdict did not match its cited defects")
    except (ValueError, TypeError, KeyError, RecursionError):
        raise Blocked("Visual Reviewer response did not match the evidence schema") from None
    return {**result, "response_sha256": digest(message)}, blockers


def _description(value):
    return (type(value) is str and 1 <= len(value) <= MAX_DESCRIPTION_CHARS and bool(value.strip())
            and all(32 <= ord(char) and not 0xD800 <= ord(char) <= 0xDFFF for char in value))
