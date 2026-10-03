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
    # 정상 합성 입력이면 6개 분석 모두 status 없이 유한한 추정·CI를 낸다 (R1-5)
    for key, a in res["analyses"].items():
        assert "status" not in a, (key, a)
        assert np.isfinite(a["ate"]) and np.isfinite(a["ci_low"]) and np.isfinite(a["ci_high"]), key



def test_ci_covers_true_ate_in_confounded_dgp():
    """교란이 있는 합성 DGP(참값 0.5)에서 추정이 참값에서 SE의 3배 안에 있고 CI가 참값을 포함한다."""
    df = _synthetic(3000, ate=0.5, seed=3)
    x = de.build_design(df)
    f = de.fit_irm(x, df[de.OUTCOME].to_numpy(), df[de.TREATMENT].to_numpy(), "hgb", n_rep=1)
    assert abs(f["ate"] - 0.5) < 3 * f["se"]
    assert f["ci_low"] <= 0.5 <= f["ci_high"]


def test_failed_analysis_is_recorded_as_unfittable(monkeypatch):
    real = de.fit_irm

    def flaky(x, y, d, kind="hgb", *a, **k):
        if kind == "simple":
            raise RuntimeError("forced failure")
        return real(x, y, d, kind, *a, **k)

    monkeypatch.setattr(de, "fit_irm", flaky)
    res = de.run_sample(_synthetic(800), "합성", True, {"tenure_months": [de.CONTINUOUS_COL]})
    assert res["analyses"]["4_simple"]["status"] == "적합 불가"
    assert "status" not in res["analyses"]["1_main"]


def test_excluded_analysis_reclips_outcome_on_its_own_sample():
    """R1-1: 행 제외 민감도는 남긴 행의 원본 결과변수로 분위를 다시 계산한다(전체 표본 분위가 아님)."""
    rng = np.random.default_rng(5)
    df = _synthetic(2000, seed=5)
    z = (df[de.CONTINUOUS_COL].fillna(100) - 100) / 20
    d = rng.binomial(1, 1 / (1 + np.exp(-2.5 * z - 1.5)))  # 강한 선택 -> 성향점수가 [0.05, 0.95] 밖인 행이 생긴다
    df[de.TREATMENT] = d
    df[de.OUTCOME] = 0.5 * d + 0.7 * z + rng.normal(scale=0.5, size=len(df))
    res = de.run_sample(df, "합성", True, {"tenure_months": [de.CONTINUOUS_COL]})
    ex = res["analyses"]["3_excluded"]
    assert ex["n_excluded"] > 0
    assert (ex["clip"]["lower"], ex["clip"]["upper"]) != (res["clip"]["lower"], res["clip"]["upper"])


def test_simple_learner_standardizes_inside_the_pipeline():
    from sklearn.pipeline import Pipeline

    x = de.build_design(_synthetic(300))
    ml_g, ml_m = de.make_learners("simple", list(x.columns))
    assert isinstance(ml_g, Pipeline) and isinstance(ml_m, Pipeline)
    assert hasattr(ml_m, "predict_proba")


def test_simple_learner_works_without_tenure_column():
    df = _synthetic(1000)
    df[de.CONTINUOUS_COL] = 100.0  # 상수 -> 설계행렬에서 삭제된다
    df[de.FLAG_COL] = 0
    x = de.build_design(df)
    assert de.CONTINUOUS_COL not in x.columns
    ml_g, ml_m = de.make_learners("simple", list(x.columns))
    assert not hasattr(ml_g, "steps") and not hasattr(ml_m, "steps")
    f = de.fit_irm(x, df[de.OUTCOME].to_numpy(), df[de.TREATMENT].to_numpy(), "simple", n_rep=1)
    assert np.isfinite(f["ate"])


def test_smd_zero_denominator_cases():
    """R1-3: 분모 0이고 평균차 0이면 0, 평균차가 0이 아니면 inf + smd_undefined."""
    d = np.array([1, 1, 1, 0, 0, 0, 0, 0])
    x = pd.DataFrame({"same": np.full(8, 5.0), "split": d.astype(float), "varied": np.arange(8, dtype=float)})
    t = de.smd_table(x, d, np.full(8, 0.4)).set_index("covariate")
    assert t.loc["same", "smd_before"] == 0.0 and not t.loc["same", "smd_undefined"]
    assert np.isinf(t.loc["split", "smd_before"]) and t.loc["split", "smd_undefined"]
    assert np.isfinite(t.loc["varied", "smd_before"]) and not t.loc["varied", "smd_undefined"]
    assert int((t["smd_before"].abs() > 0.1).sum()) >= 2  # inf도 0.1 초과로 센다
