# -*- coding: utf-8 -*-
"""W2-2 단순 기준 모형 비교 (선행 연구 Christodoulou 외 2019의 "복잡한 모형 vs 로지스틱 회귀" 비교 절차 보완).

기존 HGB(base·enriched)와 같은 분할로 기준 모형 3개를 학습·평가한다.
- 분할: embargo 4분기, rolling OOF (학습 origin ≥ 4개가 되는 2023Q1 ~ 2025Q2, 10개 origin).
  `calibration.rolling_oof_predictions`를 그대로 쓴다 — origin t 예측 = origin ≤ t−5로 학습.
- tenure_only    : 업력대(1년 미만/1–3/3–5/5–10/10년 이상) × 업종의 학습 구간 폐업률 표.
- logit_license  : 로지스틱 회귀, 인허가 5개 (업력은 자연 3차 스플라인, 범주형은 원-핫).
- logit_base     : 로지스틱 회귀, base 19개. 수치형은 표준화.
- logit_enriched : logit_base + 온라인 6개 (PR #33). 개수형(cnt_3m·cnt_12m·months_since_last)은 log1p 후 표준화.
  온라인 NA(Issue #25 규칙으로 정해진 미관측)도 학습 구간 중앙값으로 채운다.
로지스틱의 결측은 학습 구간 중앙값(범주형은 최빈값)으로 채우고 **결측 지시자는 만들지 않는다**
(`features.py` — 결측 지시자는 상권 polygon 소속·공시지가 시기 정보가 새는 경로). 학습 구간에 값이 하나도 없는
컬럼(초기 fold의 land_price)은 HGB와 같이 뺀다.

추가 점검
- (a) origin별 학습 구간 폐업률(train_base_rate)과 OOF 예측 평균 — 초기 과소 예측이 학습 구간 기준율 때문인지.
- (b) HGB 하이퍼파라미터: fold마다 **학습 구간 안의 시간 분할**(가장 최근 학습 origin을 내부 검증)로
  learning_rate × max_leaf_nodes 격자를 고르고, 고른 설정의 rolling OOF AUC를 고정값과 비교한다.
  검증(test) origin은 선택에 쓰지 않는다. 내부 분할에도 embargo를 두되, 학습 origin이 모자라는 초기 fold는
  embargo 없이(내부 학습 = 검증 직전까지) 고른다 — 어느 쪽이었는지 기록한다.
- (c) hgb_enriched 보정 기울기 개선 후보 (서빙 모형은 바꾸지 않는다): 튜닝 설정 / 로지스틱 재보정 / 둘 다.
  재보정은 fold마다 학습 구간 안에서 embargo를 지켜 OOF를 만들 수 있는 가장 최근 origin s의 예측으로
  logit(p)에 절편·기울기를 적합한다 (s의 모형은 origin ≤ s−5로 학습). 검증 origin은 쓰지 않는다.
  그런 s가 없는 초기 fold는 재보정 없이 그대로 두고 기록한다. 등급 비율은 현재 서빙 컷오프를 그대로 적용한다.

실행:
    python -m src.models.benchmark [--master outputs/master/master_base.parquet] \\
        [--online outputs/online/online_features.parquet] [--out outputs/models/benchmark] [--n-boot 1000] [--skip-hpo]
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from src.data import config
from src.models import bands, calibration, detect, features, train_detect

EMBARGO = train_detect.EMBARGO
MIN_TRAIN_ORIGINS = train_detect.MIN_TRAIN_ORIGINS
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "models" / "benchmark"
DEFAULT_ONLINE = config.REPO_ROOT / "outputs" / "online" / "online_features.parquet"
DEFAULT_BAND_CUTOFFS = config.REPO_ROOT / "outputs" / "models" / "detect_v0_enriched" / "band_cutoffs.csv"
LICENSE_COLS = ["age_months", "biz_type", "area", "has_coord", "gu"]
ONLINE_COLS = ["online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_has_12m", "online_blog_trend_6m",
               "online_blog_has_ever", "online_blog_months_since_last"]  # PR #33 enriched = base + 이 6개
# 로지스틱에서 log1p 후 표준화하는 개수형(0 이상, 오른쪽 꼬리) 온라인 열. trend_6m은 부호가 있고 has_*는 0/1이라 그대로
ONLINE_LOG1P_COLS = ("online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_months_since_last")
# 업력대 경계(개월, 상한 포함). W2-3 진단 업력대와 같은 구간
AGE_BANDS = [(-np.inf, 12, "1년 미만"), (12, 36, "1–3년"), (36, 60, "3–5년"), (60, 120, "5–10년"),
             (120, np.inf, "10년 이상")]
SPLINE_KNOT_QUANTILES = (0.05, 0.35, 0.65, 0.95)  # Harrell 권장 4-knot 위치 → 자유도 3
HPO_GRID = {"learning_rate": (0.03, 0.06, 0.1), "max_leaf_nodes": (15, 31, 63)}
COMPARE_ORIGIN = "2025Q2"
BOOT_SEED = 20260927


def log(msg: str) -> None:
    train_detect.log(msg)


# ---------------------------------------------------------------------------
# 기준 모형
# ---------------------------------------------------------------------------
def age_band(age_months) -> pd.Series:
    a = pd.Series(np.asarray(age_months, dtype=float))
    out = pd.Series(pd.NA, index=a.index, dtype="object")
    for lo, hi, name in AGE_BANDS:
        out[(a > lo) & (a <= hi)] = name
    return out


class TenureRate:
    """업력대 × 업종 학습 구간 폐업률 표. 비어 있는 칸은 업종 → 전체 폐업률로 채운다."""

    def fit(self, X: pd.DataFrame, y) -> "TenureRate":
        d = pd.DataFrame({"band": age_band(X["age_months"]).to_numpy(), "biz": X["biz_type"].astype(str).to_numpy(),
                          "y": np.asarray(y, dtype=float)})
        self.cell_ = d.groupby(["band", "biz"])["y"].mean()
        self.biz_ = d.groupby("biz")["y"].mean()
        self.all_ = float(d["y"].mean())
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        band, biz = age_band(X["age_months"]).to_numpy(), X["biz_type"].astype(str).to_numpy()
        cell = pd.Series(list(zip(band, biz))).map(self.cell_.to_dict())
        p = cell.fillna(pd.Series(biz).map(self.biz_.to_dict())).fillna(self.all_)
        return p.to_numpy(dtype=float)


def rcs_basis(x: np.ndarray, knots) -> np.ndarray:
    """자연(제한) 3차 스플라인 기저 (Harrell). 반환 열: x, 비선형 항 k−2개. 마지막 knot 바깥에서 선형."""
    x = np.asarray(x, dtype=float)
    t = np.asarray(knots, dtype=float)
    k = len(t)
    norm = (t[-1] - t[0]) ** 2
    cols = [x]
    p3 = lambda v: np.clip(v, 0, None) ** 3  # noqa: E731
    for j in range(k - 2):
        cols.append((p3(x - t[j]) - p3(x - t[-2]) * (t[-1] - t[j]) / (t[-1] - t[-2])
                     + p3(x - t[-1]) * (t[-2] - t[j]) / (t[-1] - t[-2])) / norm)
    return np.column_stack(cols)


class LogitModel:
    """로지스틱 회귀 + 학습 구간 기준 전처리 (중앙값·최빈값 대치, 결측 지시자 없음, 표준화, 원-핫).
    log1p_cols는 log1p 변환 후 같은 처리를 한다 (중앙값도 변환된 값에서 — 단조 변환이라 같은 점포 값이다)."""

    def __init__(self, cols, spline_age: bool = False, log1p_cols=()):
        self.cols = list(cols)
        self.spline_age = spline_age
        self.log1p_cols = set(log1p_cols)

    def _num(self, X: pd.DataFrame, c: str) -> pd.Series:
        v = X[c].astype(float)
        if c in self.log1p_cols:
            if (v < 0).any():
                raise ValueError(f"{c}: log1p 대상인데 음수가 있다")
            v = np.log1p(v)
        return v

    def _design(self, X: pd.DataFrame) -> np.ndarray:
        parts = []
        for c in self.num_:
            v = self._num(X, c).fillna(self.median_[c]).to_numpy()
            if c == "age_months" and self.spline_age:
                parts.append(rcs_basis(v, self.knots_))
            else:
                parts.append(v[:, None])
        for c in self.cat_:
            v = X[c].astype(object).where(X[c].notna(), self.mode_[c]).astype(str).to_numpy()
            parts.append(np.column_stack([(v == lvl).astype(float) for lvl in self.levels_[c][1:]])
                         if len(self.levels_[c]) > 1 else np.empty((len(v), 0)))  # 첫 범주 = 기준
        return np.hstack(parts)

    def fit(self, X: pd.DataFrame, y) -> "LogitModel":
        X = X[self.cols]
        self.dropped_all_na_ = [c for c in self.cols if X[c].isna().all()]
        use = [c for c in self.cols if c not in self.dropped_all_na_]
        self.cat_ = [c for c in use if c in features.CATEGORICAL]
        self.num_ = [c for c in use if c not in features.CATEGORICAL]
        self.median_ = {c: float(self._num(X, c).median()) for c in self.num_}
        self.mode_ = {c: str(X[c].dropna().astype(str).mode().iloc[0]) for c in self.cat_}
        self.levels_ = {c: sorted(X[c].dropna().astype(str).unique().tolist()) for c in self.cat_}
        if self.spline_age and "age_months" in self.num_:
            a = X["age_months"].astype(float).fillna(self.median_["age_months"])
            self.knots_ = np.quantile(a, SPLINE_KNOT_QUANTILES)
        D = self._design(X)
        self.mu_, sd = D.mean(axis=0), D.std(axis=0)
        n_num = D.shape[1] - sum(max(len(self.levels_[c]) - 1, 0) for c in self.cat_)
        self.mu_[n_num:], sd[n_num:] = 0.0, 1.0  # 원-핫 열은 표준화하지 않는다
        self.sd_ = np.where(sd > 0, sd, 1.0)
        self.model_ = LogisticRegression(C=np.inf, max_iter=5000).fit((D - self.mu_) / self.sd_, np.asarray(y))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.model_.predict_proba((self._design(X[self.cols]) - self.mu_) / self.sd_)[:, 1]


def make_fitters(base_cols, enriched_cols, hgb_params=None) -> dict:
    """모형 이름 → fit_predict(X_tr, y_tr, X_te). X는 enriched 열까지 담은 행렬 하나를 받는다."""
    p = dict(hgb_params or detect.DEFAULT_PARAMS)
    fitters = {
        "tenure_only": lambda a, b, c: TenureRate().fit(a, b).predict_proba(c),
        "logit_license": lambda a, b, c: LogitModel(LICENSE_COLS, spline_age=True).fit(a, b).predict_proba(c),
        "logit_base": lambda a, b, c: LogitModel(base_cols).fit(a, b).predict_proba(c),
        "hgb_base": lambda a, b, c: detect.fit_predict(a[base_cols], b, c[base_cols], p),
    }
    if enriched_cols:
        fitters["logit_enriched"] = lambda a, b, c: LogitModel(enriched_cols, log1p_cols=ONLINE_LOG1P_COLS).fit(
            a, b).predict_proba(c)
        fitters["hgb_enriched"] = lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols], p)
    return fitters


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------
def calibration_slope_intercept(y, p) -> tuple[float, float]:
    """logit 척도 보정 기울기(y ~ logit p)와 절편(calibration-in-the-large: 기울기 1 고정, offset logit p)."""
    y = np.asarray(y, dtype=float)
    lp = logit(np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6))
    slope = float(LogisticRegression(C=np.inf, max_iter=1000).fit(lp[:, None], y).coef_[0, 0])
    intercept = float(brentq(lambda a: expit(a + lp).mean() - y.mean(), -10, 10))
    return slope, intercept


def summary_row(name: str, oof: pd.DataFrame) -> dict:
    bo = train_detect.metrics_by_origin(oof)
    slope, icpt = calibration_slope_intercept(oof["y"], oof["p_oof"])
    last = oof[oof["origin"] == COMPARE_ORIGIN]
    s_last, i_last = calibration_slope_intercept(last["y"], last["p_oof"])
    return {"model": name, "n_origins": int(bo["origin"].nunique()),
            "origins": f"{bo['origin'].min()}~{bo['origin'].max()}",
            "auc_mean": bo["auc"].mean(), "ap_mean": bo["ap"].mean(), "ap_lift_mean": bo["ap_lift"].mean(),
            "ece_mean": bo["ece"].mean(),
            "auc_mean_from_2023Q4": bo.loc[bo["origin"] >= train_detect.EVAL_FROM, "auc"].mean(),
            "calib_slope_pooled": slope, "calib_intercept_pooled": icpt,
            f"auc_{COMPARE_ORIGIN}": float(bo.loc[bo["origin"] == COMPARE_ORIGIN, "auc"].iloc[0]),
            f"calib_slope_{COMPARE_ORIGIN}": s_last, f"calib_intercept_{COMPARE_ORIGIN}": i_last}


def bootstrap_auc_diff(y, p_a, p_b, n_boot: int, seed: int = BOOT_SEED) -> dict:
    """AUC(a) − AUC(b), AP(a) − AP(b)와 점포 단위 부트스트랩 95% 백분위 구간 (같은 재표본으로 두 지표).
    한 origin 안에서는 점포당 1행이라 행 = 점포."""
    y, p_a, p_b = np.asarray(y), np.asarray(p_a), np.asarray(p_b)
    rng = np.random.default_rng(seed)
    d = np.empty((n_boot, 2))
    for b in range(n_boot):
        i = rng.integers(0, len(y), len(y))
        d[b] = (roc_auc_score(y[i], p_a[i]) - roc_auc_score(y[i], p_b[i]),
                average_precision_score(y[i], p_a[i]) - average_precision_score(y[i], p_b[i]))
    obs = (roc_auc_score(y, p_a) - roc_auc_score(y, p_b),
           average_precision_score(y, p_a) - average_precision_score(y, p_b))
    out = {"n_boot": n_boot}
    for j, (m, prefix) in enumerate((("auc", ""), ("ap", "ap_"))):
        lo, hi = np.percentile(d[:, j], [2.5, 97.5])
        out.update({f"{m}_diff": obs[j], f"{prefix}ci_low": lo, f"{prefix}ci_high": hi,
                    f"{prefix}p_boot_le0": float((d[:, j] <= 0).mean()), f"{prefix}significant": bool(lo > 0 or hi < 0)})
    return out


def base_rate_table(df: pd.DataFrame, y: np.ndarray, oofs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """origin별 학습 구간 폐업률 vs 실측 vs 모형별 OOF 예측 평균."""
    origins = sorted(df["origin"].unique())
    rows = []
    for o in sorted(next(iter(oofs.values()))["origin"].unique()):
        i = origins.index(o)
        tr = df["origin"].isin(origins[: i - EMBARGO]).to_numpy()
        te = (df["origin"] == o).to_numpy()
        row = {"origin": o, "train_origins": f"{origins[0]}~{origins[i - EMBARGO - 1]}",
               "train_base_rate": float(y[tr].mean()), "obs_rate": float(y[te].mean())}
        for m, oof in oofs.items():
            row[f"pred_mean_{m}"] = float(oof.loc[oof["origin"] == o, "p_oof"].mean())
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (b) HGB 하이퍼파라미터 — 학습 구간 안의 시간 분할로만 고른다
# ---------------------------------------------------------------------------
def inner_split(train_origins: list[str]) -> tuple[list[str], str, bool]:
    """학습 origin 안의 내부 분할: 검증 = 가장 최근 학습 origin. 내부 학습은 embargo를 두되,
    남는 origin이 없으면 embargo 없이 검증 직전까지. 반환: (내부 학습 origin, 내부 검증 origin, embargo 적용)."""
    val = train_origins[-1]
    n_gap = len(train_origins) - 1 - EMBARGO  # 음수면 슬라이스가 뒤에서 잘리므로 명시적으로 검사한다
    if n_gap > 0:
        return train_origins[:n_gap], val, True
    return train_origins[:-1], val, False


def hpo(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, cols) -> tuple[pd.DataFrame, pd.DataFrame]:
    origins = sorted(df["origin"].unique())
    grid = [dict(zip(HPO_GRID, v)) for v in itertools.product(*HPO_GRID.values())]
    inner_rows, oof_rows = [], []
    for i in range(MIN_TRAIN_ORIGINS + EMBARGO, len(origins)):
        test_o, train_o = origins[i], origins[: i - EMBARGO]
        in_tr, in_val, gap = inner_split(train_o)
        assert test_o not in in_tr and test_o != in_val and in_val in train_o  # 검증 origin은 선택에 안 쓴다
        tr_m, va_m = df["origin"].isin(in_tr).to_numpy(), (df["origin"] == in_val).to_numpy()
        scores = []
        for g in grid:
            p = detect.fit_predict(X.loc[tr_m, cols], y[tr_m], X.loc[va_m, cols], {**detect.DEFAULT_PARAMS, **g})
            auc = roc_auc_score(y[va_m], p)
            scores.append(auc)
            inner_rows.append({"test_origin": test_o, "inner_train": f"{in_tr[0]}~{in_tr[-1]}", "inner_val": in_val,
                               "inner_embargo": gap, **g, "inner_auc": auc})
        best = grid[int(np.argmax(scores))]
        tr, te = df["origin"].isin(train_o).to_numpy(), (df["origin"] == test_o).to_numpy()
        p = detect.fit_predict(X.loc[tr, cols], y[tr], X.loc[te, cols], {**detect.DEFAULT_PARAMS, **best})
        oof_rows.append({"origin": test_o, **{f"chosen_{k}": v for k, v in best.items()}, "inner_embargo": gap,
                         "auc_tuned": roc_auc_score(y[te], p)})
        log(f"HPO {test_o}: 내부 검증 {in_val} (embargo {'O' if gap else 'X'}) → {best} · OOF AUC {oof_rows[-1]['auc_tuned']:.4f}")
    inner = pd.DataFrame(inner_rows)
    tuned = pd.DataFrame(oof_rows)
    chosen = tuned.set_index("origin")[[f"chosen_{k}" for k in HPO_GRID]]
    inner["chosen"] = [all(r[k] == chosen.at[r["test_origin"], f"chosen_{k}"] for k in HPO_GRID)
                       for _, r in inner.iterrows()]
    return inner, tuned


# ---------------------------------------------------------------------------
# 보정 기울기 개선 후보 (hgb_enriched) — 서빙 모형은 바꾸지 않고 비교만 한다
# ---------------------------------------------------------------------------
TUNED_PARAMS = {"learning_rate": 0.03, "max_leaf_nodes": 15}  # (b) HPO에서 내부 분할로 고른 설정
CALIB_CANDIDATES = {  # 이름 → (HGB 설정, 로지스틱 재보정 여부)
    "hgb_enriched": ("default", False),
    "a_tuned": ("tuned", False),
    "b_default_recal": ("default", True),
    "c_tuned_recal": ("tuned", True),
}


def recal_split(origins: list[str], i: int) -> tuple[str, list[str]]:
    """검증 origin i의 재보정 표본: 학습 구간(origins[: i−EMBARGO]) 안에서 embargo를 지켜 OOF 예측을 만들 수 있는
    가장 최근 origin s와, s를 예측할 모형의 학습 origin(origins[: s−EMBARGO]). 만들 수 없으면 학습 목록이 빈다."""
    s_idx = i - EMBARGO - 1
    n_tr = s_idx - EMBARGO
    return origins[s_idx], (origins[:n_tr] if n_tr > 0 else [])


def fit_logistic_recal(p, y) -> tuple[float, float]:
    """logit(p)에 절편·기울기를 적합한다 (Platt 방식 재보정). 반환: (절편, 기울기)."""
    lp = logit(np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6))
    m = LogisticRegression(C=np.inf, max_iter=1000).fit(lp[:, None], np.asarray(y))
    return float(m.intercept_[0]), float(m.coef_[0, 0])


def apply_logistic_recal(p, intercept: float, slope: float) -> np.ndarray:
    return expit(intercept + slope * logit(np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)))


def band_row(oof: pd.DataFrame, cut_mid: float, cut_high: float, origin: str = COMPARE_ORIGIN) -> dict:
    g = oof[oof["origin"] == origin]
    b = bands.assign_bands_absolute(g["p_oof"].to_numpy(), cut_mid=cut_mid, cut_high=cut_high)
    y = g["y"].to_numpy()
    base = y.mean()
    out = {f"share_{k}_{origin}": float((b == k).mean()) for k in ("low", "mid", "high")}
    out[f"high_obs_rate_{origin}"] = float(y[b == "high"].mean()) if (b == "high").any() else float("nan")
    out[f"high_lift_{origin}"] = out[f"high_obs_rate_{origin}"] / base if base else float("nan")
    return out


def calibration_candidates(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, cols, cut_mid: float, cut_high: float
                           ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    origins = sorted(df["origin"].unique())
    params = {"default": dict(detect.DEFAULT_PARAMS), "tuned": {**detect.DEFAULT_PARAMS, **TUNED_PARAMS}}
    preds = {name: [] for name in CALIB_CANDIDATES}
    recal_rows = []
    for i in range(MIN_TRAIN_ORIGINS + EMBARGO, len(origins)):
        o = origins[i]
        tr, te = df["origin"].isin(origins[: i - EMBARGO]).to_numpy(), (df["origin"] == o).to_numpy()
        s, cal_tr_o = recal_split(origins, i)
        assert o not in cal_tr_o and s < o and s in origins[: i - EMBARGO]  # 검증 origin은 재보정에 안 쓴다
        for cfg, prm in params.items():
            p_raw = detect.fit_predict(X.loc[tr, cols], y[tr], X.loc[te, cols], prm)
            a, b, applied, why = 0.0, 1.0, False, "재보정 표본 없음"
            if cal_tr_o:
                ctr, cs = df["origin"].isin(cal_tr_o).to_numpy(), (df["origin"] == s).to_numpy()
                p_s = detect.fit_predict(X.loc[ctr, cols], y[ctr], X.loc[cs, cols], prm)
                a, b = fit_logistic_recal(p_s, y[cs])
                # 기울기 ≤ 0이면 순위를 뒤집으므로 적용하지 않는다 (그 fold는 원래 예측 그대로)
                applied, why = (True, "") if b > 0 else (False, "재보정 기울기 ≤ 0")
            p_rec = apply_logistic_recal(p_raw, a, b) if applied else p_raw
            recal_rows.append({"origin": o, "config": cfg, "recal_origin": s,
                               "recal_model_train": f"{cal_tr_o[0]}~{cal_tr_o[-1]}" if cal_tr_o else "",
                               "applied": applied, "not_applied_reason": why, "recal_intercept": a, "recal_slope": b})
            for name, (c, rec) in CALIB_CANDIDATES.items():
                if c == cfg:
                    preds[name].append(pd.DataFrame({"origin": o, "idx": np.flatnonzero(te),
                                                     "p_oof": p_rec if rec else p_raw, "y": y[te]}))
        log(f"보정 후보 {o}: 재보정 표본 {s} ({'적용' if cal_tr_o else '표본 없음 — 그대로'})")
    oofs = {n: pd.concat(v, ignore_index=True) for n, v in preds.items()}
    recal = pd.DataFrame(recal_rows)
    applied_o = sorted(recal.loc[recal["applied"], "origin"].unique())
    rows = []
    for n, oof in oofs.items():
        r = summary_row(n, oof)
        bo = train_detect.metrics_by_origin(oof[oof["origin"].isin(applied_o)])
        r.update({"auc_mean_recal_origins": bo["auc"].mean(), "ece_mean_recal_origins": bo["ece"].mean(),
                  "recal_origins": f"{applied_o[0]}~{applied_o[-1]}" if applied_o else ""})
        rows.append({**r, **band_row(oof, cut_mid, cut_high)})
    by_origin = pd.concat([train_detect.metrics_by_origin(o).assign(model=n) for n, o in oofs.items()])
    return pd.DataFrame(rows), by_origin, recal


# ---------------------------------------------------------------------------
# PR #21 (origin/feature/online-presence-collection, 커밋 f026602) src/data/collect_online_presence.py의
# normalize_name과 똑같이 맞춘다 — 블로그 언급 매칭에 쓴 정규화라야 "짧은 상호"가 매칭 규칙과 같은 뜻이 된다
# (#39 name_match_review.match_name_norm과 같은 식).
def match_name_norm(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", name or "").lower()


SHORT_NAME_MAX = 2
SHORT_NAME_POLICIES = ("keep", "na")
DEFAULT_LICENSES = config.REPO_ROOT / "outputs" / "standardized" / "licenses_3gu.parquet"


def short_name_store_ids(licenses_path: Path) -> set[str]:
    """정규화 상호 길이 ≤ SHORT_NAME_MAX인 점포 (#28 짧은 상호, 매칭 오탐률이 높은 집단)."""
    lic = pd.read_parquet(licenses_path, columns=["store_id", "name_raw"])
    return set(lic.loc[lic["name_raw"].map(lambda s: len(match_name_norm(s)) <= SHORT_NAME_MAX), "store_id"])


def apply_short_name_policy(df: pd.DataFrame, X: pd.DataFrame, policy: str, short_ids: set[str]) -> pd.DataFrame:
    """na: 짧은 상호 점포의 온라인 feature 6개를 전 origin NA로 (결측 지시자는 만들지 않는다). keep: 그대로."""
    if policy not in SHORT_NAME_POLICIES:
        raise ValueError(f"알 수 없는 short-name policy: {policy}")
    X = X.copy()
    if policy == "na":
        X.loc[df["store_id"].isin(short_ids).to_numpy(), ONLINE_COLS] = np.nan
    return X


def attach_extra_features(df: pd.DataFrame, X: pd.DataFrame, extra_path: Path, cols=None
                          ) -> tuple[pd.DataFrame, list[str], dict]:
    """(store_id, origin)으로 추가 feature 표를 m:1로 붙인다(없는 키는 NA). 반환: (X, 붙인 열, 키 대조 보고).
    cols가 없으면 표의 `comp_*` 수치 열(PR #43 경쟁지표 6종)을 쓴다. 메타 열(asof·status 등)은 붙이지 않는다."""
    ex = pd.read_parquet(extra_path)
    cols = list(cols or [c for c in COMP_COLS if c in ex.columns])
    if not cols:
        raise ValueError(f"추가 feature 열이 없다: {extra_path}")
    key = ["store_id", "origin"]
    if ex.duplicated(key).any():
        raise ValueError("추가 feature (store_id, origin) 중복")
    m = df[key].merge(ex[key + cols], on=key, how="left", validate="1:1", indicator=True)
    if len(m) != len(df):
        raise ValueError("추가 feature 결합 후 행 수가 바뀌었다")
    X = X.copy()
    for c in cols:
        X[c] = pd.to_numeric(m[c]).astype("Float64").astype("float64").to_numpy()
    matched = int((m["_merge"] == "both").sum())
    report = {"extra_path": str(extra_path), "extra_sha256": train_detect.sha256(Path(extra_path)), "cols": cols,
              "panel_rows": int(len(df)), "matched_rows": matched, "panel_rows_without_extra": int(len(df) - matched),
              "extra_rows_not_in_panel": int(len(ex) - matched),
              "na_rate": {c: float(X[c].isna().mean()) for c in cols}}
    return X, cols, report


def load_inputs(master_path: Path, online_path: Path | None, short_name_policy: str = "keep",
                licenses_path: Path | None = None):
    df = train_detect.load_master(master_path)
    y = df["event_12m"].astype(int).to_numpy()
    base_cols = features.select_features(df.columns, "base")
    X = features.build_X(df, base_cols)
    enriched_cols = []
    if online_path is not None and Path(online_path).exists():
        on = pd.read_parquet(online_path, columns=["store_id", "origin", *ONLINE_COLS])
        if on.duplicated(["store_id", "origin"]).any():
            raise ValueError("온라인 feature (store_id, origin) 중복")
        m = df[["store_id", "origin"]].merge(on, on=["store_id", "origin"], how="left", validate="1:1")
        if len(m) != len(df):
            raise ValueError("온라인 feature 결합 후 행 수가 바뀌었다")
        for c in ONLINE_COLS:
            X[c] = pd.to_numeric(m[c]).astype("Float64").astype("float64").to_numpy()
        enriched_cols = base_cols + ONLINE_COLS
        if short_name_policy != "keep":
            X = apply_short_name_policy(df, X, short_name_policy, short_name_store_ids(licenses_path or DEFAULT_LICENSES))
    return df, X, y, base_cols, enriched_cols


def run(master_path: Path, online_path: Path | None, out: Path, n_boot: int, do_hpo: bool,
        band_cutoffs: Path | None = None, do_calib: bool = True, short_name_policy: str = "keep",
        licenses_path: Path | None = None) -> dict:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    df, X, y, base_cols, enriched_cols = load_inputs(master_path, online_path, short_name_policy, licenses_path)
    log(f"master {len(df):,}행 · base {len(base_cols)}개 · enriched {len(enriched_cols) or '없음'}")

    oofs = {}
    for name, fp in make_fitters(base_cols, enriched_cols).items():
        oofs[name] = calibration.rolling_oof_predictions(df, X, y, fp, min_train_origins=MIN_TRAIN_ORIGINS,
                                                         embargo=EMBARGO)
        log(f"{name}: OOF {len(oofs[name]):,}행 · AUC 평균 {train_detect.metrics_by_origin(oofs[name])['auc'].mean():.4f}")
    by_origin = pd.concat([train_detect.metrics_by_origin(o).assign(model=m) for m, o in oofs.items()])
    by_origin.to_csv(out / "oof_metrics_by_origin.csv", index=False, encoding="utf-8-sig")
    summary = pd.DataFrame([summary_row(m, o) for m, o in oofs.items()])
    summary.to_csv(out / "benchmark_summary.csv", index=False, encoding="utf-8-sig")

    # 2025Q2 AUC 차이 (같은 점포 집합)
    last = {m: o[o["origin"] == COMPARE_ORIGIN].sort_values("idx") for m, o in oofs.items()}
    idx = last["hgb_base"]["idx"].to_numpy()
    assert all((v["idx"].to_numpy() == idx).all() for v in last.values())
    assert df.loc[idx, "store_id"].is_unique
    yl = last["hgb_base"]["y"].to_numpy()
    pairs = [("hgb_base", "logit_base"), ("hgb_base", "logit_license"), ("hgb_base", "tenure_only"),
             ("logit_base", "logit_license")]
    if "hgb_enriched" in oofs:
        pairs = [("hgb_enriched", "logit_enriched"), ("hgb_enriched", "logit_base"), ("hgb_enriched", "hgb_base"),
                 ("logit_enriched", "logit_base")] + pairs
    diff = pd.DataFrame([{"origin": COMPARE_ORIGIN, "model_a": a, "model_b": b,
                          **bootstrap_auc_diff(yl, last[a]["p_oof"], last[b]["p_oof"], n_boot)} for a, b in pairs])
    diff.to_csv(out / f"auc_diff_bootstrap_{COMPARE_ORIGIN}.csv", index=False, encoding="utf-8-sig")
    log("AUC 차이 부트스트랩 완료")

    br = base_rate_table(df, y, oofs)
    br.to_csv(out / "base_rate_vs_pred.csv", index=False, encoding="utf-8-sig")

    hpo_cmp = None
    if do_hpo:
        inner, tuned = hpo(df, X, y, base_cols)
        inner.to_csv(out / "hpo_inner_selection.csv", index=False, encoding="utf-8-sig")
        default = train_detect.metrics_by_origin(oofs["hgb_base"])[["origin", "auc"]].rename(columns={"auc": "auc_default"})
        hpo_cmp = tuned.merge(default, on="origin")
        hpo_cmp["auc_tuned_minus_default"] = hpo_cmp["auc_tuned"] - hpo_cmp["auc_default"]
        hpo_cmp.to_csv(out / "hpo_comparison.csv", index=False, encoding="utf-8-sig")

    calib = None
    if do_calib and enriched_cols:
        cut = pd.read_csv(band_cutoffs).iloc[0] if band_cutoffs else None
        if cut is None:
            raise ValueError("보정 후보 비교에는 --band-cutoffs(현재 서빙 컷오프)가 필요하다")
        calib, calib_by_origin, recal = calibration_candidates(df, X, y, enriched_cols, float(cut["cut_mid"]),
                                                               float(cut["cut_high"]))
        calib.to_csv(out / "calibration_candidates_summary.csv", index=False, encoding="utf-8-sig")
        calib_by_origin.to_csv(out / "calibration_candidates_by_origin.csv", index=False, encoding="utf-8-sig")
        recal.to_csv(out / "calibration_recal_params.csv", index=False, encoding="utf-8-sig")
        # 같은 설정·seed라 후보 비교의 hgb_enriched는 본 비교와 같아야 한다
        assert np.isclose(calib.set_index("model").at["hgb_enriched", "auc_mean"],
                          summary.set_index("model").at["hgb_enriched", "auc_mean"])

    meta = {"master": str(master_path), "master_sha256": train_detect.sha256(Path(master_path)),
            "online": str(online_path) if enriched_cols else None,
            "online_sha256": train_detect.sha256(Path(online_path)) if enriched_cols else None,
            "embargo": EMBARGO, "min_train_origins": MIN_TRAIN_ORIGINS, "hgb_params": detect.DEFAULT_PARAMS,
            "base_cols": base_cols, "enriched_cols": enriched_cols, "license_cols": LICENSE_COLS,
            "age_bands": [b[2] for b in AGE_BANDS], "spline_knot_quantiles": SPLINE_KNOT_QUANTILES,
            "n_boot": n_boot, "boot_seed": BOOT_SEED, "hpo_grid": HPO_GRID if do_hpo else None,
            "online_log1p_cols": ONLINE_LOG1P_COLS, "tuned_params": TUNED_PARAMS if calib is not None else None,
            "band_cutoffs": str(band_cutoffs) if calib is not None else None,
            "seconds": round(time.time() - t0, 1)}
    (out / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"완료 ({meta['seconds']}초) → {out}")
    return {"summary": summary, "diff": diff, "base_rate": br, "hpo": hpo_cmp, "calib": calib}


# ---------------------------------------------------------------------------
# 적용 전 검정 (튜닝 hgb_enriched) — 서빙 모형·컷오프는 바꾸지 않고 값만 낸다
# ---------------------------------------------------------------------------
BAND_TEST_ORIGINS = ["2025Q1", "2025Q2"]  # train_detect TEST_SIZE=2와 같은 검증 origin


def cluster_bootstrap_diff(y, p_a, p_b, clusters, n_boot: int, seed: int = BOOT_SEED) -> dict:
    """여러 origin을 합친 OOF의 AUC·AP 차이와 점포 단위 클러스터 부트스트랩 95% 구간.
    점포를 복원추출하고, 뽑힌 점포의 모든 origin 행을 뽑힌 횟수만큼 넣는다 (정수 가중치 = 행 복제와 같다)."""
    y, p_a, p_b = np.asarray(y), np.asarray(p_a), np.asarray(p_b)
    codes, uniq = pd.factorize(pd.Series(clusters))
    n = len(uniq)
    rng = np.random.default_rng(seed)
    d = np.empty((n_boot, 2))
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n)[codes]
        m = w > 0
        yy, ww = y[m], w[m]
        d[b] = (roc_auc_score(yy, p_a[m], sample_weight=ww) - roc_auc_score(yy, p_b[m], sample_weight=ww),
                average_precision_score(yy, p_a[m], sample_weight=ww)
                - average_precision_score(yy, p_b[m], sample_weight=ww))
    obs = (roc_auc_score(y, p_a) - roc_auc_score(y, p_b), average_precision_score(y, p_a) - average_precision_score(y, p_b))
    out = {"n_rows": len(y), "n_clusters": n, "n_boot": n_boot}
    for j, m in enumerate(("auc", "ap")):
        lo, hi = np.percentile(d[:, j], [2.5, 97.5])
        out.update({f"{m}_diff": obs[j], f"{m}_ci_low": lo, f"{m}_ci_high": hi,
                    f"{m}_p_boot_le0": float((d[:, j] <= 0).mean()), f"{m}_significant": bool(lo > 0 or hi < 0)})
    return out


