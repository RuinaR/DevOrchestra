"""Offline contract for one tool-free OpenAI Responses Manager request."""
from dataclasses import dataclass, field
from hashlib import sha256
import json

from .lease import Blocked
from .ledger import canonical
from .role_contract import OUTPUT_SCHEMAS, _unique_object, parse_role_output


ENDPOINT = "https://api.openai.com/v1/responses"
ROLE = "manager-planning"
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class ManagerAPIDraft:
    body: bytes = field(repr=False)
    sha256: str
    endpoint: str = ENDPOINT


def build_manager_api_draft(request, instructions, task, *, context_limit, output_limit):
    if (type(context_limit) is not int or type(output_limit) is not int or output_limit < 1024
            or request.budget.cost_microunits <= 0):
        raise Blocked("API Manager requires explicit context, output and cost allowances")
    schema = OUTPUT_SCHEMAS[ROLE]
    fields = dict(schema["properties"], disposition={"type": "string", "enum": ["PROPOSED", "CLARIFICATION"]})
    payload = {
        "model": request.model, "reasoning": {"effort": request.reasoning},
        "input": [{"role": "developer", "content": text} for text in instructions]
        + [{"role": "user", "content": task}],
        "tools": [], "tool_choice": "none", "parallel_tool_calls": False,
        "store": False, "background": False, "truncation": "disabled", "max_output_tokens": output_limit,
        "text": {"format": {"type": "json_schema", "name": "manager_plan", "strict": True,
                            "schema": dict(schema, properties=fields)}},
    }
    body = canonical(payload).encode("ascii")
    if len(body) > MAX_BODY_BYTES or len(body) + output_limit > min(context_limit, request.budget.tokens):
        raise Blocked("API Manager input exceeds its local sizing allowance")
    return ManagerAPIDraft(body, sha256(body).hexdigest())


def parse_manager_api_result(raw, *, model, output_limit, token_limit):
    if type(raw) is not bytes or len(raw) > MAX_BODY_BYTES:
        raise Blocked("Invalid API Manager response size")
    try:
        response = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise Blocked("Invalid API Manager response JSON") from None
    if (type(response) is not dict or response.get("object") != "response"
            or response.get("status") != "completed" or response.get("model") != model
            or response.get("error") is not None or response.get("incomplete_details") is not None
            or response.get("tools") != [] or response.get("tool_choice") != "none"
            or type(response.get("output")) is not list):
        raise Blocked("API Manager response does not match the tool-free contract")
    reply = None
    for item in response["output"]:
        if type(item) is dict and item.get("type") == "reasoning":
            continue
        if (reply is not None or type(item) is not dict or item.get("type") != "message"
                or item.get("role") != "assistant" or item.get("status") != "completed"
                or type(item.get("content")) is not list or len(item["content"]) != 1):
            raise Blocked("Unexpected API Manager output item")
        content = item["content"][0]
        if type(content) is not dict or content.get("type") != "output_text" or type(content.get("text")) is not str:
            raise Blocked("API Manager output text is unverified")
        try:
            reply = parse_role_output(ROLE, content["text"].encode("utf-8"))
        except UnicodeError:
            raise Blocked("API Manager output text is unverified") from None
    if (not reply or reply["disposition"] not in ("PROPOSED", "CLARIFICATION")
            or (reply["disposition"] == "PROPOSED" and not reply["acceptance_checks"])
            or (reply["disposition"] == "CLARIFICATION" and
                (len(reply["task_spec"]) > 1000 or reply["proposed_paths"] or reply["acceptance_checks"]))):
        raise Blocked("Invalid first API Manager plan")
    usage = response.get("usage")
    if type(usage) is not dict:
        raise Blocked("API Manager usage is unverified")
    inputs, outputs, total = (usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens"))
    if (any(type(value) is not int or value < 0 for value in (inputs, outputs, total))
            or total != inputs + outputs or outputs > output_limit or total > token_limit):
        raise Blocked("API Manager usage is inconsistent or exceeds its allowance")
    return reply, {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": total,
                   "cost_microunits": "UNKNOWN"}
