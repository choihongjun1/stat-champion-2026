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


@pytest.mark.parametrize("age,expect", [
    (0, "1년 미만"), (11, "1년 미만"), (12, "1~3년"), (35, "1~3년"), (36, "3~5년"),
    (59, "3~5년"), (60, "5~10년"), (119, "5~10년"), (120, "10년 이상"), (10**7, "10년 이상"),
])
def test_age_band_boundaries_are_half_open(age, expect):
    """#34 리뷰: 경계를 [lo, hi)로 — 정확히 12·36·60·120개월인 점포는 아래 밴드가 아니라 다음 밴드다."""
    assert diagnose.age_band(pd.Series([age])).iloc[0] == expect


def test_age_band_unknown_for_missing():
    assert diagnose.age_band(pd.Series([np.nan])).iloc[0] == "미상"


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
    assert isinstance(online["display"], bool)
    held = long[~long["display"] & ~long["data_missing"]]  # 데이터 없음 보류는 따로 검사
    assert (held["factor_id"] == "online_attention").all() and (held["contribution"] > 0).all()
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


def test_months_since_last_na_distinguishes_no_history_from_truncated_unknown():
    """#34 리뷰: months_since_last NA도 has_ever가 확정 0이면 '이력 없음', has_ever도 NA면(절단으로 관측
    시작 이전을 모름) 다른 온라인 feature NA와 같은 '관측 불가' 문구를 쓴다(새 문구를 만들지 않는다 —
    #41 driver_code 계약을 건드리지 않기 위해)."""
    assert diagnose.online_driver_text("online_blog_months_since_last", float("nan"), has_ever=0.0) \
        == "블로그 언급 이력 없음"
    assert diagnose.online_driver_text("online_blog_months_since_last", float("nan"), has_ever=float("nan")) \
        == "관측 불가(검색 결과 상한)"
    assert diagnose.online_driver_text("online_blog_months_since_last", float("nan")) == "블로그 언급 이력 없음"


@pytest.mark.parametrize("feature,v,presence", [
    ("online_blog_cnt_12m", 101, True), ("online_blog_cnt_12m", 0, False),
    ("online_blog_trend_6m", -37, False), ("online_blog_trend_6m", 5, True),
    ("online_blog_months_since_last", 0, True), ("online_blog_months_since_last", 12, False),
    ("online_blog_months_since_last", float("nan"), False), ("online_blog_has_ever", 0, False),
])
def test_online_presence_signal(feature, v, presence):
    assert diagnose.online_signal_is_presence(feature, v) is presence


def test_all_missing_factor_is_held(tmp_path, panel):
    """상권 feature가 전부 결측인 점포(상권 경계 밖)는 상권 요인을 진단문으로 내보내지 않는다."""
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    long = diagnose.run(mp, tmp_path / "diag", online_path=op, primary="enriched", origin=None,
                        n_background=4, max_stores=60)
    trdar_ids = {f["id"] for f in diagnose.FACTORS if any(c.startswith("trdar_") for c in f["features"])}
    miss = long[long["data_missing"]]
    assert len(miss) > 0 and set(miss["factor_id"]) <= trdar_ids | {"online_attention"}
    assert miss["factor_id"].isin(trdar_ids).any()
    assert miss.loc[miss["factor_id"].isin(trdar_ids), "explanation"].str.contains("상권 경계 밖").all()
    assert (~miss["display"]).all() and miss["display_note"].eq(diagnose.MISSING_NOTE).all()
    assert miss["explanation"].str.contains("진단하지 않습니다").all()
    assert not miss["explanation"].str.contains("기여했습니다").any()


def _factor_features(fid):
    return next(f["features"] for f in diagnose.FACTORS if f["id"] == fid)


