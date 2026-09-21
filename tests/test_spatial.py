# -*- coding: utf-8 -*-
"""공간조인 로직 테스트 (합성 polygon/point — raw 데이터 불필요)."""
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from src.data.config import CRS_STD
from src.data.spatial import (
    PROVISIONAL_QA_D1_MAX_M,
    PROVISIONAL_QA_GAP_MIN_M,
    distance_distribution,
    make_points,
    nearest_analysis,
    nearest_two_analysis,
    separation_summary,
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


def _n2(lic_rows, areas=None, **kw):
    """nearest_two_analysis를 within 결과와 함께 실행하는 헬퍼."""
    areas = make_areas() if areas is None else areas
    pts = make_points(make_lic(lic_rows))
    within = within_join(pts, areas)
    return nearest_two_analysis(pts, areas, within, **kw).set_index("store_id")


def test_nearest_two_d1_d2_gap_ratio():
    # (170, 50): C(x≤150)까지 20m, B(x≥200)까지 30m
    r = _n2([("L1", 170.0, 50.0, False, False)]).loc["L1"]
    assert r["nearest_trdar_cd_chk"] == "C"
    assert abs(r["nearest_distance_m_chk"] - 20.0) < 1e-6
    assert r["second_nearest_trdar_cd"] == "B"
    assert abs(r["second_nearest_distance_m"] - 30.0) < 1e-6
    assert abs(r["nearest_gap_m"] - 10.0) < 1e-6          # gap = d2 - d1
    assert abs(r["nearest_ratio"] - (20.0 / 30.0)) < 1e-9  # ratio = d1 / d2
    # d1은 20m 이내지만 gap이 20m 미만 → provisional flag False
    assert not r["nearest_candidate_high_conf_provisional"]


def test_nearest_two_provisional_flag_true():
    # (160, 50): C까지 10m, B까지 40m → d1≤20 & gap≥20
    r = _n2([("L1", 160.0, 50.0, False, False)]).loc["L1"]
    assert r["nearest_distance_m_chk"] <= PROVISIONAL_QA_D1_MAX_M
    assert r["nearest_gap_m"] >= PROVISIONAL_QA_GAP_MIN_M
    assert r["nearest_candidate_high_conf_provisional"]


def test_nearest_two_no_second_candidate_is_na():
    # 후보가 1개뿐이면 d2/gap/ratio는 NA이고 flag는 False여야 한다
    single = gpd.GeoDataFrame(
        {"TRDAR_CD": ["A"], "TRDAR_CD_N": ["상권A"], "TRDAR_SE_1": ["골목상권"]},
        geometry=[Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])],
        crs=CRS_STD,
    )
    r = _n2([("L1", 200.0, 50.0, False, False)], areas=single,
            search_radii=(150.0,)).loc["L1"]
    assert abs(r["nearest_distance_m_chk"] - 100.0) < 1e-6
    assert r["second_nearest_trdar_cd"] is None
    assert pd.isna(r["second_nearest_distance_m"])
    assert pd.isna(r["nearest_gap_m"]) and pd.isna(r["nearest_ratio"])
    assert not r["nearest_candidate_high_conf_provisional"]


def test_nearest_two_dedupes_multipolygon_parts():
    # 같은 상권코드의 MultiPolygon 파트가 1·2순위를 동시에 차지하면 안 된다
    from shapely.geometry import MultiPolygon

    areas = gpd.GeoDataFrame(
        {"TRDAR_CD": ["A", "M"], "TRDAR_CD_N": ["상권A", "상권M"],
         "TRDAR_SE_1": ["골목상권", "발달상권"]},
        geometry=[
            Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]),
            MultiPolygon([
                Polygon([(200, 0), (210, 0), (210, 100), (200, 100)]),
                Polygon([(220, 0), (230, 0), (230, 100), (220, 100)]),
            ]),
        ],
        crs=CRS_STD,
    )
    # (190, 50): M 근처 파트 10m, M 먼 파트 30m, A 90m → 2순위는 A(90m)
    r = _n2([("L1", 190.0, 50.0, False, False)], areas=areas).loc["L1"]
    assert r["nearest_trdar_cd_chk"] == "M"
    assert abs(r["nearest_distance_m_chk"] - 10.0) < 1e-6
    assert r["second_nearest_trdar_cd"] == "A"
    assert abs(r["second_nearest_distance_m"] - 90.0) < 1e-6


def test_nearest_two_only_for_within_unmatched():
    # within 성공점은 nearest QA 대상이 아니다 (기존 base assignment 동작 유지)
    res = _n2([("L1", 10.0, 10.0, False, False),     # A 내부
               ("L2", 170.0, 50.0, False, False)])   # 미매칭
    assert list(res.index) == ["L2"]


def test_nearest_two_does_not_assign_trdar_cd():
    # nearest QA를 추가해도 within 실패점의 base assignment는 비어 있어야 한다
    areas = make_areas()
    pts = make_points(make_lic([("L1", 160.0, 50.0, False, False)]))
    within = within_join(pts, areas).set_index("store_id")
    n2 = nearest_two_analysis(pts, areas, within.reset_index())
    assert pd.isna(within.loc["L1", "trdar_cd"])
    assert not within.loc["L1", "in_polygon"]
    assert "trdar_cd" not in n2.columns  # 배정 컬럼을 만들지 않는다
    assert bool(n2.set_index("store_id").loc[
        "L1", "nearest_candidate_high_conf_provisional"])


def test_nearest_two_excludes_coord_missing_and_suspect():
    res = _n2([("L1", 170.0, 50.0, False, False),
               ("L2", None, None, True, False),
               ("L3", 170.0, 50.0, False, True)])
    assert list(res.index) == ["L1"]


def test_separation_summary_shape():
    df = pd.DataFrame({
        "nearest_distance_m": [5.0, 15.0, 60.0],
        "nearest_gap_m": [30.0, 2.0, 10.0],
        "nearest_ratio": [0.14, 0.88, 0.86],
        "nearest_candidate_high_conf_provisional": [True, False, False],
    })
    s = separation_summary(df)
    assert s["d1"]["n"] == 3 and s["d1"]["median"] == 15.0
    assert s["gap"]["median"] == 10.0
    assert s["n_high_conf_provisional"] == 1


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
