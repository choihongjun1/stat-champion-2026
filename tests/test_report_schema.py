# -*- coding: utf-8 -*-
"""W2-5 결과 스키마 검증 테스트.

레코드는 모두 손으로 지어낸 가린 값이다 (SAMPLE-NNN, 샘플로 N). 실제 점포·실제 예측과 무관하다.
형태는 PR #36 serve.py의 reports.jsonl(schema 0.1)에 W2-5 블록을 더한 schema 0.2 계약을 따른다.
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from src.serving import report_validation as rv

INTERVAL_NOTE = "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지의 범위이며, 폐업 확률 자체의 범위가 아닙니다."
DISCLAIMER = "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."


def _factor(fid, contribution, *, pp=50, display=True, data_missing=False, missing_reason=None,
            hold_reason=None, driver=None, values=None, explanation=None):
    name, cat, act = rv.FACTOR_CONTRACT[fid]
    if explanation is None:
        if data_missing:
            explanation = f"이 점포는 {name} 데이터가 없어(상권 경계 밖) 이 요인은 진단하지 않습니다."
        else:
            way = "높이는" if contribution > 0 else "낮추는"
            explanation = f"{name} 요인이 예측 위험도를 약 {abs(contribution) * 100:.1f}%p {way} 쪽으로 기여했습니다."
    return {
        "factor_id": fid, "name": name, "category": cat, "actionability": act,
        "contribution": contribution, "direction": rv.direction_of(contribution),
        "peer_percentile": pp, "explanation": explanation, "driver": driver, "values": values or {},
        "display": display, "data_missing": data_missing, "missing_reason": missing_reason,
        "hold_reason": hold_reason or ("data_missing" if data_missing else None),
        "display_note": None if display else "내부 메모",
    }


def _policy(pid="sample_online_2026", linked=("online_attention",), status="matched"):
    return {
        "id": pid, "name": "(예시) 온라인 판로 지원", "operator": "(예시) 운영기관", "link": "https://example.org",
        "eligibility_text": "업력 1년 이상", "announce_year": 2026, "collected_at": "2026-09-20",
        "match_status": status, "matched_by": ["biz_type", "tenure"],
        "unverifiable_conditions": ["연매출 3억 원 이하"] if status == "check_required" else [],
        "linked_factor_ids": list(linked), "check_note": "매출 조건 확인 필요" if status == "check_required" else None,
    }


def _report_basic():
    """상권 안 점포, 온라인 언급 감소가 위험을 올린 경우 (정책 요인 연결 있음)."""
    factors = [
        _factor("online_attention", 0.031, pp=92, driver="마지막 블로그 언급 이후 14개월",
                values={"online_blog_months_since_last": 14.0}),
        _factor("tenure", 0.012, values={"age_months": 30}),
        _factor("trdar_population", 0.004),
        _factor("district", 0.0, explanation="자치구는 이 점포의 예측 위험도에 거의 영향을 주지 않았습니다."),
        _factor("peer_sales", -0.002),
        _factor("trdar_vitality", -0.003),
        _factor("peer_competition", -0.004),
        _factor("store_profile", -0.011, values={"biz_type": "일반음식점", "area": 40.0, "has_coord": True}),
    ]
    return {
        "_schema_version": "0.2", "store_id": "SAMPLE-001", "score_origin": "2026Q2", "as_of": "2026-06-30",
        "store": {
            "biz_type": "일반음식점", "gu": "광진구", "dong": "샘플동", "name": "(샘플) 일반음식점 A",
            "address_road": "서울특별시 광진구 샘플로 1", "address_jibun": "서울특별시 광진구 샘플동 1",
            "license_date": "2023-01-01", "mdis_industry_code": None,
            "status": {"open_at_as_of": True, "current": "open", "close_date": None,
                       "license_snapshot_date": "2026-09-11"},
        },
        "risk": {"probability_12m": 0.1523, "ci_low": 0.121, "ci_high": 0.188, "interval_note": INTERVAL_NOTE,
                 "band": "mid", "percentile": 71, "peer_group": "광진구 일반음식점", "peer_median": 0.118,
                 "model": "detect_v0_enriched", "calibrated": False},
        "factors": factors,
        "unavailable_categories": ["비용"],
        "prescriptions": [],
        "online_presence": {"basis": "current_snapshot", "collected_at": "2026-09-20",
                            "naver_local_registered": True, "kakao_registered": None,
                            "naver_blog_total_12m": 0, "first_date_truncated": False,
                            "note": "현재 시점 스냅샷 — 표시 전용, 예측·진단에 쓰지 않음"},
        "policy_matching": "performed",
        "policies": [_policy(), _policy("sample_rent_2026", linked=(), status="check_required")],
        "disclaimer": DISCLAIMER,
    }


def _report_missing_and_hold():
    """상권 경계 밖 + 온라인 검수 대기 + 기준일 이후 폐업 + 이름 없음 + 처방 unavailable."""
    rec = _report_basic()
    rec["store_id"] = "SAMPLE-002"
    rec["store"].update(name=None, address_road=None, dong=None, license_date=None, gu="마포구",
                        biz_type="미용업", mdis_industry_code=None)
    rec["store"]["status"] = {"open_at_as_of": True, "current": "closed", "close_date": "2026-08-14",
                              "license_snapshot_date": "2026-09-11"}
    rec["risk"].update(peer_group="마포구 미용업", band="high", probability_12m=0.26, ci_low=0.2, ci_high=0.3,
                       percentile=None)
    rec["factors"] = [
        _factor("online_attention", 0.05, display=False, hold_reason="online_review",
                driver="최근 12개월 블로그 언급 90건"),
        _factor("tenure", 0.02),
        _factor("trdar_population", 0.006, display=False, data_missing=True, missing_reason="out_of_trdar",
                pp=None),
        _factor("peer_sales", 0.001, display=False, data_missing=True, missing_reason="out_of_trdar"),
        _factor("store_profile", -0.004),
    ]
    rec["prescriptions"] = [{
        "id": "ecommerce_channel", "title": "온라인 판매채널 도입", "related_factor_ids": ["online_attention"],
        "status": "unavailable", "unavailable_reason": "W3 효과 분석 전", "evidence_level": None,
        "effect_value": None, "effect_summary": None, "source": None, "caveat": None, "actionability": "owner",
    }]
    rec["online_presence"] = None
    rec["policies"] = [_policy(linked=("tenure",))]
    return rec


VALID = [_report_basic, _report_missing_and_hold]


# ---------------------------------------------------------------------------
def test_schema_file_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(rv.load_schema())


@pytest.mark.parametrize("make", VALID)
def test_valid_samples_pass(make):
    assert rv.validate_report(make()) == []


def test_factor_contract_matches_schema_enum():
    enum = rv.load_schema()["$defs"]["factor_id"]["enum"]
    assert list(rv.FACTOR_CONTRACT) == enum
    cats = set(rv.load_schema()["$defs"]["category"]["enum"])
    assert {c for _, c, _ in rv.FACTOR_CONTRACT.values()} == cats


def _mutate(make, fn):
    rec = copy.deepcopy(make())
    fn(rec)
    return rv.validate_report(rec)


def _set_factor(rec, fid, **kw):
    next(f for f in rec["factors"] if f["factor_id"] == fid).update(kw)


BROKEN = {
    # display / data_missing / missing_reason / hold_reason 구분
    "missing_without_reason": (_report_missing_and_hold,
                               lambda r: _set_factor(r, "trdar_population", missing_reason=None)),
    "missing_but_displayed": (_report_missing_and_hold,
                              lambda r: _set_factor(r, "trdar_population", display=True)),
    "reason_without_missing": (_report_basic, lambda r: _set_factor(r, "tenure", missing_reason="out_of_trdar")),
    "hold_without_reason": (_report_missing_and_hold,
                            lambda r: _set_factor(r, "online_attention", hold_reason=None)),
    "missing_with_online_hold": (_report_missing_and_hold,
                                 lambda r: _set_factor(r, "trdar_population", hold_reason="online_review")),
    "online_hold_marked_missing": (_report_missing_and_hold,
                                   lambda r: _set_factor(r, "online_attention", hold_reason="data_missing")),
    "old_w2_5_missing_code": (_report_missing_and_hold,
                              lambda r: _set_factor(r, "trdar_population", missing_reason="outside_trdar")),
    "display_true_with_missing_hold": (_report_basic, lambda r: _set_factor(r, "tenure", hold_reason="data_missing")),
    "rounded_below_boundary_increase": (_report_basic,
                                        lambda r: _set_factor(r, "district", contribution=0.0009, direction="위험 증가")),
    "hold_reason_on_displayed": (_report_basic,
                                 lambda r: _set_factor(r, "tenure", hold_reason="online_review")),
    "free_text_missing_reason": (_report_missing_and_hold,
                                 lambda r: _set_factor(r, "trdar_population", missing_reason="상권 경계 밖")),
    "unavailable_category_present": (_report_basic, lambda r: r["unavailable_categories"].append("경쟁")),
    # 처방: W3 전에는 효과 수치·available 금지
    "prescription_effect_value": (_report_missing_and_hold,
                                  lambda r: r["prescriptions"][0].update(effect_value=0.12)),
    "prescription_available": (_report_missing_and_hold,
                               lambda r: r["prescriptions"][0].update(status="available")),
    "prescription_evidence": (_report_missing_and_hold,
                              lambda r: r["prescriptions"][0].update(evidence_level="A")),
    # 정책: factor_id 연결 규칙 (FACTOR_POLICY_LINKS.md)
    "policy_linked_to_held_factor": (_report_missing_and_hold,
                                     lambda r: r["policies"][0].update(linked_factor_ids=["online_attention"])),
    "policy_linked_to_negative_factor": (_report_basic,
                                         lambda r: r["policies"][0].update(linked_factor_ids=["store_profile"])),
    "policy_linked_to_missing_factor": (_report_missing_and_hold,
                                        lambda r: r["policies"][0].update(linked_factor_ids=["trdar_population"])),
    "policy_unknown_factor": (_report_basic, lambda r: r["policies"][0].update(linked_factor_ids=["업력 구간"])),
    "policy_check_required_no_condition": (_report_basic,
                                           lambda r: r["policies"][1].update(unverifiable_conditions=[])),
    "policy_related_factor_name": (_report_basic, lambda r: r["policies"][0].update(related_factor="업력 구간")),
    # risk
    "ci_order": (_report_basic, lambda r: r["risk"].update(ci_low=0.2)),
    "peer_group_with_tenure": (_report_basic, lambda r: r["risk"].update(peer_group="광진구 일반음식점 업력 유사 구간")),
    "band_label": (_report_basic, lambda r: r["risk"].update(band="주의")),
    "interval_called_ci": (_report_basic, lambda r: r["risk"].update(interval_note="95% 신뢰구간입니다.")),
    # factors
    "unsorted_factors": (_report_basic, lambda r: r["factors"].reverse()),
    "duplicate_factor": (_report_basic, lambda r: r["factors"].append(copy.deepcopy(r["factors"][-1]))),
    "wrong_direction": (_report_basic, lambda r: _set_factor(r, "tenure", direction="위험 감소")),
    "rounded_zero_marked_increase": (_report_basic, lambda r: _set_factor(r, "district", direction="위험 증가")),
    "wrong_category": (_report_basic, lambda r: _set_factor(r, "online_attention", category="온라인 노출")),
    "causal_explanation": (_report_basic,
                           lambda r: _set_factor(r, "tenure", explanation="업력 때문에 위험이 높습니다.")),
    "driver_on_non_online": (_report_basic, lambda r: _set_factor(r, "tenure", driver="근거")),
    # store / 시점
    "as_of_not_quarter_end": (_report_basic, lambda r: r.update(as_of="2026-06-15")),
    "closed_before_as_of": (_report_missing_and_hold,
                            lambda r: r["store"]["status"].update(close_date="2026-05-01")),
    "open_with_close_date": (_report_basic, lambda r: r["store"]["status"].update(close_date="2026-08-01")),
    "admin_dong_field": (_report_basic, lambda r: r["store"].update(admin_dong="샘플1동")),
    "online_presence_as_feature": (_report_basic, lambda r: r["online_presence"].update(basis="feature")),
    "schema_version": (_report_basic, lambda r: r.update(_schema_version="0.1")),
    "policies_without_matching": (_report_basic, lambda r: r.update(policy_matching="not_performed")),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_broken_records_fail(case):
    make, fn = BROKEN[case]
    assert _mutate(make, fn), f"{case}: 검증을 통과하면 안 된다"


# ---------------------------------------------------------------------------
# PR #36 serve 출력 — 구버전 0.1과 현재 0.2 (최종 리포트 0.2와 다른 구조)
def _serve_v0_2():
    rec = _report_basic()
    out = {k: rec[k] for k in ("store_id", "score_origin", "as_of", "risk", "factors", "unavailable_categories",
                               "disclaimer")}
    out["_schema_version"] = "0.2"
    out["store"] = {k: rec["store"][k] for k in ("biz_type", "gu", "name", "address_road", "address_jibun",
                                                 "dong", "license_date")}
    return out


def test_serve_v0_2_is_the_build_input():
    assert rv.validate_def("serve_record_v0_2", _serve_v0_2()) == []
    hold = _report_missing_and_hold()
    serve = {k: hold[k] for k in _serve_v0_2()}
    serve["_schema_version"] = "0.2"
    serve["store"] = {k: hold["store"][k] for k in _serve_v0_2()["store"]}
    assert rv.validate_def("serve_record_v0_2", serve) == []


def test_final_report_is_not_accepted_as_serve_input():
    """버전 번호는 같아도 구조가 다르다 — 최종 리포트를 serve 입력으로 넣으면 거부한다."""
    errs = " ".join(rv.validate_def("serve_record_v0_2", _report_basic()))
    assert "policies" in errs and "prescriptions" in errs


@pytest.mark.parametrize("change", [
    {"missing_reason": "outside_trdar"},                       # 이전 W2-5 코드명
    {"hold_reason": None},                                     # data_missing인데 보류 사유 없음
    {"hold_reason": "online_review"},
])
def test_serve_v0_2_missing_factor_rules(change):
    serve = _serve_v0_2()
    serve["factors"][2].update(display=False, data_missing=True, missing_reason="out_of_trdar",
                               hold_reason="data_missing")
    assert rv.validate_def("serve_record_v0_2", serve) == []
    serve["factors"][2].update(change)
    assert rv.validate_def("serve_record_v0_2", serve)


def test_rounding_boundary_direction():
    """서빙은 반올림 전 값으로 방향을 정한다 — 반올림 후 정확히 ±0.0010이면 두 방향 모두 허용, 그 밖은 엄격."""
    rec = _report_basic()
    f = next(x for x in rec["factors"] if x["factor_id"] == "district")
    for c, d, ok in [(0.001, "영향 미미", True), (0.001, "위험 증가", True), (-0.001, "영향 미미", True),
                     (0.0009, "위험 증가", False), (0.0011, "영향 미미", False), (0.0, "영향 미미", True)]:
        f.update(contribution=c, direction=d)
        rec["factors"].sort(key=lambda x: -x["contribution"])
        errs = [e for e in rv.validate_report(rec) if "direction" in e]
        assert (errs == []) == ok, (c, d, errs)


def _serve_v0_1():
    rec = _report_basic()
    out = {k: rec[k] for k in ("store_id", "as_of", "risk", "unavailable_categories", "disclaimer")}
    out["_schema_version"] = "0.1"
    out["store"] = {k: rec["store"][k] for k in ("biz_type", "gu", "name", "address_road", "address_jibun",
                                                 "dong", "license_date")}
    out["factors"] = [{**{k: v for k, v in f.items() if k not in ("missing_reason", "hold_reason")},
                       # 0.1은 부호만 본다 (반올림된 0.0도 '위험 감소'/'위험 증가' 중 하나)
                       "direction": "위험 증가" if f["contribution"] > 0 else "위험 감소"}
                      for f in rec["factors"]]
    return out


def test_serve_v0_1_shape_is_accepted_as_build_input():
    assert rv.validate_def("serve_record_v0_1", _serve_v0_1()) == []


def test_serve_v0_1_is_not_a_final_report():
    """0.1 그대로는 최종 계약을 통과하지 못한다 — PR #36 출력 변경(missing_reason 등) + W2-5 블록이 필요하다."""
    errs = rv.validate_report(_serve_v0_1())
    assert errs
    msg = " ".join(errs)
    for key in ("score_origin", "prescriptions", "policies", "online_presence", "policy_matching"):
        assert key in msg


