# -*- coding: utf-8 -*-
"""#33 리뷰 — 절단 결측 누수 민감도(i/ii/iii + NA 플래그) 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import online_truncation_sensitivity as ots
from src.data.online_features import FEATURES


def _online_table(n=6):
    """FEATURES 6개 컬럼을 가진 최소 테이블. 절반은 전부 NA(절단·미관측 흉내)."""
    rows = {"store_id": [f"S{i}" for i in range(n)], "origin": ["2024Q1"] * n}
    for c in FEATURES:
        rows[c] = [np.nan if i % 2 == 0 else 1.0 for i in range(n)]
    return pd.DataFrame(rows)


def test_na_flag_column_marks_any_na_row():
    t = _online_table()
    flag = ots.na_flag_column(t)
    assert list(flag) == [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]


def test_truncation_na_flag_marks_only_truncated_store_na():
    """절단 전용 플래그: NA이면서 절단 점포인 행만 1 — 언급 없음·미수집 NA는 0."""
    t = _online_table()  # S0·S2·S4가 NA
    flag = ots.truncation_na_flag(t, {"S0", "S1"})
    assert list(flag) == [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # S1은 절단 점포지만 NA가 없다, S2·S4는 절단 점포가 아니다


def test_any_na_composition_separates_never_mentioned_from_truncation():
    from src.data import online_features as of
    stores = ["N", "T", "Q"]  # N: 언급 없음, T: 절단 점포, Q: QA 미수집
    panel = pd.DataFrame({"store_id": stores, "origin": "2024Q1", "origin_end": pd.Timestamp("2024-03-31")})
    monthly = pd.DataFrame({"store_id": ["T"], "month": [pd.Period("2024-02", "M")], "mention_count": [5]})
    qa = pd.DataFrame({"store_id": ["N", "T"], "ok": [True, True], "truncated": [False, True],
                       "trunc_month": [pd.NaT, pd.Period("2023-12", "M")]})
    t = of.build_online_features(panel, monthly, qa)
    comp = ots.any_na_composition(t, {"T"})
    assert comp["any_na_rows"] == 3
    assert (comp["never_mentioned"], comp["truncated_store"], comp["all_na_qa_missing_or_error"]) == (1, 1, 1)
    assert list(ots.truncation_na_flag(t, {"T"})) == [0.0, 1.0, 0.0]  # 옛 any-NA 플래그라면 셋 다 1


def test_truncated_store_ids_needs_ok_and_truncated():
    qa = pd.DataFrame({"store_id": ["A", "B", "C"], "ok": [True, True, False], "truncated": [True, False, True]})
    assert ots.truncated_store_ids(qa) == {"A"}


def test_exclude_stores_drops_only_matching_rows():
    df = pd.DataFrame({"store_id": ["A", "B", "A", "B"]})
    oof = pd.DataFrame({"idx": [0, 1, 2, 3], "y": [1, 0, 1, 0], "p_oof": [0.1, 0.2, 0.3, 0.4], "origin": "2024Q1"})
    out = ots.exclude_stores(oof, df, {"A"})
    assert set(out["idx"]) == {1, 3}


def test_align_matches_by_idx_and_rejects_mismatch():
    a = pd.DataFrame({"idx": [0, 1, 2], "y": [1, 0, 1], "p_oof": [0.2, 0.3, 0.9]})
    b = pd.DataFrame({"idx": [0, 1, 2], "p_oof": [0.5, 0.5, 0.5]})
    y, pa, pb, idx = ots._align(a, b)
    assert list(y) == [1, 0, 1] and list(pa) == [0.2, 0.3, 0.9] and list(pb) == [0.5, 0.5, 0.5]
    with pytest.raises(ValueError):
        ots._align(a, b.iloc[:2])


def _synthetic_oof(seed, n_per_origin=400, gap=0.0):
    """origin 3개, store 단위 신호가 있는 합성 OOF (y, p_oof) — gap을 더하면 p_a가 더 잘 맞는다."""
    rng = np.random.default_rng(seed)
    rows = []
    idx = 0
    for o in ("2023Q1", "2023Q2", "2023Q3"):
        signal = rng.normal(size=n_per_origin)
        y = (signal + rng.normal(scale=1.5, size=n_per_origin) > 0).astype(int)
        p = 1 / (1 + np.exp(-signal))
        rows.append(pd.DataFrame({"idx": np.arange(idx, idx + n_per_origin), "origin": o, "y": y, "p_oof": p}))
        idx += n_per_origin
    return pd.concat(rows, ignore_index=True)


def test_bootstrap_auc_diff_detects_real_gap_and_null():
    oof = _synthetic_oof(1)
    y, p = oof["y"].to_numpy(), oof["p_oof"].to_numpy()
    stores = np.array([f"S{i}" for i in range(len(oof))])
    better = 1 / (1 + np.exp(-6 * (p - 0.5)))  # 같은 순위, 더 뚜렷한 확률 → AUC는 순위만 보므로 거의 동일
    null = ots.bootstrap_auc_diff(y, p, p, stores, n_boot=200)
    assert not null["significant"]
    assert null["ci_low"] <= 0 <= null["ci_high"]

    noisy = np.clip(p + np.random.default_rng(2).normal(scale=2.0, size=len(p)), 0, 1)
    real = ots.bootstrap_auc_diff(y, p, noisy, stores, n_boot=200)
    assert real["diff"] > 0
    assert real["significant"]


def test_pooled_auc_matches_sklearn():
    from sklearn.metrics import roc_auc_score

    oof = _synthetic_oof(3)
    assert ots.pooled_auc(oof) == pytest.approx(roc_auc_score(oof["y"], oof["p_oof"]))


def test_sensitivity_report_end_to_end_synthetic():
    """실제 rolling_oof를 작은 합성 데이터로 한 번 돌려 키·정합성만 확인한다(성능 수치 자체는 실데이터에서 본다)."""
    from src.data import online_features as of

    origins = [str(p) for p in pd.period_range("2021Q1", "2023Q4", freq="Q")]
    n_store = 60
    rng = np.random.default_rng(7)
    stores = [f"S{i}" for i in range(n_store)]
    rows = []
    for o in origins:
        end = pd.Period(o, "Q").end_time.normalize()
        age = rng.integers(1, 200, n_store)
        y = (rng.random(n_store) < 0.1).astype(int)
        rows.append(pd.DataFrame({"store_id": stores, "origin": o, "origin_end": end,
                                  "event_12m": y, "age_months": age, "biz_type": "A", "area": 30.0,
                                  "has_coord": 1, "gu": "G1"}))
    df = pd.concat(rows, ignore_index=True)

    monthly = pd.DataFrame({"store_id": [s for s in stores for _ in range(3)],
                            "year_month": ["2022-01", "2022-06", "2023-01"] * n_store,
                            "mention_count": rng.integers(0, 3, n_store * 3)})
    monthly["month"] = pd.PeriodIndex(monthly["year_month"], freq="M")
    trunc_flags = [i % 5 == 0 for i in range(n_store)]  # 20%는 절단 점포
    qa = pd.DataFrame({"store_id": stores, "ok": True, "truncated": trunc_flags,
                       "trunc_month": [pd.Period("2022-01", "M") if t else pd.NaT for t in trunc_flags]})

    panel = df[["store_id", "origin", "origin_end"]].drop_duplicates()
    online_na = of.build_online_features(panel, monthly, qa, "test#na", "na")
    online_lb = of.build_online_features(panel, monthly, qa, "test#lb", "lower_bound")

    trunc_stores = ots.truncated_store_ids(qa)
    variants = ots.build_feature_variants(df, online_na, online_lb, trunc_stores)
    assert set(variants) == {"base", "flag", "i", "iii"}
    assert ots.TRUNC_FLAG in variants["flag"][1] and "online_any_na" not in variants["flag"][1]
    d_flag = variants["flag"][0]
    assert (d_flag.loc[~d_flag["store_id"].isin(trunc_stores), ots.TRUNC_FLAG] == 0).all()  # 비절단 점포는 항상 0

    y = df["event_12m"].to_numpy()
    r = ots.sensitivity_report(variants, y, trunc_stores, n_boot=20)

    for k in ots.SCENARIOS:
        assert k in r["mean_auc"] and k in r["pooled_auc"]
    assert len(r["oof"]["ii"]) < len(r["oof"]["i"])  # 절단 점포가 빠져 행 수가 줄어야 한다
    # clean: 절단 점포가 학습·평가 어디에도 없다 (평가 행에 없고, 행 수가 ii와 같다)
    clean_stores = set(df["store_id"].to_numpy()[r["oof"]["i_clean"]["idx"].to_numpy()])
    assert not clean_stores & trunc_stores and len(r["oof"]["i_clean"]) == len(r["oof"]["ii"])
    assert set(r["by_origin"]["scenario"]) == set(ots.SCENARIOS)
    for key in ("i_vs_iii", "flag_vs_base", "i_vs_base", "clean_i_vs_base"):
        assert {"diff", "ci_low", "ci_high", "significant"} <= set(r["ci"][key])


def test_clean_scenario_trains_without_truncated_stores(monkeypatch):
    """clean 시나리오의 학습 행에 절단 점포가 한 번도 들어가지 않는다 (평가만 빼는 ii와 다르다)."""
    from src.models import train_detect
    seen = []
    real = train_detect.rolling_oof

    def spy(d, X, y, params=None):
        seen.append(set(d["store_id"]))
        return real(d, X, y, params=params)

    monkeypatch.setattr(train_detect, "rolling_oof", spy)
    origins = [str(p) for p in pd.period_range("2021Q1", "2023Q4", freq="Q")]
    stores = [f"S{i}" for i in range(40)]
    rng = np.random.default_rng(3)
    df = pd.concat([pd.DataFrame({"store_id": stores, "origin": o, "origin_end": pd.Period(o, "Q").end_time.normalize(),
                                  "event_12m": (rng.random(40) < 0.15).astype(int), "age_months": rng.integers(1, 200, 40),
                                  "biz_type": "A", "area": 30.0, "has_coord": 1, "gu": "G1"}) for o in origins],
                   ignore_index=True)
    on = df[["store_id", "origin"]].copy()
    for c in FEATURES:
        on[c] = rng.integers(0, 3, len(on)).astype(float)
    trunc = {"S0", "S1", "S2"}
    variants = ots.build_feature_variants(df, on, on, trunc)
    ots.sensitivity_report(variants, df["event_12m"].to_numpy(), trunc, n_boot=10)
    assert len(seen) == 6 and all(not (s & trunc) for s in seen[-2:]) and all(s & trunc for s in seen[:4])
