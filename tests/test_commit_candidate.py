from contextlib import closing
from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile, ZipInfo

from devorchestra.adapter import Budget
from devorchestra.commit_candidate import (
    Approval, ChangeResult, ControllerTestReceipt, ROLES, nominate, recheck, require_live_commit_evidence,
)
from devorchestra.lease import Blocked
from devorchestra.ledger import Ledger, digest
from devorchestra.quarantine import FileSpec, package_inputs


def spec(path, data):
    return FileSpec(path, len(data), sha256(data).hexdigest())


class LiveEvidenceTests(unittest.TestCase):
    def test_provider_and_synthetic_claims_cannot_admit_live_commit(self):
        for evidence in ((), ("PASS",), ({"record": {"method": "turn/completed"}},),
                         ({"kind": "synthetic_commit_candidate"},)):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(Blocked, "Controller test receipts"):
                require_live_commit_evidence(evidence)
        plausible = ControllerTestReceipt("approval", "verifier", "replica", "before", "after",
                                          ("test",), 0, "drain", 1, "audit")
        with self.assertRaisesRegex(Blocked, "provenance and OS isolation"):
            require_live_commit_evidence((plausible,))


@unittest.skipUnless(os.name == "nt", "Windows ledger and global slot")
class CommitCandidateTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "synthetic.sqlite3")
        self.ledger.initialize()
        self.old, self.new = b"before", b"after"
        self.baseline = (spec("docs/note.txt", self.old),)
        self.manifest = (spec("docs/note.txt", self.new),)
        self.approval = Approval(
            "project", "session", "task", "run", 2, ("plan", "dev", "review", "verify", "final"),
            digest("frozen goal"), self.baseline, (("fixture-test",),),
            (Budget(2, 2, 0),) * 5, Budget(5, 5, 0), 10, 20, True,
        )
        self.approval_ref = digest(asdict(self.approval))
        self.before = package_inputs({"docs/note.txt": self.old}, self.baseline)
        self.after = package_inputs({"docs/note.txt": self.new}, self.manifest)

    def seed(self, *, bad_test=False, bad_review=False, bad_manager=False, extra_tokens=False):
        manifest_ref = digest([asdict(self.manifest[0])])
        payloads = [
            {"goal_hash": self.approval.goal_hash},
            {"manifest_ref": manifest_ref},
            {"verdict": "FAIL" if bad_review else "PASS", "manifest_ref": manifest_ref},
        ]
        tail = ""
        for index, (role, agent_id) in enumerate(zip(ROLES, self.approval.role_ids)):
            if index == 3:
                payloads.append({"manifest_ref": manifest_ref, "review_ref": digest(event),
                                 "tests": [{"argv": ["fixture-test"], "exit_code": int(bad_test)}]})
            if index == 4:
                payloads.append({"decision": "GOAL_COMPLETE", "goal_hash": self.approval.goal_hash,
                                 "evidence_ref": "forged" if bad_manager else digest(event)})
            event = {"kind": "synthetic_commit_evidence", "approval_ref": self.approval_ref,
                     "role": role, "agent_run_id": agent_id, "evidence": payloads[index],
                     "usage": [1, 2 if extra_tokens else 1, 0]}
            self.ledger.append(index, tail, event)
            tail = digest([index + 1, tail, event])
        self.result = ChangeResult(self.approval_ref, tail, self.manifest)

    def nominate(self, **overrides):
        args = dict(ledger=self.ledger, approval=self.approval, result=self.result,
                    before_bundle=self.before, after_bundle=self.after, clock=lambda: 15)
        return nominate(**(args | overrides))

    def test_nomination_reopen_replay_and_changed_bytes(self):
        self.seed()
        receipt = self.nominate()
        self.assertEqual(("SYNTHETIC", "BLOCKED"), (receipt["evidence_source"], receipt["live_commit"]))
        reopened = Ledger(self.ledger.path)
        recheck(reopened, self.approval, self.result, receipt, self.before, self.after, clock=lambda: 15)
        with self.assertRaises(Blocked):
            self.nominate(ledger=reopened)
        with self.assertRaises(Blocked):
            self.nominate(ledger=reopened, result=replace(self.result, audit_tail=reopened.snapshot()[1]))
        with self.assertRaises(Blocked):
            recheck(reopened, self.approval, self.result, receipt, self.before, self.before, clock=lambda: 15)
        with self.assertRaises(Blocked):
            recheck(reopened, self.approval, self.result, receipt, self.before, self.after, clock=lambda: 20)

    def test_inventory_path_binary_and_link_denials(self):
        self.seed()
        for path, data, attrs in (
            ("../note.txt", self.new, 0), ("c:/note.txt", self.new, 0),
            ("other.txt", self.new, 0), ("tool.exe", self.new, 0),
            ("docs/note.txt", b"\x00binary", 0),
            ("docs/note.txt", self.new, 0o120777 << 16),
            ("docs/note.txt", self.new, 0x400),
        ):
            with self.subTest(path=path, attrs=attrs), self.assertRaises(Blocked):
                member = ZipInfo(path)
                member.external_attr = attrs
                output = BytesIO()
                with ZipFile(output, "w") as archive:
                    archive.writestr(member, data)
                self.nominate(result=replace(self.result, after=(spec(path, data),)),
                              after_bundle=output.getvalue())
        self.assertEqual(5, len(self.ledger.read()))

    def test_stale_identity_hash_approval_and_corrupt_ledger(self):
        self.seed()
        for field, value in (("project_id", "foreign"), ("task_id", "previous"), ("generation", 3),
                             ("session_id", "old"), ("commit_allowed", False)):
            with self.subTest(field=field), self.assertRaises(Blocked):
                self.nominate(approval=replace(self.approval, **{field: value}))
        with self.assertRaises(Blocked):
            self.nominate(clock=lambda: 20)
        with self.assertRaises(Blocked):
            self.nominate(result=replace(self.result, audit_tail="forged"))
        with self.assertRaises(Blocked):
            self.nominate(result=replace(self.result, after=self.baseline))
        with self.assertRaisesRegex(Blocked, "Malformed change result"):
            self.nominate(result=replace(self.result, after=(FileSpec(None, 0, ""),)))
        extra = self.manifest + (spec("extra.txt", b"extra"),)
        mixed = package_inputs({"docs/note.txt": self.new, "extra.txt": b"extra"}, extra)
        with self.assertRaises(Blocked):
            self.nominate(after_bundle=mixed)
        with closing(self.ledger.connect("rw")) as db, db:
            db.execute("UPDATE events SET hash = 'damaged' WHERE seq = 1")
        with self.assertRaises(Blocked):
            self.nominate()

    def test_unverified_test(self):
        self.seed(bad_test=True)
        with self.assertRaisesRegex(Blocked, "Verifier"):
            self.nominate()

    def test_legacy_candidate_rechecks_as_synthetic(self):
        self.seed()
        receipt = {"kind": "synthetic_commit_candidate", "approval_ref": self.approval_ref,
                   "evidence_tail": self.result.audit_tail,
                   "manifest_ref": digest([asdict(self.manifest[0])])}
        self.ledger.append(5, self.result.audit_tail, receipt)
        recheck(self.ledger, self.approval, self.result, receipt, self.before, self.after, clock=lambda: 15)
        with self.assertRaises(Blocked):
            require_live_commit_evidence((receipt,))

    def test_failed_review(self):
        self.seed(bad_review=True)
        with self.assertRaisesRegex(Blocked, "Reviewer"):
            self.nominate()

    def test_forged_manager_evidence(self):
        self.seed(bad_manager=True)
        with self.assertRaisesRegex(Blocked, "Manager"):
            self.nominate()

    def test_total_budget(self):
        self.seed(extra_tokens=True)
        with self.assertRaisesRegex(Blocked, "Total budget"):
            self.nominate()
