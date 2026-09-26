"""W2-5 합성 샘플 10건 — 최종 0.2 계약을 따르는 완전한 합성 점포로 정적 번들 2개를 만든다.

실제 인허가·서빙 결과에서 가져와 가리는 방식이 아니다. 점포(SAMPLE-NNN, '(샘플)' 상호, '가상로' 주소), 위험도, 요인 기여,
정책('(예시)')은 모두 지어낸 값이며, 설명문에 '[합성 예시]'를 붙인다. 처방은 W3 전이라 빈 배열이고 근거 등급·효과 수치는 없다.
리포트 스키마는 `_dummy` 키를 허용하지 않으므로(계약을 완화하지 않는다) 합성 표시는 meta/manifest의
`data_kind = synthetic_sample`·`publication_note`와 `sample_cases.json`·README에 둔다.

정책 매칭 여부(`policy_matching`)는 실행(run) 단위라 번들을 둘로 나눈다.
- `bundle/`            SAMPLE-001~009, 정책 원천 있음 (performed)
- `bundle_no_policy/`  SAMPLE-010, 정책 원천 없음 (not_performed)

실행 (저장소의 docs/samples/w2-5/를 다시 만든다):
    python -m src.serving.synthetic_samples [--out docs/samples/w2-5]
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import pandas as pd

from src.data import config
from src.data.names import normalize_name
from src.serving import build_db as bd
from src.serving import export_static as ex
from src.serving import report_validation as rv

DEFAULT_OUT = config.REPO_ROOT / "docs" / "samples" / "w2-5"
SCORE_ORIGIN, AS_OF = "2026Q2", "2026-06-30"
SNAPSHOT, UPDATED = "2026-09-11", "2026-09-10 00:00:00"
SAMPLE_MIN_CELL_N = 2  # 합성 번들 시연용 (provisional). 실제 하한값이 아니다
TAG = " [합성 예시]"
INTERVAL_NOTE = "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지의 범위이며, 폐업 확률 자체의 범위가 아닙니다."
DISCLAIMER = "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."
REASON_TEXT = {"outside_trdar": "상권 경계 밖", "no_sales_disclosed": "해당 상권에 이 업종 매출 공개 자료 없음",
               "online_unobserved": "관측 불가"}
BAND_P = {"low": (0.061, 0.045, 0.079), "mid": (0.172, 0.141, 0.205), "high": (0.284, 0.236, 0.331)}

# 요인 기여 기본값 (합성). 시나리오별로 덮어쓴다.
BASE_FACTORS = {"tenure": 0.012, "store_profile": 0.006, "district": 0.0004, "trdar_population": -0.002,
                "trdar_vitality": 0.003, "online_attention": -0.004, "peer_competition": 0.002, "peer_sales": -0.001}

# (번호, 번들, 업종, 구, 법정동, 상호, 도로명, 지번, 인허가일, 폐업일, 등급, 요인 덮어쓰기, 온라인, 사례)
SAMPLES = [
    (1, "bundle", "일반음식점", "마포구", "망원동", "(샘플) 가상식당 망원점", "서울특별시 마포구 가상로 101", None,
     "2021-03-02", None, "low", {}, "registered", ["band_low", "duplicate_name"]),
    (2, "bundle", "일반음식점", "마포구", "합정동", "(샘플) 가상식당 합정점", "서울특별시 마포구 가상로 202", None,
     "2019-07-15", None, "mid", {"tenure": 0.031}, "registered", ["band_mid", "duplicate_name"]),
    (3, "bundle", "휴게음식점", "광진구", "화양동", "(샘플) 가상카페", "서울특별시 광진구 가상로 1",
     "서울특별시 광진구 화양동 가상번지 1", "2024-02-01", None, "high",
     {"tenure": 0.052, "online_attention": 0.041}, "registered", ["band_high", "address_distinct"]),
    (4, "bundle", "미용업", "광진구", "화양동", "(샘플) 가상미용실", "서울특별시 광진구 가상로 12",
     "서울특별시 광진구 화양동 가상번지 12", "2018-05-20", None, "low", {}, "absent",
     ["band_low", "address_distinct", "online_presence_absent"]),
    (5, "bundle", "일반음식점", "영등포구", "여의도동", "(샘플) 상권밖식당", None, "서울특별시 영등포구 여의도동 가상번지 5",
     "2022-09-09", None, "mid",
     {"trdar_population": ("missing", "outside_trdar", 0.007), "trdar_vitality": ("missing", "outside_trdar", 0.004),
      "peer_competition": ("missing", "outside_trdar", -0.003), "peer_sales": ("missing", "outside_trdar", 0.002)},
     None, ["band_mid", "data_missing", "online_presence_not_collected"]),
    (6, "bundle", "휴게음식점", "마포구", "서교동", "(샘플) 검토대기카페", "서울특별시 마포구 가상로 606", None,
     "2021-11-11", None, "high", {"online_attention": ("hold", 0.063)}, "registered", ["band_high", "review_hold"]),
    (7, "bundle", "일반음식점", "광진구", "구의동", "(샘플) 기준일후폐업식당", "서울특별시 광진구 가상로 707", None,
     "2017-04-04", "2026-08-14", "mid", {}, "registered", ["band_mid", "closed_after_as_of"]),
    (8, "bundle", "미용업", "영등포구", "당산동", "(샘플) 인허가일미상미용실", "서울특별시 영등포구 가상로 808", None,
     None, None, "low", {}, "registered", ["band_low", "policy_check_required"]),
    (9, "bundle", "휴게음식점", "영등포구", "여의도동", "(샘플) 매출미공개카페", "서울특별시 영등포구 가상로 909", None,
     "2020-01-06", None, "high",
     {"peer_sales": ("missing", "no_sales_disclosed", 0.005), "online_attention": ("missing", "online_unobserved", 0.009),
      "tenure": 0.044}, "registered", ["band_high", "data_missing"]),
    (10, "bundle_no_policy", "일반음식점", "마포구", "망원동", "(샘플) 정책미실행식당", "서울특별시 마포구 가상로 1010", None,
     "2023-06-01", None, "low", {}, "registered", ["band_low", "policy_not_performed"]),
]
ALWAYS_CASES = ["cost_unavailable"]  # 모든 샘플: 비용 유형 판단 불가

POLICIES = [
    {"id": "sample_online_support", "name": "(예시) 온라인 판로 지원", "operator": "(예시) 운영기관",
     "link": "https://example.org/sample-online", "announce_year": 2026, "collected_at": "2026-09-20",
     "eligibility_text": "(예시) 업력 1년 이상 소상공인",
     "conditions": {"gu": None, "biz_type": ["일반음식점", "휴게음식점", "미용업"], "tenure_months_min": 12,
                    "tenure_months_max": None},
     "unverifiable_conditions": [], "related_factor_ids": ["online_attention"]},
    {"id": "sample_rent_support", "name": "(예시) 임차료 부담 완화", "operator": "(예시) 운영기관",
     "link": "https://example.org/sample-rent", "announce_year": 2026, "collected_at": "2026-09-20",
     "eligibility_text": "(예시) 연매출 3억 원 이하 소상공인",
     "conditions": {"gu": None, "biz_type": None, "tenure_months_min": None, "tenure_months_max": None},
     "unverifiable_conditions": ["연매출 3억 원 이하"], "related_factor_ids": ["rent_level"]},
    {"id": "sample_mapo_consulting", "name": "(예시) 마포구 경영 상담", "operator": "(예시) 운영기관",
     "link": "https://example.org/sample-mapo", "announce_year": 2026, "collected_at": "2026-09-20",
     "eligibility_text": "(예시) 마포구 소재 점포",
     "conditions": {"gu": ["마포구"], "biz_type": None, "tenure_months_min": None, "tenure_months_max": None},
     "unverifiable_conditions": [], "related_factor_ids": ["tenure"]},
]


def _factor(fid: str, spec) -> dict:
    name, cat, act = rv.FACTOR_CONTRACT[fid]
    kind, reason = "normal", None
    if isinstance(spec, tuple):
        if spec[0] == "missing":
            kind, reason, c = "missing", spec[1], spec[2]
        else:
            kind, c = "hold", spec[1]
    else:
        c = spec
    driver = None
    if fid == "online_attention" and kind != "missing":
        driver = ("최근 12개월 블로그 언급 40건" if kind == "hold" else
                  "마지막 블로그 언급 후 14개월" if c > 0 else "최근 12개월 블로그 언급 6건") + TAG
    if kind == "missing":
        expl = f"이 점포는 {name} 데이터가 없어({REASON_TEXT[reason]}) 이 요인은 진단하지 않습니다.{TAG}"
    elif abs(c) < 0.001:
        expl = f"'{name}' 요인은 이 점포의 예측 위험도에 거의 영향을 주지 않았습니다.{TAG}"
    else:
        way = "높이는" if c > 0 else "낮추는"
        expl = f"'{name}' 요인이 예측 위험도를 약 {abs(c) * 100:.1f}%p {way} 쪽으로 기여했습니다.{TAG}"
        if driver:
            expl = expl[:-len(TAG)] + f" 주된 근거: {driver[:-len(TAG)]}.{TAG}"
    return {"category": cat, "name": name, "factor_id": fid, "contribution": c, "direction": rv.direction_of(c),
            "peer_percentile": None if kind == "missing" else 55, "actionability": act, "explanation": expl,
            "driver": driver, "display": kind == "normal",
            "display_note": None if kind == "normal" else "합성 예시 — 표시 보류",
            "data_missing": kind == "missing", "missing_reason": reason,
            "hold_reason": "online_review" if kind == "hold" else None, "values": {}}


def _record(s) -> dict:
    n, _, biz, gu, _, _, _, _, _, _, band, over, _, _ = s
    p, lo, hi = BAND_P[band]
    specs = {**BASE_FACTORS, **over}
    factors = sorted((_factor(fid, spec) for fid, spec in specs.items()), key=lambda f: -f["contribution"])
    return {"_schema_version": "0.1.1", "store_id": f"SAMPLE-{n:03d}", "as_of": AS_OF, "score_origin": SCORE_ORIGIN,
            "store": {"biz_type": biz, "gu": gu},
            "risk": {"probability_12m": p, "ci_low": lo, "ci_high": hi, "interval_note": INTERVAL_NOTE, "band": band,
                     "percentile": {"low": 30, "mid": 72, "high": 94}[band], "peer_group": f"{gu} {biz}",
                     "peer_median": 0.118, "model": "sample_synthetic", "calibrated": False},
            "factors": factors, "unavailable_categories": ["비용"], "disclaimer": DISCLAIMER}


def _online(kind: str) -> dict:
    registered = kind == "registered"
    return {"basis": "current_snapshot", "collected_at": "2026-09-20", "naver_local_registered": registered,
            "kakao_registered": registered, "naver_blog_total_12m": 6 if registered else 0,
            "first_date_truncated": False, "note": "현재 시점 스냅샷 — 표시 전용, 예측·진단에 쓰지 않음" + TAG}


def write_inputs(d: Path, samples: list, with_policies: bool) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    lic = pd.DataFrame([{
        "store_id": f"SAMPLE-{s[0]:03d}", "business_type": s[2], "gu": s[3], "dong": s[4], "name_raw": s[5],
        "name_norm": normalize_name(s[5])[0], "road_addr_raw": s[6], "addr_raw": s[7], "license_date": s[8],
        "close_date": s[9], "status_name": "폐업" if s[9] else "영업/정상", "data_updated_raw": UPDATED}
        for s in samples])
    lic["license_date"] = pd.to_datetime(lic["license_date"])
    lic["close_date"] = pd.to_datetime(lic["close_date"])
    lic.to_parquet(d / "licenses.parquet", index=False)
    (d / "reports.jsonl").write_text("".join(json.dumps(_record(s), ensure_ascii=False) + "\n" for s in samples),
                                     encoding="utf-8", newline="\n")
    (d / "serve_meta.json").write_text(json.dumps({
        "score_origin": SCORE_ORIGIN, "as_of": AS_OF, "n_stores": len(samples), "detect_run": "sample_synthetic",
        "band_cutoffs": {"mid": 0.1493, "high": 0.2142}}), encoding="utf-8", newline="\n")
    online = [{"store_id": f"SAMPLE-{s[0]:03d}", "online_presence": _online(s[12])} for s in samples if s[12]]
    (d / "online.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in online),
                                    encoding="utf-8", newline="\n")
    kw = {"online_presence_path": d / "online.jsonl"}
    if with_policies:
        (d / "policies.json").write_text(json.dumps(POLICIES, ensure_ascii=False, indent=2), encoding="utf-8",
                                         newline="\n")
        kw["policies_path"] = d / "policies.json"
    return kw


def generate(out_root: Path = DEFAULT_OUT) -> dict:
    """합성 번들 2개와 sample_cases.json을 out_root 아래에 만든다. → {번들 이름: manifest}."""
    out_root = Path(out_root)
    manifests = {}
    with tempfile.TemporaryDirectory() as td:
        for bundle, with_policies in (("bundle", True), ("bundle_no_policy", False)):
            samples = [s for s in SAMPLES if s[1] == bundle]
            d = Path(td) / bundle
            kw = write_inputs(d, samples, with_policies)
            db = d / "sample.sqlite"
            bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", db,
                     license_snapshot_date=SNAPSHOT, purpose="release", **kw)
            manifests[bundle] = ex.export(db, out_root / bundle, min_cell_n=SAMPLE_MIN_CELL_N,
                                          min_cell_n_status="provisional", allow_tracked_synthetic=True, indent=2)
    cases = {"_note": ex.SYNTHETIC_NOTE, "min_cell_n_note": f"동 요약은 시연용 provisional 하한 {SAMPLE_MIN_CELL_N}로 만들었다 "
                                                          "— 실제 하한값·실제 통계가 아니다",
             "samples": [{"store_id": f"SAMPLE-{s[0]:03d}", "bundle": s[1], "name": s[5],
                          "cases": [*s[13], *ALWAYS_CASES]} for s in SAMPLES],
             "search_checks": [
                 {"kind": "name", "query": "가상식당", "expect": ["SAMPLE-001", "SAMPLE-002"]},
                 {"kind": "address", "query": "광진구 가상로 1", "expect_first": "SAMPLE-003"},
                 {"kind": "address", "query": "광진구 가상로 12", "expect_first": "SAMPLE-004"}]}
    (out_root / "sample_cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8", newline="\n")
    return manifests


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-5 합성 샘플 번들 생성")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    for name, m in generate(a.out).items():
        print(f"{name}: 점포 {m['n_stores']} · 파일 {len(m['files'])} · run_id {m['run_id']}")


if __name__ == "__main__":
    main()
