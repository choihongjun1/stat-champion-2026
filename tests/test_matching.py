# -*- coding: utf-8 -*-
"""Entity Resolution cascade 테스트 (합성 데이터)."""
import numpy as np
import pandas as pd

from src.data.matching import (
    bigram_jaccard,
    build_grid,
    match_coord_tier,
    match_exact_tiers,
    match_fuzzy_tier,
    neighbors_within,
    run_cascade,
    seq_ratio,
)
from src.data.semas import combine_name_branch


def make_lic(rows):
    """rows: (store_id, pnu, name_norm, branch, x, y)"""
    df = pd.DataFrame(rows, columns=["store_id", "pnu", "name_norm", "branch",
                                     "x_5179", "y_5179"])
    df["name_raw"] = df["name_norm"]
    df["addr_raw"] = "주소"
    df["business_type"] = "일반음식점"
    df["gu"] = "마포구"
    df["status_name"] = "영업"
    return df


def make_cand(rows):
    """rows: (sj_store_id, pnu, name_norm, name_branch_norm, entity_id)"""
    df = pd.DataFrame(rows, columns=["sj_store_id", "pnu", "name_norm",
                                     "name_branch_norm", "sj_entity_id"])
    df["x_5179"] = np.nan
    df["y_5179"] = np.nan
    return df


def make_entities(rows):
    """rows: (sj_store_id, entity_id, name_norm_last, name_branch_norm_last, x, y)"""
    df = pd.DataFrame(rows, columns=["sj_store_id", "sj_entity_id", "name_norm_last",
                                     "name_branch_norm_last", "x_5179", "y_5179"])
    for c in ["name_raw_last", "branch_raw_last", "addr_raw_last", "pnu_last"]:
        df[c] = "x"
    return df


# --- 이름 결합 ---

def test_combine_name_branch():
    assert combine_name_branch("스타벅스", "홍대점") == "스타벅스홍대점"
    assert combine_name_branch("스타벅스홍대점", "홍대점") == "스타벅스홍대점"  # 중복 방지
    assert combine_name_branch("스타벅스", None) == "스타벅스"
    assert combine_name_branch(None, "홍대점") is None


# --- 유사도 ---

def test_similarity_functions():
    assert seq_ratio("김밥천국", "김밥천국") == 1.0
    assert 0.7 < seq_ratio("김밥천국", "김밥천곡") < 1.0
    assert bigram_jaccard("김밥천국", "김밥천국") == 1.0
    assert bigram_jaccard("김밥천국", "완전다른곳") == 0.0


# --- Tier 1 ---

def test_tier1_exact_unique_match():
    lic = make_lic([("L1", "P1", "김밥천국", None, None, None)])
    cand = make_cand([("S1", "P1", "김밥천국", "김밥천국", "S1")])
    res = match_exact_tiers(lic, cand)
    r = res.loc["L1"]
    assert r["matched"] and r["match_tier"] == 1
    assert r["sj_entity_id"] == "S1"
    assert r["candidate_count"] == 1 and not r["ambiguous"]


def test_tier1_ambiguous_two_entities_preserved():
    # 동일 필지 동명 점포 2개 → 자동 병합/매칭 금지, ambiguous 보존
    lic = make_lic([("L1", "P1", "미용실", None, None, None)])
    cand = make_cand([
        ("S1", "P1", "미용실", "미용실", "S1"),
        ("S2", "P1", "미용실", "미용실", "S2"),
    ])
    res = match_exact_tiers(lic, cand)
    r = res.loc["L1"]
    assert not r["matched"] and r["ambiguous"]
    assert r["candidate_count"] == 2


def test_tier1_same_entity_two_ids_not_ambiguous():
    # ID 재발급으로 entity가 하나로 병합된 경우 후보 2개라도 유일 entity
    lic = make_lic([("L1", "P1", "국밥집", None, None, None)])
    cand = make_cand([
        ("OLD", "P1", "국밥집", "국밥집", "OLD"),
        ("NEW", "P1", "국밥집", "국밥집", "OLD"),
    ])
    res = match_exact_tiers(lic, cand)
    r = res.loc["L1"]
    assert r["matched"] and r["sj_entity_id"] == "OLD"


# --- Tier 2 ---

def test_tier2_branch_combined():
    # 인허가 상호에 지점명이 붙어 있고 소진공은 상호/지점명 분리된 경우
    lic = make_lic([("L1", "P1", "스타벅스", "홍대점", None, None)])
    cand = make_cand([("S1", "P1", "스타벅스홍대점", "스타벅스홍대점", "S1")])
    res = match_exact_tiers(lic, cand)
    r = res.loc["L1"]
    assert r["matched"] and r["match_tier"] == 2
    assert r["branch_used"]


def test_tier2_sj_branch_column():
    # 소진공 지점명 컬럼 결합 표현과 인허가 상호가 일치
    lic = make_lic([("L1", "P1", "쥬씨서울숲점", None, None, None)])
    cand = make_cand([("S1", "P1", "쥬씨", "쥬씨서울숲점", "S1")])
    res = match_exact_tiers(lic, cand)
    assert res.loc["L1", "matched"] and res.loc["L1", "match_tier"] == 2


# --- Tier 3 ---

def test_tier3_fuzzy_match_within_pnu():
    lic = make_lic([("L1", "P1", "김밥천국", None, None, None)])
    cand = make_cand([("S1", "P1", "김밥천곡", "김밥천곡", "S1")])
    res, pairs = match_fuzzy_tier(lic, cand, threshold=0.7, margin=0.05)
    r = res.loc["L1"]
    assert r["matched"] and r["match_tier"] == 3
    assert 0.7 <= r["match_score"] < 1.0
    assert len(pairs) == 1


