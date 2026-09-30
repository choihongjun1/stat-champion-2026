# -*- coding: utf-8 -*-
"""Stage 1 탐지 모형 — 학습·예측 래퍼.

입력 행렬은 `src.models.features.build_X`로 만든다 (predictor만, 결측 지시자 없음).

설계 제약
---------
- **결측 행을 삭제하지 않는다** (DECISIONS.md 2026-09-13 complete-case 금지).
  HistGradientBoosting은 NaN을 그대로 받아 분기한다.
- 범주형은 category dtype으로 넘기고 `categorical_features="from_dtype"`로 native 처리한다.
  범주 목록은 학습 시점에 고정해 두고(`categories_`), 예측할 때 같은 목록으로 맞춘다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

DEFAULT_PARAMS = dict(
    loss="log_loss",
    learning_rate=0.06,
    max_iter=400,
    max_leaf_nodes=31,
    min_samples_leaf=200,
    l2_regularization=1.0,
    early_stopping=False,
    random_state=20260922,
)


@dataclass
class DetectModel:
    """HistGradientBoosting 기반 탐지 모형."""

    params: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    model_: HistGradientBoostingClassifier | None = None
    columns_: list[str] | None = None
    categories_: dict[str, list] | None = None
    dropped_all_na_: list[str] = field(default_factory=list)

    def fit(self, X: pd.DataFrame, y) -> "DetectModel":
        # 학습 구간에서 값이 하나도 없는 컬럼은 뺀다. 초기 fold(2021~)는 land_price가 구조적으로 전부
        # NA인데, 전부 NaN인 컬럼은 HistGradientBoosting binning이 실패한다. 예측 때도 같은 컬럼만 쓴다.
        self.dropped_all_na_ = [c for c in X.columns if X[c].isna().all()]
        X = X.drop(columns=self.dropped_all_na_)
        self.model_ = HistGradientBoostingClassifier(categorical_features="from_dtype", **self.params)
        self.model_.fit(X, np.asarray(y))
        self.columns_ = list(X.columns)
        self.categories_ = {
            c: list(X[c].cat.categories) for c in X.columns if isinstance(X[c].dtype, pd.CategoricalDtype)
        }
        return self

    def _align(self, X: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.columns_ if c not in X.columns]
        if missing:
            raise ValueError(f"예측 입력에 학습 컬럼이 없다: {missing}")
        X = X[self.columns_].copy()
        for c, cats in self.categories_.items():
            if list(X[c].cat.categories) != cats:
                X[c] = pd.Categorical(X[c].astype(object), categories=cats)
        return X

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if self.model_ is None:
            raise RuntimeError("fit을 먼저 호출한다")
        return self.model_.predict_proba(self._align(X))[:, 1]


def fit_predict(X_tr, y_tr, X_te, params: dict | None = None) -> np.ndarray:
    """부트스트랩·rolling 평가에서 쓰는 1회용 학습·예측."""
    return DetectModel(params=dict(params or DEFAULT_PARAMS)).fit(X_tr, y_tr).predict_proba(X_te)


def ranking_metrics(y, p) -> dict:
    """순위 성능. 보정과 별개로 본다."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    y = np.asarray(y)
    p = np.asarray(p)
    base = float(y.mean())
    both = 0 < y.sum() < len(y)
    ap = float(average_precision_score(y, p)) if both else float("nan")
    return {
        "auc": float(roc_auc_score(y, p)) if both else float("nan"),
        "ap": ap,
        "ap_lift": ap / base if base else float("nan"),
        "base_rate": base,
        "n": int(len(y)),
    }
