# -*- coding: utf-8 -*-
"""확률 보정(calibration)과 reliability 진단.

화면에 "위험도 18.7%"를 그대로 쓰려면 예측 확률이 실제 발생률과 일치해야 한다.
순위만 맞는 모형은 AUC가 높아도 이 용도로는 쓸 수 없다.

보정기는 **학습에 쓰지 않은 보정 구간**에서 적합한다 (`splits.final_holdout` 참조).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit, logit
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


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


class PlattCalibrator:
    """로지스틱(Platt) 보정. logit(p)에 기울기·절편을 적합해 확률 수준을 맞춘다.
    Isotonic과 달리 단조 계단이 아니라 매끄러운 곡선이고, 표본이 적을 때 덜 흔들린다."""

    def __init__(self) -> None:
        self.model_: LogisticRegression | None = None

    def fit(self, p_cal: np.ndarray, y_cal: np.ndarray) -> "PlattCalibrator":
        lp = logit(np.clip(np.asarray(p_cal, dtype=float), 1e-6, 1 - 1e-6))
        self.model_ = LogisticRegression(C=np.inf, max_iter=1000).fit(lp[:, None], np.asarray(y_cal))
        return self

    def predict(self, p: np.ndarray) -> np.ndarray:
        if self.model_ is None:
            raise RuntimeError("fit을 먼저 호출한다")
        lp = logit(np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6))
        return self.model_.predict_proba(lp[:, None])[:, 1]

    @property
    def slope(self) -> float:
        return float(self.model_.coef_[0, 0])

    @property
    def intercept(self) -> float:
        return float(self.model_.intercept_[0])


def calibration_slope_intercept(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """logit 척도 보정 기울기(y ~ logit p)와 절편(calibration-in-the-large: 기울기 1 고정,
    logit p에 더하는 offset). 기울기 < 1이면 예측이 실제보다 극단적이다(과신)."""
    y = np.asarray(y, dtype=float)
    lp = logit(np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6))
    slope = float(LogisticRegression(C=np.inf, max_iter=1000).fit(lp[:, None], y).coef_[0, 0])
    intercept = float(brentq(lambda a: expit(a + lp).mean() - y.mean(), -10, 10))
    return slope, intercept


def bootstrap_brier_diff(y: np.ndarray, p_a: np.ndarray, p_b: np.ndarray, store_ids: np.ndarray, *,
                         n_boot: int = 1000, seed: int = 20260928) -> dict:
    """Brier(a) − Brier(b)와 점포 단위 클러스터 부트스트랩 95% 구간. 같은 점포의 여러 origin 행을 정수
    가중치로 함께 리샘플한다 — 뽑힌 횟수만큼 행을 실제로 복제하지 않고 가중 평균으로 계산해(=Brier가
    제곱오차의 평균이라 가능) 부트스트랩 1회가 O(행 수)로 끝난다(점포별로 훑는 O(점포 수 × 행 수)보다 훨씬 빠르다).
    Brier는 작을수록 좋으므로 diff < 0이면 a가 더 낫다."""
    y, p_a, p_b = np.asarray(y, dtype=float), np.asarray(p_a, dtype=float), np.asarray(p_b, dtype=float)
    sq_diff = (p_a - y) ** 2 - (p_b - y) ** 2  # 점(행) 단위 Brier 차이 — 가중합/가중치합이 곧 가중 평균 차이
    codes, uniq = pd.factorize(pd.Series(store_ids))
    n = len(uniq)
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n)[codes].astype(float)
        diffs[b] = (w * sq_diff).sum() / w.sum()
    obs = brier_score(y, p_a) - brier_score(y, p_b)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"diff": float(obs), "ci_low": float(lo), "ci_high": float(hi), "n_boot": n_boot,
            "significant": bool(lo > 0 or hi < 0)}


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
