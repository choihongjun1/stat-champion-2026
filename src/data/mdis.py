"""MDIS 소상공인실태조사 2023 전처리 (W1 A-3 2~7번).

Stage 3 인과분석(DML, W3)의 입력 테이블을 만든다. 이 모듈은 DML을 실행하지 않는다.
컬럼은 항상 이름으로 접근하고(`mdis_schema.ROLE_COLUMNS`), 위치 인덱싱을 사용하지 않는다.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from src.data import mdis_schema as schema


def load_mdis_raw(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="cp949", dtype=str)


def assign_row_id(df: pd.DataFrame) -> pd.DataFrame:
    """원본 CSV의 0-based 행 위치를 `mdis_row_id`로 부여한다.

    필터링 전에 호출해야 원본 파일 행 위치와 그대로 대응한다. 원본에는 ID 컬럼이
    없어(156열 확인) 이 값이 유일한 추적 key다.
    """
    out = df.copy()
    out["mdis_row_id"] = range(len(out))
    return out


def filter_industry(
    df: pd.DataFrame, expected_rows: int = schema.EXPECTED_INDUSTRY_FILTERED_ROWS
) -> pd.DataFrame:
    """`expected_rows`는 실제 파이프라인 실행 시 5,042로 고정된 값을 검증하기 위한 것이다.
    단위테스트에서는 합성 데이터 크기에 맞춰 이 값을 오버라이드한다."""
    out = df[df["산업중분류코드"].str.strip().isin(schema.INDUSTRY_CODES)].copy()
    if len(out) != expected_rows:
        raise ValueError(f"산업중분류코드 필터 결과 행수 불일치: {len(out)} != {expected_rows}")
    return out


def extract_role_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [c for role_cols in schema.ROLE_COLUMNS.values() for c in role_cols]
    if "mdis_row_id" in df.columns:
        columns = ["mdis_row_id"] + columns
    return df[columns].copy()


def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """`schema.NUMERIC_COLUMNS`를 숫자형으로 변환한다.

    원본 CSV를 dtype=str로 읽기 때문에(코드형 컬럼 보존 목적) 실제로는 숫자인 컬럼도
    object로 남는다. 변환 전 비결측이었는데 변환 후 NaN이 된 행(=파싱 실패)이 있으면
    `ValueError`를 발생시킨다 — 조용히 NaN으로 버리지 않는다.
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
            raise ValueError(
                f"{col}: 숫자 변환 실패 {n_failed}건 (원본 값 예시: {list(samples)}) "
                "- 원본 값 형식을 확인하세요."
            )
        out[col] = converted
    return out


def validate_no_missing_required_columns(df: pd.DataFrame) -> None:
    """`schema.REQUIRED_NON_MISSING_COLUMNS`에 결측이 있으면 `ValueError`를 발생시킨다.

    호출 전 `coerce_numeric_columns`로 대상 컬럼이 숫자형이어야 한다."""
    for col in schema.REQUIRED_NON_MISSING_COLUMNS:
        n_missing = int(df[col].isna().sum())
        if n_missing > 0:
            raise ValueError(
                f"{col} 결측 {n_missing}건 - 원본 데이터 또는 변환 로직을 재확인하세요."
            )


def validate_weight_positive(df: pd.DataFrame) -> None:
    """사업체수가중값이 `schema.WEIGHT_MIN_VALUE` 이하인 행이 있으면 `ValueError`를 발생시킨다.

    호출 전 `coerce_numeric_columns`로 숫자형이어야 한다."""
    col = "사업체수가중값"
    invalid_mask = df[col] <= schema.WEIGHT_MIN_VALUE
    n_invalid = int(invalid_mask.sum())
    if n_invalid > 0:
        samples = df.loc[invalid_mask, col].unique()[:5]
        raise ValueError(
            f"{col}: {schema.WEIGHT_MIN_VALUE} 이하인 행 {n_invalid}건 (예시: {list(samples)}) "
            "- 가중치가 0 이하면 가중 통계에서 해당 행이 사라지거나 부호가 뒤집힙니다."
        )


