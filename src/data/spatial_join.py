# -*- coding: utf-8 -*-
"""인허가 점포 공간조인(상권 polygon) + 개별공시지가 결합 파이프라인.

실행:
    python -m src.data.spatial_join

전제: outputs/standardized/licenses_3gu.parquet (address-normalization 산출물).

산출물 (outputs/spatial/, git 미추적):
    - spatial_joined.parquet
    - spatial_validation_sample.csv
    - qa_report.md

범위 제한: 소진공 ER / 상권 feature 결합 / 폐업 label / master_v1은 수행하지 않는다.
상권 polygon의 historical consistency는 미검증이며, 이 결과는 현재 확보된
geometry snapshot 기준의 assignment다 (QA 리포트에 명시).
"""
from __future__ import annotations

import sys

import pandas as pd

from src.data import landprice, spatial
from src.data.config import OUTPUT_DIR, REPO_ROOT, TARGET_GUS

SPATIAL_DIR = REPO_ROOT / "outputs" / "spatial"

SAMPLE_SEED = 20260916
SAMPLE_PER_GROUP = 8

LIC_COLS = ["store_id", "business_type", "gu", "dong", "pnu", "addr_raw",
            "name_raw", "status_name", "x_5179", "y_5179",
            "coord_missing", "coord_suspect"]


def build(lic: pd.DataFrame):
    """공간조인 + 공시지가 결합 실행. (결과, QA 조각들) 반환."""
    areas, shp_report = spatial.load_trade_areas()
    overlap = spatial.polygon_overlap_qa(areas)

    points = spatial.make_points(lic)
    within = spatial.within_join(points, areas)
    boundary = spatial.boundary_qa(points, areas, within)
    nearest = spatial.nearest_analysis(points, areas, within)

    out = lic.copy()
    out = out.merge(within, on="store_id", how="left")
    out = out.merge(nearest, on="store_id", how="left")
    assert len(out) == len(lic), "행 손실 금지"

    # spatial_match_method: 배정/제외/미매칭 사유를 명시
    method = pd.Series("unmatched_within", index=out.index, dtype="object")
    method[out["in_polygon"] == True] = "within"  # noqa: E712
    method[out["spatial_ambiguous"] == True] = "within_ambiguous"  # noqa: E712
    method[out["coord_suspect"] == True] = "excluded_coord_suspect"  # noqa: E712
    method[out["coord_missing"] == True] = "excluded_coord_missing"  # noqa: E712
    out["spatial_match_method"] = method
    out["in_polygon"] = out["in_polygon"].fillna(False).astype(bool)
    out["spatial_ambiguous"] = out["spatial_ambiguous"].fillna(False).astype(bool)

    out, lp_qas = landprice.join_landprice(out)
    assert out["store_id"].is_unique, "store_id 1행 보장 실패"
    return out, shp_report, overlap, boundary, lp_qas


