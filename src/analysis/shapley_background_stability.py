# -*- coding: utf-8 -*-
"""이슈 #34 리뷰 — 요인 Shapley 배경 표본 크기·구성의 안정성.

`diagnose.factor_shapley`의 가치함수는 배경 표본 평균 f(x_S, b_{-S})다. 배경이 작으면(현재 기본 16개)
그 평균이 표본에 따라 흔들려 기여값·1순위 요인이 바뀔 수 있다. 이 모듈은:
  1. 배경 크기 16/64/128/256 × 시드 5개(무작위 추출)를 비교한다.
  2. 대표 배경 한 가지(업종×자치구 층화 추출)도 같은 크기로 비교한다.
  3. 기준값(base_value)의 범위, 256-배경 대비 1순위 요인 일치율, 온라인 요인 기여 부호 일치율,
     1,000점포당 계산 시간을 표로 낸다.
  4. 일치율 90% 이상이면서 계산 시간이 허용되는 설정을 권장하고, 그 배경의 (store_id, origin) 인덱스를
     저장한다 — #36 서빙이 같은 배경을 재현할 수 있게.

기준(reference) = 무작위 256-배경 5개 시드의 기여 평균(시드 하나보다 덜 흔들린다).

실행:
    python -m src.analysis.shapley_background_stability --online outputs/online/online_features.parquet \
        --primary enriched --max-stores 300
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config
from src.models import detect, diagnose, features, splits, train_detect

BG_SIZES = (16, 64, 128, 256)
SEEDS = (20260930, 20260931, 20260932, 20260933, 20260934)
REFERENCE_SIZE = 256
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "w2" / "shapley_background_stability"


def sample_random(pool_idx: np.ndarray, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.choice(pool_idx, n, replace=False)


def sample_stratified(pool_idx: np.ndarray, strata: pd.Series, n: int, seed: int) -> np.ndarray:
    """업종×자치구 층화 추출 — 각 층에서 크기 비례로 뽑고(최소 1), 남는/부족한 만큼 무작위로 채운다."""
    rng = np.random.default_rng(seed)
    s = strata.loc[pool_idx]
    counts = s.value_counts()
    quota = np.maximum(1, np.round(counts / counts.sum() * n)).astype(int)
    picked = []
    for key, q in quota.items():
        cand = np.asarray(pool_idx)[(s.values == key)]
        picked.append(rng.choice(cand, min(q, len(cand)), replace=False))
    out = np.concatenate(picked)
    out = np.unique(out)
    if len(out) > n:
        out = rng.choice(out, n, replace=False)
    elif len(out) < n:
        rest = np.setdiff1d(pool_idx, out)
        out = np.concatenate([out, rng.choice(rest, n - len(out), replace=False)])
    return out


def sample_kmeans(X_pool: pd.DataFrame, pool_idx: np.ndarray, n: int, seed: int) -> np.ndarray:
    """수치형 컬럼만으로 k-means 중심을 구하고, 각 중심에 가장 가까운 실제 학습 행을 배경으로 쓴다
    (모형 입력은 범주형 dtype이 섞여 있어 중심 자체를 합성 행으로 쓸 수 없다)."""
    from sklearn.cluster import KMeans

    num = X_pool.loc[pool_idx, [c for c in X_pool.columns if pd.api.types.is_numeric_dtype(X_pool[c])]]
    num = num.loc[:, num.notna().any(axis=0)]  # 표본 전체가 NA인 컬럼(예: 이 구간엔 없는 land_price)은 제외
    Z = num.fillna(num.median()).to_numpy()
    mu, sd = Z.mean(axis=0), Z.std(axis=0)
    sd[sd == 0] = 1.0
    Zs = (Z - mu) / sd
    km = KMeans(n_clusters=n, random_state=seed, n_init=4).fit(Zs)
    chosen, used = [], set()
    for c in km.cluster_centers_:
        d = ((Zs - c) ** 2).sum(axis=1)
        order = np.argsort(d)
        for o in order:
            if o not in used:
                used.add(o)
                chosen.append(pool_idx[o])
                break
    return np.array(chosen)


def _top1_ids(phi: np.ndarray, factor_ids: list[str]) -> np.ndarray:
    return np.array(factor_ids)[phi.argmax(axis=1)]


def run_one(model, Xt, background_idx, X_pool, factor_cols, factor_ids, on_idx) -> dict:
    t0 = time.time()
    phi, base = diagnose.factor_shapley(model, Xt, X_pool.loc[background_idx], factor_cols)
    elapsed = time.time() - t0
    return {"phi": phi, "base": base, "elapsed": elapsed,
            "top1": _top1_ids(phi, factor_ids),
            "online_sign": np.sign(phi[:, on_idx]) if on_idx is not None else None}


def study(df: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, origins: list[str], *,
         primary: str, max_stores: int, seed: int = 20260929) -> dict:
    cols = features.select_features(df.columns, primary)
    diagnose.check_mapping(cols)
    active = [f for f in diagnose.FACTORS if any(c in cols for c in f["features"])]
    factor_cols = [[c for c in f["features"] if c in cols] for f in active]
    factor_ids = [f["id"] for f in active]
    on_idx = next((k for k, f in enumerate(active) if f["id"] == "online_attention"), None)

    origin = origins[-1]
    t = origins.index(origin)
    tr = df["origin"].isin(origins[: t - train_detect.EMBARGO]).to_numpy()
    te_idx = np.flatnonzero((df["origin"] == origin).to_numpy())
    rng = np.random.default_rng(seed)
    if len(te_idx) > max_stores:
        te_idx = np.sort(rng.choice(te_idx, max_stores, replace=False))
    train_detect.log(f"모형 학습 (배경 안정성 연구용, {origin} {len(te_idx):,}점포)")
    model = detect.DetectModel().fit(X[tr], y[tr])
    Xt = X.iloc[te_idx]
    pool_idx = np.flatnonzero(tr)
    X_pool = X

    strata = (df["biz_type"].astype(str) + "×" + df["gu"].astype(str))

    rows, backgrounds = [], {}
    ref_phis = []
    for seed_i in SEEDS:
        bg = sample_random(pool_idx, REFERENCE_SIZE, seed_i)
        r = run_one(model, Xt, bg, X_pool, factor_cols, factor_ids, on_idx)
        ref_phis.append(r["phi"])
    ref_phi = np.mean(ref_phis, axis=0)
    ref_top1 = _top1_ids(ref_phi, factor_ids)
    ref_sign = np.sign(ref_phi[:, on_idx]) if on_idx is not None else None

    configs = [("random", n, s) for n in BG_SIZES for s in SEEDS]
    configs += [("stratified", n, s) for n in BG_SIZES for s in SEEDS]
    configs += [("kmeans", n, SEEDS[0]) for n in BG_SIZES]  # k-means는 시드 영향이 작아 1개만

    for method, n, s in configs:
        if method == "random":
            bg = sample_random(pool_idx, n, s)
        elif method == "stratified":
            bg = sample_stratified(pool_idx, strata, n, s)
        else:
            bg = sample_kmeans(X_pool, pool_idx, n, s)
        r = run_one(model, Xt, bg, X_pool, factor_cols, factor_ids, on_idx)
        top1_agree = float((r["top1"] == ref_top1).mean())
        sign_agree = float((r["online_sign"] == ref_sign).mean()) if on_idx is not None else float("nan")
        per_1000 = r["elapsed"] / len(Xt) * 1000
        rows.append({"method": method, "n_background": n, "seed": s, "base_value": r["base"],
                    "top1_agreement_vs_256": top1_agree, "online_sign_agreement_vs_256": sign_agree,
                    "seconds_per_1000_stores": per_1000, "n_stores": len(Xt)})
        backgrounds[(method, n, s)] = bg
        train_detect.log(f"[{method} n={n} seed={s}] base={r['base']:.4f} top1일치 {top1_agree:.3f} "
                         f"온라인부호일치 {sign_agree:.3f} {per_1000:.1f}초/1000점포")

    table = pd.DataFrame(rows)
    return {"table": table, "backgrounds": backgrounds, "df": df, "pool_idx": pool_idx,
            "te_idx": te_idx, "origin": origin}


def recommend(table: pd.DataFrame, *, min_agree: float = 0.90) -> dict:
    """일치율(top1·온라인 부호 평균) 90% 이상이면서 계산 시간이 가장 짧은 설정. 시드는 평균으로 묶는다."""
    agg = table.groupby(["method", "n_background"]).agg(
        top1_agreement_vs_256=("top1_agreement_vs_256", "mean"),
        online_sign_agreement_vs_256=("online_sign_agreement_vs_256", "mean"),
        seconds_per_1000_stores=("seconds_per_1000_stores", "mean"),
        base_value_min=("base_value", "min"), base_value_max=("base_value", "max"),
    ).reset_index()
    agg["min_agreement"] = agg[["top1_agreement_vs_256", "online_sign_agreement_vs_256"]].min(axis=1)
    ok = agg[agg["min_agreement"] >= min_agree].sort_values("seconds_per_1000_stores")
    chosen = ok.iloc[0] if len(ok) else agg.sort_values("min_agreement", ascending=False).iloc[0]
    return {"table": agg, "chosen": chosen.to_dict(), "met_threshold": len(ok) > 0}


def save_background_index(df: pd.DataFrame, background_idx: np.ndarray, out_path: Path) -> str:
    """선택된 배경의 store_id×origin과 그 목록의 sha256을 저장한다 — #36이 같은 배경을 재현하는 열쇠."""
    import hashlib

    idx_df = df.iloc[background_idx][["store_id", "origin"]].reset_index(drop=True)
    idx_df.to_csv(out_path, index=False)
    h = hashlib.sha256(idx_df.to_csv(index=False).encode("utf-8")).hexdigest()
    return h


