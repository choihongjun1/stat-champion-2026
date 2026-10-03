# -*- coding: utf-8 -*-
"""온라인 feature 집계(#25 결측 규칙)와 #26 삭제 편향 진단 테스트 (합성 데이터)."""
from __future__ import annotations

import json

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


def test_available_at_is_last_post_month_end_and_collected_at_is_separate():
    """2026-10-01: online_available_at = 창에 들어갈 수 있는 마지막 게시월의 말일(축B available_at = 게시월 말일),
    실제 수집 시각은 online_collected_at으로 따로 둔다 (수집 시각을 available_at에 넣지 않는다)."""
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2022-01", 2)], columns=["store_id", "year_month", "mention_count"]))
    q = qa([("A", True, False, None)])
    q["collected_at"] = pd.Timestamp("2026-09-23 23:39:46")
    t = of.build_online_features(p, m, q)
    assert list(of.META) == ["online_feature_asof", "online_available_at", "online_collected_at", "online_source_snapshot"]
    month_end = pd.PeriodIndex(p["origin_end"], freq="M").to_timestamp(how="end").normalize()
    assert (t["online_available_at"].to_numpy() == month_end.to_numpy()).all()
    assert (pd.to_datetime(t["online_available_at"]) <= p["origin_end"]).all()  # 불변식
    assert (t["online_collected_at"] == pd.Timestamp("2026-09-23 23:39:46")).all()
    assert (t["online_collected_at"] > t["online_available_at"]).all()  # 과거 origin은 모두 회고적 재구성


def test_collected_at_is_nat_without_qa_column():
    """QA에 collected_at이 없으면(합성 테스트 등) online_collected_at만 NaT — available_at은 게시월 기준 그대로."""
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2022-01", 2)], columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("A", True, False, None)]))
    assert t["online_collected_at"].isna().all() and t["online_available_at"].notna().all()


def test_load_qa_parses_collected_at_as_kst_naive(tmp_path):
    """수집 스크립트의 UTC ISO8601 → KST tz-naive (#21 export_online_features._to_naive_kst와 같은 처리)."""
    csv = tmp_path / "qa.csv"
    csv.write_text(
        "store_id,error,first_date_truncated,oldest_raw_postdate,collected_at\n"
        "A,,False,,2026-09-23T14:39:46.383173+00:00\n",
        encoding="utf-8")
    q = of.load_qa(csv)
    assert q["collected_at"].iloc[0] == pd.Timestamp("2026-09-23 23:39:46.383173")  # UTC+9
    assert q["collected_at"].dt.tz is None


@pytest.mark.parametrize("policy", ["na", "lower_bound"])
def test_assert_no_future_posts_passes_on_built_table(policy):
    p = panel_for(["A", "T", "Z"])  # Z: 월별 행 없음
    m = _prep(pd.DataFrame([("A", "2021-04", 5), ("A", "2022-03", 1), ("T", "2023-11", 2), ("T", "2024-05", 3)],
                           columns=["store_id", "year_month", "mention_count"]))
    q = qa([("A", True, False, None), ("T", True, True, "2023-10"), ("Z", True, False, None)])
    t = of.build_online_features(p, m, q, truncated_policy=policy)
    r = of.assert_no_future_posts(t, p, m)
    assert r["rows"] == len(p) and r["cells_checked"] > 0


def test_assert_no_future_posts_fails_when_window_includes_future_month():
    """창이 한 달이라도 미래로 밀리면(= origin_end 이후 게시월 포함) 멈춘다 — 표가 스스로 만든 값끼리 비교하는
    자명한 검사가 아니라, 월별 원천에서 다시 집계한 값과 대조한다."""
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2021-04", 5), ("A", "2022-04", 2)], columns=["store_id", "year_month", "mention_count"]))
    q = qa([("A", True, False, None)])
    shifted = p.assign(origin_end=p["origin_end"] + pd.offsets.MonthEnd(1))  # 창 끝을 origin 다음 달로
    leaky = of.build_online_features(shifted, m, q)
    leaky["online_available_at"] = of.build_online_features(p, m, q)["online_available_at"]  # 메타는 정상인 척
    with pytest.raises(ValueError, match="미래 게시월"):
        of.assert_no_future_posts(leaky, p, m)
    tampered = of.build_online_features(p, m, q)
    tampered.loc[tampered["origin"] == "2021Q1", "online_blog_cnt_3m"] = 5.0  # 2021-04 글이 2021Q1에 섞임
    with pytest.raises(ValueError, match="미래 게시월"):
        of.assert_no_future_posts(tampered, p, m)


