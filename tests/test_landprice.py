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


def test_meta_structure_has_needs_verification():
    for y, m in lp.LANDPRICE_META.items():
        assert m["reference_year"] == y
        assert m["feature_asof"] == f"{y}-01-01"
        assert m["available_at"] == "NEEDS_VERIFICATION"
