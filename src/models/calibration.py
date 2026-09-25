# -*- coding: utf-8 -*-
"""확률 보정(calibration)과 reliability 진단.

화면에 "위험도 18.7%"를 그대로 쓰려면 예측 확률이 실제 발생률과 일치해야 한다.
순위만 맞는 모형은 AUC가 높아도 이 용도로는 쓸 수 없다.

보정기는 **학습에 쓰지 않은 보정 구간**에서 적합한다 (`splits.final_holdout` 참조).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


def brier_score(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y: np.ndarray, p: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def reliability_table(
    y: np.ndarray, p: np.ndarray, *, n_bins: int = 10, strategy: str = "quantile"
) -> pd.DataFrame:
    """reliability 곡선의 원천 표.

    strategy='quantile'이면 분위수 구간(구간별 표본 수가 균등),
    'uniform'이면 [0,1] 등간격 구간.
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)

    if strategy == "quantile":
        edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
    else:
        edges = np.linspace(p.min(), p.max(), n_bins + 1)
    if len(edges) < 2:
        edges = np.array([p.min(), p.max() + 1e-9])

    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, len(edges) - 2)
    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        if not m.any():
            continue
        rows.append(
            {
                "bin": b + 1,
                "p_low": float(edges[b]),
                "p_high": float(edges[b + 1]),
                "n": int(m.sum()),
                "pred_mean": float(p[m].mean()),
                "obs_rate": float(y[m].mean()),
                "gap": float(p[m].mean() - y[m].mean()),
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(
    y: np.ndarray, p: np.ndarray, *, n_bins: int = 10
) -> float:
    """ECE — reliability 표의 |예측-실측| 가중 평균. 0에 가까울수록 좋다."""
    t = reliability_table(y, p, n_bins=n_bins)
    if t.empty:
        return float("nan")
    w = t["n"] / t["n"].sum()
    return float((w * (t["pred_mean"] - t["obs_rate"]).abs()).sum())


class IsotonicCalibrator:
    """단조 보정. 순위는 보존하고 확률 수준만 실측에 맞춘다."""

    def __init__(self) -> None:
        self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self.fitted_ = False

    def fit(self, p_cal: np.ndarray, y_cal: np.ndarray) -> "IsotonicCalibrator":
        self.iso.fit(np.asarray(p_cal, dtype=float), np.asarray(y_cal, dtype=float))
        self.fitted_ = True
        return self

    def predict(self, p: np.ndarray) -> np.ndarray:
        if not self.fitted_:
            raise RuntimeError("fit을 먼저 호출한다")
        return np.clip(self.iso.predict(np.asarray(p, dtype=float)), 0.0, 1.0)


def calibration_report(
    y: np.ndarray, p_raw: np.ndarray, p_cal: np.ndarray | None = None, *, n_bins: int = 10
) -> pd.DataFrame:
    """보정 전후를 한 표로."""
    rows = [
        {
            "version": "raw",
            "brier": brier_score(y, p_raw),
            "log_loss": log_loss(y, p_raw),
            "ece": expected_calibration_error(y, p_raw, n_bins=n_bins),
            "pred_mean": float(np.mean(p_raw)),
            "obs_rate": float(np.mean(y)),
        }
    ]
    if p_cal is not None:
        rows.append(
            {
                "version": "calibrated",
                "brier": brier_score(y, p_cal),
                "log_loss": log_loss(y, p_cal),
                "ece": expected_calibration_error(y, p_cal, n_bins=n_bins),
                "pred_mean": float(np.mean(p_cal)),
                "obs_rate": float(np.mean(y)),
            }
        )
    return pd.DataFrame(rows)


def _use_korean_font() -> None:
    """한글 라벨이 □로 깨지지 않게 CJK 폰트를 잡는다. 없으면 조용히 넘어간다."""
    import matplotlib
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    # Noto CJK는 .ttc 한 파일에 KR/JP/SC/TC 얼굴이 함께 들어 있어 matplotlib이
    # 첫 얼굴 이름(보통 JP)으로만 등록하는 경우가 있다. 어느 이름이든 한글은 나온다.
    for cand in ("Noto Sans CJK KR", "Noto Sans CJK HK", "Noto Sans CJK JP",
                 "NanumGothic", "Malgun Gothic", "AppleGothic", "Noto Serif CJK JP"):
        if cand in installed:
            matplotlib.rcParams["font.family"] = cand
            matplotlib.rcParams["axes.unicode_minus"] = False
            return


def rolling_oof_predictions(
    df: pd.DataFrame,
    X: pd.DataFrame,
    y: np.ndarray,
    fit_predict,
    *,
    origin_col: str = "origin",
    min_train_origins: int = 4,
    embargo: int = 4,
) -> pd.DataFrame:
    """보정용 out-of-fold 예측을 시간 순서를 지키며 모은다.

    origin i의 예측은 `origins[: i-embargo]`로 학습한 모형에서만 얻는다.
    학습 데이터를 따로 떼어내지 않고도 보정 표본을 넓게 확보할 수 있어,
    base rate 변동이 큰 패널에서 단일 보정 구간보다 안정적이다.

    Returns
    -------
    DataFrame[origin, idx, p_oof, y] — 보정기 적합에 그대로 쓴다.
    """
    origins = sorted(df[origin_col].dropna().unique().tolist())
    out = []
    for i in range(min_train_origins + embargo, len(origins)):
        tr = df[origin_col].isin(origins[: i - embargo]).to_numpy()
        te = (df[origin_col] == origins[i]).to_numpy()
        if tr.sum() == 0 or te.sum() == 0:
            continue
        p = fit_predict(X[tr], y[tr], X[te])
        out.append(
            pd.DataFrame(
                {
                    "origin": origins[i],
                    "idx": np.flatnonzero(te),
                    "p_oof": p,
                    "y": y[te],
                }
            )
        )
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def plot_reliability(tables: dict[str, pd.DataFrame], path, *, title: str = "Reliability"):
    """reliability 곡선 저장. tables = {"raw": df, "calibrated": df, ...}"""
    import matplotlib

    matplotlib.use("Agg")
    _use_korean_font()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.2, 5.2))
    ax.plot([0, 1], [0, 1], "--", color="#999", lw=1, label="perfect")
    for name, t in tables.items():
        if t.empty:
            continue
        ax.plot(t["pred_mean"], t["obs_rate"], "o-", ms=4, lw=1.4, label=name)
    hi = max(
        [t[["pred_mean", "obs_rate"]].to_numpy().max() for t in tables.values() if not t.empty]
        + [0.05]
    )
    ax.set_xlim(0, hi * 1.08)
    ax.set_ylim(0, hi * 1.08)
    ax.set_xlabel("예측 확률")
    ax.set_ylabel("실제 폐업 비율")
    ax.set_title(title)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
