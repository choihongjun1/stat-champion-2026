# -*- coding: utf-8 -*-
"""W2-0 master_base 조립.

실행:
    python -m src.data.master

`outputs/labels/labels_base.parquet`의 `(store_id, origin)` 패널을 축으로 feature 원천을
LEFT JOIN해 모델 입력용 한 장의 테이블을 만든다. 산출물(outputs/master/, git 미추적):
    - master_base.parquet
    - qa_report.md

원칙:
- 행을 절대 늘리거나 지우지 않는다. 모든 조인은 m:1이며 매칭 실패는 NA로 둔다
  (complete-case 금지, DECISIONS.md 2026-09-13).
- 조인 단계마다 행수 / store_id 수 / (store_id, origin) 중복 / origin별 event 비율 /
  label 값 / temporal leakage를 검사하고 결과를 step log로 남긴다. 하나라도 깨지면 멈춘다.
- 컬럼 역할은 `master_schema.COLUMN_ROLES`가 단일 출처다. ER 결과는 provenance 전용이다.
- 온라인 존재감은 Base에 넣지 않는다. Enriched는 이 테이블에 `(store_id, origin)` 단위
  m:1 테이블을 LEFT JOIN하는 방식으로 확장한다 (`attach_enriched_table`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data import config, landprice, trdar_features
from src.data import master_schema as schema

SPATIAL_PATH = config.REPO_ROOT / "outputs" / "spatial" / "spatial_joined.parquet"
ER_PATH = config.REPO_ROOT / "outputs" / "matching" / "license_semas_matches.parquet"

SPATIAL_COLS = ["store_id", "gu", "pnu", "coord_missing", "coord_suspect", "trdar_cd",
                "trdar_type", "in_polygon", "spatial_ambiguous", "spatial_match_method"]
TRDAR_FEATURE_COLS = [c for spec in trdar_features.AREA_SERIES.values()
                      for c in spec["columns"].values()]
TRDAR_ASOF_COLS = ["trdar_value_asof"] + [
    spec["asof_column"] for spec in trdar_features.AREA_SERIES.values() if spec["frozen"]]
LAND_PRICE_VALID_COLS = [f"land_price_{y}_valid" for y in landprice.YEARS]
ER_COLS = {
    "store_id": "store_id",
    "gu_mismatch": "gu_mismatch",
    "parse_status": "parse_status",
    "matched": "er_matched",
    "ambiguous": "er_ambiguous",
    "match_tier": "match_tier",
    "match_confidence": "match_confidence",
    "crowded_pnu": "crowded_pnu",
    "sj_entity_id": "sj_entity_id",
    "unmatched_reason": "unmatched_reason",
}


class MasterValidationError(AssertionError):
    """master 조립 불변식 위반."""


# ---------------------------------------------------------------------------
# 단계별 검증
# ---------------------------------------------------------------------------
def label_baseline(panel: pd.DataFrame) -> dict:
    """조인 전 기준값. 이후 모든 단계는 이 값과 비교한다."""
    return {
        "rows": len(panel),
        "stores": panel["store_id"].nunique(),
        "event_by_origin": panel.groupby("origin")["event_12m"].agg(["size", "mean"]),
        "labels": panel[schema.LABEL_COLUMNS].sort_values(schema.KEY).reset_index(drop=True),
    }


def temporal_leakage_counts(df: pd.DataFrame) -> dict[str, int]:
    """시점 정합성 위반 건수. 전부 0이어야 한다.

    `available_at > origin_end`인 non-null 값과, 금지 목록에 걸리는 predictor 등록을 센다.
    feature group이 아직 붙지 않았으면 그 group의 검사는 건너뛴다.
    """
    end = df["origin_end"]
    out = {
        "license available_at > origin_end": int((df["available_at"] > end).sum()),
        "forbidden predictor registered": len(schema.forbidden_predictors_in_registry()),
    }
    if "land_price_available_at" in df.columns:
        out["land_price available_at > origin_end"] = int(
            (df["land_price_available_at"] > end).sum())
        out["land_price value without available_at"] = int(
            (df["land_price"].notna() & df["land_price_available_at"].isna()).sum())
    if "trdar_quarter_used" in df.columns:
        used = pd.PeriodIndex(df["trdar_quarter_used"], freq="Q")
        origin = pd.PeriodIndex(df["origin"], freq="Q")
        out["trdar_quarter_used >= origin"] = int((used >= origin).sum())
        out["trdar available_at > origin_end"] = int((df["trdar_available_at"] > end).sum())
        for col in TRDAR_ASOF_COLS:
            asof = df[col].dropna()
            out[f"{col} > trdar_quarter_used"] = int(
                (asof > df.loc[asof.index, "trdar_quarter_used"]).sum())
    return out


def biz_integrity_counts(df: pd.DataFrame) -> dict[str, int]:
    """업종 단위 상권 feature 불변식 위반 건수 (W2-0 I-1). 전부 0이어야 한다."""
    if "trdar_biz_store_n_codes_observed" not in df.columns:
        return {}
    feats = trdar_features.BIZ_FEATURES
    has_feat = df[feats].notna().any(axis=1)
    out = {
        "biz feature where trdar_cd NA": int((has_feat & df["trdar_cd"].isna()).sum()),
        "biz feature in origin 2021Q1 (T-1 원천 부재)": int((has_feat & (df["origin"] == "2021Q1")).sum()),
        "biz meta where trdar_cd NA": int(
            (df["trdar_biz_store_n_codes_observed"].notna() & df["trdar_cd"].isna()).sum()),
    }
    for src in ("store", "sales"):
        obs = df[f"trdar_biz_{src}_n_codes_observed"]
        exp = df[f"trdar_biz_{src}_n_codes_expected"]
        cov = df[f"trdar_biz_{src}_code_coverage"]
        part = df[f"trdar_biz_{src}_is_partial"]
        known = obs.notna()
        out[f"{src} observed > expected"] = int((obs > exp).fillna(False).sum())
        out[f"{src} coverage outside [0, 1]"] = int(((cov < 0) | (cov > 1)).fillna(False).sum())
        out[f"{src} is_partial != (observed < expected)"] = int(
            (part[known] != (obs[known] < exp[known])).sum())
        out[f"{src} expected != mapping size"] = int(
            (exp[known] != df.loc[known, "biz_type"].map(trdar_features.BIZ_N_CODES_EXPECTED)).sum())
    feats_by_src = {"store": trdar_features.BIZ_STORE_FEATURES,
                    "sales": trdar_features.BIZ_SALES_FEATURES}
    for src, cols in feats_by_src.items():
        none_observed = df[f"trdar_biz_{src}_n_codes_observed"] == 0
        out[f"{src} feature with 0 observed codes"] = int(
            (df[cols].notna().any(axis=1) & none_observed.fillna(False)).sum())
    ssc = df["trdar_biz_sales_store_coverage"]
    out["sales_store_coverage outside [0, 1]"] = int(((ssc < 0) | (ssc > 1)).fillna(False).sum())
    per_store = df["trdar_biz_sales_per_store_observed"].astype("float64")
    out["sales_per_store inf"] = int(np.isinf(per_store).sum())
    out["sales_per_store without sales_amt"] = int(
        (df["trdar_biz_sales_per_store_observed"].notna()
         & df["trdar_biz_sales_amt_observed"].isna()).sum())
    return out


def verify_step(step: str, df: pd.DataFrame, baseline: dict, log: list[dict]) -> None:
    """조인 단계 불변식 검사 → log에 한 줄 추가. 하나라도 깨지면 MasterValidationError."""
    dup = int(df.duplicated(schema.KEY).sum())
    ev = df.groupby("origin")["event_12m"].agg(["size", "mean"])
    ev_ok = ev.equals(baseline["event_by_origin"])
    ev_diff = float((ev["mean"] - baseline["event_by_origin"]["mean"]).abs().max())
    labels_now = df[schema.LABEL_COLUMNS].sort_values(schema.KEY).reset_index(drop=True)
    labels_ok = labels_now.equals(baseline["labels"])
    leakage = temporal_leakage_counts(df)
    n_leak = sum(leakage.values())
    integrity = biz_integrity_counts(df)
    n_integrity = sum(integrity.values())

    row = {
        "step": step,
        "rows": len(df),
        "stores": df["store_id"].nunique(),
        "dup_key": dup,
        "event_rate_max_abs_diff": ev_diff,
        "event_by_origin_equal": ev_ok,
        "labels_unchanged": labels_ok,
        "leakage_violations": n_leak,
        "integrity_violations": n_integrity,
        "n_columns": df.shape[1],
    }
    log.append(row)
    problems = []
    if row["rows"] != baseline["rows"]:
        problems.append(f"rows {baseline['rows']} -> {row['rows']}")
    if row["stores"] != baseline["stores"]:
        problems.append(f"stores {baseline['stores']} -> {row['stores']}")
    if dup:
        problems.append(f"(store_id, origin) dup={dup}")
    if not ev_ok:
        problems.append(f"origin별 event 비율 변화 (max diff {ev_diff})")
    if not labels_ok:
        problems.append("label/panel 컬럼 값 변화")
    if n_leak:
        problems.append(f"temporal leakage {leakage}")
    if n_integrity:
        problems.append(f"biz feature integrity { {k: v for k, v in integrity.items() if v} }")
    if problems:
        raise MasterValidationError(f"[{step}] " + "; ".join(problems))


def _merge_m1(left: pd.DataFrame, right: pd.DataFrame, on, name: str) -> pd.DataFrame:
    """m:1 LEFT JOIN. 오른쪽 키가 유일하지 않으면 pandas가 MergeError로 멈춘다."""
    overlap = (set(left.columns) & set(right.columns)) - set([on] if isinstance(on, str) else on)
    if overlap:
        raise MasterValidationError(f"[{name}] 컬럼 충돌: {sorted(overlap)}")
    return left.merge(right, on=on, how="left", validate="m:1")


# ---------------------------------------------------------------------------
# 원천별 결합
# ---------------------------------------------------------------------------
def attach_store_attributes(panel: pd.DataFrame, spatial: pd.DataFrame) -> pd.DataFrame:
    """점포 정적 속성 + 상권 배정(within-only). spatial_joined는 store_id 유일."""
    return _merge_m1(panel, spatial[SPATIAL_COLS], "store_id", "spatial")


def attach_er_provenance(panel: pd.DataFrame, er: pd.DataFrame) -> pd.DataFrame:
    """ER 결과를 provenance로만 붙인다. 매칭 실패 점포도 그대로 남는다."""
    right = er[list(ER_COLS)].rename(columns=ER_COLS)
    return _merge_m1(panel, right, "store_id", "er")


def attach_land_price(panel: pd.DataFrame, spatial: pd.DataFrame) -> pd.DataFrame:
    """개별공시지가 strict as-of (DECISIONS.md 2026-09-23 W2-0 I-3).

    행마다 `available_at(y) <= origin_end`인 최대 연도 y를 고르고 그 연도의 `_valid` 값(0원 → NA)
    하나만 `land_price`로 둔다. 해당 연도가 없으면(origin < 첫 공시일) 전부 NA — 이후 연도 값을
    과거로 소급하지 않는다. 선택 연도 값이 NA여도 다른 연도로 대체(carry-forward)하지 않는다.
    연도별 wide 컬럼은 master에 남기지 않는다 (사용 불가 연도 값이 섞여 있어 누수 위험).
    """
    df = _merge_m1(panel, spatial[["store_id"] + LAND_PRICE_VALID_COLS], "store_id", "land_price")
    end = df["origin_end"]
    year_used = pd.Series(pd.NA, index=df.index, dtype="Int64")
    for y in sorted(landprice.YEARS):
        year_used = year_used.mask(end >= pd.Timestamp(landprice.LANDPRICE_META[y]["available_at"]), y)

    value = pd.Series(pd.NA, index=df.index, dtype="Float64")
    for y in landprice.YEARS:
        m = year_used == y
        value = value.mask(m.fillna(False), df[f"land_price_{y}_valid"].astype("Float64"))
    df["land_price"] = value
    df["land_price_year_used"] = year_used

    def _meta(key: str) -> pd.Series:
        mapping = {y: m[key] for y, m in landprice.LANDPRICE_META.items()}
        return pd.to_datetime(year_used.map(mapping, na_action="ignore"))

    df["land_price_feature_asof"] = _meta("feature_asof")
    df["land_price_available_at"] = _meta("available_at")
    # Int64 → lambda map은 값을 float로 넘기므로('2024.0') dict로 매핑한다.
    files = {y: landprice.LANDPRICE_FILE.format(year=y) for y in landprice.YEARS}
    df["land_price_source_snapshot"] = year_used.map(files, na_action="ignore").astype("object")
    return df.drop(columns=LAND_PRICE_VALID_COLS)


def attach_trdar_features(panel: pd.DataFrame, table: pd.DataFrame, source_snapshot: str,
                          lag_quarters: int = schema.TRDAR_LAG_QUARTERS) -> pd.DataFrame:
    """상권 단위 feature를 origin 분기 T의 T-lag 값으로 붙인다 (기본 T-1).

    origin 분기 T 자체의 값은 origin_end에 아직 공표되지 않았으므로 쓰지 않는다. T-lag 분기가
    원천에 없으면(origin 2021Q1 → 2020Q4) 값은 NA다. trdar_cd가 없는 점포(within 미매칭·좌표
    결측)도 NA. `trdar_available_at`은 확인된 published_at만 담고, 나머지는 NA + basis다.
    """
    if lag_quarters < 1:
        raise ValueError("origin 분기 T 자체의 상권 값은 사용 금지 (lag >= 1)")
    df = panel.copy()
    used = pd.PeriodIndex(df["origin"], freq="Q") - lag_quarters
    df["trdar_quarter_used"] = used.astype(str)
    right = table.rename(columns={"quarter": "trdar_quarter_used"})
    df = _merge_m1(df, right, ["trdar_cd", "trdar_quarter_used"], "trdar")
    no_area = df["trdar_cd"].isna()
    if df.loc[no_area, TRDAR_FEATURE_COLS].notna().any().any():
        raise MasterValidationError("trdar_cd 없는 행에 상권 feature가 채워졌다")

    has_quarterly = df[["trdar_flow_pop", "trdar_change_index"]].notna().any(axis=1)
    df["trdar_value_asof"] = df["trdar_quarter_used"].where(has_quarterly)
    avail = trdar_features.availability(df["trdar_quarter_used"])
    df["trdar_available_at"] = avail["trdar_available_at"]
    df["trdar_available_at_basis"] = avail["trdar_available_at_basis"]
    df["trdar_source_snapshot"] = source_snapshot
    snapshot = pd.Timestamp(schema.TRDAR_GEOMETRY_SNAPSHOT)
    df["trdar_geometry_snapshot"] = snapshot
    df["trdar_geometry_backcast_flag"] = df["origin_end"] < snapshot
    return df


def attach_trdar_biz_features(panel: pd.DataFrame, table: pd.DataFrame,
                              source_snapshot: str) -> pd.DataFrame:
    """업종 단위 상권 feature (점포·추정매출, W2-0 I-1)를 T-1 분기로 붙인다.

    key는 `(trdar_cd, trdar_quarter_used, biz_type)` — 분기는 `attach_trdar_features`가 만든
    T-1 분기를 그대로 쓰므로 source quarter는 항상 T-1이다. biz_type은 인허가 종류라 origin 이후
    정보가 없다. observed partial 집계이며 row 없는 코드를 0으로 채우지 않는다
    (`trdar_features.aggregate_biz`).
    """
    if "trdar_quarter_used" not in panel.columns:
        raise MasterValidationError("attach_trdar_features(T-1)를 먼저 실행해야 한다")
    keys = ["trdar_cd", "quarter", "biz_type"]
    if table.duplicated(keys).any():
        raise MasterValidationError("biz table (trdar_cd, quarter, biz_type) 중복")
    right = table.rename(columns={"quarter": "trdar_quarter_used"})
    df = _merge_m1(panel, right, ["trdar_cd", "trdar_quarter_used", "biz_type"], "trdar_biz")
    df["trdar_biz_source_snapshot"] = source_snapshot
    return df


def attach_enriched_table(master: pd.DataFrame, table: pd.DataFrame,
                          name: str) -> pd.DataFrame:
    """Enriched 확장 인터페이스 (Base에서는 호출하지 않는다).

    table은 `(store_id, origin)` 유일이어야 하고, 값은 origin_end 이전 정보만 집계한 것이어야
    한다 (예: 온라인 블로그 월별 건수 중 month_end <= origin_end인 달만). 시점 메타는
    `{group}_feature_asof` / `{group}_available_at` / `{group}_source_snapshot`로 함께 넣는다.
    """
    return _merge_m1(master, table, schema.KEY, name)


# ---------------------------------------------------------------------------
# 조립 / 검증 / 저장
# ---------------------------------------------------------------------------
def validate_schema(df: pd.DataFrame) -> None:
    registered = list(schema.COLUMN_ROLES)
    missing = [c for c in registered if c not in df.columns]
    extra = [c for c in df.columns if c not in schema.COLUMN_ROLES]
    if missing or extra:
        raise MasterValidationError(f"schema 불일치: missing={missing} extra={extra}")


def build_master_base(labels: pd.DataFrame, spatial: pd.DataFrame, er: pd.DataFrame,
                      trdar: pd.DataFrame, trdar_source_snapshot: str,
                      trdar_biz: pd.DataFrame, trdar_biz_source_snapshot: str,
                      ) -> tuple[pd.DataFrame, list[dict]]:
    """labels_base 축 LEFT JOIN 조립. (master, step log) 반환."""
    log: list[dict] = []
    baseline = label_baseline(labels)
    df = labels.copy()
    verify_step("0 labels_base", df, baseline, log)

    df = attach_store_attributes(df, spatial)
    verify_step("1 +store attributes / trdar assignment", df, baseline, log)

    df = attach_er_provenance(df, er)
    verify_step("2 +ER provenance", df, baseline, log)

    df = attach_land_price(df, spatial)
    verify_step("3 +land price (strict as-of)", df, baseline, log)

    df = attach_trdar_features(df, trdar, trdar_source_snapshot)
    verify_step("4 +trdar area features (T-1)", df, baseline, log)

    df = attach_trdar_biz_features(df, trdar_biz, trdar_biz_source_snapshot)
    verify_step("5 +trdar biz features (T-1, observed)", df, baseline, log)

    df = df[list(schema.COLUMN_ROLES)]
    validate_schema(df)
    return df, log


def missing_rate_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """predictor 결측률: 전체 / origin별."""
    cols = schema.predictor_columns(df.columns)
    overall = df[cols].isna().mean().rename("missing_rate").to_frame()
    by_origin = df.groupby("origin")[cols].apply(lambda g: g.isna().mean())
    return overall, by_origin


def _md(df: pd.DataFrame, index: bool = True, floatfmt: str = ".4f") -> str:
    """tabulate는 pd.NA를 처리하지 못한다 → nullable 컬럼의 NA를 None으로 바꿔 넘긴다."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_extension_array_dtype(out[c].dtype):
            out[c] = out[c].astype(object).where(out[c].notna(), None)
    return out.to_markdown(index=index, floatfmt=floatfmt)


