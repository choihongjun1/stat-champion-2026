# -*- coding: utf-8 -*-
"""W2-5 동 요약 테스트 (합성 정본, Issue #29)."""
import json
import sqlite3

import pandas as pd
import pytest

from src.serving import dong_summary as ds
from src.serving import report_validation as rv
from tests.serving_synth import make_db, store


def _cell(start, gu, dong, biz, bands):
    out, i = [], start
    for band, k in bands.items():
        for _ in range(k):
            out.append(store(i, gu, dong, biz, band))
            i += 1
    return out


STORES = [
    *_cell(100, "마포구", "망원동", "일반음식점", {"high": 3, "mid": 4, "low": 5}),
    *_cell(200, "마포구", "망원동", "휴게음식점", {"low": 2}),
    *_cell(300, "마포구", "망원동", "미용업", {"high": 1, "low": 7}),
    *_cell(400, "마포구", "신정동", "일반음식점", {"low": 3}),
    *_cell(500, "영등포구", "신정동", "일반음식점", {"high": 2, "low": 4}),
    *_cell(600, "영등포구", "신정동", "미용업", {"high": 2, "mid": 1, "low": 3}),
    *_cell(700, "마포구", "합정동", "일반음식점", {"mid": 6}),                 # 평균 위험은 높지만 high 없음
    *_cell(800, "마포구", "합정동", "휴게음식점", {"high": 1, "low": 5}),
    store(900, "마포구", None, "미용업", "low"),                              # 법정동 결측
]


def _summary(path, **kw):
    conn = sqlite3.connect(path)
    try:
        return ds.build_summary(conn, **{"min_cell_n": 5, **kw})
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    return make_db(tmp_path, STORES)


@pytest.fixture
def out(db):
    return _summary(db)


def _row(out, gu, dong, biz):
    return next(r for r in out["rows"] if (r["gu"], r["dong"], r["biz_type"]) == (gu, dong, biz))


def test_structure_schema_and_no_store_level_data(out):
    assert rv.validate_def("dong_summary_file", out) == []
    text = json.dumps(out, ensure_ascii=False)
    for token in ("GR_", "SR_", "BT_", "probability", "store_id"):
        assert token not in text
    assert all("개별 가게 진단이 아닙니다" in r["note"] for r in out["rows"])
    assert out["n_stores_total"] == len(STORES) and out["n_stores_without_dong"] == 1
    assert out["min_cell_n"] == 5 and out["min_cell_n_status"] == "provisional"


def test_dong_list_uses_gu_and_dong_with_label(out):
    assert [(d["gu"], d["dong"], d["label"]) for d in out["dongs"]] == [
        ("마포구", "망원동", "망원동 (마포구)"), ("마포구", "신정동", "신정동 (마포구)"),
        ("마포구", "합정동", "합정동 (마포구)"), ("영등포구", "신정동", "신정동 (영등포구)")]
    assert out["duplicate_dong_names"] == ["신정동"]  # 동명이동 — (gu, dong)으로 구분


def test_band_distribution_sums(db, out):
    conn = sqlite3.connect(db)
    try:
        truth = pd.read_sql_query("SELECT s.gu, s.dong, s.biz_type, r.band FROM stores s JOIN risk r USING (store_id)",
                                  conn)
    finally:
        conn.close()
    for r in out["rows"]:
        if r["suppressed"]:
            continue
        m = (truth["gu"] == r["gu"]) & (truth["dong"] == r["dong"])
        if r["biz_type"] is not None:
            m &= truth["biz_type"] == r["biz_type"]
        assert r["n_stores"] == int(m.sum())
        assert abs(sum(r["band_share"].values()) - 1) < 1e-3
        for b, share in r["band_share"].items():
            assert round(share * r["n_stores"]) == int((truth.loc[m, "band"] == b).sum())


def test_small_cell_and_complementary_suppression(out):
    assert _row(out, "마포구", "망원동", "휴게음식점")["suppression_reason"] == "small_cell"      # 2곳
    assert _row(out, "마포구", "망원동", "미용업")["suppression_reason"] == "complementary"       # 역산 방지
    assert _row(out, "마포구", "망원동", "일반음식점")["suppressed"] is False
    assert _row(out, "마포구", "망원동", None)["n_stores"] == 22
    total = _row(out, "마포구", "신정동", None)  # 동 전체 3곳
    assert total["suppressed"] and total["suppression_reason"] == "small_cell" and total["band_share"] is None
    hidden = _row(out, "마포구", "신정동", "일반음식점")
    assert hidden["n_stores"] is None and hidden["top_risk_biz_types"] is None