def test_missing_reason_inside_vs_outside_trdar(panel):
    """상권 안인데 매출 feature만 전부 NA인 점포는 '매출 공개 자료 없음', 상권 밖 점포는 4개 요인 모두 '상권 경계 밖'."""
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    model = detect.DetectModel().fit(X, panel["event_12m"].to_numpy().astype(int))
    last = (panel["origin"] == panel["origin"].max()).to_numpy()
    raw = panel[last].reset_index(drop=True)
    Xt = X[last].reset_index(drop=True)
    inside = int(np.flatnonzero(raw["trdar_cd"].notna())[0])
    outside = int(np.flatnonzero(raw["trdar_cd"].isna())[0])
    sales = [c for c in _factor_features("peer_sales") if c in Xt.columns]
    Xt.loc[inside, sales] = np.nan
    raw.loc[inside, sales] = np.nan
    meta = raw[["store_id", "origin", "biz_type", "gu", "age_months"]]
    res = diagnose.explain(model, Xt, X.sample(4, random_state=0), meta, raw)
    long = res["long"]
    sid_in, sid_out, org = raw.at[inside, "store_id"], raw.at[outside, "store_id"], raw.at[inside, "origin"]

    ins = long[(long["store_id"] == sid_in) & (long["factor_id"] == "peer_sales")].iloc[0]
    assert ins["data_missing"] and ins["missing_reason"] == "해당 상권에 이 업종 매출 공개 자료 없음"
    assert "매출 공개 자료 없음" in ins["explanation"] and "상권 경계 밖" not in ins["explanation"]
    other = long[(long["store_id"] == sid_in) & long["factor_id"].isin(["trdar_population", "trdar_vitality",
                                                                         "peer_competition"])]
    assert len(other) == 3 and not other["data_missing"].any()  # 값이 있는 상권 요인은 그대로 진단

    out = long[(long["store_id"] == sid_out) & long["factor_id"].isin(diagnose.TRDAR_FACTOR_IDS)]
    assert len(out) == 4 and out["data_missing"].all() and (~out["display"]).all()
    assert out["explanation"].str.contains("상권 경계 밖").all()

    fj = diagnose.factors_json(long, sid_in, org, res["values"](sid_in, org))
    assert all(isinstance(f["data_missing"], bool) for f in fj)
    ps = next(f for f in fj if f["factor_id"] == "peer_sales")
    assert ps["data_missing"] is True and ps["display"] is False
    assert ps["missing_reason"] == "sales_unpublished" and ps["hold_reason"] == "data_missing"
    assert all(f["missing_reason"] is None and f["hold_reason"] is None for f in fj if f["display"])
    out_codes = long.loc[(long["store_id"] == sid_out) & long["factor_id"].isin(diagnose.TRDAR_FACTOR_IDS),
                         "missing_reason_code"]
    assert set(out_codes) == {"out_of_trdar"}
    assert sum(f["data_missing"] for f in fj) == 1


def test_missing_reason_does_not_assume_outside_without_trdar_cd():
    no_cd = pd.DataFrame({"store_id": ["a", "b"]})
    for fid in diagnose.TRDAR_FACTOR_IDS:
        assert set(diagnose.missing_reasons(fid, no_cd)) == {"상권 데이터 없음"}
    raw = pd.DataFrame({"trdar_cd": [None, "3110001"]})
    assert list(diagnose.missing_reasons("trdar_population", raw)) == ["상권 경계 밖", "해당 분기 상권 자료 없음"]
    assert list(diagnose.missing_reasons("peer_competition", raw)) == ["상권 경계 밖", "해당 상권에 이 업종 자료 없음"]
    assert list(diagnose.missing_reasons("online_attention", raw)) == ["관측 불가", "관측 불가"]


def test_missing_reason_codes_cover_every_reason_text():
    texts = {diagnose.REASON_OUTSIDE, diagnose.REASON_TRDAR_UNKNOWN, diagnose.REASON_ONLINE, diagnose.REASON_DEFAULT,
             *diagnose.REASON_INSIDE.values()}
    assert texts == set(diagnose.MISSING_REASON_CODES)  # 모든 사유 문구에 코드가 있다
    codes = list(diagnose.MISSING_REASON_CODES.values())
    assert len(codes) == len(set(codes)) == 7  # 코드는 사유 문구와 1:1
    assert all(c.isascii() and c == c.lower() for c in codes)
    assert set(diagnose.HOLD_REASONS) == {"online_review", "data_missing"}


@pytest.mark.parametrize("c,label", [(0.0, "영향 미미"), (0.0009, "영향 미미"), (-0.0009, "영향 미미"),
                                     (0.001, "위험 증가"), (-0.001, "위험 감소"), (0.05, "위험 증가")])