def trdar_origin_table(df: pd.DataFrame) -> pd.DataFrame:
    """origin별 상권 T-1 분기 / 공표 근거 / 결합 현황."""
    g = df.groupby("origin")
    out = g.agg(
        quarter_used=("trdar_quarter_used", "first"),
        basis=("trdar_available_at_basis", "first"),
        available_at=("trdar_available_at", "first"),
        backcast=("trdar_geometry_backcast_flag", "first"),
        rows=("store_id", "size"),
        rows_with_trdar_cd=("trdar_cd", "count"),
        rows_with_flow_pop=("trdar_flow_pop", "count"),
    )
    out["available_at"] = out["available_at"].dt.strftime("%Y-%m-%d")
    return out


BIZ_WARNINGS = [
    "**[점포 row 부재 — structural zero 근거, 공식 명세 아님]** 점포 원천에서 mapped code의 row 부재는 "
    "시계열 전이, 명시적 0 row, 매출 원천과의 교차검증 및 연도별 패턴상 점포 0을 의미하는 것으로 해석할 "
    "강한 실증 근거가 있다. 다만 원천 공식 명세로 확인된 규칙은 아니므로 raw row를 임의 생성하거나 0으로 "
    "imputation하지 않고 observed-row 집계와 coverage metadata를 유지한다. "
    "(분석적 해석: structural zero 근거 있음 / 물리적 처리: missing row를 0 row로 만들지 않음)",
    "**[매출 row 부재 ≠ 0]** 매출 0원 row는 원천에 없고, 점포 1~2개 코드는 매출 row가 100% 없다. "
    "소수 점포 코드의 매출 비공개/억제로 판단한다. `trdar_biz_sales_amt_observed`는 매출이 공개된 mapped "
    "code의 합계이며 biz_type 전체 매출의 **하한/부분관측치**일 수 있다. 과소 정도는 "
    "`trdar_biz_sales_store_coverage`로 행마다 확인한다.",
    "**[broad biz_type 매핑 한계]** 매핑 key는 인허가 종류(biz_type)다. 휴게음식점 중 편의점(업태 기준 "
    "1,158개 점포), 일반음식점 '까페'·'기타', 미용업 '메이크업업' 등은 실제 세부 업종과 다른 그룹 값을 받는다. "
    "현재 시점 `업태구분명`으로 예외를 판정하면 시간 기준 불확실성이 생기므로 예외를 만들지 않았다.",
    "**[2021~22 재발행본]** 2021~2022 점포·추정매출 CSV는 2023-10-30 재발행본이며 과거 origin 시점에 공개된 "
    "값과 같다고 보지 않는다 (DATA_CATALOG.md §3-B).",
    "**[계열별 공표일 미확인]** `trdar_available_at`은 golmok 서비스 전체 업데이트일이다. 점포·추정매출 "
    "계열의 공표일을 별도로 확인하지 않았으며 같은 일정으로 본다.",
    "**[율 > 100%]** 개업·폐업률은 원천 정의(건수 / 분기 말 전체 점포 수 × 100)를 합계 건수로 재계산한 값이다. "
    "분기 중 폐업이 분기 말 점포 수보다 많으면 100%를 넘으며 원천 `폐업_률`에도 100% 초과가 있다. 자르지 않는다.",
]


