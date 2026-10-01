# -*- coding: utf-8 -*-
"""탐지 모형 집단별 점검 테스트 (합성 데이터)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import detect_subgroups as ds


def _synthetic(n_stores=1500, seed=0):
    rng = np.random.default_rng(seed)
    ids = [f"S{i:05d}" for i in range(n_stores)]
    rows, mrows, orows = [], [], []
    for origin in ("2025Q1", "2025Q2"):
        for i, sid in enumerate(ids):
            p = float(np.clip(rng.beta(2, 14) + (0.05 if i % 3 == 0 else 0), 0, 1))
            y = int(rng.random() < p)
            band = "high" if p >= 0.21 else "mid" if p >= 0.15 else "low"
            gu = ("마포구", "광진구", "영등포구")[i % 3]
            biz = ("일반음식점", "휴게음식점", "미용업")[(i // 3) % 3]
            rows.append({"store_id": sid, "origin": origin, "gu": gu, "biz_type": biz, "probability_12m": p,
                         "band": band, "event_12m": y, "model": "detect_v0"})  # 옛 실행처럼 model 열이 부정확
            outside = i % 5 == 0
            mrows.append({"store_id": sid, "origin": origin, "age_months": int(rng.integers(0, 200)),
                          "trdar_flow_pop": np.nan if outside else float(rng.random()),
                          "trdar_biz_store_cnt_observed": np.nan if outside else float(rng.integers(1, 9))})
            if i % 7:  # 7의 배수 점포는 온라인 테이블에 없음
                orows.append({"store_id": sid, "origin": origin,
                              "online_blog_cnt_12m": np.nan if i % 11 == 0 else float(rng.integers(0, 50))})
    return pd.DataFrame(rows), pd.DataFrame(mrows), pd.DataFrame(orows)


@pytest.mark.parametrize("months,band", [(0, "1년 미만"), (12, "1년 미만"), (13, "1–3년"), (36, "1–3년"),
                                         (37, "3–5년"), (60, "3–5년"), (61, "5–10년"), (120, "5–10년"),
                                         (121, "10년 이상"), (400, "10년 이상")])
def test_age_band_edges(months, band):
    assert ds.age_band(pd.Series([months])).iloc[0] == band


def test_build_panel_trdar_and_online_flags():
    risk, master, online = _synthetic(n_stores=40)
    p = ds.build_panel(risk, master, online).set_index(["store_id", "origin"])
    # 상권 feature 전부 결측 → 상권 밖
    assert (p.loc[("S00000", "2025Q1"), "trdar_in"]) == "상권 밖"
    assert (p.loc[("S00001", "2025Q1"), "trdar_in"]) == "상권 안"
    # 온라인 테이블에 없거나 cnt_12m 결측 → 미관측
    assert p.loc[("S00007", "2025Q1"), "online_obs"] == "온라인 미관측"
    assert p.loc[("S00011", "2025Q1"), "online_obs"] == "온라인 미관측"
    assert p.loc[("S00001", "2025Q1"), "online_obs"] == "온라인 관측"
    assert len(p) == len(risk)


def test_build_panel_requires_one_to_one():
    risk, master, online = _synthetic(n_stores=20)
    with pytest.raises(ValueError, match="중복"):
        ds.build_panel(risk, pd.concat([master, master.head(1)]), online)
    with pytest.raises(ValueError):
        ds.build_panel(risk, master.iloc[1:], online)  # master에 없는 행


def test_small_groups_are_masked():
    risk, master, online = _synthetic(n_stores=1500)
    panel = ds.build_panel(risk, master, online)
    tab = ds.subgroup_table(panel, n_boot=0)
    ov = tab[tab["dimension"] == "전체"].iloc[0]
    assert ov["note"] == "" and not np.isnan(ov["auc"])
    tiny = panel.head(60).copy()
    tiny["event_12m"] = 0
    tiny.loc[tiny.index[:5], "event_12m"] = 1  # 사건 5건 < 30
    t2 = ds.subgroup_table(tiny, n_boot=0)
    row = t2[t2["dimension"] == "전체"].iloc[0]
    assert row["note"] == "표본 부족" and np.isnan(row["auc"]) and np.isnan(row["ap"]) and row["n"] == 60


def test_bootstrap_seed_reproducible():
    risk, master, online = _synthetic(n_stores=800)
    panel = ds.build_panel(risk, master, online)
    a = ds.subgroup_table(panel, n_boot=25, seed=1)
    b = ds.subgroup_table(panel, n_boot=25, seed=1)
    c = ds.subgroup_table(panel, n_boot=25, seed=2)
    pd.testing.assert_frame_equal(a, b)
    ok = a["auc_lo"].notna()
    assert ok.any() and not np.allclose(a.loc[ok, "auc_lo"], c.loc[ok, "auc_lo"])
    assert (a.loc[ok, "auc_lo"] <= a.loc[ok, "auc"]).all() and (a.loc[ok, "auc"] <= a.loc[ok, "auc_hi"]).all()


def test_run_outputs_have_no_store_identifiers(tmp_path):
    risk, master, online = _synthetic(n_stores=900)
    rp, mp, op = tmp_path / "risk.parquet", tmp_path / "master.parquet", tmp_path / "online.parquet"
    risk.to_parquet(rp, index=False)
    master.to_parquet(mp, index=False)
    online.to_parquet(op, index=False)
    dd = tmp_path / "detect"
    dd.mkdir()
    (dd / "run_meta.json").write_text('{"primary_feature_set": "enriched"}', encoding="utf-8")
    pd.DataFrame({"origin": ["2025Q1", "2025Q2", "2025Q1"], "auc": [0.6, 0.62, 0.55], "ap": [0.17, 0.18, 0.15],
                  "base_rate": [0.12, 0.11, 0.12], "n": [900, 900, 900],
                  "feature_set": ["enriched", "enriched", "base"]}).to_csv(dd / "oof_metrics_by_origin.csv", index=False)
    pd.DataFrame({"origin": ["2025Q1", "2025Q2"], "low": [0.8, 0.78], "mid": [0.14, 0.15],
                  "high": [0.06, 0.07]}).to_csv(dd / "band_share_by_origin.csv", index=False)
    out = tmp_path / "out"
    ds.run(rp, mp, op, dd, out, n_boot=10)
    for f in ("subgroup_metrics.csv", "time_stability.csv", "fig_subgroup_auc.png", "fig_subgroup_calibration.png",
              "SUMMARY.md"):
        assert (out / f).exists(), f
    for f in ("subgroup_metrics.csv", "time_stability.csv"):
        cols = pd.read_csv(out / f).columns
        assert "store_id" not in cols and not any("name" in c for c in cols)
    ts = pd.read_csv(out / "time_stability.csv")
    assert len(ts) == 2 and np.isclose(ts.loc[ts["origin"] == "2025Q1", "auc"].iloc[0], 0.6)  # enriched만
    text = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "## 요점" in text and "S0000" not in text
    tab = pd.read_csv(out / "subgroup_metrics.csv")
    assert set(tab["dimension"]) == {"전체", "자치구", "업종", "업력대", "상권", "온라인", "자치구×업종"}


def _detect_dir(tmp_path, with_meta=True):
    dd = tmp_path / "detect"
    dd.mkdir()
    if with_meta:
        (dd / "run_meta.json").write_text('{"primary_feature_set": "enriched", "calibration_applied": false}',
                                          encoding="utf-8")
    pd.DataFrame({"origin": ["2025Q1", "2025Q2"], "auc": [0.6, 0.62], "ap": [0.17, 0.18], "base_rate": [0.12, 0.11],
                  "n": [900, 900], "feature_set": "enriched"}).to_csv(dd / "oof_metrics_by_origin.csv", index=False)
    pd.DataFrame({"origin": ["2025Q1", "2025Q2"], "low": [0.8, 0.78], "mid": [0.14, 0.15],
                  "high": [0.06, 0.07]}).to_csv(dd / "band_share_by_origin.csv", index=False)
    return dd


def _write_inputs(tmp_path, n_stores=600):
    risk, master, online = _synthetic(n_stores=n_stores)
    paths = tmp_path / "risk.parquet", tmp_path / "master.parquet", tmp_path / "online.parquet"
    for df, pth in zip((risk, master, online), paths):
        df.to_parquet(pth, index=False)
    return paths


def test_model_name_from_run_meta_and_oof_calibration(tmp_path):
    rp, mp, op = _write_inputs(tmp_path)
    dd = _detect_dir(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame({"origin": ["2025Q1", "2025Q2"], "n": [900, 900], "obs_rate": [0.12, 0.11],
                  "pred_mean": [0.10, 0.108], "calib_gap": [-0.02, -0.002], "share_high": [0.06, 0.07],
                  "obs_rate_high": [0.24, 0.25]}).to_csv(out / "oof_calibration_by_origin.csv", index=False)
    ds.run(rp, mp, op, dd, out, n_boot=0)
    text = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "모형 detect_v0_enriched" in text  # risk_scores의 model 열(detect_v0)이 아니라 run_meta 기준
    assert "| 예측 평균 | 예측−실측 |" in text and "10.0%→10.8%" in text and "-2.0%p→-0.2%p" in text
    ts = pd.read_csv(out / "time_stability.csv")
    assert {"pred_mean", "calib_gap", "obs_rate_high"} <= set(ts.columns)


def test_model_name_falls_back_to_risk_column(tmp_path, capsys):
    rp, mp, op = _write_inputs(tmp_path)
    dd = _detect_dir(tmp_path, with_meta=False)
    ds.run(rp, mp, op, dd, tmp_path / "out", n_boot=0)
    assert "run_meta.json" in capsys.readouterr().out
    text = (tmp_path / "out" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "모형 detect_v0" in text and "detect_v0_enriched" not in text
    assert "| 예측 평균 |" not in text  # 예측 평균 파일이 없으면 열도 없다
