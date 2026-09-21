"""MDIS 검증 표 작성 (W1 A-4).

mdis_stage_a/b.parquet을 Stage 3 DML(W3)에 넘기기 전에 처치군/대조군 공변량 균형과
가중치 영향을 확인한다. 이 모듈은 DML을 실행하지 않는다.

컬럼은 A-3(`src/data/mdis.py`)가 만든 최종 컬럼명(예: `tenure_months`, `treat_binary`)을
그대로 사용한다. A-3의 `mdis.py`/`mdis_schema.py`는 이 모듈에서 수정하지 않는다.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.data import mdis_schema as schema

_A4_COVARIATES = (
    schema.A4_CONTINUOUS_COVARIATES
    + schema.A4_CATEGORICAL_COVARIATES
    + schema.A4_BINARY_COVARIATES
)


def compute_missing_rates(df: pd.DataFrame) -> pd.Series:
    """컬럼별 결측률(%)을 반환한다."""
    return (df.isna().mean() * 100).round(2)


def coerce_weight_column(
    df: pd.DataFrame, col: str = schema.ROLE_COLUMNS["weight"][0]
) -> pd.Series:
    """`사업체수가중값`을 숫자형으로 변환한다.

    A-3의 `coerce_numeric_columns`는 `schema.NUMERIC_COLUMNS`만 처리하며 가중치 컬럼은
    대상이 아니므로 mdis_stage_a/b.parquet에는 문자열로 남아 있다. 변환 실패 건이 있으면
    조용히 버리지 않고 콘솔에 보고한다(A-3 컨벤션과 동일).
    """
    series = df[col]
    was_notna = series.notna()
    converted = pd.to_numeric(series, errors="coerce")
    failed = was_notna & converted.isna()
    n_failed = int(failed.sum())
    if n_failed > 0:
        samples = series.loc[failed].unique()[:5]
        print(
            f"[경고] {col}: 숫자 변환 실패 {n_failed}건 (원본 값 예시: {list(samples)}) "
            "- 원본 값 형식을 확인하세요."
        )
    return converted


def compute_smd_continuous(treat: pd.Series, control: pd.Series) -> float:
    """연속형 공변량의 표준화 평균차(SMD). 결측은 호출 전에 제거해 전달한다."""
    mean_t, mean_c = treat.mean(), control.mean()
    var_t, var_c = treat.var(ddof=1), control.var(ddof=1)
    pooled_sd = np.sqrt((var_t + var_c) / 2)
    if pooled_sd == 0:
        return np.nan
    return (mean_t - mean_c) / pooled_sd


def compute_smd_binary(treat: pd.Series, control: pd.Series) -> float:
    """이진형(비율) 공변량의 SMD. 범주형 더미 변환 후 개별 범주에도 동일하게 적용한다."""
    p_t, p_c = treat.mean(), control.mean()
    pooled_sd = np.sqrt((p_t * (1 - p_t) + p_c * (1 - p_c)) / 2)
    if pooled_sd == 0:
        return np.nan
    return (p_t - p_c) / pooled_sd


def compute_covariate_balance(
    df: pd.DataFrame, treat_col: str = "treat_binary"
) -> pd.DataFrame:
    """처치군/대조군 공변량 평균(연속형)·비율(이진형·범주형 더미) 비교 + SMD 표.

    범주형(`schema.A4_CATEGORICAL_COVARIATES`)은 더미 변환 후 범주별로 SMD를 계산한다.
    `|SMD| > schema.SMD_FLAG_THRESHOLD`이면 `flag=True`.
    """
    treat_mask = df[treat_col] == 1
    rows: list[dict] = []

    for col in schema.A4_CONTINUOUS_COVARIATES:
        t = df.loc[treat_mask, col].dropna()
        c = df.loc[~treat_mask, col].dropna()
        smd = compute_smd_continuous(t, c)
        rows.append(
            {
                "변수": col,
                "범주": "",
                "처치군 값": round(t.mean(), 3),
                "대조군 값": round(c.mean(), 3),
                "SMD": round(smd, 3),
                "flag": bool(abs(smd) > schema.SMD_FLAG_THRESHOLD),
            }
        )

    for col in schema.A4_BINARY_COVARIATES:
        t = df.loc[treat_mask, col].dropna()
        c = df.loc[~treat_mask, col].dropna()
        smd = compute_smd_binary(t, c)
        rows.append(
            {
                "변수": col,
                "범주": "",
                "처치군 값": round(t.mean(), 3),
                "대조군 값": round(c.mean(), 3),
                "SMD": round(smd, 3),
                "flag": bool(abs(smd) > schema.SMD_FLAG_THRESHOLD),
            }
        )

    for col in schema.A4_CATEGORICAL_COVARIATES:
        dummies = pd.get_dummies(df[col], prefix=col)
        for dummy_col in dummies.columns:
            t = dummies.loc[treat_mask, dummy_col]
            c = dummies.loc[~treat_mask, dummy_col]
            smd = compute_smd_binary(t, c)
            rows.append(
                {
                    "변수": col,
                    "범주": dummy_col[len(col) + 1 :],
                    "처치군 값": round(t.mean(), 3),
                    "대조군 값": round(c.mean(), 3),
                    "SMD": round(smd, 3),
                    "flag": bool(abs(smd) > schema.SMD_FLAG_THRESHOLD),
                }
            )

    return pd.DataFrame(rows)


def fit_propensity_scores(
    df: pd.DataFrame, treat_col: str = "treat_binary"
) -> pd.Series:
    """공변량으로 로지스틱 회귀를 적합해 성향점수를 추정한다.

    `schema.A4_CONTINUOUS_COVARIATES` + `A4_CATEGORICAL_COVARIATES` + `A4_BINARY_COVARIATES`
    중 하나라도 결측인 행은 이 모델 적합에서만 제외한다(listwise deletion). 제외 건수는
    조용히 버리지 않고 콘솔에 보고한다. 반환값은 `df`와 동일한 인덱스를 갖는 `pd.Series`이며
    제외된 행은 NaN이다.
    """
    complete_mask = df[_A4_COVARIATES].notna().all(axis=1)
    n_dropped = int((~complete_mask).sum())
    if n_dropped > 0:
        print(
            f"[A-4] 성향점수 모델: 공변량 결측으로 {n_dropped}건 제외 "
            f"(전체 {len(df)}건 중, 사용 {int(complete_mask.sum())}건)"
        )

    sub = df.loc[complete_mask]

    scaler = StandardScaler()
    cont_scaled = pd.DataFrame(
        scaler.fit_transform(sub[schema.A4_CONTINUOUS_COVARIATES]),
        columns=schema.A4_CONTINUOUS_COVARIATES,
        index=sub.index,
    )
    cat_dummies = pd.get_dummies(
        sub[schema.A4_CATEGORICAL_COVARIATES],
        columns=schema.A4_CATEGORICAL_COVARIATES,
        drop_first=True,
    )
    binary = sub[schema.A4_BINARY_COVARIATES]
    design_matrix = pd.concat([cont_scaled, cat_dummies, binary], axis=1)

    model = LogisticRegression(max_iter=1000)
    model.fit(design_matrix, sub[treat_col])
    proba = model.predict_proba(design_matrix)[:, 1]

    scores = pd.Series(np.nan, index=df.index, name="propensity_score")
    scores.loc[sub.index] = proba
    return scores


def plot_propensity_overlap(
    propensity: pd.Series, treat: pd.Series, out_path: Path
) -> None:
    """처치군/대조군 성향점수 분포를 겹쳐 그린 히스토그램을 `out_path`에 저장한다."""
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False

    t = propensity[treat == 1].dropna()
    c = propensity[treat == 0].dropna()

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(c, bins=30, density=True, alpha=0.5, label=f"대조군 (n={len(c)})")
    ax.hist(t, bins=30, density=True, alpha=0.5, label=f"처치군 (n={len(t)})")
    ax.set_xlabel("추정 성향점수 P(treat_binary=1 | X)")
    ax.set_ylabel("밀도")
    ax.set_title("성향점수 분포 겹침")
    ax.legend()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def compute_weighted_unweighted_profit(
    df: pd.DataFrame, weight: pd.Series, treat_col: str = "treat_binary"
) -> pd.DataFrame:
    """`profit_margin`의 처치군/대조군 가중·무가중 평균 + 처치효과 단순 차이.

    가중치는 `coerce_weight_column`으로 숫자형 변환된 `사업체수가중값`을 전달받는다.
    """
    treat_mask = df[treat_col] == 1
    rows: list[dict] = []
    for label, mask in [("처치군", treat_mask), ("대조군", ~treat_mask)]:
        vals = df.loc[mask, "profit_margin"].dropna()
        w = weight.loc[vals.index]
        rows.append(
            {
                "집단": label,
                "무가중 평균": round(vals.mean(), 4),
                "가중 평균": round(np.average(vals, weights=w), 4),
            }
        )
    result = pd.DataFrame(rows)

    treat_row = result.loc[result["집단"] == "처치군"].iloc[0]
    control_row = result.loc[result["집단"] == "대조군"].iloc[0]
    diff_row = pd.DataFrame(
        [
            {
                "집단": "처치효과(처치-대조)",
                "무가중 평균": round(treat_row["무가중 평균"] - control_row["무가중 평균"], 4),
                "가중 평균": round(treat_row["가중 평균"] - control_row["가중 평균"], 4),
            }
        ]
    )
    return pd.concat([result, diff_row], ignore_index=True)


def compute_stage_b_treat_cont_association(stage_b_df: pd.DataFrame) -> pd.DataFrame:
    """mdis_stage_b(857건)에서 `treat_cont`와 주요 공변량의 연관성/분포 쏠림을 점검한다.

    연속형·이진형은 Pearson 상관계수, 범주형은 범주별 `treat_cont` 평균 + 표본비율을 낸다.
    표본비율이 `schema.CATEGORY_CONCENTRATION_THRESHOLD`를 넘으면 `flag=True`(쏠림).
    """
    rows: list[dict] = []

    for col in schema.A4_CONTINUOUS_COVARIATES + schema.A4_BINARY_COVARIATES:
        sub = stage_b_df[[col, "treat_cont"]].dropna()
        corr = sub[col].corr(sub["treat_cont"])
        rows.append(
            {
                "변수": col,
                "범주": "",
                "지표": "Pearson r (treat_cont)",
                "값": round(corr, 3),
                "표본비율": "",
                "flag": "",
            }
        )

    for col in schema.A4_CATEGORICAL_COVARIATES:
        shares = stage_b_df[col].value_counts(normalize=True)
        means = stage_b_df.groupby(col)["treat_cont"].mean()
        for category, share in shares.items():
            rows.append(
                {
                    "변수": col,
                    "범주": category,
                    "지표": "범주별 treat_cont 평균",
                    "값": round(means.loc[category], 3),
                    "표본비율": round(share, 3),
                    "flag": bool(share > schema.CATEGORY_CONCENTRATION_THRESHOLD),
                }
            )

    return pd.DataFrame(rows)


def render_validation_report_md(
    missing_rates: pd.Series,
    balance_df: pd.DataFrame,
    propensity_fig_rel_path: str,
    n_excluded_ps: int,
    profit_df: pd.DataFrame,
    assoc_df: pd.DataFrame,
) -> str:
    """A-4 검증 결과를 마크다운 절 본문(문자열)으로 렌더링한다."""
    high_missing = missing_rates[missing_rates > schema.MISSING_RATE_REPORT_THRESHOLD]

    lines = [
        "## A-4 검증 결과",
        "",
        "`docs/W1_MDIS_AND_LABEL.md` A-4 산출물. `scripts/run_mdis_validation.py`로 재생성한다. "
        "DML은 이 단계에서 실행하지 않는다.",
        "",
        "### 결측률",
        "",
    ]
    if len(high_missing) > 0:
        lines.append(f"결측률 {schema.MISSING_RATE_REPORT_THRESHOLD}% 초과 변수 {len(high_missing)}개 (팀 보고 대상):")
        lines.append(high_missing.to_frame("결측률(%)").to_markdown())
    else:
        lines.append(f"결측률 {schema.MISSING_RATE_REPORT_THRESHOLD}% 초과 변수 없음.")

    lines += [
        "",
        f"### 공변량 균형 (SMD, |SMD| > {schema.SMD_FLAG_THRESHOLD} 이면 flag)",
        "",
        balance_df.to_markdown(index=False),
        "",
        "### 성향점수 겹침",
        "",
        f"공변량 결측으로 {n_excluded_ps}건을 제외하고 로지스틱 회귀로 적합했다.",
        f"![성향점수 겹침]({propensity_fig_rel_path})",
        "",
        "### 가중치 적용 전후 영업이익률(profit_margin) 비교",
        "",
        profit_df.to_markdown(index=False),
        "",
        f"### mdis_stage_b treat_cont 연관성 / 범주 쏠림 (표본비율 > {schema.CATEGORY_CONCENTRATION_THRESHOLD} 이면 flag)",
        "",
        assoc_df.to_markdown(index=False),
        "",
    ]
    return "\n".join(lines)


def append_validation_section_md(section_md: str, path: Path) -> None:
    """`path`(MDIS_CODEBOOK.md)에 A-4 검증 절을 마커 기반으로 덧붙인다.

    마커(`<!-- A4-VALIDATION-START/END -->`) 사이를 교체하므로 재실행해도 중복되지 않는다.
    A-3의 `mdis.write_codebook_md`는 이 블록과 수기 메모 블록
    (`<!-- MANUAL-NOTES-START/END -->`)을 보존하므로 실행 순서에 상관없이 살아남는다.
    """
    start_marker = "<!-- A4-VALIDATION-START -->"
    end_marker = "<!-- A4-VALIDATION-END -->"
    block = f"{start_marker}\n{section_md}\n{end_marker}"

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if start_marker in existing and end_marker in existing:
        pre, rest = existing.split(start_marker, 1)
        _, post = rest.split(end_marker, 1)
        new_content = pre + block + post
    else:
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        new_content = existing + sep + block + "\n"

    path.write_text(new_content, encoding="utf-8")
