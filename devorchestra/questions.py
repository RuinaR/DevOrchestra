"""Durable question records. Owner responses never grant an execution capability."""
from uuid import uuid4

from .lease import Blocked
from .policy import Capability


QUESTION_TYPES = ("CLARIFICATION", "PERMISSION", "DEPENDENCY", "DESIGN_DECISION", "BLOCKED")
AUTHORIZATION_TYPES = ("PERMISSION", "DEPENDENCY")


def _text(value, limit):
    return (type(value) is str and 0 < len(value) <= limit and bool(value.strip())
            and all(char.isprintable() for char in value))


def new_question(kind, prompt, reason, impact, recommendation, decline, *, expires, scope=None):
    if kind not in QUESTION_TYPES or not all(
        _text(value, 1000) for value in (prompt, reason, impact, recommendation, decline)
    ):
        raise Blocked("Invalid user question")
    if kind in AUTHORIZATION_TYPES:
        eligible = (Capability.READ.value, Capability.WRITE.value, Capability.BUILD.value,
                    Capability.TEST.value) if kind == "PERMISSION" else ("dependency-install",)
        if (type(scope) is not dict or set(scope) != {"capability", "target"}
                or scope["capability"] not in eligible or not _text(scope["target"], 512)):
            raise Blocked("Unsupported approval scope")
    elif scope is not None:
        raise Blocked("An informational question cannot carry authority")
    return {"id": uuid4().hex, "type": kind, "prompt": prompt, "reason": reason,
            "impact": impact, "recommendation": recommendation, "decline": decline,
            "expires": expires, "scope": dict(scope) if scope else None}


def pending_question(events):
    """Return the latest globally pending question, including its run binding."""
    for event in reversed(events):
        if event["kind"] in ("question_responded", "question_expired", "native_manager_question_responded",
                             "native_manager_question_expired", "cancelled", "run_started"):
            return None
        if event["kind"] in ("question_opened", "native_manager_result", "supervised_imported") and event.get("question"):
            return event
    return None


def validate_response(question, decision, answer):
    allowed = ("APPROVE", "DECLINE") if question["type"] in AUTHORIZATION_TYPES else (
        ("DECLINE",) if question["type"] == "BLOCKED" else ("ANSWER", "DECLINE")
    )
    if decision not in allowed or (not _text(answer, 2000) if decision == "ANSWER" else answer is not None):
        raise Blocked("Invalid response for this question type")