def validate_categorical_code_columns(
    df: pd.DataFrame, valid_codes: dict[str, set[str]] | None = None
) -> None:
    """다항(2값 초과) 코드 컬럼의 결측/이상값을 검증한다.

    `recode_yesno_columns`가 이진 컬럼(예/아니오)을 다루는 것과 짝을 이루는 다항
    코드 버전이다. `valid_codes` 기본값은 `schema.CATEGORICAL_CODE_COLUMNS`."""
    if valid_codes is None:
        valid_codes = schema.CATEGORICAL_CODE_COLUMNS
    for col, valid_values in valid_codes.items():
        missing_mask = df[col].isna()
        n_missing = int(missing_mask.sum())
        if n_missing > 0:
            raise ValueError(
                f"{col} 결측 {n_missing}건 - 원본 데이터 또는 변환 로직을 재확인하세요."
            )

        invalid_mask = ~df[col].isin(valid_values)
        n_invalid = int(invalid_mask.sum())
        if n_invalid > 0:
            samples = df.loc[invalid_mask, col].unique()[:5]
            raise ValueError(
                f"{col}: {valid_values} 밖의 값 {n_invalid}건 (예시: {list(samples)}) "
                "- 원본 값 형식을 재확인하세요."
            )


def recode_yesno_columns(df: pd.DataFrame) -> pd.DataFrame:
    """`schema.YESNO_COLUMNS` 중 df에 실제로 존재하는 컬럼만 변환한다
    (부분 컬럼만 담은 데이터에도 안전하게 재사용 가능하도록).

    결측(NaN)은 그대로 통과시키지만(skip-pattern 등으로 원래 결측일 수 있음),
    `{YESNO_YES_VALUE, YESNO_NO_VALUE}` 밖의 값이 있으면 조용히 NaN으로 매핑하지
    않고 `ValueError`를 발생시킨다.
    """
    out = df.copy()
    valid_values = {schema.YESNO_YES_VALUE, schema.YESNO_NO_VALUE}
    for col in schema.YESNO_COLUMNS:
        if col not in out.columns:
            continue
        invalid_mask = out[col].notna() & ~out[col].isin(valid_values)
        n_invalid = int(invalid_mask.sum())
        if n_invalid > 0:
            samples = out.loc[invalid_mask, col].unique()[:5]
            raise ValueError(
                f"{col}: {valid_values} 밖의 값 {n_invalid}건 (예시: {list(samples)}) "
                "- 원본 값 형식을 재확인하세요."
            )
        bin_col = f"{col}_bin"
        out[bin_col] = out[col].map(
            {schema.YESNO_YES_VALUE: 1, schema.YESNO_NO_VALUE: 0}
        )
    return out


