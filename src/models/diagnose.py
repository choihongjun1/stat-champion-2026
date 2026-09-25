# -*- coding: utf-8 -*-
"""W2-3 Stage 2 진단 — 점포별 예측 위험도를 위험요인(factor)별 기여로 나눈다.

방법: 요인 단위 정확 Shapley 값 (interventional, 배경 표본 기준)
- 요인(factor)은 feature 묶음이다 (`FACTORS`). 한 요인에 속한 feature는 함께 켜고 끈다.
- 가치함수 v(S) = 배경 표본 b에 대해 평균한 f(x_S, b_{-S}) — S에 속한 요인은 이 점포 값, 나머지는 배경 값.
- 요인 수 F ≤ 10이라 2^F 조합을 전부 계산해 **근사 없이** Shapley 값을 낸다.
- 확률 척도다: Σ 기여 = f(x) − E_b[f(b)] (정확히 성립, 테스트로 확인).

SHAP 라이브러리(TreeExplainer)를 쓰지 않는 이유: HistGradientBoosting의 범주형 분기를 해석하지 못해
기여 합이 예측과 맞지 않는다 (shap 0.51 실측: 합 오차 최대 7.8 log-odds). 이 방식은 모형 종류와 무관하다.

해석 경계 (DECISIONS.md 2026-09-10, CLAUDE.md): 기여도는 **예측이 어떤 관측 특성에서 비롯됐는지**의
분해이지 인과효과가 아니다. "이 요인을 바꾸면 위험이 내려간다"는 표현은 Stage 3 검증을 거친 요인에만 쓴다.

실행:
    python -m src.models.diagnose --online outputs/online/online_features.parquet --primary enriched
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config
from src.models import detect, features, splits, train_detect

# ---------------------------------------------------------------------------
# 요인 매핑표 (초안) — category ∈ ANALYSIS_PLAN §2 4개 유형, actionability ∈ owner / policy / external
# ---------------------------------------------------------------------------
FACTORS: list[dict] = [
    {"id": "tenure", "name": "업력", "category": "사업체 구조", "actionability": "external",
     "features": ["age_months"],
     "note": "영업 기간. 바꿀 수 없는 속성이라 위험 배경 설명에만 쓴다"},
    {"id": "store_profile", "name": "업종·점포 규모", "category": "사업체 구조", "actionability": "external",
     "features": ["biz_type", "area", "has_coord"],
     "note": "인허가 업종과 소재지 면적. has_coord는 인허가 정보 완결성 지표라 여기에 묶는다"},
    {"id": "district", "name": "자치구", "category": "입지·수요", "actionability": "external",
     "features": ["gu"], "note": ""},
    {"id": "trdar_population", "name": "상권 유동·배후 인구", "category": "입지·수요", "actionability": "external",
     "features": ["trdar_flow_pop", "trdar_resident_pop", "trdar_worker_pop", "trdar_facility_cnt"],
     "note": "직전 분기(T-1) 상권 단위 값"},
    {"id": "trdar_vitality", "name": "상권 변화·영업 지속", "category": "입지·수요", "actionability": "external",
     "features": ["trdar_change_index", "trdar_oper_months_avg", "trdar_close_months_avg"],
     "note": "상권변화지표와 운영·폐업 점포의 평균 영업기간"},
    {"id": "online_attention", "name": "온라인 언급(블로그)", "category": "입지·수요", "actionability": "owner",
     "features": list(features.ONLINE_PREDICTORS),
     "note": "고객 관심·노출. 사업자가 직접 관리할 수 있는 유일한 요인 — 개선 효과는 Stage 3 검증 전까지 주장하지 않는다"},
    {"id": "peer_competition", "name": "동종 업종 경쟁·개폐업", "category": "경쟁", "actionability": "external",
     "features": ["trdar_biz_store_cnt_observed", "trdar_biz_franchise_cnt_observed",
                  "trdar_biz_open_rate_observed", "trdar_biz_close_rate_observed"],
     "note": "같은 상권·같은 업종 그룹의 점포 수·프랜차이즈 수·개폐업률 (T-1)"},
    {"id": "peer_sales", "name": "동종 업종 매출 수준", "category": "경쟁", "actionability": "external",
     "features": ["trdar_biz_sales_amt_observed", "trdar_biz_sales_per_store_observed"],
     "note": "매출 공개 코드 기준 부분관측치(하한)"},
    {"id": "rent_level", "name": "임대료 수준(공시지가)", "category": "비용", "actionability": "policy",
     "features": ["land_price"],
     "note": "임대료 대리 지표. 시간 분할 학습에 값이 들어가지 못해 현재는 기여 0 (DECISIONS.md 2026-09-25)"},
]
CATEGORIES = ("입지·수요", "경쟁", "비용", "사업체 구조")
ACTIONABILITY = ("owner", "policy", "external")
AGE_BANDS = [(-1, 12, "1년 미만"), (12, 36, "1~3년"), (36, 60, "3~5년"), (60, 120, "5~10년"), (120, 10**6, "10년 이상")]
MIN_PEER_N = 30
PEER_SENTENCE_MIN = 70  # peer 비교 문장은 상위 30% 이내일 때만 붙인다 ("상위 85%" 같은 문장은 오해를 부른다)


def factor_table() -> pd.DataFrame:
    """PR 첨부용 매핑표."""
    rows = []
    for f in FACTORS:
        for c in f["features"]:
            rows.append({"factor_id": f["id"], "factor": f["name"], "category": f["category"],
                         "actionability": f["actionability"], "feature": c, "note": f["note"]})
    return pd.DataFrame(rows)


def check_mapping(predictors) -> None:
    """모든 predictor가 정확히 한 요인에 속하는지 확인한다."""
    mapped = [c for f in FACTORS for c in f["features"]]
    dup = sorted({c for c in mapped if mapped.count(c) > 1})
    missing = sorted(set(predictors) - set(mapped))
    bad = [f["id"] for f in FACTORS if f["category"] not in CATEGORIES or f["actionability"] not in ACTIONABILITY]
    if dup or missing or bad:
        raise ValueError(f"요인 매핑 오류 — 중복 {dup} / 미매핑 predictor {missing} / 잘못된 유형·A/P/N {bad}")


# ---------------------------------------------------------------------------
# 요인 단위 정확 Shapley
# ---------------------------------------------------------------------------
def _shapley_weights(F: int) -> np.ndarray:
    return np.array([math.factorial(s) * math.factorial(F - s - 1) / math.factorial(F) for s in range(F)])


def factor_shapley(model: detect.DetectModel, X: pd.DataFrame, background: pd.DataFrame,
                   factor_cols: list[list[str]], *, chunk: int = 2000) -> tuple[np.ndarray, float]:
    """반환: (n × F 기여 행렬, base value = E_b f(b)). 확률 척도.

    factor_cols[k] = k번째 요인의 입력 컬럼(모형이 실제로 쓰는 컬럼만). 빈 요인은 넘기지 않는다.
    """
    F = len(factor_cols)
    if F > 12:
        raise ValueError("요인이 너무 많다 (2^F 조합)")
    K = len(background)
    B = background.reset_index(drop=True)
    base_value = float(model.predict_proba(B).mean())
    w = _shapley_weights(F)
    masks = np.arange(2 ** F)
    size = np.array([bin(m).count("1") for m in masks])
    out = np.zeros((len(X), F))
    for s0 in range(0, len(X), chunk):
        Xc = X.iloc[s0:s0 + chunk].reset_index(drop=True)
        n = len(Xc)
        big_x = Xc.iloc[np.repeat(np.arange(n), K)].reset_index(drop=True)
        big_b = B.iloc[np.tile(np.arange(K), n)].reset_index(drop=True)
        v = np.empty((n, len(masks)))
        for m in masks:
            df = big_x.copy()
            for k in range(F):
                if not (m >> k) & 1:
                    for c in factor_cols[k]:
                        df[c] = big_b[c]
            v[:, m] = model.predict_proba(df).reshape(n, K).mean(axis=1)
        for k in range(F):
            without = masks[(masks >> k) & 1 == 0]
            out[s0:s0 + n, k] = (w[size[without]] * (v[:, without | (1 << k)] - v[:, without])).sum(axis=1)
    return out, base_value


# ---------------------------------------------------------------------------
# peer 백분위
# ---------------------------------------------------------------------------
def age_band(age_months: pd.Series) -> pd.Series:
    out = pd.Series("미상", index=age_months.index, dtype=object)
    for lo, hi, name in AGE_BANDS:
        out[(age_months > lo) & (age_months <= hi)] = name
    return out


def add_peer_percentiles(long: pd.DataFrame, min_n: int = MIN_PEER_N) -> pd.DataFrame:
    """요인별 기여를 peer group 안의 백분위로 바꾼다. 100에 가까울수록 이 요인이 비슷한 점포보다 위험을 더 올린다.

    peer = 같은 origin·업종·자치구·업력 구간. 표본이 min_n 미만이면 자치구 → 업력 구간 순으로 조건을 푼다.
    """
    levels = [["origin", "biz_type", "gu", "age_band"], ["origin", "biz_type", "age_band"], ["origin", "biz_type"]]
    long = long.copy()
    long["peer_percentile"] = np.nan
    long["peer_level"] = ""
    long["peer_n"] = 0
    todo = pd.Series(True, index=long.index)
    for keys in levels:
        # 상위 단계 peer는 아직 배정되지 않은 점포만이 아니라 그 조건에 맞는 전체 점포다.
        g = long.groupby(keys + ["factor_id"], observed=True)["contribution"]
        n = g.transform("size")
        pct = g.rank(pct=True, method="average") * 100
        take = todo & (n >= min_n)
        long.loc[take, "peer_percentile"] = pct[take].round()
        long.loc[take, "peer_level"] = "·".join(k for k in keys if k != "origin")
        long.loc[take, "peer_n"] = n[take]
        todo &= ~take
    long["peer_percentile"] = long["peer_percentile"].astype("Int64")
    return long


# ---------------------------------------------------------------------------
# 진단문 (W2-5 스키마 factors[])
# ---------------------------------------------------------------------------
def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침 유무로 조사를 고른다. 괄호 부분은 무시한다 (예: '온라인 언급(블로그)' → '언급' 기준)."""
    core = word.split("(")[0].strip() or word
    ch = core[-1]
    if "가" <= ch <= "힣":
        return word + (with_final if (ord(ch) - 0xAC00) % 28 else without_final)
    return word + with_final + "(" + without_final + ")"