PR36_SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "samples" / "serve_2025Q2_trial" / "sample_reports.jsonl"


@pytest.mark.skipif(not PR36_SAMPLE.exists(), reason="PR #36 가린 샘플(docs/samples/serve_2025Q2_trial)이 아직 main에 없다")
def test_pr36_masked_samples_match_input_contract():
    from src.serving.build_db import INPUT_DEFS
    for line in PR36_SAMPLE.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        assert rv.validate_def(INPUT_DEFS[rec["_schema_version"]], rec) == []


# ---------------------------------------------------------------------------
# 빌드 입력·보조 산출물 정의
def test_policy_source_definition():
    src = {
        "id": "sample_online_2026", "name": "(예시) 온라인 판로 지원", "operator": "(예시) 운영기관",
        "link": "https://example.org", "announce_year": 2026, "collected_at": "2026-09-20",
        "eligibility_text": "업력 1년 이상",
        "conditions": {"gu": None, "biz_type": ["일반음식점", "휴게음식점", "미용업"],
                       "tenure_months_min": 12, "tenure_months_max": None},
        "unverifiable_conditions": [], "related_factor_ids": ["online_attention"],
    }
    assert rv.validate_def("policy_source", src) == []
    bad = copy.deepcopy(src)
    bad["related_factor_ids"] = ["온라인 노출 채널 수"]
    assert rv.validate_def("policy_source", bad)