def _calibration_step(oof: pd.DataFrame, origins: list[str], test_origins: list[str]):
    """#45에 인용한 수치를 만든 **#32 리뷰 이전** 보정 규칙(train_detect.calibration_step, c5151eb)의 사본이다.
    첫 검증 origin 기준 ≤ t−EMBARGO−1 OOF로 isotonic을 적합하고 검증 ECE가 좋아질 때만 적용한다. 현행 서빙은 fit/select/test
    3구간 후보 비교(`train_detect.calibration_analysis`)로 바뀌었지만, 이 벤치마크의 비교 수치(pre_adoption_*)는 그대로
    재현되도록 옛 규칙을 여기에 고정해 둔다 — 서빙 규칙이 아니다."""
    first_test = origins.index(test_origins[0])
    calib_origins = [o for o in oof["origin"].unique() if origins.index(o) < first_test - EMBARGO]
    cal = oof[oof["origin"].isin(calib_origins)]
    iso = calibration.IsotonicCalibrator().fit(cal["p_oof"], cal["y"])
    te = oof[oof["origin"].isin(test_origins)]
    rep = calibration.calibration_report(te["y"].to_numpy(), te["p_oof"].to_numpy(), iso.predict(te["p_oof"]))
    apply = bool(rep.loc[rep["version"] == "calibrated", "ece"].iloc[0]
                 < rep.loc[rep["version"] == "raw", "ece"].iloc[0])
    return iso, apply, calib_origins, rep


