"""SQLite event ledger. Every read validates one consistent snapshot."""
import hashlib
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from .lease import Blocked

LOCAL_DIRECTORIES = ("instructions", "state", "logs", "recovery", "temp")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def check_path(path):
    if not path.is_absolute() or path.drive.startswith("\\\\") or ".." in path.parts:
        raise Blocked("Ledger requires a local absolute path")
    candidates = (*reversed(path.parents), path, *(
        path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm")
    ))
    for candidate in candidates:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise Blocked("Ledger path contains a reparse point")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise Blocked("Ledger path contains a hard link")


class Ledger:
    def __init__(self, path: Path, *, local_root=None):
        self.path = path
        self.local_root = local_root

    @classmethod
    def for_project(cls, project_root=None):
        """Resolve default state from this source checkout, independent of cwd."""
        if os.name != "nt":
            raise Blocked("Windows local storage required")
        project_root = Path(__file__).absolute().parent.parent if project_root is None else project_root
        check_path(project_root)
        if not project_root.is_dir():
            raise Blocked("DevOrchestra project root unavailable")
        root = project_root / ".ai-local"
        check_path(root)
        if root.exists() and not root.is_dir():
            raise Blocked("Local storage root conflicts with a file")
        ledger = cls(root / "state" / "p1.sqlite3", local_root=root)
        for name in LOCAL_DIRECTORIES:
            ledger.local_directory(name)
        check_path(ledger.path)
        return ledger

    @property
    def state_boundary(self):
        return self.local_root if self.local_root is not None else self.path.parent

    def local_directory(self, name, *, create=False):
        if name not in LOCAL_DIRECTORIES:
            raise ValueError("Unknown local storage category")
        if self.local_root is None:
            # Explicitly constructed ledgers are used by isolated fixtures.
            directory = self.path.parent / "private-instructions" if name == "instructions" else self.path.parent
        else:
            directory = self.local_root / name
        check_path(directory)
        if directory.exists() and not directory.is_dir():
            raise Blocked("Local storage directory conflicts with a file")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
            check_path(directory)
        return directory

    def connect(self, mode):
        check_path(self.path)
        return sqlite3.connect(self.path.as_uri() + f"?mode={mode}", uri=True, timeout=1)

    def storage_status(self):
        result = {"storage": ".ai-local", "runtime_present": False, "instructions_present": False,
                  "legacy_import": "NOT_ATTEMPTED", "status": "BLOCKED"}
        try:
            root = self.state_boundary
            check_path(root)
            directories = [self.local_directory(name) for name in LOCAL_DIRECTORIES]
            instructions = self.local_directory("instructions") / "baselines.sqlite3"
            catalog = self.path.with_name(self.path.stem + "-catalog.sqlite3")
            for path in (self.path, instructions, catalog):
                check_path(path)
                if path.exists() and not path.is_file():
                    return result
            result.update(runtime_present=self.path.is_file(), instructions_present=instructions.is_file())
            if not root.exists() or (self.local_root is None and not result["runtime_present"]):
                result["status"] = "NOT_INITIALIZED"
            elif result["runtime_present"] and (
                    self.local_root is None or all(path.is_dir() for path in directories)):
                for path in (self.path, instructions, catalog):
                    if path.is_file():
                        Ledger(path).snapshot()
                result["status"] = "READY"
        except (Blocked, OSError, sqlite3.Error, ValueError, TypeError, RecursionError):
            pass
        return result

    def initialize(self):
        check_path(self.path)
        if self.local_root is not None and self.path == self.local_root / "state" / "p1.sqlite3":
            status = self.storage_status()["status"]
            if status == "READY":
                return
            if status != "NOT_INITIALIZED":
                raise Blocked("Local storage is incomplete or damaged; initialization blocked")
            self.local_root.mkdir()
            for name in LOCAL_DIRECTORIES:
                self.local_directory(name, create=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Existing databases, including empty or damaged ones, are never reset.
        with self.path.open("xb"):
            pass
        with closing(self.connect("rw")) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE anchor (version INTEGER, seq INTEGER, hash TEXT)")
            db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, payload TEXT, hash TEXT)")
            db.execute("INSERT INTO anchor VALUES (1, 0, '')")

    @staticmethod
    def verified(db):
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise Blocked("SQLite integrity check failed")
        events = []
        previous = ""
        for seq, payload, actual in db.execute("SELECT * FROM events ORDER BY seq"):
            event = json.loads(payload)
            expected = digest([seq, previous, event])
            if seq != len(events) + 1 or actual != expected:
                raise Blocked("Audit chain verification failed")
            events.append(event)
            previous = actual
        if db.execute("SELECT * FROM anchor").fetchall() != [(1, len(events), previous)]:
            raise Blocked("Audit anchor verification failed")
        return events, previous

    def snapshot(self, recover_journal=False):
        with closing(self.connect("rw" if recover_journal else "ro")) as db:
            db.execute("BEGIN")
            return self.verified(db)

    def read(self):
        return self.snapshot()[0]

    def update(self, choose_event):
        """Choose and append at most one event in a verified write transaction."""
        with closing(self.connect("rw")) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            events, tail = self.verified(db)
            event = choose_event(events, tail)
            if event is None:
                return len(events), tail
            seq = len(events) + 1
            current = digest([seq, tail, event])
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (seq, canonical(event), current))
            db.execute("UPDATE anchor SET seq=?, hash=?", (seq, current))
            return seq, current

    def append(self, expected_count, expected_tail, event, *, allow_shutdown=False, admission=None):
        def choose(events, tail):
            if (len(events), tail) != (expected_count, expected_tail):
                suffix = events[expected_count:]
                if (not allow_shutdown or not suffix or
                        any(item["kind"] != "shutdown_scheduled" for item in suffix)):
                    raise Blocked("Execution fence changed")
                prefix_hash = ""
                for sequence, item in enumerate(events[:expected_count], 1):
                    prefix_hash = digest([sequence, prefix_hash, item])
                if prefix_hash != expected_tail:
                    raise Blocked("Execution fence changed")
            if admission is not None:
                admission(events)
            return event

        return self.update(choose)
