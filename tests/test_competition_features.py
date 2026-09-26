# -*- coding: utf-8 -*-
"""W2 경쟁지표 6종 테스트 (합성 인허가 원천)."""
import numpy as np
import pandas as pd
import pytest

from src.data import competition_features as cf
from src.data import labels, master

T = pd.Timestamp("2025-06-30")  # origin 2025Q2
MAPO_DONG = "1144012000"
MAPO_DONG2 = "1144012100"
GJ_DONG = "1121510100"


def lic_row(sid, biz="일반음식점", gu="마포구", pnu="1144012000100010000", bjd=MAPO_DONG,
            lic="2020-01-01", close=None):
    return {"store_id": sid, "business_type": biz, "gu": gu, "pnu": pnu, "bjd_code": bjd,
            "license_date": pd.Timestamp(lic), "close_date": pd.Timestamp(close) if close else pd.NaT}


def licenses(rows):
    return cf.prepare_licenses(pd.DataFrame(rows))


def panel_for(lic, origins=("2025Q2",)):
    """labels.build_long_panel로 만든 패널 (적격 판정을 라벨 파이프라인과 맞춘다)."""
    raw = lic.rename(columns={"license_date": "인허가일자_dt", "close_date": "폐업일자_dt"})
    p = labels.build_long_panel(raw, [pd.Period(o, freq="Q") for o in origins])
    return p[["store_id", "origin", "origin_end"]].sort_values(["origin", "store_id"]).reset_index(drop=True)


def feat(lic, panel=None):
    panel = panel_for(lic) if panel is None else panel
    return cf.build_competition_features(panel, lic).set_index(["store_id", "origin"])


def val(t, sid, col, origin="2025Q2"):
    v = t.loc[(sid, origin), col]
    return None if pd.isna(v) else v


# ---------------------------------------------------------------- 시점 판정
def test_open_rule_matches_label_pipeline():
    rows = [lic_row("A", lic="2025-06-30"), lic_row("B", lic="2025-07-01"),
            lic_row("C", close="2025-06-30"), lic_row("D", close="2025-07-01"), lic_row("E")]
    lic = licenses(rows)
    got = set(lic.loc[cf.is_open_at(lic, T), "store_id"])
    assert got == set(panel_for(lic)["store_id"]) == {"A", "D", "E"}


def test_origin_end_day_open_and_close():
    """당일 개업 → 영업 점포, 당일 폐업 → 영업 아님이지만 폐업 집계에는 포함."""
    lic = licenses([lic_row("S"), lic_row("OPEN_T", lic="2025-06-30"), lic_row("CLOSE_T", close="2025-06-30")])
    t = feat(lic)
    assert val(t, "S", "comp_dong_same_type_cnt") == 1          # OPEN_T만
    assert val(t, "S", "comp_dong_open_4q") == 1                # OPEN_T
    assert val(t, "S", "comp_dong_close_4q") == 1               # CLOSE_T
    assert ("CLOSE_T", "2025Q2") not in t.index


def test_after_origin_events_excluded():
    lic = licenses([lic_row("S"), lic_row("LATE", lic="2025-07-01"), lic_row("D", close="2025-07-01")])
    t = feat(lic)
    assert val(t, "S", "comp_dong_same_type_cnt") == 1          # D는 t에 영업 중, LATE는 아님
    assert val(t, "S", "comp_dong_open_4q") == 0
    assert val(t, "S", "comp_dong_close_4q") == 0               # t 이후 폐업은 세지 않는다


def test_window_boundaries():
    """(t−12개월, t]: 2024-06-30은 제외, 2024-07-01은 포함."""
    lic = licenses([lic_row("S"),
                    lic_row("O_EDGE", lic="2024-06-30"), lic_row("O_IN", lic="2024-07-01"),
                    lic_row("C_EDGE", close="2024-06-30"), lic_row("C_IN", close="2024-07-01")])
    t = feat(lic)
    assert val(t, "S", "comp_dong_open_4q") == 1
    assert val(t, "S", "comp_dong_close_4q") == 1


