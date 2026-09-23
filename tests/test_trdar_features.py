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
