"""W3-14/T3 public projection; internal reports remain schema 0.3."""
from copy import deepcopy

from src.serving import report_validation as rv

CONTRACT_VERSION = "public-static-0.1"
PRIVATE_KEYS = frozenset({"probability_12m", "ci_low", "ci_high", "interval_note"})
# A percentile is a rank, not a probability. peer_median is a probability and is omitted.
RISK_FIELDS = ("band", "percentile", "peer_group", "model", "calibrated")


def private_key_paths(value, path="") -> list[str]:
    """Reserved individual probability/interval keys are forbidden at any depth."""
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            location = f"{path}/{key}"
            if key in PRIVATE_KEYS:
                found.append(location)
            found.extend(private_key_paths(child, location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(private_key_paths(child, f"{path}/{index}"))
    return found


def validate_public_report(record: dict) -> list[str]:
    errors = [f"{p}: private probability/interval key in public report"
              for p in private_key_paths(record)]
    errors += rv.validate_def("public_report", record)
    return errors or rv.semantic_errors(record, check_private_risk=False)


def project_report(record: dict) -> dict:
    """Validate the canonical record first, then copy only the public risk contract.

    Fail closed if reserved keys leak elsewhere; do not silently alter factors/policies.
    The caller's record (and SQLite source) is never mutated.
    """
    errors = rv.validate_report(record)
    if errors:
        raise ValueError("internal report contract: " + " / ".join(errors[:3]))
    public = deepcopy(record)
    public["risk"] = {key: deepcopy(record["risk"][key]) for key in RISK_FIELDS}
    public["_public_contract_version"] = CONTRACT_VERSION
    errors = validate_public_report(public)
    if errors:
        raise ValueError("public report contract: " + " / ".join(errors[:3]))
    return public
