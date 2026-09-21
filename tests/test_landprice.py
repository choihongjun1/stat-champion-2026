# -*- coding: utf-8 -*-
"""공시지가 결합 로직 테스트 (합성 데이터)."""
import pandas as pd
import pytest

import src.data.landprice as lp


def make_year_table(monkeypatch, tables):
    """load_landprice_year를 합성 테이블로 대체. tables: {year: (df, qa)}"""
    monkeypatch.setattr(lp, "load_landprice_year", lambda y: tables[y])


def _qa(year):
    return {"year": year, "rows": 0, "unique_pnu": 0, "invalid_pnu": 0,
            "dup_pnu_rows": 0, "dup_pnu_unique": 0,
            "dup_pnu_conflicting_values": 0, "excluded_conflicting_pnus": 0,
            "price_nan": 0, "price_zero": 0, "price_negative": 0,
            "price_median": 0.0, "price_p99": 0.0, "price_max": 0.0,
            "reference_years_in_file": [str(year)],
            "reference_months_in_file": [f"{year}-01-01"]}


PNU_A = "1144012000103580018"
PNU_B = "1121510300102130002"


def test_left_join_preserves_unmatched(monkeypatch):
    stores = pd.DataFrame({"store_id": ["L1", "L2", "L3"],
                           "pnu": [PNU_A, PNU_B, None]})
    tables = {
        y: (pd.DataFrame({"pnu": [PNU_A], f"land_price_{y}": [1000000.0]}), _qa(y))
        for y in lp.YEARS
    }
    make_year_table(monkeypatch, tables)
    out, qas = lp.join_landprice(stores)
    assert len(out) == 3  # unmatched 행 보존
    o = out.set_index("store_id")
    assert o.loc["L1", "land_price_2024"] == 1000000.0
    assert pd.isna(o.loc["L2", "land_price_2024"])
    assert bool(o.loc["L1", "land_price_match"])
    assert not bool(o.loc["L3", "land_price_match"])


def test_join_guards_against_duplicate_pnu(monkeypatch):
    # 중복 PNU 테이블이 들어오면 행 수 증가 → assert로 방어
    stores = pd.DataFrame({"store_id": ["L1"], "pnu": [PNU_A]})
    dup_tbl = pd.DataFrame({"pnu": [PNU_A, PNU_A],
                            "land_price_2024": [1.0, 2.0]})
    tables = {y: (pd.DataFrame({"pnu": [], f"land_price_{y}": []}), _qa(y))
              for y in lp.YEARS}
    tables[2024] = (dup_tbl, _qa(2024))
    make_year_table(monkeypatch, tables)
    with pytest.raises(AssertionError):
        lp.join_landprice(stores)


def test_pnu_string_and_leading_zero():
    # PNU가 문자열로 유지되고 19자리 leading zero가 보존되는지 (join key 무결성)
    pnu = "0000000000100010001"
    stores = pd.DataFrame({"store_id": ["L1"], "pnu": [pnu]})
    assert stores["pnu"].str.len().iloc[0] == 19
    tbl = pd.DataFrame({"pnu": [pnu], "land_price_2024": [5.0]})
    out = stores.merge(tbl, on="pnu", how="left")
    assert out["land_price_2024"].iloc[0] == 5.0


def test_meta_reference_and_available_at():
    # feature_asof(기준일)와 available_at(공시일)은 별개로 유지되어야 한다
    assert set(lp.LANDPRICE_META) == set(lp.YEARS)
    for y, m in lp.LANDPRICE_META.items():
        assert m["reference_year"] == y
        assert m["feature_asof"] == f"{y}-01-01"
        assert m["available_at"] == f"{y}-04-30"
        assert m["feature_asof"] != m["available_at"]
        assert m["available_at_source"]  # 출처 provenance 필드 존재


