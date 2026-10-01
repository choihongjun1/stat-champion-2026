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


def test_cluster_bootstrap_weights_equal_row_replication():
    rng = np.random.default_rng(6)
    stores = np.repeat([f"S{i}" for i in range(300)], 3)  # 점포당 origin 3행
    y = (rng.random(len(stores)) < 0.2).astype(int)
    pa, pb = y + rng.normal(0, 1, len(y)), y + rng.normal(0, 2, len(y))
    r = bm.cluster_bootstrap_diff(y, pa, pb, stores, n_boot=1, seed=11)
    # 같은 seed로 점포를 뽑아 행을 실제로 복제한 값과 같아야 한다 (구간 폭 0인 1회 재표본)
    codes, uniq = pd.factorize(pd.Series(stores))
    draw = np.random.default_rng(11).integers(0, len(uniq), len(uniq))
    rows = np.concatenate([np.flatnonzero(codes == s) for s in draw])
    want = roc_auc_score(y[rows], pa[rows]) - roc_auc_score(y[rows], pb[rows])
    assert r["auc_ci_low"] == pytest.approx(want) and r["auc_ci_high"] == pytest.approx(want)
    assert r["n_clusters"] == 300 and r["n_rows"] == 900
    same = bm.cluster_bootstrap_diff(y, pa, pa, stores, n_boot=20)
    assert same["auc_diff"] == 0 and same["ap_diff"] == 0 and not same["auc_significant"]


def test_pre_adoption_end_to_end(tmp_path, panel):
    master = tmp_path / "master.parquet"
    panel.to_parquet(master, index=False)
    online = panel[["store_id", "origin"]].copy()
    rng = np.random.default_rng(7)
    for c in bm.ONLINE_COLS:
        online[c] = rng.poisson(1, len(online)).astype(float)
    # 등급 규칙이 순서 맞는 컷오프를 낼 만큼 신호를 넣는다 (합성 패널 기본 신호는 약하다)
    online["online_blog_cnt_12m"] += 4 * panel["event_12m"].to_numpy() * (rng.random(len(online)) < 0.6)
    online.to_parquet(tmp_path / "online.parquet", index=False)
    cuts = tmp_path / "band_cutoffs.csv"
    pd.DataFrame([{"cut_mid": 0.12, "cut_high": 0.2, "base_rate": 0.1}]).to_csv(cuts, index=False)
    r = bm.pre_adoption(master, tmp_path / "online.parquet", tmp_path / "out", n_boot=10, current_cutoffs=cuts)
    b = r["bootstrap"]
    assert list(zip(b["model_a"], b["model_b"])) == [("hgb_enriched_tuned", "logit_enriched"),
                                                     ("hgb_enriched_tuned", "hgb_enriched")]
    assert (b["n_rows"] == 200 * 10).all() and (b["n_clusters"] == 200).all()  # 10개 origin 합산, 점포 = 묶음
    c = r["cutoffs"].set_index("model")
    assert set(c["calib_origins"]) == {"2023Q1~2023Q4"} and "reproduces_current" in c.columns
    # 합성 패널에서는 기존 규칙이 순서 맞는 컷오프를 못 낼 수 있다 — 그 경우 멈추지 않고 표시만 하고 등급 표는 비운다
    ok = c.index[~c["cutoff_rule_failed"]]
    assert (c.loc[ok, "cut_mid"] < c.loc[ok, "cut_high"]).all()
    assert (c.loc[c["cutoff_rule_failed"], "cut_mid"] >= c.loc[c["cutoff_rule_failed"], "cut_high"]).all()
    p = r["profile"]
    assert set(p.get("model", pd.Series(dtype=str))) == set(ok)
    if len(p):
        assert set(p["test_origins"]) == {"2025Q1~2025Q2", "2025Q1", "2025Q2"}
        assert np.allclose(p.groupby(["model", "test_origins"])["share"].sum(), 1)
    for f in ("pre_adoption_cluster_bootstrap.csv", "pre_adoption_cutoffs.csv", "pre_adoption_band_profile.csv",
              "pre_adoption_meta.json"):
        assert (tmp_path / "out" / f).exists()