def run(master_path: Path, online_path: Path | None, primary: str, out_dir: Path, max_stores: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = train_detect.load_master(master_path)
    if online_path is not None:
        df = train_detect.attach_online(df, online_path)
    cols = features.select_features(df.columns, primary)
    X = features.build_X(df, cols)
    y = df["event_12m"].to_numpy().astype(int)
    origins = splits.sorted_origins(df)

    res = study(df, X, y, origins, primary=primary, max_stores=max_stores)
    res["table"].to_csv(out_dir / "background_stability_raw.csv", index=False)
    rec = recommend(res["table"])
    rec["table"].to_csv(out_dir / "background_stability_summary.csv", index=False)

    key = (rec["chosen"]["method"], int(rec["chosen"]["n_background"]), SEEDS[0])
    bg_idx = res["backgrounds"][key]
    sha = save_background_index(df, bg_idx, out_dir / "chosen_background.csv")

    meta = {"origin": res["origin"], "max_stores": max_stores, "chosen": rec["chosen"],
           "met_90pct_threshold": rec["met_threshold"], "chosen_background_sha256": sha,
           "chosen_background_n": len(bg_idx)}
    (out_dir / "recommendation.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                                                 encoding="utf-8")
    train_detect.log(f"권장: {rec['chosen']['method']} n={int(rec['chosen']['n_background'])} "
                     f"(일치율 90% 충족: {rec['met_threshold']}) → {out_dir}")
    return meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#34 Shapley 배경 표본 안정성 연구")
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--online", type=Path, default=None)
    ap.add_argument("--primary", default="enriched")
    ap.add_argument("--max-stores", type=int, default=300)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    run(a.master, a.online, a.primary, a.out, a.max_stores)


if __name__ == "__main__":
    main()
