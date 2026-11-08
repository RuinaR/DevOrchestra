"""Durable ingress for a single trusted collector under the execution slot."""
from dataclasses import asdict
from datetime import datetime, timezone

from .event_audit import AuditCursor, normalize_packet
from .lease import Blocked
from .ledger import digest

OPEN_ROLE_EVENTS = frozenset(("role_started", "stream_event", "stream_interrupted"))


class EventJournal:
    """A receipt is acknowledged only after the supplied fenced append returns."""
    def __init__(self, binding, commit, *, fixture_usage=False):
        self.binding = binding
        self.commit = commit
        self.fixture_usage = fixture_usage
        self.cursor = AuditCursor()
        self.receipts = {}
        self.blocked = False

    @classmethod
    def restore(cls, binding, records, commit, *, fixture_usage=False):
        """Inspect a verified committed prefix. Reconnect cannot prove continuity."""
        journal = cls(binding, commit, fixture_usage=fixture_usage)
        for receipt in records:
            record = receipt["record"]
            if receipt["binding"] != asdict(binding):
                raise Blocked("Stored audit binding mismatch")
            journal.cursor.apply(record)
            journal.receipts[record["event_id"]] = receipt
        journal.blocked = True
        return journal

    def receive(self, packet):
        try:
            record = normalize_packet(self.binding, packet, fixture_usage=self.fixture_usage)
            fingerprint = digest(packet.message)
            previous = self.receipts.get(packet.event_id)
            if previous:
                if previous["fingerprint"] != fingerprint or previous["record"] != record:
                    raise Blocked("Conflicting event with same ID")
                return "DUPLICATE"
            if self.blocked:
                raise Blocked("Audit continuity unverified; new events blocked")
            receipt = {"binding": asdict(self.binding), "record": record, "fingerprint": fingerprint,
                       "received_at": datetime.now(timezone.utc).isoformat()}
            self.cursor.apply(record)
            self.commit("stream_event", receipt)
        except (Exception, KeyboardInterrupt):
            # Discard this cursor after a validation/write error. The runtime's
            # durable role intent independently blocks admission after a crash.
            self.blocked = True
            raise
        self.receipts[packet.event_id] = receipt
        return "COMMITTED"

    def disconnect(self):
        self.blocked = True
        self.commit("stream_interrupted", {"binding": asdict(self.binding),
                                           "reason": "CONTINUITY_UNVERIFIED"})

    def fixture_settlement(self, *, expected_status, resources_released):
        summary = self.cursor.summary()
        if (not self.fixture_usage or self.blocked or not resources_released
                or summary["provider_status"] != expected_status or summary["open_items"]
                or summary["open_requests"] or not summary["synthetic_usage_final"]):
            raise Blocked("Synthetic audit cannot settle or release the role")
        return summary