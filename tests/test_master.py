# -*- coding: utf-8 -*-
"""W2-0 master_base 조립 테스트 (합성 데이터)."""
import pandas as pd
import pytest

from src.data import master
from src.data import master_schema as schema


def _labels():
    rows = [
        ("GR_1", "2024Q1", "2024-01-01", "2024-03-31", 0, 50),
        ("GR_1", "2025Q2", "2025-04-01", "2025-06-30", 1, 65),
        ("GR_2", "2024Q1", "2024-01-01", "2024-03-31", 0, 10),
        ("GR_3", "2025Q2", "2025-04-01", "2025-06-30", 0, 3),
    ]
    df = pd.DataFrame(rows, columns=["store_id", "origin", "origin_start", "origin_end",
                                     "event_12m", "age_months"])
    df["origin_start"] = pd.to_datetime(df["origin_start"])
    df["origin_end"] = pd.to_datetime(df["origin_end"])
    df["age_months"] = df["age_months"].astype("int32")
    df["source_type"] = "일반음식점"
    df["biz_type"] = "일반음식점"
    df["area"] = pd.array([10.0, 10.0, None, 30.0], dtype="Float64")
    df["has_coord"] = [True, True, False, True]
    df["feature_asof"] = df["origin_end"]
    df["available_at"] = df["origin_end"]
    df["source_snapshot"] = "gr.csv"
    df["maturity_cutoff_used_months"] = 1
    return df[schema.LABEL_COLUMNS]


def _spatial():
    return pd.DataFrame({
        "store_id": ["GR_1", "GR_2", "GR_3", "GR_9"],
        "gu": ["마포구", "광진구", "마포구", "마포구"],
        "pnu": ["1144012000103580018", None, "1144012000103580019", None],
        "coord_missing": [False, True, False, False],
        "coord_suspect": [False, False, False, False],
        "trdar_cd": ["3110001", None, None, "3110002"],
        "trdar_type": ["골목상권", None, None, "골목상권"],
        "in_polygon": [True, False, False, True],
        "spatial_ambiguous": [False, False, False, False],
        "spatial_match_method": ["within", "excluded_coord_missing", "unmatched_within", "within"],
        "land_price_2024_valid": [100.0, None, 300.0, 1.0],
        "land_price_2025_valid": [110.0, None, None, 1.0],
        "land_price_2026_valid": [120.0, None, 330.0, 1.0],
    })


def _er():
    return pd.DataFrame({
        "store_id": ["GR_1", "GR_2", "GR_3", "GR_9"],
        "gu_mismatch": [False, False, False, False],
        "parse_status": ["ok", "ok", "ok", "ok"],
        "matched": [True, False, False, True],
        "ambiguous": [False, True, False, False],
        "match_tier": [1.0, 3.0, None, 1.0],
        "match_confidence": ["high", None, None, "high"],
        "crowded_pnu": pd.array([False, None, None, False], dtype="boolean"),
        "sj_entity_id": ["E1", None, None, "E9"],
        "unmatched_reason": [None, "ambiguous_candidates", "fuzzy_below_threshold", None],
    })


def _trdar():
    """상권 3110001 하나. origin 분기 T(2025Q2) 값도 넣어 T-1만 쓰는지 확인한다."""
    q = ["2023Q4", "2024Q1", "2025Q1", "2025Q2"]
    return pd.DataFrame({
        "trdar_cd": ["3110001"] * 4,
        "quarter": q,
        "trdar_flow_pop": pd.array([100, 110, 120, 999], dtype="Float64"),
        "trdar_change_index": ["LL", "LH", "HH", "XX"],
        "trdar_oper_months_avg": pd.array([50, 51, 52, 999], dtype="Float64"),
        "trdar_close_months_avg": pd.array([30, 31, 32, 999], dtype="Float64"),
        "trdar_resident_pop": pd.array([7, 7, 7, 7], dtype="Float64"),
        "trdar_resident_value_asof": ["2023Q4"] * 4,
        "trdar_worker_pop": pd.array([9, 9, 9, 9], dtype="Float64"),
        "trdar_worker_value_asof": ["2023Q4"] * 4,
        "trdar_facility_cnt": pd.array([None, None, 3, 3], dtype="Float64"),
        "trdar_facility_value_asof": [None, None, "2025Q1", "2025Q1"],
    })


