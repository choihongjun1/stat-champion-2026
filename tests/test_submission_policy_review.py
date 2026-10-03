"""Internal policy review prose never crosses the submission boundary (synthetic only)."""
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

from scripts import check_claims
from src.data import config
from src.serving import submission_report as sr
from tests.test_submission_report import _record, _case


def review_fixture():
    record = _record()
    record["policies"][0].update(
        match_status="check_required",
        unverifiable_conditions=["합성 신청 가능 내부 조건 메모", "합성 추가 검수 조건"],
        check_note="합성 신청 가능 내부 판정 메모",
    )
    return record


def scan_case(tmp_path, case):
    path = tmp_path / "CASE-A.json"
    path.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    rules, allowlist = check_claims.load_config(
        config.REPO_ROOT / "configs/claims_rules.json", config.REPO_ROOT / "configs/claims_allowlist.json")
    return check_claims.scan([path], rules, allowlist)


def test_internal_review_prose_excluded_and_claims_unchanged(tmp_path):
    record = review_fixture()
    before = copy.deepcopy(record)
    application = {p["id"]: dict(apply_status="open", apply_end="2026-10-16", checked_at="2026-10-03")
                   for p in record["policies"]}
    case = _case(record, policy_apply=application)
    policy = case["policies"][0]
    assert record == before
    assert sr.validate_case(case) == []
    assert policy["match_status"] == "check_required"
    assert policy["unverified_condition_count"] == 2
    assert policy["linked_factor_ids"] == []
    assert {k: policy[k] for k in application[policy["id"]]} == application[policy["id"]]
    text = json.dumps(case, ensure_ascii=False)
    for field in ("unverifiable_conditions", "check_note"):
        assert field not in text
    assert "신청 가능" not in text
    assert all(note not in text for note in before["policies"][0]["unverifiable_conditions"])
    assert before["policies"][0]["check_note"] not in text
    assert scan_case(tmp_path, case)["findings"] == []
    # Reproduce the previous clone-all-fields boundary with the exact same fixture.
    legacy = copy.deepcopy(case)
    for field in ("unverifiable_conditions", "check_note"):
        legacy["policies"][0][field] = before["policies"][0][field]
    assert scan_case(tmp_path, legacy)["summary"]["by_rule"]["CL-12"] == 2


def test_matched_state_has_zero_unverified_conditions():
    record = review_fixture()
    record["policies"][0].update(match_status="matched", unverifiable_conditions=[], check_note=None)
    policy = _case(record)["policies"][0]
    assert policy["match_status"] == "matched" and policy["unverified_condition_count"] == 0


@pytest.mark.parametrize("field,value", [
    ("unverifiable_conditions", ["합成内部メモ"]), ("check_note", "internal review"),
    ("unverified_condition_count", 0), ("unverified_condition_count", 1.5),
])
def test_public_schema_rejects_internal_prose_or_invalid_count(field, value):
    case = _case(review_fixture())
    case["policies"][0][field] = value
    assert sr.validate_case(case)


def test_runtime_review_helper_uses_submission_projection(tmp_path):
    helper = Path(os.environ.get("DEMO_REVIEW_HELPER_PATH",
        config.REPO_ROOT.parent / "demo15/scripts/demo_review_gate.py"))
    if not helper.is_file():
        pytest.skip("Set DEMO_REVIEW_HELPER_PATH to the #64 runtime helper")
    spec = importlib.util.spec_from_file_location("demo_review_submission_regression", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gate = module.SubmissionGate(config.REPO_ROOT, config.REPO_ROOT)
    record = review_fixture()
    before = copy.deepcopy(record)
    result = gate.review([record], "a" * 64)
    assert record == before
    assert result["canonical_count"] == result["review_count"] == 1
    row = result["stores"][0]
    assert row["publication_guard_passed"] is True
    assert row["claims_passed"] is True
    case = gate.projection.project_case(record, label="A", data_kind="real", rule_stage="base",
                                        n_candidates=1, seed=20261004)
    scan = scan_case(tmp_path, case)
    assert scan["findings"] == [] and scan["summary"]["by_rule"].get("CL-12", 0) == 0