def test_tier3_below_threshold_unmatched_with_provenance():
    lic = make_lic([("L1", "P1", "김밥천국", None, None, None)])
    cand = make_cand([("S1", "P1", "완전다른상호", "완전다른상호", "S1")])
    res, _ = match_fuzzy_tier(lic, cand, threshold=0.7)
    r = res.loc["L1"]
    assert not r["matched"] and not r["ambiguous"]
    assert r["unmatched_reason"] == "fuzzy_below_threshold"
    assert r["best_sj_entity_id"] == "S1"  # 수작업 검증용 best 후보 보존


def test_tier3_margin_ambiguous():
    # 1·2위 score 차이가 margin 미만이면 ambiguous
    lic = make_lic([("L1", "P1", "가나다라마", None, None, None)])
    cand = make_cand([
        ("S1", "P1", "가나다라미", "가나다라미", "S1"),
        ("S2", "P1", "가나다라바", "가나다라바", "S2"),
    ])
    res, _ = match_fuzzy_tier(lic, cand, threshold=0.7, margin=0.05)
    r = res.loc["L1"]
    assert not r["matched"] and r["ambiguous"]


def test_tier3_blocking_no_cross_pnu():
    # 다른 PNU의 동일 상호는 후보가 아니다 (blocking)
    lic = make_lic([("L1", "P1", "김밥천국", None, None, None)])
    cand = make_cand([("S1", "P2", "김밥천국", "김밥천국", "S1")])
    res, pairs = match_fuzzy_tier(lic, cand)
    assert len(pairs) == 0 and len(res) == 0


# --- 거리/grid ---

def test_grid_neighbors_distance():
    xs = np.array([0.0, 10.0, 100.0])
    ys = np.array([0.0, 0.0, 0.0])
    grid = build_grid(xs, ys, 30.0)
    out = neighbors_within(0.0, 0.0, grid, xs, ys, 30.0)
    idx = [i for i, _ in out]
    assert idx == [0, 1]
    assert abs(out[1][1] - 10.0) < 1e-9


# --- Tier 4 ---

def test_tier4_coord_with_name_agreement():
    lic = make_lic([("L1", None, "김밥천국", None, 100.0, 100.0)])
    ents = make_entities([("S1", "S1", "김밥천국", "김밥천국", 105.0, 100.0)])
    res, att = match_coord_tier(lic, ents, radius=30.0, threshold=0.7)
    r = res.loc["L1"]
    assert r["matched"] and r["match_tier"] == 4
    assert abs(r["distance_m"] - 5.0) < 1e-9
    assert len(att) == 1


def test_tier4_no_distance_only_match_on_name_conflict():
    # 반경 내 후보가 있어도 이름이 다르면 거리만으로 매칭하지 않는다
    lic = make_lic([("L1", None, "김밥천국", None, 100.0, 100.0)])
    ents = make_entities([("S1", "S1", "완전다른상호", "완전다른상호", 101.0, 100.0)])
    res, _ = match_coord_tier(lic, ents, radius=30.0, threshold=0.7)
    r = res.loc["L1"]
    assert not r["matched"]
    assert r["unmatched_reason"] == "name_disagreement_in_radius"


def test_tier4_ambiguous_two_near_candidates():
    lic = make_lic([("L1", None, "김밥천국", None, 100.0, 100.0)])
    ents = make_entities([
        ("S1", "S1", "김밥천국", "김밥천국", 102.0, 100.0),
        ("S2", "S2", "김밥천국", "김밥천국", 103.0, 100.0),
    ])
    res, _ = match_coord_tier(lic, ents, radius=30.0, threshold=0.7, margin_m=5.0)
    r = res.loc["L1"]
    assert not r["matched"] and r["ambiguous"]


# --- cascade 전체 ---

def test_cascade_preserves_all_license_rows():
    lic = make_lic([
        ("L1", "P1", "김밥천국", None, None, None),       # tier1 match
        ("L2", "P9", "이름없는가게", None, None, None),    # pnu에 후보 없음
        ("L3", None, "좌표도없음", None, None, None),      # 아무 정보 없음
    ])
    cand = make_cand([("S1", "P1", "김밥천국", "김밥천국", "S1")])
    ents = make_entities([("S1", "S1", "김밥천국", "김밥천국", 0.0, 0.0)])
    out, _, _ = run_cascade(lic, cand, ents)
    assert len(out) == 3  # unmatched 행 삭제 금지
    assert out.set_index("store_id").loc["L1", "matched"]
    assert not out.set_index("store_id").loc["L2", "matched"]
    assert out.set_index("store_id").loc["L3", "unmatched_reason"] == "no_pnu_no_coord_or_no_candidate"


def test_cascade_confidence_levels():
    lic = make_lic([
        ("L1", "P1", "김밥천국", None, None, None),
        ("L2", "P2", "분식왕국", None, None, None),
    ])
    cand = make_cand([
        ("S1", "P1", "김밥천국", "김밥천국", "S1"),
        ("S2", "P2", "분식왕곡", "분식왕곡", "S2"),
    ])
    ents = make_entities([
        ("S1", "S1", "김밥천국", "김밥천국", 0.0, 0.0),
        ("S2", "S2", "분식왕곡", "분식왕곡", 10.0, 0.0),
    ])
    out, _, _ = run_cascade(lic, cand, ents)
    o = out.set_index("store_id")
    assert o.loc["L1", "match_confidence"] == "high"
    assert o.loc["L2", "match_confidence"] == "medium"  # fuzzy tier3
