# -*- coding: utf-8 -*-
"""서빙 — 라벨 없는 예측용 패널(현재 영업 중 점포)에 위험도·구간·등급·진단을 한 번에 만든다.

검증과 같은 규칙으로 학습한다
- score origin s의 학습 구간 = 라벨이 확정된 origin 중 s−5분기 이하 (rolling OOF·W2-3 진단과 같은 embargo 규칙).
  그래서 검증 구간의 마지막 origin을 score로 넣으면 `train_detect`의 risk_scores와 확률이 정확히 같다 (테스트).
- 보정 적용 여부·보정기·등급 컷오프는 `train_detect` 실행 결과(`--detect-dir`의 run_meta.json,
  calibrator.pkl, band_cutoffs.csv)를 따른다. 검증에서 정한 기준을 그대로 쓰기 위해서다.
  보정이 적용되면 요인 기여도(보정 전 척도)의 합은 화면 확률과 달라진다 — serve_meta에 기록.
  현재 실데이터 실행(base·enriched)은 보정 미적용이다.
- 검증 구간의 학습에 한 번도 값이 없던 feature(land_price)는 score origin이 뒤로 가도 넣지 않는다.
- 진단은 `diagnose.explain` — W2-3과 같은 요인 매핑·Shapley·peer 비교·표시 보류 규칙.

입력
- `--master`: 라벨 있는 master_base (학습용)
- `--score` : 예측용 패널 (master_base와 같은 predictor, `event_12m` 없음, origin 하나)
- `--online` / `--online-score`: 온라인 Enriched 테이블 (enriched일 때). 예측용은
  `python -m src.data.online_features --panel <score parquet> --out <online_score parquet>`로 만든다.
- `--licenses`: 인허가 표준화 테이블 (기본 `outputs/standardized/licenses_3gu.parquet`, 없으면 건너뜀).
  store_id로 1:1 조인해 reports.jsonl의 store 블록에 사업장명·주소를 붙인다 (모형 입력에는 쓰지 않는다).

출력 (`outputs/serve/<origin>_<feature set>/`)
- `risk_scores.parquet`, `diagnosis.parquet`, `diagnosis_by_category.parquet`
- `reports.jsonl` — 점포당 1줄, W2-5 결과 스키마의 risk + factors 블록
- `serve_meta.json` — 입력 sha256, 학습 구간, 컷오프, 소요 시간

실행:
    python -m src.models.serve --score outputs/master/master_score.parquet --primary enriched \\
        --online outputs/online/online_features.parquet --online-score outputs/online/online_features_score.parquet
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config
from src.models import bands, detect, diagnose, features, train_detect, uncertainty

SCHEMA_VERSION = "0.1"
DISCLAIMER = "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."
INTERVAL_NOTE = "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지의 범위이며, 폐업 확률 자체의 범위가 아닙니다."
DEFAULT_LICENSES = config.REPO_ROOT / "outputs" / "standardized" / "licenses_3gu.parquet"
# store 블록 필드 ← 인허가 표준화 컬럼 (사업장명·주소는 원문 그대로)
STORE_META_COLS = {"name": "name_raw", "road_address": "road_addr_raw", "address": "addr_raw", "dong": "dong"}


def store_meta(store_ids, licenses_path: Path) -> dict[str, dict]:
    """store_id → {name, road_address, address, dong}. 인허가 테이블과 1:1 조인, 없는 점포는 값 None."""
    lic = pd.read_parquet(licenses_path, columns=["store_id", *STORE_META_COLS.values()])
    dup = lic["store_id"].duplicated()
    if dup.any():
        raise ValueError(f"인허가 테이블 store_id 중복 {int(dup.sum())}건 — 1:1 조인 불가: {licenses_path}")
    sub = lic.set_index("store_id").reindex(list(store_ids))
    return {sid: {k: (None if pd.isna(row[c]) else str(row[c])) for k, c in STORE_META_COLS.items()}
            for sid, row in sub.iterrows()}


def load_score_panel(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "event_12m" in df.columns:  # 검증용으로 라벨 있는 origin을 넣은 경우. 서빙 입력에는 쓰지 않는다
        train_detect.log("예측용 패널의 event_12m 컬럼을 버린다")
        df = df.drop(columns="event_12m")
    origins = df["origin"].astype(str).unique()
    if len(origins) != 1:
        raise ValueError(f"예측용 패널에는 origin이 하나여야 한다: {list(origins)}")
    if df["store_id"].duplicated().any():
        raise ValueError("예측용 패널 store_id 중복")
    return df.reset_index(drop=True)


def training_mask(labeled: pd.DataFrame, score_origin: str) -> np.ndarray:
    """score origin s에 대해 학습에 쓸 수 있는 라벨 행 (origin ≤ s − (EMBARGO+1))."""
    cutoff = pd.Period(score_origin, freq="Q") - (train_detect.EMBARGO + 1)
    return np.asarray(pd.PeriodIndex(labeled["origin"].astype(str), freq="Q") <= cutoff)


def unvalidated_features(labeled: pd.DataFrame, cols: list[str], last_test_origin: str) -> list[str]:
    """검증 구간 마지막 origin의 학습 구간에서 값이 전부 NA였던 feature — 검증을 한 번도 거치지 않았다.

    score origin이 뒤로 가면 학습 구간이 넓어져 이런 feature(예: 2024Q2~에만 값이 있는 land_price)가
    서빙 모형에만 들어갈 수 있다. DECISIONS(2026-09-25 W2-2)에 따라 검증과 같은 feature로만 학습한다.
    """
    tr = training_mask(labeled, last_test_origin)
    sub = labeled.loc[tr, cols]
    return [c for c in cols if sub[c].isna().all()]


def read_detect_run(detect_dir: Path) -> tuple[dict, dict, object | None]:
    """탐지 실행의 메타·등급 컷오프·보정기(적용된 경우만)."""
    meta = json.loads((detect_dir / "run_meta.json").read_text(encoding="utf-8"))
    cut = pd.read_csv(detect_dir / "band_cutoffs.csv").iloc[0].to_dict()
    iso = None
    if meta.get("calibration_applied"):
        path = detect_dir / "calibrator.pkl"
        if not path.exists():
            raise FileNotFoundError(f"보정이 적용된 실행인데 {path}가 없다 — train_detect를 다시 돌린다")
        with open(path, "rb") as f:
            iso = pickle.load(f)
    return meta, cut, iso


def run(master_path: Path, score_path: Path, detect_dir: Path, out_dir: Path, *, primary: str,
        online_path: Path | None = None, online_score_path: Path | None = None,
        licenses_path: Path | None = None,
        n_boot: int = 20, n_background: int = 16, seed: int = 20260925) -> pd.DataFrame:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    if licenses_path is not None and not Path(licenses_path).exists():
        raise FileNotFoundError(f"인허가 테이블이 없다: {licenses_path}")
    run_meta, cut, iso = read_detect_run(detect_dir)
    if run_meta.get("primary_feature_set", "base") != primary:
        raise ValueError(f"탐지 실행의 feature set({run_meta.get('primary_feature_set')})과 서빙({primary})이 다르다")

    lab = train_detect.load_master(master_path)
    sc = load_score_panel(score_path)
    if online_path is not None:
        lab = train_detect.attach_online(lab, online_path)
    if online_score_path is not None:
        sc = train_detect.attach_online(sc, online_score_path)
    s = str(sc["origin"].iloc[0])

    cols = features.select_features(lab.columns, primary)
    missing = [c for c in cols if c not in sc.columns]
    if missing:
        raise ValueError(f"예측용 패널에 학습 feature가 없다: {missing}")
    diagnose.check_mapping(cols)
    last_test = (run_meta.get("test_origins") or [sorted(lab["origin"].astype(str).unique())[-1]])[-1]
    excluded = unvalidated_features(lab, cols, last_test)
    cols = [c for c in cols if c not in excluded]
    if excluded:
        train_detect.log(f"검증되지 않은 feature 제외 (검증 마지막 origin {last_test}의 학습 구간에 값 없음): {excluded}")

    tr = training_mask(lab, s)
    if not tr.any():
        raise ValueError(f"{s}에 대해 학습 가능한 라벨 origin이 없다")
    train_origins = sorted(lab.loc[tr, "origin"].unique())
    cats = features.fit_categories(lab, cols)
    Xtr = features.build_X(lab.loc[tr], cols, categories=cats)
    ytr = lab.loc[tr, "event_12m"].to_numpy().astype(int)
    Xs = features.build_X(sc, cols, categories=cats)
    train_detect.log(f"score {s} · {len(sc):,}점포 · 학습 {train_origins[0]}~{train_origins[-1]} "
                     f"({int(tr.sum()):,}행) · feature set {primary}")

    model = detect.DetectModel().fit(Xtr, ytr)
    p_raw = model.predict_proba(Xs)
    p = iso.predict(p_raw) if iso is not None else p_raw

    lo = hi = np.full(len(sc), np.nan)
    if n_boot > 0:
        train_detect.log(f"부트스트랩 {n_boot}회 (점포 단위)")
        lo, hi, _ = uncertainty.bootstrap_interval(
            lambda a, b, c: detect.fit_predict(a, b, c), Xtr, ytr, Xs, n_boot=n_boot, alpha=0.10,
            group=lab.loc[tr, "store_id"].to_numpy())
        if iso is not None:
            lo, hi = iso.predict(lo), iso.predict(hi)

    risk = sc[["store_id", "origin", "gu", "biz_type"]].copy()
    risk["probability_12m"] = p
    risk["ci_low"], risk["ci_high"] = np.fmin(lo, p), np.fmax(hi, p)
    risk["band"] = bands.assign_bands_absolute(p, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"])
    risk = train_detect.peer_stats(risk)
    model_name = train_detect.MODEL_NAME if primary == "base" else f"{train_detect.MODEL_NAME}_{primary}"
    risk["model"], risk["calibrated"] = model_name, iso is not None
    risk.to_parquet(out_dir / "risk_scores.parquet", index=False)

    rng = np.random.default_rng(seed)
    bg = rng.choice(len(Xtr), n_background, replace=False)
    meta = sc[["store_id", "origin", "biz_type", "gu", "age_months"]]
    res = diagnose.explain(model, Xs, Xtr.iloc[bg], meta, sc)
    long = res["long"]
    long.to_parquet(out_dir / "diagnosis.parquet", index=False)
    res["by_category"].to_parquet(out_dir / "diagnosis_by_category.parquet", index=False)
    # 요인 기여도는 보정 전 확률 척도에서 정확히 합산된다 (W2-3). 보정이 적용되면 화면 확률과 합이 달라진다.
    if np.abs(res["meta"]["probability_12m"].to_numpy() - p_raw).max() > 1e-12:
        raise RuntimeError("진단 확률과 모형 확률이 다르다")

    unavailable = [c for c in diagnose.CATEGORIES if not any(f["category"] == c for f in res["active"])]
    as_of = str(pd.Period(s, freq="Q").end_time.date())
    by_store = dict(tuple(long.groupby("store_id", sort=False)))
    names = store_meta(risk["store_id"], licenses_path) if licenses_path is not None else {}
    n_no_name = sum(v["name"] is None for v in names.values()) if names else None
    if names:
        train_detect.log(f"가게 메타 결합: {len(names) - n_no_name:,} / {len(names):,}점포 (이름 없음 {n_no_name:,})")
    with open(out_dir / "reports.jsonl", "w", encoding="utf-8") as f:
        for r in risk.itertuples(index=False):
            rec = {
                "_schema_version": SCHEMA_VERSION, "store_id": r.store_id, "as_of": as_of,
                "store": {"biz_type": r.biz_type, "gu": r.gu, **names.get(r.store_id, {})},
                "risk": {"probability_12m": round(float(r.probability_12m), 4),
                         "ci_low": round(float(r.ci_low), 4), "ci_high": round(float(r.ci_high), 4),
                         "interval_note": INTERVAL_NOTE, "band": r.band,
                         "percentile": None if pd.isna(r.percentile) else int(r.percentile),
                         "peer_group": r.peer_group, "peer_median": round(float(r.peer_median), 4),
                         "model": r.model, "calibrated": bool(r.calibrated)},
                "factors": diagnose.factors_json(by_store[r.store_id], r.store_id, r.origin,
                                                 res["values"](r.store_id, r.origin)),
                "unavailable_categories": unavailable,
                "disclaimer": DISCLAIMER,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    serve_meta = {
        "score_origin": s, "as_of": as_of, "n_stores": int(len(sc)), "primary_feature_set": primary,
        "train_origins": [train_origins[0], train_origins[-1]], "n_train_rows": int(tr.sum()),
        "band_cutoffs": cut, "n_boot": n_boot, "n_background": n_background,
        "detect_run": str(detect_dir), "detect_master_sha256": run_meta.get("master_sha256"),
        "master": str(master_path), "master_sha256": train_detect.sha256(master_path),
        "score": str(score_path), "score_sha256": train_detect.sha256(score_path),
        "online_score": str(online_score_path) if online_score_path else None,
        "licenses": str(licenses_path) if licenses_path is not None else None,
        "licenses_sha256": train_detect.sha256(licenses_path) if licenses_path is not None else None,
        "n_stores_without_name": n_no_name,
        "calibrated": iso is not None,
        "features_used": list(model.columns_), "excluded_unvalidated": excluded,
        "diagnosis_scale": "calibrated와 다름 (보정 전 확률)" if iso is not None else "risk 확률과 같음",
        "band_share": risk["band"].value_counts(normalize=True).round(4).to_dict(),
        "display_held_online": int((~long["display"] & ~long["data_missing"]).sum()),
        "display_held_missing": {fid: g["missing_reason"].value_counts().to_dict()
                                 for fid, g in long.loc[long["data_missing"]].groupby("factor_id")},
        "seconds": round(time.time() - t0, 1),
    }
    (out_dir / "serve_meta.json").write_text(json.dumps(serve_meta, ensure_ascii=False, indent=2, default=str),
                                             encoding="utf-8")
    train_detect.log(f"등급 비율 {serve_meta['band_share']} · 온라인 표시 보류 {serve_meta['display_held_online']:,}점포 "
                     f"· 데이터 없음 보류 {serve_meta['display_held_missing']}")
    train_detect.log(f"완료 ({serve_meta['seconds']}초) → {out_dir}")
    return risk


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="서빙: 예측용 패널 → 위험도·진단")
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--score", type=Path, required=True)
    ap.add_argument("--primary", default="base")
    ap.add_argument("--detect-dir", type=Path, default=None,
                    help="train_detect 결과 폴더 (기본: outputs/models/detect_v0[_<primary>])")
    ap.add_argument("--online", type=Path, default=None)
    ap.add_argument("--online-score", type=Path, default=None)
    ap.add_argument("--licenses", type=Path, default=None,
                    help=f"인허가 표준화 테이블 (기본: {DEFAULT_LICENSES.relative_to(config.REPO_ROOT)}가 있으면 사용)")
    ap.add_argument("--n-boot", type=int, default=20)
    ap.add_argument("--n-background", type=int, default=16)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    licenses = a.licenses
    if licenses is None:
        if DEFAULT_LICENSES.exists():
            licenses = DEFAULT_LICENSES
        else:
            train_detect.log(f"경고: {DEFAULT_LICENSES}가 없어 store 블록에 이름·주소를 붙이지 않는다")
    models = config.REPO_ROOT / "outputs" / "models"
    detect_dir = a.detect_dir or (models / (train_detect.MODEL_NAME + ("" if a.primary == "base" else f"_{a.primary}")))
    origin = str(pd.read_parquet(a.score, columns=["origin"])["origin"].iloc[0])
    out = a.out or (config.REPO_ROOT / "outputs" / "serve" / f"{origin}_{a.primary}")
    run(a.master, a.score, detect_dir, out, primary=a.primary, online_path=a.online,
        online_score_path=a.online_score, licenses_path=licenses, n_boot=a.n_boot, n_background=a.n_background)


if __name__ == "__main__":
    main()