def test_available_at_source_urls_recorded():
    # 결정·공시일의 원 출처가 연도별로 실제 URL과 함께 기록되어야 한다
    assert set(lp.AVAILABLE_AT_SOURCES) == set(lp.YEARS)
    for y, src in lp.AVAILABLE_AT_SOURCES.items():
        assert str(y) in src["title"]
        assert src["url"].startswith("https://")
        assert src["note"]
        m = lp.LANDPRICE_META[y]
        assert m["available_at_source_url"] == src["url"]


def test_meta_no_needs_verification_left():
    # 리뷰 후 공시일이 확정되었으므로 NEEDS_VERIFICATION이 남아 있으면 안 된다
    for m in lp.LANDPRICE_META.values():
        assert "NEEDS_VERIFICATION" not in str(m.values())


def test_available_at_blocks_earlier_origin():
    # origin이 공시일 이전이면 그 연도 값은 사용 불가라는 판단이 metadata만으로 가능해야 한다
    avail = pd.Timestamp(lp.LANDPRICE_META[2026]["available_at"])
    assert pd.Timestamp("2026-03-31") < avail   # 사용 금지
    assert pd.Timestamp("2026-04-30") >= avail  # 사용 가능


def test_zero_price_is_preserved_not_na(monkeypatch):
    # 0원 필지를 NA로 바꾸거나 임의 대체하지 않는다
    stores = pd.DataFrame({"store_id": ["L1"], "pnu": [PNU_A]})
    tables = {
        y: (pd.DataFrame({"pnu": [PNU_A], f"land_price_{y}": [0.0]}), _qa(y))
        for y in lp.YEARS
    }
    make_year_table(monkeypatch, tables)
    out, _ = lp.join_landprice(stores)
    for y in lp.YEARS:
        assert out[f"land_price_{y}"].iloc[0] == 0.0
        assert out[f"land_price_{y}"].notna().all()
    # 값이 존재하므로 매칭으로 집계된다 (0 != 결측)
    assert bool(out["land_price_match"].iloc[0])
    # 0은 유효 지가가 아니므로 valid 파생값에서는 제외되고 flag가 선다 (Issue #9)
    assert bool(out["land_price_zero_flag"].iloc[0])
    for y in lp.YEARS:
        assert pd.isna(out[f"land_price_{y}_valid"].iloc[0])


def test_positive_price_is_valid_and_not_flagged(monkeypatch):
    stores = pd.DataFrame({"store_id": ["L1"], "pnu": [PNU_A]})
    tables = {
        y: (pd.DataFrame({"pnu": [PNU_A], f"land_price_{y}": [1234000.0]}), _qa(y))
        for y in lp.YEARS
    }
    make_year_table(monkeypatch, tables)
    out, _ = lp.join_landprice(stores)
    assert not bool(out["land_price_zero_flag"].iloc[0])
    for y in lp.YEARS:
        assert out[f"land_price_{y}_valid"].iloc[0] == 1234000.0


def test_zero_in_one_year_only_keeps_other_years_valid(monkeypatch):
    # 2026만 0원인 실제 패턴: 2024·2025 valid는 살아 있어야 한다
    stores = pd.DataFrame({"store_id": ["L1"], "pnu": [PNU_A]})
    prices = {2024: 3213000.0, 2025: 3408000.0, 2026: 0.0}
    tables = {
        y: (pd.DataFrame({"pnu": [PNU_A], f"land_price_{y}": [prices[y]]}), _qa(y))
        for y in lp.YEARS
    }
    make_year_table(monkeypatch, tables)
    out, _ = lp.join_landprice(stores)
    assert bool(out["land_price_zero_flag"].iloc[0])
    assert out["land_price_2024_valid"].iloc[0] == 3213000.0
    assert out["land_price_2025_valid"].iloc[0] == 3408000.0
    assert pd.isna(out["land_price_2026_valid"].iloc[0])
    assert out["land_price_2026"].iloc[0] == 0.0  # raw는 보존
