# -*- coding: utf-8 -*-
"""서울 상권분석서비스 상권 단위 feature 테이블 (W2-0 Base).

`(trdar_cd, quarter)` 유일 테이블을 만든다. master는 이 테이블을 origin의 T-1 분기로 붙인다
(`master.attach_trdar_features`). 규칙은 DECISIONS.md 2026-09-23 "상권분석 available_at 및
polygon backcast 규칙", 실측은 DATA_CATALOG.md §3, §3-0, §3-A, §3-B.

범위:
- 상권 단위 5개 계열(길단위인구·상권변화지표·상주인구·직장인구·집객시설): 키 `(분기, 상권)`.
  `build_area_quarter_table()`.
- 업종 단위 계열(점포·추정매출): 키에 `서비스_업종_코드`가 있어 `BIZ_CODE_MAP`(W2-0 I-1)으로
  biz_type 그룹에 묶어 `(trdar_cd, quarter, biz_type)` 유일 테이블로 집계한다.
  `build_biz_quarter_table()`. 집계 규칙은 아래 "업종 단위 집계" 참조.

업종 단위 집계 (DECISIONS.md 2026-09-23 W2-0 I-1 최종):
- **observed partial**: mapped code 중 원천 row가 있는 코드만 합한다. row가 없는 코드를 0 row로
  만들거나 0으로 채우지 않는다. 대신 코드 수·coverage·partial 여부를 메타로 남긴다.
- 점포: row 부재는 시계열 전이·명시적 0 row·매출 원천 교차검증·연도별 패턴상 점포 0을 뜻한다는
  강한 실증 근거가 있으나 공식 명세로 확인된 규칙이 아니다 → 분석적 해석(structural zero 근거 있음)과
  물리적 처리(0 row를 만들지 않음)를 구분한다.
- 매출: row 부재는 0이 아니다. 점포 1~2개 코드는 매출 row가 100% 없다(소수 점포 억제/비공개로 판단).
  매출 합계는 항상 "매출이 공개된 mapped code의 합계"이며 biz_type 전체 매출의 하한일 수 있다.

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


# ---------------------------------------------------------------------------
# 업종 단위 계열 (점포·추정매출) — W2-0 I-1
# ---------------------------------------------------------------------------
# 확정 매핑 (DECISIONS.md 2026-09-23 W2-0 I-1 최종). 키는 labels_base.biz_type(인허가 종류)이다.
# 업태구분명·위생업태명·소진공 cat3는 origin 시점 값임을 검증할 수 없어 결합 key로 쓰지 않는다.
# CS100006(패스트푸드점)·CS100010(커피-음료)은 휴게음식점에만 배정한다 (중복 배정 없음).
BIZ_CODE_MAP: dict[str, tuple[str, ...]] = {
    "일반음식점": ("CS100001", "CS100002", "CS100003", "CS100004", "CS100007", "CS100008", "CS100009"),
    "휴게음식점": ("CS100005", "CS100006", "CS100010"),
    "미용업": ("CS200028", "CS200029", "CS200030"),
}
CODE_TO_BIZ = {code: biz for biz, codes in BIZ_CODE_MAP.items() for code in codes}
assert len(CODE_TO_BIZ) == sum(len(c) for c in BIZ_CODE_MAP.values()), "CS 코드 중복 배정"
BIZ_N_CODES_EXPECTED = {biz: len(codes) for biz, codes in BIZ_CODE_MAP.items()}

# 파일 연도 태그 → DATA_CATALOG.md §3-B sha256 앞 16자리
EXPECTED_BIZ_SHA16 = {
    ("점포", "2021"): "3c589015d87a0f56", ("점포", "2022"): "19c1e4f0179e928a",
    ("점포", "2023"): "594ed70b19a47713", ("점포", "2024"): "8446399758a9c3b1",
    ("점포", "2025_2026"): "511d4ebb81bfb3f1",
    ("추정매출", "2021"): "06c8336f41424dc5", ("추정매출", "2022"): "d10f27d9318ba93d",
    ("추정매출", "2023"): "1e8ea69cd4892176", ("추정매출", "2024"): "8b3e066e91f399d0",
    ("추정매출", "2025_2026"): "74c0462ce5765b3a",
}

# 점포 파일 컬럼명 변화 (DATA_CATALOG.md §3-0): 2021~2024 → 2025_2026
STORE_TOTAL_COLS = ("유사_업종_점포_수", "전체_점포_수")   # 전체 = 일반 + 프랜차이즈
STORE_GENERAL_COLS = ("점포_수", "일반_점포_수")

BIZ_STORE_FEATURES = [
    "trdar_biz_store_cnt_observed", "trdar_biz_franchise_cnt_observed",
    "trdar_biz_open_rate_observed", "trdar_biz_close_rate_observed",
]
BIZ_SALES_FEATURES = ["trdar_biz_sales_amt_observed", "trdar_biz_sales_per_store_observed"]
BIZ_FEATURES = BIZ_STORE_FEATURES + BIZ_SALES_FEATURES
BIZ_META = [
    "trdar_biz_store_n_codes_observed", "trdar_biz_store_n_codes_expected",
    "trdar_biz_store_code_coverage", "trdar_biz_store_is_partial",
    "trdar_biz_sales_n_codes_observed", "trdar_biz_sales_n_codes_expected",
    "trdar_biz_sales_code_coverage", "trdar_biz_sales_is_partial",
    "trdar_biz_sales_store_coverage",
]


def _year_tag(path) -> str:
    stem = path.stem
    return "2025_2026" if stem.endswith("2025_2026년") else stem[-5:-1]


def _pick(raw: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    (col,) = [c for c in names if c in raw.columns]
    return pd.to_numeric(raw[col], errors="raise")


def _load_biz_files(sub: str, reader) -> tuple[pd.DataFrame, set, set, list[dict]]:
    """연도 파일을 읽어 mapped code row만 남긴다. (rows, 전체 상권코드, 전체 분기, qa) 반환."""
    frames, trdars, quarters, qas = [], set(), set(), []
    for path in sorted((TRDAR_DIR / sub).glob("*.csv")):
        raw = pd.read_csv(path, encoding="cp949", dtype=str)
        df = pd.DataFrame({
            "trdar_cd": raw["상권_코드"].str.strip(),
            "quarter": quarter_code_to_label(raw["기준_년분기_코드"]),
            "code": raw["서비스_업종_코드"].str.strip(),
        })
        df = pd.concat([df, reader(raw)], axis=1)
        trdars |= set(df["trdar_cd"])
        quarters |= set(df["quarter"])
        digest = sha16(path)
        qas.append({"series": sub, "file": path.name, "rows": len(df),
                    "mapped_code_rows": int(df["code"].isin(CODE_TO_BIZ).sum()),
                    "sha16": digest,
                    "sha16_matches_catalog": digest == EXPECTED_BIZ_SHA16.get((sub, _year_tag(path)))})
        frames.append(df[df["code"].isin(CODE_TO_BIZ)])
    rows = pd.concat(frames, ignore_index=True)
    dup = int(rows.duplicated(["trdar_cd", "quarter", "code"]).sum())
    if dup:
        raise ValueError(f"{sub}: (상권, 분기, 서비스업종) 중복 {dup}건")
    return rows, trdars, quarters, qas


def _read_store(raw: pd.DataFrame) -> pd.DataFrame:
    total = _pick(raw, STORE_TOTAL_COLS)
    general = _pick(raw, STORE_GENERAL_COLS)
    franchise = pd.to_numeric(raw["프랜차이즈_점포_수"], errors="raise")
    if not (total == general + franchise).all():
        raise ValueError("점포: 전체 != 일반 + 프랜차이즈 (컬럼 매핑 오류 의심)")
    return pd.DataFrame({
        "store_total": total, "store_franchise": franchise,
        "store_open": pd.to_numeric(raw["개업_점포_수"], errors="raise"),
        "store_close": pd.to_numeric(raw["폐업_점포_수"], errors="raise"),
    })


def _read_sales(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"sales_amt": pd.to_numeric(raw["당월_매출_금액"], errors="raise")})


def load_store_rows():
    return _load_biz_files("점포", _read_store)


def load_sales_rows():
    return _load_biz_files("추정매출", _read_sales)


def code_level_grid(store: pd.DataFrame, sales: pd.DataFrame, trdars, quarters) -> pd.DataFrame:
    """(상권, 분기, mapped code) 전체 격자에 원천 row를 붙인 코드 단위 표.

    격자는 **존재 여부를 세기 위한 틀**이다. row가 없는 코드의 값은 NA로 남고 0으로 채우지 않는다.
    """
    idx = pd.MultiIndex.from_product([sorted(trdars), sorted(quarters), list(CODE_TO_BIZ)],
                                     names=["trdar_cd", "quarter", "code"])
    g = idx.to_frame(index=False)
    g["biz_type"] = g["code"].map(CODE_TO_BIZ)
    g = g.merge(store, on=["trdar_cd", "quarter", "code"], how="left", validate="1:1")
    g = g.merge(sales, on=["trdar_cd", "quarter", "code"], how="left", validate="1:1")
    g["store_row"] = g["store_total"].notna()
    g["sales_row"] = g["sales_amt"].notna()
    return g


def aggregate_biz(g: pd.DataFrame) -> pd.DataFrame:
    """코드 단위 표 → (trdar_cd, quarter, biz_type) observed partial 집계."""
    g = g.copy()
    # 매출이 공개된 코드만의 점포 수: 점포당 매출·sales_store_coverage에서 같은 code set을 쓰기 위함
    g["store_total_sales_codes"] = g["store_total"].where(g["sales_row"])
    g["sales_code_without_store"] = g["sales_row"] & ~g["store_row"]
    keys = ["trdar_cd", "quarter", "biz_type"]
    s = g.groupby(keys, sort=True)
    out = pd.DataFrame({
        "n_store": s["store_row"].sum(),
        "n_sales": s["sales_row"].sum(),
        # min_count=1: 관측 코드가 하나도 없으면 합계는 0이 아니라 NA
        "store_total": s["store_total"].sum(min_count=1),
        "store_franchise": s["store_franchise"].sum(min_count=1),
        "store_open": s["store_open"].sum(min_count=1),
        "store_close": s["store_close"].sum(min_count=1),
        "sales_amt": s["sales_amt"].sum(min_count=1),
        "store_total_sales_codes": s["store_total_sales_codes"].sum(min_count=1),
        "n_sales_code_without_store": s["sales_code_without_store"].sum(),
    }).reset_index()

    exp = out["biz_type"].map(BIZ_N_CODES_EXPECTED).astype("Int64")
    n_st = out["n_store"].astype("Int64")
    n_sa = out["n_sales"].astype("Int64")
    out["trdar_biz_store_n_codes_observed"] = n_st
    out["trdar_biz_store_n_codes_expected"] = exp
    out["trdar_biz_store_code_coverage"] = (n_st / exp).astype("Float64")
    out["trdar_biz_store_is_partial"] = (n_st < exp).astype("boolean")
    out["trdar_biz_sales_n_codes_observed"] = n_sa
    out["trdar_biz_sales_n_codes_expected"] = exp
    out["trdar_biz_sales_code_coverage"] = (n_sa / exp).astype("Float64")
    out["trdar_biz_sales_is_partial"] = (n_sa < exp).astype("boolean")

    total = out["store_total"].astype("Float64")
    positive = total > 0
    out["trdar_biz_store_cnt_observed"] = total
    out["trdar_biz_franchise_cnt_observed"] = out["store_franchise"].astype("Float64")
    # 율은 원천 율을 평균하지 않고 합계 건수로 다시 계산한다 (원천 정의: 건수 / 전체 점포 수 × 100).
    out["trdar_biz_open_rate_observed"] = (out["store_open"].astype("Float64") / total * 100).where(positive)
    out["trdar_biz_close_rate_observed"] = (out["store_close"].astype("Float64") / total * 100).where(positive)
    out["trdar_biz_sales_amt_observed"] = out["sales_amt"].astype("Float64")

    # 매출 row가 있는 코드 중 점포 row가 없는 것이 있으면 그 코드의 점포 수를 알 수 없다
    # → sales_store_coverage 분자와 점포당 매출 분모가 모두 불완전하므로 둘 다 NA.
    code_set_complete = out["n_sales_code_without_store"] == 0
    # sales_store_coverage = 매출 row가 있는 mapped code의 점포 수 합 / 관측된 mapped code 전체 점포 수 합.
    # 분자·분모 모두 같은 T-1 점포 원천. code set 불일치이거나 분모가 0 또는 NA이면 NA.
    num = out["store_total_sales_codes"].astype("Float64").fillna(0)
    out["trdar_biz_sales_store_coverage"] = (num / total).where(positive & code_set_complete)
    # 점포당 매출: 분자·분모 모두 "매출 row가 있는 mapped code" 집합. 그 코드 중 점포 row가 없는 것이
    # 있거나 분모가 0이면 NA (code set 불일치·0 나눗셈 방지, inf 금지).
    den = out["store_total_sales_codes"].astype("Float64")
    valid = (den > 0) & code_set_complete & out["sales_amt"].notna()
    out["trdar_biz_sales_per_store_observed"] = (out["sales_amt"].astype("Float64") / den).where(valid)
    for c in BIZ_FEATURES + ["trdar_biz_sales_store_coverage"]:
        out[c] = out[c].astype("Float64")
    mismatch = ~code_set_complete
    for c in ("trdar_biz_sales_store_coverage", "trdar_biz_sales_per_store_observed"):
        if out.loc[mismatch, c].notna().any():
            raise ValueError(f"{c}: 매출 코드에 대응하는 점포 row가 없는 그룹에 값이 채워졌다")
    result = out[keys + BIZ_FEATURES + BIZ_META].copy()
    result.attrs["n_sales_code_without_store"] = int(out["n_sales_code_without_store"].sum())
    result.attrs["n_groups_sales_code_without_store"] = int(mismatch.sum())
    return result


def store_absence_evidence(g: pd.DataFrame) -> dict:
    """점포 row 부재 = 점포 0 해석의 실증 근거, 매출 row 부재 = 비공개 판단 근거 (QA용)."""
    d = g.sort_values(["trdar_cd", "code", "quarter"])
    prev_total = d.groupby(["trdar_cd", "code"])["store_total"].shift()
    no_row = ~d["store_row"]
    zero = d["store_total"] == 0
    ev = {
        "코드 셀 수 (상권×분기×mapped code)": len(d),
        "점포 row 없음": int(no_row.sum()),
        "점포 명시적 0 row": int(zero.sum()),
        "명시적 0 row 중 폐업 점포 > 0": int((zero & (d["store_close"] > 0)).sum()),
        "전이: 점포>0 → row 없음 (근거라면 0)": int(((prev_total > 0) & no_row).sum()),
        "전이: 점포>0 → 명시적 0": int(((prev_total > 0) & zero).sum()),
        "전이: 명시적 0 → row 없음": int(((prev_total == 0) & no_row).sum()),
        "점포 row 없음 & 매출 row 있음 (근거라면 0)": int((no_row & d["sales_row"]).sum()),
        "매출 row 없음 & 점포 > 0": int((~d["sales_row"] & (d["store_total"] > 0)).sum()),
        "매출 0원 row": int((d["sales_amt"] == 0).sum()),
    }
    bucket = pd.cut(d["store_total"], [-1, 0, 1, 2, 3, 5, 10, float("inf")],
                    labels=["0", "1", "2", "3", "4-5", "6-10", "11+"])
    suppression = (~d["sales_row"]).groupby(bucket, observed=True).agg(["size", "mean"])
    suppression.columns = ["store_row_cells", "sales_row_missing_rate"]
    suppression.index.name = "code 점포 수"
    by_year = d.groupby(d["quarter"].str[:4]).agg(
        store_no_row_rate=("store_row", lambda s: 1 - s.mean()),
        sales_no_row_rate=("sales_row", lambda s: 1 - s.mean()))
    return {"counts": ev, "sales_suppression": suppression, "by_year": by_year}


def build_biz_quarter_table() -> tuple[pd.DataFrame, dict]:
    """점포·추정매출 → (trdar_cd, quarter, biz_type) 유일 테이블 + QA."""
    store, s_trdars, s_quarters, s_qas = load_store_rows()
    sales, _, a_quarters, a_qas = load_sales_rows()
    if not set(sales["trdar_cd"]) <= s_trdars:
        raise ValueError("매출에만 있는 상권코드가 있다")
    g = code_level_grid(store, sales, s_trdars, s_quarters | a_quarters)
    table = aggregate_biz(g)
    if table.duplicated(["trdar_cd", "quarter", "biz_type"]).any():
        raise ValueError("(trdar_cd, quarter, biz_type) 중복")
    qa = {
        "files": s_qas + a_qas,
        "evidence": store_absence_evidence(g),
        "n_sales_code_without_store": table.attrs["n_sales_code_without_store"],
    }
    table.attrs["source_snapshot"] = source_snapshot(s_qas + a_qas)
    return table, qa
