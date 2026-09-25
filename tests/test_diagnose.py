# -*- coding: utf-8 -*-
"""W2-3 요인 진단 테스트 (합성 데이터)."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from src.data import master_schema as ms
from src.models import detect, diagnose, features
from tests.test_models import _online_table, synthetic_master


@pytest.fixture(scope="module")
def panel():
    return synthetic_master(n_stores=200)


def test_mapping_covers_every_predictor_once():
    preds = ms.predictor_columns() + list(features.ONLINE_PREDICTORS)
    diagnose.check_mapping(preds)  # 예외가 없어야 한다
    mapped = [c for f in diagnose.FACTORS for c in f["features"]]
    assert sorted(mapped) == sorted(set(mapped)) and set(preds) == set(mapped)
    assert {f["category"] for f in diagnose.FACTORS} == set(diagnose.CATEGORIES)
    t = diagnose.factor_table()
    assert set(t["feature"]) == set(preds)


def test_mapping_rejects_unmapped_predictor():
    with pytest.raises(ValueError):
        diagnose.check_mapping(ms.predictor_columns() + ["new_feature_x"])


def test_shapley_is_exact_on_additive_model():
    """f(x) = Σ g_k(x_k)인 모형에서 요인 Shapley = g_k(x_k) − E_b g_k(b_k)."""
    class Additive:
        def predict_proba(self, X):
            return 0.1 * X["a"].to_numpy() + 0.2 * X["b"].to_numpy() + 0.05 * X["c"].to_numpy()

    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(7, 3)), columns=["a", "b", "c"])
    B = pd.DataFrame(rng.normal(size=(5, 3)), columns=["a", "b", "c"])
    phi, base = diagnose.factor_shapley(Additive(), X, B, [["a"], ["b", "c"]])
    exp_a = 0.1 * (X["a"] - B["a"].mean())
    exp_bc = 0.2 * (X["b"] - B["b"].mean()) + 0.05 * (X["c"] - B["c"].mean())
    assert np.allclose(phi[:, 0], exp_a) and np.allclose(phi[:, 1], exp_bc)
    assert math.isclose(base, Additive().predict_proba(B).mean())


def test_shapley_additivity_on_real_model(panel):
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    y = panel["event_12m"].to_numpy()
    m = detect.DetectModel().fit(X, y)
    fc = [[c for c in f["features"] if c in m.columns_] for f in diagnose.FACTORS]
    fc = [c for c in fc if c]
    phi, base = diagnose.factor_shapley(m, X.head(40), X.sample(8, random_state=0), fc, chunk=15)
    assert np.abs(phi.sum(axis=1) + base - m.predict_proba(X.head(40))).max() < 1e-9


def test_peer_percentile_fallback():
    rows = []
    for i in range(40):  # 큰 그룹: 1단계에서 백분위
        rows.append(("S%d" % i, "2025Q2", "미용업", "마포구", "1~3년", "tenure", i / 100))
    for i in range(5):  # 작은 그룹: 자치구를 풀어 상위 단계로
        rows.append(("T%d" % i, "2025Q2", "미용업", "광진구", "1~3년", "tenure", i / 100))
    long = pd.DataFrame(rows, columns=["store_id", "origin", "biz_type", "gu", "age_band", "factor_id", "contribution"])
    out = diagnose.add_peer_percentiles(long, min_n=30)
    assert (out.loc[out["gu"] == "마포구", "peer_level"] == "biz_type·gu·age_band").all()
    assert (out.loc[out["gu"] == "광진구", "peer_level"] == "biz_type·age_band").all()
    assert out["peer_percentile"].between(0, 100).all()


@pytest.mark.parametrize("word,expect", [("업력", "업력이"), ("자치구", "자치구가"),
                                         ("온라인 언급(블로그)", "온라인 언급(블로그)이")])
def test_josa(word, expect):
    assert diagnose._josa(word, "이", "가") == expect


def test_run_end_to_end(tmp_path, panel):
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    out = tmp_path / "diag"
    long = diagnose.run(mp, out, online_path=op, primary="enriched", origin=None,
                        n_background=4, max_stores=30)
    assert set(long["category"]) <= set(diagnose.CATEGORIES)
    assert "rent_level" not in set(long["factor_id"])  # 학습 구간에 공시지가 값이 없다
    cat = pd.read_parquet(out / "diagnosis_by_category.parquet")
    total = cat[list(diagnose.CATEGORIES)].sum(axis=1) + cat["base_value"]
    assert np.allclose(total, cat["probability_12m"])  # 유형 합 + base = 예측 확률
    sample = json.loads((out / "sample_factors.json").read_text(encoding="utf-8"))
    f0 = sample[0]["factors"][0]
    assert set(f0) >= {"category", "name", "contribution", "direction", "peer_percentile", "actionability",
                       "explanation", "values"}
    online = [f for f in sample[0]["factors"] if f["factor_id"] == "online_attention"][0]
    assert set(online["values"]) == set(features.ONLINE_PREDICTORS)  # 판단 근거 값이 함께 나간다
    assert online["driver"] and "주된 근거" in online["explanation"]
    assert (long.loc[long["factor_id"] == "online_attention", "driver_feature"].isin(features.ONLINE_PREDICTORS)).all()
    assert sample[0]["unavailable_categories"] == ["비용"]
    assert (cat["비용_available"] == False).all() and (cat["경쟁_available"] == True).all()  # noqa: E712
    text = " ".join(f["explanation"] for s in sample for f in s["factors"])
    for banned in ("때문", "원인", "고치면", "개선하면"):  # 인과 표현 금지 (§10-3)
        assert banned not in text
    for f in ("factor_map.csv", "factor_summary.csv", "diagnosis.parquet"):
        assert (out / f).exists()


def test_peer_sentence_only_for_top30():
    base = {"factor": "업력", "contribution": 0.05, "peer_level": "biz_type·gu·age_band"}
    assert "상위" in diagnose.explanation({**base, "peer_percentile": 85})
    assert "상위" not in diagnose.explanation({**base, "peer_percentile": 60})


@pytest.mark.parametrize("feature,v,expect", [
    ("online_blog_trend_6m", -37, "최근 6개월 블로그 언급이 그 전 6개월보다 37건 줄어듦"),
    ("online_blog_months_since_last", float("nan"), "블로그 언급 이력 없음"),
    ("online_blog_months_since_last", 0, "이번 달에도 블로그 언급 있음"),
    ("online_blog_cnt_12m", 101, "최근 12개월 블로그 언급 101건"),
    ("online_blog_cnt_3m", float("nan"), "관측 불가(검색 결과 상한)"),
])
def test_online_driver_text(feature, v, expect):
    assert diagnose.online_driver_text(feature, v) == expect