def _build(labels, er=None):
    return master.build_master_base(labels, _spatial(), _er() if er is None else er,
                                    _trdar(), "synthetic.csv@0000")


def test_build_preserves_panel_and_unmatched():
    labels = _labels()
    out, log = _build(labels)
    assert len(out) == len(labels)
    assert not out.duplicated(schema.KEY).any()
    assert list(out.columns) == list(schema.COLUMN_ROLES)
    o = out.set_index(schema.KEY)
    # 매칭 실패·좌표 결측 점포도 행이 남고 값만 NA
    assert pd.isna(o.loc[("GR_2", "2024Q1"), "trdar_cd"])
    assert o.loc[("GR_3", "2025Q2"), "er_matched"] == False  # noqa: E712
    assert all(r["leakage_violations"] == 0 for r in log)
    assert all(r["labels_unchanged"] and r["event_by_origin_equal"] for r in log)


def test_duplicate_right_key_stops():
    er = pd.concat([_er(), _er().iloc[[0]]], ignore_index=True)
    with pytest.raises(pd.errors.MergeError):
        _build(_labels(), er=er)


def test_verify_step_detects_label_change():
    labels = _labels()
    baseline = master.label_baseline(labels)
    changed = labels.copy()
    changed.loc[0, "event_12m"] = 1
    with pytest.raises(master.MasterValidationError, match="event"):
        master.verify_step("x", changed, baseline, [])


def test_verify_step_detects_row_loss():
    labels = _labels()
    baseline = master.label_baseline(labels)
    with pytest.raises(master.MasterValidationError, match="rows"):
        master.verify_step("x", labels.iloc[:-1], baseline, [])


def test_er_columns_are_not_predictors():
    preds = set(schema.predictor_columns())
    for col in ("er_matched", "er_ambiguous", "match_tier", "match_confidence",
                "crowded_pnu", "sj_entity_id", "unmatched_reason"):
        assert col not in preds
    assert schema.forbidden_predictors_in_registry() == []


def test_enriched_interface_requires_unique_key():
    labels = _labels()
    out, _ = _build(labels)
    extra = pd.DataFrame({"store_id": ["GR_1", "GR_1"], "origin": ["2024Q1", "2024Q1"],
                          "blog_posts_3m": [1, 2]})
    with pytest.raises(pd.errors.MergeError):
        master.attach_enriched_table(out, extra, "online")


