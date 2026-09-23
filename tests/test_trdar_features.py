# -*- coding: utf-8 -*-
"""상권 단위 feature 테이블 테스트 (합성 데이터)."""
import pandas as pd

from src.data import trdar_features as tf


def test_quarter_code_to_label():
    s = pd.Series(["20211", "20244", "20262"])
    assert tf.quarter_code_to_label(s).tolist() == ["2021Q1", "2024Q4", "2026Q2"]


def test_value_asof_tracks_last_change_per_trdar():
    df = pd.DataFrame({
        "trdar_cd": ["A", "A", "A", "A", "B", "B"],
        "quarter": ["2022Q3", "2022Q4", "2023Q1", "2023Q2", "2022Q4", "2023Q1"],
        "v": pd.array([10, 20, 20, 20, 20, 20], dtype="Float64"),
    })
    # 입력 순서를 섞어도 결과는 원래 index에 맞춰 돌아와야 한다
    shuffled = df.sample(frac=1, random_state=0)
    asof = tf.value_asof(shuffled, "v")
    assert asof.loc[df.index].tolist() == ["2022Q3", "2022Q4", "2022Q4", "2022Q4",
                                           "2022Q4", "2022Q4"]


def test_availability_records_only_confirmed_dates():
    q = pd.Series(["2021Q4", "2023Q4", "2022Q2"])
    a = tf.availability(q)
    assert a["trdar_available_at"].iloc[0] == pd.Timestamp("2022-03-03")
    assert pd.isna(a["trdar_available_at"].iloc[1])  # 추정 사례는 날짜를 만들지 않는다
    assert pd.isna(a["trdar_available_at"].iloc[2])
    assert a["trdar_available_at_basis"].tolist() == [
        tf.BASIS_CONFIRMED, tf.BASIS_INFERRED, tf.BASIS_UNVERIFIED]


def test_confirmed_and_inferred_do_not_overlap():
    assert not set(tf.PUBLISHED_AT_CONFIRMED) & tf.PUBLISHED_AT_INFERRED_QUARTERS


# --- 업종 단위 (W2-0 I-1)
def _store(rows):
    return pd.DataFrame(rows, columns=["trdar_cd", "quarter", "code", "store_total",
                                       "store_franchise", "store_open", "store_close"])


def _sales(rows):
    return pd.DataFrame(rows, columns=["trdar_cd", "quarter", "code", "sales_amt"])


def _biz_table():
    store = _store([
        ("A", "2025Q1", "CS100005", 4, 1, 1, 2),
        ("A", "2025Q1", "CS100010", 6, 2, 0, 0),   # CS100006은 row 없음
        ("A", "2025Q1", "CS100001", 0, 0, 0, 1),   # 일반음식점: 명시적 0 row만
        ("A", "2025Q2", "CS100001", 5, 0, 1, 0),
    ])
    sales = _sales([
        ("A", "2025Q1", "CS100010", 600.0),
        ("A", "2025Q2", "CS100001", 50.0),
        ("A", "2025Q2", "CS100002", 20.0),         # 매출 row는 있는데 점포 row 없음
    ])
    g = tf.code_level_grid(store, sales, {"A"}, {"2025Q1", "2025Q2"})
    return tf.aggregate_biz(g).set_index(["trdar_cd", "quarter", "biz_type"])


def test_biz_mapping_has_no_duplicate_and_fixed_codes():
    codes = [c for cs in tf.BIZ_CODE_MAP.values() for c in cs]
    assert len(codes) == len(set(codes)) == 13
    assert tf.CODE_TO_BIZ["CS100006"] == tf.CODE_TO_BIZ["CS100010"] == "휴게음식점"
    assert tf.BIZ_N_CODES_EXPECTED == {"일반음식점": 7, "휴게음식점": 3, "미용업": 3}


def test_biz_observed_partial_does_not_fill_zero():
    t = _biz_table()
    r = t.loc[("A", "2025Q1", "휴게음식점")]
    assert r["trdar_biz_store_n_codes_observed"] == 2
    assert r["trdar_biz_store_n_codes_expected"] == 3
    assert abs(r["trdar_biz_store_code_coverage"] - 2 / 3) < 1e-12
    assert r["trdar_biz_store_is_partial"]
    assert r["trdar_biz_store_cnt_observed"] == 10
    assert r["trdar_biz_open_rate_observed"] == 10.0
    assert r["trdar_biz_close_rate_observed"] == 20.0
    assert r["trdar_biz_sales_n_codes_observed"] == 1
    assert r["trdar_biz_sales_amt_observed"] == 600.0
    # 분자·분모 같은 code set: 매출 있는 CS100010의 점포 6개로만 나눈다 (전체 10개가 아님)
    assert r["trdar_biz_sales_per_store_observed"] == 100.0
    assert r["trdar_biz_sales_store_coverage"] == 0.6

    b = t.loc[("A", "2025Q1", "미용업")]  # row가 하나도 없음 → 값 NA, 메타만 0
    assert b["trdar_biz_store_n_codes_observed"] == 0
    assert b["trdar_biz_store_code_coverage"] == 0
    assert all(pd.isna(b[c]) for c in tf.BIZ_FEATURES + ["trdar_biz_sales_store_coverage"])


def test_biz_zero_denominator_is_na_not_inf():
    t = _biz_table()
    r = t.loc[("A", "2025Q1", "일반음식점")]  # 명시적 0 row만
    assert r["trdar_biz_store_cnt_observed"] == 0
    assert pd.isna(r["trdar_biz_open_rate_observed"])
    assert pd.isna(r["trdar_biz_sales_store_coverage"])
    assert pd.isna(r["trdar_biz_sales_per_store_observed"])


def test_biz_per_store_na_when_sales_code_lacks_store_row():
    r = _biz_table().loc[("A", "2025Q2", "일반음식점")]
    assert r["trdar_biz_sales_amt_observed"] == 70.0   # 관측된 매출 코드 합
    assert pd.isna(r["trdar_biz_sales_per_store_observed"])  # code set 불일치 → NA
    assert r["trdar_biz_sales_store_coverage"] == 1.0  # CS100001 점포 5 / 전체 5
