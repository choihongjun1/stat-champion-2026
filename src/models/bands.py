# -*- coding: utf-8 -*-
"""위험 등급(`band`) 배정 — low / mid / high.

두 방식이 있고 성격이 다르다.

absolute  절대 확률 컷오프. "위험도 20% 이상이면 high".
          - 뜻이 고정된다. 내년에 다시 돌려도 같은 확률이면 같은 등급이다.
          - 업종·지역이 달라도 같은 기준이므로 비교가 된다.
          - 전체 위험도가 낮은 업종은 high가 거의 안 나올 수 있다.

relative  백분위 컷오프. "상위 10%면 high".
          - 항상 정해진 비율이 high로 나온다. 화면 분포가 안정적이다.
          - **"high = 상위 10%"라는 동어반복**이 되고, 전체가 안전해져도
            누군가는 반드시 high가 된다. 사장님에게 설명하기 나쁘다.

peer 백분위(`risk.percentile`)는 어차피 스키마에 따로 있으므로,
band는 absolute로 두고 상대 위치는 percentile이 맡는 편이 중복이 없다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BANDS = ("low", "mid", "high")


def assign_bands_absolute(
    p: np.ndarray, *, cut_mid: float, cut_high: float
) -> np.ndarray:
    """절대 확률 컷오프로 등급을 매긴다. p < cut_mid < cut_high."""
    if not (0 < cut_mid < cut_high < 1):
        raise ValueError(f"컷오프 순서가 잘못됐다: {cut_mid}, {cut_high}")
    p = np.asarray(p, dtype=float)
    out = np.full(len(p), "low", dtype=object)
    out[p >= cut_mid] = "mid"
    out[p >= cut_high] = "high"
    return out


def assign_bands_relative(
    p: np.ndarray, *, q_mid: float = 0.70, q_high: float = 0.90
) -> tuple[np.ndarray, dict]:
    """백분위 컷오프. 실제 사용한 확률 컷오프도 함께 반환한다."""
    p = np.asarray(p, dtype=float)
    cm, ch = float(np.quantile(p, q_mid)), float(np.quantile(p, q_high))
    return assign_bands_absolute(p, cut_mid=cm, cut_high=ch), {
        "cut_mid": cm,
        "cut_high": ch,
        "q_mid": q_mid,
        "q_high": q_high,
    }


def suggest_cutoffs(
    y: np.ndarray,
    p: np.ndarray,
    *,
    target_high_lift: float = 2.0,
    target_mid_lift: float = 1.2,
) -> dict:
    """데이터로 컷오프를 고른다.

    기준: **등급이 실제 위험도 차이를 반영해야 한다.**
    high 구간의 실측 폐업률이 전체 평균의 `target_high_lift`배 이상,
    mid 구간은 `target_mid_lift`배 이상이 되는 가장 낮은 확률 컷오프를 찾는다.

    이렇게 하면 "high = 전체 평균의 2배 이상 위험"이라는 설명 가능한 정의가 된다.
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    base = y.mean()

    grid = np.unique(np.quantile(p, np.linspace(0.50, 0.995, 200)))
    cut_high = None
    for c in grid:
        m = p >= c
        if m.sum() < 200:
            continue
        if y[m].mean() >= base * target_high_lift:
            cut_high = float(c)
            break
    if cut_high is None:
        cut_high = float(np.quantile(p, 0.95))

    grid_mid = np.unique(np.quantile(p, np.linspace(0.20, 0.90, 200)))
    cut_mid = None
    for c in grid_mid:
        if c >= cut_high:
            break
        m = (p >= c) & (p < cut_high)
        if m.sum() < 200:
            continue
        if y[m].mean() >= base * target_mid_lift:
            cut_mid = float(c)
            break
    if cut_mid is None:
        cut_mid = float(np.quantile(p, 0.70))

    return {"cut_mid": cut_mid, "cut_high": cut_high, "base_rate": float(base)}


def band_profile(y: np.ndarray, p: np.ndarray, bands: np.ndarray) -> pd.DataFrame:
    """등급별 점유율과 실측 폐업률 — 컷오프가 타당한지 보는 표."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    base = y.mean()
    rows = []
    for b in BANDS:
        m = bands == b
        if not m.any():
            rows.append({"band": b, "n": 0, "share": 0.0})
            continue
        rows.append(
            {
                "band": b,
                "n": int(m.sum()),
                "share": float(m.mean()),
                "pred_mean": float(p[m].mean()),
                "obs_rate": float(y[m].mean()),
                "lift": float(y[m].mean() / base) if base > 0 else float("nan"),
            }
        )
    return pd.DataFrame(rows)
