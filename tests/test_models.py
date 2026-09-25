# -*- coding: utf-8 -*-
"""W2-2 탐지 모형 코드 테스트 (합성 패널, 실데이터 불필요)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data import master_schema as ms
from src.models import calibration, detect, features, train_detect, uncertainty

ORIGINS = [str(p) for p in pd.period_range("2021Q1", "2025Q2", freq="Q")]  # 18개


def synthetic_master(n_stores: int = 300, seed: int = 0) -> pd.DataFrame:
    """master_schema 컬럼을 모두 갖춘 합성 패널. 결측 구조는 실측을 흉내 낸다.

    누수 미끼: provenance인 er_matched를 라벨과 거의 같게 만든다.
    입력에 섞이면 성능이 비정상적으로 뛰므로, 입력에서 빠지는지 확인할 수 있다.
    """
    rng = np.random.default_rng(seed)
    stores = [f"GR_{i:05d}" for i in range(n_stores)]
    df = pd.DataFrame([(s, o) for s in stores for o in ORIGINS], columns=["store_id", "origin"])
    n = len(df)
    q = pd.PeriodIndex(df["origin"], freq="Q")
    st = pd.DataFrame({
        "store_id": stores,
        "biz_type": rng.choice(["일반음식점", "휴게음식점", "미용업"], n_stores),
        "gu": rng.choice(["광진구", "마포구", "영등포구"], n_stores),
        "trdar_cd": np.where(rng.random(n_stores) < 0.22, None, "3110001"),
        "risk": rng.normal(0, 1, n_stores),
    })
    df = df.merge(st, on="store_id")
    y = (rng.random(n) < 1 / (1 + np.exp(-(-2.2 + 0.8 * df["risk"])))).astype(int)
    df["event_12m"] = y
    for c in ms.COLUMN_ROLES:
        if c not in df.columns:
            df[c] = pd.NA
    df["source_type"] = df["biz_type"]
    df["origin_start"] = q.start_time
    df["origin_end"] = q.end_time.normalize()
    df["age_months"] = rng.integers(0, 200, n).astype("int32")
    df["area"] = pd.array(rng.lognormal(3.5, 0.5, n), dtype="Float64")
    df["has_coord"] = rng.random(n) < 0.95
    df["er_matched"] = y == 0  # 누수 미끼
    lp_ok = q >= pd.Period("2024Q2", "Q")
    df["land_price"] = pd.array(np.where(lp_ok, rng.lognormal(15, 0.5, n), np.nan), dtype="Float64")
    df.loc[~lp_ok, "land_price"] = pd.NA
    has_tr = df["trdar_cd"].notna() & (df["origin"] != "2021Q1")
    for c in ms.predictor_columns():
        if features.group_of(c) not in ("trdar", "trdar_biz"):
            continue
        if c == "trdar_change_index":
            df[c] = pd.Series(rng.choice(["HH", "HL", "LH", "LL"], n), dtype=object).where(has_tr, None)
        else:
            v = rng.lognormal(5, 1, n) + (20 * df["risk"].to_numpy() if c == "trdar_flow_pop" else 0)
            df[c] = pd.array(v, dtype="Float64")
            df.loc[~has_tr, c] = pd.NA
    return df[list(ms.COLUMN_ROLES)]


@pytest.fixture(scope="module")
def panel():
    return synthetic_master()


# ---------------------------------------------------------------- feature 선택
def test_base_is_exactly_schema_predictors(panel):
    cols = features.select_features(panel.columns, "base")
    assert cols == ms.predictor_columns(panel.columns)
    for leak in ("er_matched", "match_confidence", "sj_entity_id", "trdar_cd", "trdar_type",
                 "in_polygon", "pnu", "land_price_year_used", "event_12m"):
        assert leak not in cols


def test_feature_sets_apply_rules(panel):
    no_tr = features.select_features(panel.columns, "no_trdar")
    assert not [c for c in no_tr if features.group_of(c) in ("trdar", "trdar_biz")]
    assert "land_price" in no_tr
    lic = features.select_features(panel.columns, "license_only")
    assert {features.group_of(c) for c in lic} == {"license"}
    assert "land_price" not in features.select_features(panel.columns, "no_land_price")
    assert "gu" not in features.select_features(panel.columns, "no_gu")
    with pytest.raises(KeyError):
        features.select_features(panel.columns, "없는세트")


def test_non_predictor_input_is_rejected(panel):
    with pytest.raises(ValueError):
        features.build_X(panel, ["age_months", "er_matched"])


# ---------------------------------------------------------------- 입력 행렬
def test_build_X_dtypes(panel):
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    for c in X.columns:
        if c in features.CATEGORICAL:
            assert isinstance(X[c].dtype, pd.CategoricalDtype)
        else:
            assert X[c].dtype == "float64", c  # pd.NA 없음, nullable dtype 없음
    assert X["land_price"].isna().sum() == panel["land_price"].isna().sum()
    assert X["trdar_change_index"].isna().sum() == panel["trdar_change_index"].isna().sum()
    assert not [c for c in X.columns if c.endswith("_isna")]  # 결측 지시자를 만들지 않는다


def test_categories_fixed_between_fit_and_predict(panel):
    cols = features.select_features(panel.columns, "license_only")
    X = features.build_X(panel, cols)
    y = panel["event_12m"].to_numpy()
    m = detect.DetectModel().fit(X, y)
    new = panel.head(5).copy()
    new["gu"] = ["광진구", "처음보는구", "마포구", "영등포구", None]
    Xn = features.build_X(new, cols, categories={c: m.categories_[c] for c in m.categories_})
    p = m.predict_proba(Xn)
    assert p.shape == (5,) and np.isfinite(p).all()
    assert pd.isna(Xn["gu"].iloc[1])  # 모르는 범주는 NaN


def test_all_na_column_in_training_is_dropped(panel):
    """초기 fold는 land_price가 전부 NA — 학습에서 빼고 예측도 같은 컬럼으로 한다."""
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    y = panel["event_12m"].to_numpy()
    early = (panel["origin"] < "2024Q2").to_numpy()
    m = detect.DetectModel().fit(X[early], y[early])
    assert m.dropped_all_na_ == ["land_price"]
    p = m.predict_proba(X[~early])  # land_price 값이 있는 구간
    assert np.isfinite(p).all()


# ---------------------------------------------------------------- 시간 분할
def test_rolling_oof_respects_embargo(panel):
    X = features.build_X(panel, features.select_features(panel.columns, "license_only"))
    y = panel["event_12m"].to_numpy()
    seen = []

    def spy(a, b, c):
        seen.append((panel.loc[a.index, "origin"].max(), panel.loc[c.index, "origin"].unique().tolist()))
        return np.full(len(c), 0.1)

    oof = calibration.rolling_oof_predictions(panel, X, y, spy, min_train_origins=4, embargo=4)
    assert sorted(oof["origin"].unique()) == ORIGINS[8:]
    for train_max, test in seen:
        assert len(test) == 1
        assert ORIGINS.index(train_max) <= ORIGINS.index(test[0]) - 5  # t-1~t-4 비움


def test_bootstrap_resamples_whole_stores(panel):
    sub = panel[panel["origin"].isin(ORIGINS[:4])].reset_index(drop=True)
    X = features.build_X(sub, ["age_months"])
    y = sub["event_12m"].to_numpy()
    counts = []

    def spy(a, b, c):
        counts.append(sub.loc[a.index, "store_id"].value_counts())
        return np.zeros(len(c))

    uncertainty.bootstrap_interval(spy, X, y, X.head(3), n_boot=3, group=sub["store_id"].to_numpy())
    for vc in counts:
        assert (vc % 4 == 0).all()  # 점포당 4개 origin이 통째로 뽑힌다


# ---------------------------------------------------------------- 전 과정
def test_end_to_end(tmp_path, panel):
    path = tmp_path / "master.parquet"
    panel.to_parquet(path, index=False)
    out = tmp_path / "out"
    train_detect.run(path, out, ["base", "no_trdar"], n_boot=2, with_split_comparison=True)

    r = pd.read_parquet(out / "risk_scores.parquet")
    assert set(r["origin"]) == set(ORIGINS[-2:])
    assert len(r) == (panel["origin"].isin(ORIGINS[-2:])).sum()
    assert r["probability_12m"].between(0, 1).all()
    assert (r["ci_low"] <= r["probability_12m"]).all() and (r["probability_12m"] <= r["ci_high"]).all()
    assert set(r["band"]) <= {"low", "mid", "high"}
    assert r["percentile"].between(0, 100).all()

    summ = pd.read_csv(out / "sensitivity_summary.csv")
    assert set(summ["feature_set"]) == {"base", "no_trdar"}
    # 누수 미끼(er_matched)가 입력에 섞였다면 AUC가 0.95를 넘는다
    assert summ["all_auc_mean"].max() < 0.9
    cut = pd.read_csv(out / "band_cutoffs.csv").iloc[0]
    assert cut["base_rate"] <= cut["cut_mid"] < cut["cut_high"]  # mid는 평균 이상 위험에서 시작한다
    imp = pd.read_csv(out / "feature_importance.csv")
    assert "[group] trdar" in set(imp["feature"]) and "er_matched" not in set(imp["feature"])
    sc = pd.read_csv(out / "split_comparison.csv")
    assert {"time_split(embargo=4)", "time_split(embargo=0)"} <= set(sc["setting"])
    for f in ("calibration_report.csv", "band_cutoffs.csv", "band_profile.csv", "run_meta.json",
              "oof_metrics_by_origin.csv", "missing_by_origin.csv", "reliability.png",
              "feature_importance.csv"):
        assert (out / f).exists(), f
