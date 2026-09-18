"""MDIS 소상공인실태조사 2023 전처리 (W1 A-3 2~7번).

Stage 3 인과분석(DML, W3)의 입력 테이블을 만든다. 이 모듈은 DML을 실행하지 않는다.
컬럼은 항상 이름으로 접근하고(`mdis_schema.ROLE_COLUMNS`), 위치 인덱싱을 사용하지 않는다.
"""

from pathlib import Path

import pandas as pd

from src.data import mdis_schema as schema


def load_mdis_raw(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="cp949", dtype=str)


def filter_industry(
    df: pd.DataFrame, expected_rows: int = schema.EXPECTED_INDUSTRY_FILTERED_ROWS
) -> pd.DataFrame:
    """`expected_rows`는 실제 파이프라인 실행 시 5,042로 고정된 값을 검증하기 위한 것이다.
    단위테스트에서는 합성 데이터 크기에 맞춰 이 값을 오버라이드한다."""
    out = df[df["산업중분류코드"].str.strip().isin(schema.INDUSTRY_CODES)].copy()
    assert len(out) == expected_rows, (
        f"산업중분류코드 필터 결과 행수 불일치: {len(out)} != {expected_rows}"
    )
    return out


def extract_role_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [c for role_cols in schema.ROLE_COLUMNS.values() for c in role_cols]
    return df[columns].copy()


def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """`schema.NUMERIC_COLUMNS`를 숫자형으로 변환한다.

    원본 CSV를 dtype=str로 읽기 때문에(코드형 컬럼 보존 목적) 실제로는 숫자인 컬럼도
    object로 남는다. 변환 전 비결측이었는데 변환 후 NaN이 된 행(=파싱 실패)이 있으면
    콘솔에 즉시 보고한다 — 조용히 버리지 않는다.
    """
    out = df.copy()
    for col in schema.NUMERIC_COLUMNS:
        if col not in out.columns:
            continue
        was_notna = out[col].notna()
        converted = pd.to_numeric(out[col], errors="coerce")
        failed = was_notna & converted.isna()
        n_failed = int(failed.sum())
        if n_failed > 0:
            samples = out.loc[failed, col].unique()[:5]
            print(
                f"[경고] {col}: 숫자 변환 실패 {n_failed}건 (원본 값 예시: {list(samples)}) "
                "- 원본 값 형식을 확인하세요."
            )
        out[col] = converted
    return out


def recode_yesno_columns(df: pd.DataFrame) -> pd.DataFrame:
    """`schema.YESNO_COLUMNS` 중 df에 실제로 존재하는 컬럼만 변환한다
    (부분 컬럼만 담은 데이터에도 안전하게 재사용 가능하도록)."""
    out = df.copy()
    for col in schema.YESNO_COLUMNS:
        if col not in out.columns:
            continue
        bin_col = f"{col}_bin"
        out[bin_col] = out[col].map(
            {schema.YESNO_YES_VALUE: 1, schema.YESNO_NO_VALUE: 0}
        )
    return out


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """호출 전 `coerce_numeric_columns`로 경영_영업이익/경영_매출금액이 숫자형으로
    변환되어 있어야 한다. 일반_창업인수승계_연도/월은 NUMERIC_COLUMNS 대상이 아니므로
    여기서 직접 float 변환한다(필터 후 결측 0건, 값 형식 실측 확인됨)."""
    out = df.copy()

    # tenure_months: mdis_schema.TENURE_MONTHS_CAVEAT 참조 —
    # 일반_창업인수승계_연도/월은 "점포 개업 시점"이 아니라 "현재 사업자의 운영 시작 시점"일 수 있다.
    birth_year = out["일반_창업인수승계_연도"].astype(float)
    birth_month = out["일반_창업인수승계_월"].astype(float)
    out["tenure_months"] = 2023 * 12 - (birth_year * 12 + birth_month)

    revenue = out["경영_매출금액"]
    profit = out["경영_영업이익"]
    zero_revenue = revenue == 0
    out["profit_margin"] = (profit / revenue).where(~zero_revenue)
    out["data_flag"] = zero_revenue.astype(int)

    out["is_seoul"] = out["행정구역시도코드"] == schema.SEOUL_CODE_VALUE
    assert out["is_seoul"].sum() > 0, (
        "행정구역시도코드 값 형식이 예상과 달라 서울 매칭이 0건입니다 - "
        "mdis_schema.SEOUL_CODE_VALUE를 재확인하세요."
    )

    return out


