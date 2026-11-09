"""Offline preparation and atomic publication for two approved instruction records."""
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .lease import Blocked, execution_slot
from .ledger import Ledger, check_path


@dataclass(frozen=True)
class ImportSelection:
    database_sha256: str
    backup_version: str
    backup_sha256: str
    candidate_version: str
    candidate_sha256: str


def _require_no_sidecars(path):
    check_path(path)
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            path.with_name(path.name + suffix).lstat()
        except FileNotFoundError:
            continue
        raise Blocked("SQLite sidecar present; offline import blocked")


def _source_hash(path):
    _require_no_sidecars(path)
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def prepare_import(source, selection):
    if _source_hash(source.ledger.path) != selection.database_sha256:
        raise Blocked("Source database hash changed")
    with closing(source.ledger.connect("ro")) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise Blocked("Source SQLite integrity check failed")
        events, _tail = Ledger.verified(db)
    backup = [event for event in events if event["kind"] == "instruction_backup"
              and event["version"] == selection.backup_version]
    candidate = [event for event in events if event["kind"] == "instruction_candidate"
                 and event["version"] == selection.candidate_version]
    if (len(backup) != 1 or len(candidate) != 1
            or candidate[0]["backup_version"] != selection.backup_version
            or events.index(backup[0]) >= events.index(candidate[0])):
        raise Blocked("Selected instruction records are missing or ambiguous")
    records = backup + candidate
    _verify_selection(source, selection)
    if _source_hash(source.ledger.path) != selection.database_sha256:
        raise Blocked("Source database changed during verification")
    return records


def _verify_selection(store, selection):
    _, original, _ = store.preview(selection.backup_version)
    _, _, candidate = store.preview(selection.candidate_version)
    if (sha256(original).hexdigest() != selection.backup_sha256
            or sha256(candidate).hexdigest() != selection.candidate_sha256):
        raise Blocked("Selected instruction content hash changed")


def _publish_verified(source, destination, selection, *, verify_destination_acl):
    """Internal owner operation. The caller must obtain approval before invoking."""
    if os.name != "nt":
        raise Blocked("Instruction publication requires Windows no-replace rename")
    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("Import slot requires inspection")
        records = prepare_import(source, selection)
        path = destination.ledger.path
        _require_no_sidecars(path)
        if path == source.ledger.path or not path.parent.is_dir():
            raise Blocked("Instruction import destination is invalid")
        verify_destination_acl(path.parent)
        if path.exists():
            if destination.ledger.read() == records:
                _verify_selection(destination, selection)
                return "ALREADY_IMPORTED"
            raise Blocked("Destination already contains different data")
        with TemporaryDirectory(prefix="instruction-import-", dir=path.parent) as temporary:
            staged = Ledger(Path(temporary) / path.name)
            staged.initialize()
            count, tail = 0, ""
            for event in records:
                count, tail = staged.append(count, tail, event)
            if staged.read() != records:
                raise Blocked("Staged instruction chain mismatch")
            verify_destination_acl(staged.path)
            verify_destination_acl(path.parent)
            _require_no_sidecars(path)
            # Windows rename is same-volume and refuses an existing destination.
            staged.path.rename(path)
        if destination.ledger.read() != records:
            raise Blocked("Published instruction chain mismatch")
        _verify_selection(destination, selection)
        return "IMPORTED"
