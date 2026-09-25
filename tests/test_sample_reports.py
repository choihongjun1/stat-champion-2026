# -*- coding: utf-8 -*-
"""W2-6 샘플 추출 테스트 (합성 reports.jsonl)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.models import sample_reports as sr

FIDS = ["tenure", "district", "trdar_population", "trdar_vitality", "online_attention",
        "peer_competition", "peer_sales", "store_profile"]


def _f(fid, c, display=True, missing=False, driver=None, expl=None):
    return {"category": "입지·수요", "name": fid, "factor_id": fid, "contribution": c,
            "direction": "위험 증가" if c > 0 else "위험 감소", "peer_percentile": 50,
            "actionability": "external", "explanation": expl or f"{fid} 설명", "driver": driver,
            "display": display, "data_missing": missing, "display_note": None, "values": {}}


def _rec(i, p, band, kind):
    rng = np.random.default_rng(i)
    fs = [_f(k, float(rng.normal(0, 0.01))) for k in FIDS]
    by = {f["factor_id"]: f for f in fs}
    if kind == "outside":
        for k in sr.TRDAR_FACTORS:
            by[k].update(display=False, data_missing=True)
    elif kind == "sales":
        by["peer_sales"].update(display=False, data_missing=True)
    elif kind == "hold":
        by["online_attention"].update(contribution=0.03, display=False, driver="최근 12개월 블로그 언급 101건")
    elif kind == "decline":
        by["online_attention"].update(contribution=0.02, driver="최근 6개월 블로그 언급이 그 전 6개월보다 5건 줄어듦")
    elif kind == "absent":
        by["online_attention"].update(contribution=0.02, driver="블로그 언급 이력 없음")
    elif kind == "online_na":
        by["online_attention"].update(display=False, data_missing=True)
    elif kind == "small":
        for f in fs:
            f["contribution"] = 0.0002
    elif kind == "peer":
        by["tenure"].update(contribution=0.04, peer_percentile=97, explanation="업력이 … 상위 3% 수준입니다.")
    return {"_schema_version": "0.1", "store_id": f"S{i:04d}", "as_of": "2025-06-30",
            "store": {"biz_type": "미용업", "gu": "마포구"},
            "risk": {"probability_12m": p, "ci_low": p - 0.01 * (1 + i % 7), "ci_high": p + 0.01,
                     "band": band, "percentile": 50, "peer_group": "마포구 미용업", "peer_median": 0.1,
                     "model": "detect_v0_enriched", "calibrated": False},
            "factors": sorted(fs, key=lambda f: -f["contribution"]),
            "unavailable_categories": ["비용"], "disclaimer": "..."}


def test_sample_covers_every_case(tmp_path):
    kinds = ["plain", "outside", "sales", "hold", "decline", "absent", "online_na", "small", "peer"]
    rng = np.random.default_rng(0)
    recs = []
    for i in range(400):
        p = float(rng.beta(2, 12))
        band = "high" if p >= 0.214 else "mid" if p >= 0.149 else "low"
        recs.append(_rec(i, p, band, kinds[i % len(kinds)]))
    d = tmp_path / "serve"
    d.mkdir()
    with open(d / "reports.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (d / "serve_meta.json").write_text(json.dumps(
        {"band_cutoffs": {"cut_mid": 0.149, "cut_high": 0.214, "base_rate": 0.1245}}), encoding="utf-8")

    idx = sr.run(d)
    out = d / "sample"
    cases = set(";".join(idx["cases"]).split(";"))
    assert cases == set(sr.CASE_DESC)  # 모든 경우가 최소 1곳
    assert 12 <= len(idx) <= 30 and idx["store_id"].is_unique
    lines = (out / "sample_reports.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(idx)
    assert all("_sample_cases" not in json.loads(l) for l in lines)  # 원본 형식 그대로
    pretty = json.loads((out / "sample_reports.json").read_text(encoding="utf-8"))
    assert all(r["_sample_cases"] for r in pretty)
    readme = (out / "README_W2-6.md").read_text(encoding="utf-8")
    assert "시험 결과" in readme and "21.4%" in readme and "빠진 경우" not in readme
    assert pd.read_csv(out / "sample_index.csv")["probability_12m"].is_monotonic_decreasing


def test_flags():
    assert sr.case_flags(_rec(1, 0.1, "low", "outside"))["missing_outside_trdar"]
    f = sr.case_flags(_rec(2, 0.1, "low", "sales"))
    assert f["missing_sales_only"] and not f["missing_outside_trdar"]
    f = sr.case_flags(_rec(3, 0.3, "high", "hold"))
    assert f["online_hold"] and not f["missing_online"] and not f["online_decline"]
    assert sr.case_flags(_rec(4, 0.3, "high", "decline"))["online_decline"]
    f = sr.case_flags(_rec(5, 0.1, "low", "online_na"))
    assert f["missing_online"] and not f["online_hold"]
    f = sr.case_flags(_rec(6, 0.2, "mid", "absent"))
    assert f["online_absent"] and not f["online_decline"]
    assert not sr.case_flags(_rec(4, 0.3, "high", "decline"))["online_absent"]


def test_no_standout_and_peer_top_thresholds():
    small = _rec(7, 0.05, "low", "small")
    assert sr.case_flags(small)["no_standout"]
    small["factors"][0]["contribution"] = 0.012  # 가장 큰 위험 기여 1.2%p → 눈에 띄는 요인 있음
    assert not sr.case_flags(small)["no_standout"]
    neg = _rec(8, 0.05, "low", "small")
    for f in neg["factors"]:
        f["contribution"] = -0.05  # 위험을 낮추는 기여만 크면 no_standout
    assert sr.case_flags(neg)["no_standout"]
    peer = _rec(9, 0.2, "mid", "peer")
    assert sr.case_flags(peer)["peer_top"]
    next(f for f in peer["factors"] if f["factor_id"] == "tenure")["peer_percentile"] = 90
    assert not sr.case_flags(peer)["peer_top"]  # 상위 5% 밖
    drift = _rec(10, 0.2, "mid", "decline")
    next(f for f in drift["factors"] if f["factor_id"] == "online_attention")["driver"] = "최근 1년 블로그 언급 수 변화 없음"
    assert not sr.case_flags(drift)["online_absent"]


def test_licenses_attach_name_and_keep_existing(tmp_path):
    recs = [_rec(i, 0.3 - i * 0.01, "high" if i < 5 else "low", "plain") for i in range(12)]
    recs[0]["store"]["name"] = "서빙에서 붙인 이름"
    d = tmp_path / "serve"
    d.mkdir()
    with open(d / "reports.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    lic = pd.DataFrame({"store_id": [r["store_id"] for r in recs], "name_raw": [f"가게{i}" for i in range(12)],
                        "road_addr_raw": "도로명", "addr_raw": "지번", "dong": "망원동"})
    lic.to_parquet(tmp_path / "lic.parquet", index=False)
    idx = sr.run(d, tmp_path / "out", licenses_path=tmp_path / "lic.parquet")
    by = dict(zip(idx["store_id"], idx["name"]))
    assert by["S0000"] == "서빙에서 붙인 이름"  # reports.jsonl에 이미 있으면 그대로
    assert all(v.startswith("가게") for k, v in by.items() if k != "S0000")
    out = [json.loads(l) for l in (tmp_path / "out" / "sample_reports.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all({"name", "road_address", "address", "dong"} <= set(r["store"]) for r in out if r["store_id"] != "S0000")
    assert next(r for r in out if r["store_id"] == "S0000")["store"].get("dong") is None  # 기존 레코드는 손대지 않음


def _write_serve(d, recs):
    d.mkdir()
    with open(d / "reports.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_mask_removes_every_identifier(tmp_path):
    """--mask: 출력 파일 어디에도 원래 store_id·상호·주소 문자열이 남지 않는다."""
    kinds = ["plain", "outside", "sales", "hold", "decline", "absent", "online_na", "small", "peer"]
    recs = [_rec(i, 0.02 + (i % 37) / 100, "low", kinds[i % len(kinds)]) for i in range(120)]
    for r in recs:
        r["store_id"] = f"GR_3130000-101-2020-{int(r['store_id'][1:]):05d}"
    d = tmp_path / "serve"
    _write_serve(d, recs)
    lic = pd.DataFrame({"store_id": [r["store_id"] for r in recs],
                        "name_raw": [f"실명가게{i:03d}" for i in range(len(recs))],
                        "road_addr_raw": [f"서울특별시 마포구 진짜도로 {i}길 7" for i in range(len(recs))],
                        "addr_raw": [f"서울특별시 마포구 진짜동 {i}-3" for i in range(len(recs))],
                        "dong": "진짜동"})
    lic.to_parquet(tmp_path / "lic.parquet", index=False)
    out = tmp_path / "out"
    idx = sr.run(d, out, licenses_path=tmp_path / "lic.parquet", mask=True)
    blob = "".join((out / f).read_text(encoding="utf-8-sig") for f in
                   ("sample_reports.jsonl", "sample_reports.json", "sample_index.csv", "README_W2-6.md"))
    for sid, name, road, addr in lic[["store_id", "name_raw", "road_addr_raw", "addr_raw"]].itertuples(index=False):
        assert sid not in blob and name not in blob and road not in blob and addr not in blob
    assert "진짜동" not in blob and "GR_" not in blob and "실명가게" not in blob
    assert idx["store_id"].tolist() == [f"SAMPLE-{k:03d}" for k in range(1, len(idx) + 1)]
    assert idx["name"].str.startswith("(샘플) 미용업 ").all()
    rec = json.loads((out / "sample_reports.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["store"]["dong"] is None and rec["store"]["road_address"] == "서울특별시 마포구 샘플로 1"
    assert "가린 값" in (out / "README_W2-6.md").read_text(encoding="utf-8")


def test_docs_output_requires_mask(tmp_path):
    from src.data import config

    d = tmp_path / "serve"
    _write_serve(d, [_rec(i, 0.1, "low", "plain") for i in range(5)])
    target = config.REPO_ROOT / "docs" / "samples" / "_pytest_guard"
    with pytest.raises(ValueError, match="--mask"):
        sr.run(d, target)
    assert not target.exists()


def test_one_line_without_risk_raising_factor():
    r = _rec(11, 0.05, "low", "plain")
    for f in r["factors"]:
        f["contribution"] = -abs(f["contribution"]) - 0.001
    assert sr.one_line(r).startswith("위험을 높이는 요인 없음")
    r["factors"][0]["contribution"] = 0.02
    assert sr.one_line(r).startswith("최대 위험 요인:")


def test_mask_coarsens_area_and_age(tmp_path):
    recs = [_rec(i, 0.05 + i / 200, "low", "plain") for i in range(20)]
    for i, r in enumerate(recs):
        for f in r["factors"]:
            if f["factor_id"] == "tenure":
                f["values"] = {"age_months": 13 + i * 7}
            elif f["factor_id"] == "store_profile":
                f["values"] = {"biz_type": "미용업", "area": 23.4 + i * 3.3, "has_coord": True}
    d = tmp_path / "serve"
    _write_serve(d, recs)
    out = tmp_path / "out"
    sr.run(d, out, mask=True)
    got = [json.loads(l) for l in (out / "sample_reports.jsonl").read_text(encoding="utf-8").splitlines()]
    vals = [f["values"] for r in got for f in r["factors"] if f["factor_id"] in ("tenure", "store_profile")]
    ages = [v["age_months"] for v in vals if "age_months" in v]
    areas = [v["area"] for v in vals if "area" in v]
    assert ages and all(a % 12 == 0 for a in ages)
    assert areas and all(a % 10 == 0 for a in areas)
    assert sr._coarsen_values({"age_months": 41, "area": 46.0}) == {"age_months": 36, "area": 50.0}
    # 가리지 않으면 원래 값 그대로
    sr.run(d, tmp_path / "named")
    raw = [json.loads(l) for l in (tmp_path / "named" / "sample_reports.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(f["values"].get("age_months", 0) % 12 for r in raw for f in r["factors"])
