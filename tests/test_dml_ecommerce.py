"""src/prescribe/dml_ecommerce.py 단위 테스트 — 합성 데이터만 사용한다."""

import numpy as np
import pandas as pd
import pytest

from src.prescribe import dml_ecommerce as de


def _synthetic(n=1500, ate=0.5, seed=0, seoul_share=0.3):
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    ps = 1 / (1 + np.exp(-0.8 * z))
    d = rng.binomial(1, ps)
    y = ate * d + 0.7 * z + rng.normal(scale=0.5, size=n)
    df = pd.DataFrame(
        {
            de.OUTCOME: y,
            de.TREATMENT: d,
            "산업중분류코드": rng.choice(["47", "56", "96"], n),
            "행정구역시도코드": rng.choice(["11", "26", "41"], n),
            "일반_창업형태코드": rng.choice(["1", "2"], n),
            de.CONTINUOUS_COL: np.where(rng.random(n) < 0.02, np.nan, z * 20 + 100),
            de.FLAG_COL: 0,  # 아래에서 결측 tenure와 맞춘다
            de.WEIGHT_COL: rng.uniform(10, 1000, n),
            "is_seoul": rng.random(n) < seoul_share,
        }
    )
    df[de.FLAG_COL] = df[de.CONTINUOUS_COL].isna().astype(int)
    return df


def test_winsorize_uses_the_given_sample_quantiles_unweighted():
    y = pd.Series(np.arange(1, 101, dtype=float))
    clipped, info = de.winsorize_outcome(y)
    assert info["lower"] == pytest.approx(y.quantile(0.01))
    assert info["upper"] == pytest.approx(y.quantile(0.99))
    assert clipped.min() == info["lower"] and clipped.max() == info["upper"]
    assert info["n_clipped_lower"] == 1 and info["n_clipped_upper"] == 1


def test_winsorize_quantiles_differ_by_sample():
    a = pd.Series(np.arange(100, dtype=float))
    b = pd.Series(np.arange(100, dtype=float) * 10)
    assert de.winsorize_outcome(a)[1]["upper"] != de.winsorize_outcome(b)[1]["upper"]


def test_design_columns_with_industry():
    x = de.build_design(_synthetic(300))
    cols = set(x.columns)
    assert {"산업중분류코드_47", "산업중분류코드_56", "산업중분류코드_96"} <= cols
    assert {"행정구역시도코드_11", "일반_창업형태코드_1", de.CONTINUOUS_COL, de.FLAG_COL} <= cols
    assert x.isna().sum().sum() == 0


def test_design_without_industry_drops_industry_dummies():
    x = de.build_design(_synthetic(300), include_industry=False)
    assert not any(c.startswith("산업중분류코드") for c in x.columns)
    assert any(c.startswith("행정구역시도코드") for c in x.columns)


def test_tenure_missing_filled_with_zero():
    df = _synthetic(300)
    assert df[de.CONTINUOUS_COL].isna().any()
    x = de.build_design(df)
    assert (x.loc[df[de.CONTINUOUS_COL].isna().to_numpy(), de.CONTINUOUS_COL] == 0).all()


def test_constant_columns_are_dropped():
    df = _synthetic(300)
    df["일반_창업형태코드"] = "1"
    assert not any(c.startswith("일반_창업형태코드") for c in de.build_design(df).columns)


def test_normalize_weights_mean_one():
    assert de.normalize_weights(np.array([1.0, 2.0, 3.0])).mean() == pytest.approx(1.0)


def test_verdict_phrases():
    assert de.verdict(True) == de.VERDICT_ZERO_IN
    assert de.verdict(False) == de.VERDICT_ZERO_OUT


def test_fit_irm_recovers_known_ate():
    df = _synthetic(2500, ate=0.5)
    x = de.build_design(df)
    f = de.fit_irm(x, df[de.OUTCOME].to_numpy(), df[de.TREATMENT].to_numpy(), "hgb", n_rep=1)
    assert abs(f["ate"] - 0.5) < 0.25
    assert f["ci_low"] < f["ate"] < f["ci_high"]
    assert not f["ci_contains_zero"]
    assert f["ps_refit_max_abs_diff"] < 1e-6  # 재적합 성향점수 = DoubleML 저장값(절단 후)


def test_ps_diagnostics_counts_and_kish():
    ps = np.array([0.01, 0.5, 0.5, 0.99])
    d = np.array([0, 1, 0, 1])
    g = de.ps_diagnostics(ps, d)
    assert g["n_clip_lower"] == 1 and g["n_clip_upper"] == 1
    assert 0 < g["kish_ess"] <= 4
    assert g["treated_share"] == 0.5


def test_smd_balanced_after_weighting_in_known_design():
    df = _synthetic(4000)
    x = de.build_design(df)[[de.CONTINUOUS_COL]]
    z = (df[de.CONTINUOUS_COL].fillna(100) - 100) / 20
    true_ps = 1 / (1 + np.exp(-0.8 * z.to_numpy()))
    t = de.smd_table(x, df[de.TREATMENT].to_numpy(), true_ps)
    assert abs(t["smd_after"].iloc[0]) < abs(t["smd_before"].iloc[0])


def test_run_sample_returns_all_analyses_on_synthetic_data():
    res = de.run_sample(_synthetic(1200), "합성", True, {"tenure_months": [de.CONTINUOUS_COL]})
    assert set(res["analyses"]) == {"1_main", "2_weighted", "3_excluded", "4_simple", "5_ovb", "6_seoul"}
    assert res["analyses"]["1_main"]["n"] == 1200
    assert res["clip"]["lower"] < res["clip"]["upper"]