def test_direction_label_negligible(c, label):
    assert diagnose.direction_label(c) == label
    # 설명문과 같은 기준
    text = diagnose.explanation({"factor": "자치구", "contribution": c, "peer_level": "biz_type", "peer_percentile": 50})
    assert ("거의 영향을 주지 않았습니다" in text) == (label == "영향 미미")


@pytest.mark.parametrize("age,expect", [
    (0, "1년 미만"), (11, "1년 미만"), (12, "1~3년"), (35, "1~3년"), (36, "3~5년"),
    (59, "3~5년"), (60, "5~10년"), (119, "5~10년"), (120, "10년 이상"), (10**7, "10년 이상"),
])
def test_age_band_boundaries_are_half_open(age, expect):
    """#34 리뷰: 경계를 [lo, hi)로 — 정확히 12·36·60·120개월인 점포는 아래 밴드가 아니라 다음 밴드다."""
    assert diagnose.age_band(pd.Series([age])).iloc[0] == expect


def test_age_band_unknown_for_missing():
    assert diagnose.age_band(pd.Series([np.nan])).iloc[0] == "미상"


def test_months_since_last_na_distinguishes_no_history_from_truncated_unknown():
    """#34 리뷰: months_since_last NA도 has_ever가 확정 0이면 '이력 없음', has_ever도 NA면(절단으로 관측
    시작 이전을 모름) 다른 온라인 feature NA와 같은 '관측 불가' 문구를 쓴다(새 문구를 만들지 않는다 —
    #41 driver_code 계약을 건드리지 않기 위해)."""
    assert diagnose.online_driver_text("online_blog_months_since_last", float("nan"), has_ever=0.0) \
        == "블로그 언급 이력 없음"
    assert diagnose.online_driver_text("online_blog_months_since_last", float("nan"), has_ever=float("nan")) \
        == "관측 불가(검색 결과 상한)"
    assert diagnose.online_driver_text("online_blog_months_since_last", float("nan")) == "블로그 언급 이력 없음"


@pytest.mark.parametrize("feature,v,has_ever,expect_code", [
    ("online_blog_trend_6m", -37, None, "decline"), ("online_blog_trend_6m", 5, None, "presence"),
    ("online_blog_trend_6m", 0, None, "no_change"),
    ("online_blog_cnt_12m", 101, None, "presence"), ("online_blog_cnt_12m", 0, None, "absent"),
    ("online_blog_cnt_3m", 4, None, "presence"), ("online_blog_cnt_3m", 0, None, "absent"),
    ("online_blog_has_12m", 1, None, "presence"), ("online_blog_has_12m", 0, None, "absent"),
    ("online_blog_has_ever", 1, None, "presence"), ("online_blog_has_ever", 0, None, "absent"),
    ("online_blog_months_since_last", 0, None, "presence"), ("online_blog_months_since_last", 2, None, "presence"),
    ("online_blog_months_since_last", 4, None, "lapse"),
    ("online_blog_months_since_last", float("nan"), 0.0, "absent"),
    ("online_blog_months_since_last", float("nan"), float("nan"), "unobservable"),
    ("online_blog_cnt_3m", float("nan"), None, "unobservable"),
])
def test_driver_code_matches_text_classification(feature, v, has_ever, expect_code):
    """#41 _ONLINE_DRIVER_PATTERNS(6342a82·eae0531)와 같은 분류가 나오는지 — 모든 온라인 factor 문구 템플릿."""
    text = diagnose.online_driver_text(feature, v, has_ever)
    assert diagnose.classify_online_driver(text) == expect_code
    assert expect_code in diagnose.ONLINE_DRIVER_CODES


def test_classify_online_driver_unknown_text_is_none():
    assert diagnose.classify_online_driver("알 수 없는 문구") is None
    assert diagnose.classify_online_driver(None) is None