def add_derived_features(
    df: pd.DataFrame, expected_seoul_count: int = schema.EXPECTED_SEOUL_COUNT
) -> pd.DataFrame:
    """호출 전 `coerce_numeric_columns`로 경영_영업이익/경영_매출금액이 숫자형으로
    변환되어 있어야 한다. 일반_창업인수승계_연도/월은 NUMERIC_COLUMNS 대상이 아니므로
    여기서 직접 float 변환한다(필터 후 결측 0건, 값 형식 실측 확인됨).

    `expected_seoul_count`는 실제 파이프라인 실행 시 474로 고정된 값을 검증하기 위한
    것이다. 단위테스트에서는 합성 데이터 크기에 맞춰 이 값을 오버라이드한다."""
    out = df.copy()

    # tenure_months: mdis_schema.TENURE_MONTHS_CAVEAT 참조 —
    # 일반_창업인수승계_연도/월은 "점포 개업 시점"이 아니라 "현재 사업자의 운영 시작 시점"일 수 있다.
    birth_year = out["일반_창업인수승계_연도"].astype(float)
    birth_month = out["일반_창업인수승계_월"].astype(float)

    if not birth_month.between(1, 12).all():
        raise ValueError(
            "일반_창업인수승계_월 값이 1~12 범위를 벗어났습니다 - 원본 값 형식을 재확인하세요."
        )
    sentinel_mask = birth_year == schema.TENURE_YEAR_SENTINEL
    plausible_year = birth_year.between(schema.TENURE_YEAR_MIN_PLAUSIBLE, schema.TENURE_YEAR_MAX)
    if not (sentinel_mask | plausible_year).all():
        raise ValueError(
            f"일반_창업인수승계_연도 값이 sentinel({schema.TENURE_YEAR_SENTINEL})도 아니고 "
            f"[{schema.TENURE_YEAR_MIN_PLAUSIBLE}, {schema.TENURE_YEAR_MAX}] 범위도 아닙니다 - "
            "새로운 이상값일 수 있으니 원본 값을 재확인하세요."
        )

    out["tenure_invalid_flag"] = sentinel_mask.astype(int)
    base = schema.TENURE_BASE_YEAR * 12 + schema.TENURE_BASE_MONTH
    out["tenure_months"] = (base - (birth_year * 12 + birth_month)).where(~sentinel_mask)

    revenue = out["경영_매출금액"]
    profit = out["경영_영업이익"]
    zero_revenue = revenue == 0
    out["profit_margin"] = (profit / revenue).where(~zero_revenue)
    out["data_flag"] = zero_revenue.astype(int)

    out["is_seoul"] = out["행정구역시도코드"] == schema.SEOUL_CODE_VALUE
    n_seoul = int(out["is_seoul"].sum())
    if n_seoul != expected_seoul_count:
        raise ValueError(
            f"is_seoul 매칭 결과 불일치: {n_seoul} != {expected_seoul_count} - "
            "mdis_schema.SEOUL_CODE_VALUE 또는 EXPECTED_SEOUL_COUNT를 재확인하세요."
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

    # treat_binary는 `== ECOMMERCE_YES_VALUE` 비교로 만들어지는데, 이 비교는 원본값이
    # NaN이거나 {ECOMMERCE_YES_VALUE, YESNO_NO_VALUE} 밖이어도 항상 False(=0)를 반환해
    # 결측/이상값을 조용히 "미처치"로 오분류할 수 있다. 비교 전에 도메인을 검증한다.
    source_col = out["경영_전자상거래_매출실적여부"]
    valid_values = {schema.ECOMMERCE_YES_VALUE, schema.YESNO_NO_VALUE}
    invalid_mask = source_col.isna() | ~source_col.isin(valid_values)
    n_invalid = int(invalid_mask.sum())
    if n_invalid > 0:
        samples = source_col.loc[invalid_mask].unique()[:5]
        raise ValueError(
            f"경영_전자상거래_매출실적여부: 결측 또는 {valid_values} 밖의 값 {n_invalid}건 "
            f"(예시: {list(samples)}) - treat_binary가 조용히 0으로 오분류되기 전에 확인하세요."
        )

    out["treat_binary"] = (source_col == schema.ECOMMERCE_YES_VALUE).astype(int)
    counts = out["treat_binary"].value_counts().to_dict()
    if counts != expected_counts:
        raise ValueError(f"처치변수 분포 불일치(값 타입/인코딩 재확인 필요): {counts} != {expected_counts}")

    ratio_col = out["경영_전자상거래_매출비율"]

    # PR #15 리뷰(choihongjun1): 처치군(treat_binary==1)인데 비율이 NaN이거나 100을
    # 초과하는 행이 에러 없이 통과하던 문제. 처치군인데 비율이 없다는 건 원본 모순이고,
    # 100 초과는 퍼센트 인코딩이 깨졌다는 신호이므로 즉시 실패한다.
    treated_mask = out["treat_binary"] == 1
    treated_invalid_mask = treated_mask & (
        ratio_col.isna() | (ratio_col > schema.TREAT_RATIO_MAX)
    )
    n_treated_invalid = int(treated_invalid_mask.sum())
    if n_treated_invalid > 0:
        samples = ratio_col.loc[treated_invalid_mask].unique()[:5]
        raise ValueError(
            f"경영_전자상거래_매출비율: treat_binary==1인데 NaN이거나 "
            f"{schema.TREAT_RATIO_MAX} 초과인 행 {n_treated_invalid}건 (예시: {list(samples)}) "
            "- 원본을 재확인하세요."
        )

    # 여부=2(미처치)인데 매출비율이 실제 값을 가진 행은 아래에서 조용히 0으로 덮어써진다.
    # 그 전에 이런 행이 없는지 확인한다(원본이 바뀌면 이 가정이 깨질 수 있다).
    mismatch_mask = (out["treat_binary"] == 0) & ratio_col.notna()
    n_mismatch = int(mismatch_mask.sum())
    if n_mismatch > 0:
        raise ValueError(
            f"treat_binary==0인데 경영_전자상거래_매출비율이 NaN이 아닌 행 {n_mismatch}건 - "
            "treat_cont를 0으로 덮어쓰면 실제 값이 사라집니다. 원본을 재확인하세요."
        )

    # 호출 전 coerce_numeric_columns로 경영_전자상거래_매출비율이 숫자형이어야 한다.
    out["treat_cont"] = ratio_col.where(out["treat_binary"] == 1, 0)

    return out


def split_stage_ab(
    df: pd.DataFrame, expected_stage_b_len: int = schema.EXPECTED_TREAT_COUNTS[1]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    stage_a = df.copy()
    stage_b = df[df["treat_binary"] == 1].copy()
    if len(stage_b) != expected_stage_b_len:
        raise ValueError(f"stage_b 행수 불일치: {len(stage_b)} != {expected_stage_b_len}")
    if "mdis_row_id" in stage_a.columns:
        if not stage_a["mdis_row_id"].is_unique:
            raise ValueError("mdis_row_id가 stage_a 내에서 유일하지 않습니다.")
        if not set(stage_b["mdis_row_id"]) <= set(stage_a["mdis_row_id"]):
            raise ValueError("stage_b의 mdis_row_id가 stage_a에 모두 포함되어 있지 않습니다.")
    return stage_a, stage_b


def build_provenance(raw_path: Path) -> dict:
    """M6: 산출물이 어떤 원본 파일·코드 버전에서 나왔는지 재현성 기록을 만든다.

    git commit을 못 구하는 환경(예: git 미설치, .git 없음)에서도 파이프라인 자체는
    실패하지 않도록 `None`으로 남긴다 — provenance 기록 실패가 산출물 생성을 막아서는
    안 된다."""
    sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        git_commit = None
    return {
        "raw_file": str(raw_path),
        "raw_sha256": sha256,
        "pandas_version": pd.__version__,
        "python_version": sys.version,
        "git_commit": git_commit,
    }


def write_provenance_json(provenance: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")


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
        {"원본 컬럼": "일반_창업형태코드", "원본값": "1 (신규창업)", "변환값": "-"},
        {"원본 컬럼": "일반_창업형태코드", "원본값": "2 (인수창업)", "변환값": "-"},
        {"원본 컬럼": "일반_창업형태코드", "원본값": "3 (가업승계)", "변환값": "-"},
        {
            "원본 컬럼": "일반_창업인수승계_연도",
            "원본값": f"{schema.TENURE_YEAR_SENTINEL} (sentinel)",
            "변환값": "tenure_months = NaN, tenure_invalid_flag = 1",
        },
    ]
    for col in schema.YESNO_COLUMNS:
        rows.append({"원본 컬럼": col, "원본값": f"{schema.YESNO_YES_VALUE} (예/수행)", "변환값": f"{col}_bin = 1"})
        rows.append({"원본 컬럼": col, "원본값": f"{schema.YESNO_NO_VALUE} (아니오/미수행)", "변환값": f"{col}_bin = 0"})
    return pd.DataFrame(rows)


# 이 파일이 생성하는 구간 밖에서 보존해야 하는 블록들.
# - A-4 블록: `mdis_validation.append_validation_section_md`가 채운다.
# - 수기 메모 블록: 사람이 직접 쓴 해석·판단. 어떤 스크립트도 내용을 쓰지 않는다.
PRESERVED_CODEBOOK_BLOCKS = [
    ("<!-- A4-VALIDATION-START -->", "<!-- A4-VALIDATION-END -->"),
    ("<!-- MANUAL-NOTES-START -->", "<!-- MANUAL-NOTES-END -->"),
]


def extract_preserved_blocks(text: str) -> list[str]:
    """기존 코드북에서 재생성 대상이 아닌 블록을 마커째로 추출한다."""
    blocks = []
    for start, end in PRESERVED_CODEBOOK_BLOCKS:
        if start in text and end in text:
            body = text.split(start, 1)[1].split(end, 1)[0]
            blocks.append(f"{start}{body}{end}")
    return blocks


def write_codebook_md(rows: pd.DataFrame, encoding_table: pd.DataFrame, path: Path) -> None:
    """변수 사전·인코딩표를 재생성한다.

    A-4 검증 절(`mdis_validation`이 생성)과 수기 메모 절은 **보존**한다. 이 함수가 파일을
    통째로 덮어쓰면 다른 스크립트의 산출물과 사람이 쓴 문단이 조용히 사라지기 때문이다.
    """
    lines = ["# MDIS 코드북", "", "`docs/W1_MDIS_AND_LABEL.md` A-3 7번 산출물.", "", "## 변수 사전", ""]
    lines.append(rows.to_markdown(index=False))
    lines += ["", "## 값 인코딩 변환표", ""]
    lines.append(encoding_table.to_markdown(index=False))
    lines.append("")

    preserved = extract_preserved_blocks(path.read_text(encoding="utf-8")) if path.exists() else []
    for block in preserved:
        lines += [block, ""]

    path.write_text("\n".join(lines), encoding="utf-8")
