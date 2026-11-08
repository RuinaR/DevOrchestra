"""Offline App Server-shaped event audit. No transport, credentials or authority."""
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
    # All three fields below are assigned by the trusted local collector.
    binding: Binding
    sequence: int
    event_id: str
    message: dict


def normalize_stream(binding, packets):
    """Replay one bounded role stream. Provider completion never proves handoff."""
    if not 1 <= len(packets) <= 1024:
        raise Blocked("Audit stream size outside contract")
    ids, pending, seen, requests, audit = set(), {}, set(), set(), []
    started = False
    terminal = None
    for expected, packet in enumerate(packets, 1):
        if (packet.binding != binding or type(packet.sequence) is not int or packet.sequence != expected
                or not isinstance(packet.event_id, str) or not packet.event_id or packet.event_id in ids
                or terminal is not None or not isinstance(packet.message, dict)):
            raise Blocked("Unbound, duplicate or unordered audit event")
        ids.add(packet.event_id)
        message = packet.message
        method, params = message.get("method"), message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise Blocked("Invalid audit event parameters")
        record = {"sequence": expected, "event_id": packet.event_id, "method": method}
        if method in TURN:
            turn = params.get("turn", {})
            if (not isinstance(turn, dict) or turn.get("id") != binding.turn_id
                    or params.get("threadId", binding.thread_id) != binding.thread_id):
                raise Blocked("Audit turn mismatch")
            status = turn.get("status")
            if method == "turn/started" and not started and status == "inProgress":
                started = True
            elif method == "turn/completed" and started and not pending and status in (
                    "completed", "failed", "interrupted"):
                terminal = status
            else:
                raise Blocked("Invalid audit turn transition")
            record["status"] = status
        else:
            if (not started or params.get("threadId") != binding.thread_id
                    or params.get("turnId") != binding.turn_id):
                raise Blocked("Audit event context mismatch")
            if method in ITEM:
                item = params.get("item", {})
                if not isinstance(item, dict):
                    raise Blocked("Invalid audit item")
                identity, kind = item.get("id"), item.get("type")
                if not isinstance(identity, str) or not identity or not isinstance(kind, str) or kind not in ITEM_TYPES:
                    raise Blocked("Unsupported audit item")
                if method == "item/started" and identity not in seen:
                    seen.add(identity)
                    pending[identity] = kind
                elif method == "item/completed" and pending.get(identity) == kind:
                    del pending[identity]
                else:
                    raise Blocked("Duplicate or missing audit item")
                record.update(item_ref=digest(identity), item_type=kind)
                if kind == "commandExecution" and method == "item/completed":
                    code = item.get("exitCode")
                    record["exit_code"] = code if type(code) is int else "UNKNOWN"
            elif method in APPROVAL:
                request_id, item_id = message.get("id"), params.get("itemId")
                if (type(request_id) not in (str, int) or request_id in requests
                        or not isinstance(item_id, str) or not item_id):
                    raise Blocked("Duplicate or invalid approval request")
                if (method != "item/permissions/requestApproval"
                        and pending.get(item_id) != method.split("/")[1]):
                    raise Blocked("Approval request has no matching open item")
                requests.add(request_id)
                record.update(item_ref=digest(item_id), decision="NOT_GRANTED")
            elif method in UPDATES:
                if method == "turn/diff/updated" and isinstance(params.get("diff"), str):
                    record["diff_ref"] = digest(params["diff"])
                # The public guide confirms the usage notification, but does not
                # pin its counter schema. Never infer a value or add snapshots.
            else:
                raise Blocked("Unsupported audit event method")
        audit.append(record)
    return {"provider_status": terminal or "UNKNOWN", "tokens": "UNKNOWN", "cost": "UNKNOWN",
            "handoff": "UNKNOWN", "events": audit}
