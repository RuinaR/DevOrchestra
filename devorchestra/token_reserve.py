"""Local synthetic admission budget. Provider account quota is never inferred."""

DEFAULT_RESERVE = {"tokens": 0, "percent": 0}


def validate_reserve(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_RESERVE)
            or type(value["tokens"]) is not int or not 0 <= value["tokens"] <= 10**12
            or type(value["percent"]) is not int or not 0 <= value["percent"] <= 100):
        raise ValueError("Reserve requires integer tokens and percent (0–100)")


def reserve_status(limit, used, planned, policy, *, active=0, verified=True):
    reserve = max(policy["tokens"], (limit * policy["percent"] + 99) // 100)
    remaining = limit - used - active - reserve if verified else None
    return {
        "approved": limit, "used": used if verified else None,
        "active_reservation": active, "reserve": reserve, "next_role_max": planned,
        "available": remaining, "usage": "SYNTHETIC" if verified else "UNKNOWN",
        "account_quota": "UNKNOWN",
    }