def band_rule_values(oof: pd.DataFrame, origins: list[str]) -> tuple[dict, pd.DataFrame]:
    """train_detect와 같은 등급 규칙: 보정 구간(첫 검증 origin − embargo 이전 OOF)으로 isotonic 적용 여부를 정하고
    (검증 ECE가 좋아질 때만), 같은 구간 OOF로 `bands.suggest_cutoffs`. 검증 origin(2025Q1–Q2)에 적용한 등급 비율·lift."""
    iso, apply, calib_origins, _ = _calibration_step(oof, origins, BAND_TEST_ORIGINS)
    cal = oof[oof["origin"].isin(calib_origins)]
    p_cal = iso.predict(cal["p_oof"]) if apply else cal["p_oof"].to_numpy()
    cut = bands.suggest_cutoffs(cal["y"].to_numpy(), p_cal)
    info = {**cut, "isotonic_applied": apply, "calib_origins": f"{min(calib_origins)}~{max(calib_origins)}"}
    if not 0 < cut["cut_mid"] < cut["cut_high"] < 1:
        # 규칙이 순서가 맞는 컷오프를 못 만든 경우 (예측이 기준율 근처에 몰림) — 멈추지 않고 기록만 한다
        return {**info, "cutoff_rule_failed": True}, pd.DataFrame()
    te = oof[oof["origin"].isin(BAND_TEST_ORIGINS)]
    p_te = iso.predict(te["p_oof"]) if apply else te["p_oof"].to_numpy()
    b = bands.assign_bands_absolute(p_te, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
    prof = []
    for name, m in [("2025Q1~2025Q2", np.ones(len(te), bool))] + [(o, (te["origin"] == o).to_numpy())
                                                                  for o in BAND_TEST_ORIGINS]:
        prof.append(bands.band_profile(te["y"].to_numpy()[m], p_te[m], b[m]).assign(test_origins=name))
    return {**info, "cutoff_rule_failed": False}, pd.concat(prof, ignore_index=True)


def pre_adoption(master_path: Path, online_path: Path, out: Path, n_boot: int,
                 current_cutoffs: Path | None = None, short_name_policy: str = "keep",
                 licenses_path: Path | None = None) -> dict:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    df, X, y, base_cols, enriched_cols = load_inputs(master_path, online_path, short_name_policy, licenses_path)
    if not enriched_cols:
        raise ValueError("적용 전 검정에는 온라인 feature(--online)가 필요하다")
    tuned = {**detect.DEFAULT_PARAMS, **TUNED_PARAMS}
    fitters = {
        "hgb_enriched_tuned": lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols], tuned),
        "hgb_enriched": make_fitters(base_cols, enriched_cols)["hgb_enriched"],
        "logit_enriched": make_fitters(base_cols, enriched_cols)["logit_enriched"],
    }
    oofs = {}
    for n, fp in fitters.items():
        oofs[n] = calibration.rolling_oof_predictions(df, X, y, fp, min_train_origins=MIN_TRAIN_ORIGINS,
                                                      embargo=EMBARGO).sort_values(["origin", "idx"], ignore_index=True)
        log(f"{n}: OOF {len(oofs[n]):,}행 · 합산 AUC {roc_auc_score(oofs[n]['y'], oofs[n]['p_oof']):.4f}")
    ref = oofs["hgb_enriched_tuned"]
    assert all((o["idx"].to_numpy() == ref["idx"].to_numpy()).all() for o in oofs.values())
    stores = df.loc[ref["idx"], "store_id"].to_numpy()

    rows = []
    for a, b in (("hgb_enriched_tuned", "logit_enriched"), ("hgb_enriched_tuned", "hgb_enriched")):
        rows.append({"model_a": a, "model_b": b, "origins": f"{ref['origin'].min()}~{ref['origin'].max()}",
                     **cluster_bootstrap_diff(ref["y"], oofs[a]["p_oof"], oofs[b]["p_oof"], stores, n_boot)})
        log(f"클러스터 부트스트랩 {a} − {b} 완료")
    boot = pd.DataFrame(rows)
    boot.to_csv(out / "pre_adoption_cluster_bootstrap.csv", index=False, encoding="utf-8-sig")

    origins = sorted(df["origin"].unique())
    cuts, profs = [], []
    for n in ("hgb_enriched", "hgb_enriched_tuned"):
        c, p = band_rule_values(oofs[n], origins)
        cuts.append({"model": n, **c})
        profs.append(p.assign(model=n))
    cuts = pd.DataFrame(cuts)
    if current_cutoffs is not None and Path(current_cutoffs).exists():
        cur = pd.read_csv(current_cutoffs).iloc[0]
        got = cuts.set_index("model").loc["hgb_enriched"]
        # 같은 규칙을 현 모형에 돌리면 서빙 컷오프가 그대로 나와야 한다 (규칙 재현 확인)
        cuts["reproduces_current"] = [bool(np.isclose(got["cut_mid"], cur["cut_mid"])
                                           and np.isclose(got["cut_high"], cur["cut_high"])), None]
    cuts.to_csv(out / "pre_adoption_cutoffs.csv", index=False, encoding="utf-8-sig")
    prof = pd.concat(profs, ignore_index=True)
    prof.to_csv(out / "pre_adoption_band_profile.csv", index=False, encoding="utf-8-sig")
    meta = {"master_sha256": train_detect.sha256(Path(master_path)), "online_sha256": train_detect.sha256(Path(online_path)),
            "tuned_params": TUNED_PARAMS, "n_boot": n_boot, "boot_seed": BOOT_SEED, "band_test_origins": BAND_TEST_ORIGINS,
            "current_cutoffs": str(current_cutoffs), "seconds": round(time.time() - t0, 1)}
    (out / "pre_adoption_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"적용 전 검정 완료 ({meta['seconds']}초) → {out}")
    return {"bootstrap": boot, "cutoffs": cuts, "profile": prof}


# ---------------------------------------------------------------------------
# 등급 컷오프 보정 기간 민감도 — 서빙 컷오프는 바꾸지 않는다
# ---------------------------------------------------------------------------
CUTOFF_WINDOWS = {  # 이름 → (시작, 끝) origin. 검증 origin(BAND_TEST_ORIGINS)과 겹치면 멈춘다
    "2023Q1~2023Q4": ("2023Q1", "2023Q4"),   # 현행 (train_detect 보정 구간)
    "2024Q1~2024Q4": ("2024Q1", "2024Q4"),   # 검증 직전 4분기
    "2023Q1~2024Q4": ("2023Q1", "2024Q4"),   # 8분기 전체
}


def window_origins(origins: list[str], start: str, end: str) -> list[str]:
    w = [o for o in origins if start <= o <= end]
    if set(w) & set(BAND_TEST_ORIGINS):
        raise ValueError(f"보정 기간 {start}~{end}에 검증 origin이 들어 있다")
    return w


def labels_matured_by(window: list[str], origins: list[str], test_origin: str) -> bool:
    """test_origin 시점에 window의 라벨이 모두 확정돼 있는지 (embargo 규칙: origin ≤ t−EMBARGO−1)."""
    return origins.index(max(window)) <= origins.index(test_origin) - EMBARGO - 1


def high_lift_cluster_ci(y, is_high, clusters, n_boot: int, seed: int = BOOT_SEED) -> tuple[float, float]:
    """high 실측 폐업률 ÷ 전체 실측 폐업률의 점포 단위 클러스터 부트스트랩 95% 구간 (컷오프는 고정)."""
    y, h = np.asarray(y, dtype=float), np.asarray(is_high, dtype=bool)
    codes, uniq = pd.factorize(pd.Series(clusters))
    n = len(uniq)
    rng = np.random.default_rng(seed)
    lifts = np.empty(n_boot)
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n)[codes].astype(float)
        wh = w * h
        lifts[b] = ((wh * y).sum() / wh.sum()) / ((w * y).sum() / w.sum()) if wh.sum() > 0 else np.nan
    lo, hi = np.nanpercentile(lifts, [2.5, 97.5])
    return float(lo), float(hi)


