# -*- coding: utf-8 -*-
"""서울 상권 polygon 공간조인.

원칙:
- SHP CRS는 파일명/문서가 아니라 실제 .prj/GeoDataFrame.crs로 검증한다 (실측 EPSG:5181).
- 공간연산 전 모든 layer를 EPSG:5179로 명시적으로 통일한다 (projected x/y — lon/lat 아님).
- within 미매칭 점포를 삭제하지 않는다. nearest는 거리·후보 정보만 산출하고
  자동 배정하지 않는다 (threshold는 QA 후 별도 확정).
- 복수 polygon 후보는 임의로 첫 행을 선택하지 않고 ambiguous로 보존한다.
- polygon geometry의 historical consistency(과거 경계 동일 여부)는 미검증이다.
  이 결과는 '현재 확보된 geometry 기준의 spatial assignment'일 뿐이며,
  과거 시점 feature로 사용할 때의 시간 정합성 문제는 여기서 해결하지 않는다.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.validation import make_valid

from src.data.config import CRS_STD, RAW_DIR

SHP_PATH = RAW_DIR / "상권분석" / "영역" / "서울시 상권분석서비스(영역-상권).shp"
EXPECTED_SHP_EPSG = 5181  # 실측 검증값 — 다르면 중단하고 보고

# nearest tie 판정 허용 오차(m): 이 이내 거리 차이는 동일 순위 후보로 센다
NEAREST_TIE_TOL = 0.01

TRDAR_COLS = {
    "TRDAR_CD": "trdar_cd",
    "TRDAR_CD_N": "trdar_name",
    "TRDAR_SE_1": "trdar_type",
}


def load_trade_areas(path=SHP_PATH) -> tuple[gpd.GeoDataFrame, dict]:
    """상권 영역 SHP 로드 + CRS/geometry 검증 + EPSG:5179 통일.

    반환: (GeoDataFrame, 검증 리포트 dict)
    """
    g = gpd.read_file(path)
    report: dict = {}
    epsg = g.crs.to_epsg() if g.crs else None
    report["source_epsg"] = epsg
    if epsg != EXPECTED_SHP_EPSG:
        raise ValueError(
            f"SHP CRS가 예상(EPSG:{EXPECTED_SHP_EPSG})과 다름: {epsg} — 검증 필요")

    report["n_polygons"] = len(g)
    report["geom_types"] = g.geometry.geom_type.value_counts().to_dict()
    report["n_empty"] = int(g.geometry.is_empty.sum())
    report["n_invalid_before"] = int((~g.geometry.is_valid).sum())
    if report["n_empty"]:
        raise ValueError(f"empty geometry {report['n_empty']}건")

    # invalid geometry는 make_valid로 수리 (원본 SHP는 수정하지 않음)
    invalid = ~g.geometry.is_valid
    if invalid.any():
        g.loc[invalid, "geometry"] = g.loc[invalid, "geometry"].apply(make_valid)
    report["n_invalid_after"] = int((~g.geometry.is_valid).sum())

    dup = int(g["TRDAR_CD"].duplicated().sum())
    if dup:
        raise ValueError(f"TRDAR_CD 중복 {dup}건")

    g = g.to_crs(CRS_STD)
    report["bounds_5179"] = [round(float(v), 1) for v in g.total_bounds]
    return g, report


def polygon_overlap_qa(g: gpd.GeoDataFrame, min_area: float = 1.0) -> dict:
    """상권 polygon 간 overlap 현황 (min_area ㎡ 초과 겹침 pair 수)."""
    si = g.sindex
    overlap_pairs = []
    touch_pairs = 0
    for i, geom in enumerate(g.geometry):
        for j in si.query(geom, predicate="intersects"):
            if j <= i:
                continue
            inter = geom.intersection(g.geometry.iloc[j])
            if inter.area > min_area:
                overlap_pairs.append(
                    (g["TRDAR_CD"].iloc[i], g["TRDAR_CD"].iloc[int(j)],
                     round(float(inter.area), 1))
                )
            else:
                touch_pairs += 1
    return {
        "n_overlap_pairs": len(overlap_pairs),
        "n_touch_pairs": touch_pairs,
        "overlap_examples": sorted(overlap_pairs, key=lambda t: -t[2])[:10],
    }


def make_points(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """유효 좌표(결측·suspect 제외) 점포만 EPSG:5179 Point로 만든다."""
    ok = df[~df["coord_missing"] & ~df["coord_suspect"]].copy()
    return gpd.GeoDataFrame(
        ok,
        geometry=gpd.points_from_xy(ok["x_5179"], ok["y_5179"]),
        crs=CRS_STD,
    )


def within_join(points: gpd.GeoDataFrame, areas: gpd.GeoDataFrame) -> pd.DataFrame:
    """predicate='within' 공간조인. store_id별 후보 수를 보존한다.

    후보 1개 → 배정, 2개 이상 → 배정 없이 ambiguous (후보 목록 보존),
    0개 → 미배정. 임의 선택은 하지 않는다.
    """
    j = gpd.sjoin(
        points[["store_id", "geometry"]],
        areas[list(TRDAR_COLS) + ["geometry"]].rename(columns=TRDAR_COLS),
        how="left", predicate="within",
    )
    grp = j.groupby("store_id", sort=False)
    n_cand = grp["trdar_cd"].nunique()

    first = j.drop_duplicates("store_id").set_index("store_id")
    out = pd.DataFrame(index=n_cand.index)
    out["polygon_candidate_count"] = n_cand
    out["in_polygon"] = n_cand > 0
    out["spatial_ambiguous"] = n_cand > 1

    single = n_cand == 1
    for c in ("trdar_cd", "trdar_name", "trdar_type"):
        out[c] = first[c].where(single)
    # ambiguous 후보 목록 (검토용, 최대 5개)
    amb_ids = n_cand.index[n_cand > 1]
    cands = (
        j[j["store_id"].isin(amb_ids)]
        .groupby("store_id")["trdar_cd"]
        .apply(lambda s: "|".join(sorted(s.dropna().unique())[:5]))
    )
    out["candidate_trdar_cds"] = cands
    return out.reset_index()


def boundary_qa(points: gpd.GeoDataFrame, areas: gpd.GeoDataFrame,
                within_res: pd.DataFrame) -> dict:
    """경계선 문제 QA: within 미매칭 점포가 intersects/touches로는 잡히는지 비교.

    기본 규칙(within)은 바꾸지 않고 비교 수치만 산출한다.
    """
    unmatched_ids = set(within_res.loc[~within_res["in_polygon"], "store_id"])
    un_pts = points[points["store_id"].isin(unmatched_ids)]
    if len(un_pts) == 0:
        return {"n_within_unmatched": 0, "n_intersects_extra": 0}
    ji = gpd.sjoin(
        un_pts[["store_id", "geometry"]],
        areas[["TRDAR_CD", "geometry"]],
        how="inner", predicate="intersects",
    )
    return {
        "n_within_unmatched": len(unmatched_ids),
        "n_intersects_extra": int(ji["store_id"].nunique()),
        "note": "within 미매칭 중 intersects로 잡히는 수 = polygon 경계선 위 점포",
    }


def nearest_analysis(points: gpd.GeoDataFrame, areas: gpd.GeoDataFrame,
                     within_res: pd.DataFrame) -> pd.DataFrame:
    """within 미매칭 점포의 nearest 상권 거리 산출 (자동 배정하지 않음)."""
    unmatched_ids = set(within_res.loc[~within_res["in_polygon"], "store_id"])
    un_pts = points[points["store_id"].isin(unmatched_ids)]
    if len(un_pts) == 0:
        return pd.DataFrame(columns=["store_id", "nearest_trdar_cd",
                                     "nearest_distance_m", "nearest_candidate_count"])
    jn = gpd.sjoin_nearest(
        un_pts[["store_id", "geometry"]],
        areas[["TRDAR_CD", "geometry"]].rename(columns={"TRDAR_CD": "nearest_trdar_cd"}),
        how="left", distance_col="nearest_distance_m",
    )
    # sjoin_nearest는 완전 동거리 tie에서 복수 행 반환 → tie 수 보존, 대표는 코드 사전순
    grp = jn.sort_values("nearest_trdar_cd").groupby("store_id", sort=False)
    out = grp.agg(
        nearest_trdar_cd=("nearest_trdar_cd", "first"),
        nearest_distance_m=("nearest_distance_m", "min"),
        nearest_candidate_count=("nearest_trdar_cd", "nunique"),
    ).reset_index()
    return out


def distance_distribution(d: pd.Series) -> dict:
    q = d.quantile
    return {
        "n": int(d.notna().sum()),
        "min": float(d.min()),
        "median": float(d.median()),
        "p75": float(q(0.75)),
        "p90": float(q(0.90)),
        "p95": float(q(0.95)),
        "p99": float(q(0.99)),
        "max": float(d.max()),
    }


def threshold_sensitivity(d: pd.Series,
                          thresholds=(10, 20, 30, 50, 100)) -> list[dict]:
    rows = []
    prev = 0
    for t in thresholds:
        n = int((d <= t).sum())
        rows.append({"threshold_m": t, "cum_assignable": n, "added": n - prev})
        prev = n
    rows.append({"threshold_m": f">{thresholds[-1]}",
                 "cum_assignable": int(d.notna().sum()),
                 "added": int((d > thresholds[-1]).sum())})
    return rows
