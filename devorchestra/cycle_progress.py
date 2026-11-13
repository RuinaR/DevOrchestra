"""Controller-owned acceptance evidence shared by development backends."""
from .ledger import digest
from .lease import Blocked


def freeze(acceptance, bindings):
    checks = {} if bindings is None else bindings
    if type(checks) is not dict:
        raise Blocked("Criterion bindings must be an object keyed by criterion ID")
    criteria = {}
    for index, text in enumerate(acceptance, 1):
        key = f"C{index}-{digest(text)[:8]}"
        selectors = checks.get(key, [])
        if (type(selectors) is not list or len(selectors) > 20
                or any(type(item) is not dict or not (set(item) & {"input", "title"})
                       or set(item) - {"file", "input", "title"}
                       or any(type(value) not in {str, int, bool} for value in item.values())
                       for item in selectors)
                or len({digest(item) for item in selectors}) != len(selectors)):
            raise Blocked("Invalid frozen criterion observation selector")
        criteria[key] = {"text": text, "checks": selectors}
    if set(checks) - criteria.keys():
        raise Blocked("Criterion bindings contain an unapproved ID")
    return criteria


def compare(criteria, previous, verifier):
    """Use only Controller-observed verdicts and unambiguous owner-bound checks."""
    usable = (verifier.get("phase") == "verifier" and verifier.get("outcome") == "COMPLETED"
              and verifier.get("owned_tree") == "DRAINED"
              and verifier.get("verdict") in {"PASS", "FAIL", "NOT_VALIDATED"})
    observed = verifier.get("observations", []) if usable else []
    if verifier.get("runtime") == "mxc-dotnet-sdk-project-v1":
        # Test status is reported over stdout shared with the code under test.
        observed = [row for row in observed if row.get("input") == "BUILD"]
    current, evidence = {}, {}
    for key, criterion in criteria.items():
        states = []
        for check in criterion["checks"]:
            matched = [row for row in observed if all(field in row and digest(row[field]) == digest(value)
                                                      for field, value in check.items())]
            if len(matched) != 1 or not {"expected", "actual"} <= matched[0].keys():
                states.append(None)
            else:
                states.append(digest(matched[0]["expected"]) == digest(matched[0]["actual"]))
        if False in states:
            current[key] = "REGRESSED" if previous.get(key) in {"VERIFIED", "REGRESSED"} else "BLOCKED"
            evidence[key] = "FAILED"
        elif states and all(state is True for state in states):
            current[key] = "VERIFIED"
            evidence[key] = "PASSED"
        else:
            current[key] = "NOT_VERIFIED"
            evidence[key] = "MISSING" if states else "UNCONFIGURED"
    return {"criteria": current,
            "criterion_evidence": evidence,
            "newly_verified": [key for key in current if current[key] == "VERIFIED"
                               and previous.get(key) != "VERIFIED"],
            "regressed": [key for key in current if current[key] == "REGRESSED"
                          and previous.get(key) == "VERIFIED"],
            "verifier_sha256": digest(verifier)}


def failure_fingerprint(phase, feedback, environment):
    def normalize(value):
        return " ".join(value.casefold().split()) if isinstance(value, str) else value

    rows = feedback.get("observations", []) if isinstance(feedback, dict) else feedback
    facts = []
    for row in rows:
        if isinstance(row, dict) and {"expected", "actual"} <= row.keys():
            if digest(row["expected"]) != digest(row["actual"]):
                facts.append([row.get("title", row.get("file")), row.get("input"), row["expected"], row["actual"]])
        elif isinstance(row, dict):
            facts.append([normalize(row.get("location")), normalize(row.get("observed"))])
        else:
            facts.append(normalize(row))
    return digest([phase, environment, sorted(digest(fact) for fact in facts)])
