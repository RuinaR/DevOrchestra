"""SQLite event ledger. Every read validates one consistent snapshot."""
import hashlib
import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from .lease import Blocked


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
    def __init__(self, path: Path):
        self.path = path

    def connect(self, mode):
        check_path(self.path)
        return sqlite3.connect(self.path.as_uri() + f"?mode={mode}", uri=True, timeout=1)

    def initialize(self):
        check_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Existing databases, including empty or damaged ones, are never reset.
        with self.path.open("xb"):
            pass
        with closing(self.connect("rw")) as db, db:
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
