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
- 모형 설정(#51): 탐지 실행 run_meta의 `params`·`params_name`·`model_class`가 모두 있어야 하고 `params_name="adopted"`
  (= `detect.ADOPTED_PARAMS`, (0.03, 31))여야 한다. 아니면 멈춘다 — 다른 설정은 `--allow-non-adopted-params`로만
  (개발·과거 재현용, 운영 출력 아님). DEFAULT_PARAMS로 대체하지 않는다.
- 같은 탐지 실행(#51): run_meta의 master·온라인 해시가 서빙 입력과 같아야 하고, run_meta의 band_cutoffs가
  band_cutoffs.csv와 같아야 한다. `--diagnose-meta`를 주면 그 진단이 같은 run_meta(파일 해시)·같은 params·같은
  master로 만들어졌는지 확인한다. 다르면 멈춘다.
- 진단은 `diagnose`(#53) 그대로 — 요인 매핑·Shapley·peer 비교·표시 보류(hold_reason/missing_reason)·절단 점포
  온라인 보류·driver_code·S8 두 배경(`background_rows_s8`·`explain_s8`, "해석 민감")·배경 manifest 검증. serve는
  탐지 결과 로드 → 진단 호출 → 서빙 레코드 조립 → serve_meta 기록만 한다.
- 경쟁지표(comp_*)는 이번 서빙에 넣지 않는다(#43, feature set에 없음 — 들어오면 멈춘다).

입력
- `--master`: 라벨 있는 master_base (학습용)
- `--score` : 예측용 패널 (master_base와 같은 predictor, `event_12m` 없음, origin 하나)
- `--online` / `--online-score`: 온라인 Enriched 테이블 (enriched일 때). 예측용은
  `python -m src.data.online_features --panel <score parquet> --out <online_score parquet>`로 만든다.
- `--licenses`: 인허가 표준화 테이블 (기본 `outputs/standardized/licenses_3gu.parquet`, 없으면 건너뜀).
  store_id로 1:1 조인해 reports.jsonl의 store 블록에 사업장명·주소·인허가일을 붙인다 (모형 입력에는 쓰지 않는다).

출력 (`outputs/serve/<origin>_<feature set>/`)
- `risk_scores.parquet`, `diagnosis.parquet`, `diagnosis_by_category.parquet`
- `reports.jsonl` — 점포당 1줄, serve 입력 0.2. S8일 때 interpretation_sensitive/sensitivity_label을
  기본으로 포함한다(최종 W2-5 report 0.3 계약). 무작위 개발 배경에는 sensitivity pair가 없다.
- `serve_meta.json` — 입력 sha256, 학습 구간, 모형 설정(params_name), 같은 run 검사, S8 배경, 컷오프, 소요 시간

실행:
    python -m src.models.serve --score outputs/master/master_score.parquet --primary enriched \\
        --online outputs/online/online_features.parquet --online-score outputs/online/online_features_score.parquet \\
        --detect-dir outputs/models/detect_v0_enriched --qa <online_blog_monthly_qa.csv> \\
        --diagnose-meta outputs/models/diagnosis_enriched/diagnose_meta.json
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
from src.models import background, bands, detect, diagnose, features, train_detect, uncertainty

SCHEMA_VERSION = "0.2"  # 0.2: score_origin, factors[].missing_reason·hold_reason, direction "영향 미미"
# 2026-09-30(#34/#41): factors[].driver_code 추가 — #41 report_schema.json $defs/online_driver_code와 같은 선택
# 필드라 버전은 올리지 않는다(없거나 null이면 구버전 입력과 같다). online_attention에만 값이 있다.
DISCLAIMER = "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."
INTERVAL_NOTE = "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지의 범위이며, 폐업 확률 자체의 범위가 아닙니다."
DEFAULT_LICENSES = config.REPO_ROOT / "outputs" / "standardized" / "licenses_3gu.parquet"
# store 블록 필드 ← 인허가 표준화 컬럼 (사업장명·주소는 원문 그대로). 필드명은 W2-6 화면 더미에 맞춘다 (PR #37).
STORE_META_COLS = {"name": "name_raw", "address_road": "road_addr_raw", "address_jibun": "addr_raw", "dong": "dong",
                   "license_date": "license_date"}


def _meta_value(v):
    if v is None or pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return str(v)


def store_meta(store_ids, licenses_path: Path) -> dict[str, dict]:
    """store_id → {name, address_road, address_jibun, dong, license_date("YYYY-MM-DD")}.

    인허가 테이블과 1:1 조인, 없는 점포는 값 None.
    """
    lic = pd.read_parquet(licenses_path, columns=["store_id", *STORE_META_COLS.values()])
    dup = lic["store_id"].duplicated()
    if dup.any():
        raise ValueError(f"인허가 테이블 store_id 중복 {int(dup.sum())}건 — 1:1 조인 불가: {licenses_path}")
    sub = lic.set_index("store_id").reindex(list(store_ids))
    return {sid: {k: _meta_value(row[c]) for k, c in STORE_META_COLS.items()} for sid, row in sub.iterrows()}


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


SERVING_PARAMS_NAME = "adopted"  # #51: 최종 서빙은 detect.ADOPTED_PARAMS (0.03, 31)
BAND_CUTOFF_KEYS = ("cut_mid", "cut_high", "base_rate")  # serve_meta.band_cutoffs (#41 serve_band_cutoffs)


def model_params(run_meta: dict, *, allow_non_adopted: bool = False) -> dict:
    """#45/#51: 탐지 실행(run_meta.json)의 모형 설정을 그대로 읽어 서빙 모형·부트스트랩·진단에 쓴다.

    기본 경로는 params·params_name·model_class가 모두 있고 params_name="adopted"이며 params가
    detect.ADOPTED_PARAMS와 같아야 한다. params_name이 없는 #51 이전 run_meta는 legacy_default/legacy_unnamed
    (`train_detect.run_meta_params_name`)로 읽혀 거부된다. allow_non_adopted=True(개발·과거 재현용)이면 이름·값
    검사만 풀고 그 사실을 contract에 남긴다. params가 없거나 model_class가 다른 클래스면 언제나 멈춘다
    (DEFAULT_PARAMS로 대체하지 않는다). 반환: {params, params_name, model_class, source, contract}."""
    params = run_meta.get("params")
    if not isinstance(params, dict) or not params:
        raise ValueError("탐지 실행 run_meta에 params가 없다 — #51 이후 train_detect로 다시 만든다 "
                         "(DEFAULT_PARAMS로 대체하지 않는다)")
    name = train_detect.run_meta_params_name(run_meta)
    cls = run_meta.get("model_class")
    if cls is not None and cls != detect.MODEL_CLASS:
        raise ValueError(f"탐지 실행 model_class({cls})가 서빙 모형({detect.MODEL_CLASS})과 다르다")
    problems = []
    if cls is None:
        problems.append("model_class 없음")
    if name != SERVING_PARAMS_NAME:
        problems.append(f"params_name={name}")
    elif dict(params) != dict(detect.ADOPTED_PARAMS):
        problems.append("params_name=adopted인데 params가 detect.ADOPTED_PARAMS와 다르다")
    if problems and not allow_non_adopted:
        raise ValueError(f"서빙은 채택 설정(params_name={SERVING_PARAMS_NAME}, detect.ADOPTED_PARAMS)만 쓴다: "
                         f"{'; '.join(problems)} — train_detect --params adopted로 다시 만든다 "
                         "(개발·과거 재현만 --allow-non-adopted-params)")
    return {"params": dict(params), "params_name": name, "model_class": cls or detect.MODEL_CLASS,
            "source": "detect run_meta.params",
            "contract": SERVING_PARAMS_NAME if not problems else "override (비운영): " + "; ".join(problems)}


def check_same_run(run_meta: dict, cut: dict, master_path: Path, online_path: Path | None) -> None:
    """#51 같은 탐지 실행: 컷오프·모형 설정을 가져온 run이 서빙 입력과 같은 master·온라인 표로 만들어졌는지,
    run_meta의 band_cutoffs가 band_cutoffs.csv와 같은지. 다르면 멈춘다."""
    want = run_meta.get("master_sha256")
    if want is None or want != train_detect.sha256(master_path):
        raise ValueError(f"탐지 실행의 master 해시({want})가 서빙 master({master_path})와 다르다 — 같은 run이 아니다")
    want_on = run_meta.get("online_sha256")
    got_on = train_detect.sha256(online_path) if online_path is not None else None
    if want_on != got_on:
        raise ValueError(f"탐지 실행의 온라인 표 해시({want_on})가 서빙 온라인 표({got_on})와 다르다 — 같은 run이 아니다")
    rc = run_meta.get("band_cutoffs") or {}
    for k in ("cut_mid", "cut_high"):
        if k not in rc or abs(float(rc[k]) - float(cut[k])) > 1e-12:
            raise ValueError(f"run_meta.band_cutoffs.{k}({rc.get(k)})가 band_cutoffs.csv({cut[k]})와 다르다")
    bp = run_meta.get("band_provenance") or {}
    for k in ("cut_mid", "cut_high"):
        if k in bp and abs(float(bp[k]) - float(cut[k])) > 1e-12:
            raise ValueError(f"run_meta.band_provenance.{k}({bp[k]})가 band_cutoffs.csv({cut[k]})와 다르다")


def check_diagnose_meta(diagnose_meta_path: Path, run_meta_sha256: str, params: dict, master_path: Path,
                        primary: str) -> dict:
    """`python -m src.models.diagnose --detect-run <같은 run>`의 diagnose_meta.json이 이 서빙과 같은 탐지 실행
    (run_meta 파일 해시)·같은 params·같은 master·같은 feature set으로 만들어졌는지. 다르면 멈춘다."""
    dm = json.loads(Path(diagnose_meta_path).read_text(encoding="utf-8"))
    errs = []
    if dm.get("detect_run_meta_sha256") != run_meta_sha256:
        errs.append(f"run_meta 해시 {dm.get('detect_run_meta_sha256')} ≠ {run_meta_sha256}")
    if dm.get("model_params") != params:
        errs.append("model_params가 다르다")
    if dm.get("master_sha256") != train_detect.sha256(master_path):
        errs.append("master 해시가 다르다")
    if dm.get("primary_feature_set") != primary:
        errs.append(f"feature set {dm.get('primary_feature_set')} ≠ {primary}")
    if errs:
        raise ValueError(f"진단 실행({diagnose_meta_path})이 서빙과 같은 탐지 실행이 아니다: {'; '.join(errs)}")
    return {"path": str(diagnose_meta_path), "sha256": train_detect.sha256(Path(diagnose_meta_path)),
            "detect_run_meta_sha256": dm["detect_run_meta_sha256"], "model_params_source": dm.get("model_params_source")}


def run(master_path: Path, score_path: Path, detect_dir: Path, out_dir: Path, *, primary: str,
        online_path: Path | None = None, online_score_path: Path | None = None,
        licenses_path: Path | None = None, qa_path: Path | None = None, background_manifest: Path | None = None,
        random_background: int | None = None, diagnose_meta: Path | None = None,
        allow_non_adopted_params: bool = False, expose_sensitivity: bool = True,
        n_boot: int = 20, seed: int = 20260925) -> pd.DataFrame:
    """background_manifest: S8 manifest(기본 `background.DEFAULT_MANIFEST`, 없으면 멈춤) — 두 배경으로 진단(#53).
    random_background: 정수를 주면 manifest 대신 학습 구간 무작위 그 개수 하나로만(시험용, 해석 민감 없음, 비운영).
    diagnose_meta: 별도로 돌린 diagnose의 diagnose_meta.json — 같은 탐지 실행인지 확인(다르면 멈춤).
    expose_sensitivity: S8 reports factor에 sensitivity pair를 넣는다(기본 True, False는 개발 호환용)."""
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    if licenses_path is not None and not Path(licenses_path).exists():
        raise FileNotFoundError(f"인허가 테이블이 없다: {licenses_path}")
    if random_background is None and background_manifest is None:
        background_manifest = background.DEFAULT_MANIFEST
    run_meta, cut, iso = read_detect_run(detect_dir)
    if run_meta.get("primary_feature_set", "base") != primary:
        raise ValueError(f"탐지 실행의 feature set({run_meta.get('primary_feature_set')})과 서빙({primary})이 다르다")
    mp = model_params(run_meta, allow_non_adopted=allow_non_adopted_params)
    params = mp["params"]
    check_same_run(run_meta, cut, master_path, online_path)
    run_meta_sha = train_detect.sha256(Path(detect_dir) / "run_meta.json")
    diag_check = (check_diagnose_meta(diagnose_meta, run_meta_sha, params, master_path, primary)
                  if diagnose_meta is not None else None)

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
    comp = [c for c in cols if c.startswith("comp_")]
    if comp:
        raise ValueError(f"경쟁지표는 이번 서빙에 넣지 않는다(#43): {comp}")
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

    train_detect.log(f"모형 설정({mp['source']}, params_name={mp['params_name']}, {mp['contract']}): {params}")
    model = detect.DetectModel(params=params).fit(Xtr, ytr)
    p_raw = model.predict_proba(Xs)
    p = iso.predict(p_raw) if iso is not None else p_raw

    lo = hi = np.full(len(sc), np.nan)
    if n_boot > 0:
        train_detect.log(f"부트스트랩 {n_boot}회 (점포 단위)")
        lo, hi, _ = uncertainty.bootstrap_interval(
            lambda a, b, c: detect.fit_predict(a, b, c, params), Xtr, ytr, Xs, n_boot=n_boot, alpha=0.10,
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
    truncated_stores = None
    if online_score_path is not None and qa_path is not None:
        truncated_stores = diagnose.load_truncated_stores(qa_path)
        train_detect.log(f"절단 점포 {len(truncated_stores):,}곳 (QA {qa_path})")
    # 진단 = #53 diagnose 그대로. 위험 확률을 만든 같은 모형 객체를 분해한다(진단 모형 설정 = run_meta.params).
    meta = sc[["store_id", "origin", "biz_type", "gu", "age_months"]]
    if random_background is not None:
        bg, info = diagnose.background_rows(None, lab, tr, rng, random_background)
        res = diagnose.explain(model, Xs, features.build_X(lab.iloc[bg], cols, categories=cats), meta, sc,
                               truncated_stores=truncated_stores)
        bg_prov, s8 = {"backgrounds": {"primary": info}, "rule_ref": None, "operational": False}, None
    else:
        idx, bg_prov = diagnose.background_rows_s8(background_manifest, lab, tr)
        res = diagnose.explain_s8(model, Xs, features.build_X(lab.iloc[idx["primary"]], cols, categories=cats),
                                  features.build_X(lab.iloc[idx["sensitivity"]], cols, categories=cats), meta, sc,
                                  truncated_stores=truncated_stores)
        s8 = res["s8_summary"]
        bg_prov = {**bg_prov, "operational": all(b["operational"] for b in bg_prov["backgrounds"].values())}
        train_detect.log(f"S8 해석 민감: {s8['n_interpretation_sensitive']:,} / {s8['n_store_factor']:,} 점포×요인")
    long = res["long"]
    long.to_parquet(out_dir / "diagnosis.parquet", index=False)
    res["by_category"].to_parquet(out_dir / "diagnosis_by_category.parquet", index=False)
    # 요인 기여도는 보정 전 확률 척도에서 정확히 합산된다 (W2-3). 보정이 적용되면 화면 확률과 합이 달라진다.
    # 배경(S8)은 설명만 바꾼다 — 진단 확률·그 확률로 정한 등급이 위험도와 다르면 멈춘다.
    p_diag = res["meta"]["probability_12m"].to_numpy()
    if np.abs(p_diag - p_raw).max() > 1e-12:
        raise RuntimeError("진단 확률과 모형 확률이 다르다")
    p_diag_cal = iso.predict(p_diag) if iso is not None else p_diag
    if not np.array_equal(bands.assign_bands_absolute(p_diag_cal, cut_mid=cut["cut_mid"], cut_high=cut["cut_high"]),
                          risk["band"].to_numpy()):
        raise RuntimeError("진단 확률로 정한 등급이 위험도 등급과 다르다")

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
                "_schema_version": SCHEMA_VERSION, "store_id": r.store_id, "score_origin": s, "as_of": as_of,
                "store": {"biz_type": r.biz_type, "gu": r.gu, **names.get(r.store_id, {})},
                "risk": {"probability_12m": round(float(r.probability_12m), 4),
                         "ci_low": round(float(r.ci_low), 4), "ci_high": round(float(r.ci_high), 4),
                         "interval_note": INTERVAL_NOTE, "band": r.band,
                         "percentile": None if pd.isna(r.percentile) else int(r.percentile),
                         "peer_group": r.peer_group, "peer_median": round(float(r.peer_median), 4),
                         "model": r.model, "calibrated": bool(r.calibrated)},
                "factors": diagnose.factors_json(by_store[r.store_id], r.store_id, r.origin,
                                                 res["values"](r.store_id, r.origin),
                                                 with_sensitivity=expose_sensitivity and s8 is not None),
                "unavailable_categories": unavailable,
                "disclaimer": DISCLAIMER,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    serve_meta = {
        "score_origin": s, "as_of": as_of, "n_stores": int(len(sc)), "primary_feature_set": primary,
        "train_origins": [train_origins[0], train_origins[-1]], "n_train_rows": int(tr.sum()),
        # #51: params_name이 정본. model_params_is_default는 이전 소비자용 보조 필드(뜻이 겹치는 bool)
        "params_name": mp["params_name"], "model_params": params, "model_class": mp["model_class"],
        "model_params_source": mp["source"], "params_contract": mp["contract"],
        "model_params_is_default": params == dict(detect.DEFAULT_PARAMS),
        "detect_run_provenance": {"path": str(detect_dir), "run_meta_sha256": run_meta_sha,
                                  "params_name": mp["params_name"], "master_sha256": run_meta.get("master_sha256"),
                                  "online_sha256": run_meta.get("online_sha256"),
                                  "same_run_checks": ["master_sha256", "online_sha256", "band_cutoffs"]
                                  + (["diagnose_meta"] if diag_check else [])},
        "diagnosis": {"model_params_source": "serve 위험도 모형과 같은 객체 (detect run_meta.params)",
                      "same_model_as_risk": True, "diagnose_meta_check": diag_check,
                      "sensitivity_exposed_in_reports": expose_sensitivity and s8 is not None,
                      "sensitivity_note": None if expose_sensitivity and s8 is not None else
                      "개발용 출력: sensitivity pair 없음, release report 빌드 불가"},
        "s8_rule": {k: bg_prov.get(k) for k in ("rule_ref", "rule_version", "method", "n_background", "comparison")},
        "s8_summary": s8,
        "band_definition": run_meta.get("band_definition"),
        "cutoff_provenance": {**(run_meta.get("band_provenance") or {}),
                              "high_share_served": float((risk["band"] == "high").mean())},
        # #41 serve_band_cutoffs 계약(additionalProperties=false) = {cut_mid, cut_high, base_rate}. #45 이후
        # band_cutoffs.csv에 있는 fallback 여부는 cutoff_provenance(high_fallback·mid_fallback)에 이미 있다.
        "band_cutoffs": {k: cut[k] for k in BAND_CUTOFF_KEYS if k in cut}, "n_boot": n_boot,
        "background": bg_prov,
        "qa": str(qa_path) if qa_path else None, "n_truncated_stores": len(truncated_stores) if truncated_stores else 0,
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
    ap.add_argument("--qa", type=Path, default=None,
                    help="온라인 QA csv — 있으면 절단 점포를 온라인 요인 data_missing으로 보류 (#34)")
    ap.add_argument("--background-manifest", type=Path, default=background.DEFAULT_MANIFEST,
                    help="S8 배경 manifest (기본 outputs/diagnosis/background/background_manifest.json, 없으면 오류 — "
                         "`python -m src.models.background create`로 만든다)")
    ap.add_argument("--random-background", type=int, default=None, metavar="N",
                    help="manifest 대신 학습 구간 무작위 N개 하나로 (비교·시험용, 비운영, 해석 민감 없음)")
    ap.add_argument("--diagnose-meta", type=Path, default=None,
                    help="같은 --detect-run으로 돌린 diagnose의 diagnose_meta.json — 같은 탐지 실행인지 확인")
    ap.add_argument("--allow-non-adopted-params", action="store_true",
                    help="params_name≠adopted·legacy run_meta도 허용 (개발·과거 재현용, 운영 출력 아님)")
    ap.add_argument("--expose-sensitivity", action="store_true",
                    help="호환 옵션: S8 reports에 sensitivity pair는 이제 기본 포함")
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
        online_score_path=a.online_score, licenses_path=licenses, qa_path=a.qa,
        background_manifest=None if a.random_background is not None else a.background_manifest,
        random_background=a.random_background, diagnose_meta=a.diagnose_meta,
        allow_non_adopted_params=a.allow_non_adopted_params, expose_sensitivity=True,
        n_boot=a.n_boot)


if __name__ == "__main__":
    main()