def test_band_rule_values_on_clear_signal():
    """예측이 잘 갈리는 경우 기존 규칙으로 순서 맞는 컷오프·등급 비율이 나온다."""
    rng = np.random.default_rng(8)
    rows = []
    for o in ORIGINS[8:]:
        p = rng.uniform(0.02, 0.45, 3000)
        rows.append(pd.DataFrame({"origin": o, "idx": np.arange(3000), "p_oof": p,
                                  "y": (rng.random(3000) < p).astype(int)}))
    oof = pd.concat(rows, ignore_index=True)
    info, prof = bm.band_rule_values(oof, ORIGINS)
    assert not info["cutoff_rule_failed"] and 0 < info["cut_mid"] < info["cut_high"] < 1
    assert info["calib_origins"] == "2023Q1~2023Q4"
    both = prof[prof["test_origins"] == "2025Q1~2025Q2"].set_index("band")
    assert both["share"].sum() == pytest.approx(1) and both.at["high", "lift"] >= 1.8


def test_cutoff_windows_exclude_test_origins_and_flag_maturity():
    for name, (s, e) in bm.CUTOFF_WINDOWS.items():
        w = bm.window_origins(ORIGINS, s, e)
        assert not set(w) & set(bm.BAND_TEST_ORIGINS) and w[0] == s and w[-1] == e
    with pytest.raises(ValueError):
        bm.window_origins(ORIGINS, "2024Q1", "2025Q1")
    w23, w24 = bm.window_origins(ORIGINS, "2023Q1", "2023Q4"), bm.window_origins(ORIGINS, "2024Q1", "2024Q4")
    assert bm.labels_matured_by(w23, ORIGINS, "2025Q1") and bm.labels_matured_by(w23, ORIGINS, "2025Q2")
    assert not bm.labels_matured_by(w24, ORIGINS, "2025Q1") and not bm.labels_matured_by(w24, ORIGINS, "2025Q2")


def test_high_lift_cluster_ci_brackets_point_estimate():
    rng = np.random.default_rng(9)
    stores = np.repeat([f"S{i}" for i in range(2000)], 2)
    p = rng.uniform(0.02, 0.45, len(stores))
    y = (rng.random(len(p)) < p).astype(int)
    high = p >= 0.35
    lift = y[high].mean() / y.mean()
    lo, hi = bm.high_lift_cluster_ci(y, high, stores, n_boot=200)
    assert lo < lift < hi and hi - lo < 0.5


def test_cutoff_window_rows():
    rng = np.random.default_rng(10)
    rows = []
    for o in ORIGINS[8:]:
        p = rng.uniform(0.02, 0.45, 2000)
        rows.append(pd.DataFrame({"origin": o, "idx": np.arange(2000), "p_oof": p, "y": (rng.random(2000) < p).astype(int)}))
    oof = pd.concat(rows, ignore_index=True)
    stores = np.array([f"S{i}" for i in oof["idx"]])
    t = bm.cutoff_window_rows({"m": oof}, stores, ORIGINS, n_boot=50)
    assert list(t["calib_window"]) == list(bm.CUTOFF_WINDOWS) and not t["cutoff_rule_failed"].any()
    assert list(t["n_calib_origins"]) == [4, 4, 8]
    assert list(t["labels_matured_at_2025Q1"]) == [True, False, False]
    assert np.allclose(t[["share_low", "share_mid", "share_high"]].sum(axis=1), 1)
    assert (t["high_lift_ci_low"] <= t["lift_high"]).all() and (t["lift_high"] <= t["high_lift_ci_high"]).all()


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


