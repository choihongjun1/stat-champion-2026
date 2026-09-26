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


def online_driver_text(feature: str, v) -> str:
    """온라인 요인 안에서 가장 크게 작용한 신호를 사람이 읽는 말로 바꾼다 (관측값 서술, 인과 표현 없음)."""
    if v is None or pd.isna(v):
        return "블로그 언급 이력 없음" if feature == "online_blog_months_since_last" else "관측 불가(검색 결과 상한)"
    v = float(v)
    if feature == "online_blog_trend_6m":
        if v < 0:
            return f"최근 6개월 블로그 언급이 그 전 6개월보다 {abs(int(v))}건 줄어듦"
        if v > 0:
            return f"최근 6개월 블로그 언급이 그 전 6개월보다 {int(v)}건 늘어남"
        return "최근 1년 블로그 언급 수 변화 없음"
    if feature == "online_blog_cnt_12m":
        return f"최근 12개월 블로그 언급 {int(v)}건"
    if feature == "online_blog_cnt_3m":
        return f"최근 3개월 블로그 언급 {int(v)}건"
    if feature == "online_blog_has_12m":
        return "최근 12개월 블로그 언급 있음" if v > 0 else "최근 12개월 블로그 언급 없음"
    if feature == "online_blog_has_ever":
        return "과거 블로그 언급 이력 있음" if v > 0 else "블로그 언급 이력 없음"
    if feature == "online_blog_months_since_last":
        return "이번 달에도 블로그 언급 있음" if v == 0 else f"마지막 블로그 언급 이후 {int(v)}개월"
    return feature


def online_signal_is_presence(feature: str, v) -> bool:
    """주된 근거가 '언급이 있음/많음/늘어남'인지. 부재·감소·오래 끊김이면 False."""
    if v is None or pd.isna(v):
        return False
    v = float(v)
    if feature in ("online_blog_cnt_3m", "online_blog_cnt_12m", "online_blog_has_12m", "online_blog_has_ever"):
        return v > 0
    if feature == "online_blog_trend_6m":
        return v > 0
    if feature == "online_blog_months_since_last":
        return v <= 3
    return False


def online_drivers(model, Xt: pd.DataFrame, Xb: pd.DataFrame, online_cols: list[str],
                   other_cols: list[str], sign: np.ndarray) -> tuple[list[str], np.ndarray]:
    """온라인 요인 안에서 가장 크게 작용한 feature를 고른다.

    나머지 요인 전체를 한 참가자로 묶고 온라인 feature 각각을 참가자로 둔 Shapley(2^(1+m) 조합)를 계산해,
    온라인 요인 기여와 같은 방향으로 가장 큰 feature를 고른다. 설명문용이며 요인 기여값 자체는 바꾸지 않는다.
    """
    sub, _ = factor_shapley(model, Xt, Xb, [other_cols] + [[c] for c in online_cols])
    sub = sub[:, 1:] * sign[:, None]
    k = sub.argmax(axis=1)
    return [online_cols[i] for i in k], sub.max(axis=1)


MISSING_NOTE = "이 점포에는 해당 데이터가 없음 — 기여는 값이 아니라 '데이터 없음' 자체에서 나와 표시 보류"
# 데이터 없음 이유 (점포별). 상권 요인은 trdar_cd(provenance, predictor 아님)로 상권 밖/안을 가른다.
TRDAR_FACTOR_IDS = ("trdar_population", "trdar_vitality", "peer_competition", "peer_sales")
REASON_OUTSIDE = "상권 경계 밖"
REASON_TRDAR_UNKNOWN = "상권 데이터 없음"  # raw에 trdar_cd가 없어 상권 밖인지 판단할 수 없을 때
REASON_INSIDE = {"trdar_population": "해당 분기 상권 자료 없음", "trdar_vitality": "해당 분기 상권 자료 없음",
                 "peer_competition": "해당 상권에 이 업종 자료 없음",
                 "peer_sales": "해당 상권에 이 업종 매출 공개 자료 없음"}