# ---------------------------------------------------------------- 집계 단위 · 자기 제외
def test_pnu_counts_all_types_and_same_type():
    p1, p2 = "1144012000100010000", "1144012000100020000"
    lic = licenses([lic_row("A", pnu=p1), lic_row("B", pnu=p1), lic_row("C", biz="미용업", pnu=p1),
                    lic_row("D", pnu=p2)])
    t = feat(lic)
    assert val(t, "A", "comp_pnu_cnt") == 2 and val(t, "A", "comp_pnu_same_type_cnt") == 1
    assert val(t, "C", "comp_pnu_cnt") == 2 and val(t, "C", "comp_pnu_same_type_cnt") == 0
    assert val(t, "D", "comp_pnu_cnt") == 0                     # 자기 점포 제외 → 혼자면 0


def test_dong_same_type_excludes_other_types_and_dongs():
    lic = licenses([lic_row("A"), lic_row("B"), lic_row("C", biz="휴게음식점"),
                    lic_row("D", pnu="1144012100100010000", bjd=MAPO_DONG2)])
    t = feat(lic)
    assert val(t, "A", "comp_dong_same_type_cnt") == 1
    assert val(t, "C", "comp_dong_same_type_cnt") == 0
    assert val(t, "D", "comp_dong_same_type_cnt") == 0


def test_self_opening_excluded():
    lic = licenses([lic_row("NEW", lic="2025-01-15"), lic_row("NEW2", lic="2025-02-01"), lic_row("OLD")])
    t = feat(lic)
    assert val(t, "NEW", "comp_dong_open_4q") == 1              # NEW2만 (자기 개업 제외)
    assert val(t, "OLD", "comp_dong_open_4q") == 2


def test_missing_location_is_na_not_zero():
    lic = licenses([lic_row("A"), lic_row("NOPNU", pnu=None), lic_row("NOLOC", pnu=None, bjd=None)])
    t = feat(lic)
    assert val(t, "NOPNU", "comp_pnu_cnt") is None and val(t, "NOPNU", "comp_dong_same_type_cnt") == 1
    for c in cf.FEATURES:
        assert val(t, "NOLOC", c) is None
    assert val(t, "A", "comp_dong_same_type_cnt") == 1          # 위치 없는 점포는 경쟁 점포로도 세지 않는다
    assert t.loc[("NOLOC", "2025Q2"), "comp_location_status"] == "no_location"
    assert t.loc[("NOPNU", "2025Q2"), "comp_location_status"] == "pnu_missing"


def test_gu_bjd_mismatch_is_na_and_not_moved():
    """구(모집단 기준)와 bjd_code 시군구가 다르면 다른 구로 옮기지 않고 위치 지표를 NA로 둔다."""
    lic = licenses([lic_row("A"), lic_row("MIS", pnu="1121510100100010000", bjd=GJ_DONG),  # 마포구인데 광진구 코드
                    lic_row("G", gu="광진구", pnu="1121510100100010000", bjd=GJ_DONG)])
    t = feat(lic)
    for c in cf.FEATURES:
        assert val(t, "MIS", c) is None
    assert val(t, "G", "comp_pnu_cnt") == 0 and val(t, "G", "comp_dong_same_type_cnt") == 0
    assert t.loc[("MIS", "2025Q2"), "comp_location_status"] == "gu_bjd_mismatch"


def test_same_dong_name_different_code_kept_apart():
    """동 이름이 같아도 bjd_code가 다르면 다른 동이다 (이름으로 집계하지 않는다)."""
    lic = licenses([lic_row("M", bjd="1144012000", pnu="1144012000100010000"),
                    lic_row("G", gu="광진구", bjd="1121510100", pnu="1121510100100010000")])
    lic["dong"] = "신수동"  # 이름 컬럼이 있어도 쓰이지 않는다
    t = feat(lic)
    assert val(t, "M", "comp_dong_same_type_cnt") == 0 and val(t, "G", "comp_dong_same_type_cnt") == 0


def test_unknown_gu_rejected():
    with pytest.raises(cf.CompetitionValidationError):
        licenses([lic_row("X", gu="서대문구")])


# ---------------------------------------------------------------- 증감률
def test_density_yoy_and_zero_denominator():
    lic = licenses([lic_row("A"), lic_row("B", lic="2024-10-01"),
                    lic_row("N", pnu="1144012100100010000", bjd=MAPO_DONG2, lic="2025-01-01")])
    t = feat(lic)
    assert val(t, "A", "comp_dong_density_yoy") == pytest.approx((2 - 1) / 1)  # 전체 점포 수 기준
    assert val(t, "N", "comp_dong_density_yoy") is None      # 전년 영업 점포 0 → NA


