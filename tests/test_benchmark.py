# -*- coding: utf-8 -*-
"""W2-2 기준 모형 비교 테스트 (합성 패널)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from src.models import benchmark as bm
from src.models import features
from tests.test_models import ORIGINS, synthetic_master


@pytest.fixture(scope="module")
def panel():
    return synthetic_master(n_stores=200)


def test_age_band_boundaries():
    b = bm.age_band([0, 12, 13, 36, 37, 60, 61, 120, 121, 400]).tolist()
    assert b == ["1년 미만", "1년 미만", "1–3년", "1–3년", "3–5년", "3–5년", "5–10년", "5–10년", "10년 이상", "10년 이상"]


def test_rcs_basis_is_linear_beyond_outer_knots():
    knots = [10, 40, 80, 150]
    x = np.array([200.0, 250.0, 300.0])  # 마지막 knot 바깥
    B = bm.rcs_basis(x, knots)
    assert B.shape == (3, 3)  # x + 비선형 2개 = 자유도 3
    assert np.allclose(np.diff(B, 2, axis=0), 0, atol=1e-9)  # 2차 차분 0 = 선형
    assert np.allclose(bm.rcs_basis(np.array([0.0, 5.0]), knots)[:, 1:], 0)  # 첫 knot 앞은 x만


def test_tenure_rate_table_and_fallback():
    X = pd.DataFrame({"age_months": [5, 5, 5, 30, 30], "biz_type": ["A", "A", "A", "A", "B"]})
    m = bm.TenureRate().fit(X, [1, 0, 0, 1, 0])
    p = m.predict_proba(pd.DataFrame({"age_months": [5, 30, 200, 200], "biz_type": ["A", "A", "B", "C"]}))
    assert math.isclose(p[0], 1 / 3) and math.isclose(p[1], 1.0)
    assert math.isclose(p[2], 0.0)   # (10년 이상, B) 칸 없음 → 업종 B 폐업률
    assert math.isclose(p[3], 0.4)   # 업종도 없음 → 전체 폐업률


def test_logit_imputes_from_training_only_without_indicators():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"age_months": rng.integers(0, 200, 400).astype(float), "area": rng.normal(50, 10, 400),
                      "biz_type": pd.Categorical(rng.choice(["A", "B"], 400)),
                      "gu": pd.Categorical(rng.choice(["x", "y", "z"], 400)), "has_coord": 1.0})
    X.loc[:49, "area"] = np.nan
    y = (rng.random(400) < 0.3).astype(int)
    m = bm.LogitModel(["age_months", "area", "biz_type", "gu", "has_coord"], spline_age=True).fit(X, y)
    assert m.median_["area"] == pytest.approx(X["area"].median())
    # 설계 행렬 열: 업력 스플라인 3 + area 1 + has_coord 1 + 원-핫(업종 1 + 자치구 2) = 8, 결측 지시자 없음
    assert m.model_.coef_.shape[1] == 8
    Xt = X.head(5).copy()
    Xt["area"] = np.nan  # 예측 쪽 결측은 학습 중앙값으로 — 예측 데이터의 통계를 쓰지 않는다
    D = m._design(Xt)
    assert np.allclose(D[:, 3], X["area"].median())


def test_logit_drops_all_na_training_column(panel):
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    tr = panel["origin"] < "2022Q1"  # land_price 전부 NA 구간
    m = bm.LogitModel(cols).fit(X[tr], panel.loc[tr, "event_12m"])
    assert "land_price" in m.dropped_all_na_
    assert np.isfinite(m.predict_proba(X[~tr])).all()


def test_calibration_slope_intercept_of_true_probabilities():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.02, 0.5, 200_000)
    y = (rng.random(len(p)) < p).astype(int)
    s, a = bm.calibration_slope_intercept(y, p)
    assert s == pytest.approx(1, abs=0.03) and a == pytest.approx(0, abs=0.02)
    s2, a2 = bm.calibration_slope_intercept(y, np.clip(p * 0.5, 1e-4, 1))  # 전부 과소 예측
    assert a2 > 0.3


def test_bootstrap_auc_diff_detects_real_gap():
    rng = np.random.default_rng(2)
    y = (rng.random(3000) < 0.2).astype(int)
    good = y + rng.normal(0, 0.8, 3000)
    bad = y + rng.normal(0, 3.0, 3000)
    r = bm.bootstrap_auc_diff(y, good, bad, n_boot=200)
    assert r["significant"] and r["ci_low"] > 0 and r["ci_low"] < r["auc_diff"] < r["ci_high"]
    assert r["ap_significant"] and r["ap_ci_low"] < r["ap_diff"] < r["ap_ci_high"]  # AP 차이도 같은 재표본으로
    same = bm.bootstrap_auc_diff(y, good, good, n_boot=50)
    assert same["auc_diff"] == 0 and same["ap_diff"] == 0 and not same["significant"] and not same["ap_significant"]


def test_logit_log1p_count_columns():
    rng = np.random.default_rng(4)
    X = pd.DataFrame({"cnt": rng.poisson(3, 300).astype(float), "trend": rng.normal(0, 2, 300)})
    X.loc[:29, "cnt"] = np.nan
    m = bm.LogitModel(["cnt", "trend"], log1p_cols=["cnt"]).fit(X, (rng.random(300) < 0.3).astype(int))
    assert m.median_["cnt"] == pytest.approx(np.log1p(X["cnt"]).median())
    assert m.model_.coef_.shape[1] == 2  # 결측 지시자 없음
    assert np.allclose(m._design(X.head(3))[:, 0], m.median_["cnt"])  # 앞 30행은 결측 → log1p 척도의 학습 중앙값
    with pytest.raises(ValueError):
        bm.LogitModel(["trend"], log1p_cols=["trend"]).fit(X, (rng.random(300) < 0.3).astype(int))


def test_recal_split_uses_only_embargoed_training_origins():
    for i in range(bm.MIN_TRAIN_ORIGINS + bm.EMBARGO, len(ORIGINS)):
        s, tr = bm.recal_split(ORIGINS, i)
        train = ORIGINS[: i - bm.EMBARGO]
        assert s == train[-1] and ORIGINS[i] not in tr
        if tr:
            assert ORIGINS.index(s) - ORIGINS.index(tr[-1]) == bm.EMBARGO + 1
    assert bm.recal_split(ORIGINS, 8)[1] == [] and bm.recal_split(ORIGINS, 9)[1] == []  # 2023Q1·Q2: 표본 없음
    assert bm.recal_split(ORIGINS, 10)[1] == ["2021Q1"]


def test_logistic_recal_recovers_slope():
    rng = np.random.default_rng(5)
    true = rng.uniform(0.03, 0.4, 100_000)
    y = (rng.random(len(true)) < true).astype(int)
    overconf = bm.expit(2.0 * bm.logit(true) + 1.0)  # 기울기 0.5짜리 과신 예측
    a, b = bm.fit_logistic_recal(overconf, y)
    assert b == pytest.approx(0.5, abs=0.03) and a == pytest.approx(-0.5, abs=0.05)
    fixed = bm.apply_logistic_recal(overconf, a, b)
    assert bm.calibration_slope_intercept(y, fixed)[0] == pytest.approx(1, abs=0.03)
    assert roc_auc_score(y, fixed) == pytest.approx(roc_auc_score(y, overconf))  # 단조 변환 — 순위 불변


def test_inner_split_never_uses_test_or_later_origins():
    for i in range(bm.MIN_TRAIN_ORIGINS + bm.EMBARGO, len(ORIGINS)):
        train = ORIGINS[: i - bm.EMBARGO]
        in_tr, val, gap = bm.inner_split(train)
        assert val == train[-1] and ORIGINS[i] not in in_tr and max(in_tr) < val
        if gap:  # 내부 분할도 embargo 4분기
            assert ORIGINS.index(val) - ORIGINS.index(in_tr[-1]) == bm.EMBARGO + 1
        else:
            assert len(train) < bm.EMBARGO + 2


def test_base_rate_table_uses_embargoed_training_window(panel):
    y = panel["event_12m"].to_numpy()
    oof = pd.DataFrame({"origin": ["2023Q1"] * 3, "p_oof": [0.1, 0.2, 0.3]})
    t = bm.base_rate_table(panel, y, {"m": oof})
    assert t.at[0, "train_origins"] == "2021Q1~2021Q4"
    assert t.at[0, "train_base_rate"] == pytest.approx(y[panel["origin"] <= "2021Q4"].mean())
    assert t.at[0, "pred_mean_m"] == pytest.approx(0.2)


def test_end_to_end(tmp_path, panel):
    master = tmp_path / "master.parquet"
    panel.to_parquet(master, index=False)
    online = panel[["store_id", "origin"]].copy()
    rng = np.random.default_rng(3)
    for c in bm.ONLINE_COLS:
        online[c] = rng.poisson(1, len(online)).astype(float)
    online.to_parquet(tmp_path / "online.parquet", index=False)
    cuts = tmp_path / "band_cutoffs.csv"
    pd.DataFrame([{"cut_mid": 0.12, "cut_high": 0.2, "base_rate": 0.1}]).to_csv(cuts, index=False)
    r = bm.run(master, tmp_path / "online.parquet", tmp_path / "out", n_boot=20, do_hpo=True, band_cutoffs=cuts)
    s = r["summary"].set_index("model")
    assert set(s.index) == {"tenure_only", "logit_license", "logit_base", "logit_enriched", "hgb_base", "hgb_enriched"}
    assert (s["n_origins"] == 10).all() and s["origins"].eq("2023Q1~2025Q2").all()
    assert {("hgb_enriched", "logit_enriched"), ("hgb_base", "logit_base")} <= set(
        zip(r["diff"]["model_a"], r["diff"]["model_b"]))
    assert {"ap_diff", "ap_ci_low", "ap_ci_high", "ap_significant"} <= set(r["diff"].columns)
    c = r["calib"].set_index("model")
    assert list(c.index) == list(bm.CALIB_CANDIDATES)
    assert c.at["hgb_enriched", "auc_mean"] == pytest.approx(s.at["hgb_enriched", "auc_mean"])
    assert c.at["b_default_recal", "auc_mean_recal_origins"] == pytest.approx(  # 재보정은 순위를 바꾸지 않는다
        c.at["hgb_enriched", "auc_mean_recal_origins"])
    share = c[[f"share_{k}_2025Q2" for k in ("low", "mid", "high")]].sum(axis=1)
    assert np.allclose(share, 1)
    recal = pd.read_csv(tmp_path / "out" / "calibration_recal_params.csv")
    assert (~recal.loc[recal["origin"].isin(["2023Q1", "2023Q2"]), "applied"]).all()
    later = recal[~recal["origin"].isin(["2023Q1", "2023Q2"])]
    assert (later["applied"] | (later["not_applied_reason"] == "재보정 기울기 ≤ 0")).all()
    assert (recal.loc[recal["applied"], "recal_slope"] > 0).all()
    assert (recal["recal_origin"] < recal["origin"]).all()
    assert len(r["hpo"]) == 10 and r["hpo"]["origin"].tolist() == ORIGINS[8:]
    inner = pd.read_csv(tmp_path / "out" / "hpo_inner_selection.csv")
    assert len(inner) == 10 * 9 and inner.groupby("test_origin")["chosen"].sum().eq(1).all()
    assert (inner["inner_val"] < inner["test_origin"]).all()
    for f in ("benchmark_summary.csv", "oof_metrics_by_origin.csv", "auc_diff_bootstrap_2025Q2.csv",
              "base_rate_vs_pred.csv", "hpo_comparison.csv", "run_meta.json", "calibration_candidates_summary.csv",
              "calibration_candidates_by_origin.csv", "calibration_recal_params.csv"):
        assert (tmp_path / "out" / f).exists()
    # 산출물에 점포 식별 열이 없다
    for f in (tmp_path / "out").glob("*.csv"):
        assert "store_id" not in pd.read_csv(f, nrows=0).columns