def test_search_index_entry_has_no_risk():
    row = {"store_id": "SAMPLE-001", "name": "(샘플) 일반음식점 A", "name_norm": "샘플일반음식점a",
           "biz_type": "일반음식점", "gu": "광진구", "dong": "샘플동", "address_road": None,
           "address_jibun": "서울특별시 광진구 샘플동 1"}
    assert rv.validate_def("search_index_entry", row) == []
    for key in ("band", "probability_12m", "has_report"):
        assert rv.validate_def("search_index_entry", {**row, key: None})


def test_dong_summary_suppression():
    row = {"gu": "광진구", "dong": "샘플동", "biz_type": "미용업", "score_origin": "2026Q2", "as_of": "2026-06-30",
           "n_stores": 40, "suppressed": False, "band_share": {"low": 0.7, "mid": 0.2, "high": 0.1},
           "top_risk_biz_types": None, "suppression_reason": None, "note": "동 전반 현황이며 개별 가게 진단이 아닙니다."}
    assert rv.validate_def("dong_summary_row", row) == []
    hidden = {**row, "suppressed": True, "n_stores": None, "band_share": None, "suppression_reason": "small_cell"}
    assert rv.validate_def("dong_summary_row", hidden) == []
    assert rv.validate_def("dong_summary_row", {**hidden, "n_stores": 3})
    assert rv.validate_def("dong_summary_row", {**hidden, "suppression_reason": None})
    assert rv.validate_def("dong_summary_row", {**row, "top_risk_biz_types": ["미용업"]})  # 업종 행에는 순위 없음
