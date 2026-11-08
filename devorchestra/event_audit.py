"""Sanitized App Server-shaped audit receipts and local synthetic accounting."""
from dataclasses import dataclass

from .lease import Blocked
from .ledger import digest

TURN = {"turn/started", "turn/completed"}
ITEM = {"item/started", "item/completed"}
APPROVAL = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval",
            "item/permissions/requestApproval"}
UPDATES = {"item/agentMessage/delta", "turn/plan/updated", "turn/diff/updated",
           "thread/tokenUsage/updated"}
ITEM_TYPES = {"commandExecution", "fileChange", "agentMessage", "plan", "reasoning",
              "userMessage", "functionCallOutput"}
FIXTURE_USAGE = "devorchestra.fixture/usage"


@dataclass(frozen=True)
class Binding:
    project_id: str
    development_id: str | None
    run_id: str
    generation: int
    agent_run_id: str
    context_id: str
    thread_id: str
    turn_id: str


@dataclass(frozen=True)
class Packet:
    binding: Binding
    sequence: int
    event_id: str
    message: dict


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 256


def _request_ref(value):
    if type(value) is not int and not _identifier(value):
        raise Blocked("Invalid approval request ID")
    return digest(value)


def normalize_packet(binding, packet, *, fixture_usage=False):
    if (packet.binding != binding or type(packet.sequence) is not int
            or not 1 <= packet.sequence <= 1024 or not _identifier(packet.event_id)
            or not isinstance(packet.message, dict)):
        raise Blocked("Unbound or malformed audit event")
    message = packet.message
    method, params = message.get("method"), message.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        raise Blocked("Invalid audit event parameters")
    record = {"sequence": packet.sequence, "event_id": packet.event_id, "method": method}
    if method in TURN:
        turn = params.get("turn", {})
        if (not isinstance(turn, dict) or turn.get("id") != binding.turn_id
                or params.get("threadId", binding.thread_id) != binding.thread_id):
            raise Blocked("Audit turn mismatch")
        status = turn.get("status")
        if status not in ("inProgress", "completed", "failed", "interrupted"):
            raise Blocked("Unknown turn status")
        record["status"] = status
        return record
    if (params.get("threadId") != binding.thread_id
            or params.get("turnId", binding.turn_id if method == "serverRequest/resolved" else None)
            != binding.turn_id):
        raise Blocked("Audit event context mismatch")
    if method in ITEM:
        item = params.get("item")
        if not isinstance(item, dict):
            raise Blocked("Invalid audit item")
        identity, kind = item.get("id"), item.get("type")
        if not _identifier(identity) or not isinstance(kind, str) or kind not in ITEM_TYPES:
            raise Blocked("Unsupported audit item")
        record.update(item_ref=digest(identity), item_type=kind)
        if kind in ("commandExecution", "fileChange"):
            status = item.get("status")
            record["item_status"] = status if status in ("inProgress", "completed", "failed", "declined") else "UNKNOWN"
        if kind == "commandExecution" and method == "item/completed":
            code = item.get("exitCode")
            record["exit_code"] = code if type(code) is int else "UNKNOWN"
    elif method in APPROVAL:
        if not _identifier(params.get("itemId")):
            raise Blocked("Invalid approval item")
        record.update(item_ref=digest(params["itemId"]), request_ref=_request_ref(message.get("id")),
                      decision="NOT_GRANTED")
    elif method == "serverRequest/resolved":
        record.update(request_ref=_request_ref(params.get("requestId")), decision="RESOLVED_NOT_PROOF_OF_GRANT")
    elif method in UPDATES:
        if method == "turn/diff/updated" and isinstance(params.get("diff"), str):
            record["diff_ref"] = digest(params["diff"])
    elif method == FIXTURE_USAGE and fixture_usage:
        for key in ("tokens", "cost_microunits"):
            amount = params.get(key)
            if type(amount) is not int or not 0 <= amount <= 10**12:
                raise Blocked("Invalid synthetic usage snapshot")
            record[key] = amount
        if type(params.get("final")) is not bool:
            raise Blocked("Synthetic usage final marker required")
        record["final"] = params["final"]
    else:
        raise Blocked("Unsupported audit event method")
    return record


class AuditCursor:
    def __init__(self):
        self.pending = {}
        self.seen_items = set()
        self.requests = {}
        self.ids = set()
        self.started = False
        self.terminal = None
        self.usage_seen = False
        self.tokens = None
        self.cost = None
        self.final_usage = False

    def apply(self, record):
        method, event_id = record["method"], record["event_id"]
        if record["sequence"] != len(self.ids) + 1 or event_id in self.ids or self.terminal:
            raise Blocked("Duplicate or unordered audit event")
        if method == "turn/started":
            if self.started or record["status"] != "inProgress":
                raise Blocked("Invalid audit turn transition")
            self.started = True
        elif not self.started:
            raise Blocked("Audit turn not started")
        elif method == "turn/completed":
            if self.pending or record["status"] == "inProgress":
                raise Blocked("Incomplete audit turn")
            self.terminal = record["status"]
        elif method in ITEM:
            identity, kind = record["item_ref"], record["item_type"]
            if method == "item/started" and identity not in self.seen_items:
                self.seen_items.add(identity)
                self.pending[identity] = kind
            elif method == "item/completed" and self.pending.get(identity) == kind:
                del self.pending[identity]
            else:
                raise Blocked("Duplicate or missing audit item")
        elif method in APPROVAL:
            request = record["request_ref"]
            if (request in self.requests or (method != "item/permissions/requestApproval"
                    and self.pending.get(record["item_ref"]) != method.split("/")[1])):
                raise Blocked("Duplicate or unmatched approval request")
            self.requests[request] = False
        elif method == "serverRequest/resolved":
            request = record["request_ref"]
            if self.requests.get(request) is not False:
                raise Blocked("Unmatched or repeated request resolution")
            self.requests[request] = True
        elif method == "thread/tokenUsage/updated":
            self.usage_seen = True
        elif method == FIXTURE_USAGE:
            if self.final_usage:
                raise Blocked("Synthetic usage already final")
            self.tokens = max(self.tokens or 0, record["tokens"])
            self.cost = max(self.cost or 0, record["cost_microunits"])
            self.final_usage = record["final"]
        self.ids.add(event_id)

    def summary(self):
        return {
            "provider_status": self.terminal or "UNKNOWN", "tokens": "UNKNOWN", "cost": "UNKNOWN",
            "provider_usage": "PARTIAL_UNVERIFIED" if self.usage_seen else "UNMEASURED",
            "synthetic_tokens": self.tokens, "synthetic_cost_microunits": self.cost,
            "synthetic_usage_final": self.final_usage,
            "open_items": len(self.pending), "open_requests": sum(not resolved for resolved in self.requests.values()),
            "handoff": "UNKNOWN",
        }


def normalize_stream(binding, packets):
    """Compatibility view for the P2.3 offline replay contract."""
    if not 1 <= len(packets) <= 1024:
        raise Blocked("Audit stream size outside contract")
    cursor = AuditCursor()
    audit = []
    for packet in packets:
        record = normalize_packet(binding, packet)
        cursor.apply(record)
        audit.append(record)
    return {**cursor.summary(), "events": audit}