def biz_qa_lines(df: pd.DataFrame, biz_qa: dict | None) -> list[str]:
    """업종 단위 상권 feature QA 섹션."""
    mapping = pd.DataFrame(
        [(b, c) for b, codes in trdar_features.BIZ_CODE_MAP.items() for c in codes],
        columns=["biz_type", "서비스_업종_코드"])
    dup_codes = int(mapping["서비스_업종_코드"].duplicated().sum())
    known = df["trdar_biz_store_n_codes_observed"].notna()
    k = df[known]

    def cov_table(col):
        return pd.crosstab(k["biz_type"], k[col].astype(float).round(3), normalize="index")

    ssc = k.groupby("biz_type")["trdar_biz_sales_store_coverage"].describe(
        percentiles=[.1, .25, .5, .75, .9])
    feats = trdar_features.BIZ_FEATURES
    miss_origin = df.groupby("origin")[feats].apply(lambda g: g.isna().mean())
    miss_biz = df.groupby("biz_type")[feats].apply(lambda g: g.isna().mean())
    partial = k.groupby("biz_type")[["trdar_biz_store_is_partial", "trdar_biz_sales_is_partial"]].mean()
    integ = pd.Series(biz_integrity_counts(df), name="violations").to_frame()
    lines = [
        "## 업종 단위 상권 feature (W2-0 I-1)",
        "",
        "### 매핑표",
        "",
        f"CS 코드 중복 배정: **{dup_codes}건**. key는 인허가 종류(`biz_type`)이며 `업태구분명`·`위생업태명`·"
        "소진공 cat3는 쓰지 않는다.",
        "",
        _md(mapping, index=False),
        "",
        "### WARNING",
        "",
        *[f"- {w}" for w in BIZ_WARNINGS],
        "",
        "### 불변식 (전부 0이어야 한다)",
        "",
        _md(integ),
        "",
    ]
    if biz_qa:
        counts = biz_qa["evidence"]["counts"]
        ev = pd.Series(counts, name="count").to_frame()
        direct = counts["전이: 점포>0 → row 없음 (근거라면 0)"]
        via_zero = counts["전이: 점포>0 → 명시적 0"]
        lines += [
            "### row 부재 실증 (서울 전체 상권 × 22분기 × mapped code)",
            "",
            "점포: '점포>0 → row 없음' 전이와 '점포 row 없음 & 매출 row 있음'이 0에 가까울수록 "
            "structural zero 해석 근거가 강하다. 3구 상권 한정 실측(2026-09-23)에서는 두 값 모두 0이었다. "
            f"서울 전체에서는 점포>0 이후 사라진 전이 {direct + via_zero:,}건 중 {via_zero:,}건"
            f"({via_zero / max(direct + via_zero, 1):.1%})이 명시적 0 row를 거쳤고 {direct}건은 예외다 "
            "→ 근거는 강하지만 예외 없는 규칙은 아니다.",
            "",
            _md(ev, floatfmt=".0f"),
            "",
            "매출 row 부재율 vs 해당 코드 점포 수 (suppression 근거):",
            "",
            _md(biz_qa["evidence"]["sales_suppression"]),
            "",
            "연도별 row 부재율 (특정 연도 집중 여부):",
            "",
            _md(biz_qa["evidence"]["by_year"].T),
            "",
            f"매출 row가 있는데 점포 row가 없는 코드: {biz_qa['n_sales_code_without_store']}건 "
            "(있으면 해당 그룹의 점포당 매출과 sales_store_coverage는 NA).",
            "",
            "원천 파일:",
            "",
            _md(pd.DataFrame(biz_qa["files"]), index=False),
            "",
        ]
    lines += [
        "### code coverage 분포 (master 행, 메타가 있는 행 기준)",
        "",
        "점포:",
        "",
        _md(cov_table("trdar_biz_store_code_coverage")),
        "",
        "매출:",
        "",
        _md(cov_table("trdar_biz_sales_code_coverage")),
        "",
        "partial 비율:",
        "",
        _md(partial),
        "",
        "### sales_store_coverage 분포",
        "",
        _md(ssc),
        "",
        "### 결측률 (biz_type별)",
        "",
        _md(miss_biz),
        "",
        "### 결측률 (origin별)",
        "",
        _md(miss_origin),
        "",
    ]
    return lines