def test_stock_change_equals_open_minus_close():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(300):
        lic_d = pd.Timestamp("2022-01-01") + pd.Timedelta(days=int(rng.integers(0, 1400)))
        close = lic_d + pd.Timedelta(days=int(rng.integers(1, 900))) if rng.random() < 0.5 else None
        rows.append(lic_row(f"S{i}", biz=rng.choice(["일반음식점", "미용업"]),
                            bjd=rng.choice([MAPO_DONG, MAPO_DONG2]), pnu=None,
                            lic=str(lic_d.date()), close=str(close.date()) if close is not None else None))
    lic = licenses(rows)
    p = panel_for(lic, ["2024Q2", "2025Q2"])
    c = cf.compute(p, lic)
    assert ((c["n_t"] - c["n_prev"]) == (c["opens_all"] - c["closes_all"])).all()
    q = cf.qa_checks(p, lic, cf.build_competition_features(p, lic), c)
    assert q["identity_violations"] == 0 and not q["failures"]


# ---------------------------------------------------------------- 불변성 · 결정성
def test_future_rows_do_not_change_past_origin():
    base_rows = [lic_row("A"), lic_row("B", lic="2025-03-01"), lic_row("C", close="2025-05-01"),
                 lic_row("D", close="2026-02-01")]
    lic = licenses(base_rows)
    before = feat(lic)[cf.FEATURES]
    future = base_rows + [lic_row("F1", lic="2025-08-01"), lic_row("F2", lic="2026-01-01", close="2026-03-01")]
    lic2 = licenses(future)
    lic2.loc[lic2["store_id"] == "A", "close_date"] = pd.Timestamp("2025-11-30")  # t 이후 폐업이 생겨도
    after = feat(lic2, panel_for(lic))[cf.FEATURES]
    pd.testing.assert_frame_equal(before, after)


def test_deterministic():
    lic = licenses([lic_row("A"), lic_row("B", lic="2025-03-01"), lic_row("C", biz="미용업")])
    p = panel_for(lic)
    a, b = cf.build_competition_features(p, lic), cf.build_competition_features(p, lic)
    pd.testing.assert_frame_equal(a, b)


def test_panel_row_not_open_is_rejected():
    lic = licenses([lic_row("A", close="2025-01-01")])
    bad = pd.DataFrame({"store_id": ["A"], "origin": ["2025Q2"], "origin_end": [T]})
    with pytest.raises(cf.CompetitionValidationError):
        cf.build_competition_features(bad, lic)


# ---------------------------------------------------------------- 패널 보존
def test_master_columns_and_rows_unchanged_after_attach():
    """event_12m을 읽지 않고, 결합해도 기존 컬럼·행·라벨이 그대로다. 상권 미배정 점포도 남는다."""
    lic = licenses([lic_row("A"), lic_row("B"), lic_row("C", pnu=None, bjd=None)])
    p = panel_for(lic)
    m = p.assign(event_12m=[0, 1, 0], trdar_cd=["3110001", None, None], age_months=[60, 10, 5])
    snapshot = m.copy()
    t = cf.build_competition_features(m, lic)
    pd.testing.assert_frame_equal(m, snapshot)  # 입력 패널을 바꾸지 않는다
    t2 = cf.build_competition_features(m.assign(event_12m=[1, 0, 1]), lic)
    pd.testing.assert_frame_equal(t, t2)         # 라벨 값과 무관
    joined = master.attach_enriched_table(m, t, "competition")
    assert len(joined) == len(m)
    pd.testing.assert_frame_equal(joined[m.columns], m)
    assert joined.loc[joined["trdar_cd"].isna(), "comp_dong_same_type_cnt"].notna().sum() == 1  # B
    assert set(cf.FEATURES) <= set(joined.columns) and "event_12m" not in t.columns


def test_provenance_columns():
    lic = licenses([lic_row("A")])
    t = cf.build_competition_features(panel_for(lic), lic, source_snapshot="x@1",
                                      raw_last_observed=pd.Timestamp("2026-09-10"))
    r = t.iloc[0]
    assert r["comp_feature_asof"] == T
    assert pd.isna(r["comp_available_at"]) and r["comp_available_at_basis"] == "unverified"
    assert r["comp_location_basis"] == "license_current_address" and r["comp_source_snapshot"] == "x@1"
    assert list(t.columns) == cf.KEY + cf.FEATURES + cf.META