def cutoff_window_rows(oofs: dict[str, pd.DataFrame], stores: np.ndarray, origins: list[str], n_boot: int) -> pd.DataFrame:
    """모형 × 보정 기간별 `bands.suggest_cutoffs` 컷오프와 검증 origin(2025Q1–Q2) 등급 비율·lift·high lift CI.
    컷오프는 보정 기간 raw OOF로 정한다 — 현행 규칙에서 두 모형 모두 isotonic이 적용되지 않았다(pre_adoption_cutoffs)."""
    rows = []
    for model, oof in oofs.items():
        te_m = oof["origin"].isin(BAND_TEST_ORIGINS).to_numpy()
        te = oof[te_m]
        y_te, p_te = te["y"].to_numpy(), te["p_oof"].to_numpy()
        for name, (s, e) in CUTOFF_WINDOWS.items():
            w = window_origins(origins, s, e)
            cal = oof[oof["origin"].isin(w)]
            assert not cal["origin"].isin(BAND_TEST_ORIGINS).any()
            cut = bands.suggest_cutoffs(cal["y"].to_numpy(), cal["p_oof"].to_numpy())
            row = {"model": model, "calib_window": name, "n_calib_origins": len(w),
                   "labels_matured_at_2025Q1": labels_matured_by(w, origins, BAND_TEST_ORIGINS[0]),
                   "labels_matured_at_2025Q2": labels_matured_by(w, origins, BAND_TEST_ORIGINS[-1]),
                   "cut_mid": cut["cut_mid"], "cut_high": cut["cut_high"], "calib_base_rate": cut["base_rate"],
                   "test_origins": f"{BAND_TEST_ORIGINS[0]}~{BAND_TEST_ORIGINS[-1]}", "test_obs_rate": y_te.mean()}
            if not 0 < cut["cut_mid"] < cut["cut_high"] < 1:
                rows.append({**row, "cutoff_rule_failed": True})
                continue
            b = bands.assign_bands_absolute(p_te, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
            for k in bands.BANDS:
                m = b == k
                row[f"share_{k}"] = float(m.mean())
                row[f"lift_{k}"] = float(y_te[m].mean() / y_te.mean()) if m.any() else float("nan")
            row["n_high"] = int((b == "high").sum())
            row["high_lift_ci_low"], row["high_lift_ci_high"] = high_lift_cluster_ci(y_te, b == "high", stores[te_m],
                                                                                     n_boot)
            row["meets_high_lift_2"] = row["lift_high"] >= 2
            row["meets_high_lift_2_ci_low"] = row["high_lift_ci_low"] >= 2
            row["meets_high_share_5_10"] = 0.05 <= row["share_high"] <= 0.10
            rows.append({**row, "cutoff_rule_failed": False})
            log(f"{model} · {name}: mid {cut['cut_mid']:.4f} high {cut['cut_high']:.4f} → high {row['share_high']:.1%} "
                f"lift {row['lift_high']:.2f} [{row['high_lift_ci_low']:.2f}, {row['high_lift_ci_high']:.2f}]")
    return pd.DataFrame(rows)


def cutoff_window(master_path: Path, online_path: Path, out: Path, n_boot: int, short_name_policy: str = "keep",
                  licenses_path: Path | None = None) -> pd.DataFrame:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    df, X, y, base_cols, enriched_cols = load_inputs(master_path, online_path, short_name_policy, licenses_path)
    if not enriched_cols:
        raise ValueError("보정 기간 민감도에는 온라인 feature(--online)가 필요하다")
    tuned = {**detect.DEFAULT_PARAMS, **TUNED_PARAMS}
    fitters = {"hgb_enriched": make_fitters(base_cols, enriched_cols)["hgb_enriched"],
               "hgb_enriched_tuned": lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols], tuned)}
    oofs = {n: calibration.rolling_oof_predictions(df, X, y, fp, min_train_origins=MIN_TRAIN_ORIGINS, embargo=EMBARGO
                                                   ).sort_values(["origin", "idx"], ignore_index=True)
            for n, fp in fitters.items()}
    ref = oofs["hgb_enriched"]
    assert (oofs["hgb_enriched_tuned"]["idx"].to_numpy() == ref["idx"].to_numpy()).all()
    tab = cutoff_window_rows(oofs, df.loc[ref["idx"], "store_id"].to_numpy(), sorted(df["origin"].unique()), n_boot)
    tab.to_csv(out / "pre_adoption_cutoff_window.csv", index=False, encoding="utf-8-sig")
    log(f"보정 기간 민감도 완료 ({time.time() - t0:.0f}초) → {out / 'pre_adoption_cutoff_window.csv'}")
    return tab