def test_hpo_selection_rows_never_include_validation_origin_or_later(panel, monkeypatch):
    """#45 근거: 하이퍼파라미터 선택(격자 학습·검증)에 들어가는 행의 origin은 각 평가 origin t의 학습 구간(t−5 이하) 안이다.
    t 이후(검증 origin 2023Q1–2025Q2 포함) 라벨은 선택에 쓰이지 않는다 — fit_predict에 들어간 행 origin 최댓값을 검사한다."""
    origins = sorted(panel["origin"].unique())
    calls = []
    real = bm.detect.fit_predict

    def spy(X_tr, y_tr, X_te, params=None):
        calls.append((panel.loc[X_tr.index, "origin"], panel.loc[X_te.index, "origin"]))
        return real(X_tr, y_tr, X_te, params)

    monkeypatch.setattr(bm.detect, "fit_predict", spy)
    monkeypatch.setattr(bm, "HPO_GRID", {"learning_rate": (0.06,), "max_leaf_nodes": (15, 31)})
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    bm.hpo(panel, X, panel["event_12m"].to_numpy(), cols)
    n_grid = 2
    tests = origins[bm.MIN_TRAIN_ORIGINS + bm.EMBARGO:]
    assert len(calls) == len(tests) * (n_grid + 1)  # origin마다 격자 n_grid번 + 선택 후 재학습 1번
    for k, test_o in enumerate(tests):
        last_train = origins[origins.index(test_o) - bm.EMBARGO - 1]
        for tr_o, va_o in calls[k * (n_grid + 1): k * (n_grid + 1) + n_grid]:  # 선택 단계
            assert tr_o.max() <= last_train and va_o.max() <= last_train and va_o.max() < test_o
        final_tr, final_te = calls[k * (n_grid + 1) + n_grid]  # 재학습(선택 이후) — 학습도 t−5 이하, 예측만 t
        assert final_tr.max() <= last_train and set(final_te) == {test_o}


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


# ---------------------------------------------------------------------------- 짧은 상호 처리 (#28)
@pytest.mark.parametrize("raw, norm", [("요즘", "요즘"), ("카페 오늘", "카페오늘"), ("BHC-치킨!", "bhc치킨"),
                                       ("밥&술", "밥술"), ("커피２", "커피"), ("ㅋㅋ", ""), (None, "")])
def test_match_name_norm_pinned_to_pr21(raw, norm):
    assert bm.match_name_norm(raw) == norm


def _licenses_for(panel, tmp_path):
    ids = sorted(panel["store_id"].unique())
    names = ["밥" if i % 4 == 0 else f"가게이름{i}" for i in range(len(ids))]  # 4곳 중 1곳 짧은 상호
    p = tmp_path / "lic.parquet"
    pd.DataFrame({"store_id": ids, "name_raw": names}).to_parquet(p, index=False)
    return p, {s for s, n in zip(ids, names) if len(bm.match_name_norm(n)) <= 2}


def test_short_name_policy_na_masks_only_short_store_online_columns(tmp_path, panel):
    lic, short = _licenses_for(panel, tmp_path)
    assert bm.short_name_store_ids(lic) == short
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    for c in bm.ONLINE_COLS:
        X[c] = 1.0
    Xn = bm.apply_short_name_policy(panel, X, "na", short)
    m = panel["store_id"].isin(short).to_numpy()
    assert Xn.loc[m, bm.ONLINE_COLS].isna().all().all()                 # 짧은 상호: 전 origin NA
    assert (Xn.loc[~m, bm.ONLINE_COLS] == 1.0).all().all()               # 나머지 그대로
    pd.testing.assert_frame_equal(Xn[cols], X[cols])                      # base 열은 그대로
    assert not any(c.endswith("_isna") for c in Xn.columns)             # 결측 지시자 없음
    pd.testing.assert_frame_equal(bm.apply_short_name_policy(panel, X, "keep", short), X)
    with pytest.raises(ValueError):
        bm.apply_short_name_policy(panel, X, "strict", short)


