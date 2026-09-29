# -*- coding: utf-8 -*-
"""이슈 #33 리뷰 — 절단 결측이 온라인 NA 패턴을 통해 정보 경로로 쓰이는지 민감도 분석.

배경: `first_date_truncated`(절단 여부)는 2026-09 수집 시점의 누적 게시물 수(>200건)로 정해진다.
이는 origin 이후 인기(=생존)를 반영한 미래 정보라 predictor로 쓰지 않지만(#25), online_features의
결측 규칙 자체가 절단 여부에 좌우된다(`na` 정책: 미관측 구간 NA). 그래서 "온라인 feature가 NA다"라는
패턴 자체가 절단(→생존)의 대리 신호로 모형에 들어갈 수 있다 — 이것이 이 모듈이 재는 정보 경로다.
(단순히 lower_bound 방식과 AUC를 비교해 그 차이를 "누수 상한"이라 부르는 것은 틀렸다: 두 방식은
결측 패턴과 관측값 자체가 동시에 달라지므로 차이를 하나의 경로로 귀속할 수 없다. → DECISIONS.md 참고.)

세 가지 민감도(rolling OOF, 10개 origin, 현 설정·튜닝 설정 모두):
  (i)   현행         — enriched(`truncated_policy="na"`), 전체 점포 평가.
  (ii)  절단 점포 제외 평가 — (i)와 같은 예측을 절단 점포(QA `first_date_truncated=True`)만 평가에서
        제외하고 다시 계산 (base도 같은 방식으로 함께 낸다 — 리뷰어가 인용한 수치는 base 쪽이다).
  (iii) 절단 경로 차단   — enriched(`truncated_policy="lower_bound"`): 미관측 구간을 관측된 값(하한)으로
        채워 NA 표시가 절단과 무관해지게 한다 (`online_features.build_online_features`가 이미 지원).
추가로 "NA 플래그만 추가" — base(온라인 6개 없이) + 온라인 결측 여부만 나타내는 이진 피처 1개.
enriched의 실제 온라인 값 없이 결측 패턴 하나만 추가해도 AUC가 오르면, 그 패턴이 정보 경로로
쓰인다는 직접 증거다. base와의 AUC 차이가 이 경로의 크기를 보여준다(부트스트랩 CI로 유의성 판정).

(iii)이 (i)에 견줘 성능을 유의하게 떨어뜨리지 않으면(부트스트랩 CI가 0을 포함), `--truncated-policy
lower_bound`를 기본값으로 바꾸는 안을 검토할 수 있다 — 단, 실제 기본값 변경은 #44(짧은 상호 처리)·
#45(하이퍼파라미터 채택) 결정과 함께 적용한다(이 모듈은 옵션만 준비하고 기본값은 바꾸지 않는다).

실행:
    python -m src.analysis.online_truncation_sensitivity \
        --online-na outputs/online/online_features.parquet \
        --online-lb outputs/online/online_features_lb.parquet
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from src.data import config
from src.data.online_features import FEATURES
from src.models import features, train_detect

DEFAULT_OUT = config.REPO_ROOT / "outputs" / "w2" / "online_truncation_sensitivity"


def na_flag_column(online_table: pd.DataFrame, feature_cols=FEATURES) -> pd.Series:
    """온라인 feature 중 하나라도 NA면 1. 값 자체는 쓰지 않고 결측 패턴만 남긴다."""
    return online_table[list(feature_cols)].isna().any(axis=1).astype(float)


def truncated_store_ids(qa: pd.DataFrame) -> set:
    """QA에서 절단 점포(`ok`이면서 `truncated`) store_id 집합. `online_features.load_qa` 반환 형식과 같다."""
    return set(qa.loc[qa["ok"] & qa["truncated"], "store_id"])


def exclude_stores(oof: pd.DataFrame, df: pd.DataFrame, excluded: set) -> pd.DataFrame:
    """oof(rolling_oof 반환, idx로 df 행 참조) 중 excluded에 속한 store_id 행을 평가에서만 뗀다."""
    store_of_idx = df["store_id"].to_numpy()[oof["idx"].to_numpy()]
    keep = ~pd.Series(store_of_idx).isin(excluded).to_numpy()
    return oof[keep]


def pooled_auc(oof: pd.DataFrame) -> float:
    y = oof["y"].to_numpy()
    return float(roc_auc_score(y, oof["p_oof"])) if 0 < y.sum() < len(y) else float("nan")


def bootstrap_auc_diff(y, p_a, p_b, store_ids, *, n_boot: int = 1000, seed: int = 20260929) -> dict:
    """합산(pooled) AUC 차이의 점포 단위 부트스트랩 95% CI. `calibration.bootstrap_brier_diff`와 같은
    정수 가중치(np.bincount) 방식 — 행을 복제하지 않고 sklearn의 sample_weight로 가중 AUC를 낸다."""
    y = np.asarray(y, dtype=float)
    p_a, p_b = np.asarray(p_a, dtype=float), np.asarray(p_b, dtype=float)
    codes, uniq = pd.factorize(pd.Series(np.asarray(store_ids)))
    n = len(uniq)
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n)[codes].astype(float)
        diffs[b] = roc_auc_score(y, p_a, sample_weight=w) - roc_auc_score(y, p_b, sample_weight=w)
    obs = roc_auc_score(y, p_a) - roc_auc_score(y, p_b)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"diff": float(obs), "ci_low": float(lo), "ci_high": float(hi), "n_boot": n_boot,
            "significant": bool(lo > 0 or hi < 0)}


def _align(oof_a: pd.DataFrame, oof_b: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """idx로 맞춘 (y, p_a, p_b, idx). i/iii·flag/base는 같은 df에서 만든 OOF라 idx 집합이 같아야 한다."""
    m = oof_a.merge(oof_b[["idx", "p_oof"]], on="idx", suffixes=("", "_b"), validate="1:1")
    if len(m) != len(oof_a):
        raise ValueError("두 OOF의 idx가 일치하지 않는다 — 같은 df·embargo로 만들었는지 확인")
    return m["y"].to_numpy(), m["p_oof"].to_numpy(), m["p_oof_b"].to_numpy(), m["idx"].to_numpy()


def build_feature_variants(df: pd.DataFrame, online_na: pd.DataFrame, online_lb: pd.DataFrame
                           ) -> dict[str, tuple[pd.DataFrame, list]]:
    """(i)/(iii)/flag/base 각각의 (join된 df, feature 컬럼 목록). base·i·iii는 (store_id, origin) 1:1
    조인, flag는 base + 결측 패턴 1개 컬럼."""
    cols = ["store_id", "origin"] + [c for c in FEATURES if c in online_na.columns]
    df_na = df.merge(online_na[cols], on=["store_id", "origin"], how="left", validate="1:1")
    cols_lb = ["store_id", "origin"] + [c for c in FEATURES if c in online_lb.columns]
    df_lb = df.merge(online_lb[cols_lb], on=["store_id", "origin"], how="left", validate="1:1")
    base_cols = features.select_features(df.columns, "base")
    enriched_cols = features.select_features(df_na.columns, "enriched")

    flag_src = online_na[["store_id", "origin"]].copy()
    flag_src["online_any_na"] = na_flag_column(online_na)
    df_flag = df.merge(flag_src, on=["store_id", "origin"], how="left", validate="1:1")

    return {
        "base": (df, base_cols),
        "flag": (df_flag, base_cols + ["online_any_na"]),
        "i": (df_na, enriched_cols),
        "iii": (df_lb, enriched_cols),
    }


def sensitivity_report(variants: dict[str, tuple[pd.DataFrame, list]], y: np.ndarray, trunc_stores: set,
                       *, params: dict | None = None, n_boot: int = 1000, seed: int = 20260929) -> dict:
    """variants: build_feature_variants()가 준 4개 시나리오. 반환에 (ii)·base_ex_trunc를 추가로 계산."""
    oof = {}
    for name, (d, cols) in variants.items():
        # online_any_na는 진단용 파생 컬럼이라 정식 predictor 허용 목록(assert_predictors_only)에
        # 없다 — 나머지 predictor로 build_X를 만들고 마지막에 그대로 덧붙인다.
        core = [c for c in cols if c != "online_any_na"]
        X = features.build_X(d, core)
        if "online_any_na" in cols:
            X["online_any_na"] = d["online_any_na"].astype(float).to_numpy()
        oof[name] = train_detect.rolling_oof(d, X, y, params=params)
    df_base = variants["base"][0]

    oof["ii"] = exclude_stores(oof["i"], df_base, trunc_stores)
    oof["base_ex_trunc"] = exclude_stores(oof["base"], df_base, trunc_stores)

    mean_auc, pooled = {}, {}
    for name, o in oof.items():
        mean_auc[name] = float(train_detect.metrics_by_origin(o)["auc"].mean())
        pooled[name] = pooled_auc(o)

    y_i, p_i, p_iii, idx_i = _align(oof["i"], oof["iii"])
    stores_i = df_base["store_id"].to_numpy()[idx_i]
    ci_i_vs_iii = bootstrap_auc_diff(y_i, p_i, p_iii, stores_i, n_boot=n_boot, seed=seed)

    y_f, p_f, p_b, idx_f = _align(oof["flag"], oof["base"])
    stores_f = df_base["store_id"].to_numpy()[idx_f]
    ci_flag_vs_base = bootstrap_auc_diff(y_f, p_f, p_b, stores_f, n_boot=n_boot, seed=seed + 1)

    return {"mean_auc": mean_auc, "pooled_auc": pooled,
            "ci_i_vs_iii": ci_i_vs_iii, "ci_flag_vs_base": ci_flag_vs_base,
            "oof": oof}


def run(master_path: Path, online_na_path: Path, online_lb_path: Path, qa_path: Path, out_dir: Path,
       *, n_boot: int = 1000) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    from src.data.online_features import load_qa

    df = train_detect.load_master(master_path)
    y = df["event_12m"].to_numpy().astype(int)
    online_na = pd.read_parquet(online_na_path)
    online_lb = pd.read_parquet(online_lb_path)
    trunc_stores = truncated_store_ids(load_qa(qa_path))
    variants = build_feature_variants(df, online_na, online_lb)

    results = {}
    for pname, params in (("현 설정", None), ("튜닝", train_detect.TUNED_PARAMS)):
        print(f"[{pname}] rolling OOF ×4 (base/flag/i/iii)")
        r = sensitivity_report(variants, y, trunc_stores, params=params, n_boot=n_boot, seed=20260929)
        results[pname] = {k: v for k, v in r.items() if k != "oof"}
        print(f"  평균 AUC: 현행(i) {r['mean_auc']['i']:.4f} / 절단제외(ii) {r['mean_auc']['ii']:.4f} "
              f"/ 경로차단(iii) {r['mean_auc']['iii']:.4f}")
        print(f"  합산 AUC: base {r['pooled_auc']['base']:.4f} / base_절단제외 {r['pooled_auc']['base_ex_trunc']:.4f} "
              f"/ flag만추가 {r['pooled_auc']['flag']:.4f} (base 대비 {r['pooled_auc']['flag']-r['pooled_auc']['base']:+.4f}) "
              f"/ i {r['pooled_auc']['i']:.4f} / iii {r['pooled_auc']['iii']:.4f}")
        print(f"  i-iii 합산 AUC 차이 CI: {r['ci_i_vs_iii']}")
        print(f"  flag-base 합산 AUC 차이 CI: {r['ci_flag_vs_base']}")

    (out_dir / "truncation_sensitivity.json").write_text(
        json.dumps({"n_trunc_stores": len(trunc_stores), **results}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    rows = []
    for pname, r in results.items():
        for scen in ("base", "base_ex_trunc", "flag", "i", "ii", "iii"):
            rows.append({"params": pname, "scenario": scen, "mean_auc": r["mean_auc"][scen],
                        "pooled_auc": r["pooled_auc"][scen]})
    pd.DataFrame(rows).to_csv(out_dir / "truncation_sensitivity_table.csv", index=False)
    return results


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#33 절단 결측 누수 민감도(rolling OOF, i/ii/iii + NA 플래그)")
    ap.add_argument("--master", type=Path, default=config.REPO_ROOT / "outputs" / "master" / "master_base.parquet")
    ap.add_argument("--online-na", type=Path, default=config.REPO_ROOT / "outputs" / "online" / "online_features.parquet")
    ap.add_argument("--online-lb", type=Path,
                    default=config.REPO_ROOT / "outputs" / "online" / "online_features_lb.parquet")
    ap.add_argument("--qa", type=Path, default=config.REPO_ROOT / "data" / "interim" / "online_blog_monthly_qa.csv")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--n-boot", type=int, default=1000)
    a = ap.parse_args(argv)
    run(a.master, a.online_na, a.online_lb, a.qa, a.out, n_boot=a.n_boot)


if __name__ == "__main__":
    main()
