# -*- coding: utf-8 -*-
"""좌표 변환·플래그 테스트."""
import pandas as pd

from src.data.coords import seoul_bbox_5179, standardize_coords


def test_seoul_point_transform():
    # 서울 시내 5174 좌표 (실데이터 예시: 마포구 서교동 인근)
    df = standardize_coords(pd.Series(["192000.5"]), pd.Series(["450500.1"]))
    row = df.iloc[0]
    assert not row["coord_missing"]
    assert not row["coord_suspect"]
    bbox = seoul_bbox_5179()
    assert bbox["x_min"] <= row["x_5179"] <= bbox["x_max"]
    assert bbox["y_min"] <= row["y_5179"] <= bbox["y_max"]


def test_raw_preserved():
    df = standardize_coords(pd.Series(["192000.5  "]), pd.Series(["450500.1"]))
    assert df["x_raw"].iloc[0] == 192000.5
    assert df["y_raw"].iloc[0] == 450500.1


def test_missing_coord():
    df = standardize_coords(pd.Series([None, "abc", "0"]), pd.Series(["450500", "450500", "450500"]))
    assert df["coord_missing"].all()
    assert df["x_5179"].isna().all()


def test_out_of_seoul_suspect():
    # 서울에서 멀리 떨어진 좌표 (충남 수준의 y값 — 실데이터에서 실제 관측된 이상 사례)
    df = standardize_coords(pd.Series(["195000"]), pd.Series(["300567"]))
    row = df.iloc[0]
    assert not row["coord_missing"]
    assert row["coord_suspect"]
    # 값 자체는 유지된다 (플래그만)
    assert pd.notna(row["x_5179"])


def test_bbox_sane():
    bbox = seoul_bbox_5179()
    # EPSG:5179 서울 권역 대략값: x ~ 93만~100만, y ~ 193만~197만
    assert 900_000 < bbox["x_min"] < bbox["x_max"] < 1_030_000
    assert 1_900_000 < bbox["y_min"] < bbox["y_max"] < 2_000_000
