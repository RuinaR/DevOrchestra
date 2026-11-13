"""Local form preferences, separate from approved execution contracts."""
import json
import os
from tempfile import NamedTemporaryFile

from .ledger import check_path, digest


MAX_BYTES = 256 * 1024


def _path(ledger, project, *, create=False):
    return ledger.local_directory("state", create=create) / f"development-profile-{digest(project)}.json"


def read(ledger, project):
    path = _path(ledger, project)
    check_path(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_BYTES:
        raise ValueError("Development preferences exceed storage limit")
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get("version") != 1 or record.get("project") != project
            or not isinstance(record.get("preferences"), dict)):
        raise ValueError("Invalid development preferences")
    return record["preferences"]


def save(ledger, project, preferences):
    raw = json.dumps({"version": 1, "project": project, "preferences": preferences},
                     ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Development preferences exceed storage limit")
    path = _path(ledger, project, create=True)
    check_path(path)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix="development-profile-", delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
