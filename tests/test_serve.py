# -*- coding: utf-8 -*-
"""서빙 테스트 — 검증 구간 마지막 origin을 라벨 없이 넣으면 train_detect 결과를 그대로 재현해야 한다."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.models import diagnose, serve, train_detect
from tests.test_models import _online_table, synthetic_master


@pytest.fixture(scope="module")
def detect_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("serve")
    panel = synthetic_master(n_stores=200)
    mp, op = d / "master.parquet", d / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    ddir = d / "detect_v0_enriched"
    train_detect.run(mp, ddir, ["enriched"], n_boot=0, with_split_comparison=False,
                     online_path=op, primary="enriched")
    last = sorted(panel["origin"].unique())[-1]
    score = panel[panel["origin"] == last].drop(columns="event_12m")
    sp, osp = d / "score.parquet", d / "online_score.parquet"
    score.to_parquet(sp, index=False)
    pd.read_parquet(op).query("origin == @last").to_parquet(osp, index=False)
    return dict(d=d, mp=mp, op=op, sp=sp, osp=osp, ddir=ddir, last=last)


def _serve(r, out, **kw):
    return serve.run(r["mp"], r["sp"], r["ddir"], out, primary="enriched", online_path=r["op"],
                     online_score_path=r["osp"], n_boot=kw.pop("n_boot", 0), n_background=4, **kw)


def test_reproduces_detect_risk_scores(detect_run, tmp_path):
    risk = _serve(detect_run, tmp_path / "out")
    ref = pd.read_parquet(detect_run["ddir"] / "risk_scores.parquet").query("origin == @detect_run['last']")
    m = risk.merge(ref, on="store_id", suffixes=("", "_ref"))
    assert len(m) == len(ref) == len(risk)
    assert np.abs(m["probability_12m"] - m["probability_12m_ref"]).max() < 1e-12
    assert (m["band"] == m["band_ref"]).all()
    assert (m["percentile"] == m["percentile_ref"]).all()
    assert (risk["model"] == "detect_v0_enriched").all()


def test_training_window_embargo():
    lab = pd.DataFrame({"origin": ["2023Q4", "2024Q1", "2024Q2", "2025Q1"]})
    assert serve.training_mask(lab, "2025Q2").tolist() == [True, True, False, False]  # ≤ 2024Q1 (s−5)


def test_reports_jsonl_schema(detect_run, tmp_path):
    out = tmp_path / "out"
    _serve(detect_run, out, n_boot=2)
    recs = [json.loads(l) for l in (out / "reports.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == pd.read_parquet(detect_run["sp"]).shape[0]
    r = recs[0]
    assert set(r) == {"_schema_version", "store_id", "as_of", "store", "risk", "factors",
                      "unavailable_categories", "disclaimer"}
    k = r["risk"]
    assert k["ci_low"] <= k["probability_12m"] <= k["ci_high"]
    meta_d = json.loads((detect_run["ddir"] / "run_meta.json").read_text(encoding="utf-8"))
    assert k["band"] in {"low", "mid", "high"} and k["calibrated"] is bool(meta_d["calibration_applied"])
    assert r["as_of"] == str(pd.Period(detect_run["last"], freq="Q").end_time.date())
    assert r["unavailable_categories"] == ["비용"]
    assert {f["category"] for f in r["factors"]} <= set(diagnose.CATEGORIES)
    meta = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert meta["score_origin"] == detect_run["last"] and meta["n_boot"] == 2
    cat = pd.read_parquet(out / "diagnosis_by_category.parquet")
    total = cat[list(diagnose.CATEGORIES)].sum(axis=1) + cat["base_value"]
    assert np.allclose(total, cat["probability_12m"])  # 보정 전 척도에서 정확히 합산


def test_rejects_multi_origin_and_mismatched_feature_set(detect_run, tmp_path):
    two = pd.read_parquet(detect_run["mp"])
    o1, o2 = sorted(two["origin"].unique())[-2:]
    ids = sorted(two["store_id"].unique())
    two = pd.concat([two[(two["origin"] == o1) & two["store_id"].isin(ids[:50])],
                     two[(two["origin"] == o2) & two["store_id"].isin(ids[50:100])]])
    p = tmp_path / "two.parquet"
    two.to_parquet(p, index=False)
    with pytest.raises(ValueError, match="origin이 하나"):
        serve.load_score_panel(p)
    with pytest.raises(ValueError, match="feature set"):
        serve.run(detect_run["mp"], detect_run["sp"], detect_run["ddir"], tmp_path / "x", primary="base")


def test_calibrated_run_needs_calibrator(tmp_path):
    (tmp_path / "run_meta.json").write_text(json.dumps({"calibration_applied": True}), encoding="utf-8")
    pd.DataFrame([{"cut_mid": 0.1, "cut_high": 0.2}]).to_csv(tmp_path / "band_cutoffs.csv", index=False)
    with pytest.raises(FileNotFoundError):
        serve.read_detect_run(tmp_path)


def test_future_origin_excludes_unvalidated_land_price(detect_run, tmp_path):
    """score origin이 라벨 구간 뒤(현재 시점)여도 검증에서 못 쓴 land_price는 학습에 넣지 않는다."""
    lab = pd.read_parquet(detect_run["mp"])
    nxt = pd.Period(detect_run["last"], freq="Q") + 4
    sc = lab[lab["origin"] == detect_run["last"]].drop(columns="event_12m").copy()
    sc["origin"] = str(nxt)
    sc["origin_end"] = nxt.end_time.normalize()
    sp = tmp_path / "score_future.parquet"
    sc.to_parquet(sp, index=False)
    on = pd.read_parquet(detect_run["osp"]).assign(origin=str(nxt), origin_end=nxt.end_time.normalize())
    osp = tmp_path / "online_future.parquet"
    on.to_parquet(osp, index=False)
    tr = serve.training_mask(lab, str(nxt))
    assert lab.loc[tr, "land_price"].notna().any()  # 넓어진 학습 구간에는 값이 있다
    out = tmp_path / "out"
    serve.run(detect_run["mp"], sp, detect_run["ddir"], out, primary="enriched", online_path=detect_run["op"],
              online_score_path=osp, n_boot=0, n_background=4)
    meta = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert "land_price" in meta["excluded_unvalidated"] and "land_price" not in meta["features_used"]
    assert meta["as_of"] == str(nxt.end_time.date())


def test_store_block_gets_name_and_address_from_licenses(detect_run, tmp_path):
    """--licenses: store_id로 1:1 조인해 store 블록에 이름·주소를 붙이고, 없는 점포는 None."""
    ids = pd.read_parquet(detect_run["sp"], columns=["store_id"])["store_id"].tolist()
    known, unknown = ids[:-3], ids[-3:]
    lic = pd.DataFrame({"store_id": known, "name_raw": [f"가게{i}" for i in range(len(known))],
                        "road_addr_raw": "서울특별시 마포구 월드컵로 1", "addr_raw": "서울특별시 마포구 망원동 1",
                        "dong": "망원동"})
    lp = tmp_path / "licenses.parquet"
    lic.to_parquet(lp, index=False)
    out = tmp_path / "out"
    _serve(detect_run, out, licenses_path=lp)
    recs = {r["store_id"]: r for r in map(json.loads, (out / "reports.jsonl").read_text(encoding="utf-8").splitlines())}
    s = recs[known[0]]["store"]
    assert s == {"biz_type": s["biz_type"], "gu": s["gu"], "name": "가게0", "road_address": "서울특별시 마포구 월드컵로 1",
                 "address": "서울특별시 마포구 망원동 1", "dong": "망원동"}
    for sid in unknown:
        assert {k: recs[sid]["store"][k] for k in serve.STORE_META_COLS} == dict.fromkeys(serve.STORE_META_COLS)
    meta = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert meta["n_stores_without_name"] == 3 and meta["licenses_sha256"] == train_detect.sha256(lp)

    pd.concat([lic, lic.head(1)]).to_parquet(tmp_path / "dup.parquet", index=False)
    with pytest.raises(ValueError, match="중복"):
        serve.store_meta(ids, tmp_path / "dup.parquet")
