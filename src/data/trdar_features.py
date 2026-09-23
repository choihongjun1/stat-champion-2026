# -*- coding: utf-8 -*-
"""서울 상권분석서비스 상권 단위 feature 테이블 (W2-0 Base).

`(trdar_cd, quarter)` 유일 테이블을 만든다. master는 이 테이블을 origin의 T-1 분기로 붙인다
(`master.attach_trdar_features`). 규칙은 DECISIONS.md 2026-09-23 "상권분석 available_at 및
polygon backcast 규칙", 실측은 DATA_CATALOG.md §3, §3-0, §3-A, §3-B.

범위 (W2-0 I-1):
- 상권 단위 5개 계열(길단위인구·상권변화지표·상주인구·직장인구·집객시설)만 다룬다.
  키가 `(분기, 상권)`이라 업종 매핑 없이 유일하다.
- 업종 단위 계열(점포·추정매출)은 키에 `서비스_업종_코드`가 있어 biz_type 매핑이 필요하다.
  매핑 확정 전까지 보류한다 (그대로 붙이면 행이 업종 수만큼 늘어난다).

원칙:
- 행이 없는 상권×분기는 0이 아니라 NA다 (0인지 공개 제외인지 원본으로 판정 불가).
- 상주·직장·집객은 분기마다 갱신되지 않는다. 분기 코드와 별도로 값이 실제로 바뀐 분기를
  `*_value_asof`로 남긴다. 관측 첫 분기(2021Q1)부터 값이 같으면 asof는 2021Q1로 기록되며,
  실제 기준시점은 그보다 이를 수 있다 (예: 집객시설 golmok 표기 2020년 12월). 이른 쪽 오차라
  누수 방향이 아니다.
- `available_at`에는 확인된 published_at만 쓴다. 추정 사례는 날짜를 만들지 않고 basis만 남긴다.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from src.data.config import RAW_DIR

TRDAR_DIR = RAW_DIR / "상권분석"
RAW_RECEIVED_AT = "2026-09-13"  # 로컬 raw 수령일 (DATA_CATALOG.md §3-B)

# 계열 → 파일 / 사용 컬럼 / 갱신 정체 여부. raw 컬럼명으로만 접근한다.
AREA_SERIES: dict[str, dict] = {
    "길단위인구": {
        "file": "지표인구/서울시 상권분석서비스(길단위인구-상권).csv",
        "columns": {"총_유동인구_수": "trdar_flow_pop"},
        "frozen": False,
    },
    "상권변화지표": {
        "file": "지표인구/서울시 상권분석서비스(상권변화지표-상권).csv",
        "columns": {
            "상권_변화_지표": "trdar_change_index",
            "운영_영업_개월_평균": "trdar_oper_months_avg",
            "폐업_영업_개월_평균": "trdar_close_months_avg",
        },
        "frozen": False,
    },
    "상주인구": {
        "file": "지표인구/서울시 상권분석서비스(상주인구-상권).csv",
        "columns": {"총_상주인구_수": "trdar_resident_pop"},
        "frozen": True,
        "asof_column": "trdar_resident_value_asof",
    },
    "직장인구": {
        "file": "지표인구/서울시 상권분석서비스(직장인구-상권).csv",
        "columns": {"총_직장_인구_수": "trdar_worker_pop"},
        "frozen": True,
        "asof_column": "trdar_worker_value_asof",
    },
    "집객시설": {
        "file": "지표인구/서울시 상권분석서비스(집객시설-상권).csv",
        "columns": {"집객시설_수": "trdar_facility_cnt"},
        "frozen": True,
        "asof_column": "trdar_facility_value_asof",
    },
}
CATEGORICAL_COLUMNS = {"trdar_change_index"}

# DATA_CATALOG.md §3-B에 기록한 sha256 앞 16자리. 다르면 raw가 바뀐 것이다 (QA에서 표시).
EXPECTED_SHA16 = {
    "상권변화지표": "0a2ea421607c167f",
    "길단위인구": "9a481ee97de352d9",
    "상주인구": "d93c249296753020",
    "직장인구": "9987bbbdaf9f1623",
    "집객시설": "632d4380d0d68c9f",
}

# golmok 데이터 출처 페이지에서 기준시점과 함께 확인된 published_at (DATA_CATALOG.md §3-A).
PUBLISHED_AT_CONFIRMED = {
    "2021Q4": "2022-03-03",
    "2022Q4": "2023-02-28",
    "2024Q2": "2024-08-23",
    "2024Q4": "2025-02-20",
    "2025Q2": "2025-08-26",
    "2026Q2": "2026-08-18",
}
# 업데이트일만 확인되고 분기 대응은 공식 일정으로 추정한 분기. 날짜는 기록하지 않는다.
PUBLISHED_AT_INFERRED_QUARTERS = {
    "2021Q3", "2023Q4", "2024Q1", "2025Q1", "2025Q3", "2025Q4", "2026Q1",
}
BASIS_CONFIRMED = "archive_confirmed"
BASIS_INFERRED = "archive_inferred"
BASIS_UNVERIFIED = "unverified"


def quarter_code_to_label(code: pd.Series) -> pd.Series:
    """'20211' → '2021Q1'."""
    code = code.astype(str).str.strip()
    return code.str[:4] + "Q" + code.str[4]


def availability(quarters: pd.Series) -> pd.DataFrame:
    """분기 라벨 → trdar_available_at(확인된 날짜만) / trdar_available_at_basis."""
    q = quarters.astype("object")
    available_at = pd.to_datetime(q.map(PUBLISHED_AT_CONFIRMED))
    basis = pd.Series(BASIS_UNVERIFIED, index=q.index, dtype="object")
    basis[q.isin(PUBLISHED_AT_INFERRED_QUARTERS)] = BASIS_INFERRED
    basis[q.isin(PUBLISHED_AT_CONFIRMED)] = BASIS_CONFIRMED
    return pd.DataFrame({"trdar_available_at": available_at,
                         "trdar_available_at_basis": basis}, index=q.index)


def sha16(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def value_asof(df: pd.DataFrame, value_col: str) -> pd.Series:
    """상권별로 분기를 정렬해, 각 분기 값이 마지막으로 바뀐 분기를 돌려준다.

    df는 trdar_cd / quarter / value_col을 가진다. 분기 라벨('2021Q1')은 사전순 = 시간순이다.
    """
    d = df[["trdar_cd", "quarter", value_col]].sort_values(["trdar_cd", "quarter"])
    prev = d.groupby("trdar_cd")[value_col].shift()
    first = d["trdar_cd"].ne(d["trdar_cd"].shift())
    changed = (first | d[value_col].ne(prev)).fillna(True).astype(bool)
    asof = d["quarter"].where(changed).groupby(d["trdar_cd"]).ffill()
    return asof.reindex(df.index)


def load_series(name: str, spec: dict) -> tuple[pd.DataFrame, dict]:
    path = TRDAR_DIR / spec["file"]
    raw = pd.read_csv(path, encoding="cp949", dtype=str,
                      usecols=["기준_년분기_코드", "상권_코드", *spec["columns"]])
    df = pd.DataFrame({
        "trdar_cd": raw["상권_코드"].str.strip(),
        "quarter": quarter_code_to_label(raw["기준_년분기_코드"]),
    })
    for src, dst in spec["columns"].items():
        if dst in CATEGORICAL_COLUMNS:
            df[dst] = raw[src].str.strip()
        else:
            df[dst] = pd.to_numeric(raw[src], errors="raise").astype("Float64")
    dup = int(df.duplicated(["trdar_cd", "quarter"]).sum())
    if dup:
        raise ValueError(f"{name}: (상권_코드, 분기) 중복 {dup}건")
    if spec["frozen"]:
        (value_col,) = spec["columns"].values()
        df[spec["asof_column"]] = value_asof(df, value_col)
    digest = sha16(path)
    qa = {
        "series": name,
        "file": path.name,
        "rows": len(df),
        "trdar_codes": df["trdar_cd"].nunique(),
        "quarters": f"{df['quarter'].min()}~{df['quarter'].max()} ({df['quarter'].nunique()})",
        "sha16": digest,
        "sha16_matches_catalog": digest == EXPECTED_SHA16[name],
    }
    return df, qa


def source_snapshot(qas: list[dict]) -> str:
    """trdar_source_snapshot 값: 계열별 파일명@sha16 + 수령일."""
    parts = [f"{q['file']}@{q['sha16']}" for q in qas]
    return "; ".join(parts) + f" (received {RAW_RECEIVED_AT})"


def build_area_quarter_table() -> tuple[pd.DataFrame, list[dict]]:
    """5개 계열을 (trdar_cd, quarter)로 outer join. 행이 없는 계열 값은 NA로 남는다."""
    table = None
    qas = []
    for name, spec in AREA_SERIES.items():
        df, qa = load_series(name, spec)
        qas.append(qa)
        table = df if table is None else table.merge(
            df, on=["trdar_cd", "quarter"], how="outer", validate="1:1")
    assert not table.duplicated(["trdar_cd", "quarter"]).any()
    table = table.sort_values(["trdar_cd", "quarter"]).reset_index(drop=True)
    table.attrs["source_snapshot"] = source_snapshot(qas)
    return table, qas
