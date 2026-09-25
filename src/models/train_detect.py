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
def split_comparison(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, params=None) -> pd.DataFrame:
    rows = []
    for emb in (EMBARGO, 0):
        f = splits.rolling_origin_folds(df, min_train_origins=MIN_TRAIN_ORIGINS, embargo=emb, test_size=1)[-1]
        tr, te = f.masks(df)
        p = detect.fit_predict(X[tr], y[tr], X[te], params)
        rows.append({"setting": f"time_split(embargo={emb})", "test": f.test_origins[0],
                     "train": f"{f.train_origins[0]}~{f.train_origins[-1]}", "n_train": int(tr.sum()),
                     **detect.ranking_metrics(y[te], p)})
    for name, (tr, te) in (("random_split(금지·대조군)", splits.random_split_baseline(df)),
                           ("store_holdout(민감도)", splits.store_holdout_split(df))):
        p = detect.fit_predict(X[tr], y[tr], X[te], params)
        rows.append({"setting": name, "test": "mixed", "train": "mixed", "n_train": int(tr.sum()),
                     **detect.ranking_metrics(y[te], p)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 보정 · 구간 · 등급 · risk_scores
# ---------------------------------------------------------------------------
def calibration_step(oof: pd.DataFrame, origins: list[str], test_origins: list[str]):
    """첫 검증 origin 기준 학습 가능 구간(≤ t−EMBARGO−1)에 속한 OOF 예측으로 isotonic을 적합한다.
    그 라벨은 검증 시점에 확정돼 있다."""
    first_test = origins.index(test_origins[0])
    calib_origins = [o for o in oof["origin"].unique() if origins.index(o) < first_test - EMBARGO]
    if len(calib_origins) < 4:
        log(f"경고: 보정 origin이 {len(calib_origins)}개뿐이다 (권장 4개 이상, base rate 변동 흡수)")
    cal = oof[oof["origin"].isin(calib_origins)]
    iso = calibration.IsotonicCalibrator().fit(cal["p_oof"], cal["y"])
    te = oof[oof["origin"].isin(test_origins)]
    p_raw, p_cal = te["p_oof"].to_numpy(), iso.predict(te["p_oof"])
    rep = calibration.calibration_report(te["y"].to_numpy(), p_raw, p_cal)
    apply = bool(rep.loc[rep["version"] == "calibrated", "ece"].iloc[0]
                 < rep.loc[rep["version"] == "raw", "ece"].iloc[0])
    rep["calib_origins"] = f"{min(calib_origins)}~{max(calib_origins)} ({len(calib_origins)}개)"
    rep["applied"] = apply
    return iso, apply, calib_origins, rep


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
        with_split_comparison: bool) -> None:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_master(master_path)
    y = df["event_12m"].to_numpy().astype(int)
    origins = splits.sorted_origins(df)
    test_origins = origins[-TEST_SIZE:]
    log(f"master {len(df):,}행 / 점포 {df['store_id'].nunique():,} / origin {origins[0]}~{origins[-1]}")

    meta = {"master": str(master_path), "master_sha256": sha256(master_path), "embargo": EMBARGO,
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
        if fs == "base":
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
        log("base feature set이 없어 보정·등급·risk_scores를 건너뛴다")
        return

    if with_split_comparison:
        log("분할 방식 비교 (embargo 4/0, random, 점포 홀드아웃)")
        split_comparison(df, X_base, y).to_csv(out_dir / "split_comparison.csv", index=False)
    log(f"변수 기여 진단 (permutation, {origins[-1]})")
    imp = permutation_importance(df, X_base, y, origins)
    imp.to_csv(out_dir / "feature_importance.csv", index=False)
    log("\n" + imp.head(12)[["feature", "auc_drop_mean", "auc_drop_std", "missing_rate"]].to_string(index=False))

    # --- 보정
    iso, apply, calib_origins, rep = calibration_step(oof_base, origins, test_origins)
    rep.to_csv(out_dir / "calibration_report.csv", index=False)
    log(f"보정: {rep['calib_origins'].iloc[0]} → 적용 {apply}\n{rep.to_string(index=False)}")
    te_oof = oof_base[oof_base["origin"].isin(test_origins)]
    p_te_raw = te_oof["p_oof"].to_numpy()
    p_te = iso.predict(p_te_raw) if apply else p_te_raw
    calibration.plot_reliability(
        {"raw": calibration.reliability_table(te_oof["y"], p_te_raw),
         "isotonic": calibration.reliability_table(te_oof["y"], iso.predict(p_te_raw))},
        out_dir / "reliability.png", title=f"Reliability — {test_origins[0]}~{test_origins[-1]}",
    )

    # --- band 컷오프: 보정 구간 OOF로 정하고 검증 구간에 적용
    cal = oof_base[oof_base["origin"].isin(calib_origins)]
    p_cal_set = iso.predict(cal["p_oof"]) if apply else cal["p_oof"].to_numpy()
    cut = bands.suggest_cutoffs(cal["y"].to_numpy(), p_cal_set)
    pd.DataFrame([cut]).to_csv(out_dir / "band_cutoffs.csv", index=False)
    band_te = bands.assign_bands_absolute(p_te, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
    bands.band_profile(te_oof["y"].to_numpy(), p_te, band_te).to_csv(out_dir / "band_profile.csv", index=False)
    p_all = iso.predict(oof_base["p_oof"]) if apply else oof_base["p_oof"].to_numpy()
    b_all = bands.assign_bands_absolute(p_all, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
    share = pd.crosstab(oof_base["origin"], b_all, normalize="index").reindex(columns=list(bands.BANDS), fill_value=0)
    share.to_csv(out_dir / "band_share_by_origin.csv")
    log(f"band 컷오프 mid {cut['cut_mid']:.4f} / high {cut['cut_high']:.4f} (base {cut['base_rate']:.4f})")

    # --- 불확실성 구간
    ci_lo = np.full(len(te_oof), np.nan)
    ci_hi = np.full(len(te_oof), np.nan)
    if n_boot > 0:
        pos_in_te = pd.Series(np.arange(len(te_oof)), index=te_oof["idx"].to_numpy())
        for o in test_origins:
            log(f"부트스트랩 {n_boot}회 ({o})")
            te_mask, lo, hi = bootstrap_ci(df, X_base, y, o, origins, n_boot=n_boot)
            if apply:
                lo, hi = iso.predict(lo), iso.predict(hi)
            at = pos_in_te.loc[np.flatnonzero(te_mask)].to_numpy()
            ci_lo[at], ci_hi[at] = lo, hi

    # --- risk_scores
    rows = df.loc[te_oof["idx"].to_numpy(), ["store_id", "origin", "gu", "biz_type"]].reset_index(drop=True)
    rows["probability_12m"] = p_te
    # 점추정(보정된 OOF)과 부트스트랩 백분위는 계산 경로가 달라 점추정이 구간 밖에 놓일 수 있다.
    # 화면 일관성을 위해 구간이 점추정을 포함하도록 넓힌다 (좁히지 않는다).
    rows["ci_low"] = np.fmin(ci_lo, p_te)
    rows["ci_high"] = np.fmax(ci_hi, p_te)
    rows["band"] = band_te
    rows = peer_stats(rows)
    rows["model"] = MODEL_NAME
    rows["calibrated"] = apply
    rows["event_12m"] = te_oof["y"].to_numpy()  # 검증용. 화면 스키마에는 넣지 않는다
    rows.to_parquet(out_dir / "risk_scores.parquet", index=False)
    log(f"risk_scores {len(rows):,}행 → {out_dir / 'risk_scores.parquet'}")

    meta["calibration_applied"] = apply
    meta["calib_origins"] = calib_origins
    meta["band_cutoffs"] = cut
    meta["seconds"] = round(time.time() - t0, 1)
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                                           encoding="utf-8")
    log(f"완료 ({meta['seconds']}초) → {out_dir}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tag", default="", help="출력 폴더 접미사 (예: t2)")
    ap.add_argument("--feature-sets", default=",".join(features.FEATURE_SETS))
    ap.add_argument("--n-boot", type=int, default=20)
    ap.add_argument("--no-split-comparison", action="store_true")
    ap.add_argument("--quick", action="store_true", help="base만, 부트스트랩 5회, 분할 비교 생략")
    a = ap.parse_args(argv)
    fsets = ["base"] if a.quick else [s.strip() for s in a.feature_sets.split(",") if s.strip()]
    out = a.out or (config.REPO_ROOT / "outputs" / "models" / (MODEL_NAME + (f"_{a.tag}" if a.tag else "")))
    run(a.master, out, fsets, 5 if a.quick else a.n_boot, not (a.quick or a.no_split_comparison))


if __name__ == "__main__":
    main()