# ---------------------------------------------------------------------------
# 짧은 상호 처리 민감도 (#28) — keep vs na. 서빙 모형은 바꾸지 않는다
# ---------------------------------------------------------------------------
def pooled_metrics(oof: pd.DataFrame, mask: np.ndarray | None = None) -> dict:
    g = oof if mask is None else oof[mask]
    y, p = g["y"].to_numpy(), g["p_oof"].to_numpy()
    return {"pooled_auc": roc_auc_score(y, p), "pooled_ap": average_precision_score(y, p), "n": len(g),
            "obs_rate": float(y.mean())}


def short_name_sensitivity(master_path: Path, online_path: Path, out: Path, n_boot: int,
                           licenses_path: Path | None = None) -> dict:
    """튜닝 hgb_enriched·logit_enriched를 keep/na로 rolling OOF (10개 origin). base 모형은 온라인 열이 없어 정책과 무관."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    df, X_keep, y, base_cols, enriched_cols = load_inputs(master_path, online_path)
    if not enriched_cols:
        raise ValueError("짧은 상호 민감도에는 온라인 feature(--online)가 필요하다")
    short_ids = short_name_store_ids(licenses_path or DEFAULT_LICENSES)
    X_na = apply_short_name_policy(df, X_keep, "na", short_ids)
    tuned = {**detect.DEFAULT_PARAMS, **TUNED_PARAMS}
    logit_enr = make_fitters(base_cols, enriched_cols)["logit_enriched"]
    runs = {  # 이름 → (X, fit_predict)
        "hgb_base_tuned": (X_keep, lambda a, b, c: detect.fit_predict(a[base_cols], b, c[base_cols], tuned)),
        "logit_base": (X_keep, make_fitters(base_cols, enriched_cols)["logit_base"]),
        "hgb_enriched_tuned_keep": (X_keep, lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols], tuned)),
        "hgb_enriched_tuned_na": (X_na, lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols], tuned)),
        "logit_enriched_keep": (X_keep, logit_enr),
        "logit_enriched_na": (X_na, logit_enr),
    }
    oofs = {}
    for n, (Xm, fp) in runs.items():
        oofs[n] = calibration.rolling_oof_predictions(df, Xm, y, fp, min_train_origins=MIN_TRAIN_ORIGINS,
                                                      embargo=EMBARGO).sort_values(["origin", "idx"], ignore_index=True)
        log(f"{n}: 평균 AUC {train_detect.metrics_by_origin(oofs[n])['auc'].mean():.4f}")
    ref = oofs["hgb_base_tuned"]
    assert all((o["idx"].to_numpy() == ref["idx"].to_numpy()).all() for o in oofs.values())
    stores = df.loc[ref["idx"], "store_id"].to_numpy()
    is_short = np.isin(stores, list(short_ids))

    rows = []
    for n, oof in oofs.items():
        bo = train_detect.metrics_by_origin(oof)
        slope, icpt = calibration_slope_intercept(oof["y"], oof["p_oof"])
        rows.append({"model": n, "auc_mean": bo["auc"].mean(), "ap_mean": bo["ap"].mean(), "ece_mean": bo["ece"].mean(),
                     "calib_slope_pooled": slope, "calib_intercept_pooled": icpt,
                     **{f"all_{k}": v for k, v in pooled_metrics(oof).items()},
                     **{f"short_{k}": v for k, v in pooled_metrics(oof, is_short).items()},
                     **{f"long_{k}": v for k, v in pooled_metrics(oof, ~is_short).items()}})
    summ = pd.DataFrame(rows)
    s = summ.set_index("model")
    for fam, base in (("hgb_enriched_tuned", "hgb_base_tuned"), ("logit_enriched", "logit_base")):
        for pol in ("keep", "na"):
            s.loc[f"{fam}_{pol}", "gain_vs_base_auc_mean"] = s.at[f"{fam}_{pol}", "auc_mean"] - s.at[base, "auc_mean"]
            s.loc[f"{fam}_{pol}", "gain_vs_base_short_auc"] = s.at[f"{fam}_{pol}", "short_pooled_auc"] - s.at[base, "short_pooled_auc"]
    summ = s.reset_index()
    summ.to_csv(out / "short_name_policy_summary.csv", index=False, encoding="utf-8-sig")

    pairs = [("hgb_enriched_tuned_na", "hgb_enriched_tuned_keep"), ("logit_enriched_na", "logit_enriched_keep"),
             ("hgb_enriched_tuned_keep", "hgb_base_tuned"), ("hgb_enriched_tuned_na", "hgb_base_tuned"),
             ("logit_enriched_keep", "logit_base"), ("logit_enriched_na", "logit_base")]
    boot = []
    for a, b in pairs:
        boot.append({"model_a": a, "model_b": b, "scope": "10개 origin 합산",
                     **cluster_bootstrap_diff(ref["y"], oofs[a]["p_oof"], oofs[b]["p_oof"], stores, n_boot)})
        log(f"클러스터 부트스트랩 {a} − {b} 완료")
    boot = pd.DataFrame(boot)
    boot.to_csv(out / "short_name_policy_bootstrap.csv", index=False, encoding="utf-8-sig")
    meta = {"short_name_max": SHORT_NAME_MAX, "normalize": "PR #21 f026602 normalize_name",
            "n_short_stores_licenses": len(short_ids), "short_rows_in_oof": int(is_short.sum()),
            "short_stores_in_oof": int(len(set(stores[is_short]))), "tuned_params": TUNED_PARAMS, "n_boot": n_boot,
            "master_sha256": train_detect.sha256(Path(master_path)), "online_sha256": train_detect.sha256(Path(online_path)),
            "seconds": round(time.time() - t0, 1)}
    (out / "short_name_policy_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"짧은 상호 민감도 완료 ({meta['seconds']}초) → {out}")
    return {"summary": summ, "bootstrap": boot}


# ---------------------------------------------------------------------------
# PR #43 경쟁지표(comp_* 6종)의 추가 효과 — 서빙 모형·feature set은 바꾸지 않는다
# ---------------------------------------------------------------------------
COMP_COLS = ["comp_pnu_cnt", "comp_pnu_same_type_cnt", "comp_dong_same_type_cnt", "comp_dong_open_4q",
             "comp_dong_close_4q", "comp_dong_density_yoy"]
COMP_LOG1P_COLS = tuple(COMP_COLS[:5])  # 점포 수(0 이상, 오른쪽 꼬리) — 로지스틱에서 log1p. 증감률은 그대로
COMP_FIT_WINDOW = ("2023Q1", "2023Q4")  # 등급 컷오프 보정 창(현행 고정 창)
# 채택 규칙 (결과를 보기 전에 고정, 2026-09-30): 튜닝 절차(중첩) HGB에서 enriched+comp − enriched의 10개 origin
# 합산 AUC 차이 점포 클러스터 부트스트랩 95% CI 하한 > 0 이고, 평균 ECE 증가 ≤ 0.002, |보정 기울기 − 1| 증가 ≤ 0.05.
# 짧은 상호 정책(#44 미결정)은 keep·na 둘 다 충족해야 "채택 후보".
COMP_RULE = {"auc_ci_low_gt": 0.0, "ece_increase_max": 0.002, "slope_dev_increase_max": 0.05}


def nested_tuned_oof(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, cols) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`hpo`와 같은 절차(각 평가 origin t의 학습 구간 t−5 이하 안에서 내부 시간 분할로 격자 선택 → 학습 구간 전체로
    재학습해 t 예측)로 OOF 예측을 만든다. 반환: (OOF[origin, idx, p_oof, y], origin별 선택값)."""
    origins = sorted(df["origin"].unique())
    grid = [dict(zip(HPO_GRID, v)) for v in itertools.product(*HPO_GRID.values())]
    rows, chosen = [], []
    for i in range(MIN_TRAIN_ORIGINS + EMBARGO, len(origins)):
        test_o, train_o = origins[i], origins[: i - EMBARGO]
        in_tr, in_val, gap = inner_split(train_o)
        assert test_o not in in_tr and test_o != in_val and in_val in train_o
        tr_m, va_m = df["origin"].isin(in_tr).to_numpy(), (df["origin"] == in_val).to_numpy()
        scores = [roc_auc_score(y[va_m], detect.fit_predict(X.loc[tr_m, cols], y[tr_m], X.loc[va_m, cols],
                                                             {**detect.DEFAULT_PARAMS, **g})) for g in grid]
        best = grid[int(np.argmax(scores))]
        tr, te = df["origin"].isin(train_o).to_numpy(), (df["origin"] == test_o).to_numpy()
        p = detect.fit_predict(X.loc[tr, cols], y[tr], X.loc[te, cols], {**detect.DEFAULT_PARAMS, **best})
        idx = np.flatnonzero(te)
        rows.append(pd.DataFrame({"origin": test_o, "idx": idx, "p_oof": p, "y": y[te]}))
        chosen.append({"origin": test_o, **best, "inner_val": in_val, "inner_embargo": gap, "inner_auc": max(scores)})
        log(f"  중첩 튜닝 {test_o}: {best}")
    return pd.concat(rows, ignore_index=True), pd.DataFrame(chosen)