def write_qa(out, shp_report, overlap, boundary, lp_qas) -> None:
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    d = out["nearest_distance_m"]

    L = ["# 공간조인 + 공시지가 QA 리포트", ""]
    L.append("생성: `python -m src.data.spatial_join`")
    L.append("")
    L.append("> **한계 명시**: 상권 polygon geometry는 단일 스냅샷이며 historical")
    L.append("> consistency(과거 기간 경계 동일 여부)는 **미검증**이다. 본 결과는 현재")
    L.append("> geometry 기준 spatial assignment이며, 과거 origin의 feature로 사용할 때의")
    L.append("> 시간 정합성 문제는 이 PR에서 다루지 않는다.")
    L.append("")

    L.append("## SHP 검증")
    L.append("")
    L.append(f"- source CRS: EPSG:{shp_report['source_epsg']} (실측 .prj/crs 확인) → EPSG:5179로 변환")
    L.append(f"- polygon {shp_report['n_polygons']}개, geometry type {shp_report['geom_types']}")
    L.append(f"- empty {shp_report['n_empty']} / invalid {shp_report['n_invalid_before']}건 "
             f"→ make_valid 후 invalid {shp_report['n_invalid_after']}건 (원본 미수정)")
    L.append(f"- 5179 bounds: {shp_report['bounds_5179']}")
    L.append(f"- polygon 간 overlap(>1㎡): {overlap['n_overlap_pairs']}쌍 "
             f"/ 경계 접촉: {overlap['n_touch_pairs']}쌍")
    L.append(f"- overlap 상위 예시 (cd1, cd2, ㎡): {overlap['overlap_examples'][:5]}")
    L.append("")

    n = len(out)
    valid = ~out["coord_missing"] & ~out["coord_suspect"]
    L.append("## Spatial join 결과")
    L.append("")
    L.append(f"- 전체 점포 {n:,} / 유효 좌표 {int(valid.sum()):,} "
             f"/ 좌표 결측 {int(out['coord_missing'].sum()):,} "
             f"({out['coord_missing'].mean():.4f}) "
             f"/ coord_suspect {int(out['coord_suspect'].sum()):,}")
    method_counts = out["spatial_match_method"].value_counts()
    for k, v in method_counts.items():
        L.append(f"- {k}: {v:,}")
    inp = out[valid]
    L.append(f"- within 매칭률 (유효 좌표 기준): {inp['in_polygon'].mean():.4f} "
             f"/ 전체 점포 기준: {out['in_polygon'].mean():.4f}")
    L.append("")
    L.append("### 구별 within 매칭률 (유효 좌표 기준)")
    L.append("")
    L.append("| gu | n | within | rate |")
    L.append("|---|---|---|---|")
    for g_, gdf in inp.groupby("gu"):
        L.append(f"| {g_} | {len(gdf):,} | {int(gdf['in_polygon'].sum()):,} "
                 f"| {gdf['in_polygon'].mean():.4f} |")
    L.append("")
    L.append("### 업종별 within 매칭률 (유효 좌표 기준)")
    L.append("")
    L.append("| business_type | n | within | rate |")
    L.append("|---|---|---|---|")
    for b, gdf in inp.groupby("business_type"):
        L.append(f"| {b} | {len(gdf):,} | {int(gdf['in_polygon'].sum()):,} "
                 f"| {gdf['in_polygon'].mean():.4f} |")
    L.append("")

    cc = inp["polygon_candidate_count"].fillna(0).astype(int)
    L.append("### polygon 후보 수 분포 (유효 좌표 점포)")
    L.append("")
    L.append(f"- 0개: {(cc == 0).sum():,} / 1개: {(cc == 1).sum():,} "
             f"/ 2개 이상: {(cc >= 2).sum():,} (ambiguous — 임의 선택 없이 보존)")
    if (cc >= 2).any():
        L.append(f"- 2개 이상 분포: {cc[cc >= 2].value_counts().sort_index().to_dict()}")
    L.append("")
    L.append("### 경계선(boundary) QA")
    L.append("")
    L.append(f"- within 미매칭 (유효 좌표): {boundary['n_within_unmatched']:,}")
    L.append(f"- 그중 intersects로는 매칭되는 점포(=경계선 위): {boundary['n_intersects_extra']:,}")
    L.append("  (기본 규칙은 within 유지 — 경계 사례는 결과 보고만)")
    L.append("")

    L.append("### Nearest 거리 분포 (within 미매칭, 자동 배정 없음)")
    L.append("")
    dist = spatial.distance_distribution(d.dropna())
    L.append(f"- {dist}")
    L.append("")
    L.append("| threshold | 누적 배정 가능 | 추가 |")
    L.append("|---|---|---|")
    for row in spatial.threshold_sensitivity(d.dropna()):
        L.append(f"| ≤{row['threshold_m']}m | {row['cum_assignable']:,} | {row['added']:,} |")
    L.append("")
    L.append("주의: nearest threshold는 이 PR에서 확정하지 않는다. ER의 좌표 반경 30m와는")
    L.append("무관한 별개 개념이다. 위 sensitivity와 검증 표본으로 별도 결정한다.")
    L.append("")

    L.append("## 개별공시지가")
    L.append("")
    L.append("| 연도 | 행수 | unique PNU | 불량 PNU(≠19자리) | 중복행 | 값충돌 PNU(제외) "
             "| 결측 | 0 | 음수 | PNU 보유 점포 매칭률 | 전체 점포 매칭률 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for qa in lp_qas:
        L.append(
            f"| {qa['year']} | {qa['rows']:,} | {qa['unique_pnu']:,} "
            f"| {qa['invalid_pnu']:,} | {qa['dup_pnu_rows']:,} "
            f"| {qa['excluded_conflicting_pnus']:,} "
            f"| {qa['price_nan']:,} | {qa['price_zero']:,} | {qa['price_negative']:,} "
            f"| {qa['store_match_with_pnu']:.4f} | {qa['store_match_all']:.4f} |"
        )
    L.append("")
    for qa in lp_qas:
        L.append(f"- {qa['year']}: 기준년도 {qa['reference_years_in_file']}, "
                 f"기준년월 {qa['reference_months_in_file']}, "
                 f"공시지가 중앙값 {qa['price_median']:,.0f}원/㎡ "
                 f"(p99 {qa['price_p99']:,.0f}, max {qa['price_max']:,.0f})")
    L.append("")
    L.append("### 구별 공시지가 매칭률 (어느 한 해라도 매칭)")
    L.append("")
    L.append("| gu | n | matched | rate |")
    L.append("|---|---|---|---|")
    for g_, gdf in out.groupby("gu"):
        L.append(f"| {g_} | {len(gdf):,} | {int(gdf['land_price_match'].sum()):,} "
                 f"| {gdf['land_price_match'].mean():.4f} |")
    L.append("")
    L.append("### 시간 누수 메타데이터 (확정 아님)")
    L.append("")
    for y, m in landprice.LANDPRICE_META.items():
        L.append(f"- {y}: reference_year={m['reference_year']}, "
                 f"feature_asof={m['feature_asof']}, available_at={m['available_at']}")
    L.append("")
    L.append("기준년월(1/1)은 reference date이며 실제 공시일(available_at)은 raw로 확인")
    L.append("불가 — NEEDS_VERIFICATION. origin별 feature availability는 여기서 확정하지 않는다.")
    L.append("")

    (SPATIAL_DIR / "qa_report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"QA 리포트: {SPATIAL_DIR / 'qa_report.md'}")


def build_validation_sample(out: pd.DataFrame) -> pd.DataFrame:
    """공간조인 수작업 검증 표본 (정답/오답 자동 판정 없음)."""
    d = out["nearest_distance_m"]
    groups = {
        "inside_polygon": out[out["spatial_match_method"] == "within"],
        "ambiguous_multi_polygon": out[out["spatial_match_method"] == "within_ambiguous"],
        "boundary_near_0m": out[(out["spatial_match_method"] == "unmatched_within")
                                & (d < 1.0)],
        "outside_0_20m": out[(out["spatial_match_method"] == "unmatched_within")
                             & d.between(1.0, 20.0)],
        "outside_20_50m": out[(out["spatial_match_method"] == "unmatched_within")
                              & d.between(20.0, 50.0, inclusive="right")],
        "outside_50_100m": out[(out["spatial_match_method"] == "unmatched_within")
                               & d.between(50.0, 100.0, inclusive="right")],
        "outside_over_100m": out[(out["spatial_match_method"] == "unmatched_within")
                                 & (d > 100.0)],
        "coord_suspect": out[out["spatial_match_method"] == "excluded_coord_suspect"],
    }
    cols = ["store_id", "business_type", "name_raw", "addr_raw", "gu", "dong",
            "x_5179", "y_5179", "trdar_cd", "trdar_name", "candidate_trdar_cds",
            "nearest_trdar_cd", "nearest_distance_m", "spatial_match_method",
            "pnu", "land_price_2024", "land_price_2025", "land_price_2026"]
    rows = []
    for gname, g in groups.items():
        if len(g) == 0:
            continue
        s = g.sample(n=min(SAMPLE_PER_GROUP, len(g)), random_state=SAMPLE_SEED)
        s = s[cols].copy()
        s.insert(0, "group", gname)
        rows.append(s)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def run() -> pd.DataFrame:
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    lic_path = OUTPUT_DIR / "licenses_3gu.parquet"
    if not lic_path.exists():
        raise FileNotFoundError(
            f"{lic_path} 없음 — 먼저 `python -m src.data.standardize` 실행 필요")
    lic = pd.read_parquet(lic_path)[LIC_COLS]
    print(f"인허가 입력: {len(lic):,} rows")

    out, shp_report, overlap, boundary, lp_qas = build(lic)

    out.to_parquet(SPATIAL_DIR / "spatial_joined.parquet", index=False)
    print(f"저장: {SPATIAL_DIR / 'spatial_joined.parquet'} ({len(out):,} rows)")

    sample = build_validation_sample(out)
    sample.to_csv(SPATIAL_DIR / "spatial_validation_sample.csv", index=False,
                  encoding="utf-8-sig")
    print(f"검증 표본: {len(sample)}건 -> spatial_validation_sample.csv")

    write_qa(out, shp_report, overlap, boundary, lp_qas)
    return out


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
