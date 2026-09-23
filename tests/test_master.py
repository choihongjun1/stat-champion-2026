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


def test_build_preserves_panel_and_unmatched():
    labels = _labels()
    out, log = master.build_master_base(labels, _spatial(), _er())
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
        master.build_master_base(_labels(), _spatial(), er)


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
    out, _ = master.build_master_base(labels, _spatial(), _er())
    extra = pd.DataFrame({"store_id": ["GR_1", "GR_1"], "origin": ["2024Q1", "2024Q1"],
                          "blog_posts_3m": [1, 2]})
    with pytest.raises(pd.errors.MergeError):
        master.attach_enriched_table(out, extra, "online")