def test_short_name_sensitivity_end_to_end(tmp_path, panel):
    master = tmp_path / "master.parquet"
    panel.to_parquet(master, index=False)
    online = panel[["store_id", "origin"]].copy()
    rng = np.random.default_rng(12)
    for c in bm.ONLINE_COLS:
        online[c] = rng.poisson(1, len(online)).astype(float)
    online.to_parquet(tmp_path / "online.parquet", index=False)
    lic, short = _licenses_for(panel, tmp_path)
    r = bm.short_name_sensitivity(master, tmp_path / "online.parquet", tmp_path / "out", n_boot=10, licenses_path=lic)
    s = r["summary"].set_index("model")
    assert set(s.index) == {"hgb_base_tuned", "logit_base", "hgb_enriched_tuned_keep", "hgb_enriched_tuned_na",
                            "logit_enriched_keep", "logit_enriched_na"}
    assert (s["all_n"] == s["short_n"] + s["long_n"]).all()
    assert s.loc["hgb_enriched_tuned_na", "gain_vs_base_auc_mean"] == pytest.approx(
        s.at["hgb_enriched_tuned_na", "auc_mean"] - s.at["hgb_base_tuned", "auc_mean"])
    assert len(r["bootstrap"]) == 6 and (r["bootstrap"]["n_clusters"] == panel["store_id"].nunique()).all()
    for f in ("short_name_policy_summary.csv", "short_name_policy_bootstrap.csv", "short_name_policy_meta.json"):
        assert (tmp_path / "out" / f).exists()


# ---------------------------------------------------------------------------- PR #43 경쟁지표 추가 효과
def _comp_table(panel, drop_last=5, seed=3):
    rng = np.random.default_rng(seed)
    t = panel[["store_id", "origin"]].copy()
    for c in bm.COMP_COLS[:5]:
        t[c] = rng.poisson(3, len(t)).astype(float)
    t["comp_dong_density_yoy"] = rng.normal(0, 0.1, len(t))
    t["comp_location_status"] = "ok"  # 메타 열은 붙이지 않는다
    return t.iloc[:-drop_last]  # 패널 일부 키가 표에 없다 → NA


def test_attach_extra_features_left_join_reports_mismatch(tmp_path, panel):
    p = tmp_path / "comp.parquet"
    _comp_table(panel).to_parquet(p, index=False)
    X = features.build_X(panel, features.select_features(panel.columns, "base"))
    X2, cols, rep = bm.attach_extra_features(panel, X, p)
    assert cols == bm.COMP_COLS and "comp_location_status" not in X2.columns and len(X2) == len(panel)
    assert rep["panel_rows_without_extra"] == 5 and rep["extra_rows_not_in_panel"] == 0
    assert X2[bm.COMP_COLS].isna().all(axis=1).sum() == 5
    dup = pd.concat([_comp_table(panel), _comp_table(panel).head(1)])
    dup.to_parquet(tmp_path / "dup.parquet", index=False)
    with pytest.raises(ValueError, match="중복"):
        bm.attach_extra_features(panel, X, tmp_path / "dup.parquet")


def test_nested_tuned_oof_uses_only_training_window(panel, monkeypatch):
    calls = []
    real = bm.detect.fit_predict

    def spy(X_tr, y_tr, X_te, params=None):
        calls.append((panel.loc[X_tr.index, "origin"].max(), set(panel.loc[X_te.index, "origin"])))
        return real(X_tr, y_tr, X_te, params)

    monkeypatch.setattr(bm.detect, "fit_predict", spy)
    monkeypatch.setattr(bm, "HPO_GRID", {"learning_rate": (0.06,), "max_leaf_nodes": (15, 31)})
    cols = features.select_features(panel.columns, "base")
    oof, chosen = bm.nested_tuned_oof(panel, features.build_X(panel, cols), panel["event_12m"].to_numpy(), cols)
    origins = sorted(panel["origin"].unique())
    assert sorted(oof["origin"].unique()) == origins[bm.MIN_TRAIN_ORIGINS + bm.EMBARGO:] and len(chosen) == 10
    for tr_max, te in calls:
        t = max(te)
        assert tr_max <= origins[origins.index(t) - bm.EMBARGO - 1] or (len(te) == 1 and tr_max < t)


