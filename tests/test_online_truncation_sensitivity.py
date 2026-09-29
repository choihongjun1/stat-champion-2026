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

    variants = ots.build_feature_variants(df, online_na, online_lb)
    assert set(variants) == {"base", "flag", "i", "iii"}
    assert "online_any_na" in variants["flag"][1]

    y = df["event_12m"].to_numpy()
    trunc_stores = ots.truncated_store_ids(qa)
    r = ots.sensitivity_report(variants, y, trunc_stores, n_boot=20)

    for k in ("base", "base_ex_trunc", "flag", "i", "ii", "iii"):
        assert k in r["mean_auc"] and k in r["pooled_auc"]
    assert len(r["oof"]["ii"]) < len(r["oof"]["i"])  # 절단 점포가 빠져 행 수가 줄어야 한다
    for key in ("ci_i_vs_iii", "ci_flag_vs_base"):
        assert {"diff", "ci_low", "ci_high", "significant"} <= set(r[key])
