# -*- coding: utf-8 -*-
"""W2-5 serving 테스트용 합성 입력 (점포·상호·주소는 모두 지어낸 값).

점포 목록(dict)으로 인허가 parquet·serve reports.jsonl·serve_meta.json을 만들고 build_db로 정본을 만든다.
"""
import json

import pandas as pd

from src.data.names import normalize_name
from src.serving import build_db as bd
from src.serving import report_validation as rv

AS_OF = "2026-06-30"
PROB = {"low": 0.05, "mid": 0.16, "high": 0.3}
INTERVAL_NOTE = "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지의 범위이며, 폐업 확률 자체의 범위가 아닙니다."
DISCLAIMER = "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."
_PREFIX = {"일반음식점": "GR", "휴게음식점": "SR", "미용업": "BT"}


def store(i, gu, dong, biz, band="low", *, name="__auto__", road="__auto__", jibun="__auto__",
          license_date="2020-01-01"):
    return {"store_id": f"{_PREFIX[biz]}_3000000-000-2020-{i:05d}", "gu": gu, "dong": dong, "biz": biz,
            "band": band, "name": f"가상점포{i}" if name == "__auto__" else name,
            "road": f"서울특별시 {gu} 가상로 {i}" if road == "__auto__" else road,
            "jibun": (f"서울특별시 {gu} {dong or '미상'} {i}" if jibun == "__auto__" else jibun),
            "license_date": license_date}


def _factor(fid, c, version):
    name, cat, act = rv.FACTOR_CONTRACT[fid]
    f = {"category": cat, "name": name, "factor_id": fid, "contribution": c,
         "direction": rv.direction_of(c) if version != "0.1" else ("위험 증가" if c > 0 else "위험 감소"),
         "peer_percentile": 50, "actionability": act,
         "explanation": f"{name} 요인이 예측 위험도를 약 {abs(c) * 100:.1f}%p 움직이는 쪽으로 기여했습니다.",
         "driver": None, "display": True, "display_note": None, "data_missing": False, "values": {}}
    if version != "0.1":
        f["missing_reason"], f["hold_reason"] = None, None
    return f


def write_inputs(d, stores, version="0.1.1", updated="2026-09-10 12:00:00", extra_licenses=()):
    d.mkdir(parents=True, exist_ok=True)
    lic = pd.DataFrame([{
        "store_id": s["store_id"], "business_type": s["biz"], "gu": s["gu"], "dong": s["dong"],
        "name_raw": s["name"], "name_norm": normalize_name(s["name"])[0], "road_addr_raw": s["road"],
        "addr_raw": s["jibun"], "license_date": s["license_date"], "close_date": None, "status_name": "영업/정상",
        "data_updated_raw": updated} for s in [*stores, *extra_licenses]])
    lic["license_date"] = pd.to_datetime(lic["license_date"])
    lic["close_date"] = pd.to_datetime(lic["close_date"])
    if updated is None:
        lic = lic.drop(columns="data_updated_raw")
    lic.to_parquet(d / "licenses.parquet", index=False)

    with open(d / "reports.jsonl", "w", encoding="utf-8") as f:
        for s in stores:
            p = PROB[s["band"]]
            rec = {"_schema_version": version, "store_id": s["store_id"], "as_of": AS_OF,
                   "store": {"biz_type": s["biz"], "gu": s["gu"]},
                   "risk": {"probability_12m": p, "ci_low": round(p - 0.02, 4), "ci_high": round(p + 0.02, 4),
                            "interval_note": INTERVAL_NOTE, "band": s["band"], "percentile": 50,
                            "peer_group": f"{s['gu']} {s['biz']}", "peer_median": 0.12,
                            "model": "detect_v0_enriched", "calibrated": False},
                   "factors": [_factor("tenure", 0.01, version), _factor("store_profile", -0.004, version)],
                   "unavailable_categories": ["비용"], "disclaimer": DISCLAIMER}
            if version != "0.1":
                rec["score_origin"] = "2026Q2"
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (d / "serve_meta.json").write_text(json.dumps(
        {"score_origin": "2026Q2", "as_of": AS_OF, "n_stores": len(stores), "detect_run": "detect_v0_enriched",
         "band_cutoffs": {"mid": 0.1493, "high": 0.2142}}), encoding="utf-8")
    return d


def make_db(tmp_path, stores, *, version="0.1.1", purpose="dev", snapshot="2026-09-11", name="report.sqlite",
            **kw):
    d = write_inputs(tmp_path / f"in_{name}", stores, version=version, **kw)
    out = tmp_path / name
    bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out,
             license_snapshot_date=snapshot, purpose=purpose)
    return out
