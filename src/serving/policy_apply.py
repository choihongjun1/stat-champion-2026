"""Frozen application-status CSV contract, joined to canonical policy IDs only."""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
from pathlib import Path

FINAL_POLICY_SHA256 = "0cfb716fbe9272efc816d34d2213354d3ed6cdccbc9851080eee7f40c3eefc2a"
FINAL_APPLY_SHA256 = "90a2b2134f54d4860481c8f84944ee615177250373abf9cd4bc119590eef5be3"
FINAL_CHECKED_AT = "2026-10-03"
# Source acceptance is broader than the public submission status contract.
PUBLIC_APPLY_STATUS = {"open": "open", "rolling": "open", "closed": "closed", "unknown": "unknown"}


def load_policy_apply(path, policy_ids, *, expected_sha256=None, checked_at=None):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("policy apply hash mismatch")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    required = {"id", "apply_status", "apply_end", "checked_at"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("missing policy apply columns")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("duplicate policy apply columns")
    rows = {}
    for row in reader:
        pid = row.get("id")
        if not pid or pid in rows or pid not in policy_ids:
            raise ValueError("missing, duplicate or unknown policy apply id")
        status, end, checked = row.get("apply_status"), row.get("apply_end") or None, row.get("checked_at")
        if status not in PUBLIC_APPLY_STATUS:
            raise ValueError("invalid policy apply status")
        try:
            date.fromisoformat(checked)
            if end is not None:
                date.fromisoformat(end)
        except (ValueError, TypeError):
            raise ValueError("invalid policy apply date") from None
        if checked_at is not None and checked != checked_at:
            raise ValueError("policy apply confirmation date mismatch")
        # Raw status stays in the private join result; project_policy only copies public fields.
        rows[pid] = dict(apply_status=PUBLIC_APPLY_STATUS[status], apply_end=end, checked_at=checked,
                         source_apply_status=status)
    if set(rows) != set(policy_ids):
        raise ValueError("incomplete policy apply ids")
    dates = {r["checked_at"] for r in rows.values()}
    if len(dates) != 1:
        raise ValueError("inconsistent policy apply confirmation dates")
    return rows, digest, next(iter(dates))
