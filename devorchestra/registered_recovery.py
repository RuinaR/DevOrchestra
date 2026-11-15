"""Owner-approved recovery of interrupted registered runs."""
import json
import os
import re
import subprocess
from hashlib import file_digest
from time import time

from .lease import Blocked, execution_slot
from .ledger import digest


DRAINED_MARKER = "native_inspection_supervisor_drain"
RECOVERED = "native_inspection_safe_abort_recovered"
IDENTITY = ("project_id", "run_id", "request_id", "probe_kind", "approval_sha256")


def windows_boot_boundary(request, workspace):
    """Read the current full boot and look for replay of this exact local run."""
    if (os.name != "nt" or not isinstance(request, str) or not re.fullmatch(r"[0-9a-f]{32}", request)
            or not isinstance(workspace, str) or not re.fullmatch(r"native-command-[a-z0-9_]+", workspace)):
        raise Blocked("Windows boot evidence target is invalid")
    from .system_powershell import _system_path, hold_system_powershell

    # Only validated identifiers enter this fixed host query. No process arguments
    # or event payloads are returned to the Controller.
    command = r"""
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
$start = Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-General';Id=12} -MaxEvents 1
$type = Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Boot';Id=27} -MaxEvents 1
$data = ([xml]$type.ToXml()).Event.EventData.Data
$full = @($data | Where-Object Name -eq BootType)
if ($full.Count -ne 1 -or $full[0].'#text' -ne '0') { throw 'Not a full Windows boot' }
$process = Get-CimInstance Win32_Process -Filter "(CommandLine LIKE '%REQUEST%' OR CommandLine LIKE '%WORKSPACE%') AND ProcessId <> $PID"
$startup = Get-CimInstance Win32_StartupCommand -Filter "Command LIKE '%REQUEST%' OR Command LIKE '%WORKSPACE%'"
$task = Get-ScheduledTask | Where-Object {
    $_.State -ne 'Disabled' -and ($_.Actions | Where-Object {
        $_.Arguments -match 'REQUEST|WORKSPACE' -or $_.Execute -match 'REQUEST|WORKSPACE'
    })
}
if ($process -or $startup -or $task) { throw 'Old run may have restarted' }
function Epoch($value) { [int64]([DateTimeOffset]$value).ToUnixTimeSeconds() }
@{started=(Epoch $os.LastBootUpTime); start_event=(Epoch $start.TimeCreated);
  type_event=(Epoch $type.TimeCreated); start_record=$start.RecordId;
  type_record=$type.RecordId; boot_type=0} | ConvertTo-Json -Compress
""".replace("REQUEST", request).replace("WORKSPACE", workspace)
    try:
        with _system_path().open("rb") as stream:
            checksum = file_digest(stream, "sha256").hexdigest()
        # The existing helper pins the bytes and verifies the Microsoft signer.
        with hold_system_powershell(checksum) as (shell, recheck):
            result = subprocess.run(
                [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, timeout=20, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=shell.parent, env={**os.environ, "PSModulePath": str(shell.parent / "Modules")},
            )
            recheck()
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise Blocked("Full Windows boot or absence of old run could not be verified") from error


def reboot_target(events, project, request):
    """Only an interrupted read-only registered planning dispatch is eligible."""
    from .registered_cycle import KIND

    role = events[-1] if events else {}
    rows = [event for event in events if event.get("request_id") == request]
    start = rows[0] if rows else {}
    old = start.get("scope", {})
    if (
        not isinstance(request, str) or not re.fullmatch(r"[0-9a-f]{32}", request)
        or role.get("kind") != "native_inspection_role_intent"
        or role.get("phase") != "manager-planning"
        or role.get("cycle") != 1
        or role.get("role_call") != 1
        or not rows or role is not rows[-1]
        or (start.get("project_id"), start.get("run_id"), start.get("probe_kind")) != (project, request, KIND)
        or any(row.get(key) != start.get(key) for row in rows for key in IDENTITY)
        or start.get("kind") != "native_inspection_intent"
        or any(row["kind"] != "native_inspection_subscription_quota" for row in rows[1:-1])
        or len(rows) < 2
        or old.get("project_id") != project
        or digest(old) != start.get("approval_sha256")
        or "manager-planning" in old.get("shell_roles", ["manager-planning"])
        or old.get("research") is not None
        or old.get("resume") or old.get("candidate_plan")
        or old.get("original_apply") is not False
        or old.get("development_mode") != "DIRECT_GIT_WORKTREE"
        or not old.get("direct_git")
        or not re.fullmatch(r"native-command-[a-z0-9_]+", start.get("workspace", ""))
        or type(start.get("started_at")) is not int
    ):
        raise Blocked("Only the current read-only planning interruption is eligible")
    return start


def reboot_scope(events, tail, project, request, expires, boot, now):
    from .direct_git import pending

    start = reboot_target(events, project, request)
    old = start["scope"]
    if (type(expires) is not int or not 0 < expires - now <= 900
            or not isinstance(boot, dict)
            or set(boot) != {"started", "start_event", "type_event", "start_record", "type_record", "boot_type"}
            or any(type(value) is not int for value in boot.values()) or boot["boot_type"] != 0
            or not start["started_at"] < boot["started"] <= now
            or any(abs(boot[key] - boot["started"]) > 5 for key in ("start_event", "type_event"))
            or min(boot["start_record"], boot["type_record"]) <= 0
            or boot["start_record"] == boot["type_record"]
            or pending(events, project)
            or any(row["kind"].startswith("direct_git_") and row.get("run_id") == request for row in events)):
        raise Blocked("Windows boot, approval expiry or Git publication does not permit recovery")
    return {"project_id": project, "old_run_id": request, "old_request_id": request,
            "old_approval_sha256": start["approval_sha256"], "old_started_at": start["started_at"],
            "ledger_sequence": len(events), "ledger_head": tail, "boot": boot, "expires": expires,
            "checkout": old["replica"], "direct_git": old["direct_git"], "image_sha256": old["replica_sha256"],
            "model_response": "UNKNOWN", "provider_usage": "UNKNOWN"}


def bind_reboot(scope, boundary):
    from .ai_usage import QUOTA_STOP_PERCENT, SUBSCRIPTION_MODE

    if (scope.get("project_id") != boundary["project_id"] or scope.get("resume")
            or scope.get("candidate_plan") or scope.get("prior_replica_candidate")
            or "manager-planning" in scope.get("shell_roles", ["manager-planning"])
            or scope.get("research") is not None
            or scope.get("cycles") != 1 or scope.get("token_mode") != SUBSCRIPTION_MODE
            or scope.get("quota_stop_percent") != QUOTA_STOP_PERCENT
            or (scope.get("ledger_head"), scope.get("replica"), scope.get("direct_git"),
                scope.get("replica_sha256")) != (boundary["ledger_head"], boundary["checkout"],
                                                boundary["direct_git"], boundary["image_sha256"])):
        raise Blocked("The fresh one-cycle approval must match the preserved checkout and Git baseline")
    return {**scope, "reboot_boundary": boundary}


def reboot_admission(scope):
    """The fresh intent and its recovery boundary are one ledger transaction."""
    from .runtime import require_admission

    boundary = scope["reboot_boundary"]

    def admit(events):
        check = reboot_scope(events, scope["ledger_head"], scope["project_id"], boundary["old_request_id"],
                             boundary["expires"], boundary["boot"], int(time()))
        if check != boundary or bind_reboot(scope, check) != scope:
            raise Blocked("Reboot approval or checkout binding changed")
        require_admission(events, reboot_boundary=boundary)

    return admit


def reboot_predecessor(events, intent):
    """Verify a persisted boundary without treating it as historical Job drain."""
    from .registered_cycle import KIND

    try:
        scope = intent["scope"]
        boundary = scope["reboot_boundary"]
        identifier = digest(boundary)[:32]
        if ((intent["kind"], intent["probe_kind"], intent["request_id"], intent["run_id"],
             intent["project_id"], intent["approval_sha256"]) !=
            ("native_inspection_intent", KIND, identifier, identifier, scope["project_id"], digest(scope))
            or any(row.get("run_id") == identifier or row.get("request_id") == identifier for row in events)):
            return None
        tail = ""
        for seq, row in enumerate(events, 1):
            tail = digest([seq, tail, row])
        reviewed = reboot_scope(events, tail, scope["project_id"], boundary["old_request_id"],
                                boundary["expires"], boundary["boot"], intent["started_at"])
        if reviewed == boundary and bind_reboot(scope, reviewed) == scope:
            return boundary["old_request_id"]
    except (Blocked, KeyError, ValueError, TypeError):
        pass
    return None


def check_reboot_receipt(events, event):
    """Reject late receipts and Git results from a superseded request."""
    if not event["kind"].startswith(("native_inspection_", "direct_git_")):
        return
    if (event["kind"] == "native_inspection_intent" and event.get("scope", {}).get("reboot_boundary")
            and not reboot_predecessor(events, event)):
        raise Blocked("Invalid post-reboot execution intent")
    for index, intent in enumerate(events):
        scope = intent.get("scope")
        if intent["kind"] != "native_inspection_intent" or not isinstance(scope, dict):
            continue
        boundary = scope.get("reboot_boundary")
        if not isinstance(boundary, dict):
            continue
        old = boundary.get("old_request_id")
        identifiers = (event.get("run_id"), event.get("request_id"))
        if old and old in identifiers and reboot_predecessor(events[:index], intent):
            raise Blocked("Receipt belongs to a superseded pre-reboot request")
        if intent["run_id"] in identifiers and reboot_predecessor(events[:index], intent):
            if any(event.get(key) != intent.get(key) for key in IDENTITY):
                raise Blocked("Receipt identity differs from the post-reboot execution")
            if event["kind"].startswith("direct_git_"):
                bound = boundary["direct_git"]
                if (event.get("branch") != bound["ref"]
                        or event.get("origin", bound["url"]) != bound["url"]
                        or event.get("before", bound["head"]) != bound["head"]):
                    raise Blocked("Git receipt differs from the post-reboot approval")


def _scope(events, tail, project, run, request, expires):
    from .native_cycle import SUPERVISOR_CALLBACK_FAILED
    from .registered_cycle import KIND

    if len(events) < 3 or events[-1].get("kind") != DRAINED_MARKER:
        raise Blocked("The ledger has no current Controller drain marker")
    role, marker = events[-2:]
    starts = [row for row in events if row.get("kind") == "native_inspection_intent"
              and row.get("request_id") == request]
    intent = next((row for row in reversed(events[:-2]) if row.get("request_id") == request
                   and row.get("kind") == "native_inspection_role_intent"), {})
    original = starts[0].get("scope") if len(starts) == 1 else None
    if (len(starts) != 1 or (marker.get("project_id"), marker.get("run_id"), marker.get("request_id"),
                            marker.get("probe_kind")) != (project, run, request, KIND)
            or not all(isinstance(key, str) and key for key in (project, run, request))
            or any(row.get(key) != marker.get(key) for row in (starts[0], intent, role) for key in IDENTITY)
            or not isinstance(original, dict) or original.get("project_id") != project
            or digest(original) != marker.get("approval_sha256")
            or any(row.get("request_id") == request and row.get("kind") in {
                "native_inspection_result", RECOVERED} for row in events)
            or role.get("kind") != "native_inspection_role_result"
            or role.get("phase") != "manager-planning" or role.get("cycle") != marker.get("cycle")
            or (intent.get("phase"), intent.get("cycle")) != (role.get("phase"), role.get("cycle"))
            or role.get("process_stop_reason") != SUPERVISOR_CALLBACK_FAILED
            or role.get("process_result") != "TERMINATED_AND_DRAINED"
            or role.get("owned_tree") != "DRAINED" or marker.get("owned_tree") != "DRAINED"
            or marker.get("staged_evidence_retained") is not True):
        raise Blocked("The Controller drain evidence does not bind to the requested run")
    return {**{key: marker[key] for key in IDENTITY}, "expires": expires,
            "ledger_sequence": len(events), "ledger_head": tail,
            "role_receipt_sha256": digest(role), "drain_marker_sha256": digest(marker),
            "owned_tree": "DRAINED", "model_response": "UNKNOWN", "provider_usage": "UNKNOWN"}


def _receipt(scope, approved_at):
    return {**{key: scope[key] for key in IDENTITY}, "kind": RECOVERED, "state": "DIAGNOSTIC",
            "outcome": "RECOVERED_ABORT", "owned_tree": "DRAINED",
            "model_response": "UNKNOWN", "provider_usage": "UNKNOWN", "usage_complete": False,
            "recovery_scope": scope, "recovery_approval_sha256": digest(scope), "approved_at": approved_at}


def verifies(events, receipt):
    """Validate the recovery against the exact preceding ledger prefix during admission."""
    try:
        scope, approved_at = receipt["recovery_scope"], receipt["approved_at"]
        if (type(approved_at) is not int or type(scope["expires"]) is not int
                or not 0 < scope["expires"] - approved_at <= 900):
            return False
        tail = ""
        for seq, row in enumerate(events, 1):
            tail = digest([seq, tail, row])
        expected = _scope(events, tail, scope["project_id"], scope["run_id"], scope["request_id"], scope["expires"])
        return receipt == _receipt(expected, approved_at)
    except (Blocked, KeyError, TypeError):
        return False


def close(ledger, project, run, request, expires, *, approval=None):
    """Review without writing, or append one safe-abort receipt using the reviewed digest."""
    with execution_slot() as abandoned:
        if abandoned:
            raise Blocked("Abandoned slot requires independent ownership recovery")
        now = int(time())
        if type(expires) is not int or not 0 < expires - now <= 900:
            raise Blocked("Use a recovery approval that expires within fifteen minutes")
        events, tail = ledger.snapshot()
        scope = _scope(events, tail, project, run, request, expires)
        if approval is None:
            return {"scope": scope, "approve": digest(scope)}
        if approval != digest(scope):
            raise Blocked("Recovery approval or ledger head changed")
        receipt = _receipt(scope, now)
        seq, head = ledger.append(len(events), tail, receipt)
        return {**receipt, "sequence": seq, "ledger_head": head}
