# -*- coding: utf-8 -*-
"""서울 상권 polygon 공간조인.

원칙:
- SHP CRS는 파일명/문서가 아니라 실제 .prj/GeoDataFrame.crs로 검증한다 (실측 EPSG:5181).
- 공간연산 전 모든 layer를 EPSG:5179로 명시적으로 통일한다 (projected x/y — lon/lat 아님).
- **base spatial assignment는 within-only다.** within 미매칭 점포를 삭제하지 않으며,
  nearest 관련 값(d1/d2/gap/ratio)은 QA·sensitivity provenance로만 보존한다.
  validation 결과 absolute nearest-distance 단독 threshold는 base assignment 근거로
  충분하지 않다고 판단했다 (짧은 거리에서도 경쟁 polygon이 존재) — 이 모듈의 어떤
  nearest 값도 trdar_cd를 채우는 데 사용하지 않는다.
- 복수 polygon 후보는 임의로 첫 행을 선택하지 않고 ambiguous로 보존한다.
- polygon geometry의 historical consistency(과거 경계 동일 여부)는 미검증이다.
  이 결과는 '현재 확보된 geometry 기준의 spatial assignment'일 뿐이며,
  과거 시점 feature로 사용할 때의 시간 정합성 문제는 여기서 해결하지 않는다.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.validation import make_valid

from src.data.config import CRS_STD, RAW_DIR

SHP_PATH = RAW_DIR / "상권분석" / "영역" / "서울시 상권분석서비스(영역-상권).shp"
EXPECTED_SHP_EPSG = 5181  # 실측 검증값 — 다르면 중단하고 보고

# nearest tie 판정 허용 오차(m): 이 이내 거리 차이는 동일 순위 후보로 센다
NEAREST_TIE_TOL = 0.01

# 2순위 후보 탐색 반경(m). 후보가 2개 미만이면 다음 반경으로 확장한다.
NEAREST_SEARCH_RADII_M = (100.0, 500.0, 2000.0, 20000.0)

# --- provisional QA rule (확정 threshold 아님) -------------------------------
# 아래 두 값은 "d1이 짧고 2순위와 충분히 떨어진 사례"를 세어 보기 위한
# provisional QA 기준일 뿐이며, 통계적으로 확정된 최종 threshold가 아니다.
# 이 기준으로 만들어지는 flag는 상권 배정에 절대 사용하지 않는다.
PROVISIONAL_QA_D1_MAX_M = 20.0
PROVISIONAL_QA_GAP_MIN_M = 20.0

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


def nearest_two_analysis(
    points: gpd.GeoDataFrame,
    areas: gpd.GeoDataFrame,
    within_res: pd.DataFrame,
    search_radii=NEAREST_SEARCH_RADII_M,
) -> pd.DataFrame:
    """within 미매칭 점포의 1·2순위 상권과 분리도 지표를 계산한다 (QA 전용).

    반환 컬럼:
      nearest_trdar_cd_chk / nearest_distance_m_chk  — 독립 재계산된 1순위 (교차검증용)
      second_nearest_trdar_cd / second_nearest_distance_m — 2순위
      nearest_gap_m   = d2 - d1
      nearest_ratio   = d1 / d2   (0 <= ratio <= 1, 1에 가까울수록 후보 분리 나쁨)
      nearest_candidate_high_conf_provisional — provisional QA flag (배정 금지)

    후보는 TRDAR_CD 단위로 dedupe한다. 같은 상권의 MultiPolygon 파트가 1·2순위를
    동시에 차지하는 geometry artifact를 막기 위함이다. 2순위 후보가 없으면 NA.
    """
    empty_cols = ["store_id", "nearest_trdar_cd_chk", "nearest_distance_m_chk",
                  "second_nearest_trdar_cd", "second_nearest_distance_m",
                  "nearest_gap_m", "nearest_ratio",
                  "nearest_candidate_high_conf_provisional"]
    unmatched_ids = set(within_res.loc[~within_res["in_polygon"], "store_id"])
    un_pts = points[points["store_id"].isin(unmatched_ids)].reset_index(drop=True)
    if len(un_pts) == 0 or len(areas) == 0:
        return pd.DataFrame(columns=empty_cols)

    pt_geoms = un_pts.geometry.to_numpy()
    area_geoms = areas.geometry.to_numpy()
    area_codes = areas["TRDAR_CD"].to_numpy()
    sindex = areas.sindex

    best: dict[int, list[tuple[float, str]]] = {}
    pending = np.arange(len(un_pts))
    for radius in search_radii:
        if len(pending) == 0:
            break
        buffers = shapely.buffer(pt_geoms[pending], radius)
        left, right = sindex.query(buffers, predicate="intersects")
        if len(left):
            dists = shapely.distance(pt_geoms[pending][left], area_geoms[right])
            cand = pd.DataFrame({
                "pos": pending[left],
                "trdar_cd": area_codes[right],
                "dist": dists,
            })
            # 같은 상권코드는 최소거리 1건으로 축약 (MultiPolygon 파트 중복 방지)
            cand = cand.groupby(["pos", "trdar_cd"], sort=False)["dist"].min().reset_index()
            cand = cand.sort_values(["pos", "dist"], kind="stable")
            for pos, g in cand.groupby("pos", sort=False):
                best[int(pos)] = list(zip(g["dist"].to_numpy(),
                                          g["trdar_cd"].to_numpy()))[:2]
        pending = np.array([p for p in pending if len(best.get(int(p), [])) < 2])

    rows = []
    for pos in range(len(un_pts)):
        cands = best.get(pos, [])
        d1 = float(cands[0][0]) if len(cands) >= 1 else np.nan
        c1 = cands[0][1] if len(cands) >= 1 else None
        d2 = float(cands[1][0]) if len(cands) >= 2 else np.nan
        c2 = cands[1][1] if len(cands) >= 2 else None
        gap = d2 - d1 if len(cands) >= 2 else np.nan
        ratio = (d1 / d2) if (len(cands) >= 2 and d2 > 0) else np.nan
        rows.append({
            "store_id": un_pts["store_id"].iloc[pos],
            "nearest_trdar_cd_chk": c1,
            "nearest_distance_m_chk": d1,
            "second_nearest_trdar_cd": c2,
            "second_nearest_distance_m": d2,
            "nearest_gap_m": gap,
            "nearest_ratio": ratio,
            "nearest_candidate_high_conf_provisional": bool(
                (not np.isnan(d1)) and d1 <= PROVISIONAL_QA_D1_MAX_M
                and (not np.isnan(gap)) and gap >= PROVISIONAL_QA_GAP_MIN_M
            ),
        })
    out = pd.DataFrame(rows, columns=empty_cols)
    # geometry artifact 방어: 2순위가 존재하면 서로 다른 상권이고 d2 >= d1이어야 한다
    both = out["second_nearest_trdar_cd"].notna()
    if both.any():
        assert (out.loc[both, "second_nearest_trdar_cd"]
                != out.loc[both, "nearest_trdar_cd_chk"]).all(), "1·2순위 상권코드 중복"
        assert (out.loc[both, "nearest_gap_m"] >= -NEAREST_TIE_TOL).all(), "d2 < d1"
    return out


def separation_summary(df: pd.DataFrame) -> dict:
    """d1/gap/ratio 분포 요약 (QA 리포트·검증 표본 공용)."""
    def _q(s: pd.Series) -> dict:
        s = s.dropna()
        if s.empty:
            return {"n": 0}
        return {
            "n": int(len(s)), "min": round(float(s.min()), 2),
            "median": round(float(s.median()), 2),
            "p90": round(float(s.quantile(0.90)), 2),
            "max": round(float(s.max()), 2),
        }
    return {
        "d1": _q(df.get("nearest_distance_m", pd.Series(dtype=float))),
        "gap": _q(df.get("nearest_gap_m", pd.Series(dtype=float))),
        "ratio": _q(df.get("nearest_ratio", pd.Series(dtype=float))),
        "n_high_conf_provisional": int(
            df.get("nearest_candidate_high_conf_provisional",
                   pd.Series(dtype=bool)).fillna(False).sum()
        ),
    }


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
