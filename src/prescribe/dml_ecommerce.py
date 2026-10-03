"""W3-6: 전자상거래 매출실적 여부 -> 영업이익률, DoubleML IRM (ATE).

사양은 Issue #49(S2~S6)에 등록된 것을 따른다. 이 모듈은 로직만 담고, 실행은 scripts/run_w3_dml.py.
결과는 조건부 연관성의 추정이며 인과효과로 서술하지 않는다(S5 판정 문구).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LassoCV, LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEED = 20261001
OUTCOME = "profit_margin"
TREATMENT = "treat_binary"
WEIGHT_COL = "사업체수가중값"
CATEGORICAL_COLS = ("산업중분류코드", "행정구역시도코드", "일반_창업형태코드")
CONTINUOUS_COL = "tenure_months"
FLAG_COL = "tenure_invalid_flag"
CLIP_Q = (0.01, 0.99)
TRIM = 0.05
N_FOLDS = 5
N_REP = 3

HGB_PARAMS = dict(
    learning_rate=0.05,
    max_leaf_nodes=15,
    min_samples_leaf=50,
    max_iter=300,
    l2_regularization=1.0,
    random_state=SEED,
)

VERDICT_ZERO_IN = "이번 자료에서 관계의 방향을 확인할 근거가 충분하지 않다"
VERDICT_ZERO_OUT = "조건부 연관성 관찰(인과효과 아님)"


def winsorize_outcome(y: pd.Series, q: tuple[float, float] = CLIP_Q) -> tuple[pd.Series, dict]:
    """해당 분석 표본의 무가중 분위수로 clip한다. 분위수와 clip 건수를 함께 돌려준다."""
    lo, hi = (float(v) for v in y.quantile(list(q)))
    clipped = y.clip(lower=lo, upper=hi)
    info = {
        "q_lower": q[0],
        "q_upper": q[1],
        "lower": lo,
        "upper": hi,
        "n_clipped_lower": int((y < lo).sum()),
        "n_clipped_upper": int((y > hi).sum()),
    }
    return clipped, info


def build_design(df: pd.DataFrame, include_industry: bool = True) -> pd.DataFrame:
    """공변량 행렬. 범주형은 원-핫(전 수준 유지), tenure_months 결측은 0, tenure_invalid_flag 포함.

    값이 하나뿐인 열은 정보가 없어 뺀다(표본 56 단독의 산업중분류 더미 등은 include_industry=False로 제외).
    """
    cats = [c for c in CATEGORICAL_COLS if include_industry or c != "산업중분류코드"]
    parts = [pd.get_dummies(df[c].astype(str), prefix=c, dtype=float) for c in cats]
    num = pd.DataFrame(
        {
            CONTINUOUS_COL: df[CONTINUOUS_COL].astype(float).fillna(0.0),
            FLAG_COL: df[FLAG_COL].astype(float),
        },
        index=df.index,
    )
    x = pd.concat(parts + [num], axis=1)
    keep = [c for c in x.columns if x[c].nunique() > 1]
    return x[keep].reset_index(drop=True)


def _with_scaling(estimator, columns):
    """연속형(tenure_months)만 fold 안에서 표준화하는 파이프라인. DoubleML이 학습기에 numpy 배열을 넘기므로
    열은 이름이 아니라 위치로 고른다. tenure 열이 설계행렬에 없으면(상수라 삭제된 경우) 표준화 단계 없이 그대로 쓴다."""
    if columns is None or CONTINUOUS_COL not in list(columns):
        return estimator
    pos = [list(columns).index(CONTINUOUS_COL)]
    scale = ColumnTransformer([("t", StandardScaler(), pos)], remainder="passthrough")
    return Pipeline([("scale", scale), ("m", estimator)])


def make_learners(kind: str = "hgb", columns=None):
    if kind == "hgb":
        return HistGradientBoostingRegressor(**HGB_PARAMS), HistGradientBoostingClassifier(**HGB_PARAMS)
    if kind == "simple":
        return (_with_scaling(LassoCV(cv=5, random_state=SEED), columns),
                _with_scaling(LogisticRegression(C=1.0, max_iter=2000), columns))
    raise ValueError(f"unknown learner kind: {kind}")


def normalize_weights(w: pd.Series | np.ndarray) -> np.ndarray:
    w = np.asarray(w, dtype=float)
    return w / w.mean()


def _build_irm(x: pd.DataFrame, y: np.ndarray, d: np.ndarray, kind: str, weights, n_rep: int):
    import doubleml as dml

    frame = x.copy()
    frame["_y"] = np.asarray(y, dtype=float)
    frame["_d"] = np.asarray(d, dtype=float)
    data = dml.DoubleMLData(frame, y_col="_y", d_cols="_d", x_cols=list(x.columns))
    ml_g, ml_m = make_learners(kind, list(x.columns))
    np.random.seed(SEED)  # 표본 분할은 생성자에서 뽑힌다
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = dml.DoubleMLIRM(
            data,
            ml_g,
            ml_m,
            n_folds=N_FOLDS,
            n_rep=n_rep,
            score="ATE",
            weights=None if weights is None else np.asarray(weights, dtype=float),
            trimming_rule="truncate",
            trimming_threshold=TRIM,
        )
    deprecations = sorted({str(w.message) for w in caught if issubclass(w.category, (DeprecationWarning, FutureWarning))})
    return model, deprecations


def raw_propensity(model, x: pd.DataFrame, d: np.ndarray, kind: str) -> np.ndarray:
    """반복별 절단 전 값 (n_rep, n)을 돌려준다. 절단 전 교차적합 성향점수(반복 평균). DoubleML이 돌려주는 predictions["ml_m"]은 절단 후 값이라
    절단 건수와 [0.05, 0.95] 밖 행을 알 수 없어, 같은 표본 분할(model.smpls)로 ml_m을 다시 적합해 얻는다."""
    from sklearn.base import clone

    ml_m = make_learners(kind, list(x.columns))[1]
    xv = x.to_numpy(float)
    reps = []
    for folds in model.smpls:
        p = np.zeros(len(d))
        for train, test in folds:
            p[test] = clone(ml_m).fit(xv[train], d[train]).predict_proba(xv[test])[:, 1]
        reps.append(p)
    return np.array(reps)


def fit_irm(x, y, d, kind="hgb", weights=None, n_rep=N_REP):
    """IRM(ATE)을 적합하고 요약을 돌려준다. 반환 dict의 "model"은 DoubleMLIRM 객체."""
    model, deprecations = _build_irm(x, y, d, kind, weights, n_rep)
    np.random.seed(SEED)
    model.fit()
    ci = model.confint(level=0.95).iloc[0]
    ps_trunc = np.asarray(model.predictions["ml_m"])[:, :, 0].mean(axis=1)
    ps_reps = raw_propensity(model, x, np.asarray(d, float), kind)
    ps = ps_reps.mean(axis=0)
    lo, hi = float(ci.iloc[0]), float(ci.iloc[1])
    return {
        "model": model,
        "ate": float(model.coef[0]),
        "se": float(model.se[0]),
        "ci_low": lo,
        "ci_high": hi,
        "ci_contains_zero": bool(lo <= 0.0 <= hi),
        "ps": ps,
        # 재적합한 반복별 성향점수를 절단해 평균한 값과 DoubleML 저장값의 최대 차이(0이어야 같은 적합)
        "ps_refit_max_abs_diff": float(np.abs(np.clip(ps_reps, TRIM, 1 - TRIM).mean(axis=0) - ps_trunc).max()),
        "api_deprecation_messages": deprecations,
    }


def verdict(ci_contains_zero: bool) -> str:
    return VERDICT_ZERO_IN if ci_contains_zero else VERDICT_ZERO_OUT


def ps_diagnostics(ps: np.ndarray, d: np.ndarray, sampling_w: np.ndarray | None = None) -> dict:
    """성향점수 분포, 절단 건수, 처치 비율, 절단 IPW의 Kish 유효 표본."""
    ps = np.asarray(ps, float)
    d = np.asarray(d, float)
    qs = np.quantile(ps, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    pc = np.clip(ps, TRIM, 1 - TRIM)
    ipw = d / pc + (1 - d) / (1 - pc)
    if sampling_w is not None:
        ipw = ipw * np.asarray(sampling_w, float)

    def kish(w):
        return float(w.sum() ** 2 / (w**2).sum()) if len(w) else float("nan")

    return {
        "n": int(len(ps)),
        "ps_min": float(ps.min()),
        "ps_max": float(ps.max()),
        "ps_quantiles": dict(zip(["p01", "p05", "p25", "p50", "p75", "p95", "p99"], (float(v) for v in qs))),
        "n_clip_lower": int((ps < TRIM).sum()),
        "n_clip_upper": int((ps > 1 - TRIM).sum()),
        "treated_share": float(d.mean()),
        "kish_ess": kish(ipw),
        "kish_ess_treated": kish(ipw[d == 1]),
        "kish_ess_control": kish(ipw[d == 0]),
    }


def smd_table(x: pd.DataFrame, d: np.ndarray, ps: np.ndarray) -> pd.DataFrame:
    """공변량 표준화 평균차: 가중 전(무가중)과 후(절단 IPW, ATE 가중)."""
    d = np.asarray(d, float)
    pc = np.clip(np.asarray(ps, float), TRIM, 1 - TRIM)
    w = d / pc + (1 - d) / (1 - pc)

    def smd(col, wt):
        v = x[col].to_numpy(float)
        out = []
        for g in (1, 0):
            m = d == g
            wg = wt[m]
            mean = np.average(v[m], weights=wg)
            var = np.average((v[m] - mean) ** 2, weights=wg)
            out.append((mean, var))
        denom = np.sqrt((out[0][1] + out[1][1]) / 2)
        diff = out[0][0] - out[1][0]
        if denom > 0:
            return float(diff / denom), False
        # 분모 0: 두 군 모두 분산이 없다. 평균차도 0이면 SMD=0, 아니면 정의되지 않음(inf, smd_undefined)
        return (0.0, False) if abs(diff) < 1e-12 else (float("inf"), True)

    ones = np.ones(len(d))
    before = [smd(c, ones) for c in x.columns]
    after = [smd(c, w) for c in x.columns]
    return pd.DataFrame(
        {
            "covariate": list(x.columns),
            "smd_before": [b[0] for b in before],
            "smd_after": [a[0] for a in after],
            "smd_undefined": [b[1] or a[1] for b, a in zip(before, after)],
        }
    )


def sensitivity(model, benchmark_sets: dict[str, list[str]], cf_y=0.03, cf_d=0.03, rho=1.0) -> dict:
    """OVB 민감도(RV)와 벤치마크. 벤치마크 변수 집합은 모형에 들어간 공변량 열이어야 한다."""
    model.sensitivity_analysis(cf_y=cf_y, cf_d=cf_d, rho=rho, level=0.95)
    params = model.sensitivity_params
    out = {
        "cf_y": cf_y,
        "cf_d": cf_d,
        "rho": rho,
        "rv": [float(v) for v in np.ravel(params["rv"])],
        "rva": [float(v) for v in np.ravel(params["rva"])],
        "theta_bounds": {k: [float(v) for v in np.ravel(val)] for k, val in params["theta"].items()},
        "benchmarks": {},
    }
    for name, cols in benchmark_sets.items():
        try:
            bm = model.sensitivity_benchmark(benchmarking_set=cols)
            out["benchmarks"][name] = {"columns": cols, **{k: float(v) for k, v in bm.iloc[0].items()}}
        except Exception as exc:  # noqa: BLE001 - 보고용으로 사유를 남긴다
            out["benchmarks"][name] = {"columns": cols, "error": f"{type(exc).__name__}: {exc}"}
    return out


def run_sample(df: pd.DataFrame, name: str, include_industry: bool, benchmark_groups: dict[str, list[str]]) -> dict:
    """한 표본에서 6개 분석을 수행한다. 분석 단위 실패는 해당 분석만 사유와 함께 남긴다."""
    df = df.dropna(subset=[OUTCOME, TREATMENT]).reset_index(drop=True)
    y, clip_info = winsorize_outcome(df[OUTCOME])
    d = df[TREATMENT].astype(int).to_numpy()
    x = build_design(df, include_industry=include_industry)
    wts = normalize_weights(df[WEIGHT_COL])
    res: dict = {
        "sample": name,
        "n": int(len(df)),
        "n_treated": int(d.sum()),
        "clip": clip_info,
        "n_covariates": int(x.shape[1]),
        "covariates": list(x.columns),
    }

    def row(fit, **extra):
        return {
            "n": extra.pop("n", int(len(d))),
            "n_treated": extra.pop("n_treated", int(d.sum())),
            "ate": fit["ate"],
            "se": fit["se"],
            "ci_low": fit["ci_low"],
            "ci_high": fit["ci_high"],
            "ci_contains_zero": fit["ci_contains_zero"],
            "verdict": verdict(fit["ci_contains_zero"]),
            **extra,
        }

    analyses: dict = {}
    main = fit_irm(x, y.to_numpy(), d, "hgb")
    ps_main = main["ps"]
    diag = ps_diagnostics(ps_main, d)
    analyses["1_main"] = row(main, n_clip_lower=diag["n_clip_lower"], n_clip_upper=diag["n_clip_upper"], n_excluded=0, kish_ess=diag["kish_ess"])
    res["api_deprecation_messages"] = main["api_deprecation_messages"]
    res["ps_refit_max_abs_diff"] = main["ps_refit_max_abs_diff"]
    res["diagnostics"] = diag
    res["smd"] = smd_table(x, d, ps_main).to_dict(orient="records")

    def attempt(key, fn):
        try:
            analyses[key] = fn()
        except Exception as exc:  # noqa: BLE001
            analyses[key] = {"status": "적합 불가", "error": f"{type(exc).__name__}: {exc}"}

    def weighted():
        f = fit_irm(x, y.to_numpy(), d, "hgb", weights=wts)
        dg = ps_diagnostics(f["ps"], d, sampling_w=wts)
        return row(f, n_clip_lower=dg["n_clip_lower"], n_clip_upper=dg["n_clip_upper"], n_excluded=0, kish_ess=dg["kish_ess"])

    def excluded():
        keep = (ps_main >= TRIM) & (ps_main <= 1 - TRIM)
        # S2: 결과변수는 해당 분석 표본의 무가중 1·99% 분위로 clip — 남긴 행의 원본 값으로 다시 계산한다
        ye, cinfo = winsorize_outcome(df.loc[keep, OUTCOME])
        f = fit_irm(x[keep].reset_index(drop=True), ye.to_numpy(), d[keep], "hgb")
        dg = ps_diagnostics(f["ps"], d[keep])
        return row(f, n=int(keep.sum()), n_treated=int(d[keep].sum()), n_clip_lower=dg["n_clip_lower"], n_clip_upper=dg["n_clip_upper"], n_excluded=int((~keep).sum()), kish_ess=dg["kish_ess"], clip=cinfo)

    def simple():
        f = fit_irm(x, y.to_numpy(), d, "simple")  # 표준화는 학습기 파이프라인이 fold 안에서 한다
        dg = ps_diagnostics(f["ps"], d)
        return row(f, n_clip_lower=dg["n_clip_lower"], n_clip_upper=dg["n_clip_upper"], n_excluded=0, kish_ess=dg["kish_ess"])

    def ovb():
        sets = {k: [c for c in x.columns if c == k or c.startswith(k + "_")] for k in benchmark_groups}
        s = sensitivity(main["model"], sets)
        return {"n": int(len(d)), "n_treated": int(d.sum()), "ate": main["ate"], "se": main["se"], "ci_low": main["ci_low"], "ci_high": main["ci_high"], "ci_contains_zero": main["ci_contains_zero"], "sensitivity": s}

    def seoul():
        m = df["is_seoul"].astype(bool).to_numpy()
        ys, cinfo = winsorize_outcome(df.loc[m, OUTCOME])  # 서울 부분집합이 자기 분석 표본
        xs = build_design(df[m].reset_index(drop=True), include_industry=include_industry)
        ds = d[m]
        f = fit_irm(xs, ys.to_numpy(), ds, "hgb")
        dg = ps_diagnostics(f["ps"], ds)
        return row(f, n=int(m.sum()), n_treated=int(ds.sum()), n_clip_lower=dg["n_clip_lower"], n_clip_upper=dg["n_clip_upper"], n_excluded=0, kish_ess=dg["kish_ess"], sign=("+" if f["ate"] > 0 else "-" if f["ate"] < 0 else "0"), clip=cinfo)

    attempt("2_weighted", weighted)
    attempt("3_excluded", excluded)
    attempt("4_simple", simple)
    attempt("5_ovb", ovb)
    attempt("6_seoul", seoul)
    res["analyses"] = analyses
    res["_ps_main"] = ps_main
    res["_d"] = d
    return res