def _variant_metrics(oof: pd.DataFrame, stores: np.ndarray, n_boot: int) -> dict:
    bo = train_detect.metrics_by_origin(oof)
    slope, icpt = calibration_slope_intercept(oof["y"], oof["p_oof"])
    fit = oof[oof["origin"].between(*COMP_FIT_WINDOW)]
    cut = bands.suggest_cutoffs(fit["y"].to_numpy(), fit["p_oof"].to_numpy())
    te_m = oof["origin"].isin(BAND_TEST_ORIGINS).to_numpy()
    y_te, p_te = oof.loc[te_m, "y"].to_numpy(), oof.loc[te_m, "p_oof"].to_numpy()
    hi = p_te >= cut["cut_high"]
    lo, up = high_lift_cluster_ci(y_te, hi, stores[te_m], n_boot)
    return {"auc_mean": bo["auc"].mean(), "ap_mean": bo["ap"].mean(), "ece_mean": bo["ece"].mean(),
            "pooled_auc": roc_auc_score(oof["y"], oof["p_oof"]), "pooled_ap": average_precision_score(oof["y"], oof["p_oof"]),
            "calib_slope_pooled": slope, "calib_intercept_pooled": icpt,
            "cut_high": cut["cut_high"], "high_fallback": cut.get("high_fallback"),
            "high_share_test": float(hi.mean()), "high_lift_test": float(y_te[hi].mean() / y_te.mean()) if hi.any() else np.nan,
            "high_lift_ci_low": lo, "high_lift_ci_high": up}


def competition_decision(summary: pd.DataFrame, boot: pd.DataFrame, rule: dict = COMP_RULE) -> pd.DataFrame:
    """정책(keep/na)별로 채택 규칙을 적용한다. summary: model·policy·features별 지표, boot: 쌍 비교."""
    rows = []
    s = summary.set_index(["model", "policy", "features"])
    for pol in sorted(summary["policy"].unique()):
        a, b = s.loc[("hgb_tuned_nested", pol, "enriched+comp")], s.loc[("hgb_tuned_nested", pol, "enriched")]
        bt = boot[(boot["model"] == "hgb_tuned_nested") & (boot["policy"] == pol)].iloc[0]
        d_ece = a["ece_mean"] - b["ece_mean"]
        d_slope = abs(a["calib_slope_pooled"] - 1) - abs(b["calib_slope_pooled"] - 1)
        ok = (bt["auc_ci_low"] > rule["auc_ci_low_gt"] and d_ece <= rule["ece_increase_max"]
              and d_slope <= rule["slope_dev_increase_max"])
        rows.append({"policy": pol, "auc_diff": bt["auc_diff"], "auc_ci_low": bt["auc_ci_low"], "auc_ci_high": bt["auc_ci_high"],
                     "ece_increase": d_ece, "slope_dev_increase": d_slope, "meets_rule": bool(ok)})
    d = pd.DataFrame(rows)
    d["verdict"] = "채택 후보" if d["meets_rule"].all() else "미채택"
    return d


def comp_permutation(df, X, y, cols, params, origin: str = COMPARE_ORIGIN, repeats: int = 5, seed: int = BOOT_SEED) -> pd.DataFrame:
    """origin의 학습 규칙(t−5 이하)으로 학습한 모형에서 comp_* 열(각각·묶음)을 섞었을 때 AUC 감소."""
    origins = sorted(df["origin"].unique())
    i = origins.index(origin)
    tr, te = df["origin"].isin(origins[: i - EMBARGO]).to_numpy(), (df["origin"] == origin).to_numpy()
    m = detect.DetectModel(params=params).fit(X.loc[tr, cols], y[tr])
    Xt = X.loc[te, cols].reset_index(drop=True)
    base = roc_auc_score(y[te], m.predict_proba(Xt))
    rng = np.random.default_rng(seed)
    rows = []
    for name, group in [(c, [c]) for c in COMP_COLS] + [("[group] comp", COMP_COLS)]:
        drops = []
        for _ in range(repeats):
            Xp = Xt.copy()
            perm = rng.permutation(len(Xp))
            for c in group:
                Xp[c] = Xp[c].to_numpy()[perm]
            drops.append(base - roc_auc_score(y[te], m.predict_proba(Xp)))
        rows.append({"feature": name, "auc_drop_mean": float(np.mean(drops)), "auc_drop_std": float(np.std(drops)),
                     "origin": origin, "base_auc": base, "missing_rate": float(Xt[group].isna().any(axis=1).mean())})
    return pd.DataFrame(rows)


def comp_trdar_corr(df: pd.DataFrame, X: pd.DataFrame, base_cols) -> pd.DataFrame:
    """comp_* 6종과 상권 변수(trdar_*)의 Spearman 상관(수치형), 범주형(trdar_change_index)은 상관비 η. 행: comp × trdar."""
    trdar = [c for c in base_cols if c.startswith("trdar_")]
    rows = []
    for c in COMP_COLS:
        for t in trdar:
            v = pd.DataFrame({"c": X[c].astype(float), "t": X[t]}).dropna()
            if t in features.CATEGORICAL:
                g = v.groupby("t", observed=True)["c"]
                eta = float(np.sqrt((g.count() * (g.mean() - v["c"].mean()) ** 2).sum() / ((v["c"] - v["c"].mean()) ** 2).sum()))
                rows.append({"comp": c, "trdar": t, "measure": "eta", "value": eta, "n": len(v)})
            else:
                rows.append({"comp": c, "trdar": t, "measure": "spearman",
                             "value": float(v["c"].corr(v["t"].astype(float), method="spearman")), "n": len(v)})
    return pd.DataFrame(rows)


