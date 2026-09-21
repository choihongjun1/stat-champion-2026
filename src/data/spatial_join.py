# -*- coding: utf-8 -*-
"""인허가 점포 공간조인(상권 polygon) + 개별공시지가 결합 파이프라인.

실행:
    python -m src.data.spatial_join

전제: outputs/standardized/licenses_3gu.parquet (address-normalization 산출물).

산출물 (outputs/spatial/, git 미추적):
    - spatial_joined.parquet
    - spatial_validation_sample.csv
    - spatial_validation_review.csv   (검증 표본 d1/d2/gap/ratio 재계산)
    - qa_report.md
    - nearest_gap_validation.png / nearest_case_maps.png  (QA figure)

base assignment 규칙: **within-only**. within 미매칭 점포의 trdar_cd는 비운다.
nearest 관련 컬럼(d1/d2/gap/ratio/provisional flag)은 QA·sensitivity provenance이며
상권 배정에 사용하지 않는다 (validation 결과 — docs/DECISIONS.md 2026-09-19).

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
    nearest2 = spatial.nearest_two_analysis(points, areas, within)

    out = lic.copy()
    out = out.merge(within, on="store_id", how="left")
    out = out.merge(nearest, on="store_id", how="left")
    out = out.merge(nearest2, on="store_id", how="left")
    assert len(out) == len(lic), "행 손실 금지"

    # 1순위 독립 재계산과 sjoin_nearest 결과의 정합성 (geometry artifact 점검)
    chk = out[out["nearest_distance_m"].notna()
              & out["nearest_distance_m_chk"].notna()]
    nearest_check = {
        "n_compared": len(chk),
        "max_abs_distance_diff_m": float(
            (chk["nearest_distance_m"] - chk["nearest_distance_m_chk"]).abs().max()
        ) if len(chk) else 0.0,
        "n_trdar_cd_mismatch": int(
            (chk["nearest_trdar_cd"] != chk["nearest_trdar_cd_chk"]).sum()
        ) if len(chk) else 0,
        "n_missing_second": int(out.loc[out["nearest_distance_m"].notna(),
                                        "second_nearest_distance_m"].isna().sum()),
    }

    # spatial_match_method: 배정/제외/미매칭 사유를 명시
    method = pd.Series("unmatched_within", index=out.index, dtype="object")
    method[out["in_polygon"] == True] = "within"  # noqa: E712
    method[out["spatial_ambiguous"] == True] = "within_ambiguous"  # noqa: E712
    method[out["coord_suspect"] == True] = "excluded_coord_suspect"  # noqa: E712
    method[out["coord_missing"] == True] = "excluded_coord_missing"  # noqa: E712
    out["spatial_match_method"] = method
    out["in_polygon"] = out["in_polygon"].fillna(False).astype(bool)
    out["spatial_ambiguous"] = out["spatial_ambiguous"].fillna(False).astype(bool)
    out["nearest_candidate_high_conf_provisional"] = (
        out["nearest_candidate_high_conf_provisional"].fillna(False).astype(bool)
    )

    # base assignment = within-only 보증: within으로 확정되지 않은 행의 trdar_cd는 비어야
    # 한다. nearest/provisional flag가 배정에 새어 들어가지 않았음을 하드 체크한다.
    not_within = out["spatial_match_method"] != "within"
    assert out.loc[not_within, "trdar_cd"].isna().all(), (
        "within-only 위반: within 미확정 행에 trdar_cd가 채워졌다")
    assert out.loc[out["nearest_candidate_high_conf_provisional"],
                   "trdar_cd"].isna().all(), (
        "provisional QA flag가 상권 배정에 사용되었다")

    out, lp_qas = landprice.join_landprice(out)
    assert out["store_id"].is_unique, "store_id 1행 보장 실패"
    return out, shp_report, overlap, boundary, lp_qas, nearest_check


def write_qa(out, shp_report, overlap, boundary, lp_qas, nearest_check,
             review: pd.DataFrame | None = None) -> None:
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    d = out["nearest_distance_m"]

    L = ["# 공간조인 + 공시지가 QA 리포트", ""]
    L.append("생성: `python -m src.data.spatial_join`")
    L.append("")
    L.append("> **base assignment = within-only.** within 미매칭 점포의 상권 feature는")
    L.append("> NA가 된다. nearest 관련 값(d1/d2/gap/ratio/provisional flag)은 QA·sensitivity")
    L.append("> provenance이며 상권 배정에 사용하지 않는다. 근거: 검증 표본 재계산에서")
    L.append("> 짧은 거리에도 경쟁 polygon이 존재해 absolute nearest-distance 단독")
    L.append("> threshold를 base assignment 근거로 삼기 어려웠다 (docs/DECISIONS.md 2026-09-19).")
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
        t = row["threshold_m"]
        label = f"{t}m" if isinstance(t, str) else f"≤{t}m"
        L.append(f"| {label} | {row['cum_assignable']:,} | {row['added']:,} |")
    L.append("")
    L.append("위 표는 **가정적 sensitivity**다. 실제로는 어떤 threshold로도 자동 배정하지")
    L.append("않는다 (base = within-only). ER의 좌표 반경 30m와는 무관한 별개 개념이다.")
    L.append("")

    L.append("### 1·2순위 분리도 (d1/d2/gap/ratio — QA 전용)")
    L.append("")
    L.append(f"- 1순위 독립 재계산 교차검증: 비교 {nearest_check['n_compared']:,}건, "
             f"거리 최대 절대차 {nearest_check['max_abs_distance_diff_m']:.6f}m, "
             f"상권코드 불일치 {nearest_check['n_trdar_cd_mismatch']:,}건 "
             "(동거리 tie에서 대표 선택이 갈리는 경우만 허용)")
    L.append(f"- 2순위 후보 없음: {nearest_check['n_missing_second']:,}건")
    sep_all = spatial.separation_summary(out[out["nearest_distance_m"].notna()])
    L.append(f"- 전체 within 미매칭 분포: d1={sep_all['d1']}, gap={sep_all['gap']}, "
             f"ratio={sep_all['ratio']}")
    L.append(f"- provisional QA flag "
             f"(d1≤{spatial.PROVISIONAL_QA_D1_MAX_M:.0f}m AND "
             f"gap≥{spatial.PROVISIONAL_QA_GAP_MIN_M:.0f}m) 해당: "
             f"{sep_all['n_high_conf_provisional']:,}건")
    L.append("")
    L.append("| d1 구간 | n | gap median | gap p90 | ratio median | provisional flag |")
    L.append("|---|---|---|---|---|---|")
    bands = [(0, 20), (20, 50), (50, 100), (100, float("inf"))]
    un = out[out["nearest_distance_m"].notna()]
    for lo, hi in bands:
        sub = un[(un["nearest_distance_m"] > lo) & (un["nearest_distance_m"] <= hi)]
        if len(sub) == 0:
            continue
        s = spatial.separation_summary(sub)
        label = f"{lo}~{hi:.0f}m" if hi != float("inf") else f">{lo}m"
        L.append(f"| {label} | {len(sub):,} | {s['gap'].get('median')} "
                 f"| {s['gap'].get('p90')} | {s['ratio'].get('median')} "
                 f"| {s['n_high_conf_provisional']:,} |")
    L.append("")
    L.append(f"`nearest_candidate_high_conf_provisional`의 {spatial.PROVISIONAL_QA_D1_MAX_M:.0f}m/"
             f"{spatial.PROVISIONAL_QA_GAP_MIN_M:.0f}m 기준은 **provisional QA 기준**이며")
    L.append("통계적으로 확정된 최종 threshold가 아니다. ratio는 보조 지표로 저장만 하며")
    L.append("ratio threshold는 정의하지 않는다. 어떤 값도 상권 배정에 사용하지 않는다.")
    L.append("")

    if review is not None and len(review):
        L.append("### 검증 표본 재계산 (SHP 직접 재계산)")
        L.append("")
        L.append(f"- 표본 {len(review)}건의 그룹 구성: "
                 f"{review['group'].value_counts().sort_index().to_dict()}")
        rv = review[review["nearest_distance_m_recomputed"].notna()]
        if len(rv):
            L.append(f"- 저장값 대비 d1 최대 절대차: "
                     f"{rv['d1_abs_diff_m'].max():.6f}m / 상권코드 불일치 "
                     f"{int(rv['nearest_trdar_cd_mismatch'].sum()):,}건")
        L.append("")
        L.append("| group | n | d1 median | gap median | gap min | ratio median | provisional flag |")
        L.append("|---|---|---|---|---|---|---|")
        for gname, g in review.groupby("group", sort=False):
            gv = g[g["nearest_distance_m_recomputed"].notna()]
            if len(gv) == 0:
                L.append(f"| {gname} | {len(g)} | - | - | - | - | - |")
                continue
            L.append(
                f"| {gname} | {len(g)} "
                f"| {gv['nearest_distance_m_recomputed'].median():.2f} "
                f"| {gv['nearest_gap_m'].median():.2f} "
                f"| {gv['nearest_gap_m'].min():.2f} "
                f"| {gv['nearest_ratio'].median():.3f} "
                f"| {int(gv['nearest_candidate_high_conf_provisional'].sum())} |"
            )
        L.append("")
        L.append("판정 주의: d1이 짧다는 사실만으로 '올바른 배정'이라고 판정하지 않았다.")
        L.append("도로 건너편·서로 다른 상권 사이·대형 단지·polygon gap에서는 짧은 거리도")
        L.append("의미상 ambiguous하며, 현 표본만으로 nearest 배정의 의미적 ground truth를")
        L.append("확보했다고 보지 않는다.")
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
    zero_cols = {y: int((out[f"land_price_{y}"] == 0).sum()) for y in landprice.YEARS}
    L.append("### 0원 필지 (raw 보존 + valid 파생, Issue #9)")
    L.append("")
    L.append(f"- 점포에 결합된 0원 값: {zero_cols}")
    L.append(f"- `land_price_zero_flag` 점포: {int(out['land_price_zero_flag'].sum()):,}")
    L.append("- raw `land_price_{year}`는 0을 그대로 보존한다. feature로는 0을 제외한")
    L.append("  `land_price_{year}_valid`를 쓴다 (0은 유효 지가가 아니라 값이 빈 상태).")
    for y in landprice.YEARS:
        v = out[f"land_price_{y}_valid"]
        L.append(f"  - {y}: valid {int(v.notna().sum()):,}건 "
                 f"(raw 결합 {int(out[f'land_price_{y}'].notna().sum()):,}건)")
    L.append("")

    L.append("### 시간 메타데이터 (feature_asof ≠ available_at)")
    L.append("")
    L.append("| 연도 | reference_year | feature_asof (기준일) | available_at (공시일) | 출처 |")
    L.append("|---|---|---|---|---|")
    for y, m in landprice.LANDPRICE_META.items():
        L.append(f"| {y} | {m['reference_year']} | {m['feature_asof']} "
                 f"| {m['available_at']} | [{m['available_at_source']}]"
                 f"({m['available_at_source_url']}) |")
    L.append("")
    for y, s in landprice.AVAILABLE_AT_SOURCES.items():
        L.append(f"- {y} 출처: {s['title']} — {s['note']}")
    L.append("- `feature_asof`는 가격의 기준일, `available_at`은 그 값을 예측자가 실제로 알 수")
    L.append("  있게 된 결정·공시일이다. **둘을 혼동하면 leakage다.**")
    L.append("- prediction origin이 해당 연도 available_at 이전이면 그 연도 값 사용 금지.")
    L.append("  예) origin 2026-03-31 → land_price_2026 사용 금지(이전 연도 값 사용),")
    L.append("  origin ≥ 2026-04-30 → land_price_2026 사용 가능.")
    L.append("- origin별 feature selection 로직은 이 PR 범위 밖이며 W2에서 위 메타데이터로 판단한다.")
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


def build_validation_review(sample: pd.DataFrame, areas) -> pd.DataFrame:
    """검증 표본을 SHP에서 **독립 재계산**해 저장값과 대조한다.

    파이프라인 산출물이 아니라 표본의 x/y 좌표와 SHP만으로 within·d1·d2를 다시 계산해
    저장값과 비교한다. 정답/오답 자동 판정은 하지 않고 대조 가능한 표만 만든다.
    """
    import geopandas as gpd

    from src.data.config import CRS_STD

    s = sample.copy()
    # 상권코드는 문자열 코드다. CSV에서 숫자로 읽히면 SHP의 문자열 코드와 비교가
    # 전부 불일치로 나오므로 비교 전에 문자열로 정규화한다 (NaN은 NaN 유지).
    for c in ("trdar_cd", "nearest_trdar_cd"):
        s[c] = s[c].map(lambda v: None if pd.isna(v) else str(v).strip())
    pts = gpd.GeoDataFrame(
        s[["store_id"]].copy(),
        geometry=gpd.points_from_xy(s["x_5179"], s["y_5179"]),
        crs=CRS_STD,
    )
    # 1) within 재계산 (inside_polygon 표본이 실제 polygon 내부인지 확인)
    w = spatial.within_join(pts, areas).set_index("store_id")
    s["within_trdar_cd_recomputed"] = s["store_id"].map(w["trdar_cd"])
    s["in_polygon_recomputed"] = s["store_id"].map(w["in_polygon"]).fillna(False)
    s["within_trdar_cd_match"] = (
        s["trdar_cd"].isna() & s["within_trdar_cd_recomputed"].isna()
    ) | (s["trdar_cd"] == s["within_trdar_cd_recomputed"])

    # 2) 1·2순위 재계산 (within 성공 표본 제외 — 모두 미매칭으로 두고 계산)
    fake_within = pd.DataFrame({
        "store_id": s["store_id"],
        "in_polygon": s["store_id"].map(w["in_polygon"]).fillna(False).astype(bool),
    })
    n2 = spatial.nearest_two_analysis(pts, areas, fake_within).set_index("store_id")
    for c in ["second_nearest_trdar_cd", "second_nearest_distance_m",
              "nearest_gap_m", "nearest_ratio",
              "nearest_candidate_high_conf_provisional"]:
        s[c] = s["store_id"].map(n2[c]) if len(n2) else pd.NA
    s["nearest_trdar_cd_recomputed"] = (
        s["store_id"].map(n2["nearest_trdar_cd_chk"]) if len(n2) else pd.NA)
    s["nearest_distance_m_recomputed"] = (
        s["store_id"].map(n2["nearest_distance_m_chk"]) if len(n2) else pd.NA)
    s["d1_abs_diff_m"] = (
        pd.to_numeric(s["nearest_distance_m"], errors="coerce")
        - pd.to_numeric(s["nearest_distance_m_recomputed"], errors="coerce")
    ).abs()
    s["nearest_trdar_cd_mismatch"] = (
        s["nearest_trdar_cd"].notna()
        & s["nearest_trdar_cd_recomputed"].notna()
        & (s["nearest_trdar_cd"] != s["nearest_trdar_cd_recomputed"])
    )
    return s


def run() -> pd.DataFrame:
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    lic_path = OUTPUT_DIR / "licenses_3gu.parquet"
    if not lic_path.exists():
        raise FileNotFoundError(
            f"{lic_path} 없음 — 먼저 `python -m src.data.standardize` 실행 필요")
    lic = pd.read_parquet(lic_path)[LIC_COLS]
    print(f"인허가 입력: {len(lic):,} rows")

    out, shp_report, overlap, boundary, lp_qas, nearest_check = build(lic)

    out.to_parquet(SPATIAL_DIR / "spatial_joined.parquet", index=False)
    print(f"저장: {SPATIAL_DIR / 'spatial_joined.parquet'} ({len(out):,} rows)")

    sample = build_validation_sample(out)
    sample_path = SPATIAL_DIR / "spatial_validation_sample.csv"
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")
    print(f"검증 표본: {len(sample)}건 -> spatial_validation_sample.csv")

    # 저장된 표본 파일을 다시 읽어 SHP에서 독립 재계산 (산출물 round-trip 포함 검증)
    areas, _ = spatial.load_trade_areas()
    review = build_validation_review(
        pd.read_csv(sample_path, dtype={"pnu": str, "trdar_cd": str,
                                        "nearest_trdar_cd": str,
                                        "candidate_trdar_cds": str}),
        areas)
    review.to_csv(SPATIAL_DIR / "spatial_validation_review.csv", index=False,
                  encoding="utf-8-sig")
    print(f"검증 재계산: {len(review)}건 -> spatial_validation_review.csv")

    write_qa(out, shp_report, overlap, boundary, lp_qas, nearest_check, review)

    try:
        from src.data import spatial_qa_viz

        figs = spatial_qa_viz.make_all(out, review, areas, SPATIAL_DIR)
        for f in figs:
            print(f"QA figure: {f}")
    except ImportError as e:  # matplotlib 미설치 환경에서도 파이프라인은 성공해야 한다
        print(f"QA figure 생략 (matplotlib 없음): {e}")
    return out


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