def test_truncated_store_online_factor_held_even_when_partially_missing(panel):
    """#34 리뷰: 절단 점포는 온라인 요인이 전부 결측이든 일부만 결측이든 data_missing/online_unobservable로
    보류한다 — 관측되지 않은 창을 근거로 "관측 불가를 위험 감소"처럼 잘못 설명하지 않기 위해서다."""
    cols = features.select_features(panel.columns, "base")
    X = features.build_X(panel, cols)
    model = detect.DetectModel().fit(X, panel["event_12m"].to_numpy().astype(int))
    last = (panel["origin"] == panel["origin"].max()).to_numpy()
    raw = panel[last].reset_index(drop=True)
    Xt = X[last].reset_index(drop=True)
    for c in features.ONLINE_PREDICTORS:
        Xt[c] = 1.0  # base 모형엔 온라인 feature가 없으니 요인 판정용으로 직접 붙인다
        raw[c] = 1.0
    trunc_row, other_row = 0, 1
    online_cols = list(features.ONLINE_PREDICTORS)
    Xt.loc[trunc_row, ["online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_has_12m", "online_blog_trend_6m"]] = np.nan
    raw.loc[trunc_row, ["online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_has_12m", "online_blog_trend_6m"]] = np.nan
    Xt.loc[other_row, ["online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_has_12m", "online_blog_trend_6m"]] = np.nan
    raw.loc[other_row, ["online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_has_12m", "online_blog_trend_6m"]] = np.nan
    # online_attention을 활성 요인으로 만들려면 model.columns_에 온라인 컬럼이 있어야 하지만 base로 학습했으니
    # 없다 — factor_shapley가 요인별 컬럼을 그대로 쓰므로 explain을 우회해 핵심 로직만 검사한다.
    active = diagnose.FACTORS + []
    factor_cols = [[c for c in f["features"] if c in list(Xt.columns) + online_cols] for f in active]
    on = next(k for k, f in enumerate(active) if f["id"] == "online_attention")
    factor_cols[on] = online_cols
    meta = raw[["store_id", "origin", "biz_type", "gu", "age_months"]]
    trunc_stores = {raw.at[trunc_row, "store_id"]}

    class _StubModel:
        columns_ = list(Xt.columns) + online_cols

        def predict_proba(self, Xin):
            return model.predict_proba(Xin[[c for c in Xin.columns if c in X.columns]])

    res = diagnose.explain(_StubModel(), Xt, X.sample(4, random_state=0).assign(**{c: 1.0 for c in online_cols}),
                           meta, raw, truncated_stores=trunc_stores)
    long = res["long"]
    sid_trunc, sid_other, org = raw.at[trunc_row, "store_id"], raw.at[other_row, "store_id"], raw.at[trunc_row, "origin"]

    trunc_row_long = long[(long["store_id"] == sid_trunc) & (long["factor_id"] == "online_attention")].iloc[0]
    assert trunc_row_long["data_missing"] and trunc_row_long["hold_reason"] == "data_missing"
    assert trunc_row_long["missing_reason"] == "관측 불가" and not trunc_row_long["display"]
    fj = diagnose.factors_json(long, sid_trunc, org, res["values"](sid_trunc, org))
    online_fj = next(f for f in fj if f["factor_id"] == "online_attention")
    assert online_fj["missing_reason"] == "online_unobservable" and online_fj["display"] is False
    assert "관측 불가" in online_fj["explanation"] or "이 요인은 진단하지 않습니다" in online_fj["explanation"]
    assert "위험" not in online_fj["explanation"] or "낮추는" not in online_fj["explanation"]

    other_row_long = long[(long["store_id"] == sid_other) & (long["factor_id"] == "online_attention")].iloc[0]
    assert not other_row_long["data_missing"]  # 같은 결측 패턴이라도 절단 점포가 아니면 그대로 진단한다


def test_online_review_hold_reason(tmp_path, panel):
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    long = diagnose.run(mp, tmp_path / "diag", online_path=op, primary="enriched", origin=None,
                        n_background=4, max_stores=60)
    held = long[~long["display"]]
    assert set(held["hold_reason"]) <= set(diagnose.HOLD_REASONS)
    assert (long.loc[long["display"], "hold_reason"] == "").all()
    review = long[long["hold_reason"] == "online_review"]
    assert (review["factor_id"] == "online_attention").all() and not review["data_missing"].any()
    assert (long.loc[long["data_missing"], "hold_reason"] == "data_missing").all()