def test_hidden_values_are_not_recoverable(out):
    assert ds.recoverable_cells(out["rows"]) == []
    # 추가 숨김이 없으면 동 전체 − 공개 칸으로 휴게음식점 2곳이 역산된다
    leaky = [dict(r) for r in out["rows"]]
    for r in leaky:
        if (r["dong"], r["biz_type"]) == ("망원동", "미용업"):
            r.update(suppressed=False, suppression_reason=None, n_stores=8)
    assert ds.recoverable_cells(leaky) == [("마포구", "망원동", "휴게음식점")]
    shown = {b: _row(out, "마포구", "망원동", b)["n_stores"] for b in ("일반음식점",)}
    assert _row(out, "마포구", "망원동", None)["n_stores"] - sum(shown.values()) == 10  # 두 숨긴 칸의 합만 알 수 있다


def test_risk_ranking_uses_high_share_not_mean(out):
    assert _row(out, "영등포구", "신정동", None)["top_risk_biz_types"] == ["미용업", "일반음식점"]  # high 1/3 동률 → mid+high
    # 합정동: 일반음식점은 전부 mid라 평균 예측 확률은 더 높지만 high가 없어 순위에 없다
    assert _row(out, "마포구", "합정동", None)["top_risk_biz_types"] == ["휴게음식점"]
    assert _row(out, "마포구", "망원동", None)["top_risk_biz_types"] == ["일반음식점"]  # 숨긴 업종은 제외
    assert "평균 예측 확률이 아니라" in out["risk_ranking_rule"]


def test_null_dong_is_excluded_not_guessed(out):
    assert all(r["dong"] is not None for r in out["rows"])
    assert sum(r["n_stores"] for r in out["rows"] if r["biz_type"] is None and not r["suppressed"]) + 3 + 1 \
        == len(STORES)  # 공개된 동 합계 + 숨긴 신정동(마포) 3 + 동 없음 1


def test_admin_dong_names_are_rejected(tmp_path):
    p = make_db(tmp_path, [*_cell(1, "마포구", "망원1동", "일반음식점", {"low": 6})])
    with pytest.raises(ds.DongSummaryError, match="행정동"):
        _summary(p)


def test_store_risk_mismatch_is_detected(db):
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM risk WHERE rowid = 1")
        with pytest.raises(ds.DongSummaryError, match="행 수 불일치"):
            ds.build_summary(conn, min_cell_n=5)
    finally:
        conn.close()


def test_min_cell_n_must_be_given():
    with pytest.raises(TypeError):
        ds.build_summary(None)  # 하한값 기본값 없음 (미확정)


def test_rebuild_is_reproducible(tmp_path):
    a = make_db(tmp_path, STORES, name="a.sqlite")
    b = make_db(tmp_path, STORES, name="b.sqlite")
    assert json.dumps(_summary(a), ensure_ascii=False) == json.dumps(_summary(b), ensure_ascii=False)


def test_release_requires_passed_release_db_and_decided_threshold(tmp_path):
    dev = make_db(tmp_path, STORES, version="0.1", name="dev.sqlite")
    s = _summary(dev)  # not_ready 정본도 개발용 집계는 된다
    assert s["release_ready"] is False
    with pytest.raises(ds.DongSummaryError, match="공개 배포용"):
        _summary(dev, purpose="release", min_cell_n_status="decided")
    rel = make_db(tmp_path, STORES, purpose="release", name="rel.sqlite")
    with pytest.raises(ds.DongSummaryError, match="하한값 미확정"):
        _summary(rel, purpose="release")
    ok = _summary(rel, purpose="release", min_cell_n_status="decided")
    assert ok["release_ready"] is True and ok["release_blockers"] == []


def test_cell_size_report():
    counts = pd.DataFrame([("마포구", "망원동", "일반음식점", 12), ("마포구", "망원동", "휴게음식점", 2),
                           ("마포구", "망원동", "미용업", 8), ("마포구", "신정동", "일반음식점", 3)],
                          columns=["gu", "dong", "biz_type", "n"])
    rep = ds.cell_size_report(counts, candidates=(5,))
    assert rep["stats"]["min"] == 2 and rep["stats"]["max"] == 12 and rep["stats"]["n_stores"] == 25
    c = rep["candidates"][0]
    assert (c["cells_small"], c["cells_complementary"], c["dong_totals_hidden"]) == (2, 1, 1)
    assert c["dongs_affected"] == 2 and c["stores_in_hidden_cells"] == 13


def test_open_population_counts_uses_as_of():
    lic = pd.DataFrame({"gu": ["마포구"] * 3, "dong": ["망원동", "망원동", None],
                        "business_type": ["미용업"] * 3,
                        "license_date": pd.to_datetime(["2020-01-01", "2026-07-01", "2020-01-01"]),
                        "close_date": pd.to_datetime([None, None, None])})
    counts, no_dong = ds.open_population_counts(lic, "2026-06-30")
    assert counts["n"].tolist() == [1] and no_dong == 1