def competition_eval(master_path: Path, online_path: Path, extra_path: Path, out: Path, n_boot: int,
                     licenses_path: Path | None = None) -> dict:
    """enriched(25) vs enriched+comp(31) × (중첩 튜닝 HGB, logit_enriched) × 짧은 상호 keep/na, rolling OOF 10개 origin.
    OOF는 variant마다 `competition_oof/`에 체크포인트로 저장하고 다시 실행하면 이어서 쓴다."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    ck = out / "competition_oof"
    ck.mkdir(exist_ok=True)
    df, X0, y, base_cols, enriched_cols = load_inputs(master_path, online_path)
    if not enriched_cols:
        raise ValueError("경쟁지표 평가에는 온라인 feature(--online)가 필요하다")
    X0, comp_cols, join = attach_extra_features(df, X0, extra_path, COMP_COLS)
    log(f"경쟁지표 결합: {join['matched_rows']:,}/{join['panel_rows']:,}행 일치, 패널에 없는 표 행 {join['extra_rows_not_in_panel']:,}")
    short_ids = short_name_store_ids(licenses_path or DEFAULT_LICENSES)
    Xs = {"keep": X0, "na": apply_short_name_policy(df, X0, "na", short_ids)}
    feats = {"enriched": enriched_cols, "enriched+comp": enriched_cols + comp_cols}
    logit_log1p = ONLINE_LOG1P_COLS + COMP_LOG1P_COLS

    oofs, chosen = {}, []
    for pol, Xp in Xs.items():
        for fname, cols in feats.items():
            for model in ("hgb_tuned_nested", "logit_enriched"):
                key = f"{model}__{pol}__{fname.replace('+', '_')}"
                path = ck / f"{key}.parquet"
                if path.exists():
                    oofs[(model, pol, fname)] = pd.read_parquet(path)
                    log(f"{key}: 체크포인트 사용")
                    continue
                log(f"{key}: rolling OOF")
                if model == "hgb_tuned_nested":
                    oof, ch = nested_tuned_oof(df, Xp, y, cols)
                    ch.assign(policy=pol, features=fname).to_csv(ck / f"{key}_chosen.csv", index=False)
                else:
                    oof = calibration.rolling_oof_predictions(
                        df, Xp, y, lambda a, b, c, cols=cols: LogitModel(cols, log1p_cols=logit_log1p).fit(a, b).predict_proba(c),
                        min_train_origins=MIN_TRAIN_ORIGINS, embargo=EMBARGO)
                oof = oof.sort_values(["origin", "idx"], ignore_index=True)
                oof.to_parquet(path, index=False)
                oofs[(model, pol, fname)] = oof
    chosen = pd.concat([pd.read_csv(p) for p in sorted(ck.glob("*_chosen.csv"))], ignore_index=True)
    ref = next(iter(oofs.values()))
    assert all((o["idx"].to_numpy() == ref["idx"].to_numpy()).all() for o in oofs.values())
    stores = df.loc[ref["idx"], "store_id"].to_numpy()

    summary = pd.DataFrame([{"model": m, "policy": p, "features": f, **_variant_metrics(o, stores, n_boot)}
                            for (m, p, f), o in oofs.items()])
    summary.to_csv(out / "competition_summary.csv", index=False, encoding="utf-8-sig")
    boot = []
    for model in ("hgb_tuned_nested", "logit_enriched"):
        for pol in Xs:
            a, b = oofs[(model, pol, "enriched+comp")], oofs[(model, pol, "enriched")]
            boot.append({"model": model, "policy": pol, "comparison": "enriched+comp − enriched",
                         **cluster_bootstrap_diff(ref["y"], a["p_oof"], b["p_oof"], stores, n_boot)})
            log(f"부트스트랩 {model} {pol} 완료")
    boot = pd.DataFrame(boot)
    boot.to_csv(out / "competition_bootstrap.csv", index=False, encoding="utf-8-sig")
    decision = competition_decision(summary, boot)
    decision.to_csv(out / "competition_decision.csv", index=False, encoding="utf-8-sig")

    # 보조: 업종·자치구별(PR #40 집단 구분) AUC 차이 — 중첩 튜닝 HGB, keep
    a, b = oofs[("hgb_tuned_nested", "keep", "enriched+comp")], oofs[("hgb_tuned_nested", "keep", "enriched")]
    sub = []
    for gcol in ("biz_type", "gu"):
        gv = df.loc[ref["idx"], gcol].astype(str).to_numpy()
        for g in sorted(set(gv)):
            m = gv == g
            sub.append({"group_by": gcol, "group": g, **cluster_bootstrap_diff(ref["y"].to_numpy()[m], a["p_oof"].to_numpy()[m],
                                                                               b["p_oof"].to_numpy()[m], stores[m], n_boot)})
    pd.DataFrame(sub).to_csv(out / "competition_subgroup.csv", index=False, encoding="utf-8-sig")
    last_params = chosen[(chosen["policy"] == "keep") & (chosen["features"] == "enriched+comp")
                         & (chosen["origin"] == COMPARE_ORIGIN)].iloc[0]
    params = {**detect.DEFAULT_PARAMS, **{k: last_params[k].item() for k in HPO_GRID}}
    comp_permutation(df, Xs["keep"], y, feats["enriched+comp"], params).to_csv(
        out / "competition_permutation.csv", index=False, encoding="utf-8-sig")
    comp_trdar_corr(df, X0, base_cols).to_csv(out / "competition_trdar_corr.csv", index=False, encoding="utf-8-sig")
    chosen.to_csv(out / "competition_hpo_chosen.csv", index=False, encoding="utf-8-sig")

    meta = {"master_sha256": train_detect.sha256(Path(master_path)), "online_sha256": train_detect.sha256(Path(online_path)),
            "extra_join": join, "rule": COMP_RULE, "comp_log1p_in_logit": list(COMP_LOG1P_COLS),
            "cutoff_fit_window": COMP_FIT_WINDOW, "band_test_origins": BAND_TEST_ORIGINS, "hpo_grid": HPO_GRID,
            "n_boot": n_boot, "boot_seed": BOOT_SEED, "n_short_stores": len(short_ids),
            "origins": f"{ref['origin'].min()}~{ref['origin'].max()}", "verdict": decision["verdict"].iloc[0],
            "seconds": round(time.time() - t0, 1)}
    (out / "competition_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"경쟁지표 평가 완료 ({meta['seconds']}초) → 판정 {meta['verdict']}")
    return {"summary": summary, "bootstrap": boot, "decision": decision}


# ---------------------------------------------------------------------------
# #45 튜닝 판단 재확인 — 고정값 (0.03, 15)을 중첩 평가(각 origin 학습 구간 안 선택)와 같은 표로 비교.
# 서빙 모형·컷오프는 바꾸지 않는다. 짧은 상호 keep, 경쟁지표 없음.
# ---------------------------------------------------------------------------
NESTED_MODELS = {"a_hgb_enriched": "현 hgb_enriched", "b_tuned_fixed": "튜닝 고정값 (0.03, 15)",
                 "c_tuned_nested": "중첩 튜닝", "d_logit_enriched": "logit_enriched",
                 "e_serving_fixed": "서빙 구간 선택값 (0.03, 31)"}
# 서빙 학습 구간(2026Q2 → 2021Q1~2025Q1)에 같은 격자·내부 분할(embargo 4)을 적용해 고른 값 (serving_window_hpo.csv)
SERVING_WINDOW_PARAMS = {k: detect.ADOPTED_PARAMS[k] for k in ("learning_rate", "max_leaf_nodes")}  # #45 채택(S13)
assert SERVING_WINDOW_PARAMS == {"learning_rate": 0.03, "max_leaf_nodes": 31}  # 이전 리터럴과 값이 같아야 한다
NESTED_PAIRS = (("c_tuned_nested", "a_hgb_enriched"), ("c_tuned_nested", "d_logit_enriched"),
                ("b_tuned_fixed", "c_tuned_nested"), ("e_serving_fixed", "a_hgb_enriched"))
# 중첩 선택값이 고정값 (0.03, 15)와 같은(학습 구간이 충분한) origin에서만 비교하는 쌍
NESTED_SUBSET_PAIRS = (("c_tuned_nested", "a_hgb_enriched"), ("e_serving_fixed", "a_hgb_enriched"),
                       ("e_serving_fixed", "b_tuned_fixed"))
DYNAMIC_WINDOW_SIZE = 4  # 동적 창: 검증 origin t마다 라벨이 확정된(≤ t−EMBARGO−1) 최근 4개 origin


def lift_curve_crossings(y, p, *, target: float = 2.0, min_n: int = 200) -> dict:
    """`bands.suggest_cutoffs`와 같은 격자(분위 0.50–0.995, 200점, {p ≥ c} ≥ min_n)에서 누적 lift(c) = 실측률(p ≥ c)
    ÷ 전체율이 target을 아래→위로 넘는 지점. 2개 이상이면 컷오프가 격자 위치에 민감하다(불안정)."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    base = y.mean()
    grid = np.unique(np.quantile(p, np.linspace(0.50, 0.995, 200)))
    cs, lifts = [], []
    for c in grid:
        m = p >= c
        if m.sum() < min_n:
            continue
        cs.append(float(c))
        lifts.append(float(y[m].mean() / base))
    above = np.array(lifts) >= target
    up = [cs[k] for k in range(len(cs)) if above[k] and (k == 0 or not above[k - 1])]
    return {"n_grid": len(cs), "n_up_crossings": len(up), "up_crossing_cutoffs": up,
            "n_grid_lift_1_9_to_2_1": int(sum(1.9 <= v <= 2.1 for v in lifts)),
            "first_cut_at_target": up[0] if up else None, "last_cut_at_target": up[-1] if up else None}


def serving_window_hpo(master_path: Path, online_path: Path, out: Path, score_origin: str = "2026Q2") -> pd.DataFrame:
    """서빙 학습 구간(score origin s → 라벨 확정 origin ≤ s−EMBARGO−1, `serve.training_mask`와 같은 규칙)에 `hpo`와 같은
    격자·내부 시간 분할(내부 검증 = 최근 학습 origin, 내부 학습 embargo 4분기)을 적용한 후보별 내부 AUC. 선택만, 서빙 불변."""
    out.mkdir(parents=True, exist_ok=True)
    df, X, y, base_cols, cols = load_inputs(master_path, online_path)
    origins = sorted(df["origin"].unique())
    last = str(pd.Period(score_origin, freq="Q") - (EMBARGO + 1))
    train_o = [o for o in origins if o <= last]
    in_tr, in_val, gap = inner_split(train_o)
    if not gap:
        raise ValueError("서빙 학습 구간이 짧아 내부 검증에 embargo를 둘 수 없다")
    tr_m, va_m = df["origin"].isin(in_tr).to_numpy(), (df["origin"] == in_val).to_numpy()
    rows = []
    for v in itertools.product(*HPO_GRID.values()):
        g = dict(zip(HPO_GRID, v))
        p = detect.fit_predict(X.loc[tr_m, cols], y[tr_m], X.loc[va_m, cols], {**detect.DEFAULT_PARAMS, **g})
        rows.append({**g, "inner_auc": roc_auc_score(y[va_m], p)})
        log(f"서빙 구간 HPO {g}: 내부 AUC {rows[-1]['inner_auc']:.4f}")
    t = pd.DataFrame(rows).sort_values("inner_auc", ascending=False, ignore_index=True)
    t.insert(0, "rank", range(1, len(t) + 1))
    t["serving_train"], t["inner_train"], t["inner_val"] = f"{train_o[0]}~{train_o[-1]}", f"{in_tr[0]}~{in_tr[-1]}", in_val
    t.to_csv(out / "serving_window_hpo.csv", index=False, encoding="utf-8-sig")
    return t


def dynamic_window_high(oof: pd.DataFrame, origins: list[str], size: int = DYNAMIC_WINDOW_SIZE) -> dict:
    """동적 창 규칙: 검증 origin t마다 t 시점에 라벨이 확정된 최근 `size`개 origin(≤ t−EMBARGO−1) raw OOF로
    `bands.suggest_cutoffs` → t의 high 판정. 검증 origin 전체 high 비율·lift와 origin별 창·컷오프."""
    ys, his, info = [], [], []
    for t in BAND_TEST_ORIGINS:
        end = origins.index(t) - EMBARGO - 1
        w = origins[end - size + 1: end + 1]
        assert len(w) == size and labels_matured_by(w, origins, t)
        fit = oof[oof["origin"].isin(w)]
        cut = bands.suggest_cutoffs(fit["y"].to_numpy(), fit["p_oof"].to_numpy())
        te = oof[oof["origin"] == t]
        ys.append(te["y"].to_numpy())
        his.append(te["p_oof"].to_numpy() >= cut["cut_high"])
        info.append(f"{t}: {w[0]}~{w[-1]} cut_high {cut['cut_high']:.4f}")
    y, hi = np.concatenate(ys), np.concatenate(his)
    return {"dyn_high_share_test": float(hi.mean()),
            "dyn_high_lift_test": float(y[hi].mean() / y.mean()) if hi.any() else np.nan, "dyn_windows": "; ".join(info)}


def nested_band_rows(oofs: dict[str, pd.DataFrame], stores: np.ndarray, n_boot: int,
                     origins: list[str] | None = None) -> pd.DataFrame:
    """고정 창(COMP_FIT_WINDOW) raw OOF로 `bands.suggest_cutoffs` → 검증 origin high 비율·lift·클러스터 CI와
    보정 창 lift 곡선의 2배 교차 지점. 교차가 여러 개면 교차 지점마다 검증 high 비율 범위도 낸다.
    origins를 주면 동적 창(`dynamic_window_high`) high 비율도 같이 낸다."""
    rows = []
    for name, oof in oofs.items():
        fit = oof[oof["origin"].between(*COMP_FIT_WINDOW)]
        assert not fit["origin"].isin(BAND_TEST_ORIGINS).any()
        cut = bands.suggest_cutoffs(fit["y"].to_numpy(), fit["p_oof"].to_numpy())
        cross = lift_curve_crossings(fit["y"].to_numpy(), fit["p_oof"].to_numpy())
        te_m = oof["origin"].isin(BAND_TEST_ORIGINS).to_numpy()
        y_te, p_te = oof.loc[te_m, "y"].to_numpy(), oof.loc[te_m, "p_oof"].to_numpy()
        hi = p_te >= cut["cut_high"]
        lo, up = high_lift_cluster_ci(y_te, hi, stores[te_m], n_boot)
        shares = [float((p_te >= c).mean()) for c in cross["up_crossing_cutoffs"]]
        rows.append({"model": name, "fit_window": "~".join(COMP_FIT_WINDOW), "cut_mid": cut["cut_mid"],
                     "cut_high": cut["cut_high"], "fit_base_rate": cut["base_rate"],
                     "fit_p95": float(np.quantile(fit["p_oof"], 0.95)),
                     "test_origins": "~".join((BAND_TEST_ORIGINS[0], BAND_TEST_ORIGINS[-1])),
                     "high_share_test": float(hi.mean()), "n_high_test": int(hi.sum()),
                     "high_lift_test": float(y_te[hi].mean() / y_te.mean()) if hi.any() else np.nan,
                     "high_lift_ci_low": lo, "high_lift_ci_high": up,
                     "lift_n_up_crossings": cross["n_up_crossings"],
                     "lift_up_crossing_cutoffs": ";".join(f"{c:.4f}" for c in cross["up_crossing_cutoffs"]),
                     "lift_grid_points_1_9_to_2_1": cross["n_grid_lift_1_9_to_2_1"],
                     "high_share_test_min_over_crossings": min(shares) if shares else np.nan,
                     "high_share_test_max_over_crossings": max(shares) if shares else np.nan,
                     "cutoff_unstable": cross["n_up_crossings"] > 1,
                     **(dynamic_window_high(oof, origins) if origins is not None else {})})
    return pd.DataFrame(rows)


