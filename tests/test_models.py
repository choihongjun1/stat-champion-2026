# -*- coding: utf-8 -*-
"""W2-2 탐지 모형 코드 테스트 (합성 패널, 실데이터 불필요)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.data import master_schema as ms
from src.models import bands, calibration, detect, features, train_detect, uncertainty

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
    assert set(sc["params"]) == {"현 설정", "튜닝"}
    assert {"test_scope", "auc_last_origin", "n_last_origin"} <= set(sc.columns)
    assert (sc.loc[sc["setting"] == "random_split(금지·대조군)", "test_scope"] == "전체 origin 무작위 ~20% 표본").all()
    for f in ("calibration_window_report.csv", "calibration_decision.json", "band_cutoffs.csv", "band_profile.csv",
              "run_meta.json", "oof_metrics_by_origin.csv", "missing_by_origin.csv", "reliability.png",
              "reliability_select.png", "feature_importance.csv", "oof_predictions.parquet"):
        assert (out / f).exists(), f

    cwr = pd.read_csv(out / "calibration_window_report.csv")
    assert set(cwr["params"]) == {"현 설정", "튜닝"}
    assert set(cwr["window"]) == {"select", "test"} and set(cwr["candidate"]) == {"raw", "isotonic", "platt"}
    decision = json.loads((out / "calibration_decision.json").read_text(encoding="utf-8"))
    assert set(decision) == {"현 설정", "튜닝"} and decision["현 설정"]["chosen"] in ("raw", "isotonic", "platt")

    oof = pd.read_parquet(out / "oof_predictions.parquet")
    assert set(oof["config"]) == {"현 설정", "튜닝"} and set(oof.columns) == {"store_id", "origin", "config", "p_oof", "y"}
    assert not oof["store_id"].isna().any() and not oof["p_oof"].isna().any()
    assert not oof.duplicated(["store_id", "origin", "config"]).any()

    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["model_params_by_config"] == {"현 설정": detect.DEFAULT_PARAMS, "튜닝": train_detect.TUNED_PARAMS}
    assert meta["params"] == detect.DEFAULT_PARAMS  # 서빙이 읽는 키는 현 설정 그대로
    assert meta["oof_predictions_sha256"] == train_detect.sha256(out / "oof_predictions.parquet")
    assert meta["oof_predictions_rows"] == len(oof)
    assert meta["calibration_candidate"] in ("raw", "isotonic", "platt")
    assert meta["calibration_applied"] == (meta["calibration_candidate"] != "raw")
    # #45: 컷오프 정의 문장·fallback·provenance가 run_meta에 남는다
    assert meta["band_definition"] == {"cut_mid": bands.CUT_MID_DEFINITION, "cut_high": bands.CUT_HIGH_DEFINITION}
    bp = meta["band_provenance"]
    assert bp["calib_origins"] == meta["calibration_windows"]["fit"] and bp["cut_mid"] == pytest.approx(cut["cut_mid"])
    assert isinstance(bp["high_fallback"], bool) and isinstance(bp["mid_fallback"], bool)
    assert bp["base_rate"] == pytest.approx(cut["base_rate"]) and 0 <= bp["high_share_test"] <= 1
    assert len(bp["high_lift_ci95"]) == 2
    assert bp["window_rule"].startswith("fixed") and bp["n_cutoff_rows"] == (oof["origin"].isin(bp["calib_origins"])
                                                                             & (oof["config"] == "현 설정")).sum()
    assert (bp["target_high_lift"], bp["target_mid_lift"], bp["high_min_group_n"]) == (2.0, 1.2, 200)
    assert bp["high_grid"] == {"quantile_from": 0.50, "quantile_to": 0.995, "n_points": 200, "fallback_quantile": 0.95}
    assert bp["high_lift_ci"]["unit"] == "store_id" and bp["high_lift_ci"]["seed"] == bands.LIFT_CI_SEED
    assert bp["high_lift_ci"]["percentiles"] == [2.5, 97.5] and bp["high_lift_ci"]["n_boot"] > 0


def test_tuned_params_keep_every_default_except_the_two_tuned_values():
    """튜닝 설정은 DEFAULT_PARAMS 전체 위에 learning_rate·max_leaf_nodes만 바꾼다 — 두 값만 넘기면 나머지가
    sklearn 기본값(max_iter=100, early_stopping='auto', random_state=None …)이 되어 다른 모형·비결정적 실행이 된다."""
    tuned, default = train_detect.TUNED_PARAMS, detect.DEFAULT_PARAMS
    assert set(tuned) == set(default)
    assert {k for k in default if tuned[k] != default[k]} == {"learning_rate", "max_leaf_nodes"}
    assert (tuned["learning_rate"], tuned["max_leaf_nodes"]) == (0.03, 15)
    assert tuned["early_stopping"] is False and tuned["random_state"] == default["random_state"]


def test_tuned_fit_is_deterministic():
    # early_stopping='auto'는 1만 행 이상에서 켜지므로 그보다 큰 학습 표본으로 확인한다
    df = synthetic_master(n_stores=900, seed=5)
    X = features.build_X(df, features.select_features(df.columns, "base"))
    y = df["event_12m"].to_numpy()
    tr = (df["origin"] < "2024Q2").to_numpy()
    te = (df["origin"] == "2025Q2").to_numpy()
    assert tr.sum() > 10_000
    a = detect.fit_predict(X[tr], y[tr], X[te], train_detect.TUNED_PARAMS)
    b = detect.fit_predict(X[tr], y[tr], X[te], train_detect.TUNED_PARAMS)
    np.testing.assert_array_equal(a, b)


# ---------------------------------------------------------------- Enriched (온라인)
def _online_table(panel, seed=1):
    rng = np.random.default_rng(seed)
    t = panel[["store_id", "origin", "origin_end"]].copy()
    y = panel["event_12m"].to_numpy()
    has = (rng.random(len(t)) < np.where(y == 1, 0.3, 0.6)).astype(float)
    for c in features.ONLINE_PREDICTORS:
        t[c] = rng.poisson(3, len(t)).astype(float)
    t["online_blog_has_12m"] = has
    t.loc[rng.random(len(t)) < 0.1, list(features.ONLINE_PREDICTORS)] = np.nan
    t["online_feature_asof"] = t["origin_end"]
    return t.drop(columns="origin_end")


def test_enriched_feature_set_requires_online(panel):
    with pytest.raises(ValueError):
        features.select_features(panel.columns, "enriched")
    cols = features.select_features(list(panel.columns) + list(features.ONLINE_PREDICTORS), "enriched")
    assert set(features.ONLINE_PREDICTORS) <= set(cols)
    assert not set(features.ONLINE_PREDICTORS) & set(features.select_features(
        list(panel.columns) + list(features.ONLINE_PREDICTORS), "base"))


def test_end_to_end_with_online(tmp_path, panel):
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    out = tmp_path / "out"
    train_detect.run(mp, out, ["base", "enriched"], n_boot=0, with_split_comparison=False,
                     online_path=op, primary="enriched")
    summ = pd.read_csv(out / "sensitivity_summary.csv").set_index("feature_set")
    assert summ.loc["enriched", "n_features"] == summ.loc["base", "n_features"] + len(features.ONLINE_PREDICTORS)
    assert summ.loc["enriched", "all_auc_mean"] > summ.loc["base", "all_auc_mean"]  # 심어 둔 신호를 쓴다
    imp = pd.read_csv(out / "feature_importance.csv")
    assert "[group] online" in set(imp["feature"])
    assert set(pd.read_parquet(out / "risk_scores.parquet")["model"]) == {"detect_v0_enriched"}


def test_online_table_built_for_other_origin_definition_is_rejected(tmp_path, panel):
    t = _online_table(panel)
    t["online_feature_asof"] = t["online_feature_asof"] + pd.Timedelta(days=1)
    op = tmp_path / "online.parquet"
    t.to_parquet(op, index=False)
    with pytest.raises(ValueError, match="다른 origin 정의"):
        train_detect.attach_online(panel, op)


def test_attach_online_new_format_checks_available_at(tmp_path, panel):
    """새 형식(online_collected_at 있음): available_at(마지막 게시월 말일) > origin_end면 멈춘다.
    collected_at(수집 시각, 항상 origin 뒤)은 검사하지 않는다 — 회고적 재구성이라 누수가 아니다."""
    t = _online_table(panel).merge(panel[["store_id", "origin", "origin_end"]], on=["store_id", "origin"])
    t["online_available_at"] = t["origin_end"]
    t["online_collected_at"] = pd.Timestamp("2026-09-23 23:39:46")
    op = tmp_path / "online.parquet"
    t.drop(columns="origin_end").to_parquet(op, index=False)
    out = train_detect.attach_online(panel, op)
    assert not {"online_feature_asof", "online_available_at", "online_collected_at"} & set(out.columns)
    t["online_available_at"] = t["origin_end"] + pd.offsets.MonthEnd(1)  # 다음 달 게시물까지 들어갈 수 있는 창
    t.drop(columns="origin_end").to_parquet(op, index=False)
    with pytest.raises(ValueError, match="available_at > origin_end"):
        train_detect.attach_online(panel, op)


def test_attach_online_old_format_still_loads(tmp_path, panel, capsys):
    """이전 형식(2026-09-29: available_at에 수집 시각, collected_at 열 없음)도 하위 산출물을 위해 읽되 경고한다."""
    t = _online_table(panel)
    t["online_available_at"] = pd.Timestamp("2026-09-23")
    op = tmp_path / "online.parquet"
    t.to_parquet(op, index=False)
    out = train_detect.attach_online(panel, op)
    assert len(out) == len(panel) and "online_available_at" not in out.columns
    assert "이전 형식 온라인 표" in capsys.readouterr().out


# ---------------------------------------------------------------- #32 리뷰: 보정 3구간 · Platt · 분할 비교
def test_platt_calibrator_recovers_known_slope():
    rng = np.random.default_rng(0)
    true = rng.uniform(0.03, 0.4, 50_000)
    y = (rng.random(len(true)) < true).astype(int)
    from scipy.special import expit, logit
    p_overconf = expit(2.0 * logit(true) + 1.0)  # 기울기 0.5·절편 -0.5짜리 과신 예측
    cal = calibration.PlattCalibrator().fit(p_overconf, y)
    fixed = cal.predict(p_overconf)
    slope, intercept = calibration.calibration_slope_intercept(y, fixed)
    assert slope == pytest.approx(1, abs=0.03)
    from sklearn.metrics import roc_auc_score
    assert roc_auc_score(y, fixed) == pytest.approx(roc_auc_score(y, p_overconf))  # 단조 변환 — 순위 불변


def test_calibration_slope_intercept_true_probabilities_near_one_zero():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.02, 0.5, 200_000)
    y = (rng.random(len(p)) < p).astype(int)
    slope, intercept = calibration.calibration_slope_intercept(y, p)
    assert slope == pytest.approx(1, abs=0.03) and intercept == pytest.approx(0, abs=0.02)


def test_bootstrap_brier_diff_detects_real_gap_and_null():
    rng = np.random.default_rng(2)
    stores = np.repeat([f"S{i}" for i in range(1500)], 2)
    y = (rng.random(len(stores)) < 0.2).astype(int)
    good = np.clip(y * 0.6 + rng.normal(0.1, 0.05, len(stores)), 0.01, 0.99)
    bad = np.clip(rng.uniform(0, 1, len(stores)), 0.01, 0.99)
    r = calibration.bootstrap_brier_diff(y, good, bad, stores, n_boot=300, seed=1)
    assert r["significant"] and r["diff"] < 0 and r["ci_low"] < r["diff"] < r["ci_high"]
    same = calibration.bootstrap_brier_diff(y, good, good, stores, n_boot=50)
    assert same["diff"] == 0 and not same["significant"]


def test_calibration_windows_split_dont_overlap_and_match_today_quarters():
    w = train_detect.calibration_windows(ORIGINS)
    assert w["fit"] == ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    assert w["select"] == ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]
    assert w["test"] == ["2025Q1", "2025Q2"]
    allo = w["fit"] + w["select"] + w["test"]
    assert len(allo) == len(set(allo))  # 겹치지 않음
    with pytest.raises(ValueError):
        train_detect.calibration_windows(ORIGINS[:5])


def _synthetic_oof(seed=0, n_per_origin=300, slope=1.0, origins=ORIGINS[8:]):
    """select+test 구간 OOF를 직접 합성 — calibration_analysis를 빠르게 단위 테스트하기 위함."""
    rng = np.random.default_rng(seed)
    rows = []
    for i, o in enumerate(origins):
        true_p = rng.uniform(0.03, 0.4, n_per_origin)
        y = (rng.random(n_per_origin) < true_p).astype(int)
        from scipy.special import expit, logit
        p_oof = expit(slope * logit(true_p))  # slope<1이면 과신(원 확률이 극단적)
        rows.append(pd.DataFrame({"origin": o, "idx": np.arange(n_per_origin) + i * n_per_origin,
                                  "p_oof": p_oof, "y": y}))
    return pd.concat(rows, ignore_index=True)


def test_choose_calibration_picks_raw_when_already_well_calibrated():
    oof = _synthetic_oof(slope=1.0)
    df = pd.DataFrame({"store_id": [f"S{i}" for i in range(len(oof))]})
    windows = train_detect.calibration_windows(ORIGINS)
    chosen, decision, cands, sel_metrics = train_detect.choose_calibration(oof, df, windows, n_boot=200)
    assert chosen == "raw" and decision["chosen"] == "raw"
    assert set(sel_metrics["candidate"]) == {"raw", "isotonic", "platt"}


def test_choose_calibration_picks_candidate_when_overconfident():
    oof = _synthetic_oof(slope=0.3)  # 뚜렷하게 과신 — 보정이 유의하게 나아야 한다
    df = pd.DataFrame({"store_id": [f"S{i}" for i in range(len(oof))]})
    windows = train_detect.calibration_windows(ORIGINS)
    chosen, decision, cands, sel_metrics = train_detect.choose_calibration(oof, df, windows, n_boot=200)
    assert chosen in ("isotonic", "platt")
    assert decision["boot_vs_raw"]["significant"] and decision["boot_vs_raw"]["diff"] < 0


def test_calibration_analysis_reports_both_windows_and_marks_chosen():
    oof = _synthetic_oof(slope=0.3)
    df = pd.DataFrame({"store_id": [f"S{i}" for i in range(len(oof))]})
    a = train_detect.calibration_analysis(oof, df, ORIGINS, n_boot=200)
    assert set(a["report"]["window"]) == {"select", "test"}
    assert set(a["report"]["candidate"]) == {"raw", "isotonic", "platt"}
    assert a["report"]["chosen"].sum() == 2  # select·test 각 1행씩 chosen=True
    assert a["chosen"] in ("raw", "isotonic", "platt")
    assert len(a["test_y"]) == len(a["test_preds"]["raw"]) == len(a["test_preds"][a["chosen"]])


def test_split_comparison_has_last_origin_columns_and_scope_note(panel):
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    y = panel["event_12m"].to_numpy()
    sc = train_detect.split_comparison(panel, X, y, {"현 설정": None, "튜닝": {"learning_rate": 0.1}})
    assert set(sc["params"]) == {"현 설정", "튜닝"}
    assert {"auc_last_origin", "ap_last_origin", "n_last_origin"} <= set(sc.columns)
    ts4 = sc[(sc["setting"] == "time_split(embargo=4)") & (sc["params"] == "현 설정")].iloc[0]
    assert ts4["test"] == ORIGINS[-1] and ts4["auc"] == pytest.approx(ts4["auc_last_origin"])  # test가 이미 최신 origin
    rnd = sc[sc["setting"].str.startswith("random_split")].iloc[0]
    assert rnd["test_scope"] == "전체 origin 무작위 ~20% 표본"
    assert rnd["n_last_origin"] < rnd["n_train"]  # 최신 origin 부분만 추린 쪽이 더 작다


def test_suggest_cutoffs_records_fallbacks_without_changing_default_behavior():
    rng = np.random.default_rng(0)
    p = rng.beta(1, 8, 20000)
    y = (rng.random(20000) < p).astype(int)  # 잘 보정된 예측 — 꼬리에 high(2배)가 존재
    c = bands.suggest_cutoffs(y, p)
    assert c["base_rate"] == pytest.approx(y.mean()) and c["high_fallback"] is False
    assert c["cut_mid"] == pytest.approx(y.mean() if c["mid_fallback"] else 1.2 * y.mean())
    assert c["mid_fallback"] == (1.2 * y.mean() >= c["cut_high"])  # mid 대체는 cut_mid ≥ cut_high일 때만
    # 예측이 신호가 없으면(라벨과 무관) 2배 집단이 없다 → p95 fallback
    y0 = (rng.random(20000) < 0.12).astype(int)
    c0 = bands.suggest_cutoffs(y0, p)
    assert c0["high_fallback"] is True and c0["cut_high"] == pytest.approx(float(np.quantile(p, 0.95)))
    # 정의 문장은 코드와 같다
    assert "1.2 × base_rate" in bands.CUT_MID_DEFINITION and "fallback" in bands.CUT_HIGH_DEFINITION


def test_high_lift_cluster_ci_brackets_point_estimate():
    rng = np.random.default_rng(1)
    n = 3000
    stores = np.repeat(np.arange(1000), 3)
    is_high = rng.random(n) < 0.1
    y = (rng.random(n) < np.where(is_high, 0.3, 0.1)).astype(int)
    lift, lo, hi = bands.high_lift_cluster_ci(y, is_high, stores, n_boot=300)
    assert lo < lift < hi and lift == pytest.approx(y[is_high].mean() / y.mean())