REASON_ONLINE = "관측 불가"
REASON_DEFAULT = "데이터 없음"
# 화면이 설명문을 파싱하지 않고 분기하도록 내보내는 코드값 (factors[].missing_reason). 사유 문구와 1:1.
MISSING_REASON_CODES = {
    REASON_OUTSIDE: "out_of_trdar",                                   # 상권 경계 밖
    REASON_INSIDE["peer_sales"]: "sales_unpublished",                 # 상권 안, 이 업종 매출 공개 자료 없음
    REASON_INSIDE["peer_competition"]: "industry_unpublished",        # 상권 안, 이 업종 자료 없음
    REASON_INSIDE["trdar_population"]: "trdar_quarter_unavailable",   # 상권 안, 해당 분기 상권 자료 없음
    REASON_ONLINE: "online_unobservable",                             # 온라인 관측 불가
    REASON_TRDAR_UNKNOWN: "trdar_unknown",                            # trdar_cd가 없어 상권 밖인지 판단 불가
    REASON_DEFAULT: "unknown",                                        # 예비값
}
# display=false인 이유 (factors[].hold_reason). 새 보류 사유가 생기면 여기에 추가한다.
HOLD_REASONS = {
    "online_review": "온라인 언급이 많은 쪽에서 위험이 높게 나와 상호 오탐(#28)·유행 상권 효과 검토 전까지 보류",
    "data_missing": "요인에 속한 값이 이 점포에서 전부 결측 — 세부 사유는 missing_reason",
}
# 개별 요인의 방향 표시 기준 (설명문의 "거의 영향을 주지 않았습니다"와 같은 기준). 샘플 추출의
# no_standout(가장 큰 위험 기여 < 1%p)과는 다른 값이다.
DIRECTION_EPS = 0.001
DIRECTION_NEGLIGIBLE = "영향 미미"


def direction_label(contribution: float) -> str:
    if abs(contribution) < DIRECTION_EPS:
        return DIRECTION_NEGLIGIBLE
    return "위험 증가" if contribution > 0 else "위험 감소"


def missing_reasons(factor_id: str, raw: pd.DataFrame) -> np.ndarray:
    """요인 feature가 전부 결측인 이유를 점포(raw 행)별로 고른다."""
    n = len(raw)
    if factor_id == "online_attention":
        return np.full(n, REASON_ONLINE, dtype=object)
    if factor_id not in TRDAR_FACTOR_IDS:
        return np.full(n, REASON_DEFAULT, dtype=object)
    if "trdar_cd" not in raw.columns:
        return np.full(n, REASON_TRDAR_UNKNOWN, dtype=object)
    inside = raw["trdar_cd"].notna().to_numpy()
    return np.where(inside, REASON_INSIDE[factor_id], REASON_OUTSIDE).astype(object)


def explanation(row) -> str:
    pp = abs(row["contribution"]) * 100
    if row.get("data_missing"):
        why = row.get("missing_reason") or REASON_DEFAULT
        return f"이 점포는 {row['factor']} 데이터가 없어({why}) 이 요인은 진단하지 않습니다."
    peer = {"biz_type·gu·age_band": "같은 업종·자치구·업력대", "biz_type·age_band": "같은 업종·업력대",
            "biz_type": "같은 업종"}.get(row["peer_level"], "비슷한 점포")
    if abs(row["contribution"]) < DIRECTION_EPS:
        return f"{_josa(row['factor'], '은', '는')} 이 점포의 예측 위험도에 거의 영향을 주지 않았습니다."
    way = "높이는" if row["contribution"] > 0 else "낮추는"
    s = f"{_josa(row['factor'], '이', '가')} 예측 위험도를 약 {pp:.1f}%p {way} 쪽으로 기여했습니다."
    if row.get("driver_text"):
        s += f" 주된 근거: {row['driver_text']}."
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
        "direction": direction_label(r["contribution"]),
        "peer_percentile": None if pd.isna(r["peer_percentile"]) else int(r["peer_percentile"]),
        "actionability": r["actionability"], "explanation": r["explanation"],
        "driver": r["driver_text"] or None,
        "display": bool(r["display"]), "display_note": r["display_note"] or None,
        "data_missing": bool(r.get("data_missing", False)),
        "missing_reason": r.get("missing_reason_code") or None,
        "hold_reason": r.get("hold_reason") or None,
        "values": {k: _plain(v) for k, v in values.get(r["factor_id"], {}).items()},
    } for _, r in g.iterrows()]


