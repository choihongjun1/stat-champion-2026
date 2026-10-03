# -*- coding: utf-8 -*-
"""submission-static-0.1 projection·검증기 단위 테스트 (합성 레코드만)."""
import copy

import pytest

from src.serving import report_validation as rv
from src.serving import submission_report as sr
from tests.test_report_schema import _report_basic


def _record():
    rec = _report_basic()
    assert rv.validate_report(rec) == []
    return rec


def _case(rec=None, **kw):
    args = dict(label="A", data_kind="real", rule_stage="base", n_candidates=4, seed=20261004)
    args.update(kw)
    return sr.project_case(rec or _record(), **args)


def test_projection_keeps_public_fields_and_drops_the_rest():
    rec = _record()
    before = copy.deepcopy(rec)
    case = _case(rec)
    assert rec == before                                   # 원 레코드는 바꾸지 않는다
    assert sr.validate_case(case) == []
    assert case["public_id"] == "CASE-A" and case["case_title"] == "비식별 실제 사례 A"
    assert case["store"] == {"gu": rec["store"]["gu"], "biz_type": rec["store"]["biz_type"]}
    assert set(case["risk"]) == {"band", "percentile", "peer_group"}
    assert case["policies"] == [dict({k: v for k, v in p.items() if k not in {"unverifiable_conditions", "check_note"}},
                                         unverified_condition_count=len(p["unverifiable_conditions"]),
                                         linked_factor_ids=[], apply_status="unknown", apply_end=None,
                                         checked_at=None) for p in rec["policies"]] and case["disclaimer"] == rec["disclaimer"]
    assert sr.forbidden_key_paths(case) == []
    for f in case["factors"]:
        assert "contribution" not in f and "explanation" not in f and "values" not in f
    synthetic = _case(rec, data_kind="synthetic_sample", label="C")
    assert synthetic["public_id"] == "CASE-C" and "실제 점포 아님" in synthetic["case_title"]


def test_hidden_factor_has_no_direction_and_fixed_text():
    rec = _record()
    linked = {fid for p in rec["policies"] for fid in p["linked_factor_ids"]}
    i = next(i for i, f in enumerate(rec["factors"]) if f["factor_id"] not in linked)
    f = rec["factors"][i]
    f.update(display=False, hold_reason="data_missing", data_missing=True, missing_reason="out_of_trdar",
             display_note="데이터 없음", explanation=f"이 점포는 {f['name']} 데이터가 없어(상권 경계 밖) 이 요인은 진단하지 않습니다.")
    case = _case(rec)
    g = case["factors"][i]
    assert g["direction"] is None and g["level_text"] == "데이터 없음"
    assert g["summary_text"] == f"이 점포는 '{f['name']}' 데이터가 없어 이 요인은 진단하지 않습니다."


@pytest.mark.parametrize("label, text", [
    ("위험 증가", "높이는 쪽으로 작용했습니다"), ("위험 감소", "낮추는 쪽으로 작용했습니다"),
    ("영향 미미", "거의 영향을 주지 않았습니다")])
def test_summary_text_has_no_numbers(label, text):
    f = {"name": "업력", "display": True, "direction": label, "data_missing": False}
    s = sr.summary_text(f)
    assert text in s and not any(ch.isdigit() for ch in s) and "%" not in s and "원인" not in s


@pytest.mark.parametrize("mutate, msg", [
    (lambda c: c.update(store_id="GR_1"), "금지된 키"),
    (lambda c: c["store"].update(name="가상상호"), "금지된 키|Additional"),
    (lambda c: c["store"].update(address_road="서울특별시 마포구 가상로 1"), "금지된 키"),
    (lambda c: c["risk"].update(probability_12m=0.3), "금지된 키"),
    (lambda c: c["risk"].update(peer_median=0.1), "금지된 키"),
    (lambda c: c["factors"][0].update(contribution=0.01), "금지된 키"),
    (lambda c: c["factors"][0].update(summary_text="약 1.2%p 높였습니다"), "summary_text|고정 템플릿"),
    (lambda c: c.update(public_id="CASE-B"), "public_id"),
    (lambda c: c.update(public_id="GR_3000000-000-2020-00001"), "public_id"),
    (lambda c: c["factors"][0].update(sensitivity_label="해석 민감"), "sensitivity pair"),
    (lambda c: c["risk"].update(peer_group="마포구 망원동 일반음식점"), "peer_group"),
    (lambda c: c["selection"].update(rule_stage="none"), "rule_stage"),
])
def test_validator_rejects_leaks(mutate, msg):
    case = _case()
    mutate(case)
    errs = sr.validate_case(case)
    assert errs and any(__import__("re").search(msg, e) for e in errs), errs


def test_bad_labels_and_stages_are_refused():
    with pytest.raises(ValueError, match="case_label"):
        _case(label="D")
    with pytest.raises(ValueError, match="규칙 단계"):
        _case(rule_stage="none")
    rec = _record()
    rec["risk"]["band"] = "very_high"
    with pytest.raises(ValueError, match="internal report contract"):
        _case(rec)
