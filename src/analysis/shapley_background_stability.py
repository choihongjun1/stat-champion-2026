# -*- coding: utf-8 -*-
"""이슈 #34 리뷰 ① — 요인 Shapley 배경 표본 크기·구성의 안정성 **평가** (운영 배경을 고르지 않는다).

`diagnose.factor_shapley`의 가치함수는 배경 표본 평균이다. 배경이 작으면(기존 16개) 기여값·1순위 요인·온라인 부호가
표본에 따라 흔들린다. 실험 설계(2026-09-30):

- 평가 점포: 2025Q2 점포에서 seed 20260930으로 1,500곳
- 기준(정답) 배경: 학습 구간 무작위 1,024개 (seed 20261024) — 기준 자체의 시드 간 안정성(천장)은 재지 않았다
- 후보(각 시드 5개 20260931–35): 무작위 16/64/128/256, 층화(업종×자치구) 64/128/256, k-means 대표 64/128
- 지표: 기준 대비 1순위 요인 일치율, 온라인 요인 기여 부호 일치율, 기준값(base value) 범위, 1,000점포당 계산 시간
- 사전 기준: 5개 시드 중앙값이 1순위 ≥ 90%, 온라인 부호 ≥ 95% (`meets_criterion`으로 표에만 표시)

**이 모듈은 운영 배경을 고르지 않는다 (2026-10-01).** 실데이터에서 어느 설정도 사전 기준을 충족하지 못했고, 이전 버전이
결과를 본 뒤 만든 대체 규칙(두 중앙값의 최솟값 최대화)과 "첫 시드" 규칙으로 층화 256·시드 20260931(온라인 부호 일치 83.7%,
그 설정의 5시드 중 최저)을 운영 배경으로 저장했었다. 운영 배경은 Issue #49 S8(`src.models.background.S8_RULE`: 층화 256
두 개 seed 20260931·20261001, 방향·표시가 다르면 "해석 민감")로 정하고, 이 실험은 그 한계를 기록하는 근거로만 쓴다.

설정마다 결과를 바로 저장(체크포인트)한다 — 다시 실행하면 끝난 설정은 건너뛴다. 설계가 바뀌면(평가 점포 해시가
다르면) 멈춘다.

실행:
    python -m src.analysis.shapley_background_stability --online outputs/online/online_features.parquet
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
from src.models import background, detect, diagnose, features, splits, train_detect

ORIGIN = "2025Q2"
EVAL_N, EVAL_SEED = 1500, 20260930
REF_N, REF_SEED = 1024, 20261024
SEEDS = (20260931, 20260932, 20260933, 20260934, 20260935)
CANDIDATES = ([("random", n) for n in (16, 64, 128, 256)] + [("stratified", n) for n in (64, 128, 256)]
              + [("kmeans", n) for n in (64, 128)])
MIN_TOP1, MIN_SIGN = 0.90, 0.95
ROWS_PER_CHUNK = 150_000  # factor_shapley 한 번에 만드는 (점포×배경) 행 수 상한 — 메모리 보호, 결과와 무관
DEFAULT_RUN_META = config.REPO_ROOT / "outputs" / "models" / train_detect.MODEL_NAME / "run_meta.json"
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "diagnosis" / "background" / "experiment"


def sample_random(pool_idx: np.ndarray, n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).choice(pool_idx, n, replace=False)


def sample_stratified(pool_idx: np.ndarray, strata: pd.Series, n: int, seed: int) -> np.ndarray:
    """업종×자치구 층화 추출 — 운영 배경(S8)과 같은 알고리즘이라 `background.sample_stratified`를 그대로 쓴다."""
    return background.sample_stratified(pool_idx, strata, n, seed)


def sample_kmeans(X_pool: pd.DataFrame, pool_idx: np.ndarray, n: int, seed: int) -> np.ndarray:
    """수치형 컬럼(표준화)으로 k-means 중심을 구하고 각 중심에 가장 가까운 실제 학습 행을 배경으로 쓴다
    (범주형 dtype이 섞여 중심을 합성 행으로 쓸 수 없다). 시드는 초기화에만 쓴다."""
    from sklearn.cluster import KMeans

    num = X_pool.loc[pool_idx, [c for c in X_pool.columns if pd.api.types.is_numeric_dtype(X_pool[c])]]
    num = num.loc[:, num.notna().any(axis=0)]
    Z = num.fillna(num.median()).to_numpy(dtype=float)
    sd = Z.std(axis=0)
    sd[sd == 0] = 1.0
    Zs = (Z - Z.mean(axis=0)) / sd
    km = KMeans(n_clusters=n, random_state=seed, n_init=1).fit(Zs)
    chosen, used = [], set()
    for c in km.cluster_centers_:
        for o in np.argsort(((Zs - c) ** 2).sum(axis=1)):
            if o not in used:
                used.add(o)
                chosen.append(pool_idx[o])
                break
    return np.array(chosen)


def draw(method: str, n: int, seed: int, pool_idx, strata, X) -> np.ndarray:
    if method == "random":
        return sample_random(pool_idx, n, seed)
    if method == "stratified":
        return sample_stratified(pool_idx, strata, n, seed)
    return sample_kmeans(X, pool_idx, n, seed)


def top1_agreement(phi: np.ndarray, ref: np.ndarray) -> float:
    return float((phi.argmax(axis=1) == ref.argmax(axis=1)).mean())


def sign_agreement(phi_on: np.ndarray, ref_on: np.ndarray) -> float:
    return float((np.sign(phi_on) == np.sign(ref_on)).mean())


def summarize_candidates(table: pd.DataFrame, *, min_top1: float = MIN_TOP1, min_sign: float = MIN_SIGN) -> pd.DataFrame:
    """설정별 5시드 요약(중앙값·최솟값)과 사전 기준 충족 여부. **하나를 고르지 않는다** — 운영 배경은 고정 규칙
    (Issue #49 S8, `background.S8_RULE`)이다. table: 시드별 행(method, n_background, seed, top1, sign, seconds_per_1000, base_value)."""
    agg = table.groupby(["method", "n_background"]).agg(
        n_seeds=("seed", "count"),
        top1_median=("top1", "median"), top1_min=("top1", "min"),
        sign_median=("sign", "median"), sign_min=("sign", "min"),
        base_min=("base_value", "min"), base_max=("base_value", "max"),
        sec_per_1000_median=("seconds_per_1000", "median")).reset_index()
    agg["meets_criterion"] = (agg["top1_median"] >= min_top1) & (agg["sign_median"] >= min_sign)
    return agg


def _rel(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(config.REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return Path(p).as_posix()


def _idx_sha(df: pd.DataFrame, idx) -> str:
    keys = background.row_keys(df.iloc[np.asarray(idx)]["store_id"].astype(str), df.iloc[np.asarray(idx)]["origin"].astype(str))
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()


def serving_params(run_meta_path: Path | None = None) -> tuple[dict, str]:
    """배경 실험에 쓰는 모형 파라미터 = 서빙 모형의 파라미터. run_meta.json의 params를 읽고,
    파일이 없거나 params가 없으면 detect.ADOPTED_PARAMS를 쓴다. (파라미터, params_name) 반환.
    #51 이전 run_meta에는 params_name이 없다 — 이름을 숨기지 않고 값으로 구분한다:
    DEFAULT_PARAMS와 같으면 "run_meta:legacy_default", 아니면 "run_meta:legacy_unnamed"."""
    if run_meta_path is not None and Path(run_meta_path).exists():
        meta = json.loads(Path(run_meta_path).read_text(encoding="utf-8"))
        if meta.get("params"):
            return dict(meta["params"]), f"run_meta:{train_detect.run_meta_params_name(meta)}"
    return dict(detect.ADOPTED_PARAMS), "adopted"


def setup(master_path: Path, online_path: Path | None, primary: str = "enriched", params: dict | None = None):
    df = train_detect.load_master(master_path)
    if online_path is not None:
        df = train_detect.attach_online(df, online_path)
    cols = features.select_features(df.columns, primary)
    diagnose.check_mapping(cols)
    X = features.build_X(df, cols)
    y = df["event_12m"].to_numpy().astype(int)
    origins = splits.sorted_origins(df)
    t = origins.index(ORIGIN)
    tr = df["origin"].isin(origins[: t - train_detect.EMBARGO]).to_numpy()
    te_all = np.flatnonzero((df["origin"] == ORIGIN).to_numpy())
    eval_idx = np.sort(np.random.default_rng(EVAL_SEED).choice(te_all, min(EVAL_N, len(te_all)), replace=False))
    model = detect.DetectModel(params=dict(params if params is not None else detect.ADOPTED_PARAMS)).fit(X[tr], y[tr])
    active = [f for f in diagnose.FACTORS if any(c in model.columns_ for c in f["features"])]
    factor_cols = [[c for c in f["features"] if c in model.columns_] for f in active]
    on = next(k for k, f in enumerate(active) if f["id"] == "online_attention")
    return dict(df=df, X=X, model=model, pool_idx=np.flatnonzero(tr), eval_idx=eval_idx, factor_cols=factor_cols,
                on=on, factor_ids=[f["id"] for f in active],
                strata=df["biz_type"].astype(str) + "×" + df["gu"].astype(str))


def _shapley(s, bg_idx) -> tuple[np.ndarray, float, float]:
    K = len(bg_idx)
    t0 = time.time()
    phi, base = diagnose.factor_shapley(s["model"], s["X"].iloc[s["eval_idx"]], s["X"].iloc[bg_idx], s["factor_cols"],
                                        chunk=max(1, ROWS_PER_CHUNK // K))
    return phi, base, time.time() - t0


def run(master_path: Path, online_path: Path | None, out_dir: Path, run_meta_path: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    params, params_name = serving_params(run_meta_path)
    s = setup(master_path, online_path, params=params)
    design = {"origin": ORIGIN, "eval_n": int(len(s["eval_idx"])), "eval_seed": EVAL_SEED,
              "eval_idx_sha256": _idx_sha(s["df"], s["eval_idx"]), "ref_n": REF_N, "ref_seed": REF_SEED,
              "seeds": list(SEEDS), "candidates": [f"{m}:{n}" for m, n in CANDIDATES],
              "criterion": f"median top1 >= {MIN_TOP1} and median online sign >= {MIN_SIGN} (evaluation only, no selection)",
              "factor_ids": s["factor_ids"], "params": params, "params_name": params_name}
    dpath = out_dir / "design.json"
    if dpath.exists():
        old = json.loads(dpath.read_text(encoding="utf-8"))
        if {k: old.get(k) for k in design} != json.loads(json.dumps(design)):
            raise RuntimeError(f"저장된 실험 설계와 다르다 — {dpath}를 옮기고 새로 시작한다")
    else:
        dpath.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")

    ref_path = out_dir / "reference_phi.npy"
    if ref_path.exists():
        ref = np.load(ref_path)
        train_detect.log(f"기준 배경 체크포인트 사용 ({ref_path.name})")
    else:
        ref_bg = sample_random(s["pool_idx"], REF_N, REF_SEED)
        ref, ref_base, sec = _shapley(s, ref_bg)
        np.save(ref_path, ref)
        (out_dir / "reference.json").write_text(json.dumps({"base_value": ref_base, "seconds": sec,
                                                            "bg_sha256": _idx_sha(s["df"], ref_bg)}), encoding="utf-8")
        train_detect.log(f"기준 배경 {REF_N}개 완료 ({sec:.0f}초)")

    res_path = out_dir / "results.jsonl"
    done = set()
    if res_path.exists():
        done = {(r["method"], r["n_background"], r["seed"])
                for r in map(json.loads, res_path.read_text(encoding="utf-8").splitlines()) if r}
    for method, n in CANDIDATES:
        for seed in SEEDS:
            if (method, n, seed) in done:
                continue
            bg = draw(method, n, seed, s["pool_idx"], s["strata"], s["X"])
            phi, base, sec = _shapley(s, bg)
            np.save(out_dir / f"bg_{method}_{n}_{seed}.npy", bg)
            row = {"method": method, "n_background": n, "seed": seed, "base_value": base,
                   "top1": top1_agreement(phi, ref), "sign": sign_agreement(phi[:, s["on"]], ref[:, s["on"]]),
                   "seconds": sec, "seconds_per_1000": sec / len(s["eval_idx"]) * 1000}
            with open(res_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            train_detect.log(f"[{method} {n} seed {seed}] top1 {row['top1']:.3f} sign {row['sign']:.3f} "
                             f"base {base:.4f} {row['seconds_per_1000']:.0f}s/1000")

    table = pd.DataFrame(map(json.loads, res_path.read_text(encoding="utf-8").splitlines()))
    summary = summarize_candidates(table)
    summary.to_csv(out_dir / "summary.csv", index=False)
    train_detect.log(f"요약 → {out_dir / 'summary.csv'} (사전 기준 충족 {int(summary['meets_criterion'].sum())}개 설정) — "
                     "운영 배경은 이 결과로 고르지 않는다 (`python -m src.models.background`)")
    return {"summary": summary}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#34 Shapley 배경 안정성 평가 (운영 배경은 고르지 않음)")
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--online", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--run-meta", type=Path, default=DEFAULT_RUN_META,
                    help="서빙 모형의 run_meta.json (없으면 detect.ADOPTED_PARAMS)")
    a = ap.parse_args(argv)
    run(a.master, a.online, a.out, a.run_meta)


if __name__ == "__main__":
    main()
