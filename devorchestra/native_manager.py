"""One-turn, read-only Manager protocol. Live admission remains independently blocked."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import time

from .app_server import INSPECTION_CONFIG, open_app_server
from .instructions import instruction_bytes, selected_source
from .lease import Blocked
from .ledger import canonical, check_path
from .policy import live_preflight
from .role_contract import OUTPUT_SCHEMAS, bind_instructions, parse_role_output


ROLE = "manager-planning"


@dataclass(frozen=True)
class ManagerApproval:
    request_id: str
    expires_epoch: float
    binary: Path
    replica: Path
    home: Path
    layers: tuple[dict, ...]
    request_hash: str


def require_live_manager_admission():
    # A caller-supplied approval is never a substitute for verified OS evidence.
    raise Blocked(live_preflight()["reason"])


def prepare_manager(ledger, approval, request):
    from .mxc_launcher import _known_folder

    replica, home = approval.replica, approval.home
    if (request.role != "Manager" or request.scope.approval_receipts != (approval.request_id,)
            or request.scope.readable != (str(replica),) or request.scope.writable
            or request.scope.tools or request.scope.network
            or not request.model or request.model in ("mock-v1", "deterministic")
            or not request.reasoning or request.reasoning == "NOT_APPLICABLE"
            or not 0 < request.budget.seconds <= 60 or request.budget.tokens <= 0
            or request.budget.cost_microunits < 0 or request.deadline_utc_epoch is None
            or not time() < request.deadline_utc_epoch <= approval.expires_epoch):
        raise Blocked("Manager scope or budget is invalid")
    check_path(replica)
    check_path(home)
    temporary = ledger.local_directory("temp")
    state = ledger.state_boundary
    if (home.parent != temporary or home == replica or home.is_relative_to(replica)
            or state.is_relative_to(replica) or replica.is_relative_to(state)
            or not replica.is_dir() or not home.is_dir()):
        raise Blocked("Manager replica or private home is invalid")
    global_file, global_data = selected_source(home)
    project_file, project_data = selected_source(replica)
    if global_file.name != "AGENTS.md" or project_file.parent != replica:
        raise Blocked("Unexpected Manager instruction source")
    for path in (replica / ".codex", home / "config.toml"):
        check_path(path)
    if (replica / ".codex").exists():
        raise Blocked("Replica-specific Codex configuration requires review")
    config = home / "config.toml"
    with config.open("rb") as stream:
        if stream.read(len(INSPECTION_CONFIG) + 1) != INSPECTION_CONFIG.encode("ascii"):
            raise Blocked("Manager home configuration changed")
    directory = ledger.local_directory("instructions")
    common = instruction_bytes(directory / "common" / "orchestration-rules.md")
    role = instruction_bytes(directory / "roles" / "manager-planning.md")
    contents = (global_data, common, project_data, role)
    if len(approval.layers) != len(contents) or any(not blob.strip() for blob in contents):
        raise Blocked("Manager instruction layers are incomplete")
    binding = bind_instructions(request, approval.layers)
    if (binding != approval.request_hash
            or any(sha256(blob).hexdigest() != ref["sha256"]
                   for blob, ref in zip(contents, approval.layers))):
        raise Blocked("Manager request or instruction hash changed")
    # The global and project instructions are discovered by Codex, not sent again.
    messages = [
        {"type": "text", "text": common.decode("utf-8-sig")},
        {"type": "text", "text": role.decode("utf-8-sig")},
        {"type": "text", "text": canonical({
            "goal": request.goal, "verified_input_refs": request.verified_input_refs,
            "task_id": request.task_id, "generation": request.generation,
            "instruction_binding": binding,
            "constraints": "Planning only. Do not invoke tools, change files or request approval.",
        })},
    ]
    windows, local = (_known_folder(number) for number in (0x24, 0x1C))
    env = {"SystemRoot": str(windows), "WINDIR": str(windows), "SystemDrive": windows.drive,
           "LOCALAPPDATA": str(local), "ProgramData": str(_known_folder(0x23)),
           "PATH": str(windows / "System32"), "CODEX_HOME": str(home),
           "TEMP": str(home), "TMP": str(home)}
    return messages, env


def _collect_plan(server, request, replica, messages, before_send):
    policy = server.inspect_policy()
    if policy != {"sandbox": "read-only", "approval": "never", "managed_requirements_present": False}:
        raise Blocked("Manager App Server policy is not the approved profile")
    thread_notice = []

    def record_thread_notice(event):
        if thread_notice:
            raise Blocked("Unexpected additional Manager thread notification")
        thread_notice.append(event)

    thread = server.request(10, "thread/start", {
        "model": request.model, "cwd": str(replica), "approvalPolicy": "never",
        "sandbox": "read-only", "ephemeral": True,
    }, record_thread_notice)["thread"]
    thread_id = thread["id"]
    if (not isinstance(thread_id, str) or not thread_id
            or len(thread_notice) > 1
            or any(event.get("method") != "thread/started"
                   or event["params"]["thread"]["id"] != thread_id for event in thread_notice)):
        raise Blocked("Manager thread identity unverified")
    early = []

    def queue_event(event):
        if len(early) == 16:
            raise Blocked("Too many Manager events before turn acknowledgement")
        early.append(event)

    before_send()
    turn = server.request(11, "turn/start", {
        "threadId": thread_id, "cwd": str(replica), "model": request.model,
        "effort": request.reasoning, "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "access": {
            "type": "restricted", "includePlatformDefaults": False, "readableRoots": [str(replica)],
        }},
        "input": messages, "outputSchema": OUTPUT_SCHEMAS[ROLE],
    }, queue_event)["turn"]
    turn_id = turn["id"]
    if not isinstance(turn_id, str) or not turn_id or turn["status"] != "inProgress":
        raise Blocked("Manager turn acknowledgement invalid")
    reply = None
    for _ in range(4096):
        event = early.pop(0) if early else server.receive()
        params = event.get("params", {})
        method = event.get("method")
        if "id" in event or params.get("threadId") != thread_id:
            raise Blocked("Unexpected Manager event or approval request")
        if method in ("turn/started", "turn/completed"):
            if params["turn"]["id"] != turn_id:
                raise Blocked("Manager terminal event belongs to another turn")
            if method == "turn/completed":
                if params["turn"]["status"] != "completed" or reply is None or early:
                    raise Blocked("Manager result is incomplete")
                return reply
            continue
        if params.get("turnId") != turn_id:
            raise Blocked("Manager event belongs to another turn")
        if method in ("item/started", "item/completed"):
            item = params["item"]
            if item["type"] not in ("agentMessage", "userMessage", "reasoning"):
                raise Blocked("Manager attempted an unsupported tool or item")
            if method == "item/completed" and item["type"] == "agentMessage":
                if reply is not None:
                    raise Blocked("Manager returned more than one plan")
                reply = parse_role_output(ROLE, item["text"].encode("utf-8"))
        elif method not in ("item/agentMessage/delta", "thread/tokenUsage/updated"):
            raise Blocked("Unsupported Manager notification")
    raise Blocked("Manager event limit exceeded")


def run_owned_manager(ledger, approval, request, before_send, cancelled):
    messages, env = prepare_manager(ledger, approval, request)
    timeout = min(request.budget.seconds, request.deadline_utc_epoch - time())
    with open_app_server(approval.binary, approval.replica, env, timeout, cancelled,
                         explicit_mxc=True) as server:
        proposal = _collect_plan(server, request, approval.replica, messages, before_send)
    return proposal  # Context exit confirmed the owned Job and pipes drained.
