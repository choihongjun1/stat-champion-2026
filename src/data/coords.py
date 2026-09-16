# -*- coding: utf-8 -*-
"""좌표 표준화: EPSG:5174 (raw, audit 검증) → EPSG:5179.

- x_raw/y_raw는 보존한다.
- 컬럼명은 투영좌표계이므로 x/y를 유지한다 (lon/lat이라고 부르지 않는다).
- coord_missing: 좌표 결측 또는 0 이하 값
- coord_suspect: 변환 후 서울 경계(bbox) 밖 — 값은 유지하고 플래그만 남긴다
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pyproj import Transformer

from src.data.config import CRS_RAW, CRS_STD, SEOUL_BBOX_4326

_TF_RAW_TO_STD = Transformer.from_crs(CRS_RAW, CRS_STD, always_xy=True)
_TF_WGS_TO_STD = Transformer.from_crs("EPSG:4326", CRS_STD, always_xy=True)


def seoul_bbox_5179() -> dict[str, float]:
    """서울 대략 경계(EPSG:4326)를 5179로 변환한 bbox (sanity check 용)."""
    b = SEOUL_BBOX_4326
    lons = [b["lon_min"], b["lon_min"], b["lon_max"], b["lon_max"]]
    lats = [b["lat_min"], b["lat_max"], b["lat_min"], b["lat_max"]]
    xs, ys = _TF_WGS_TO_STD.transform(lons, lats)
    return {
        "x_min": float(min(xs)), "x_max": float(max(xs)),
        "y_min": float(min(ys)), "y_max": float(max(ys)),
    }


def standardize_coords(x_raw: pd.Series, y_raw: pd.Series) -> pd.DataFrame:
    """raw 좌표 문자열 Series에서 5179 좌표와 플래그를 생성한다."""
    x = pd.to_numeric(x_raw, errors="coerce")
    y = pd.to_numeric(y_raw, errors="coerce")

    missing = x.isna() | y.isna() | (x <= 0) | (y <= 0)
    missing = missing.fillna(True).astype(bool)

    x5179 = np.full(len(x), np.nan)
    y5179 = np.full(len(y), np.nan)
    valid = ~missing.to_numpy()
    if valid.any():
        tx, ty = _TF_RAW_TO_STD.transform(
            x.to_numpy(dtype=float)[valid], y.to_numpy(dtype=float)[valid]
        )
        x5179[valid] = tx
        y5179[valid] = ty

    bbox = seoul_bbox_5179()
    in_seoul = (
        (x5179 >= bbox["x_min"]) & (x5179 <= bbox["x_max"])
        & (y5179 >= bbox["y_min"]) & (y5179 <= bbox["y_max"])
    )
    suspect = valid & ~in_seoul

    return pd.DataFrame(
        {
            "x_raw": x,
            "y_raw": y,
            "x_5179": x5179,
            "y_5179": y5179,
            "coord_missing": missing.to_numpy(),
            "coord_suspect": suspect,
        },
        index=x_raw.index,
    )