def test_competition_decision_rule():
    summ = pd.DataFrame([{"model": "hgb_tuned_nested", "policy": p, "features": f, "ece_mean": e, "calib_slope_pooled": s}
                         for p in ("keep", "na") for f, e, s in (("enriched", 0.02, 0.9), ("enriched+comp", 0.021, 0.92))])
    boot = pd.DataFrame([{"model": "hgb_tuned_nested", "policy": "keep", "auc_diff": 0.004, "auc_ci_low": 0.001, "auc_ci_high": 0.007},
                         {"model": "hgb_tuned_nested", "policy": "na", "auc_diff": 0.003, "auc_ci_low": 0.0005, "auc_ci_high": 0.006}])
    d = bm.competition_decision(summ, boot)
    assert d["meets_rule"].all() and (d["verdict"] == "채택 후보").all()
    boot.loc[1, "auc_ci_low"] = -0.001  # na에서 CI가 0을 포함 → 전체 미채택
    assert (bm.competition_decision(summ, boot)["verdict"] == "미채택").all()
    summ.loc[(summ["policy"] == "keep") & (summ["features"] == "enriched+comp"), "ece_mean"] = 0.03  # ECE 악화
    boot.loc[1, "auc_ci_low"] = 0.001
    assert not bm.competition_decision(summ, boot).set_index("policy").at["keep", "meets_rule"]


def test_competition_eval_end_to_end_and_checkpoint(tmp_path, panel, monkeypatch):
    monkeypatch.setattr(bm, "HPO_GRID", {"learning_rate": (0.06,), "max_leaf_nodes": (15, 31)})
    master = tmp_path / "master.parquet"
    panel.to_parquet(master, index=False)
    online = panel[["store_id", "origin"]].copy()
    rng = np.random.default_rng(12)
    for c in bm.ONLINE_COLS:
        online[c] = rng.poisson(1, len(online)).astype(float)
    online.to_parquet(tmp_path / "online.parquet", index=False)
    _comp_table(panel).to_parquet(tmp_path / "comp.parquet", index=False)
    lic, _ = _licenses_for(panel, tmp_path)
    out = tmp_path / "out"
    r = bm.competition_eval(master, tmp_path / "online.parquet", tmp_path / "comp.parquet", out, n_boot=10, licenses_path=lic)
    assert len(r["summary"]) == 8 and len(r["bootstrap"]) == 4 and set(r["decision"]["policy"]) == {"keep", "na"}
    for f in ("competition_summary.csv", "competition_bootstrap.csv", "competition_decision.csv", "competition_subgroup.csv",
              "competition_permutation.csv", "competition_trdar_corr.csv", "competition_hpo_chosen.csv", "competition_meta.json"):
        assert (out / f).exists(), f
    for f in out.glob("competition_*.csv"):
        assert "store_id" not in pd.read_csv(f, nrows=0).columns
    perm = pd.read_csv(out / "competition_permutation.csv")
    assert set(perm["feature"]) == set(bm.COMP_COLS) | {"[group] comp"}
    # 다시 실행하면 OOF 체크포인트를 쓴다 (학습 호출 없음)
    monkeypatch.setattr(bm, "nested_tuned_oof", lambda *a, **k: (_ for _ in ()).throw(AssertionError("재학습")))
    r2 = bm.competition_eval(master, tmp_path / "online.parquet", tmp_path / "comp.parquet", out, n_boot=10, licenses_path=lic)
    pd.testing.assert_frame_equal(r["summary"], r2["summary"])


def test_lift_curve_crossings_counts_each_upward_crossing():
    # p 구간별 실측률을 정해 누적 lift가 2를 넘었다가 내려가고 다시 넘게 만든다
    p = np.repeat(np.linspace(0.01, 0.99, 1000), 10)
    rate = np.where(p < 0.6, 0.05, np.where(p < 0.75, 0.9, np.where(p < 0.85, 0.0, 0.95)))
    y = (np.random.default_rng(0).random(len(p)) < rate).astype(float)
    r = bm.lift_curve_crossings(y, p, min_n=50)
    assert r["n_up_crossings"] >= 2 and r["first_cut_at_target"] < r["last_cut_at_target"]
    mono = bm.lift_curve_crossings((p > 0.8).astype(float), p, min_n=50)  # 단조 → 교차 1개
    assert mono["n_up_crossings"] == 1
    assert mono["first_cut_at_target"] == pytest.approx(bm.bands.suggest_cutoffs((p > 0.8).astype(float), p)["cut_high"])