# ---------------------------------------------------------------------------
def explain(model: detect.DetectModel, Xt: pd.DataFrame, Xb: pd.DataFrame, meta: pd.DataFrame,
            raw: pd.DataFrame) -> dict:
    """학습된 모형 하나로 대상 점포들의 요인 진단을 만든다 (진단·서빙 공용).

    meta: 대상 행의 store_id, origin, biz_type, gu, age_months (Xt와 같은 순서)
    raw : 대상 행의 원래 값 (판단 근거 `values` 출력용, Xt와 같은 순서)
    반환: long(점포×요인), by_category, active(요인 목록), base, meta(확률 포함), values(함수)
    """
    meta = meta.reset_index(drop=True).copy()
    raw = raw.reset_index(drop=True)
    active = [f for f in FACTORS if any(c in model.columns_ for c in f["features"])]
    dropped = [f["id"] for f in FACTORS if f not in active]
    factor_cols = [[c for c in f["features"] if c in model.columns_] for f in active]
    train_detect.log(f"요인 {len(active)}개 (학습에 값이 없어 제외: {dropped or '없음'}) · 배경 {len(Xb)}개 · "
                     f"조합 {2 ** len(active)}개")
    phi, base = factor_shapley(model, Xt, Xb, factor_cols)
    p = model.predict_proba(Xt)
    gap = np.abs(phi.sum(axis=1) + base - p).max()
    train_detect.log(f"가법성 확인: max|Σ기여 + base − p| = {gap:.2e}")
    if gap > 1e-6:
        raise RuntimeError("Shapley 가법성이 깨졌다")

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
    long["driver_feature"], long["driver_text"] = "", ""
    long["display"], long["display_note"], long["hold_reason"] = True, "", ""
    on = next((k for k, f in enumerate(active) if f["id"] == "online_attention"), None)
    if on is not None:
        online_cols = factor_cols[on]
        other_cols = [c for k, cs in enumerate(factor_cols) if k != on for c in cs]
        sign = np.where(phi[:, on] >= 0, 1.0, -1.0)
        train_detect.log(f"온라인 요인 세부 근거 계산 (참가자 {1 + len(online_cols)}명)")
        drv, _ = online_drivers(model, Xt, Xb, online_cols, other_cols, sign)
        vals = Xt[online_cols].reset_index(drop=True)
        texts = [online_driver_text(f, vals.at[i, f]) for i, f in enumerate(drv)]
        m = (long["factor_id"] == "online_attention").to_numpy()
        long.loc[m, "driver_feature"] = drv
        long.loc[m, "driver_text"] = texts
        # 화면 표시 보류: 온라인 요인이 위험을 올리는데 주된 근거가 "언급이 있음/많음"인 경우.
        # 사업자가 할 수 있는 일로 번역되지 않고(언급을 줄이라는 뜻이 아니다), 이름 오탐(#28)이나
        # 유행 상권 인기 점포 효과일 수 있어 검증 전까지 진단문에 내보내지 않는다. 기여값은 그대로 둔다.
        presence = np.array([online_signal_is_presence(f, vals.at[i, f]) for i, f in enumerate(drv)])
        hold = presence & (phi[:, on] > 0)
        idx = np.flatnonzero(m)
        long.loc[idx[hold], "display"] = False
        long.loc[idx[hold], "hold_reason"] = "online_review"
        long.loc[idx[hold], "display_note"] = "언급이 많은 쪽에서 위험이 높게 나온 경우 — 이름 오탐(#28)·유행 상권 효과 검토 전까지 표시 보류"
        train_detect.log(f"온라인 요인 표시 보류: {int(hold.sum()):,}점포 (위험을 올리고 주된 근거가 언급 있음/많음)")
    # 데이터 없음 표시 보류: 요인에 속한 feature 값이 이 점포에서 전부 결측이면(예: 상권 경계 밖 점포의
    # 상권 요인) 기여는 "값"이 아니라 "데이터가 없다는 사실"(= 상권 밖 위치)에서 나온다. "동종 업종 경쟁이
    # 위험을 낮췄다" 같은 문장은 사실과 다르므로 진단문으로 내보내지 않는다. 기여값·가법성은 그대로 둔다.
    # 이유는 점포별로 다르다 (상권 밖 / 상권 안이지만 해당 업종 자료·매출 공개 없음 / 온라인 관측 불가).
    long["data_missing"] = False
    long["missing_reason"], long["missing_reason_code"] = "", ""
    n = len(meta)
    for k, f in enumerate(active):
        allna = Xt[factor_cols[k]].isna().all(axis=1).to_numpy()
        if not allna.any():
            continue
        idx = np.flatnonzero((long["factor_id"] == f["id"]).to_numpy())[allna]
        why = missing_reasons(f["id"], raw)[allna]
        long.loc[idx, ["data_missing", "display"]] = [True, False]
        long.loc[idx, "display_note"] = MISSING_NOTE
        long.loc[idx, "missing_reason"] = why
        long.loc[idx, "missing_reason_code"] = [MISSING_REASON_CODES[w] for w in why]
        long.loc[idx, "hold_reason"] = "data_missing"
        by_reason = pd.Series(why).value_counts().to_dict()
        train_detect.log(f"데이터 없음 표시 보류 [{f['id']}]: {int(allna.sum()):,} / {n:,}점포 {by_reason}")
    unknown_hold = set(long["hold_reason"]) - set(HOLD_REASONS) - {""}
    if unknown_hold or ((~long["display"]) != (long["hold_reason"] != "")).any():
        raise RuntimeError(f"display=false ⇔ hold_reason 규칙이 깨졌다 (미등록 사유: {unknown_hold or '없음'})")
    long = add_peer_percentiles(long)
    long["rank_in_store"] = long.groupby(["store_id", "origin"])["contribution"].rank(ascending=False, method="first").astype(int)
    long["explanation"] = long.apply(explanation, axis=1)
    cat = long.groupby(["store_id", "origin", "category"])["contribution"].sum().unstack(fill_value=0.0)
    cat = cat.reindex(columns=list(CATEGORIES), fill_value=0.0).reset_index()
    # 활성 요인이 하나도 없는 유형(현재 비용)은 0이 아니라 "판단 불가"다. 화면이 0으로 그리지 않도록 표시한다.
    for c in CATEGORIES:
        cat[f"{c}_available"] = any(f["category"] == c for f in active)
    cat = cat.merge(meta[["store_id", "origin", "probability_12m", "base_value"]], on=["store_id", "origin"])
    pos = {(s_, o_): i for i, (s_, o_) in enumerate(zip(raw["store_id"], raw["origin"]))}

    def values(store_id, origin_):
        row = raw.iloc[pos[(store_id, origin_)]]
        return {f["id"]: {c: row[c] for c in f["features"] if c in raw.columns} for f in active}

    return {"long": long, "by_category": cat, "active": active, "base": base, "meta": meta, "values": values}


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

    bg_idx = rng.choice(np.flatnonzero(tr), n_background, replace=False)
    meta = df.iloc[te_idx][["store_id", "origin", "biz_type", "gu", "age_months"]].reset_index(drop=True)
    res = explain(model, X.iloc[te_idx], X.iloc[bg_idx], meta, df.iloc[te_idx].reset_index(drop=True))
    long, cat, active, base, meta = res["long"], res["by_category"], res["active"], res["base"], res["meta"]
    long.to_parquet(out_dir / "diagnosis.parquet", index=False)

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
    vals = res["values"]
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
