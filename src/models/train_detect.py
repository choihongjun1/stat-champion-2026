# -*- coding: utf-8 -*-
"""W2-2 Stage 1 탐지 — 시간 분할 평가 · 민감도 분석 · 보정 · 불확실성 구간 · 위험 등급.

실행:
    python -m src.models.train_detect                       # 기본 (feature set 5개, 부트스트랩 20회)
    python -m src.models.train_detect --quick               # feature set=base만, 부트스트랩 5회 (동작 확인용)
    python -m src.models.train_detect --master <다른 master.parquet> --tag t2
        # 예: attach_trdar_features(lag_quarters=2)로 만든 T-2 master로 같은 평가를 돌린다

검증 설계 (W2-2 설계 결정, 근거 수치는 docs/W2-2_DESIGN_DECISIONS)
- **시간 분할 + 라벨 성숙 embargo 4분기.** origin t를 예측할 때 t−1~t−4는 비우고 t−5 이하로만
  학습한다. origin s의 event_12m은 origin_end(s)+12개월에야 확정되므로 수식상 경계는 s ≤ t−4이지만,
  폐업 신고 지연(성숙 컷오프 1개월, DECISIONS.md 2026-09-18)을 고려해 한 분기를 더 비운다.
  `splits.rolling_origin_folds`·`calibration.rolling_oof_predictions`와 같은 규칙이다.
  embargo 없는 분할은 비교용으로만 돌린다.
- **rolling OOF**: 학습 origin이 최소 4개가 되는 origin부터 (t−5까지 학습 → t 예측)을 모든 origin에
  반복해, 전 구간에서 정직한 out-of-sample 예측을 모은다. 성능표·민감도 비교·보정은 모두 이것으로 한다.
- **보정(isotonic)**: 검증 origin보다 embargo만큼 앞선 OOF 예측으로만 적합한다(그 라벨은 검증
  시점에 확정돼 있다). 검증 구간에서 ECE가 개선될 때만 적용하고, raw/calibrated를 둘 다 기록한다.
- **불확실성 구간**: 점포 단위 부트스트랩 재학습 5~95 백분위 (Venn-ABERS는 폭이 사실상 0이라 기각).
- **band**: 절대 확률 컷오프. high = 평균 대비 2배, mid = 1.2배 이상이 되는 가장 낮은 컷오프.
- **평가 구간 분리**: 상권 polygon 스냅샷(2023-10-23) 이후 origin(2023Q4~)만으로 한 성능을 따로 낸다.
  현재 경계를 과거 origin에 소급하는 문제가 없는 구간이다.

출력 (`outputs/models/detect_v0[_<tag>]/`)
- `oof_metrics_by_origin.csv`  feature set × origin별 AUC·AP·ECE
- `sensitivity_summary.csv`    feature set별 전체 / 2023Q4 이후 성능 요약
- `split_comparison.csv`       embargo 유무 · random split · 점포 홀드아웃 비교 (base)
- `feature_importance.csv`     permutation AUC 감소 (컬럼별 + group별, 마지막 origin, 예측 기여 진단용)
- `missing_by_origin.csv`      base 입력의 origin별 결측률
- `calibration_report.csv`, `reliability.png`
- `band_cutoffs.csv`, `band_profile.csv`, `band_share_by_origin.csv`
- `risk_scores.parquet`        검증 origin의 store_id × origin 위험도 (REPORT_SCHEMA의 risk 블록)
- `run_meta.json`              입력 파일 sha256, 설정, feature 목록, 소요 시간
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config
from src.models import bands, calibration, detect, features, splits, uncertainty

EMBARGO = splits.LABEL_HORIZON_QUARTERS  # 4
MIN_TRAIN_ORIGINS = 4
EVAL_FROM = "2023Q4"  # 상권 polygon 스냅샷(2023-10-23) 이후 origin
TEST_SIZE = 2  # 최종 검증 origin 수 (마지막 2개)
MODEL_NAME = "detect_v0"
# #32 리뷰(2026-09-26·27): 튜닝 후보(HPO, feat/w2-2-benchmark). 최종 채택은 #45 결정 대기 — 여기서는
# 비교용으로만 쓰고, risk_scores.parquet 등 실제 서빙 산출물은 여전히 DEFAULT_PARAMS(현 설정)로 만든다.
TUNED_PARAMS = {"learning_rate": 0.03, "max_leaf_nodes": 15}
# 보정 3구간(#32 리뷰 M2): fit(보정기 학습) 4개 origin, select(적용 여부 선택) 4개 origin, test(최종 보고,
# TEST_SIZE개) — 서로 겹치지 않게 순서대로 이어 붙인다. 선택과 최종 평가를 같은 구간에서 하면 선택 편향이
# 생긴다(choihongjun1 리뷰). 오늘 데이터(2021Q1~2025Q2, 18개 origin)에서는 fit=2023Q1–Q4, select=2024Q1–Q4,
# test=2025Q1–Q2와 같다. select 구간의 라벨은 **모형 개발 시점(오늘)** 에는 확정돼 있지만, 실제 서빙 시점
# 규칙(embargo 4분기)과는 별개다 — 서빙은 그 시점에 확정된 origin만 쓴다.
FIT_WINDOW = 4
SELECT_WINDOW = 4
CALIB_CANDIDATES = ("raw", "isotonic", "platt")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
def attach_online(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """온라인 Enriched 테이블을 (store_id, origin) m:1로 붙인다. 행 수 불변, **게시물 내용 시점**이
    origin_end를 넘지 않는지(`online_feature_asof`)를 확인한다.

    #33 리뷰: `online_available_at`(실제 수집일, ≈2026-09)은 검증하지 않는다 — 과거 origin은 항상
    수집일보다 앞서므로 `online_available_at > origin_end`가 항상 성립하는데, 이건 시점 누수가 아니라
    회고적 재구성(수집은 한 번, 이후 게시월로 필터링)이라는 뜻이다. `online_data/online_features.py`
    docstring "시점 메타 두 가지"에 이 구분과, 이 함수가 보장하는 것/보장하지 않는 것을 적어 뒀다."""
    on = pd.read_parquet(path)
    cols = ["store_id", "origin"] + [c for c in features.ONLINE_PREDICTORS if c in on.columns]
    if "online_feature_asof" in on.columns:
        cols.append("online_feature_asof")
    if on.duplicated(["store_id", "origin"]).any():
        raise ValueError("온라인 테이블 (store_id, origin) 중복")
    out = df.merge(on[cols], on=["store_id", "origin"], how="left", validate="1:1")
    if len(out) != len(df):
        raise ValueError("온라인 조인 후 행 수가 바뀌었다")
    if "online_feature_asof" in out.columns:
        late = (pd.to_datetime(out["online_feature_asof"]) > pd.to_datetime(out["origin_end"])).sum()
        if late:
            raise ValueError(f"online_feature_asof > origin_end {late}건 — 게시물 내용 시점 누수")
        out = out.drop(columns="online_feature_asof")
    return out


def load_master(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    need = {"store_id", "origin", "event_12m"}
    if not need <= set(df.columns):
        raise ValueError(f"master에 {need - set(df.columns)} 컬럼이 없다")
    if df.duplicated(["store_id", "origin"]).any():
        raise ValueError("(store_id, origin) 중복")
    return df.sort_values(["origin", "store_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# rolling OOF
# ---------------------------------------------------------------------------
def rolling_oof(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, *, params=None) -> pd.DataFrame:
    """origin t 예측 = origin ≤ t−EMBARGO−1로 학습한 모형. 반환: [origin, idx, p_oof, y]."""
    return calibration.rolling_oof_predictions(
        df, X, y,
        lambda a, b, c: detect.fit_predict(a, b, c, params),
        min_train_origins=MIN_TRAIN_ORIGINS, embargo=EMBARGO,
    )


def metrics_by_origin(oof: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for o, g in oof.groupby("origin"):
        r = detect.ranking_metrics(g["y"], g["p_oof"])
        r["ece"] = calibration.expected_calibration_error(g["y"].to_numpy(), g["p_oof"].to_numpy())
        rows.append({"origin": o, **r})
    return pd.DataFrame(rows)


def summarize(by_origin: pd.DataFrame, oof: pd.DataFrame) -> dict:
    """전체 OOF 구간과 EVAL_FROM 이후 구간의 요약."""
    out = {}
    for name, m in (("all", oof["origin"] >= ""), (f"from_{EVAL_FROM}", oof["origin"] >= EVAL_FROM)):
        g = oof[m]
        bo = by_origin[by_origin["origin"].isin(g["origin"].unique())]
        out[f"{name}_origins"] = f"{g['origin'].min()}~{g['origin'].max()}"
        out[f"{name}_auc_mean"] = float(bo["auc"].mean())
        out[f"{name}_ap_mean"] = float(bo["ap"].mean())
        out[f"{name}_ap_lift_mean"] = float(bo["ap_lift"].mean())
        out[f"{name}_ece_mean"] = float(bo["ece"].mean())
    return out


# ---------------------------------------------------------------------------
# 분할 방식 비교 (보고서용 대조군)
# ---------------------------------------------------------------------------
def _metrics_2025q2(df: pd.DataFrame, te: np.ndarray, y: np.ndarray, p: np.ndarray, last_origin: str) -> dict:
    """te(불 마스크) 중 origin==last_origin(오늘 데이터에서는 2025Q2)인 행만 추린 성능.
    time_split 두 설정은 test 자체가 이미 이 origin 하나뿐이라 전체 성능과 같게 나온다 — random_split·
    점포 홀드아웃처럼 test가 여러 origin에 걸쳐 있는 설정과 그대로 비교할 수 있게 같은 방식으로 낸다."""
    te_idx = np.flatnonzero(te)
    sub = df.loc[te_idx, "origin"].to_numpy() == last_origin
    m = detect.ranking_metrics(y[te][sub], p[sub])
    return {f"{k}_last_origin": v for k, v in m.items()}


def split_comparison(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, params_by_name: dict[str, dict] | None = None
                     ) -> pd.DataFrame:
    """분할 방식 4가지 × 모형 설정(기본: 현 설정 하나, params_by_name으로 여러 개 비교 가능).
    random_split·점포 홀드아웃은 **"금지·대조군"이지 실전 성능이 아니다** — 전체 origin에서 무작위로 뽑은
    약 20%(splits.random_split_baseline/store_holdout_split의 test_frac=0.2)를 평가한 값이라, 두 time_split
    설정(단일 최신 origin 검증)과 모집단이 다르다. `*_last_origin` 열은 넷 모두 같은 origin(최신, 오늘
    데이터에서는 2025Q2)만 추려 낸 성능이라 이 열끼리는 비교할 수 있다."""
    params_by_name = params_by_name or {"현 설정": None}
    last_origin = splits.sorted_origins(df)[-1]
    rows = []
    for pname, params in params_by_name.items():
        for emb in (EMBARGO, 0):
            f = splits.rolling_origin_folds(df, min_train_origins=MIN_TRAIN_ORIGINS, embargo=emb, test_size=1)[-1]
            tr, te = f.masks(df)
            p = detect.fit_predict(X[tr], y[tr], X[te], params)
            rows.append({"params": pname, "setting": f"time_split(embargo={emb})",
                        "test_scope": f"단일 origin {f.test_origins[0]}", "test": f.test_origins[0],
                        "train": f"{f.train_origins[0]}~{f.train_origins[-1]}", "n_train": int(tr.sum()),
                        **detect.ranking_metrics(y[te], p), **_metrics_2025q2(df, te, y, p, last_origin)})
        for name, (tr, te) in (("random_split(금지·대조군)", splits.random_split_baseline(df)),
                               ("store_holdout(민감도)", splits.store_holdout_split(df))):
            p = detect.fit_predict(X[tr], y[tr], X[te], params)
            rows.append({"params": pname, "setting": name, "test_scope": "전체 origin 무작위 ~20% 표본",
                        "test": "mixed", "train": "mixed", "n_train": int(tr.sum()),
                        **detect.ranking_metrics(y[te], p), **_metrics_2025q2(df, te, y, p, last_origin)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 보정 · 구간 · 등급 · risk_scores
# ---------------------------------------------------------------------------
def calibration_windows(origins: list[str], *, fit_size: int = FIT_WINDOW, select_size: int = SELECT_WINDOW,
                        test_size: int = TEST_SIZE) -> dict[str, list[str]]:
    """fit·select·test 3구간 (겹치지 않고 순서대로 이어짐, test가 가장 최근)."""
    if len(origins) < fit_size + select_size + test_size:
        raise ValueError(f"origin이 부족하다: {len(origins)} < {fit_size + select_size + test_size}")
    test = origins[-test_size:]
    select = origins[-(test_size + select_size):-test_size]
    fit = origins[-(test_size + select_size + fit_size):-(test_size + select_size)]
    return {"fit": fit, "select": select, "test": test}


def fit_candidates(oof: pd.DataFrame, fit_origins: list[str]) -> dict:
    """fit 구간 OOF로 후보 보정기를 적합한다. raw는 적합할 게 없어 None."""
    cal = oof[oof["origin"].isin(fit_origins)]
    return {"raw": None, "isotonic": calibration.IsotonicCalibrator().fit(cal["p_oof"], cal["y"]),
            "platt": calibration.PlattCalibrator().fit(cal["p_oof"], cal["y"])}


def apply_candidate(cal, p: np.ndarray) -> np.ndarray:
    return np.asarray(p, dtype=float) if cal is None else cal.predict(p)


def candidate_window_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    slope, intercept = calibration.calibration_slope_intercept(y, p)
    return {"brier": calibration.brier_score(y, p), "log_loss": calibration.log_loss(y, p),
            "ece": calibration.expected_calibration_error(y, p), "pred_mean": float(np.mean(p)),
            "obs_rate": float(np.mean(y)), "n": len(y), "calib_slope": slope, "calib_intercept": intercept}


def choose_calibration(oof: pd.DataFrame, df: pd.DataFrame, windows: dict[str, list[str]], *, n_boot: int = 1000,
                       seed: int = 20260928) -> tuple[str, dict, dict, pd.DataFrame]:
    """선택 규칙(#32 리뷰 M2, 미리 고정): select 구간에서 Brier가 가장 낮은 후보를 고른다. raw보다 나아도
    그 차이가 점포 단위 부트스트랩 95% CI상 유의하지 않으면(0을 포함하면) raw를 유지한다 — select 구간
    표본이 작아 우연한 차이로 잘못 채택하는 것을 막는다. select와 최종 평가(test) 구간을 분리해 선택 편향도
    막는다. 반환: (선택된 후보, 선택 이유 dict, 후보별 fit 결과 dict, select 구간 후보별 지표 표)."""
    cands = fit_candidates(oof, windows["fit"])
    sel = oof[oof["origin"].isin(windows["select"])]
    y_sel, p_sel_raw = sel["y"].to_numpy(), sel["p_oof"].to_numpy()
    store_sel = df.loc[sel["idx"].to_numpy(), "store_id"].to_numpy()
    sel_preds = {name: apply_candidate(cal, p_sel_raw) for name, cal in cands.items()}
    sel_metrics = pd.DataFrame([{"candidate": name, "window": "select", **candidate_window_metrics(y_sel, p)}
                                for name, p in sel_preds.items()])
    best = sel_metrics.set_index("candidate")["brier"].idxmin()
    decision = {"fit_origins": windows["fit"], "select_origins": windows["select"], "test_origins": windows["test"],
               "select_rule": "select 구간 Brier 최소 후보, raw 대비 부트스트랩 95% CI로 유의성 확인"}
    if best == "raw":
        chosen, decision["reason"] = "raw", "select 구간에서 원 확률의 Brier가 이미 가장 낮음"
        decision["boot_vs_raw"] = None
    else:
        boot = calibration.bootstrap_brier_diff(y_sel, sel_preds[best], sel_preds["raw"], store_sel, n_boot=n_boot,
                                                 seed=seed)
        decision["boot_vs_raw"] = {"candidate": best, **boot}
        if boot["significant"] and boot["diff"] < 0:
            chosen = best
            decision["reason"] = (f"select 구간 Brier가 raw보다 낮고 부트스트랩 95% CI [{boot['ci_low']:.5f}, "
                                 f"{boot['ci_high']:.5f}]가 유의함 (0을 포함하지 않음)")
        else:
            chosen = "raw"
            decision["reason"] = (f"{best}의 Brier가 raw보다 낮아 보이지만 부트스트랩 95% CI "
                                 f"[{boot['ci_low']:.5f}, {boot['ci_high']:.5f}]가 0을 포함해 유의하지 않음 "
                                 "— 원 확률(raw) 유지")
    decision["chosen"] = chosen
    return chosen, decision, cands, sel_metrics


def calibration_analysis(oof: pd.DataFrame, df: pd.DataFrame, origins: list[str], *, n_boot: int = 1000,
                         seed: int = 20260928) -> dict:
    """3구간 보정 비교 전체: 선택(select) + 최종 평가(test) 두 구간에서 raw/isotonic/platt 세 후보를 모두
    보고하고, 어느 것을 채택하는지와 그 이유를 남긴다."""
    windows = calibration_windows(origins)
    chosen, decision, cands, sel_metrics = choose_calibration(oof, df, windows, n_boot=n_boot, seed=seed)
    sel = oof[oof["origin"].isin(windows["select"])]
    y_sel, p_sel_raw = sel["y"].to_numpy(), sel["p_oof"].to_numpy()
    sel_preds = {name: apply_candidate(cal, p_sel_raw) for name, cal in cands.items()}
    te = oof[oof["origin"].isin(windows["test"])]
    y_te, p_te_raw = te["y"].to_numpy(), te["p_oof"].to_numpy()
    te_preds = {name: apply_candidate(cal, p_te_raw) for name, cal in cands.items()}
    te_metrics = pd.DataFrame([{"candidate": name, "window": "test", **candidate_window_metrics(y_te, p)}
                              for name, p in te_preds.items()])
    report = pd.concat([sel_metrics, te_metrics], ignore_index=True)
    report["chosen"] = report["candidate"] == chosen
    return {"windows": windows, "decision": decision, "report": report, "candidates": cands,
            "select_y": y_sel, "select_preds": sel_preds, "test_y": y_te, "test_preds": te_preds, "chosen": chosen}


def bootstrap_ci(df, X, y, test_origin: str, origins: list[str], *, n_boot: int, params=None):
    """test_origin 예측용 학습 구간(≤ t−EMBARGO−1, rolling OOF와 동일)에서 점포 단위 부트스트랩."""
    t = origins.index(test_origin)
    tr = df["origin"].isin(origins[: t - EMBARGO]).to_numpy()
    te = (df["origin"] == test_origin).to_numpy()
    lo, hi, _ = uncertainty.bootstrap_interval(
        lambda a, b, c: detect.fit_predict(a, b, c, params),
        X[tr], y[tr], X[te], n_boot=n_boot, alpha=0.10, group=df.loc[tr, "store_id"].to_numpy(),
    )
    return te, lo, hi


def peer_stats(frame: pd.DataFrame) -> pd.DataFrame:
    """같은 origin · 자치구 · 업종 안에서의 백분위와 중앙값."""
    keys = ["origin", "gu", "biz_type"]
    g = frame.groupby(keys, observed=True)["probability_12m"]
    frame["percentile"] = (g.rank(pct=True, method="average") * 100).round().astype("Int64")
    frame["peer_median"] = g.transform("median")
    frame["peer_group"] = frame["gu"].astype(str) + " " + frame["biz_type"].astype(str)
    return frame


# ---------------------------------------------------------------------------
# 변수 기여 진단 (permutation)
# ---------------------------------------------------------------------------
def permutation_importance(df, X, y, origins: list[str], *, n_sample: int = 30000, n_repeats: int = 3,
                           seed: int = 20260925, params=None) -> pd.DataFrame:
    """마지막 origin을 rolling 규칙(≤ t−EMBARGO−1)으로 예측한 모형에서, 컬럼(또는 group)을 섞었을 때의
    AUC 감소. 예측 기여 진단용이며 인과 해석이 아니다. group 행은 같은 group 컬럼을 함께 섞은 값이다.
    """
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    t = len(origins) - 1
    tr = df["origin"].isin(origins[: t - EMBARGO]).to_numpy()
    te = np.flatnonzero((df["origin"] == origins[t]).to_numpy())
    if len(te) > n_sample:
        te = rng.choice(te, n_sample, replace=False)
    model = detect.DetectModel(params=dict(params or detect.DEFAULT_PARAMS)).fit(X[tr], y[tr])
    Xt, yt = X.iloc[te].reset_index(drop=True), y[te]
    base_auc = roc_auc_score(yt, model.predict_proba(Xt))
    units = [(c, [c]) for c in model.columns_]
    groups: dict[str, list[str]] = {}
    for c in model.columns_:
        groups.setdefault(features.group_of(c), []).append(c)
    units += [(f"[group] {g}", cs) for g, cs in groups.items() if len(cs) > 1]
    rows = []
    for name, cs in units:
        drops = []
        for _ in range(n_repeats):
            Xp = Xt.copy()
            perm = rng.permutation(len(Xp))
            for c in cs:
                Xp[c] = Xt[c].iloc[perm].to_numpy() if not isinstance(Xt[c].dtype, pd.CategoricalDtype) \
                    else pd.Categorical(Xt[c].iloc[perm].to_numpy(), categories=Xt[c].cat.categories)
            drops.append(base_auc - roc_auc_score(yt, model.predict_proba(Xp)))
        rows.append({"feature": name, "auc_drop_mean": float(np.mean(drops)), "auc_drop_std": float(np.std(drops)),
                     "missing_rate": float(Xt[cs].isna().all(axis=1).mean())})
    out = pd.DataFrame(rows).sort_values("auc_drop_mean", ascending=False)
    out.attrs["base_auc"] = base_auc
    return out.assign(base_auc=base_auc, test_origin=origins[t], dropped_all_na=",".join(model.dropped_all_na_))


# ---------------------------------------------------------------------------
def run(master_path: Path, out_dir: Path, feature_sets: list[str], n_boot: int,
        with_split_comparison: bool, online_path: Path | None = None, primary: str = "base") -> None:
    """primary: 보정·등급·부트스트랩·risk_scores를 만들 feature set (기본 base, 온라인 반영 시 enriched)."""
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_master(master_path)
    if online_path is not None:
        df = attach_online(df, online_path)
        log(f"온라인 Enriched 테이블 결합: {online_path}")
    elif "enriched" in feature_sets:
        log("온라인 테이블이 없어 enriched feature set을 건너뛴다")
        feature_sets = [f for f in feature_sets if f != "enriched"]
    y = df["event_12m"].to_numpy().astype(int)
    origins = splits.sorted_origins(df)
    test_origins = origins[-TEST_SIZE:]
    log(f"master {len(df):,}행 / 점포 {df['store_id'].nunique():,} / origin {origins[0]}~{origins[-1]}")

    meta = {"primary_feature_set": primary, "master": str(master_path), "master_sha256": sha256(master_path),
            "online": str(online_path) if online_path else None,
            "online_sha256": sha256(online_path) if online_path else None, "embargo": EMBARGO,
            "min_train_origins": MIN_TRAIN_ORIGINS, "eval_from": EVAL_FROM, "test_origins": test_origins,
            "n_boot": n_boot, "params": detect.DEFAULT_PARAMS, "feature_sets": {}}

    by_origin_all, summary, oof_base, X_base = [], [], None, None
    for fs in feature_sets:
        cols = features.select_features(df.columns, fs)
        X = features.build_X(df, cols)
        meta["feature_sets"][fs] = cols
        log(f"[{fs}] feature {len(cols)}개 → rolling OOF")
        oof = rolling_oof(df, X, y)
        bo = metrics_by_origin(oof).assign(feature_set=fs)
        by_origin_all.append(bo)
        summary.append({"feature_set": fs, "n_features": len(cols), **summarize(bo, oof)})
        log(f"[{fs}] AUC 평균 {bo['auc'].mean():.4f} / AP 평균 {bo['ap'].mean():.4f} "
            f"/ {EVAL_FROM}~ AUC {bo.loc[bo['origin'] >= EVAL_FROM, 'auc'].mean():.4f}")
        if fs == primary:
            oof_base, X_base = oof, X
            features.missing_by_origin(df, cols).to_csv(out_dir / "missing_by_origin.csv")

    pd.concat(by_origin_all).to_csv(out_dir / "oof_metrics_by_origin.csv", index=False)
    summ = pd.DataFrame(summary)
    if "base" in set(summ["feature_set"]):
        b = summ.set_index("feature_set").loc["base"]
        for k in ("all_auc_mean", "all_ap_mean", f"from_{EVAL_FROM}_auc_mean", f"from_{EVAL_FROM}_ap_mean"):
            summ[f"{k}_vs_base"] = summ[k] - b[k]
    summ.to_csv(out_dir / "sensitivity_summary.csv", index=False)

    if oof_base is None:
        log(f"주 feature set({primary})이 없어 보정·등급·risk_scores를 건너뛴다")
        return

    if with_split_comparison:
        log("분할 방식 비교 (embargo 4/0, random, 점포 홀드아웃 × 현 설정·튜닝 설정)")
        split_comparison(df, X_base, y, {"현 설정": None, "튜닝": TUNED_PARAMS}).to_csv(
            out_dir / "split_comparison.csv", index=False)
    log(f"변수 기여 진단 (permutation, {origins[-1]})")
    imp = permutation_importance(df, X_base, y, origins)
    imp.to_csv(out_dir / "feature_importance.csv", index=False)
    log("\n" + imp.head(12)[["feature", "auc_drop_mean", "auc_drop_std", "missing_rate"]].to_string(index=False))

    # --- 보정: 3구간(fit/select/test) 비교, 현 설정으로 실제 서빙에 쓸 후보를 고른다.
    # 튜닝 설정은 같은 방식으로 한 번 더 돌려 비교표에만 싣는다(#45 결정 전까지 서빙은 그대로 현 설정).
    # 보정 선택 유의성 검정은 불확실성 구간용 n_boot(--quick=5 등 리스크 구간 재학습 횟수)와 별개다 —
    # 재학습이 아니라 이미 있는 OOF 예측의 재표본이라 훨씬 싸다. --quick만 줄인다.
    calib_boot = 200 if n_boot <= 5 else 1000
    log(f"보정 3구간 비교 (fit/select/test): {calibration_windows(origins)}")
    analysis = calibration_analysis(oof_base, df, origins, n_boot=calib_boot)
    log(f"보정 선택: {analysis['chosen']} — {analysis['decision']['reason']}")
    log("\n" + analysis["report"].to_string(index=False))

    oof_tuned = rolling_oof(df, X_base, y, params=TUNED_PARAMS)
    analysis_tuned = calibration_analysis(oof_tuned, df, origins, n_boot=calib_boot)
    log(f"[비교용] 튜닝 설정 보정 선택: {analysis_tuned['chosen']} — {analysis_tuned['decision']['reason']}")
    pd.concat([analysis["report"].assign(params="현 설정"), analysis_tuned["report"].assign(params="튜닝")],
             ignore_index=True).to_csv(out_dir / "calibration_window_report.csv", index=False)
    (out_dir / "calibration_decision.json").write_text(
        json.dumps({"현 설정": analysis["decision"], "튜닝": analysis_tuned["decision"]},
                  ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    chosen = analysis["chosen"]
    cal_current = analysis["candidates"][chosen]
    calibration.plot_reliability(
        {"raw": calibration.reliability_table(analysis["select_y"], analysis["select_preds"]["raw"]),
         chosen: calibration.reliability_table(analysis["select_y"], analysis["select_preds"][chosen])},
        out_dir / "reliability_select.png",
        title=f"Reliability(선택 구간) — {analysis['windows']['select'][0]}~{analysis['windows']['select'][-1]}")
    calibration.plot_reliability(
        {"raw": calibration.reliability_table(analysis["test_y"], analysis["test_preds"]["raw"]),
         chosen: calibration.reliability_table(analysis["test_y"], analysis["test_preds"][chosen])},
        out_dir / "reliability.png",
        title=f"Reliability(최종) — {analysis['windows']['test'][0]}~{analysis['windows']['test'][-1]}")

    te_oof = oof_base[oof_base["origin"].isin(analysis["windows"]["test"])]
    p_te = analysis["test_preds"][chosen]

    # --- band 컷오프: fit 구간 OOF(선택된 후보 적용)로 정하고 최종(test) 구간에 적용
    fit_oof = oof_base[oof_base["origin"].isin(analysis["windows"]["fit"])]
    p_fit = apply_candidate(cal_current, fit_oof["p_oof"].to_numpy())
    cut = bands.suggest_cutoffs(fit_oof["y"].to_numpy(), p_fit)
    pd.DataFrame([cut]).to_csv(out_dir / "band_cutoffs.csv", index=False)
    band_te = bands.assign_bands_absolute(p_te, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
    band_prof = bands.band_profile(te_oof["y"].to_numpy(), p_te, band_te)
    band_prof.to_csv(out_dir / "band_profile.csv", index=False)
    hi = band_prof.set_index("band").loc["high"]
    if hi["pred_mean"] > hi["obs_rate"] * 1.05:
        log(f"참고: high 등급 평균 예측 {hi['pred_mean']:.3f}이 실측 {hi['obs_rate']:.3f}보다 높다 "
            "— 과대예측 경향 (DECISIONS 기록 대상)")
    p_all = apply_candidate(cal_current, oof_base["p_oof"].to_numpy())
    b_all = bands.assign_bands_absolute(p_all, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
    share = pd.crosstab(oof_base["origin"], b_all, normalize="index").reindex(columns=list(bands.BANDS), fill_value=0)
    share.to_csv(out_dir / "band_share_by_origin.csv")
    log(f"band 컷오프 mid {cut['cut_mid']:.4f} / high {cut['cut_high']:.4f} (base {cut['base_rate']:.4f})"
        + (" — high fallback(p95)" if cut["high_fallback"] else ""))
    # #45: 컷오프 provenance — 서빙(serve_meta)이 그대로 옮겨 적는다. 기본 동작은 바꾸지 않고 기록만 한다.
    is_high_te = band_te == "high"
    lift, lift_lo, lift_hi = bands.high_lift_cluster_ci(
        te_oof["y"].to_numpy(), is_high_te, df.loc[te_oof["idx"].to_numpy(), "store_id"].to_numpy(), n_boot=calib_boot)
    meta["band_definition"] = {"cut_mid": bands.CUT_MID_DEFINITION, "cut_high": bands.CUT_HIGH_DEFINITION}
    meta["band_provenance"] = {
        "calib_origins": analysis["windows"]["fit"], "test_origins": analysis["windows"]["test"],
        "base_rate": cut["base_rate"], "cut_mid": cut["cut_mid"], "cut_high": cut["cut_high"],
        "high_fallback": cut["high_fallback"], "mid_fallback": cut["mid_fallback"],
        "high_share_test": float(is_high_te.mean()), "high_lift_test": lift, "high_lift_ci95": [lift_lo, lift_hi],
        "scale": "calibrated" if chosen != "raw" else "raw", "method": "bands.suggest_cutoffs"}

    # --- 불확실성 구간
    ci_lo = np.full(len(te_oof), np.nan)
    ci_hi = np.full(len(te_oof), np.nan)
    if n_boot > 0:
        pos_in_te = pd.Series(np.arange(len(te_oof)), index=te_oof["idx"].to_numpy())
        for o in analysis["windows"]["test"]:
            log(f"부트스트랩 {n_boot}회 ({o})")
            te_mask, lo, hi_ci = bootstrap_ci(df, X_base, y, o, origins, n_boot=n_boot)
            lo, hi_ci = apply_candidate(cal_current, lo), apply_candidate(cal_current, hi_ci)
            at = pos_in_te.loc[np.flatnonzero(te_mask)].to_numpy()
            ci_lo[at], ci_hi[at] = lo, hi_ci

    # --- OOF 저장 (#32 리뷰 M5): store_id × origin × 모형 설정별 OOF 예측, 생존분석(C-index)은 후속 PR
    oof_out = pd.concat([
        oof_base.assign(config="현 설정", store_id=df.loc[oof_base["idx"].to_numpy(), "store_id"].to_numpy()),
        oof_tuned.assign(config="튜닝", store_id=df.loc[oof_tuned["idx"].to_numpy(), "store_id"].to_numpy()),
    ], ignore_index=True)[["store_id", "origin", "config", "p_oof", "y"]]
    oof_path = out_dir / "oof_predictions.parquet"
    oof_out.to_parquet(oof_path, index=False)
    log(f"OOF 예측 {len(oof_out):,}행 → {oof_path}")

    # --- risk_scores
    rows = df.loc[te_oof["idx"].to_numpy(), ["store_id", "origin", "gu", "biz_type"]].reset_index(drop=True)
    rows["probability_12m"] = p_te
    # 점추정(보정된 OOF)과 부트스트랩 백분위는 계산 경로가 달라 점추정이 구간 밖에 놓일 수 있다.
    # 화면 일관성을 위해 구간이 점추정을 포함하도록 넓힌다 (좁히지 않는다).
    rows["ci_low"] = np.fmin(ci_lo, p_te)
    rows["ci_high"] = np.fmax(ci_hi, p_te)
    rows["band"] = band_te
    rows = peer_stats(rows)
    rows["model"] = MODEL_NAME if primary == "base" else f"{MODEL_NAME}_{primary}"
    rows["calibrated"] = chosen != "raw"
    rows["event_12m"] = te_oof["y"].to_numpy()  # 검증용. 화면 스키마에는 넣지 않는다
    rows.to_parquet(out_dir / "risk_scores.parquet", index=False)
    log(f"risk_scores {len(rows):,}행 → {out_dir / 'risk_scores.parquet'}")

    meta["calibration_applied"] = chosen != "raw"  # 이름 유지 — feat/w2-serve의 read_detect_run이 이 키를 읽는다
    meta["calibration_candidate"] = chosen
    meta["calibration_windows"] = analysis["windows"]
    meta["band_cutoffs"] = cut
    meta["oof_predictions_sha256"] = sha256(oof_path)
    meta["oof_predictions_rows"] = len(oof_out)
    meta["seconds"] = round(time.time() - t0, 1)
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                                           encoding="utf-8")
    log(f"완료 ({meta['seconds']}초) → {out_dir}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tag", default="", help="출력 폴더 접미사 (예: t2)")
    ap.add_argument("--online", type=Path, default=None,
                    help="온라인 Enriched 테이블 (src.data.online_features 산출물). 주면 enriched feature set 평가")
    ap.add_argument("--feature-sets", default=",".join(features.FEATURE_SETS))
    ap.add_argument("--n-boot", type=int, default=20)
    ap.add_argument("--no-split-comparison", action="store_true")
    ap.add_argument("--primary", default="base", help="risk_scores를 만들 feature set (예: enriched)")
    ap.add_argument("--quick", action="store_true", help="base만, 부트스트랩 5회, 분할 비교 생략")
    a = ap.parse_args(argv)
    fsets = [a.primary] if a.quick else [s.strip() for s in a.feature_sets.split(",") if s.strip()]
    if a.primary not in fsets:
        fsets.append(a.primary)
    out = a.out or (config.REPO_ROOT / "outputs" / "models" / (MODEL_NAME + (f"_{a.tag}" if a.tag else "")))
    run(a.master, out, fsets, 5 if a.quick else a.n_boot, not (a.quick or a.no_split_comparison), a.online,
        a.primary)


if __name__ == "__main__":
    main()