def test_assert_no_future_posts_checks_available_at_invariant():
    p = panel_for(["A"])
    m = _prep(pd.DataFrame([("A", "2022-01", 2)], columns=["store_id", "year_month", "mention_count"]))
    t = of.build_online_features(p, m, qa([("A", True, False, None)]))
    t["online_available_at"] = pd.Timestamp("2026-09-23")  # 2026-09-29 버전처럼 수집 시각을 넣으면
    with pytest.raises(ValueError, match="available_at > origin_end"):
        of.assert_no_future_posts(t, p, m)


def test_cli_runs_future_post_check_before_writing(tmp_path, monkeypatch):
    """CLI는 표를 쓰기 전에 assert_no_future_posts를 실행한다 — 실패하면 파일을 만들지 않는다."""
    p = panel_for(["A"])
    pp, mp, qp, op = tmp_path / "panel.parquet", tmp_path / "m.parquet", tmp_path / "qa.csv", tmp_path / "o.parquet"
    p.to_parquet(pp, index=False)
    lp = tmp_path / "lic.parquet"
    pd.DataFrame({"store_id": ["A"], "name_raw": ["가나다라"]}).to_parquet(lp, index=False)
    pd.DataFrame([("A", "2022-01", 2)], columns=["store_id", "year_month", "mention_count"]).to_parquet(mp, index=False)
    qp.write_text("store_id,error,first_date_truncated,oldest_raw_postdate,collected_at\n"
                  "A,,False,,2026-09-23T14:39:46+00:00\n", encoding="utf-8")
    of.main(["--panel", str(pp), "--monthly", str(mp), "--qa", str(qp), "--out", str(op), "--licenses", str(lp)])
    assert op.exists()

    def boom(*a, **k):
        raise ValueError("미래 게시월 (테스트)")

    monkeypatch.setattr(of, "assert_no_future_posts", boom)
    op2 = tmp_path / "o2.parquet"
    with pytest.raises(ValueError):
        of.main(["--panel", str(pp), "--monthly", str(mp), "--qa", str(qp), "--out", str(op2), "--licenses", str(lp)])
    assert not op2.exists()


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


# ---------------------------------------------------------------- #44 A안: 짧은 상호(정규화 ≤ 2자) 전 origin NA
# (store_id, name_raw, 정규화 후 짧은 상호인가) — 정규화 = PR #21 collect_online_presence.normalize_name
SHORT_CASES = [
    ("S1", "가", True),                  # 1자
    ("S2", "가나", True),                # 2자
    ("S3", "  가 · 나 !! ", True),       # raw는 길지만 공백·기호 제거 후 2자
    ("S4", "★A-b★", True),              # 영문 2자(소문자화)
    ("S5", None, True),                  # 상호 없음 → 길이 0
    ("L1", "가나다", False),             # 3자 → 유지
    ("L2", "A B C", False),              # 공백 제거 후 3자
    ("L3", "(주) 가 나", False),         # 괄호 기호만 빠지고 '주'는 남는다 → '주가나' 3자
]


def _short_inputs():
    stores = [c[0] for c in SHORT_CASES]
    p = panel_for(stores)
    monthly = _prep(pd.DataFrame([(s, f"{y}-{m:02d}", 1 + (i % 3)) for i, s in enumerate(stores)
                                  for y in (2020, 2021, 2022, 2023, 2024, 2025) for m in (1, 4, 7, 10)],
                                 columns=["store_id", "year_month", "mention_count"]))
    q = qa([(s, True, False, None) for s in stores])
    lic = pd.DataFrame({"store_id": stores, "name_raw": [c[1] for c in SHORT_CASES]})
    return p, monthly, q, lic


def test_feature_list_is_the_model_online_predictor_list():
    from src.models import features

    assert of.FEATURES == list(features.ONLINE_PREDICTORS)


def test_short_name_store_ids_use_pr21_normalization():
    _, _, _, lic = _short_inputs()
    assert of.short_name_store_ids(lic) == {c[0] for c in SHORT_CASES if c[2]}


