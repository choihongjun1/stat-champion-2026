"""인허가 단독 패널 (W1 §B-2 단계 1).

이 모듈은 소진공 매칭(§B-3)이나 모델링을 하지 않는다. 인허가 3종(일반음식점·휴게음식점·
미용업) 원본만으로 store_id 기준 Long Panel과 12개월 폐업 라벨(event_12m)을 만드는
단계까지만 구현한다. 컬럼은 항상 이름으로 접근하고(`label_schema.CORE_COLUMNS` 등),
위치 인덱싱을 사용하지 않는다.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from lifelines import KaplanMeierFitter

from src.data import label_schema as schema

# 그래프에 한글 라벨을 쓰므로 Windows 기본 한글 폰트를 지정한다 (미지정 시 글자가 네모로 깨짐).
plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False


def load_licensing_raw(
    source_type: str, path: Path, expected_columns: int | None = None
) -> pd.DataFrame:
    """`expected_columns`는 실제 파이프라인 실행 시 label_schema.EXPECTED_RAW_COLUMN_COUNTS로
    고정된 값을 검증하기 위한 것이다. 단위테스트에서는 합성 데이터 크기에 맞춰 오버라이드한다."""
    if expected_columns is None:
        expected_columns = schema.EXPECTED_RAW_COLUMN_COUNTS[source_type]

    df = pd.read_csv(
        path,
        dtype=str,
        encoding="cp949",
        encoding_errors=schema.ENCODING_ERRORS_POLICY[source_type],
    )
    assert df.shape[1] == expected_columns, (
        f"{source_type} 원본 컬럼 수 불일치: {df.shape[1]} != {expected_columns}"
    )

    if schema.ENCODING_ERRORS_POLICY[source_type] == "replace":
        n_replaced = int(
            df.apply(lambda s: s.str.count("�")).sum().sum()
        )
        if n_replaced > 0:
            print(
                f"[경고] {source_type}: cp949 디코딩 중 대체된 문자(\\ufffd) {n_replaced}건 "
                "(encoding_errors='replace') - 해당 셀의 원본 바이트는 복구 불가."
            )

    return df


def standardize_columns(df: pd.DataFrame, source_type: str) -> pd.DataFrame:
    out = df[schema.CORE_COLUMNS].copy()
    out["source_type"] = source_type
    return out


def diagnose_district_filter(
    df: pd.DataFrame, district_codes: dict[str, str] = schema.DISTRICT_CODES
) -> pd.DataFrame:
    """코드 기반 매칭과 주소텍스트 기반 매칭이 갈리는 행만 반환한다 (QA 전용, assert 없음).

    실측(2026-09-18, 2026-09-21 재확인): 두 기준이 갈리는 행이 3개 파일 합산 20건 있다
    (주소텍스트에만 14 / 코드에만 6). 주소는 명백히 3구인데 코드가 다르거나, 코드는 3구인데
    주소가 서대문구·구로구·성북구·용산구·인천 등인 행이 섞여 있어 **어느 필드가 틀렸는지
    원본만으로 판정할 수 없다.**

    `DECISIONS.md` 2026-09-21 결정에 따라 모집단 정의는 `개방자치단체코드`가 정본이고
    (filter_target_districts), 주소텍스트는 이 함수의 QA 대조에만 쓴다. 불일치 행은
    삭제하지 않고 여기서 노출한다.
    """
    code_match = df["개방자치단체코드"].isin(district_codes.values())
    address = df["지번주소"].fillna("") + " " + df["도로명주소"].fillna("")
    text_match = pd.Series(False, index=df.index)
    for name in district_codes:
        text_match = text_match | address.str.contains(name, na=False)

    diff = code_match != text_match
    out = df[diff].copy()
    out["_code_match"] = code_match[diff]
    out["_text_match"] = text_match[diff]
    return out


def filter_target_districts(
    df: pd.DataFrame,
    district_codes: dict[str, str] = schema.DISTRICT_CODES,
    expected_rows: int | None = None,
) -> pd.DataFrame:
    """`개방자치단체코드` 기준 3구 필터.

    `DECISIONS.md` 2026-09-21 "3구 모집단 정의 기준": 모집단 포함/제외는 구조화 필드인
    개방자치단체코드로 결정하고, 주소텍스트는 QA 용도로만 쓴다(diagnose_district_filter).
    두 기준이 갈리는 20건은 어느 쪽이 틀렸는지 원본만으로 판정할 수 없으므로, 재현성이
    높은 코드 기준을 모집단 정의에 쓰고 불일치는 삭제하지 않고 진단 함수로 노출한다.
    표준화 파이프라인(`src/data/io_license.py:filter_target_gu`)과 동일 기준이라
    licenses_3gu.parquet과 모집단이 일치한다.
    """
    out = df[df["개방자치단체코드"].str.strip().isin(district_codes.values())].copy()
    if expected_rows is not None:
        assert len(out) == expected_rows, (
            f"3구 필터 결과 행수 불일치: {len(out)} != {expected_rows}"
        )
    return out


def combine_sources(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(dfs, ignore_index=True)


def build_store_id(df: pd.DataFrame, expected_duplicate_count: int = 0) -> pd.DataFrame:
    """store_id = '{업종 prefix}_{관리번호}'.

    prefix는 `config.BUSINESS_TYPES`가 단일 출처이며, 표준화 파이프라인
    (`src/data/standardize.py`)이 만드는 licenses_3gu.parquet의 store_id와 같은 규칙이다.
    두 테이블을 store_id로 직접 join하기 위한 조건이다.
    """
    out = df.copy()
    unknown = set(out["source_type"]) - set(schema.STORE_ID_PREFIXES)
    if unknown:
        raise ValueError(f"store_id prefix를 알 수 없는 source_type: {sorted(unknown)}")
    out["store_id"] = (
        out["source_type"].map(schema.STORE_ID_PREFIXES)
        + "_"
        + out["관리번호"].str.strip()
    )
    dup = int(out["store_id"].duplicated().sum())
    assert dup == expected_duplicate_count, (
        f"store_id 중복 {dup}건 (기대 {expected_duplicate_count}건)"
    )
    return out


def parse_dates(
    df: pd.DataFrame, license_col: str = "인허가일자", closure_col: str = "폐업일자"
) -> pd.DataFrame:
    """폐업일자의 공백 문자열은 `.str.strip()` 후 결측 처리한 뒤 파싱한다
    (DATA_CATALOG.md §1: 영업 중 행의 폐업일자가 NaN이 아닌 공백 문자열인 경우가 있음)."""
    out = df.copy()

    closure_stripped = out[closure_col].str.strip().replace("", pd.NA)
    out[f"{closure_col}_dt"] = pd.to_datetime(
        closure_stripped, format="%Y-%m-%d", errors="coerce"
    )
    out[f"{license_col}_dt"] = pd.to_datetime(
        out[license_col].str.strip(), format="%Y-%m-%d", errors="coerce"
    )

    closed_mask = closure_stripped.notna()
    assert not out.loc[~closed_mask, f"{closure_col}_dt"].notna().any(), (
        f"{closure_col} 파싱 오류: 영업중으로 표기된 행에서 {closure_col}_dt가 채워졌습니다 - "
        "strip 로직을 재확인하세요."
    )

    closed_n = int(closed_mask.sum())
    closed_parse_missing = (
        out.loc[closed_mask, f"{closure_col}_dt"].isna().mean() * 100 if closed_n > 0 else 0.0
    )
    print(
        f"{closure_col} 파싱 결과: 폐업표기 {closed_n}건 중 파싱 실패 {closed_parse_missing:.2f}%, "
        f"영업중표기 {int((~closed_mask).sum())}건"
    )
    license_missing = out[f"{license_col}_dt"].isna().mean() * 100
    print(f"{license_col} 파싱 결측률 {license_missing:.2f}%")

    return out


def generate_candidate_origins(
    min_origin: str, last_data_date: pd.Timestamp, maturity_cutoff_months: int
) -> list[pd.Period]:
    """`maturity_cutoff_months`는 기본값을 두지 않는다 - 호출부가 어떤 컷오프를 쓰는지
    항상 명시하도록 강제하기 위함이다. 확정값은 `label_schema.MATURITY_CUTOFF_MONTHS`
    (=1, DECISIONS.md 2026-09-18)이며, 민감도 비교용으로 다른 값을 넣을 수 있다."""
    start = pd.Period(min_origin, freq="Q")
    last_quarter = pd.Period(last_data_date, freq="Q")
    candidates = pd.period_range(start=start, end=last_quarter, freq="Q")

    cutoff_date = last_data_date - pd.DateOffset(months=maturity_cutoff_months)
    origins = [
        origin
        for origin in candidates
        # build_long_panel의 12개월 라벨 창이 origin_end 기준이므로(시간 누수 방지),
        # 관측 창 확보 여부도 origin_end 기준으로 판정해야 짝이 맞는다.
        if origin.end_time.normalize() + pd.DateOffset(months=schema.LONG_PANEL_WINDOW_MONTHS)
        <= cutoff_date
    ]
    assert len(origins) > 0, (
        "성숙 컷오프 적용 후 유효한 origin이 0개입니다 - "
        "maturity_cutoff_months 또는 last_data_date를 확인하세요."
    )
    return origins


def build_long_panel(df: pd.DataFrame, origins: list[pd.Period]) -> pd.DataFrame:
    origins_df = pd.DataFrame(
        {
            "origin": [str(o) for o in origins],
            "origin_start": [o.start_time for o in origins],
            "origin_end": [o.end_time.normalize() for o in origins],
        }
    )

    left = df.copy()
    left["_key"] = 1
    origins_df["_key"] = 1
    panel = left.merge(origins_df, on="_key").drop(columns="_key")

    # feature_asof/age_months/available_at이 모두 origin_end 기준이므로 적격 조건과
    # 라벨 창도 origin_end로 통일한다. origin_start를 쓰면 "feature_asof 시점에 이미
    # 폐업한 점포"가 event_12m=1로 패널에 남는 시간 누수가 생긴다.
    eligible = (panel["인허가일자_dt"] <= panel["origin_end"]) & (
        panel["폐업일자_dt"].isna() | (panel["폐업일자_dt"] > panel["origin_end"])
    )
    panel = panel[eligible].copy()

    window_end = panel["origin_end"] + pd.DateOffset(
        months=schema.LONG_PANEL_WINDOW_MONTHS
    )
    panel["event_12m"] = (
        panel["폐업일자_dt"].notna()
        & (panel["폐업일자_dt"] > panel["origin_end"])
        & (panel["폐업일자_dt"] <= window_end)
    ).astype(int)

    # origin_end 기준 통일(시간 누수 방지) - feature_asof 이전에 이미 폐업한 행이
    # 남아있지 않은지 최종 확인.
    assert (
        panel["폐업일자_dt"].isna() | (panel["폐업일자_dt"] > panel["origin_end"])
    ).all(), "패널에 origin_end 이전 폐업 행이 포함되었습니다 (시간 누수) - 필터 로직 오류"

    return panel


def add_panel_features(df: pd.DataFrame, maturity_cutoff_months: int) -> pd.DataFrame:
    out = df.copy()

    license_dt = out["인허가일자_dt"]
    origin_end = out["origin_end"]
    out["age_months"] = (origin_end.dt.year - license_dt.dt.year) * 12 + (
        origin_end.dt.month - license_dt.dt.month
    )
    assert (out["age_months"] >= 0).all(), (
        "age_months가 음수인 행이 있습니다 - "
        "패널 진입 조건(인허가일자<=origin_end) 위반 의심"
    )

    out["biz_type"] = out["source_type"]

    # 1,000㎡ 이상 값은 원본에 천단위 콤마가 붙어 있어("1,390.75") 콤마 제거 없이는
    # pd.to_numeric()이 전부 NaN으로 만든다 - 콤마 제거 후 변환한다.
    area_stripped = out["소재지면적"].astype("string").str.replace(",", "", regex=False)
    area_numeric = pd.to_numeric(area_stripped, errors="coerce")
    failed = out["소재지면적"].notna() & area_numeric.isna()
    n_failed = int(failed.sum())
    if n_failed > 0:
        samples = out.loc[failed, "소재지면적"].unique()[:5]
        print(f"[경고] 소재지면적: 숫자 변환 실패 {n_failed}건 (원본 값 예시: {list(samples)})")
    out["area"] = area_numeric

    out["has_coord"] = out["좌표정보(X)"].notna() & out["좌표정보(Y)"].notna()

    out["feature_asof"] = out["origin_end"]
    out["source_snapshot"] = out["source_type"].map(schema.SOURCE_SNAPSHOT)
    out["available_at"] = out["feature_asof"]
    out["maturity_cutoff_used_months"] = maturity_cutoff_months

    return out


def select_panel_output_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df[schema.PANEL_OUTPUT_COLUMNS].copy()


def recommend_maturity_cutoff(
    closure_dates: pd.Series,
    baseline_window_months: int = 6,
    drop_threshold: float = 0.75,
) -> dict:
    """탐색적 산출물 - assert 없음. 월별 폐업 신고 건수가 trailing baseline 대비 급락하는
    최근 개월을 단순 임계값 방식으로 찾는다 (formal changepoint 모델 아님, 의도적으로 단순화)."""
    monthly = closure_dates.dropna().dt.to_period("M").value_counts().sort_index()
    baseline = monthly.rolling(baseline_window_months, min_periods=1).median().shift(1)

    flagged: list[pd.Period] = []
    for period in monthly.index[::-1]:
        base = baseline.get(period)
        if pd.isna(base):
            continue
        if monthly[period] < drop_threshold * base:
            flagged.append(period)
        else:
            break
    flagged = sorted(flagged)

    return {
        "monthly_counts": monthly,
        "baseline": baseline,
        "flagged_months": flagged,
        "recommended_cutoff_months": len(flagged),
    }


def build_maturity_diagnostic_table(
    report: dict, last_data_date: pd.Timestamp, recent_months: int = 6
) -> pd.DataFrame:
    """recommend_maturity_cutoff() 결과를 사람이 판단할 수 있는 표로 정리한다.

    `months_ago=0`인 달은 last_data_date 기준으로 아직 끝나지 않은 '부분월'이라
    폐업 건수가 낮게 나오는 것이 당연하다 - 이것만 flag되어 있다면 신고 지연이
    아니라 단순히 관측 기간이 짧아서(달이 안 끝나서)일 가능성이 높다. `months_ago>=1`인
    달까지 낮게 나온다면 그 달은 이미 끝난 달이므로 신고 지연 쪽 설명이 더 설득력 있다.
    """
    monthly = report["monthly_counts"]
    baseline = report["baseline"]
    flagged = set(report["flagged_months"])
    last_month = last_data_date.to_period("M")

    rows = []
    for period in monthly.index:
        months_ago = (last_month - period).n
        if months_ago < 0 or months_ago >= recent_months:
            continue
        base = baseline.get(period)
        note = ""
        if months_ago == 0:
            note = f"부분월(당월 {last_data_date.day}/{period.days_in_month}일까지만 관측)"
        rows.append(
            {
                "월": str(period),
                "months_ago": months_ago,
                "폐업건수": int(monthly[period]),
                "trailing_baseline": round(float(base), 1) if pd.notna(base) else None,
                "건수/baseline": (
                    round(float(monthly[period]) / float(base), 2)
                    if pd.notna(base) and base > 0
                    else None
                ),
                "flagged": period in flagged,
                "비고": note,
            }
        )

    return pd.DataFrame(rows).sort_values("months_ago", ascending=False).reset_index(drop=True)


def plot_maturity_tail(report: dict, path: Path) -> None:
    monthly = report["monthly_counts"]
    baseline = report["baseline"]
    flagged = set(report["flagged_months"])

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(monthly.index.to_timestamp(), monthly.values, marker="o", label="월별 폐업 건수")
    ax.plot(
        baseline.index.to_timestamp(),
        baseline.values,
        linestyle="--",
        label="trailing median baseline",
    )
    flag_periods = [p for p in monthly.index if p in flagged]
    if flag_periods:
        ax.scatter(
            [p.to_timestamp() for p in flag_periods],
            [monthly[p] for p in flag_periods],
            color="red",
            zorder=5,
            label="성숙 컷오프 후보(tail 불안정)",
        )
    ax.set_xlabel("월")
    ax.set_ylabel("폐업 신고 건수")
    ax.set_title("월별 폐업 신고 건수 및 성숙 컷오프 tail 진단")
    ax.legend()
    fig.autofmt_xdate()

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_km_input(
    df: pd.DataFrame, source_type: str, district_code: str, as_of: pd.Timestamp
) -> pd.DataFrame:
    subset = df[
        (df["source_type"] == source_type) & (df["개방자치단체코드"] == district_code)
    ].copy()

    end_dt = subset["폐업일자_dt"].where(subset["폐업일자_dt"].notna(), as_of)
    end_dt = end_dt.clip(upper=as_of)
    duration_months = (end_dt - subset["인허가일자_dt"]).dt.days / 30.44
    event_observed = (
        subset["폐업일자_dt"].notna() & (subset["폐업일자_dt"] <= as_of)
    ).astype(int)

    out = pd.DataFrame(
        {
            "store_id": subset["store_id"].values,
            "duration_months": duration_months.values,
            "event_observed": event_observed.values,
        }
    )

    negative = out["duration_months"] < 0
    n_negative = int(negative.sum())
    if n_negative > 0:
        print(f"[경고] KM 입력: duration이 음수인 행 {n_negative}건 제외 (데이터 이상치)")
        out = out[~negative].copy()

    return out


def plot_km_curve(km_input: pd.DataFrame, path: Path, title: str) -> None:
    kmf = KaplanMeierFitter()
    kmf.fit(km_input["duration_months"], event_observed=km_input["event_observed"])

    max_duration = km_input["duration_months"].max()
    for months in (12, 24):
        if months <= max_duration:
            survival = float(kmf.survival_function_at_times(months).iloc[0])
            print(f"{title}: {months}개월 시점 생존율 {survival:.3f}")

    fig, ax = plt.subplots(figsize=(8, 5))
    kmf.plot_survival_function(ax=ax)
    ax.set_title(title)
    ax.set_xlabel("업력(개월)")
    ax.set_ylabel("생존 확률")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_km_curve_by_license_year_cohort(
    km_input: pd.DataFrame,
    license_years: pd.Series,
    path_prefix: Path,
    title: str,
    cohorts: list[tuple] = schema.LICENSE_YEAR_COHORTS,
) -> list[Path]:
    """개업 연도(인허가일자) 코호트별로 서로 다른 가게 집합에 독립적인 KM을 적합한다.

    이전 구현(단일 곡선을 업력 구간별로 x축만 잘라 보여주는 방식)은 통계적으로 무의미했다 -
    구간 시작이 항상 이전 구간의 끝과 같아 사실상 하나의 곡선을 나눠 읽은 것에 불과했다.
    여기서는 인허가일자 연도를 기준으로 서로 겹치지 않는 4개 가게 집합(코호트)을 만들고,
    각 코호트에 대해 duration=0(개업 시점)부터 시작하는 별도의 KaplanMeierFitter를
    fit()한다 - 한 가게는 정확히 하나의 코호트에만 속한다.

    `license_years`는 store_id -> 인허가연도(int) 매핑(Series, index=store_id)이다.
    `km_input`(build_km_input 반환값)의 duration_months는 이미 각 가게의 인허가일자를
    기준(0)으로 계산되어 있으므로 그대로 코호트별 KM 입력으로 쓸 수 있다.

    2025년처럼 최근에 개업한 코호트는 관측 가능한 최대 업력 자체가 짧으므로 곡선이
    일찍 끝나는 것이 정상이다 - 버그가 아니다.
    """
    km_input = km_input.copy()
    km_input["license_year"] = km_input["store_id"].map(license_years)

    saved_paths = []
    for start_year, end_year, label, suffix in cohorts:
        cohort = km_input[
            km_input["license_year"].between(start_year, end_year)
        ]
        n_stores = len(cohort)
        if n_stores == 0:
            print(f"[경고] {title} [{label}]: 표본 0건 - 건너뜀")
            continue

        n_events = int(cohort["event_observed"].sum())
        max_duration = float(cohort["duration_months"].max())
        print(
            f"{title} [{label}]: 표본 {n_stores}개 가게, 폐업 관측 {n_events}건, "
            f"관측 업력 최댓값 {max_duration:.1f}개월"
        )

        kmf = KaplanMeierFitter()
        kmf.fit(cohort["duration_months"], event_observed=cohort["event_observed"], label=label)

        fig, ax = plt.subplots(figsize=(8, 5))
        kmf.plot_survival_function(ax=ax)
        ax.set_title(f"{title} - {label} 코호트 (n={n_stores})")
        ax.set_xlabel("업력(개월, 개업 시점=0)")
        ax.set_ylabel("생존 확률")

        out_path = path_prefix.parent / f"{path_prefix.stem}_{suffix}{path_prefix.suffix}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(out_path)

    return saved_paths


def build_label_codebook_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        if col not in schema.VARIABLE_DEFINITIONS:
            raise KeyError(
                f"'{col}'의 정의가 label_schema.VARIABLE_DEFINITIONS에 없습니다. "
                "코드북에 TODO를 남기지 않는다 - 정의를 추가한 뒤 다시 실행하세요."
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


def build_exclusion_summary(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["사유", "건수"])


def build_constants_table() -> pd.DataFrame:
    rows = [
        {"상수": "DISTRICT_CODES", "값": str(schema.DISTRICT_CODES)},
        {"상수": "ENCODING_ERRORS_POLICY", "값": str(schema.ENCODING_ERRORS_POLICY)},
        {
            "상수": "EXPECTED_DISTRICT_FILTERED_ROWS",
            "값": str(schema.EXPECTED_DISTRICT_FILTERED_ROWS),
        },
        {
            "상수": "MATURITY_CUTOFF_MONTHS",
            "값": str(schema.MATURITY_CUTOFF_MONTHS),
        },
        {"상수": "MIN_ORIGIN_QUARTER", "값": schema.MIN_ORIGIN_QUARTER},
        {"상수": "LONG_PANEL_WINDOW_MONTHS", "값": str(schema.LONG_PANEL_WINDOW_MONTHS)},
    ]
    return pd.DataFrame(rows)


def write_label_spec_md(
    codebook_rows: pd.DataFrame,
    exclusion_rows: pd.DataFrame,
    maturity_report: dict,
    constants_table: pd.DataFrame,
    path: Path,
) -> None:
    lines = [
        "# 라벨 정의 명세 (LABEL_SPEC)",
        "",
        "`docs/W1_MDIS_AND_LABEL.md` §B-2 산출물.",
        "",
        "## 라벨 정의",
        "",
        codebook_rows.to_markdown(index=False),
        "",
        "## 경계 기준 상수",
        "",
        constants_table.to_markdown(index=False),
        "",
        "## 제외 사유별 건수",
        "",
        exclusion_rows.to_markdown(index=False),
        "",
        "## 성숙 컷오프 분석 결과",
        "",
        f"- 실측 권고 컷오프: {maturity_report['recommended_cutoff_months']}개월",
        f"- 불안정 tail 월: {[str(m) for m in maturity_report['flagged_months']]}",
        f"- 이번 실행에 적용한 확정 컷오프: {schema.MATURITY_CUTOFF_MONTHS}개월 "
        "(DECISIONS.md 2026-09-18 확정, labels_base.parquet의 maturity_cutoff_used_months와 일치)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
