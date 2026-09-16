# -*- coding: utf-8 -*-
"""공간조인 로직 테스트 (합성 polygon/point — raw 데이터 불필요)."""
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from src.data.config import CRS_STD
from src.data.spatial import (
    distance_distribution,
    make_points,
    nearest_analysis,
    threshold_sensitivity,
    within_join,
)


def make_areas():
    """정사각형 상권 2개: A(0~100), B(200~300). C는 A와 겹침(50~150)."""
    return gpd.GeoDataFrame(
        {
            "TRDAR_CD": ["A", "B", "C"],
            "TRDAR_CD_N": ["상권A", "상권B", "상권C"],
            "TRDAR_SE_1": ["골목상권", "발달상권", "골목상권"],
        },
        geometry=[
            Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]),
            Polygon([(200, 0), (300, 0), (300, 100), (200, 100)]),
            Polygon([(50, 0), (150, 0), (150, 100), (50, 100)]),
        ],
        crs=CRS_STD,
    )


def make_lic(rows):
    """rows: (store_id, x, y, coord_missing, coord_suspect)"""
    df = pd.DataFrame(rows, columns=["store_id", "x_5179", "y_5179",
                                     "coord_missing", "coord_suspect"])
    return df


def test_make_points_excludes_missing_and_suspect():
    lic = make_lic([
        ("L1", 10.0, 10.0, False, False),
        ("L2", None, None, True, False),
        ("L3", 999.0, 999.0, False, True),
    ])
    pts = make_points(lic)
    assert list(pts["store_id"]) == ["L1"]
    assert pts.crs.to_epsg() == 5179


def test_within_join_basic():
    pts = make_points(make_lic([("L1", 10.0, 10.0, False, False)]))
    res = within_join(pts, make_areas()).set_index("store_id")
    r = res.loc["L1"]
    assert r["in_polygon"] and r["trdar_cd"] == "A"
    assert r["trdar_name"] == "상권A" and r["trdar_type"] == "골목상권"
    assert r["polygon_candidate_count"] == 1 and not r["spatial_ambiguous"]


def test_within_join_unmatched_preserved():
    pts = make_points(make_lic([("L1", 400.0, 400.0, False, False)]))
    res = within_join(pts, make_areas()).set_index("store_id")
    r = res.loc["L1"]
    assert not r["in_polygon"] and r["polygon_candidate_count"] == 0
    assert pd.isna(r["trdar_cd"])


def test_within_join_ambiguous_overlap_no_arbitrary_pick():
    # (75, 50)은 A와 C 겹침 영역 → ambiguous, 임의 배정 금지
    pts = make_points(make_lic([("L1", 75.0, 50.0, False, False)]))
    res = within_join(pts, make_areas()).set_index("store_id")
    r = res.loc["L1"]
    assert r["polygon_candidate_count"] == 2
    assert r["spatial_ambiguous"]
    assert pd.isna(r["trdar_cd"])  # 배정하지 않음
    assert r["candidate_trdar_cds"] == "A|C"


def test_boundary_point_not_within():
    # 경계선 위 점은 within에서 빠진다 (shapely 정의) — 이 사실을 고정
    pts = make_points(make_lic([("L1", 0.0, 50.0, False, False)]))
    res = within_join(pts, make_areas()).set_index("store_id")
    assert not res.loc["L1", "in_polygon"]


def test_nearest_analysis_distance():
    lic = make_lic([("L1", 170.0, 50.0, False, False)])  # C(150) 밖 20m, B(200) 밖 30m
    pts = make_points(lic)
    areas = make_areas()
    within = within_join(pts, areas)
    near = nearest_analysis(pts, areas, within).set_index("store_id")
    r = near.loc["L1"]
    assert r["nearest_trdar_cd"] == "C"
    assert abs(r["nearest_distance_m"] - 20.0) < 1e-6


def test_nearest_only_for_unmatched():
    lic = make_lic([("L1", 10.0, 10.0, False, False),
                    ("L2", 400.0, 50.0, False, False)])
    pts = make_points(lic)
    areas = make_areas()
    within = within_join(pts, areas)
    near = nearest_analysis(pts, areas, within)
    assert list(near["store_id"]) == ["L2"]


def test_distance_distribution_and_sensitivity():
    d = pd.Series([5.0, 15.0, 25.0, 45.0, 90.0, 150.0])
    dist = distance_distribution(d)
    assert dist["n"] == 6 and dist["min"] == 5.0 and dist["max"] == 150.0
    sens = threshold_sensitivity(d)
    by_thr = {r["threshold_m"]: r for r in sens}
    assert by_thr[10]["cum_assignable"] == 1
    assert by_thr[20]["added"] == 1
    assert by_thr[100]["cum_assignable"] == 5
    assert by_thr[">100"]["added"] == 1
