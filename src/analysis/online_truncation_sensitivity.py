# -*- coding: utf-8 -*-
"""이슈 #33 리뷰 — 절단 결측이 온라인 NA 패턴을 통해 정보 경로로 쓰이는지 민감도 분석.

배경: `first_date_truncated`(절단 여부)는 2026-09 수집 시점의 누적 게시물 수(>200건)로 정해진다.
이는 origin 이후 인기(=생존)를 반영한 미래 정보라 predictor로 쓰지 않지만(#25), online_features의
결측 규칙 자체가 절단 여부에 좌우된다(`na` 정책: 미관측 구간 NA). 그래서 "온라인 feature가 NA다"라는
패턴 자체가 절단(→생존)의 대리 신호로 모형에 들어갈 수 있다 — 이것이 이 모듈이 재는 정보 경로다.
(`lower_bound`와의 AUC 차이를 "누수 상한"이라 부르는 것은 틀렸다: 두 방식은 결측 패턴과 관측값이 동시에
달라지므로 차이를 하나의 경로로 귀속할 수 없다. → DECISIONS.md 참고.)

민감도 (rolling OOF, 10개 origin, 현 설정·튜닝 설정 모두):
  (i)    현행          — enriched(`truncated_policy="na"`), 전체 점포.
  (ii)   절단 점포 평가 제외 — (i)와 같은 예측에서 절단 점포(QA `first_date_truncated=True`)만 평가에서 뗌.
         학습에는 절단 점포가 그대로 들어가므로 clean 실험이 아니다.
  (iii)  lower_bound  — enriched(`truncated_policy="lower_bound"`): 절단 점포의 미관측 구간을 관측된 글만 센
         하한값으로 채운다. NA 표시는 사라지지만 **절단 경로를 차단하지 않는다** — 수집 시점 인기(>200건)로
         정해지는 절단 점포의 과거 창이 과소 집계되므로 절단 여부가 값에 그대로 남는다.
  (clean) 절단 점포 제외 — 절단 점포를 **학습·평가 모두**에서 빼고 base vs enriched를 비교한다. 절단 경로가 아예
         없는 표본에서 온라인 feature의 효과를 본다(점포를 모집단에서 지우는 처리가 아니라 민감도 실험이다).
  (flag) 절단 전용 NA 플래그 — base + "이 행의 온라인 NA가 절단 때문인가"(절단 점포이면서 온라인 feature 중
         하나라도 NA) 이진 피처 1개. base보다 AUC가 유의하게 오르면 절단 경로가 실제 정보를 싣는다는 직접 증거다.
         이전 버전의 "온라인 feature 중 하나라도 NA" 플래그(`na_flag_column`)는 쓰지 않는다 — 실데이터에서 그 NA의
         대부분은 절단이 아니라 "언급이 한 번도 없음"(`online_blog_months_since_last`가 정의상 NA)이라 절단 대리
         지표가 아니다(구성은 `any_na_composition`으로 함께 낸다).

(iii)이 (i)에 견줘 성능을 유의하게 떨어뜨리지 않으면 `--truncated-policy lower_bound`를 기본값으로 바꾸는 안을
검토할 수 있다는 기존 규칙은 유지하지만, lower_bound도 절단 영향을 담으므로 전환이 경로 차단을 뜻하지는 않는다.
실제 기본값·처리 정책은 #44(짧은 상호)·#45 결정과 함께 정한다(이 모듈은 측정만 한다).

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
TRUNC_FLAG = "online_trunc_na"  # 진단용 파생 컬럼 (정식 predictor 아님)


def na_flag_column(online_table: pd.DataFrame, feature_cols=FEATURES) -> pd.Series:
    """온라인 feature 중 하나라도 NA면 1. **절단 대리 지표로 쓰지 않는다** — 언급 없음·미수집 NA가 섞인다
    (`any_na_composition`). 구성 보고용으로만 남긴다."""
    return online_table[list(feature_cols)].isna().any(axis=1).astype(float)


def truncation_na_flag(online_table: pd.DataFrame, trunc_stores: set, feature_cols=FEATURES) -> pd.Series:
    """절단 때문에 생긴 NA만 1: 절단 점포(QA ok·truncated)이면서 온라인 feature 중 하나라도 NA인 행.
    QA가 ok인 절단 점포에서 NA는 결측 규칙상 미관측 구간(절단)에서만 생긴다 — 언급 없음은 0/NA가 아니라
    `months_since_last`만 NA인데, 절단 점포의 그 NA도 "관측 구간 안에서 못 찾음"이라 절단 때문이다."""
    is_trunc = online_table["store_id"].isin(trunc_stores).to_numpy()
    return pd.Series((is_trunc & online_table[list(feature_cols)].isna().any(axis=1).to_numpy()).astype(float),
                     index=online_table.index)


def any_na_composition(online_table: pd.DataFrame, trunc_stores: set) -> dict:
    """'하나라도 NA' 행의 원인별 구성 — 절단 점포 / 전부 NA(QA 미수집·오류) / 언급 없음(has_ever=0) / 기타."""
    any_na = online_table[list(FEATURES)].isna().any(axis=1).to_numpy()
    all_na = online_table[list(FEATURES)].isna().all(axis=1).to_numpy()
    trunc = online_table["store_id"].isin(trunc_stores).to_numpy()
    never = online_table["online_blog_has_ever"].eq(0).to_numpy()
    n = int(any_na.sum())
    # 절단 점포(QA ok)는 창이 전부 미관측이면 전부 NA가 되므로 "전부 NA"보다 먼저 센다 — 전부 NA는 비절단 점포만(미수집·오류)
    parts = {"truncated_store": int((any_na & trunc).sum()), "all_na_qa_missing_or_error": int((any_na & all_na & ~trunc).sum()),
             "never_mentioned": int((any_na & ~trunc & ~all_na & never).sum())}
    parts["other"] = n - sum(parts.values())
    return {"rows": int(len(online_table)), "any_na_rows": n, **parts,
            "share_truncated": parts["truncated_store"] / n if n else float("nan"),
            "share_never_mentioned": parts["never_mentioned"] / n if n else float("nan")}


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
    """idx로 맞춘 (y, p_a, p_b, idx). 같은 df에서 만든 OOF라 idx 집합이 같아야 한다."""
    m = oof_a.merge(oof_b[["idx", "p_oof"]], on="idx", suffixes=("", "_b"), validate="1:1")
    if len(m) != len(oof_a):
        raise ValueError("두 OOF의 idx가 일치하지 않는다 — 같은 df·embargo로 만들었는지 확인")
    return m["y"].to_numpy(), m["p_oof"].to_numpy(), m["p_oof_b"].to_numpy(), m["idx"].to_numpy()


def build_feature_variants(df: pd.DataFrame, online_na: pd.DataFrame, online_lb: pd.DataFrame, trunc_stores: set
                           ) -> dict[str, tuple[pd.DataFrame, list]]:
    """base / flag(base + 절단 전용 NA 플래그) / i(na) / iii(lower_bound) 각각의 (join된 df, feature 컬럼 목록)."""
    cols = ["store_id", "origin"] + [c for c in FEATURES if c in online_na.columns]
    df_na = df.merge(online_na[cols], on=["store_id", "origin"], how="left", validate="1:1")
    cols_lb = ["store_id", "origin"] + [c for c in FEATURES if c in online_lb.columns]
    df_lb = df.merge(online_lb[cols_lb], on=["store_id", "origin"], how="left", validate="1:1")
    base_cols = features.select_features(df.columns, "base")
    enriched_cols = features.select_features(df_na.columns, "enriched")

    flag_src = online_na[["store_id", "origin"]].copy()
    flag_src[TRUNC_FLAG] = truncation_na_flag(online_na, trunc_stores).to_numpy()
    df_flag = df.merge(flag_src, on=["store_id", "origin"], how="left", validate="1:1")

    return {
        "base": (df, base_cols),
        "flag": (df_flag, base_cols + [TRUNC_FLAG]),
        "i": (df_na, enriched_cols),
        "iii": (df_lb, enriched_cols),
    }


def _oof(d: pd.DataFrame, cols: list, y: np.ndarray, params) -> pd.DataFrame:
    # 절단 플래그는 진단용 파생 컬럼이라 정식 predictor 허용 목록(assert_predictors_only)에 없다 —
    # 나머지 predictor로 build_X를 만들고 마지막에 그대로 덧붙인다.
    X = features.build_X(d, [c for c in cols if c != TRUNC_FLAG])
    if TRUNC_FLAG in cols:
        X[TRUNC_FLAG] = d[TRUNC_FLAG].astype(float).to_numpy()
    return train_detect.rolling_oof(d, X, y, params=params)


def sensitivity_report(variants: dict[str, tuple[pd.DataFrame, list]], y: np.ndarray, trunc_stores: set,
                       *, params: dict | None = None, n_boot: int = 1000, seed: int = 20260929) -> dict:
    """variants: build_feature_variants()가 준 4개 시나리오. (ii)·base_ex_trunc(평가만 제외)와
    clean(학습·평가 모두 제외: base_clean·i_clean)을 추가로 계산한다."""
    oof = {name: _oof(d, cols, y, params) for name, (d, cols) in variants.items()}
    df_base = variants["base"][0]
    oof["ii"] = exclude_stores(oof["i"], df_base, trunc_stores)
    oof["base_ex_trunc"] = exclude_stores(oof["base"], df_base, trunc_stores)

    # clean: 절단 점포를 학습·평가 모두에서 뺀 표본으로 다시 rolling OOF (idx는 원래 df 행 번호로 되돌린다)
    keep = ~df_base["store_id"].isin(trunc_stores).to_numpy()
    rows = np.flatnonzero(keep)
    for name, src in (("base_clean", "base"), ("i_clean", "i")):
        d, cols = variants[src]
        o = _oof(d.iloc[rows].reset_index(drop=True), cols, y[rows], params)
        oof[name] = o.assign(idx=rows[o["idx"].to_numpy()])

    mean_auc = {n: float(train_detect.metrics_by_origin(o)["auc"].mean()) for n, o in oof.items()}
    pooled = {n: pooled_auc(o) for n, o in oof.items()}
    by_origin = pd.concat([train_detect.metrics_by_origin(o)[["origin", "auc", "ap"]].assign(scenario=n)
                           for n, o in oof.items()], ignore_index=True)

    stores = df_base["store_id"].to_numpy()
    cis = {}
    for key, (a, b), s in (("i_vs_iii", ("i", "iii"), 0), ("flag_vs_base", ("flag", "base"), 1),
                           ("i_vs_base", ("i", "base"), 2), ("clean_i_vs_base", ("i_clean", "base_clean"), 3)):
        ya, pa, pb, idx = _align(oof[a], oof[b])
        cis[key] = bootstrap_auc_diff(ya, pa, pb, stores[idx], n_boot=n_boot, seed=seed + s)
    return {"mean_auc": mean_auc, "pooled_auc": pooled, "ci": cis, "by_origin": by_origin, "oof": oof}


SCENARIOS = ("base", "base_ex_trunc", "flag", "i", "ii", "iii", "base_clean", "i_clean")


def run(master_path: Path, online_na_path: Path, online_lb_path: Path, qa_path: Path, out_dir: Path,
       *, n_boot: int = 1000) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    from src.data.online_features import load_qa

    df = train_detect.load_master(master_path)
    y = df["event_12m"].to_numpy().astype(int)
    online_na = pd.read_parquet(online_na_path)
    online_lb = pd.read_parquet(online_lb_path)
    trunc_stores = truncated_store_ids(load_qa(qa_path))
    comp = any_na_composition(online_na, trunc_stores)
    print(f"'하나라도 NA' 행 {comp['any_na_rows']:,}: 절단 점포 {comp['truncated_store']:,} · 언급 없음 "
          f"{comp['never_mentioned']:,} · 미수집/오류 {comp['all_na_qa_missing_or_error']:,} · 기타 {comp['other']:,}")
    variants = build_feature_variants(df, online_na, online_lb, trunc_stores)
    flag_rate = float(variants["flag"][0][TRUNC_FLAG].mean())

    results, by_origin = {}, []
    for pname, params in (("현 설정", None), ("튜닝 (0.03, 15)", train_detect.TUNED_PARAMS)):
        print(f"[{pname}] rolling OOF ×6 (base/flag/i/iii/base_clean/i_clean)")
        r = sensitivity_report(variants, y, trunc_stores, params=params, n_boot=n_boot, seed=20260929)
        results[pname] = {k: v for k, v in r.items() if k not in ("oof", "by_origin")}
        by_origin.append(r["by_origin"].assign(params=pname))
        print(f"  합산 AUC: base {r['pooled_auc']['base']:.4f} / 절단 플래그 {r['pooled_auc']['flag']:.4f} / "
              f"i {r['pooled_auc']['i']:.4f} / iii {r['pooled_auc']['iii']:.4f} / clean base {r['pooled_auc']['base_clean']:.4f} "
              f"→ enriched {r['pooled_auc']['i_clean']:.4f}")
        for k, v in r["ci"].items():
            print(f"  {k}: {v['diff']:+.4f} [{v['ci_low']:+.4f}, {v['ci_high']:+.4f}]")

    meta = {"n_trunc_stores": len(trunc_stores), "trunc_flag_rate": flag_rate, "any_na_composition": comp,
            "n_boot": n_boot, **results}
    (out_dir / "truncation_sensitivity.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                                                         encoding="utf-8")
    rows = [{"params": p, "scenario": s, "mean_auc": r["mean_auc"][s], "pooled_auc": r["pooled_auc"][s]}
            for p, r in results.items() for s in SCENARIOS]
    pd.DataFrame(rows).to_csv(out_dir / "truncation_sensitivity_table.csv", index=False)
    pd.concat(by_origin, ignore_index=True).to_csv(out_dir / "truncation_sensitivity_by_origin.csv", index=False)
    return results


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#33 절단 결측 경로 민감도(rolling OOF: i/ii/iii, 절단 전용 플래그, clean)")
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
