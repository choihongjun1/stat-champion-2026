# -*- coding: utf-8 -*-
"""개별공시지가(2024·2025·2026) 결합.

원칙:
- 토지코드(=PNU)는 문자열로 처리하고 leading zero를 보존하며 19자리를 검증한다.
- 19자리가 아닌 불량 코드는 제외하고 건수를 기록한다 (DATA_CATALOG 실측: 연 ~200건).
- 동일 PNU 중복은 원인을 조사한다: 값까지 동일하면 안전 dedup,
  값이 다르면 임의 선택하지 않고 조인에서 제외 + 건수 보고.
- 인허가 점포와는 left join — 미매칭 점포를 삭제하지 않는다.

시간 누수 주의:
- 기준년월(1월 1일)은 reference date일 뿐이며 실제 공시(available_at)는 그보다 늦다.
  DATA_CATALOG는 '통상 4~5월 공시'로 기록하고 있으나 raw 파일만으로는 확인 불가.
  → available_at = NEEDS_VERIFICATION 으로 남긴다. 이 모듈은 값 결합까지만 수행하고
  origin별 feature availability는 확정하지 않는다.
"""
from __future__ import annotations

import pandas as pd

from src.data.config import RAW_DIR

LANDPRICE_DIR = RAW_DIR / "공시지가"
YEARS = (2024, 2025, 2026)

# reference_year → 메타데이터. available_at은 raw로 확인 불가 → NEEDS_VERIFICATION
LANDPRICE_META = {
    y: {
        "reference_year": y,
        "feature_asof": f"{y}-01-01",       # raw 기준년월 실측값
        "available_at": "NEEDS_VERIFICATION",  # 공시일은 raw에 없음 (통상 4~5월, 미검증)
    }
    for y in YEARS
}


def load_landprice_year(year: int) -> tuple[pd.DataFrame, dict]:
    """연도 파일 로드 → (pnu, land_price_{year}) 테이블 + QA dict.

    반환 테이블의 pnu는 19자리 검증·중복 처리 완료 상태 (조인 안전).
    """
    path = LANDPRICE_DIR / f"공시지가_{year}년.csv"
    df = pd.read_csv(path, dtype=str, encoding="cp949",
                     usecols=["토지코드", "공시지가(원/㎡)", "기준년도", "기준년월"])
    qa: dict = {"year": year, "rows": len(df)}

    ref_years = df["기준년도"].dropna().unique().tolist()
    qa["reference_years_in_file"] = ref_years
    qa["reference_months_in_file"] = df["기준년월"].dropna().unique().tolist()[:3]

    pnu = df["토지코드"].fillna("").str.strip()
    valid_pnu = (pnu.str.len() == 19) & pnu.str.isdigit()
    qa["invalid_pnu"] = int((~valid_pnu).sum())
    qa["invalid_pnu_len_dist"] = (
        pnu[~valid_pnu].str.len().value_counts().to_dict()
    )
    df = df[valid_pnu].copy()
    df["pnu"] = pnu[valid_pnu]

    price = pd.to_numeric(df["공시지가(원/㎡)"], errors="coerce")
    df["price"] = price
    qa["price_nan"] = int(price.isna().sum())
    qa["price_zero"] = int((price == 0).sum())
    qa["price_negative"] = int((price < 0).sum())
    qa["price_median"] = float(price.median())
    qa["price_p99"] = float(price.quantile(0.99))
    qa["price_max"] = float(price.max())

    # 중복 PNU 조사
    dup_mask = df["pnu"].duplicated(keep=False)
    qa["dup_pnu_rows"] = int(dup_mask.sum())
    if dup_mask.any():
        dup = df[dup_mask]
        # 같은 PNU에서 가격까지 동일하면 안전 dedup 가능
        conflict = dup.groupby("pnu")["price"].nunique()
        conflict_pnus = set(conflict[conflict > 1].index)
        qa["dup_pnu_unique"] = int(dup["pnu"].nunique())
        qa["dup_pnu_conflicting_values"] = len(conflict_pnus)
        # 값 동일 중복 → dedup / 값 상이 중복 → 조인에서 제외 (임의 선택 금지)
        df = df[~df["pnu"].isin(conflict_pnus)]
        df = df.drop_duplicates("pnu")
        qa["excluded_conflicting_pnus"] = len(conflict_pnus)
    else:
        qa["dup_pnu_unique"] = 0
        qa["dup_pnu_conflicting_values"] = 0
        qa["excluded_conflicting_pnus"] = 0
        df = df.drop_duplicates("pnu")

    qa["unique_pnu"] = int(df["pnu"].nunique())
    out = df[["pnu", "price"]].rename(columns={"price": f"land_price_{year}"})
    return out, qa


def join_landprice(stores: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """인허가 점포(pnu 컬럼)에 3개년 공시지가를 wide로 left join한다."""
    out = stores.copy()
    qas = []
    for y in YEARS:
        tbl, qa = load_landprice_year(y)
        before = len(out)
        out = out.merge(tbl, on="pnu", how="left")
        assert len(out) == before, f"{y} 조인에서 행 수 변화 — PNU 중복 방어 실패"
        matched = out[f"land_price_{y}"].notna()
        qa["store_match_all"] = float(matched.mean())
        has_pnu = out["pnu"].notna()
        qa["store_match_with_pnu"] = float(matched[has_pnu].mean())
        qas.append(qa)
    price_cols = [f"land_price_{y}" for y in YEARS]
    out["land_price_match"] = out[price_cols].notna().any(axis=1)
    return out, qas
