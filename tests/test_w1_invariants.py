# -*- coding: utf-8 -*-
"""W1 invariant 통합 테스트.

합성 데이터로 **검사 로직 자체**를 검증하고(항상 실행), 실제 산출물이 있으면
`w1_invariants.run()`을 그대로 돌린다(없으면 skip). 산출물 검사는 원본 데이터가
있는 환경에서만 의미가 있으므로 CI에서 raw 없이도 테스트가 돌아야 한다.
"""
import pandas as pd
import pytest

from src.data import w1_invariants as inv


def _synthetic():
    """freeze 규칙을 만족하는 최소 산출물 4종."""
    # GR_3는 패널에 들어가지 않는 최신 개업 점포다 - 관측 한계를 label window 뒤로 밀어
    # "마지막 window가 관측 범위 안" 조건을 실제 데이터처럼 만족시킨다.
    lic = pd.DataFrame(
        {
            "store_id": ["GR_1", "GR_2", "GR_3"],
            "business_type": ["일반음식점", "일반음식점", "휴게음식점"],
            "license_date": pd.to_datetime(["2020-01-01", "2020-01-01", "2022-06-01"]),
            "close_date": pd.to_datetime([None, "2021-08-01", None]),
        }
    )
    labels = pd.DataFrame(
        {
            "store_id": ["GR_1", "GR_2"],
            "origin": ["2021Q1", "2021Q1"],
            "origin_end": pd.to_datetime(["2021-03-31", "2021-03-31"]),
            "feature_asof": pd.to_datetime(["2021-03-31", "2021-03-31"]),
            "available_at": pd.to_datetime(["2021-03-31", "2021-03-31"]),
            "event_12m": [0, 1],
            "age_months": [14, 14],
        }
    )
    spatial = pd.DataFrame(
        {
            "store_id": ["GR_1", "GR_2", "GR_3"],
            "in_polygon": [True, False, True],
            "trdar_cd": ["A", None, "B"],
            "land_price_2024": [100, 0, 100],
            "land_price_2025": [100, 100, 100],
            "land_price_2026": [100, 100, 100],
            "land_price_2024_valid": [100.0, None, 100.0],
            "land_price_2025_valid": [100.0, 100.0, 100.0],
            "land_price_2026_valid": [100.0, 100.0, 100.0],
            "land_price_zero_flag": [False, True, False],
        }
    )
    er = pd.DataFrame(
        {
            "store_id": ["GR_1", "GR_2", "GR_3"],
            "matched": [True, False, True],
            "match_tier": [1.0, None, 2.0],
            "ambiguous": [False, False, False],
            "sj_entity_id": ["E1", None, "E3"],
        }
    )
    return labels, lic, spatial, er


def _failed(results):
    return {name for _, name, ok, _ in results if not ok}


def test_structural_passes_on_valid_artifacts():
    assert _failed(inv.check_structural(*_synthetic())) == set()


def test_detects_label_leakage_when_store_closed_before_feature_asof():
    labels, lic, spatial, er = _synthetic()
    lic.loc[lic.store_id == "GR_1", "close_date"] = pd.Timestamp("2021-01-15")
    failed = _failed(inv.check_structural(labels, lic, spatial, er))
    assert "close_date <= feature_asof 행 없음" in failed


def test_detects_event_definition_mismatch():
    labels, lic, spatial, er = _synthetic()
    labels.loc[labels.store_id == "GR_1", "event_12m"] = 1
    assert "event_12m == (feature_asof, +12M] 폐업" in _failed(
        inv.check_structural(labels, lic, spatial, er)
    )


def test_detects_join_row_explosion():
    labels, lic, spatial, er = _synthetic()
    lic = pd.concat([lic, lic.iloc[[0]]], ignore_index=True)  # store_id 중복
    failed = _failed(inv.check_structural(labels, lic, spatial, er))
    assert "licenses store_id unique" in failed
    assert "+licenses join 후 행수 불변" in failed


def test_detects_store_id_rule_mismatch_as_unmatched_key():
    labels, lic, spatial, er = _synthetic()
    lic["store_id"] = ["LIC_3040000_1", "LIC_3040000_2", "LIC_3040000_3"]  # 옛 규칙
    failed = _failed(inv.check_structural(labels, lic, spatial, er))
    assert "labels ⊆ licenses" in failed
    assert "licenses 키 미매칭 0" in failed


def test_detects_nearest_assignment_leaking_into_trdar_cd():
    labels, lic, spatial, er = _synthetic()
    spatial.loc[spatial.store_id == "GR_2", "trdar_cd"] = "C"  # within 아닌데 배정
    assert "trdar_cd는 within일 때만 채워짐" in _failed(
        inv.check_structural(labels, lic, spatial, er)
    )


def test_detects_land_price_zero_not_nulled_in_valid_column():
    labels, lic, spatial, er = _synthetic()
    spatial.loc[spatial.store_id == "GR_2", "land_price_2024_valid"] = 0.0
    assert "land_price_2024_valid: raw 0 -> NA" in _failed(
        inv.check_structural(labels, lic, spatial, er)
    )


def test_golden_counts_flag_changes():
    labels, lic, spatial, er = _synthetic()
    failed = _failed(inv.check_golden(labels, lic, spatial, er))
    assert "labels rows" in failed  # 합성 데이터는 golden 수치와 다르다


@pytest.mark.skipif(
    not (inv.LABELS_PATH.exists() and inv.LICENSES_PATH.exists()
         and inv.SPATIAL_PATH.exists() and inv.ER_PATH.exists()),
    reason="W1 산출물이 없는 환경 (raw 데이터 필요)",
)
def test_real_artifacts_satisfy_all_invariants():
    table = inv.run()
    assert bool(table["통과"].all())
