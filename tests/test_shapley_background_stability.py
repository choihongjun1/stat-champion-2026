# -*- coding: utf-8 -*-
"""#34 리뷰 — Shapley 배경 표본 안정성 연구 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import shapley_background_stability as sbs
from src.models import features, splits, train_detect
from tests.test_models import _online_table, synthetic_master


def test_sample_random_is_distinct_and_sized():
    pool = np.arange(100)
    out = sbs.sample_random(pool, 20, seed=1)
    assert len(out) == 20 and len(set(out)) == 20
    assert set(out) <= set(pool)


def test_sample_stratified_covers_every_stratum_present_in_quota():
    pool = np.arange(100)
    strata = pd.Series(["A"] * 90 + ["B"] * 10)
    out = sbs.sample_stratified(pool, strata, 20, seed=1)
    assert len(out) == 20 and len(set(out)) == 20
    picked_strata = strata.loc[out]
    assert "A" in set(picked_strata) and "B" in set(picked_strata)  # 작은 층도 최소 1개 보장


def test_sample_kmeans_returns_distinct_valid_rows():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=200), "b": rng.normal(size=200)})
    pool = np.arange(200)
    out = sbs.sample_kmeans(X, pool, 10, seed=0)
    assert len(out) == 10 and len(set(out)) == 10
    assert set(out) <= set(pool)


def test_top1_ids_picks_argmax_column():
    phi = np.array([[0.1, 0.9, -0.2], [0.5, 0.1, 0.05]])
    ids = sbs._top1_ids(phi, ["x", "y", "z"])
    assert list(ids) == ["y", "x"]


def test_recommend_prefers_fastest_config_meeting_threshold():
    table = pd.DataFrame([
        {"method": "random", "n_background": 16, "seed": 1, "base_value": 0.1,
         "top1_agreement_vs_256": 0.80, "online_sign_agreement_vs_256": 0.80, "seconds_per_1000_stores": 1.0},
        {"method": "random", "n_background": 64, "seed": 1, "base_value": 0.1,
         "top1_agreement_vs_256": 0.95, "online_sign_agreement_vs_256": 0.93, "seconds_per_1000_stores": 4.0},
        {"method": "random", "n_background": 128, "seed": 1, "base_value": 0.1,
         "top1_agreement_vs_256": 0.97, "online_sign_agreement_vs_256": 0.96, "seconds_per_1000_stores": 8.0},
    ])
    rec = sbs.recommend(table, min_agree=0.90)
    assert rec["met_threshold"]
    assert rec["chosen"]["n_background"] == 64  # 임계 충족 중 가장 빠른 것


def test_recommend_falls_back_to_best_agreement_when_none_meet_threshold():
    table = pd.DataFrame([
        {"method": "random", "n_background": 16, "seed": 1, "base_value": 0.1,
         "top1_agreement_vs_256": 0.5, "online_sign_agreement_vs_256": 0.5, "seconds_per_1000_stores": 1.0},
        {"method": "random", "n_background": 64, "seed": 1, "base_value": 0.1,
         "top1_agreement_vs_256": 0.7, "online_sign_agreement_vs_256": 0.6, "seconds_per_1000_stores": 4.0},
    ])
    rec = sbs.recommend(table, min_agree=0.90)
    assert not rec["met_threshold"]
    assert rec["chosen"]["n_background"] == 64


def test_save_background_index_hash_changes_with_content(tmp_path):
    df = pd.DataFrame({"store_id": ["A", "B", "C"], "origin": ["2024Q1"] * 3})
    h1 = sbs.save_background_index(df, np.array([0, 1]), tmp_path / "bg1.csv")
    h2 = sbs.save_background_index(df, np.array([0, 2]), tmp_path / "bg2.csv")
    h3 = sbs.save_background_index(df, np.array([0, 1]), tmp_path / "bg3.csv")
    assert h1 != h2
    assert h1 == h3


def test_study_end_to_end_synthetic(monkeypatch):
    """작은 합성 데이터로 study()가 끝까지 돌고 표·배경 인덱스가 정합적인지만 확인한다."""
    monkeypatch.setattr(sbs, "BG_SIZES", (4, 8))
    monkeypatch.setattr(sbs, "SEEDS", (1, 2))

    panel = synthetic_master(n_stores=80)
    online_cols = ["store_id", "origin"] + list(features.ONLINE_PREDICTORS) + ["online_feature_asof"]
    panel = panel.merge(_online_table(panel)[online_cols], on=["store_id", "origin"], how="left", validate="1:1")
    cols = features.select_features(panel.columns, "enriched")
    X = features.build_X(panel, cols)
    y = panel["event_12m"].to_numpy()
    origins = splits.sorted_origins(panel)

    res = sbs.study(panel, X, y, origins, primary="enriched", max_stores=15, seed=0)
    tab = res["table"]
    assert set(tab["method"]) == {"random", "stratified", "kmeans"}
    assert (tab["top1_agreement_vs_256"] <= 1.0).all() and (tab["top1_agreement_vs_256"] >= 0.0).all()
    assert tab["online_sign_agreement_vs_256"].notna().all()
    assert (tab.loc[tab["method"] != "kmeans"].groupby(["method", "n_background"]).size() == len(sbs.SEEDS)).all()

    rec = sbs.recommend(tab)
    key = (rec["chosen"]["method"], int(rec["chosen"]["n_background"]), sbs.SEEDS[0])
    assert key in res["backgrounds"]