def test_short_name_policy_masks_all_predictors_in_every_origin_and_keeps_the_rest():
    p, monthly, q, lic = _short_inputs()
    t = of.build_online_features(p, monthly, q, "snap")
    out, rep = of.apply_short_name_policy(t, lic, "na")
    short = out["store_id"].isin({c[0] for c in SHORT_CASES if c[2]})
    # 행·점포·순서 불변, 점포 삭제 없음
    assert len(out) == len(t) and (out["store_id"].to_numpy() == t["store_id"].to_numpy()).all()
    assert (out["origin"].to_numpy() == t["origin"].to_numpy()).all()
    # ≤2자: 모든 origin에서 모든 온라인 predictor NA (NA율 100%)
    assert out.loc[short, of.FEATURES].isna().all().all()
    assert set(out.loc[short, "origin"]) == set(ORIGINS)
    # 마스크 전에는 값이 있었다 (정책이 실제로 지운 것)
    assert t.loc[short, of.FEATURES].notna().any().any()
    # ≥3자: 값 그대로 (NA 위치 포함)
    pd.testing.assert_frame_equal(out.loc[~short], t.loc[~short])
    # 식별자·시점 메타는 전부 그대로
    keep_cols = [c for c in t.columns if c not in of.FEATURES]
    pd.testing.assert_frame_equal(out[keep_cols], t[keep_cols])
    assert rep["short_name_masked_store_count"] == 5 and rep["short_name_masked_row_count"] == 5 * len(ORIGINS)
    assert rep["unaffected_store_count"] == 3 and rep["unaffected_row_count"] == 3 * len(ORIGINS)
    assert rep["short_name_masked_rows_by_origin"] == {o: 5 for o in ORIGINS}
    assert rep["masked_columns"] == of.FEATURES and rep["rows"] == len(t) and rep["stores"] == 8


def test_short_name_keep_policy_changes_nothing_and_bad_inputs_fail():
    p, monthly, q, lic = _short_inputs()
    t = of.build_online_features(p, monthly, q, "snap")
    out, rep = of.apply_short_name_policy(t, lic, "keep")
    pd.testing.assert_frame_equal(out, t)
    assert rep["short_name_masked_row_count"] == 0 and rep["short_name_row_count"] == 5 * len(ORIGINS)
    with pytest.raises(ValueError, match="인허가 표에 없다"):
        of.apply_short_name_policy(t, lic.iloc[1:], "na")
    with pytest.raises(ValueError, match="중복"):
        of.apply_short_name_policy(t, pd.concat([lic, lic.head(1)]), "na")
    with pytest.raises(ValueError, match="policy"):
        of.apply_short_name_policy(t, lic, "drop")


@pytest.mark.parametrize("kind", ["train", "score"])
def test_cli_applies_short_name_policy_to_training_and_score_tables(tmp_path, kind):
    """학습용(라벨 패널 전 origin)·예측용(score origin 하나) 표 모두 같은 CLI 경로로 #44가 적용되고 QA가 남는다."""
    p, monthly, _, lic = _short_inputs()
    if kind == "score":
        p = p[p["origin"] == ORIGINS[-1]]
    pp, mp, qp, lp = (tmp_path / n for n in ("panel.parquet", "m.parquet", "qa.csv", "lic.parquet"))
    p.to_parquet(pp, index=False)
    monthly[["store_id", "year_month", "mention_count"]].to_parquet(mp, index=False)
    qp.write_text("store_id,error,first_date_truncated,oldest_raw_postdate\n"
                  + "".join(f"{s},,False,\n" for s in p["store_id"].unique()), encoding="utf-8")
    lic.to_parquet(lp, index=False)
    out = tmp_path / f"online_{kind}.parquet"
    of.main(["--panel", str(pp), "--monthly", str(mp), "--qa", str(qp), "--out", str(out), "--licenses", str(lp)])
    t = pd.read_parquet(out)
    short = t["store_id"].isin({c[0] for c in SHORT_CASES if c[2]})
    assert len(t) == len(p) and t["store_id"].nunique() == 8
    assert t.loc[short, of.FEATURES].isna().all().all() and t.loc[~short, "online_blog_has_ever"].notna().all()
    assert t["online_source_snapshot"].str.endswith("#short_name=na").all()
    rep = json.loads((tmp_path / f"online_{kind}.parquet.short_name_qa.json").read_text(encoding="utf-8"))
    assert rep["policy"] == "na" and rep["short_name_masked_store_count"] == 5
    assert rep["short_name_masked_row_count"] == int(short.sum()) and rep["unaffected_store_count"] == 3
    assert rep["licenses_sha12"] and rep["panel"] == str(pp)
    # keep은 명시할 때만 (#44 이전 표 재현용)
    out2 = tmp_path / "keep.parquet"
    of.main(["--panel", str(pp), "--monthly", str(mp), "--qa", str(qp), "--out", str(out2), "--licenses", str(lp),
             "--short-name-policy", "keep"])
    assert pd.read_parquet(out2).loc[short.to_numpy(), "online_blog_has_ever"].notna().all()