def test_land_price_strict_asof():
    labels = _labels()
    # 2024Q2 origin(2024-06-30) 추가: 2024년(공시 2024-04-30)만 사용 가능
    extra = labels.iloc[[0]].copy()
    extra["origin"] = "2024Q2"
    extra["origin_start"] = pd.Timestamp("2024-04-01")
    extra["origin_end"] = extra["feature_asof"] = extra["available_at"] = pd.Timestamp("2024-06-30")
    labels = pd.concat([labels, extra], ignore_index=True)
    out, log = _build(labels)
    o = out.set_index(schema.KEY)

    # origin 2024Q1(2024-03-31) < 2024-04-30 → 어느 연도도 못 씀: 소급 없이 NA
    assert pd.isna(o.loc[("GR_1", "2024Q1"), "land_price"])
    assert pd.isna(o.loc[("GR_1", "2024Q1"), "land_price_year_used"])
    assert pd.isna(o.loc[("GR_1", "2024Q1"), "land_price_available_at"])
    # 2024Q2 → 2024년
    assert o.loc[("GR_1", "2024Q2"), "land_price"] == 100.0
    assert o.loc[("GR_1", "2024Q2"), "land_price_year_used"] == 2024
    assert o.loc[("GR_1", "2024Q2"), "land_price_source_snapshot"] == "공시지가_2024년.csv"
    # 2025Q2 → 2025년 (2026년 값은 절대 안 씀)
    assert o.loc[("GR_1", "2025Q2"), "land_price"] == 110.0
    assert o.loc[("GR_1", "2025Q2"), "land_price_feature_asof"] == pd.Timestamp("2025-01-01")
    # 선택 연도 값이 NA면 이전 연도로 carry-forward하지 않는다
    assert pd.isna(o.loc[("GR_3", "2025Q2"), "land_price"])
    assert o.loc[("GR_3", "2025Q2"), "land_price_year_used"] == 2025
    # wide 연도 컬럼은 master에 남지 않는다
    assert not any(c.startswith("land_price_20") for c in out.columns)
    assert (out["land_price_available_at"].dropna() <= out.loc[
        out["land_price_available_at"].notna(), "origin_end"]).all()
    assert log[-1]["leakage_violations"] == 0


def test_trdar_uses_t_minus_1_only():
    out, log = _build(_labels())
    o = out.set_index(schema.KEY)
    r = o.loc[("GR_1", "2025Q2")]
    assert r["trdar_quarter_used"] == "2025Q1"
    assert r["trdar_flow_pop"] == 120  # origin 분기 2025Q2 값(999)은 쓰지 않는다
    assert r["trdar_change_index"] == "HH"
    assert r["trdar_value_asof"] == "2025Q1"
    assert r["trdar_facility_value_asof"] == "2025Q1"
    assert r["trdar_available_at_basis"] == "archive_inferred"
    assert pd.isna(r["trdar_available_at"])  # 추정 공표일은 날짜로 기록하지 않는다
    assert not r["trdar_geometry_backcast_flag"]
    r = o.loc[("GR_1", "2024Q1")]
    assert r["trdar_quarter_used"] == "2023Q4"
    assert pd.isna(r["trdar_facility_cnt"])  # 원천 행 없음 → 0이 아니라 NA
    assert not r["trdar_geometry_backcast_flag"]  # 2024-03-31 >= geometry 2023-10-23
    early = _labels().iloc[[0]].assign(origin="2023Q3", origin_end=pd.Timestamp("2023-09-30"))
    df = early.merge(_spatial()[["store_id", "trdar_cd"]], on="store_id")
    assert master.attach_trdar_features(df, _trdar(), "x")["trdar_geometry_backcast_flag"].all()
    # trdar_cd 없는 점포는 상권 feature 전부 NA
    assert o.loc[("GR_2", "2024Q1"), master.TRDAR_FEATURE_COLS].isna().all()
    assert log[-1]["leakage_violations"] == 0


def test_trdar_lag_zero_is_rejected():
    df = _labels().merge(_spatial()[["store_id", "trdar_cd"]], on="store_id")
    with pytest.raises(ValueError):
        master.attach_trdar_features(df, _trdar(), "x", lag_quarters=0)


def test_leakage_counts_flag_same_quarter_use():
    out, _ = _build(_labels())
    bad = out.copy()
    bad["trdar_quarter_used"] = bad["origin"]  # T 자체 사용
    assert master.temporal_leakage_counts(bad)["trdar_quarter_used >= origin"] == len(bad)


def test_every_column_has_definition_and_gu_note(tmp_path):
    assert set(schema.VARIABLE_DEFINITIONS) == set(schema.COLUMN_ROLES)
    out, _ = _build(_labels())
    path = tmp_path / "MASTER_SPEC.md"
    master.write_master_spec_md(out, path=path)
    text = path.read_text(encoding="utf-8")
    assert "미래정보를 사용하지 않는다" in text and "ablation" in text
    assert schema.COLUMN_ROLES["gu"][0] == "predictor"
