# -*- coding: utf-8 -*-
"""인허가 ↔ 소진공 Entity Resolution (단계적 cascade).

실행:
    python -m src.data.matching

전제: outputs/standardized/licenses_3gu.parquet (첫 PR 산출물).
소진공 panel/entities는 없으면 이 스크립트가 재생성한다.

원칙:
- 인허가 전체 점포가 모집단. unmatched 행을 절대 삭제하지 않는다.
- 소진공 7개 스냅샷 union은 entity resolution 용도로만 사용한다 (DECISIONS).
  어떤 스냅샷의 상호/PNU와 일치해도 '같은 점포인가'의 근거일 뿐이며,
  스냅샷별 관측값을 feature로 붙이는 작업은 이 모듈에서 하지 않는다.
- 하나의 fuzzy rule로 일괄 처리하지 않고 tier cascade로 처리하며,
  각 매칭에 provenance(방법·score·후보 수·거리)를 저장한다.
- 후보가 복수이고 구분 근거가 없으면 자동 병합하지 않고 ambiguous로 보존한다.
- '필지 내 후보 1개면 무조건 매칭' 규칙은 사용하지 않는다. 후보 유일성은
  candidate_count로 기록되는 evidence일 뿐, 이름 근거 없는 자동 매칭은 없다.
- 좌표 단독 매칭 금지: tier 4도 이름 일치(threshold 이상)를 요구한다.

규칙 상태 (docs/DECISIONS.md 2026-09-19):
- Tier4 최종 규칙: 반경 30m + 상호 exact/containment + name_score 0.75 이상.
  검증 표본과 변경 13행 수작업 검토로 확정.
- Tier3 threshold 0.75는 잠정 유지. 혼잡 PNU는 crowded_pnu 표시만 한다.
- 인허가 영업기간과 소진공 관측구간의 겹침은 ER에서 검사하지 않는다 (B-3 판단).
- sj_status는 이 모듈에서 만들지 않는다 (B-3 파생).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from src.data import identity, semas
from src.data.config import OUTPUT_DIR, REPO_ROOT
from src.data.names import _clean_base  # 상호 정규화와 동일한 문자 정제 규칙

MATCH_DIR = REPO_ROOT / "outputs" / "matching"

# --- 매칭 파라미터 (sensitivity QA는 matching_validation.py) ---
FUZZY_THRESHOLD = 0.75      # 상호 유사도 하한: tier3 잠정값 / tier4 score 하한(확정)
FUZZY_MARGIN = 0.05         # 1위-2위 score 차이 하한 (미만이면 ambiguous)
COORD_RADIUS_M = 30.0       # tier 4 탐색 반경 (확정)
COORD_MARGIN_M = 5.0        # 1위-2위 거리 차이 하한 (미만이면 ambiguous)
FUZZY_SENS_GRID = [0.5, 0.6, 0.7, 0.75, 0.8, 0.9]
RADIUS_SENS_GRID = [10.0, 20.0, 30.0]

# Tier4 이름 구조 조건: 다른 필지의 후보는 상호가 정확히 같거나 한쪽이 다른 쪽을 포함할
# 때만 이름 일치로 본다. 0.75는 4자 상호의 1자 치환(나라헤어↔유나헤어)이 정확히 통과하는
# 하한이라, 인접 필지에서는 서로 다른 이웃 점포를 붙이는 경로가 된다.
# 검증 표본과 변경 13행 수작업 검토로 확정한 최종 규칙 (docs/DECISIONS.md 2026-09-19)
TIER4_REQUIRE_EXACT_OR_CONTAINMENT = True

# Tier3 혼잡 PNU: 후보 entity가 이 수를 넘으면 crowded_pnu로 표시한다.
# TIER3_CROWDED_MIN_SCORE를 숫자로 두면 crowded Tier3 중 그 점수 미만을 ambiguous로
# 내린다. 기본은 None(비활성) — 근거 표본이 11건뿐이라 확정하지 않았다.
TIER3_CROWDED_CC = 50
TIER3_CROWDED_MIN_SCORE: float | None = None

NAME_EXACT = "exact"
NAME_CONTAINMENT = "containment"
NAME_OTHER = "substitution_or_other"

SAMPLE_SEED = 20260916
SAMPLE_PER_GROUP = 8


# ---------------------------------------------------------------------------
# 유사도
# ---------------------------------------------------------------------------
def seq_ratio(a: str, b: str) -> float:
    """difflib SequenceMatcher ratio (주 유사도)."""
    return SequenceMatcher(None, a, b).ratio()


def name_structure(a: str, b: str) -> str:
    """두 정규화 상호의 관계: exact / containment(짧은 쪽이 긴 쪽의 부분문자열) / 그 외."""
    if a == b:
        return NAME_EXACT
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if short and short in long_:
        return NAME_CONTAINMENT
    return NAME_OTHER


def bigram_jaccard(a: str, b: str) -> float:
    """문자 bigram Jaccard (보조 유사도 — threshold 비교 검토용)."""
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    sa = {a[i:i + 2] for i in range(len(a) - 1)}
    sb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# 좌표 blocking (grid hash) — Cartesian product 금지
# ---------------------------------------------------------------------------
def build_grid(xs: np.ndarray, ys: np.ndarray, cell: float) -> dict[tuple[int, int], list[int]]:
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (x, y) in enumerate(zip(xs, ys)):
        grid[(int(x // cell), int(y // cell))].append(i)
    return grid


def neighbors_within(
    x: float, y: float, grid: dict, xs: np.ndarray, ys: np.ndarray, radius: float
) -> list[tuple[int, float]]:
    """반경 radius(m) 내 후보 index와 거리. grid cell 크기 = radius."""
    cx, cy = int(x // radius), int(y // radius)
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for i in grid.get((cx + dx, cy + dy), ()):
                d = float(np.hypot(xs[i] - x, ys[i] - y))
                if d <= radius:
                    out.append((i, d))
    return sorted(out, key=lambda t: t[1])


# ---------------------------------------------------------------------------
# 후보 테이블
# ---------------------------------------------------------------------------
def build_candidate_table(panel: pd.DataFrame, entities: pd.DataFrame) -> pd.DataFrame:
    """소진공 매칭 후보 키: 전 스냅샷에서 관측된 (ID, PNU, 상호) 고유 조합.

    상호·PNU가 스냅샷에 따라 바뀐 경우 모든 변형을 후보 키로 남긴다
    (union의 ER 목적 사용). entity 좌표는 신뢰 가능한 최근 스냅샷 값.
    """
    keys = (
        panel[["sj_store_id", "pnu", "name_norm", "name_branch_norm"]]
        .drop_duplicates()
        .dropna(subset=["pnu", "name_norm"])
    )
    keys = keys.merge(
        entities[["sj_store_id", "sj_entity_id", "x_5179", "y_5179"]],
        on="sj_store_id", how="left",
    )
    return keys.reset_index(drop=True)


# ---------------------------------------------------------------------------
# cascade
# ---------------------------------------------------------------------------
def _resolve_unique(g: pd.DataFrame) -> tuple[bool, pd.Series | None, int]:
    """후보 그룹에서 entity가 유일하면 (True, 대표행, n) 아니면 (False, None, n)."""
    ents = g["sj_entity_id"].unique()
    if len(ents) == 1:
        return True, g.iloc[0], 1
    return False, None, len(ents)


def match_exact_tiers(lic: pd.DataFrame, cand: pd.DataFrame) -> pd.DataFrame:
    """Tier 1(PNU+상호 exact)과 Tier 2(지점명 보강 exact)를 수행한다.

    반환: store_id별 결과 (matched/ambiguous/미해결).
    """
    results: dict[str, dict] = {}

    lic_nb = lic.copy()
    branch_clean = lic_nb["branch"].map(lambda b: _clean_base(b) if pd.notna(b) else None)
    lic_nb["name_branch_norm_lic"] = [
        semas.combine_name_branch(n, b)
        for n, b in zip(lic_nb["name_norm"], branch_clean)
    ]

    tier_defs = [
        # (tier, method, lic 키, cand 키, branch_used)
        (1, "pnu+name_exact", "name_norm", "name_norm", False),
        (2, "pnu+name_branch(sj)_exact", "name_norm", "name_branch_norm", True),
        (2, "pnu+name_branch(lic)_exact", "name_branch_norm_lic", "name_norm", True),
        (2, "pnu+name_branch(both)_exact", "name_branch_norm_lic", "name_branch_norm", True),
    ]

    for tier, method, lkey, ckey, branch_used in tier_defs:
        todo = lic_nb[
            ~lic_nb["store_id"].isin([s for s, r in results.items() if r["matched"]])
        ]
        todo = todo.dropna(subset=["pnu", lkey])
        csub = cand.dropna(subset=[ckey])
        m = todo[["store_id", "pnu", lkey]].merge(
            csub[["pnu", ckey, "sj_store_id", "sj_entity_id"]],
            left_on=["pnu", lkey], right_on=["pnu", ckey],
        )
        if m.empty:
            continue
        for sid, g in m.groupby("store_id"):
            ok, row, n = _resolve_unique(g)
            if ok:
                results[sid] = {
                    "matched": True, "match_tier": tier, "match_method": method,
                    "sj_store_id": row["sj_store_id"],
                    "sj_entity_id": row["sj_entity_id"],
                    "match_score": 1.0, "name_score": 1.0,
                    "candidate_count": len(g["sj_entity_id"].unique()),
                    "pnu_exact": True, "name_exact": tier == 1,
                    "branch_used": branch_used, "ambiguous": False,
                }
            else:
                prev = results.get(sid)
                if prev is None or not prev["matched"]:
                    results[sid] = {
                        "matched": False, "match_tier": tier, "match_method": method,
                        "sj_store_id": None, "sj_entity_id": None,
                        "match_score": 1.0, "name_score": 1.0,
                        "candidate_count": n,
                        "pnu_exact": True, "name_exact": tier == 1,
                        "branch_used": branch_used, "ambiguous": True,
                        "candidates": "|".join(sorted(g["sj_entity_id"].unique())[:5]),
                    }
    return pd.DataFrame.from_dict(results, orient="index").rename_axis("store_id")


def match_fuzzy_tier(
    lic: pd.DataFrame, cand: pd.DataFrame, threshold: float = FUZZY_THRESHOLD,
    margin: float = FUZZY_MARGIN,
    crowded_cc: int = TIER3_CROWDED_CC,
    crowded_min_score: float | None = TIER3_CROWDED_MIN_SCORE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tier 3: PNU가 같은 후보 내에서만 상호 fuzzy 매칭.

    후보 entity가 crowded_cc를 넘는 PNU(대형 복합건물·몰)는 crowded_pnu로 표시한다.
    crowded_min_score가 주어지면 crowded PNU에서 그 점수 미만인 매칭을 ambiguous로
    내린다 (None이면 표시만 하고 매칭은 바꾸지 않는다).

    반환: (store_id별 결과, 전 후보 pair의 score 기록 — sensitivity/분포 QA용)
    """
    todo = lic.dropna(subset=["pnu", "name_norm"])
    csub = cand.dropna(subset=["name_norm"])
    pairs = todo[["store_id", "pnu", "name_norm"]].merge(
        csub[["pnu", "name_norm", "name_branch_norm", "sj_store_id", "sj_entity_id"]],
        on="pnu", suffixes=("_lic", "_sj"),
    )
    if pairs.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 고유 문자열 쌍만 score 계산 (성능)
    uniq = pairs[["name_norm_lic", "name_norm_sj"]].drop_duplicates()
    cache = {
        (a, b): seq_ratio(a, b)
        for a, b in uniq.itertuples(index=False)
    }
    pairs["score_name"] = [
        cache[(a, b)] for a, b in zip(pairs["name_norm_lic"], pairs["name_norm_sj"])
    ]
    # 지점명 결합 표현과의 유사도 (더 높으면 branch_used)
    has_nb = pairs["name_branch_norm"].notna() & (
        pairs["name_branch_norm"] != pairs["name_norm_sj"]
    )
    uniq_nb = pairs.loc[has_nb, ["name_norm_lic", "name_branch_norm"]].drop_duplicates()
    cache_nb = {(a, b): seq_ratio(a, b) for a, b in uniq_nb.itertuples(index=False)}
    pairs["score_nb"] = [
        cache_nb.get((a, b), np.nan) if pd.notna(b) else np.nan
        for a, b in zip(pairs["name_norm_lic"], pairs["name_branch_norm"])
    ]
    pairs["score"] = pairs[["score_name", "score_nb"]].max(axis=1)
    pairs["branch_used"] = pairs["score_nb"] > pairs["score_name"]
    pairs["score_bigram"] = [
        bigram_jaccard(a, b) for a, b in zip(pairs["name_norm_lic"], pairs["name_norm_sj"])
    ]

    results: dict[str, dict] = {}
    for sid, g in pairs.groupby("store_id"):
        # entity 단위 최고 score
        by_ent = g.groupby("sj_entity_id")["score"].max().sort_values(ascending=False)
        best_ent = by_ent.index[0]
        best = float(by_ent.iloc[0])
        second = float(by_ent.iloc[1]) if len(by_ent) > 1 else -1.0
        best_row = g[g["sj_entity_id"] == best_ent].sort_values("score").iloc[-1]
        base = {
            "match_tier": 3, "match_method": "pnu_block+fuzzy_name",
            "match_score": best, "name_score": best,
            "name_score_bigram": float(best_row["score_bigram"]),
            "candidate_count": int(len(by_ent)),
            "pnu_exact": True,
            "name_exact": False,
            "branch_used": bool(best_row["branch_used"]),
            "best_sj_store_id": best_row["sj_store_id"],
            "best_sj_entity_id": best_ent,
            "best_name_norm_sj": best_row["name_norm_sj"],
            "crowded_pnu": bool(len(by_ent) > crowded_cc),
        }
        crowded_low = (
            base["crowded_pnu"] and crowded_min_score is not None
            and best < crowded_min_score
        )
        if best >= threshold and (best - second) >= margin and crowded_low:
            results[sid] = {**base, "matched": False, "ambiguous": True,
                            "sj_store_id": None, "sj_entity_id": None,
                            "unmatched_reason": "tier3_crowded_pnu_low_score"}
        elif best >= threshold and (best - second) >= margin:
            results[sid] = {**base, "matched": True, "ambiguous": False,
                            "sj_store_id": best_row["sj_store_id"],
                            "sj_entity_id": best_ent}
        elif best >= threshold:
            results[sid] = {**base, "matched": False, "ambiguous": True,
                            "sj_store_id": None, "sj_entity_id": None}
        else:
            results[sid] = {**base, "matched": False, "ambiguous": False,
                            "sj_store_id": None, "sj_entity_id": None,
                            "unmatched_reason": "fuzzy_below_threshold"}
    res = pd.DataFrame.from_dict(results, orient="index").rename_axis("store_id")
    return res, pairs


