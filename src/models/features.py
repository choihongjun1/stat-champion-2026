# -*- coding: utf-8 -*-
"""W2-2 모델 입력 행렬 — master_base에서 predictor만 꺼내 모형이 받을 수 있는 형태로 바꾼다.

입력 컬럼의 **단일 출처는 `src.data.master_schema.COLUMN_ROLES`**다.
master_base에는 ER 매칭 결과(`er_matched` 등)·상권 배정(`trdar_cd` 등)·시점 메타가 함께
들어 있다. 이 컬럼들은 predictor가 아니며, 특히 `er_matched`는 점포 생존의 결과라서
입력에 들어가면 곧바로 누수다 (DECISIONS.md 2026-09-23 W2-0 I-2). 그래서 "메타만 빼고
나머지 전부"가 아니라 **role == predictor인 것만** 가져간다.

결측 지시자(`*_isna`)는 만들지 않는다.
- 상권 feature 결측 = 상권 polygon 밖(또는 origin 2021Q1). polygon 소속 여부는 predictor로
  쓰지 않기로 했다 (DECISIONS.md 2026-09-23). 지시자를 붙이면 그 정보가 명시적으로 들어간다.
- 공시지가 결측 = origin 2021Q1~2024Q1 (구조적). 지시자가 곧 시기 변수가 된다.
HistGradientBoosting은 NaN을 그대로 받으므로 지시자 없이도 학습된다. NaN 분기로 새는
정보의 크기는 feature set 비교(`FEATURE_SETS`)로 따로 잰다.
"""
from __future__ import annotations

import pandas as pd

from src.data import master_schema as ms

# 범주형 predictor. 순서가 없으므로 category dtype으로 넘긴다.
CATEGORICAL = ("biz_type", "gu", "trdar_change_index")

# 민감도 분석용 feature set. 값은 predictor 목록에 적용할 규칙.
#   drop_groups / drop_cols : 빼기   keep_groups : 이 group만 남기기
FEATURE_SETS: dict[str, dict] = {
    "base": {},
    # 상권 feature 전체 제외 — polygon 소속 여부가 NaN 분기로 새는 효과 + 상권 정보 기여 확인
    "no_trdar": {"drop_groups": ("trdar", "trdar_biz")},
    # 공시지가 제외 — 2021Q1~2024Q1 구조적 NA가 시기 변수 역할을 하는지 확인
    "no_land_price": {"drop_cols": ("land_price",)},
    # 자치구 제외 — 3구 × 3업종이라 업종·상권 feature와 상관이 큼 (MASTER_SPEC 메모)
    "no_gu": {"drop_cols": ("gu",)},
    # 인허가 기본 속성만 — 하한선 모형
    "license_only": {"keep_groups": ("license",)},
}


def group_of(col: str) -> str:
    return ms.COLUMN_ROLES[col][1]


def select_features(columns, feature_set: str = "base") -> list[str]:
    """feature set 규칙을 적용한 predictor 목록. `columns`에 없는 predictor는 뺀다."""
    if feature_set not in FEATURE_SETS:
        raise KeyError(f"알 수 없는 feature set: {feature_set} (가능: {list(FEATURE_SETS)})")
    rule = FEATURE_SETS[feature_set]
    cols = ms.predictor_columns(columns)
    if "keep_groups" in rule:
        cols = [c for c in cols if group_of(c) in rule["keep_groups"]]
    cols = [c for c in cols if group_of(c) not in rule.get("drop_groups", ())]
    cols = [c for c in cols if c not in rule.get("drop_cols", ())]
    assert_predictors_only(cols)
    return cols


def assert_predictors_only(cols) -> None:
    """입력 컬럼이 전부 predictor이고 금지 목록에 걸리지 않는지 확인한다."""
    allowed = set(ms.predictor_columns())
    bad = [c for c in cols if c not in allowed]
    forbidden = [
        c for c in cols
        if c in ms.FORBIDDEN_PREDICTORS or c.startswith(ms.FORBIDDEN_PREDICTOR_PREFIXES)
    ]
    if bad or forbidden:
        raise ValueError(f"predictor가 아닌 입력: {bad} / 금지 컬럼: {forbidden}")


def fit_categories(df: pd.DataFrame, cols) -> dict[str, list]:
    """범주형 컬럼의 범주 목록을 고정한다. 학습·예측에서 같은 코드를 쓰기 위해서다."""
    out = {}
    for c in cols:
        if c in CATEGORICAL:
            vals = pd.Series(df[c]).dropna().astype(str).unique().tolist()
            out[c] = sorted(vals)
    return out


def build_X(df: pd.DataFrame, cols, categories: dict[str, list] | None = None) -> pd.DataFrame:
    """모형 입력 행렬.

    - 범주형 → category dtype (범주는 `categories`로 고정, 없으면 df에서 만든다.
      모르는 범주는 NaN이 된다)
    - bool / nullable boolean → float (NA는 NaN)
    - nullable 정수·실수(Int64/Float64) → float64 (pd.NA → NaN). sklearn은 pd.NA를 받지 못한다.
    - 그 외 dtype이 숫자가 아니면 멈춘다 (범주형 등록 누락을 조용히 넘기지 않기 위해)
    """
    assert_predictors_only(cols)
    if categories is None:
        categories = fit_categories(df, cols)
    X = pd.DataFrame(index=df.index)
    for c in cols:
        s = df[c]
        if c in CATEGORICAL:
            vals = s.astype(object).where(s.notna(), None)
            vals = vals.map(lambda v: v if v is None else str(v))
            X[c] = pd.Categorical(vals, categories=categories[c])
        elif pd.api.types.is_bool_dtype(s.dtype):
            X[c] = s.astype("Float64").astype("float64")
        elif pd.api.types.is_numeric_dtype(s.dtype):
            X[c] = pd.to_numeric(s, errors="raise").astype("Float64").astype("float64")
        else:
            raise TypeError(f"{c}: 숫자도 범주형도 아닌 dtype({s.dtype}). CATEGORICAL 등록이 필요한지 확인")
    return X


def missing_by_origin(df: pd.DataFrame, cols, origin_col: str = "origin") -> pd.DataFrame:
    """origin × feature 결측률 표 (보고용)."""
    return df[cols].isna().groupby(df[origin_col]).mean().round(4)


__all__ = [
    "CATEGORICAL", "FEATURE_SETS", "select_features", "assert_predictors_only",
    "fit_categories", "build_X", "missing_by_origin", "group_of",
]