def nested_recheck(master_path: Path, online_path: Path, out: Path, n_boot: int) -> dict:
    """(a) 현 hgb_enriched (b) 튜닝 고정값 (c) 중첩 튜닝 (d) logit_enriched — rolling OOF 10개 origin, keep, 경쟁지표 없음.
    OOF는 `nested_oof/`에 체크포인트로 저장한다."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    ck = out / "nested_oof"
    ck.mkdir(exist_ok=True)
    df, X, y, base_cols, enriched_cols = load_inputs(master_path, online_path)
    if not enriched_cols:
        raise ValueError("중첩 재확인에는 온라인 feature(--online)가 필요하다")
    tuned = {**detect.DEFAULT_PARAMS, **TUNED_PARAMS}
    fitters = {"a_hgb_enriched": make_fitters(base_cols, enriched_cols)["hgb_enriched"],
               "b_tuned_fixed": lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols], tuned),
               "d_logit_enriched": make_fitters(base_cols, enriched_cols)["logit_enriched"],
               "e_serving_fixed": lambda a, b, c: detect.fit_predict(a[enriched_cols], b, c[enriched_cols],
                                                                     {**detect.DEFAULT_PARAMS, **SERVING_WINDOW_PARAMS})}
    oofs = {}
    for name in NESTED_MODELS:
        path = ck / f"{name}.parquet"
        if path.exists():
            oofs[name] = pd.read_parquet(path)
            log(f"{name}: 체크포인트 사용")
            continue
        log(f"{name}: rolling OOF")
        if name == "c_tuned_nested":
            oof, ch = nested_tuned_oof(df, X, y, enriched_cols)
            ch.to_csv(ck / "c_tuned_nested_chosen.csv", index=False)
        else:
            oof = calibration.rolling_oof_predictions(df, X, y, fitters[name], min_train_origins=MIN_TRAIN_ORIGINS,
                                                      embargo=EMBARGO)
        oofs[name] = oof.sort_values(["origin", "idx"], ignore_index=True)
        oofs[name].to_parquet(path, index=False)
    chosen = pd.read_csv(ck / "c_tuned_nested_chosen.csv")
    ref = oofs["a_hgb_enriched"]
    assert all((o["idx"].to_numpy() == ref["idx"].to_numpy()).all() for o in oofs.values())
    stores = df.loc[ref["idx"], "store_id"].to_numpy()

    rows, by_origin = [], []
    for name, oof in oofs.items():
        bo = train_detect.metrics_by_origin(oof)
        slope, icpt = calibration_slope_intercept(oof["y"], oof["p_oof"])
        per = [(o, *calibration_slope_intercept(g["y"], g["p_oof"])) for o, g in oof.groupby("origin")]
        by_origin.append(bo.assign(model=name, calib_slope=[s for _, s, _ in per], calib_intercept=[i for _, _, i in per]))
        rows.append({"model": name, "label": NESTED_MODELS[name], "pooled_auc": roc_auc_score(oof["y"], oof["p_oof"]),
                     "pooled_ap": average_precision_score(oof["y"], oof["p_oof"]), "auc_mean": bo["auc"].mean(),
                     "ece_mean": bo["ece"].mean(), "calib_slope_pooled": slope, "calib_intercept_pooled": icpt,
                     "calib_slope_origin_mean": float(np.mean([s for _, s, _ in per])),
                     "calib_slope_origin_median": float(np.median([s for _, s, _ in per]))})
    summary = pd.DataFrame(rows)
    summary.to_csv(out / "nested_recheck_summary.csv", index=False, encoding="utf-8-sig")
    pd.concat(by_origin, ignore_index=True).to_csv(out / "nested_recheck_by_origin.csv", index=False, encoding="utf-8-sig")
    chosen.to_csv(out / "nested_recheck_hpo_chosen.csv", index=False, encoding="utf-8-sig")

    boot = []
    for a, b in NESTED_PAIRS:
        boot.append({"model_a": a, "model_b": b, "scope": "10개 origin 합산",
                     **cluster_bootstrap_diff(ref["y"], oofs[a]["p_oof"], oofs[b]["p_oof"], stores, n_boot)})
        log(f"클러스터 부트스트랩 {a} − {b} 완료")
    # 보조: 중첩 선택값이 고정값과 같은 origin만 — 거기서는 (b)=(c)이므로 (c)−(a)가 "튜닝 vs 현"의 사후 적용 없는 비교
    same = chosen.loc[np.all([chosen[k] == TUNED_PARAMS[k] for k in HPO_GRID], axis=0), "origin"].tolist()
    m = ref["origin"].isin(same).to_numpy()
    if m.any():
        for a, b in NESTED_SUBSET_PAIRS:
            boot.append({"model_a": a, "model_b": b, "scope": f"선택값=고정값 origin {len(same)}개 ({min(same)}~{max(same)})",
                         **cluster_bootstrap_diff(ref["y"].to_numpy()[m], oofs[a]["p_oof"].to_numpy()[m],
                                                  oofs[b]["p_oof"].to_numpy()[m], stores[m], n_boot)})
            log(f"클러스터 부트스트랩 {a} − {b} (선택값=고정값 origin) 완료")
    boot = pd.DataFrame(boot)
    boot.to_csv(out / "nested_recheck_bootstrap.csv", index=False, encoding="utf-8-sig")

    band = nested_band_rows({k: oofs[k] for k in ("a_hgb_enriched", "b_tuned_fixed", "c_tuned_nested", "e_serving_fixed")},
                            stores, n_boot, sorted(df["origin"].unique()))
    band.to_csv(out / "nested_recheck_bands.csv", index=False, encoding="utf-8-sig")
    # 보정 창 origin별 선택값·예측 분포 — 창 안에서 모형 설정이 섞이는지 본다
    fitwin = []
    for name in ("b_tuned_fixed", "c_tuned_nested"):
        for o, g in oofs[name][oofs[name]["origin"].between(*COMP_FIT_WINDOW)].groupby("origin"):
            fitwin.append({"model": name, "origin": o, "p_mean": g["p_oof"].mean(), "p_sd": g["p_oof"].std(),
                           "p_p95": float(np.quantile(g["p_oof"], 0.95)), "obs_rate": g["y"].mean()})
    fitwin = pd.DataFrame(fitwin).merge(chosen[["origin", *HPO_GRID]], on="origin", how="left")
    fitwin.loc[fitwin["model"] == "b_tuned_fixed", list(HPO_GRID)] = [TUNED_PARAMS[k] for k in HPO_GRID]
    fitwin.to_csv(out / "nested_recheck_fit_window.csv", index=False, encoding="utf-8-sig")

    meta = {"master_sha256": train_detect.sha256(Path(master_path)), "online_sha256": train_detect.sha256(Path(online_path)),
            "short_name_policy": "keep", "extra_features": None, "tuned_params": TUNED_PARAMS, "hpo_grid": HPO_GRID,
            "serving_window_params": SERVING_WINDOW_PARAMS, "dynamic_window_size": DYNAMIC_WINDOW_SIZE,
            "cutoff_fit_window": COMP_FIT_WINDOW, "band_test_origins": BAND_TEST_ORIGINS, "n_boot": n_boot,
            "boot_seed": BOOT_SEED, "origins": f"{ref['origin'].min()}~{ref['origin'].max()}",
            "seconds": round(time.time() - t0, 1)}
    (out / "nested_recheck_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"중첩 재확인 완료 ({meta['seconds']}초) → {out}")
    return {"summary": summary, "bootstrap": boot, "bands": band, "chosen": chosen, "fit_window": fitwin}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-2 단순 기준 모형 비교")
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--online", type=Path, default=DEFAULT_ONLINE, help="PR #33 online_features (없으면 enriched 생략)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--skip-hpo", action="store_true")
    ap.add_argument("--band-cutoffs", type=Path, default=DEFAULT_BAND_CUTOFFS,
                    help="보정 후보의 등급 비율에 쓸 현재 컷오프 (기본: detect_v0_enriched/band_cutoffs.csv)")
    ap.add_argument("--skip-calib", action="store_true", help="hgb_enriched 보정 후보 비교 생략")
    ap.add_argument("--pre-adoption", action="store_true",
                    help="튜닝 hgb_enriched 적용 전 검정만 (합산 OOF 클러스터 부트스트랩, 새 컷오프 값)")
    ap.add_argument("--cutoff-window", action="store_true",
                    help="등급 컷오프 보정 기간 민감도만 (현·튜닝 hgb_enriched × 보정 기간 3가지)")
    ap.add_argument("--short-name-policy", choices=SHORT_NAME_POLICIES, default="keep",
                    help="na = 정규화 상호 ≤2자 점포의 온라인 feature 6개를 전 origin NA (#28)")
    ap.add_argument("--licenses", type=Path, default=DEFAULT_LICENSES, help="짧은 상호 판정용 인허가 표준화 테이블")
    ap.add_argument("--short-name-sensitivity", action="store_true",
                    help="튜닝 hgb_enriched·logit_enriched를 keep/na 두 정책으로 비교만")
    ap.add_argument("--extra-features", type=Path, default=None,
                    help="(store_id, origin) 추가 feature 표 — 예: PR #43 경쟁지표 competition_features.parquet (없는 키는 NA)")
    ap.add_argument("--competition", action="store_true",
                    help="enriched vs enriched+comp 추가 효과 평가만 (--extra-features 필요, 모형·서빙 기본값 불변)")
    ap.add_argument("--nested-recheck", action="store_true",
                    help="#45 재확인: 현·튜닝 고정·중첩 튜닝 HGB·logit_enriched 비교와 등급 안정성만 (기본값 불변)")
    ap.add_argument("--serving-window-hpo", action="store_true",
                    help="서빙 학습 구간(2026Q2 기준)에 같은 격자·내부 분할(embargo 4)을 적용한 선택만 (서빙 불변)")
    a = ap.parse_args(argv)
    if a.serving_window_hpo:
        serving_window_hpo(a.master, a.online, a.out)
        return
    if a.nested_recheck:
        nested_recheck(a.master, a.online, a.out, a.n_boot)
        return
    if a.competition:
        if a.extra_features is None:
            raise SystemExit("--competition에는 --extra-features가 필요하다")
        competition_eval(a.master, a.online, a.extra_features, a.out, a.n_boot, a.licenses)
        return
    if a.short_name_sensitivity:
        short_name_sensitivity(a.master, a.online, a.out, a.n_boot, a.licenses)
        return
    if a.cutoff_window:
        cutoff_window(a.master, a.online, a.out, a.n_boot, a.short_name_policy, a.licenses)
        return
    if a.pre_adoption:
        pre_adoption(a.master, a.online, a.out, a.n_boot, a.band_cutoffs, a.short_name_policy, a.licenses)
        return
    run(a.master, a.online, a.out, a.n_boot, not a.skip_hpo, a.band_cutoffs, not a.skip_calib,
        a.short_name_policy, a.licenses)


if __name__ == "__main__":
    main()
