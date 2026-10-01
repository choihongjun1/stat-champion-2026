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

# 컷오프 규칙의 상수 — suggest_cutoffs 기본값이자 run_meta.band_provenance에 그대로 기록하는 값이다.
TARGET_HIGH_LIFT = 2.0
TARGET_MID_LIFT = 1.2
HIGH_GRID_QUANTILES = (0.50, 0.995, 200)  # cut_high 후보: 예측 확률 50~99.5 백분위를 200등분한 격자 (np.unique 후 오름차순)
HIGH_MIN_GROUP = 200                      # {p ≥ c} 집단이 이보다 작은 후보는 건너뛴다
HIGH_FALLBACK_QUANTILE = 0.95
LIFT_CI_SEED = 20260927
LIFT_CI_PERCENTILES = (2.5, 97.5)

# 컷오프 정의 — 코드·DECISIONS·run_meta·serve_meta가 같은 문장을 쓴다(#45, choihongjun1 지적 1번).
CUT_MID_DEFINITION = ("cut_mid = 1.2 × base_rate (보정 창 OOF 관측 폐업률의 1.2배인 개별 예측 확률 임계값이다. "
                      "'실측 lift 1.2배가 되는 컷오프'가 아니다. cut_mid ≥ cut_high이면 base_rate로 대체하고 mid_fallback에 기록)")
CUT_HIGH_DEFINITION = ("cut_high = 보정 창 OOF 예측 확률의 50~99.5 백분위 200등분 격자를 낮은 쪽부터 훑어, {p ≥ c} 집단이 "
                       "200곳 이상이고 그 관측 폐업률이 base_rate의 2배 이상인 첫 c. 그런 c가 없으면 예측 확률 95백분위로 "
                       "fallback한다(fallback 여부는 high_fallback에 기록)")


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
    target_high_lift: float = TARGET_HIGH_LIFT,
    target_mid_lift: float = TARGET_MID_LIFT,
) -> dict:
    """데이터로 컷오프를 고른다.

    기준: **등급이 실제 위험도 차이를 반영해야 한다.**
    - high: 50~99.5 백분위 격자에서 {p ≥ c} 집단(≥ `HIGH_MIN_GROUP`곳)의 실측 폐업률이 평균의
      `target_high_lift`배 이상이 되는 첫 컷오프 → "high 등급 점포들은 실제로 평균의 2배 위험"
    - mid: 예측 확률이 평균의 `target_mid_lift`배 이상 → "예측 위험이 평균보다 높은 점포"

    정의 문장은 `CUT_MID_DEFINITION`·`CUT_HIGH_DEFINITION`. 기본 동작은 그대로이고, 조건을 만족하는 컷오프가 없어
    fallback을 썼는지(`high_fallback`, `mid_fallback`)를 함께 돌려준다.
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    base = y.mean()

    grid = np.unique(np.quantile(p, np.linspace(*HIGH_GRID_QUANTILES)))
    cut_high = None
    high_fallback = False
    for c in grid:
        m = p >= c
        if m.sum() < HIGH_MIN_GROUP:
            continue
        if y[m].mean() >= base * target_high_lift:
            cut_high = float(c)
            break
    if cut_high is None:
        cut_high = float(np.quantile(p, HIGH_FALLBACK_QUANTILE))
        high_fallback = True

    # mid는 "예측 위험이 평균의 target_mid_lift배 이상"인 점포다 (개별 예측값 기준).
    # 구간 평균 lift로 찾으면(이전 방식) 구간 안에 평균 미만 점포가 섞여 컷오프가 base rate 아래로
    # 내려간다 — 실데이터에서 mid 0.0846 < base 0.1245, mid 비율 61%가 됐다. 보정 오차(ECE)가
    # 작을 때 개별 예측값 기준이 "평균 대비 N배"라는 설명과 일치한다.
    cut_mid = float(base * target_mid_lift)
    mid_fallback = False
    if cut_mid >= cut_high:
        cut_mid = float(base)
        mid_fallback = True

    return {"cut_mid": cut_mid, "cut_high": cut_high, "base_rate": float(base),
            "high_fallback": high_fallback, "mid_fallback": mid_fallback}


def high_lift_cluster_ci(y, is_high, clusters, *, n_boot: int = 1000, seed: int = LIFT_CI_SEED
                         ) -> tuple[float, float, float]:
    """high 등급의 lift(= high 관측 폐업률 / 전체 관측 폐업률)와 점포 단위 부트스트랩 95% 구간. 반환: (lift, lo, hi).
    행을 복제하지 않고 정수 가중치(np.bincount)로 계산한다."""
    y = np.asarray(y, dtype=float)
    h = np.asarray(is_high, dtype=bool)
    codes, uniq = pd.factorize(pd.Series(np.asarray(clusters)))
    rng = np.random.default_rng(seed)
    n = len(uniq)
    vals = np.empty(n_boot)
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n)[codes].astype(float)
        wh = w * h
        vals[b] = (wh @ y / wh.sum()) / (w @ y / w.sum()) if wh.sum() > 0 and w @ y > 0 else np.nan
    lift = float((y[h].mean() / y.mean())) if h.any() and y.mean() > 0 else float("nan")
    lo, hi = np.nanpercentile(vals, list(LIFT_CI_PERCENTILES))
    return lift, float(lo), float(hi)


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