def write_qa_report(df: pd.DataFrame, log: list[dict], trdar_qas: list[dict] | None = None,
                    biz_qa: dict | None = None, path=config.MASTER_QA_REPORT_PATH) -> None:
    overall, by_origin = missing_rate_tables(df)
    leak = pd.Series(temporal_leakage_counts(df), name="violations").to_frame()
    roles = pd.DataFrame(
        [(c, r, g) for c, (r, g) in schema.COLUMN_ROLES.items()],
        columns=["column", "role", "group"],
    )
    er_rate = df.groupby("er_matched")["event_12m"].agg(["size", "mean"])
    lp_by_origin = df.groupby("origin").agg(
        year_used=("land_price_year_used", "max"),
        rows=("land_price", "size"),
        non_null=("land_price", "count"),
    )
    lines = [
        "# master_base QA report",
        "",
        "`python -m src.data.master`가 생성한다. 모든 단계는 labels_base 기준값과 비교했다.",
        "",
        "## 단계별 검증",
        "",
        _md(pd.DataFrame(log), index=False, floatfmt=".6f"),
        "",
        "## Temporal leakage (전부 0이어야 한다)",
        "",
        _md(leak),
        "",
        "## Predictor 결측률 (전체)",
        "",
        _md(overall),
        "",
        "## Predictor 결측률 (origin별)",
        "",
        _md(by_origin),
        "",
        "## 공시지가 strict as-of (origin별 선택 연도)",
        "",
        "선택 연도가 없는 origin은 구조적 NA다 (소급·carry-forward 없음).",
        "",
        _md(lp_by_origin),
        "",
        "## 상권 T-1 (origin별)",
        "",
        "`available_at`은 확인된 published_at만 기록한다. 비어 있는 origin은 T-1 규칙(관측 최대",
        "lag 83일 < T-1 종료~origin_end 90~92일)으로 방어한다. origin 2021Q1의 T-1(2020Q4)은 원천에 없다.",
        "",
        _md(trdar_origin_table(df)),
        "",
        "## 상권 원천 파일",
        "",
        _md(pd.DataFrame(trdar_qas or []), index=False),
        "",
        *biz_qa_lines(df, biz_qa),
        "## ER 누수 경고 지표 (provenance 전용 근거)",
        "",
        "`er_matched`는 2024-12~2026-06 스냅샷 union으로 계산된다. 아래 event 비율 격차가",
        "predictor로 쓰면 안 되는 이유다.",
        "",
        _md(er_rate),
        "",
        "## 컬럼 역할",
        "",
        _md(roles, index=False),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_master_spec_md(df: pd.DataFrame, path=config.MASTER_SPEC_PATH) -> None:
    """docs/MASTER_SPEC.md 생성 (LABEL_SPEC.md와 같은 생성 문서). 손으로 고치지 않는다."""
    rows = [
        {
            "변수명": c,
            "role": role,
            "group": group,
            "타입": str(df[c].dtype),
            "결측률(%)": round(float(df[c].isna().mean()) * 100, 2),
            "정의": schema.VARIABLE_DEFINITIONS[c],
        }
        for c, (role, group) in schema.COLUMN_ROLES.items()
    ]
    lines = [
        "# master_base 명세 (MASTER_SPEC)",
        "",
        "`python -m src.data.master`가 생성한다. 손으로 고치지 말고 "
        "`src/data/master_schema.py`를 고친 뒤 다시 생성한다.",
        "",
        f"- 관측 단위: `(store_id, origin)` / {len(df):,}행 / store_id {df['store_id'].nunique():,} / "
        f"origin {df['origin'].nunique()}개 ({df['origin'].min()}~{df['origin'].max()})",
        f"- predictor {len(schema.predictor_columns(df.columns))}개 / 전체 컬럼 {df.shape[1]}개",
        "",
        "## 메모",
        "",
        *[f"- {n}" for n in schema.SPEC_NOTES],
        "",
        "## 변수",
        "",
        pd.DataFrame(rows).to_markdown(index=False),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def load_inputs() -> dict:
    trdar, trdar_qas = trdar_features.build_area_quarter_table()
    trdar_biz, biz_qa = trdar_features.build_biz_quarter_table()
    return {
        "labels": pd.read_parquet(config.LABELS_BASE_PATH),
        "spatial": pd.read_parquet(SPATIAL_PATH, columns=SPATIAL_COLS + LAND_PRICE_VALID_COLS),
        "er": pd.read_parquet(ER_PATH, columns=list(ER_COLS)),
        "trdar": trdar,
        "trdar_source_snapshot": trdar.attrs["source_snapshot"],
        "trdar_qas": trdar_qas,
        "trdar_biz": trdar_biz,
        "trdar_biz_source_snapshot": trdar_biz.attrs["source_snapshot"],
        "biz_qa": biz_qa,
    }


def run() -> pd.DataFrame:
    from src.data import w1_invariants

    w1_invariants.run()  # W2 진입 게이트: freeze 상태와 다르면 여기서 멈춘다
    inputs = load_inputs()
    master, log = build_master_base(inputs["labels"], inputs["spatial"], inputs["er"],
                                    inputs["trdar"], inputs["trdar_source_snapshot"],
                                    inputs["trdar_biz"], inputs["trdar_biz_source_snapshot"])
    print(pd.DataFrame(log).to_string(index=False))
    config.MASTER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    master.to_parquet(config.MASTER_BASE_PATH, index=False)
    write_qa_report(master, log, trdar_qas=inputs["trdar_qas"], biz_qa=inputs["biz_qa"])
    write_master_spec_md(master)
    print(f"\n저장: {config.MASTER_BASE_PATH} {master.shape}")
    return master


if __name__ == "__main__":
    run()