def apply_winsorize(df: pd.DataFrame) -> pd.DataFrame:
    """호출 전 `coerce_numeric_columns`로 경영_영업이익이 숫자형이어야 한다."""
    out = df.copy()
    profit = out["경영_영업이익"]
    lower, upper = profit.quantile([0.01, 0.99])
    out["경영_영업이익_winsorized"] = profit.clip(lower=lower, upper=upper)
    out["profit_outlier"] = ((profit < lower) | (profit > upper)).astype(int)
    return out


def build_treatment_vars(
    df: pd.DataFrame, expected_counts: dict[int, int] = schema.EXPECTED_TREAT_COUNTS
) -> pd.DataFrame:
    """`expected_counts`는 값 타입/인코딩 불일치를 즉시 잡기 위한 안전장치다.
    단위테스트에서는 합성 데이터의 실제 분포로 오버라이드한다."""
    out = df.copy()

    out["treat_binary"] = (
        out["경영_전자상거래_매출실적여부"] == schema.ECOMMERCE_YES_VALUE
    ).astype(int)
    counts = out["treat_binary"].value_counts().to_dict()
    assert counts == expected_counts, (
        f"처치변수 분포 불일치(값 타입/인코딩 재확인 필요): {counts} != {expected_counts}"
    )

    # 호출 전 coerce_numeric_columns로 경영_전자상거래_매출비율이 숫자형이어야 한다.
    out["treat_cont"] = out["경영_전자상거래_매출비율"].where(out["treat_binary"] == 1, 0)

    return out


def split_stage_ab(
    df: pd.DataFrame, expected_stage_b_len: int = schema.EXPECTED_TREAT_COUNTS[1]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    stage_a = df.copy()
    stage_b = df[df["treat_binary"] == 1].copy()
    assert len(stage_b) == expected_stage_b_len, (
        f"stage_b 행수 불일치: {len(stage_b)} != {expected_stage_b_len}"
    )
    return stage_a, stage_b


def build_codebook_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        if col not in schema.VARIABLE_DEFINITIONS:
            raise KeyError(
                f"'{col}'의 정의가 mdis_schema.VARIABLE_DEFINITIONS에 없습니다. "
                "코드북에 TODO를 남기지 않는다 — 정의를 추가한 뒤 다시 실행하세요."
            )
        rows.append(
            {
                "변수명": col,
                "타입": str(df[col].dtype),
                "결측률(%)": round(df[col].isna().mean() * 100, 2),
                "정의": schema.VARIABLE_DEFINITIONS[col],
            }
        )
    return pd.DataFrame(rows)


def build_encoding_table() -> pd.DataFrame:
    rows = [
        {"원본 컬럼": "경영_전자상거래_매출실적여부", "원본값": f"{schema.ECOMMERCE_YES_VALUE} (예)", "변환값": "treat_binary = 1"},
        {"원본 컬럼": "경영_전자상거래_매출실적여부", "원본값": f"{schema.YESNO_NO_VALUE} (아니오)", "변환값": "treat_binary = 0"},
        {"원본 컬럼": "행정구역시도코드", "원본값": f"{schema.SEOUL_CODE_VALUE} (서울)", "변환값": "is_seoul = True"},
    ]
    for col in schema.YESNO_COLUMNS:
        rows.append({"원본 컬럼": col, "원본값": f"{schema.YESNO_YES_VALUE} (예/수행)", "변환값": f"{col}_bin = 1"})
        rows.append({"원본 컬럼": col, "원본값": f"{schema.YESNO_NO_VALUE} (아니오/미수행)", "변환값": f"{col}_bin = 0"})
    return pd.DataFrame(rows)


def write_codebook_md(rows: pd.DataFrame, encoding_table: pd.DataFrame, path: Path) -> None:
    lines = ["# MDIS 코드북", "", "`docs/W1_MDIS_AND_LABEL.md` A-3 7번 산출물.", "", "## 변수 사전", ""]
    lines.append(rows.to_markdown(index=False))
    lines += ["", "## 값 인코딩 변환표", ""]
    lines.append(encoding_table.to_markdown(index=False))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