def explanation(row) -> str:
    pp = abs(row["contribution"]) * 100
    peer = {"biz_type·gu·age_band": "같은 업종·자치구·업력대", "biz_type·age_band": "같은 업종·업력대",
            "biz_type": "같은 업종"}.get(row["peer_level"], "비슷한 점포")
    if abs(row["contribution"]) < 0.001:
        return f"{_josa(row['factor'], '은', '는')} 이 점포의 예측 위험도에 거의 영향을 주지 않았습니다."
    way = "높이는" if row["contribution"] > 0 else "낮추는"
    s = f"{_josa(row['factor'], '이', '가')} 예측 위험도를 약 {pp:.1f}%p {way} 쪽으로 기여했습니다."
    if pd.notna(row["peer_percentile"]) and row["contribution"] > 0 and row["peer_percentile"] >= PEER_SENTENCE_MIN:
        s += f" {peer} 점포 중 이 요인의 위험 기여가 상위 {max(1, 100 - int(row['peer_percentile']))}% 수준입니다."
    return s


def _plain(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return round(float(v), 4)
    return str(v)


def factors_json(long: pd.DataFrame, store_id: str, origin: str, values: dict | None = None) -> list[dict]:
    """values: {factor_id: {feature: 값}} — 화면이 "무엇을 보고 이렇게 판단했는지"를 함께 보여줄 수 있게 한다."""
    g = long[(long["store_id"] == store_id) & (long["origin"] == origin)].sort_values("contribution", ascending=False)
    values = values or {}
    return [{
        "category": r["category"], "name": r["factor"], "factor_id": r["factor_id"],
        "contribution": round(float(r["contribution"]), 4),
        "direction": "위험 증가" if r["contribution"] > 0 else "위험 감소",
        "peer_percentile": None if pd.isna(r["peer_percentile"]) else int(r["peer_percentile"]),
        "actionability": r["actionability"], "explanation": r["explanation"],
        "values": {k: _plain(v) for k, v in values.get(r["factor_id"], {}).items()},
    } for _, r in g.iterrows()]


# ---------------------------------------------------------------------------
def run(master_path: Path, out_dir: Path, *, online_path: Path | None, primary: str, origin: str | None,
        n_background: int, max_stores: int | None, seed: int = 20260925) -> pd.DataFrame:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    df = train_detect.load_master(master_path)
    if online_path is not None:
        df = train_detect.attach_online(df, online_path)
    cols = features.select_features(df.columns, primary)
    check_mapping(cols)
    X = features.build_X(df, cols)
    y = df["event_12m"].to_numpy().astype(int)
    origins = splits.sorted_origins(df)
    origin = origin or origins[-1]
    t = origins.index(origin)
    tr = df["origin"].isin(origins[: t - train_detect.EMBARGO]).to_numpy()
    te_idx = np.flatnonzero((df["origin"] == origin).to_numpy())
    rng = np.random.default_rng(seed)
    if max_stores and len(te_idx) > max_stores:
        te_idx = np.sort(rng.choice(te_idx, max_stores, replace=False))
    train_detect.log(f"진단 대상 {origin} {len(te_idx):,}점포 · 학습 {origins[0]}~{origins[t - train_detect.EMBARGO - 1]} "
                     f"· feature set {primary}")
    model = detect.DetectModel().fit(X[tr], y[tr])

    active = [f for f in FACTORS if any(c in model.columns_ for c in f["features"])]
    dropped = [f["id"] for f in FACTORS if f not in active]
    factor_cols = [[c for c in f["features"] if c in model.columns_] for f in active]
    bg_idx = rng.choice(np.flatnonzero(tr), n_background, replace=False)
    Xt, Xb = X.iloc[te_idx], X.iloc[bg_idx]
    train_detect.log(f"요인 {len(active)}개 (학습에 값이 없어 제외: {dropped or '없음'}) · 배경 {n_background}개 · "
                     f"조합 {2 ** len(active)}개")
    phi, base = factor_shapley(model, Xt, Xb, factor_cols)
    p = model.predict_proba(Xt)
    gap = np.abs(phi.sum(axis=1) + base - p).max()
    train_detect.log(f"가법성 확인: max|Σ기여 + base − p| = {gap:.2e}")
    if gap > 1e-6:
        raise RuntimeError("Shapley 가법성이 깨졌다")

    meta = df.iloc[te_idx][["store_id", "origin", "biz_type", "gu", "age_months"]].reset_index(drop=True)
    meta["age_band"] = age_band(meta["age_months"])
    meta["probability_12m"] = p
    meta["base_value"] = base
    long = []
    for k, f in enumerate(active):
        part = meta[["store_id", "origin", "biz_type", "gu", "age_band"]].copy()
        part["factor_id"], part["factor"], part["category"], part["actionability"] = (
            f["id"], f["name"], f["category"], f["actionability"])
        part["contribution"] = phi[:, k]
        long.append(part)
    long = pd.concat(long, ignore_index=True)
    long = add_peer_percentiles(long)
    long["rank_in_store"] = long.groupby(["store_id", "origin"])["contribution"].rank(ascending=False, method="first").astype(int)
    long["explanation"] = long.apply(explanation, axis=1)
    long.to_parquet(out_dir / "diagnosis.parquet", index=False)

    cat = long.groupby(["store_id", "origin", "category"])["contribution"].sum().unstack(fill_value=0.0)
    cat = cat.reindex(columns=list(CATEGORIES), fill_value=0.0).reset_index()
    # 활성 요인이 하나도 없는 유형(현재 비용)은 0이 아니라 "판단 불가"다. 화면이 0으로 그리지 않도록 표시한다.
    for c in CATEGORIES:
        cat[f"{c}_available"] = any(f["category"] == c for f in active)
    cat = cat.merge(meta[["store_id", "origin", "probability_12m", "base_value"]], on=["store_id", "origin"])
    cat.to_parquet(out_dir / "diagnosis_by_category.parquet", index=False)

    summary = long.groupby(["category", "factor_id", "factor", "actionability"]).agg(
        mean_abs=("contribution", lambda s: s.abs().mean()), mean=("contribution", "mean"),
        share_top1=("rank_in_store", lambda s: (s == 1).mean()),
    ).sort_values("mean_abs", ascending=False).reset_index()
    summary.to_csv(out_dir / "factor_summary.csv", index=False)
    factor_table().to_csv(out_dir / "factor_map.csv", index=False)

    # 샘플 진단문 10건: high 등급 쪽에서 5건, 나머지 5건
    order = meta.sort_values("probability_12m", ascending=False)
    pick = pd.concat([order.head(5), order.sample(5, random_state=seed)])
    raw = df.iloc[te_idx].reset_index(drop=True)
    pos = {(s_, o_): i for i, (s_, o_) in enumerate(zip(raw["store_id"], raw["origin"]))}

    def vals(store_id, origin_):
        row = raw.iloc[pos[(store_id, origin_)]]
        return {f["id"]: {c: row[c] for c in f["features"] if c in raw.columns} for f in active}

    sample = [{"store_id": r.store_id, "origin": r.origin, "probability_12m": round(float(r.probability_12m), 4),
               "base_value": round(base, 4),
               "unavailable_categories": [c for c in CATEGORIES if not any(f["category"] == c for f in active)],
               "factors": factors_json(long, r.store_id, r.origin, vals(r.store_id, r.origin)),
               "disclaimer": "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."}
              for r in pick.itertuples()]
    (out_dir / "sample_factors.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")

    train_detect.log("\n" + summary.round(4).to_string(index=False))
    train_detect.log(f"완료 ({time.time() - t0:.0f}초) → {out_dir}")
    return long


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-3 요인별 진단")
    ap.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    ap.add_argument("--online", type=Path, default=None)
    ap.add_argument("--primary", default="base")
    ap.add_argument("--origin", default=None, help="진단할 origin (기본: 마지막)")
    ap.add_argument("--n-background", type=int, default=16)
    ap.add_argument("--max-stores", type=int, default=None, help="동작 확인용 표본 점포 수")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    out = a.out or (config.REPO_ROOT / "outputs" / "models" / f"diagnosis_{a.primary}")
    run(a.master, out, online_path=a.online, primary=a.primary, origin=a.origin,
        n_background=a.n_background, max_stores=a.max_stores)


if __name__ == "__main__":
    main()
