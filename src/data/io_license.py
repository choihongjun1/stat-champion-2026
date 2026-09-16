# -*- coding: utf-8 -*-
"""인허가 raw CSV 안전 로딩.

- CP949 + 디코딩 불가 바이트는 U+FFFD 치환(raw는 절대 수정하지 않음)
- 전 컬럼 문자열로 읽고, 앞뒤 공백 strip 후 빈 문자열은 결측(NA) 처리
  (영업 중 행의 폐업일자가 NaN이 아닌 공백 문자열인 경우가 있음 — audit 확인)
"""
from __future__ import annotations

import pandas as pd

from src.data.config import (
    BUSINESS_TYPES,
    COMMON_COLUMNS,
    LICENSE_DIR,
    PER_TYPE_COLUMNS,
    RAW_ENCODING,
    RAW_ENCODING_ERRORS,
    TARGET_GU_CODES,
)

REPLACEMENT_CHAR = "�"


def load_license_raw(business_type: str) -> pd.DataFrame:
    """업종별 인허가 CSV를 표준 컬럼명으로 로딩한다 (필터 전, 전국/서울 전체).

    반환 컬럼은 COMMON_COLUMNS + PER_TYPE_COLUMNS의 표준명이며 전부 문자열(str) dtype.
    """
    if business_type not in BUSINESS_TYPES:
        raise ValueError(f"unknown business_type: {business_type}")

    path = LICENSE_DIR / BUSINESS_TYPES[business_type]["file"]
    col_map = {**COMMON_COLUMNS, **PER_TYPE_COLUMNS[business_type]}
    raw_cols = list(col_map.values())

    df = pd.read_csv(
        path,
        encoding=RAW_ENCODING,
        encoding_errors=RAW_ENCODING_ERRORS,
        usecols=raw_cols,
        dtype=str,
    )
    # raw 컬럼명 -> 표준 컬럼명
    df = df.rename(columns={v: k for k, v in col_map.items()})

    # 공백 strip 후 빈 문자열 -> NA
    for c in df.columns:
        s = df[c].str.strip()
        df[c] = s.mask(s == "")

    df["business_type"] = business_type
    df["source"] = BUSINESS_TYPES[business_type]["file"]
    return df


def filter_target_gu(df: pd.DataFrame) -> pd.DataFrame:
    """개방자치단체코드 기준으로 광진·마포·영등포 3구 관할 행만 남긴다."""
    out = df[df["gov_code"].isin(TARGET_GU_CODES)].copy()
    out["gu"] = out["gov_code"].map(TARGET_GU_CODES)
    return out.reset_index(drop=True)


def count_replacement_chars(df: pd.DataFrame) -> dict[str, int]:
    """디코딩 치환문자(U+FFFD)가 포함된 셀 수를 컬럼별로 집계한다 (QA용)."""
    counts: dict[str, int] = {}
    for c in df.columns:
        if df[c].dtype == object or str(df[c].dtype).startswith("str"):
            n = int(df[c].fillna("").str.contains(REPLACEMENT_CHAR).sum())
            if n:
                counts[c] = n
    return counts
