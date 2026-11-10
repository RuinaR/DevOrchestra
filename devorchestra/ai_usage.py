"""Separate provider quota signals, reported tokens, and local session budgets."""
from math import isfinite

from .lease import Blocked, execution_slot
from .ledger import digest

USAGE_URL = "https://chatgpt.com/settings/usage"
LIMIT_CODE = "subscription_sharing_usage_limit_exceeded"
ROLE_RESULTS = {"native_manager_result": "manager-planning", "subscription_developer_result": "developer",
                "subscription_cycle_result": None}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def response_usage(result):
    usage = result.get("usage")
    usage = usage if type(usage) is dict else {}
    detail = usage.get("input_tokens_details")
    return {"input": _count(usage.get("input_tokens")), "output": _count(usage.get("output_tokens")),
            "cached": _count(detail.get("cached_tokens")) if type(detail) is dict else None,
            "total": _count(usage.get("total_tokens"))}


def provider_status(error):
    code, status = getattr(error, "code", None), getattr(error, "status", None)
    if code == LIMIT_CODE:
        return "LIMIT_REACHED"
    if status == 401 or code in {"invalid_grant", "invalid_token"}:
        return "AUTH_REQUIRED"
    if status == 403:
        return "NO_PERMISSION"
    if status is not None or str(error).startswith(("ChatGPT subscription network", "ChatGPT subscription connection")):
        return "QUERY_FAILED"
    return None


def pause_event(events, account):
    account_ref = digest(account)
    for event in reversed(events):
        if event.get("usage_account") != account_ref:
            continue
        if event["kind"] == "subscription_usage_resumed":
            return None
        if event.get("provider_usage_status") == "LIMIT_REACHED":
            return event
    return None


def check_account(ledger, account):
    if pause_event(ledger.read(), account):
        raise Blocked("ChatGPT usage limit paused requests. Review Settings → Usage and explicitly resume.")


def resume(runner, account):
    with execution_slot() as abandoned:
        events, head = runner._idle_diagnostic(abandoned)
        paused = pause_event(events, account)
        if paused is None:
            raise Blocked("This registration has no recorded usage pause")
        runner.ledger.append(len(events), head, {"kind": "subscription_usage_resumed",
                                                "usage_account": digest(account), "pause_sha256": digest(paused)})


def check_connection(ledger, account):
    """Use the supported OAuth model-list route without starting inference."""
    from .chatgpt_subscription import AccountStore, list_models

    try:
        with execution_slot() as abandoned:
            if abandoned:
                return "BUSY"
            store = AccountStore(ledger)
            row = next((item for item in store.accounts() if item["account"] == account), None)
            if not row or not row["signed_in"]:
                return "AUTH_REQUIRED"
            if not row["subscription_granted"]:
                return "NO_PERMISSION"
            list_models(store.access(account))
            return "CONNECTED"
    except Blocked as error:
        if "expired" in str(error):
            return "AUTH_REQUIRED"
        return provider_status(error) or "QUERY_FAILED"


def codex_snapshot(limits, activity=None):
    """Allowlist public fields from official Codex account results without keeping raw payloads."""
    if type(limits) is dict and type(limits.get("result")) is dict:
        limits = limits["result"]
    if type(activity) is dict and type(activity.get("result")) is dict:
        activity = activity["result"]
    if type(limits) is not dict or not ({"rateLimits", "rateLimitsByLimitId"} & limits.keys()):
        raise ValueError("Codex rate-limit result required")
    buckets = limits.get("rateLimitsByLimitId")
    buckets = list(buckets.items()) if type(buckets) is dict and buckets else [("codex", limits.get("rateLimits"))]
    windows, reached = [], []
    for key, bucket in buckets[:32]:
        if type(bucket) is not dict:
            continue
        state = bucket.get("rateLimitReachedType")
        if type(state) is str and 0 < len(state) <= 64:
            reached.append({"bucket": str(key)[:48], "state": state})
        for name in ("primary", "secondary"):
            item = bucket.get(name)
            if type(item) is not dict:
                continue
            used = item.get("usedPercent")
            used = used if type(used) in (int, float) and 0 <= used <= 100 and isfinite(used) else None
            reset = _count(item.get("resetsAt"))
            windows.append({"bucket": str(key)[:48], "window": name, "minutes": _count(item.get("windowDurationMins")),
                            "used": used, "remaining": None if used is None else 100 - used,
                            "resets_at": reset if reset is not None and reset < 253402300799 else None})
    summary = activity.get("summary") if type(activity) is dict else None
    return {"windows": windows, "reached": reached,
            "lifetime_tokens": _count(summary.get("lifetimeTokens")) if type(summary) is dict else None}


def project_usage(events, project):
    history = [event for event in events if event.get("project_id") == project]
    started = next((e for e in reversed(history) if e["kind"] == "subscription_session_started"), None)
    session_id = started.get("development_session_id") if started else None
    ended = next((e for e in reversed(history) if e["kind"] == "subscription_session_ended"
                  and e.get("development_session_id") == session_id), None)
    model_rows = [e for e in history if e["kind"] in ROLE_RESULTS
                  and (e.get("backend") or e.get("manager_backend")) == "chatgpt-subscription-text"
                  and e.get("phase") != "verifier"]
    session_rows = [e for e in model_rows if session_id and e.get("development_session_id") == session_id]
    known = sum(_count(e.get("usage")) or 0 for e in session_rows)
    complete = all(_count(e.get("usage")) is not None for e in session_rows)
    if ended:
        complete = complete and ended.get("usage_complete") is True
    model_intents = {"native_manager_intent", "subscription_developer_intent", "subscription_cycle_intent"}
    settled = {e.get("run_id") for e in session_rows}
    if any(e["kind"] in model_intents and e.get("development_session_id") == session_id
           and e.get("phase") != "verifier" and e.get("run_id") not in settled for e in history if session_id):
        complete = False
    scope = started["scope"] if started else {}
    budget = scope.get("tokens")
    return {"account": scope.get("account"), "models": scope.get("models", {}), "budget": budget, "known": known,
            "remaining": max(0, budget - known) if budget is not None and complete else None,
            "project_reported": sum(_count(e.get("usage")) or 0 for e in model_rows),
            "project_unknown": sum(_count(e.get("usage")) is None for e in model_rows),
            "roles": [{"phase": e.get("phase") or ROLE_RESULTS[e["kind"]], "model": e.get("model"),
                       "total": _count(e.get("usage")), **{key: (e.get("usage_details") or {}).get(key)
                                                         for key in ("input", "output", "cached")}}
                      for e in session_rows]}