def match_coord_tier(
    lic: pd.DataFrame, entities: pd.DataFrame,
    radius: float = COORD_RADIUS_M, threshold: float = FUZZY_THRESHOLD,
    margin_m: float = COORD_MARGIN_M,
    require_structure: bool = TIER4_REQUIRE_EXACT_OR_CONTAINMENT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tier 4: 좌표 반경 내 후보 + 이름 일치 필수 (거리 단독 매칭 금지).

    require_structure=True면 score가 threshold 이상이어도 상호가 exact 또는
    containment일 때만 이름 일치로 인정한다. 좌표는 entity의 신뢰 가능한 스냅샷
    좌표만 쓰므로 202503(좌표 전량 서울 밖) 값은 여기 들어오지 않는다.

    반환: (store_id별 결과, 시도된 (license, candidate) 거리·score 기록)
    """
    ents = entities.dropna(subset=["x_5179", "y_5179", "name_norm_last"]).reset_index(drop=True)
    xs = ents["x_5179"].to_numpy(dtype=float)
    ys = ents["y_5179"].to_numpy(dtype=float)
    grid = build_grid(xs, ys, radius)

    todo = lic.dropna(subset=["x_5179", "y_5179", "name_norm"])
    results: dict[str, dict] = {}
    attempts = []
    for row in todo.itertuples(index=False):
        neigh = neighbors_within(row.x_5179, row.y_5179, grid, xs, ys, radius)
        if not neigh:
            results[row.store_id] = {
                "matched": False, "match_tier": 4, "ambiguous": False,
                "match_method": "coord_radius+name",
                "candidate_count": 0,
                "unmatched_reason": "no_candidate_in_radius",
            }
            continue
        scored = []
        for i, d in neigh:
            e = ents.iloc[i]
            # 상호 단독과 상호+지점명 중 score가 높은 쪽을 쓰고, 그 문자열로 구조를 판정한다
            best_str = e["name_norm_last"]
            s = seq_ratio(row.name_norm, best_str)
            nb = e["name_branch_norm_last"]
            if pd.notna(nb) and nb != e["name_norm_last"]:
                s_nb = seq_ratio(row.name_norm, nb)
                if s_nb > s:
                    s, best_str = s_nb, nb
            struct = name_structure(row.name_norm, best_str)
            scored.append((i, d, s, struct))
            attempts.append({
                "store_id": row.store_id, "sj_entity_id": e["sj_entity_id"],
                "distance_m": d, "name_score": s, "name_structure": struct,
            })
        score_ok = [t for t in scored if t[2] >= threshold]
        agree = [t for t in score_ok
                 if not require_structure or t[3] != NAME_OTHER]
        if not agree:
            # score는 통과했지만 이름 구조(치환형)로 걸러진 경우를 따로 남긴다
            pool, reason = (
                (score_ok, "tier4_name_not_exact_or_contained") if score_ok
                else (scored, "name_disagreement_in_radius")
            )
            best_i, best_d, best_s, best_struct = max(pool, key=lambda t: t[2])
            results[row.store_id] = {
                "matched": False, "match_tier": 4, "ambiguous": False,
                "match_method": "coord_radius+name",
                "candidate_count": len(scored),
                "distance_m": best_d, "name_score": best_s,
                "name_structure": best_struct,
                "best_sj_entity_id": ents.iloc[best_i]["sj_entity_id"],
                "best_name_norm_sj": ents.iloc[best_i]["name_norm_last"],
                "unmatched_reason": reason,
            }
            continue
        # 이름이 일치하는 후보 중 최근접. entity 중복 제거.
        agree_ents: dict[str, tuple[float, float, int, str]] = {}
        for i, d, s, struct in agree:
            eid = ents.iloc[i]["sj_entity_id"]
            if eid not in agree_ents or d < agree_ents[eid][0]:
                agree_ents[eid] = (d, s, i, struct)
        ranked = sorted(agree_ents.items(), key=lambda kv: kv[1][0])
        eid, (d, s, i, struct) = ranked[0]
        second_d = ranked[1][1][0] if len(ranked) > 1 else float("inf")
        base = {
            "match_tier": 4, "match_method": "coord_radius+name",
            "match_score": s, "name_score": s, "distance_m": d,
            "candidate_count": len(ranked),
            "pnu_exact": False, "name_exact": s >= 0.9999,
            "branch_used": False,
            "name_structure": struct,
            "best_sj_entity_id": eid,
            "best_name_norm_sj": ents.iloc[i]["name_norm_last"],
        }
        if len(ranked) == 1 or (second_d - d) >= margin_m:
            results[row.store_id] = {**base, "matched": True, "ambiguous": False,
                                     "sj_store_id": ents.iloc[i]["sj_store_id"],
                                     "sj_entity_id": eid}
        else:
            results[row.store_id] = {**base, "matched": False, "ambiguous": True,
                                     "sj_store_id": None, "sj_entity_id": None}
    res = pd.DataFrame.from_dict(results, orient="index").rename_axis("store_id")
    return res, pd.DataFrame(attempts)


def run_cascade(
    lic: pd.DataFrame, cand: pd.DataFrame, entities: pd.DataFrame,
    tier3_crowded_min_score: float | None = TIER3_CROWDED_MIN_SCORE,
    tier4_require_structure: bool = TIER4_REQUIRE_EXACT_OR_CONTAINMENT,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """4-tier cascade 실행. 인허가 전체 행을 보존한 매칭 테이블을 만든다.

    규칙 옵션은 sensitivity 비교를 위해 인자로 받는다. 기본값은 모듈 상수다.
    """
    exact = match_exact_tiers(lic, cand)
    matched_ids = set(exact.index[exact["matched"]]) if len(exact) else set()
    amb_exact_ids = set(exact.index[~exact["matched"]]) if len(exact) else set()

    remain = lic[~lic["store_id"].isin(matched_ids | amb_exact_ids)]
    fuzzy, fuzzy_pairs = match_fuzzy_tier(
        remain, cand, crowded_min_score=tier3_crowded_min_score)
    matched_ids |= set(fuzzy.index[fuzzy["matched"]]) if len(fuzzy) else set()
    amb_fuzzy_ids = set(fuzzy.index[fuzzy.get("ambiguous", pd.Series(dtype=bool)) == True]) if len(fuzzy) else set()

    remain2 = lic[~lic["store_id"].isin(
        matched_ids | amb_exact_ids | amb_fuzzy_ids
        | (set(fuzzy.index) if len(fuzzy) else set())
    )]
    coord, coord_attempts = match_coord_tier(
        remain2, entities, require_structure=tier4_require_structure)

    parts = [df for df in (exact, fuzzy, coord) if len(df)]
    allres = pd.concat(parts) if parts else pd.DataFrame()

    out = lic.merge(allres.reset_index(), on="store_id", how="left")
    out["matched"] = out["matched"].fillna(False).astype(bool)
    out["ambiguous"] = out["ambiguous"].fillna(False).astype(bool)
    if "unmatched_reason" not in out.columns:
        out["unmatched_reason"] = pd.NA
    no_attempt = out["match_tier"].isna()
    out.loc[no_attempt & out["pnu"].isna(), "unmatched_reason"] = "no_pnu_no_coord_or_no_candidate"
    out.loc[no_attempt & out["pnu"].notna(), "unmatched_reason"] = "no_candidate_on_pnu"
    # 규칙이 이미 구체적 사유(예: tier3_crowded_pnu_low_score)를 남겼으면 덮어쓰지 않는다
    out.loc[out["ambiguous"] & out["unmatched_reason"].isna(),
            "unmatched_reason"] = "ambiguous_candidates"
    for col in ("crowded_pnu",):
        if col in out.columns:
            out[col] = out[col].astype("boolean")

    conf = pd.Series(pd.NA, index=out.index, dtype="object")
    conf[out["matched"] & out["match_tier"].isin([1, 2])] = "high"
    conf[out["matched"] & (out["match_tier"] == 3)] = "medium"
    conf[out["matched"] & (out["match_tier"] == 4)] = "low"
    out["match_confidence"] = conf
    return out, fuzzy_pairs, coord_attempts


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------
def _rate_table(df: pd.DataFrame, by: str) -> list[str]:
    lines = [f"| {by} | n | matched | rate |", "|---|---|---|---|"]
    for k, g in df.groupby(by, dropna=False):
        lines.append(f"| {k} | {len(g):,} | {int(g['matched'].sum()):,} "
                     f"| {g['matched'].mean():.4f} |")
    return lines


def write_qa(
    out: pd.DataFrame, fuzzy_pairs: pd.DataFrame, coord_attempts: pd.DataFrame,
    trans_qa: pd.DataFrame, links: pd.DataFrame, entities: pd.DataFrame,
    panel_qa_lines: list[str],
) -> None:
    MATCH_DIR.mkdir(parents=True, exist_ok=True)
    L = ["# 인허가 ↔ 소진공 Entity Resolution QA", ""]
    L.append("생성: `python -m src.data.matching` — Tier4 규칙은 확정, Tier3 0.75는 잠정값이며 "
             "sensitivity와 수작업 표본 검증으로 확정 예정.")
    L.append(f"- FUZZY_THRESHOLD={FUZZY_THRESHOLD}, FUZZY_MARGIN={FUZZY_MARGIN}, "
             f"COORD_RADIUS_M={COORD_RADIUS_M}, COORD_MARGIN_M={COORD_MARGIN_M}")
    L.append("")
    L += panel_qa_lines

    L.append("## 소진공 ID 안정성 (인접 스냅샷 전이)")
    L.append("")
    L.append("| from | to | 유지 | 유지율 | 소멸(구간) | 완전소멸 | 신규(전체) | 완전신규 | 공백 후 재등장 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in trans_qa.itertuples(index=False):
        L.append(f"| {r._0} | {r.to} | {r.retained:,} | {r.retention_rate:.3f} "
                 f"| {r.lost:,} | {r.lost_forever:,} | {r.new_total:,} | {r.brand_new:,} "
                 f"| {r.reappeared_after_gap:,} |")
    L.append("")
    L.append("- 소멸(구간): from에 있고 to에 없음 / 완전소멸: from에 있고 to 이후 어디에도 없음.")
    L.append("  재발급 후보의 old 모집단은 완전소멸이므로 아래 비율의 분모도 완전소멸을 사용한다.")
    L.append("")

    L.append("## ID 재발급 후보 (소멸 → 동일 PNU+상호 신규)")
    L.append("")
    if len(links):
        for (f_, t_), g in links.groupby(["from_snapshot", "to_snapshot"]):
            n_old = g["old_id"].nunique()
            n_amb = int(g.loc[g["ambiguous"], "old_id"].nunique())
            lost_n = int(trans_qa.loc[trans_qa["to"] == t_, "lost_forever"].iloc[0])
            L.append(f"- {f_}→{t_}: 재발급 후보 old ID {n_old:,}건 "
                     f"(해당 구간 완전소멸 {lost_n:,}건의 {n_old / lost_n:.1%}), "
                     f"그중 ambiguous {n_amb:,}건")
    else:
        L.append("- 후보 없음")
    n_linked = int(entities["id_linked"].sum())
    n_merged = int((entities["sj_entity_id"] != entities["sj_store_id"]).sum())
    L.append(f"- unambiguous 링크로 병합된 ID: {n_merged:,} "
             f"(union-find 참여 {n_linked:,}) — ambiguous 링크는 병합하지 않음")
    L.append(f"- entity 수: {entities['sj_entity_id'].nunique():,} "
             f"(원 업소번호 {len(entities):,})")
    if "id_reissued" in entities.columns:
        reissued = entities[entities["id_reissued"]]
        L.append(f"- `id_reissued` entity: {reissued['sj_entity_id'].nunique():,} "
                 f"(소속 업소번호 {len(reissued):,}) — 재발급 전후를 한 entity로 유지")
        amb = links[links["ambiguous"]]
        same = 0
        if len(amb):
            eid = entities.set_index("sj_store_id")["sj_entity_id"]
            same = int((amb["old_id"].map(eid) == amb["new_id"].map(eid)).sum())
        L.append(f"- ambiguous 링크 {len(amb):,}건 중 같은 entity로 묶인 쌍: {same} (0이어야 정상)")
        L.append("- 폐업 보조정보(sj_status)는 ID 기준 `last_snapshot`이 아니라 "
                 "`entity_last_snapshot`을 써야 재발급을 소멸로 오인하지 않는다.")
    L.append("")
    L.append("주의: 소진공 소멸·신규는 폐업·개업 label이 아니다 (DECISIONS 2026-09-13).")
    L.append("")

    L.append("## 매칭률")
    L.append("")
    L.append(f"- 전체: {len(out):,}행 중 matched {int(out['matched'].sum()):,} "
             f"({out['matched'].mean():.4f}) / ambiguous {int(out['ambiguous'].sum()):,} "
             f"/ unmatched {int((~out['matched'] & ~out['ambiguous']).sum()):,}")
    L.append("")
    L.append("### 업종별")
    L += _rate_table(out, "business_type")
    L.append("")
    L.append("### 구별")
    L += _rate_table(out, "gu")
    L.append("")
    L.append("### 인허가 영업상태별")
    L += _rate_table(out, "status_name")
    L.append("")
    L.append("주의: 영업상태별 매칭률 차이는 complete-case 금지 근거의 재확인용 QA이며,")
    L.append("이를 이용해 label을 수정하지 않는다.")
    L.append("")

    L.append("### tier / confidence 별")
    L.append("")
    tier_tab = out[out["matched"]].groupby(["match_tier", "match_method"]).size()
    for (tier, method), n in tier_tab.items():
        L.append(f"- tier {int(tier)} ({method}): {n:,}")
    L.append("")
    conf_tab = out.groupby("match_confidence", dropna=False)["store_id"].count()
    for k, n in conf_tab.items():
        L.append(f"- confidence {k}: {n:,}")
    L.append("")
    L.append("### 후보 수 분포 (매칭 시도 행)")
    cc = out["candidate_count"].dropna().astype(int)
    L.append(f"- candidate_count 분포: {cc.value_counts().sort_index().head(10).to_dict()}"
             f" (max {cc.max() if len(cc) else 0})")
    L.append("")

    L.append("## Fuzzy (tier 3) score 분포와 threshold sensitivity")
    L.append("")
    if len(fuzzy_pairs):
        best = fuzzy_pairs.groupby("store_id")["score"].max()
        hist = pd.cut(best, bins=[0, .3, .5, .6, .7, .75, .8, .9, .9999, 1.0]).value_counts().sort_index()
        L.append("best score 분포 (tier 3 시도 store 기준):")
        for iv, n in hist.items():
            L.append(f"- {iv}: {n:,}")
        L.append("")
        L.append("| threshold | score≥thr store 수 | (참고) margin 조건 무시 |")
        L.append("|---|---|---|")
        for thr in FUZZY_SENS_GRID:
            L.append(f"| {thr} | {(best >= thr).sum():,} | 단순 count |")
    else:
        L.append("- tier 3 시도 없음")
    L.append("")

    L.append("## 좌표 (tier 4) 거리 분포와 radius sensitivity")
    L.append("")
    if len(coord_attempts):
        d = coord_attempts["distance_m"]
        L.append(f"- 시도 pair 수 {len(coord_attempts):,}, 거리 중앙값 {d.median():.1f}m")
        agree = coord_attempts[coord_attempts["name_score"] >= FUZZY_THRESHOLD]
        L.append("| radius(m) | 이름 일치 후보 보유 store 수 | ambiguous(2개 이상) |")
        L.append("|---|---|---|")
        for r in RADIUS_SENS_GRID:
            sub = agree[agree["distance_m"] <= r]
            per = sub.groupby("store_id")["sj_entity_id"].nunique()
            L.append(f"| {r:.0f} | {len(per):,} | {(per > 1).sum():,} |")
        L.append("")
        t4 = out[(out["match_tier"] == 4) & out["name_structure"].notna()] \
            if "name_structure" in out.columns else out.iloc[0:0]
        if len(t4):
            L.append("Tier4 이름 구조 (matched / 구조 조건으로 제외):")
            for struct, g in t4.groupby("name_structure"):
                L.append(f"- {struct}: matched {int(g['matched'].sum()):,} / "
                         f"제외 {int((g['unmatched_reason'] == 'tier4_name_not_exact_or_contained').sum()):,}")
            rej = t4[t4["unmatched_reason"] == "tier4_name_not_exact_or_contained"]
            if len(rej):
                L.append("")
                L.append("구조 조건으로 제외된 Tier4 후보 (전수):")
                for r in rej.sort_values("distance_m").itertuples(index=False):
                    L.append(f"- {r.name_norm} ↔ {r.best_name_norm_sj} "
                             f"(score {r.name_score:.3f}, {r.distance_m:.1f}m)")
    else:
        L.append("- tier 4 시도 없음")
    L.append("")

    L.append("## Unmatched 사유")
    L.append("")
    for k, n in out.loc[~out["matched"], "unmatched_reason"].value_counts(dropna=False).items():
        L.append(f"- {k}: {n:,}")
    L.append("")

    (MATCH_DIR / "qa_report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"QA 리포트: {MATCH_DIR / 'qa_report.md'}")


def build_validation_sample(out: pd.DataFrame, entities: pd.DataFrame) -> pd.DataFrame:
    """수작업 검증 표본: 그룹별 추출, 인허가·소진공 정보를 나란히 배치."""
    ent = entities.set_index("sj_store_id")
    groups = {
        "tier1_exact": out[out["matched"] & (out["match_tier"] == 1)],
        "tier2_branch": out[out["matched"] & (out["match_tier"] == 2)],
        "tier3_high_score": out[out["matched"] & (out["match_tier"] == 3)
                                & (out["match_score"] >= FUZZY_THRESHOLD + 0.1)],
        "tier3_near_threshold": out[
            (out["match_tier"] == 3)
            & out["match_score"].between(FUZZY_THRESHOLD - 0.05, FUZZY_THRESHOLD + 0.05)
        ],
        "tier4_coord": out[out["match_tier"] == 4],
        "ambiguous": out[out["ambiguous"]],
        "unmatched_with_pnu": out[~out["matched"] & ~out["ambiguous"] & out["pnu"].notna()],
    }
    rows = []
    for gname, g in groups.items():
        if len(g) == 0:
            continue
        s = g.sample(n=min(SAMPLE_PER_GROUP, len(g)), random_state=SAMPLE_SEED)
        for r in s.itertuples(index=False):
            # 확정 매칭 ID가 없으면 best 후보(리뷰용)로 대체: store_id → entity_id 순
            sj_id = None
            for attr in ("sj_store_id", "best_sj_store_id", "best_sj_entity_id"):
                v = getattr(r, attr, None)
                if v is not None and pd.notna(v):
                    sj_id = v
                    break
            e = ent.loc[sj_id] if sj_id is not None and sj_id in ent.index else None
            rows.append({
                "group": gname,
                "store_id": r.store_id,
                "business_type": r.business_type,
                "gu": r.gu,
                "lic_name_raw": r.name_raw,
                "lic_name_norm": r.name_norm,
                "lic_branch": r.branch,
                "lic_addr": r.addr_raw,
                "lic_pnu": r.pnu,
                "lic_x_5179": r.x_5179,
                "lic_y_5179": r.y_5179,
                "sj_store_id": sj_id,
                "sj_name_raw": e["name_raw_last"] if e is not None else None,
                "sj_branch_raw": e["branch_raw_last"] if e is not None else None,
                "sj_addr": e["addr_raw_last"] if e is not None else None,
                "sj_pnu": e["pnu_last"] if e is not None else None,
                "sj_x_5179": e["x_5179"] if e is not None else None,
                "sj_y_5179": e["y_5179"] if e is not None else None,
                "match_tier": getattr(r, "match_tier", None),
                "match_method": getattr(r, "match_method", None),
                "match_score": getattr(r, "match_score", None),
                "name_score": getattr(r, "name_score", None),
                "distance_m": getattr(r, "distance_m", None),
                "candidate_count": getattr(r, "candidate_count", None),
                "matched": r.matched,
                "ambiguous": r.ambiguous,
            })
    return pd.DataFrame(rows)


TIER3_SCORE_BANDS = [(0.9, 1.01), (0.8, 0.9), (0.75, 0.8), (0.7, 0.75)]
TIER4_DIST_BANDS = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
PRECISION_SAMPLE_PER_BAND = 20


def build_precision_sample(out: pd.DataFrame, entities: pd.DataFrame) -> pd.DataFrame:
    """자동 매칭 precision sanity check용 확대 표본.

    Tier 3는 score 구간별, Tier 4는 거리 구간별로 최대 20건씩 추출한다.
    matched 행을 우선 추출하고 부족하면 동일 구간의 미매칭(threshold 미달·ambiguous)
    행으로 채운다 — 정답/오답 판정은 하지 않으며 사람 검토용 표만 생성한다.
    """
    ent = entities.set_index("sj_store_id")
    ent_by_entity = entities.drop_duplicates("sj_entity_id").set_index("sj_entity_id")

    def _sj_info(r):
        for attr, idx in (("sj_store_id", ent), ("best_sj_store_id", ent),
                          ("best_sj_entity_id", ent_by_entity)):
            v = getattr(r, attr, None)
            if v is not None and pd.notna(v) and v in idx.index:
                return v, idx.loc[v]
        return None, None

    def _sample_band(pool: pd.DataFrame, band_name: str) -> list[dict]:
        matched_pool = pool[pool["matched"]]
        rest_pool = pool[~pool["matched"]]
        take = pd.concat([
            matched_pool.sample(n=min(PRECISION_SAMPLE_PER_BAND, len(matched_pool)),
                                random_state=SAMPLE_SEED),
            rest_pool.sample(
                n=min(max(0, PRECISION_SAMPLE_PER_BAND - len(matched_pool)),
                      len(rest_pool)),
                random_state=SAMPLE_SEED),
        ]) if len(matched_pool) or len(rest_pool) else pool.head(0)
        rows = []
        for r in take.itertuples(index=False):
            sj_id, e = _sj_info(r)
            rows.append({
                "band": band_name,
                "store_id": r.store_id,
                "business_type": r.business_type,
                "gu": r.gu,
                "lic_name_raw": r.name_raw,
                "lic_branch": r.branch,
                "lic_addr": r.addr_raw,
                "lic_pnu": r.pnu,
                "sj_store_id": sj_id,
                "sj_name_raw": e["name_raw_last"] if e is not None else None,
                "sj_branch_raw": e["branch_raw_last"] if e is not None else None,
                "sj_addr": e["addr_raw_last"] if e is not None else None,
                "sj_pnu": e["pnu_last"] if e is not None else None,
                "name_score": getattr(r, "name_score", None),
                "distance_m": getattr(r, "distance_m", None),
                "candidate_count": getattr(r, "candidate_count", None),
                "matched": r.matched,
                "ambiguous": r.ambiguous,
            })
        return rows

    rows: list[dict] = []
    t3 = out[out["match_tier"] == 3]
    for lo, hi in TIER3_SCORE_BANDS:
        pool = t3[t3["match_score"].ge(lo) & t3["match_score"].lt(hi)]
        rows += _sample_band(pool, f"tier3_score_{lo}-{hi if hi <= 1 else 1.0}")
    t4 = out[(out["match_tier"] == 4) & out["distance_m"].notna()]
    for lo, hi in TIER4_DIST_BANDS:
        pool = t4[t4["distance_m"].ge(lo) & t4["distance_m"].lt(hi)]
        rows += _sample_band(pool, f"tier4_dist_{lo:.0f}-{hi:.0f}m")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def run() -> pd.DataFrame:
    MATCH_DIR.mkdir(parents=True, exist_ok=True)

    lic_path = OUTPUT_DIR / "licenses_3gu.parquet"
    if not lic_path.exists():
        raise FileNotFoundError(
            f"{lic_path} 없음 — 먼저 `python -m src.data.standardize` 실행 필요")
    lic = pd.read_parquet(lic_path)

    panel_path = OUTPUT_DIR / "semas_panel.parquet"
    if panel_path.exists():
        panel = pd.read_parquet(panel_path)
        print(f"소진공 panel 로드: {len(panel):,} rows")
    else:
        panel = semas.run()

    print("ID 안정성 분석...")
    trans = identity.transition_qa(identity.snapshot_presence(panel))
    links = identity.detect_id_reissue(panel)
    entities = identity.build_entities(panel, links)
    entities.to_parquet(OUTPUT_DIR / "semas_entities.parquet", index=False)
    links.to_parquet(OUTPUT_DIR / "semas_id_links.parquet", index=False)
    print(f"  entities {len(entities):,} (canonical {entities['sj_entity_id'].nunique():,}), "
          f"재발급 링크 {len(links):,} (ambiguous {int(links['ambiguous'].sum()):,})")

    print("매칭 cascade 실행...")
    cand = build_candidate_table(panel, entities)
    out, fuzzy_pairs, coord_attempts = run_cascade(lic, cand, entities)
    assert len(out) == len(lic), "인허가 행 손실 금지"

    out.to_parquet(MATCH_DIR / "license_semas_matches.parquet", index=False)
    print(f"저장: {MATCH_DIR / 'license_semas_matches.parquet'} "
          f"(matched {out['matched'].mean():.4f})")

    sample = build_validation_sample(out, entities)
    sample.to_csv(MATCH_DIR / "match_validation_sample.csv", index=False,
                  encoding="utf-8-sig")
    print(f"검증 표본: {len(sample)}건 -> match_validation_sample.csv")

    precision = build_precision_sample(out, entities)
    precision.to_csv(MATCH_DIR / "match_precision_sample.csv", index=False,
                     encoding="utf-8-sig")
    print(f"precision 표본: {len(precision)}건 -> match_precision_sample.csv")

    write_qa(out, fuzzy_pairs, coord_attempts, trans, links, entities,
             semas.panel_qa(panel))
    return out


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
