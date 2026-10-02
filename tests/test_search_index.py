# -*- coding: utf-8 -*-
"""W2-5 검색 인덱스 테스트 (합성 정본)."""
import json
import sqlite3

import pytest

from src.serving import report_validation as rv
from src.serving import search_index as si
from tests.serving_synth import make_db, store

STORES = [
    store(1, "마포구", "망원동", "일반음식점", "high", name="샘플식당 망원점", road="서울특별시 마포구 가상로 1"),
    store(2, "광진구", "화양동", "일반음식점", "low", name="샘플식당 건대점", road="서울특별시 광진구 가상로 12"),
    store(3, "광진구", "구의동", "일반음식점", "mid", name="샘플식당", road="서울특별시 광진구 가상로 12, 2층"),
    store(4, "광진구", "화양동", "휴게음식점", "low", name="샘플식당2"),
    store(5, "영등포구", "여의도동", "휴게음식점", "low", name="(주)우리샘플식당"),
    store(6, "마포구", "망원동", "미용업", "mid", name=None, road=None, jibun="서울특별시 마포구 망원동 44-14"),
    store(7, "마포구", None, "미용업", "low", name="ＣＡＦＥ 모모", road=None, jibun=None),
]


def _conn(path):
    return sqlite3.connect(path)


@pytest.fixture
def db(tmp_path):
    return make_db(tmp_path, STORES)


@pytest.fixture
def idx(db):
    conn = _conn(db)
    try:
        return si.build_index(conn)
    finally:
        conn.close()


def _ids(hits):
    return [h["store_id"][-1] for h in hits]


def test_index_structure_and_no_risk_fields(idx):
    assert rv.validate_def("search_index_file", idx) == []
    assert idx["n_entries"] == len(STORES)
    forbidden = {"band", "probability_12m", "percentile", "risk", "factors", "contribution", "policies", "ci_low"}
    for e in idx["entries"]:
        assert list(e) == si.ENTRY_FIELDS
        assert not forbidden & set(e)
    assert "band" not in json.dumps(idx, ensure_ascii=False)


def test_duplicate_names_return_all_candidates_in_stable_order(idx):
    hits = si.search_name(idx["entries"], "샘플식당")
    # 지점명 없는 '샘플식당'(3)이 정확 일치, 지점만 다른 1·2는 같은 정규화 상호, 4는 앞부분, 5는 포함
    assert _ids(hits) == ["3", "2", "1", "4", "5"]
    assert _ids(si.search_name(idx["entries"], "샘플식당 망원점"))[:1] == ["1"]  # 지점명까지 같으면 먼저
    assert set(_ids(si.search_name(idx["entries"], "샘플식당 망원점"))) >= {"1", "2", "3"}


def test_name_normalization_matches_license_rule(idx):
    assert _ids(si.search_name(idx["entries"], "cafe모모")) == ["7"]      # NFKC·대문자
    assert _ids(si.search_name(idx["entries"], "우리 샘플-식당")) == ["5"]  # 공백·기호 제거, (주) 제외
    assert si.search_name(idx["entries"], "샘") == []                     # 너무 짧은 검색어
    assert si.search_name(idx["entries"], "   ") == []


def test_address_search(idx):
    assert _ids(si.search_address(idx["entries"], "서울시 광진구 가상로 12")) == ["3", "2"]
    # '가상로 1'은 1번이 경계 일치, 12번지는 숫자가 이어져 뒤로
    assert _ids(si.search_address(idx["entries"], "마포구 가상로 1")) == ["1"]
    assert _ids(si.search_address(idx["entries"], "가상로 1"))[:1] == ["1"]
    assert _ids(si.search_address(idx["entries"], "망원동 44-14")) == ["6"]  # 지번 (도로명 없음)


def test_missing_name_and_address_are_safe(idx):
    e6 = next(e for e in idx["entries"] if e["store_id"].endswith("6"))
    e7 = next(e for e in idx["entries"] if e["store_id"].endswith("7"))
    assert e6["name"] is None and e6["name_norm"] is None
    assert e7["address_road"] is None and e7["address_jibun"] is None and e7["dong"] is None
    assert "6" not in _ids(si.search_name(idx["entries"], "가상점포"))
    assert "7" not in _ids(si.search_address(idx["entries"], "마포구"))


def test_index_ids_match_reports(db):
    conn = _conn(db)
    try:
        conn.execute("DELETE FROM risk WHERE store_id = (SELECT store_id FROM stores LIMIT 1)")
        with pytest.raises(si.SearchIndexError, match="store_id 집합 불일치"):
            si.build_index(conn)
    finally:
        conn.close()


def test_duplicate_store_id_is_detected(idx):
    run = {"n_stores": len(STORES) + 1}
    entries = idx["entries"] + [dict(idx["entries"][0])]

    class _C:  # store_id 집합 조회만 흉내
        def execute(self, sql):
            return [(e["store_id"],) for e in idx["entries"]]
    errs = si.check_consistency(_C(), entries, run)
    assert any("중복" in e for e in errs)


def test_name_norm_mismatch_is_detected(db):
    conn = _conn(db)
    try:
        conn.execute("UPDATE stores SET name_norm = '다른값' WHERE name IS NOT NULL AND rowid = 1")
        with pytest.raises(si.SearchIndexError, match="normalize_name"):
            si.build_index(conn)
    finally:
        conn.close()


def test_rebuild_is_reproducible(tmp_path):
    a = make_db(tmp_path, STORES, name="a.sqlite")
    b = make_db(tmp_path, STORES, name="b.sqlite")
    outs = []
    for p in (a, b):
        conn = _conn(p)
        try:
            outs.append(json.dumps(si.build_index(conn), ensure_ascii=False))
        finally:
            conn.close()
    assert outs[0] == outs[1]


def test_not_ready_db_is_dev_only(tmp_path):
    dev = make_db(tmp_path, STORES, version="0.1", name="dev.sqlite")
    conn = _conn(dev)
    try:
        idx = si.build_index(conn)  # 개발용은 만들 수 있다
        assert idx["release_ready"] is False and idx["release_blockers"]
        with pytest.raises(si.SearchIndexError, match="공개 배포용"):
            si.build_index(conn, purpose="release")
    finally:
        conn.close()
    rel = make_db(tmp_path, STORES, purpose="release", name="rel.sqlite")
    conn = _conn(rel)
    try:
        out = si.build_index(conn, purpose="release")
        assert out["release_ready"] is True and out["release_blockers"] == []
    finally:
        conn.close()


def test_write_json_refuses_tracked_path(idx):
    from src.data import config
    with pytest.raises(Exception, match="docs/"):
        si.write_json(idx, config.REPO_ROOT / "docs" / "search_index.json")
