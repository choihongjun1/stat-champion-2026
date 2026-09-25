# -*- coding: utf-8 -*-
"""온라인 feature 집계(#25 결측 규칙)와 #26 삭제 편향 진단 테스트 (합성 데이터)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import online_deletion_bias as odb
from src.data import online_features as of

ORIGINS = [str(p) for p in pd.period_range("2021Q1", "2025Q2", freq="Q")]


def panel_for(stores):
    rows = [(s, o, pd.Period(o, "Q").end_time.normalize()) for s in stores for o in ORIGINS]
    return pd.DataFrame(rows, columns=["store_id", "origin", "origin_end"])


def _prep(m):
    m = m.copy()
    m["month"] = pd.PeriodIndex(m["year_month"], freq="M")
    return m


def qa(rows):
    """(store_id, ok, truncated, 절단 시작 월 'YYYY-MM' 또는 None) → load_qa와 같은 형태."""
    q = pd.DataFrame(rows, columns=["store_id", "ok", "truncated", "trunc"])
    q["trunc_month"] = [pd.Period(t, "M") if isinstance(t, str) else pd.NaT for t in q["trunc"]]
    return q.drop(columns="trunc")


def get(t, store, origin, col):
    return t.loc[(t["store_id"] == store) & (t["origin"] == origin), col].iloc[0]


def test_counts_windows_and_zero_fill():
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2022-01", 2), ("A", "2022-03", 1), ("A", "2021-06", 4)],
                           columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("A", True, False, None)]))
    assert get(t, "A", "2022Q1", "online_blog_cnt_3m") == 3          # 2022-01~03
    assert get(t, "A", "2022Q1", "online_blog_cnt_12m") == 7         # 2021-04~2022-03
    assert get(t, "A", "2022Q1", "online_blog_trend_6m") == 3 - 4    # 최근 6개월 − 앞 6개월
    assert get(t, "A", "2022Q1", "online_blog_months_since_last") == 0
    assert get(t, "A", "2023Q1", "online_blog_cnt_12m") == 0         # 행이 없는 달은 0
    assert get(t, "A", "2023Q1", "online_blog_has_ever") == 1
    assert get(t, "A", "2023Q1", "online_blog_months_since_last") == 12
    assert get(t, "A", "2021Q1", "online_blog_has_ever") == 0
    assert np.isnan(get(t, "A", "2021Q1", "online_blog_months_since_last"))


def test_future_months_never_used():
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2021-04", 5)], columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("A", True, False, None)]))
    assert get(t, "A", "2021Q1", "online_blog_cnt_12m") == 0  # 2021-04 글은 2021Q1(~03-31)에 안 보인다
    assert get(t, "A", "2021Q2", "online_blog_cnt_3m") == 5


def test_missing_and_error_stores_are_all_na():
    p = panel_for(["A", "B"])
    m = _prep(pd.DataFrame([("A", "2022-01", 2)], columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("A", False, False, None)]))  # A 오류, B QA 없음
    assert t[of.FEATURES].isna().all().all()


def test_truncated_store_masks_unobserved_windows():
    p = panel_for(["T"])
    m = _prep(pd.DataFrame([("T", "2024-02", 3), ("T", "2024-06", 1)],
                           columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("T", True, True, "2024-01")]))  # 2024-01 이전은 미관측
    assert np.isnan(get(t, "T", "2023Q4", "online_blog_cnt_12m"))
    assert np.isnan(get(t, "T", "2023Q4", "online_blog_has_ever"))    # 관측 구간 안에 언급 없음
    assert np.isnan(get(t, "T", "2024Q2", "online_blog_cnt_12m"))     # 창 시작 2023-07 < 2024-01
    assert get(t, "T", "2024Q2", "online_blog_cnt_3m") == 1           # 2024-04~06 관측 완료
    assert get(t, "T", "2024Q2", "online_blog_has_ever") == 1
    assert get(t, "T", "2024Q2", "online_blog_months_since_last") == 0
    assert get(t, "T", "2025Q1", "online_blog_cnt_12m") == 1          # 2024-04~2025-03


def test_mask_origins():
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2021-02", 1)], columns=["store_id", "year_month", "mention_count"]))
    t = of.mask_origins(of.build_online_features(p, m, qa([("A", True, False, None)])), ["2021Q1", "2021Q2"])
    assert t.loc[t["origin"].isin(["2021Q1", "2021Q2"]), of.FEATURES].isna().all().all()
    assert t.loc[t["origin"] == "2021Q3", "online_blog_has_ever"].iloc[0] == 1


# ---------------------------------------------------------------- #26
def synthetic_bias_panel(bias: bool, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for o in ORIGINS:
        ev = rng.random(n) < 0.12
        p_has = np.where(ev, 0.40, 0.55)  # 진짜 격차 0.15
        if bias and o in odb.TEST_ORIGINS:
            p_has = np.where(ev, 0.20, 0.55)  # 삭제로 폐업 쪽 보유율만 더 낮아짐
        has = (rng.random(n) < p_has).astype(float)
        rows.append(pd.DataFrame({"store_id": [f"S{i}" for i in range(n)], "origin": o,
                                  "event_12m": ev.astype(int), "online_blog_has_12m": has}))
    return pd.concat(rows, ignore_index=True)


@pytest.mark.parametrize("bias,expect_fail", [(True, True), (False, False)])
def test_deletion_bias_verdict(bias, expect_fail):
    df = synthetic_bias_panel(bias)
    tab = odb.gap_table(df, ORIGINS, n_boot=100)
    fails = tab.loc[tab["verdict"] == "FAIL", "origin"].tolist()
    if expect_fail:
        assert set(fails) == set(odb.TEST_ORIGINS)
    else:
        assert len(fails) <= 1  # 7개 검정 중 우연한 불통과는 거의 없어야 한다
    assert (tab.loc[tab["role"] != "test", "verdict"] == "-").all()


def test_lower_bound_policy_keeps_partial_counts():
    p = panel_for(["T"])
    m = _prep(pd.DataFrame([("T", "2024-02", 3), ("T", "2024-06", 1)],
                           columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("T", True, True, "2024-01")]), truncated_policy="lower_bound")
    assert get(t, "T", "2024Q2", "online_blog_cnt_12m") == 4   # 관측된 것만 센 하한
    assert get(t, "T", "2023Q4", "online_blog_has_ever") == 0
    with pytest.raises(ValueError):
        of.build_online_features(p, m, qa([("T", True, True, "2024-01")]), truncated_policy="x")
