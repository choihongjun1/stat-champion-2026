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
def load_inputs(master_path: Path, online_path: Path | None):
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
    return df, X, y, base_cols, enriched_cols


def run(master_path: Path, online_path: Path | None, out: Path, n_boot: int, do_hpo: bool,
        band_cutoffs: Path | None = None, do_calib: bool = True) -> dict:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    df, X, y, base_cols, enriched_cols = load_inputs(master_path, online_path)
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
    a = ap.parse_args(argv)
    run(a.master, a.online, a.out, a.n_boot, not a.skip_hpo, a.band_cutoffs, not a.skip_calib)


if __name__ == "__main__":
    main()