def test_serving_window_hpo_uses_serving_train_window_with_embargo(tmp_path, panel, monkeypatch):
    monkeypatch.setattr(bm, "HPO_GRID", {"learning_rate": (0.06,), "max_leaf_nodes": (15, 31)})
    master = tmp_path / "master.parquet"
    panel.to_parquet(master, index=False)
    online = panel[["store_id", "origin"]].copy()
    for c in bm.ONLINE_COLS:
        online[c] = 1.0
    online.to_parquet(tmp_path / "online.parquet", index=False)
    origins = sorted(panel["origin"].unique())
    score = str(pd.Period(origins[-1], freq="Q") + 1)  # 라벨 없는 다음 분기를 서빙한다고 가정
    t = bm.serving_window_hpo(master, tmp_path / "online.parquet", tmp_path, score)
    last = str(pd.Period(score, freq="Q") - (bm.EMBARGO + 1))
    assert (t["serving_train"] == f"{origins[0]}~{last}").all() and (t["inner_val"] == last).all()
    in_end = t["inner_train"].iloc[0].split("~")[1]
    assert pd.Period(in_end, freq="Q") <= pd.Period(last, freq="Q") - (bm.EMBARGO + 1)
    assert list(t["rank"]) == [1, 2] and t["inner_auc"].is_monotonic_decreasing


def test_dynamic_window_uses_only_matured_recent_origins(panel):
    origins = sorted(panel["origin"].unique())
    rng = np.random.default_rng(3)
    oof = pd.DataFrame({"origin": panel["origin"].to_numpy(), "y": panel["event_12m"].to_numpy(),
                        "p_oof": rng.random(len(panel))})
    r = bm.dynamic_window_high(oof, origins)
    for t, part in zip(bm.BAND_TEST_ORIGINS, r["dyn_windows"].split("; ")):
        end = origins[origins.index(t) - bm.EMBARGO - 1]
        start = origins[origins.index(t) - bm.EMBARGO - bm.DYNAMIC_WINDOW_SIZE]
        assert part.startswith(f"{t}: {start}~{end}")


def test_nested_recheck_end_to_end_and_checkpoint(tmp_path, panel, monkeypatch):
    monkeypatch.setattr(bm, "HPO_GRID", {"learning_rate": (0.06,), "max_leaf_nodes": (15, 31)})
    master = tmp_path / "master.parquet"
    panel.to_parquet(master, index=False)
    online = panel[["store_id", "origin"]].copy()
    rng = np.random.default_rng(12)
    for c in bm.ONLINE_COLS:
        online[c] = rng.poisson(1, len(online)).astype(float)
    online.to_parquet(tmp_path / "online.parquet", index=False)
    out = tmp_path / "out"
    r = bm.nested_recheck(master, tmp_path / "online.parquet", out, n_boot=10)
    n_pairs = len(bm.NESTED_PAIRS)
    assert list(r["summary"]["model"]) == list(bm.NESTED_MODELS)
    assert len(r["bootstrap"]) in (n_pairs, n_pairs + len(bm.NESTED_SUBSET_PAIRS))
    assert set(r["bands"]["model"]) == {"a_hgb_enriched", "b_tuned_fixed", "c_tuned_nested", "e_serving_fixed"}
    assert r["bands"]["dyn_high_share_test"].between(0, 1).all() and len(r["chosen"]) == 10
    for f in out.glob("nested_recheck_*.csv"):
        assert "store_id" not in pd.read_csv(f, nrows=0).columns
    monkeypatch.setattr(bm, "nested_tuned_oof", lambda *a, **k: (_ for _ in ()).throw(AssertionError("재학습")))
    r2 = bm.nested_recheck(master, tmp_path / "online.parquet", out, n_boot=10)
    pd.testing.assert_frame_equal(r["summary"], r2["summary"])