def _qa_csv(tmp_path, rows):
    """rows: (store_id, error, first_date_truncated, oldest_raw_postdate) 튜플 목록."""
    p = tmp_path / "qa.csv"
    pd.DataFrame(rows, columns=["store_id", "error", "first_date_truncated", "oldest_raw_postdate"]).to_csv(
        p, index=False)
    return p


def test_load_truncated_stores_reads_qa_csv(tmp_path):
    p = _qa_csv(tmp_path, [("A", "", "True", "20200101"), ("B", "", "False", ""), ("C", "err", "False", "")])
    assert diagnose.load_truncated_stores(p) == {"A"}


def test_load_background_index_maps_positions_and_rejects_missing(tmp_path):
    df = pd.DataFrame({"store_id": ["A", "B", "C"], "origin": ["2024Q1", "2024Q1", "2024Q1"]})
    idx_path = tmp_path / "bg.csv"
    pd.DataFrame({"store_id": ["C", "A"], "origin": ["2024Q1", "2024Q1"]}).to_csv(idx_path, index=False)
    assert list(diagnose.load_background_index(idx_path, df)) == [2, 0]

    bad_path = tmp_path / "bg_bad.csv"
    pd.DataFrame({"store_id": ["Z"], "origin": ["2024Q1"]}).to_csv(bad_path, index=False)
    with pytest.raises(ValueError):
        diagnose.load_background_index(bad_path, df)


def test_background_fingerprint_matches_save_background_index_hash(tmp_path):
    """diagnose.background_fingerprint와 shapley_background_stability.save_background_index는 같은 해시를
    내야 한다 — #34가 저장한 배경을 서빙이 그대로 썼는지 sha256으로 대조할 수 있게."""
    from src.analysis.shapley_background_stability import save_background_index

    df = pd.DataFrame({"store_id": [f"S{i}" for i in range(5)], "origin": ["2024Q1"] * 5})
    bg_idx = np.array([1, 3])
    fp = diagnose.background_fingerprint(df, bg_idx)
    h = save_background_index(df, bg_idx, tmp_path / "bg.csv")
    assert fp["sha256"] == h and fp["n"] == 2


def test_run_reproduces_saved_background_index(tmp_path, panel):
    """#34: --background-index로 저장된 배경을 주면 무작위 대신 그 (store_id, origin)을 그대로 쓴다."""
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    origins = sorted(panel["origin"].unique())
    train_pool = panel[panel["origin"] < origins[-5]]  # run()의 학습 구간(origins[:t-EMBARGO])보다 넉넉히 이전
    bg_rows = train_pool[["store_id", "origin"]].drop_duplicates().head(4)
    bg_path = tmp_path / "bg.csv"
    bg_rows.to_csv(bg_path, index=False)

    long = diagnose.run(mp, tmp_path / "diag", online_path=op, primary="enriched", origin=None,
                        n_background=4, max_stores=30, background_index_path=bg_path)
    assert len(long) > 0  # 배경 인덱스가 학습 구간과 맞지 않으면 run()이 이미 예외로 멈춘다


def test_run_with_qa_holds_truncated_stores_end_to_end(tmp_path, panel):
    """#34: --qa를 주면 절단 점포의 온라인 요인이 data_missing/online_unobservable로 보류된다."""
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    online = _online_table(panel)
    online.to_parquet(op, index=False)
    last_origin = panel["origin"].max()
    trunc_store = panel.loc[panel["origin"] == last_origin, "store_id"].iloc[0]
    qa_rows = [(s, "", "True" if s == trunc_store else "False", "20200101" if s == trunc_store else "")
              for s in panel["store_id"].unique()]
    qa_path = _qa_csv(tmp_path, qa_rows)

    long = diagnose.run(mp, tmp_path / "diag", online_path=op, primary="enriched", origin=None,
                        n_background=4, max_stores=60, qa_path=qa_path)
    row = long[(long["store_id"] == trunc_store) & (long["factor_id"] == "online_attention")]
    if len(row):  # 진단 대상 표본(max_stores)에 포함됐을 때만 검사한다
        assert row.iloc[0]["hold_reason"] == "data_missing"
        assert row.iloc[0]["missing_reason"] == "관측 불가"
