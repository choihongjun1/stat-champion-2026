# -*- coding: utf-8 -*-
"""위험도의 불확실성 구간 — 리포트 스키마의 `ci_low` / `ci_high`.

**채택: 부트스트랩(점포 단위 리샘플)** — W2-2 설계 결정 1 (`DECISIONS.md` 기록 대상).

Bootstrap (채택)
    "학습 데이터가 조금 달랐다면 예측이 얼마나 흔들렸을까"를 준다.
    모형을 B번 학습해야 해서 B배 느리다. 보정은 별도로 한다.
    패널이므로 **반드시 점포(store_id) 단위로 리샘플**한다.

Venn-ABERS (비교용, 화면에 쓰지 않음)
    보정 맵의 불확실성만 담는다. 보정 표본이 수만 행이면 폭이 거의 0이 되어
    (실측 평균 폭 0.0013, 구간 커버 0/10) 화면 구간으로 쓸 수 없다.

어느 쪽이든 화면 문구에는 이것이 "점포가 폐업할 확률의 범위"가 아니라
"예측이 얼마나 흔들리는가"의 구간임을 적는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


# ---------------------------------------------------------------------------
# Venn-ABERS (Inductive Venn-ABERS Predictor)
# ---------------------------------------------------------------------------
def venn_abers_interval(
    s_cal: np.ndarray,
    y_cal: np.ndarray,
    s_test: np.ndarray,
    *,
    n_grid: int = 200,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Venn-ABERS 구간 [p0, p1]과 점추정치를 계산한다.

    원리: 검증 점수 s를 보정 집합에 넣고 그 라벨을 0이라 가정해 isotonic을
    적합한 값(p0)과, 1이라 가정해 적합한 값(p1)을 구한다. 진짜 확률은
    두 값 사이에 있다. 두 가정 사이의 벌어짐이 곧 불확실성이다.

    정확한 IVAP는 검증 점 하나마다 isotonic을 두 번 적합해야 한다.
    여기서는 점수 격자 `n_grid`개에서만 계산하고 선형 보간한다
    (isotonic은 단조라 보간 오차가 작다). n_grid를 올리면 정확도가 올라간다.

    Returns
    -------
    (p_lo, p_hi, p_point) — 각각 s_test와 같은 길이
    """
    s_cal = np.asarray(s_cal, dtype=float)
    y_cal = np.asarray(y_cal, dtype=float)
    s_test = np.asarray(s_test, dtype=float)

    lo_q, hi_q = float(s_cal.min()), float(s_cal.max())
    grid = np.unique(
        np.concatenate(
            [
                np.quantile(s_cal, np.linspace(0, 1, n_grid)),
                np.array([lo_q, hi_q]),
            ]
        )
    )

    p0 = np.empty(len(grid))
    p1 = np.empty(len(grid))
    for i, g in enumerate(grid):
        xs = np.append(s_cal, g)
        for label, out in ((0.0, p0), (1.0, p1)):
            ys = np.append(y_cal, label)
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(xs, ys)
            out[i] = iso.predict([g])[0]

    # p0 <= p1 보장 (수치 오차 방어)
    p0, p1 = np.minimum(p0, p1), np.maximum(p0, p1)

    s_clip = np.clip(s_test, grid[0], grid[-1])
    lo = np.interp(s_clip, grid, p0)
    hi = np.interp(s_clip, grid, p1)
    # Venn-ABERS 점추정: p1 / (1 - p0 + p1)
    denom = 1.0 - lo + hi
    point = np.where(denom > 0, hi / denom, (lo + hi) / 2.0)
    return lo, hi, np.clip(point, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def bootstrap_interval(
    fit_predict,
    X_train,
    y_train,
    X_test,
    *,
    n_boot: int = 30,
    alpha: float = 0.10,
    seed: int = 20260922,
    group: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """부트스트랩 재학습으로 예측 분포를 만들고 백분위 구간을 낸다.

    Parameters
    ----------
    fit_predict : (X_tr, y_tr, X_te) -> 예측확률 배열
    group : 주면 **점포 단위**로 리샘플한다. 패널에서는 이게 맞다 —
            행 단위로 뽑으면 같은 점포의 18개 행이 쪼개져 독립처럼 취급되어
            구간이 실제보다 좁게 나온다.
    alpha : 0.10이면 5~95 백분위 구간
    """
    rng = np.random.default_rng(seed)
    n = len(y_train)
    preds = np.empty((n_boot, len(X_test)))

    if group is not None:
        # 점포별 행 위치. `group == g`를 점포마다 반복하면 O(점포 수 × 행 수)라
        # 실데이터(44,067점포 × 수십만 행)에서 수 분이 걸린다 → groupby 인덱스로 한 번에 만든다.
        pos_by_group = pd.Series(np.arange(len(group))).groupby(np.asarray(group)).indices
        uniq = np.array(list(pos_by_group.keys()), dtype=object)
        pos = [pos_by_group[g] for g in uniq]

    for b in range(n_boot):
        if group is None:
            idx = rng.integers(0, n, n)
        else:
            picked = rng.integers(0, len(uniq), len(uniq))
            idx = np.concatenate([pos[k] for k in picked])
        Xb = X_train.iloc[idx] if hasattr(X_train, "iloc") else X_train[idx]
        preds[b] = fit_predict(Xb, np.asarray(y_train)[idx], X_test)

    lo = np.percentile(preds, 100 * alpha / 2, axis=0)
    hi = np.percentile(preds, 100 * (1 - alpha / 2), axis=0)
    return lo, hi, preds.mean(axis=0)


# ---------------------------------------------------------------------------
# 구간 품질 진단
# ---------------------------------------------------------------------------
def interval_diagnostics(y, p_lo, p_hi, *, n_bins: int = 10) -> dict:
    """구간이 쓸만한지 보는 최소 지표.

    coverage_by_bin : 점수 구간별로 실측 발생률이 [lo, hi] 안에 들어오는 비율.
                      1에 가까워야 한다. 구간 자체가 점포 단위 "정답"을 갖지
                      않으므로 개별 coverage는 정의되지 않고, 구간 집계로 본다.
    """
    y = np.asarray(y, dtype=float)
    p_lo = np.asarray(p_lo, dtype=float)
    p_hi = np.asarray(p_hi, dtype=float)
    mid = (p_lo + p_hi) / 2

    edges = np.unique(np.quantile(mid, np.linspace(0, 1, n_bins + 1)))
    idx = np.clip(np.digitize(mid, edges[1:-1]), 0, len(edges) - 2)
    covered, total = 0, 0
    for b in range(len(edges) - 1):
        m = idx == b
        if not m.any():
            continue
        obs = y[m].mean()
        total += 1
        if p_lo[m].mean() <= obs <= p_hi[m].mean():
            covered += 1
    return {
        "mean_width": float(np.mean(p_hi - p_lo)),
        "median_width": float(np.median(p_hi - p_lo)),
        "p90_width": float(np.percentile(p_hi - p_lo, 90)),
        "bin_coverage": covered / total if total else float("nan"),
        "n_bins_checked": total,
    }
