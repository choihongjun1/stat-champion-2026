# -*- coding: utf-8 -*-
"""소진공 상가(상권)정보 7개 스냅샷 표준화.

실행:
    python -m src.data.semas

산출물 (outputs/standardized/, git 미추적):
    - semas_panel.parquet     store × snapshot 관측 테이블 (snapshot 컬럼 보존)
    - semas_entities.parquet  entity-level resolution 테이블 (identity.py에서 생성)
    - semas_id_links.parquet  ID 재발급 후보 링크
    - semas_qa_report.md      QA 리포트

시간 누수 규칙: 이 panel/entity 구조는 entity resolution 용도다.
특정 origin의 feature 계산에 origin 이후 스냅샷을 사용하지 않도록
관측 테이블에는 snapshot을 반드시 보존하고, entity 테이블은 feature로 쓰지 않는다.
"""
from __future__ import annotations

import glob
import os
import re

import numpy as np
import pandas as pd
from pyproj import Transformer

from src.data.config import CRS_STD, OUTPUT_DIR, SEMAS_DIR, TARGET_GUS
from src.data.coords import seoul_bbox_5179
from src.data.names import normalize_name

_TF_WGS_TO_STD = Transformer.from_crs("EPSG:4326", CRS_STD, always_xy=True)

# 소진공 raw 컬럼 -> 표준 컬럼
SEMAS_COLUMNS: dict[str, str] = {
    "상가업소번호": "sj_store_id",
    "상호명": "name_raw",
    "지점명": "branch_raw",
    "상권업종대분류코드": "cat1_code",
    "상권업종대분류명": "cat1_name",
    "상권업종중분류코드": "cat2_code",
    "상권업종중분류명": "cat2_name",
    "상권업종소분류코드": "cat3_code",
    "상권업종소분류명": "cat3_name",
    "표준산업분류코드": "ksic_code",
    "표준산업분류명": "ksic_name",
    "시군구코드": "sgg_code",
    "시군구명": "sgg_name",
    "행정동코드": "adm_dong_code",
    "행정동명": "adm_dong_name",
    "법정동코드": "bjd_code",
    "법정동명": "bjd_name",
    "지번코드": "pnu",
    "지번주소": "addr_raw",
    "도로명주소": "road_addr_raw",
    "층정보": "floor_info",
    "호정보": "room_info",
    "경도": "lon",
    "위도": "lat",
}

SNAPSHOT_RE = re.compile(r"_(\d{6})\.csv$")


def list_snapshots(semas_dir=SEMAS_DIR) -> dict[str, str]:
    """{snapshot(YYYYMM): 파일경로} — 파일명에서 스냅샷 시점을 추출, 시간순 정렬."""
    out: dict[str, str] = {}
    for f in sorted(glob.glob(str(semas_dir / "*.csv"))):
        m = SNAPSHOT_RE.search(os.path.basename(f))
        if not m:
            raise ValueError(f"스냅샷 시점을 파일명에서 찾을 수 없음: {f}")
        out[m.group(1)] = f
    return dict(sorted(out.items()))


def combine_name_branch(name_norm, branch_norm) -> str | None:
    """정규화 상호 + 정규화 지점명 결합 표현 (지점명 차이 보강 매칭용).

    입력은 None/NaN일 수 있다 (pandas 결측).
    """
    if name_norm is None or pd.isna(name_norm) or not name_norm:
        return None
    if branch_norm is None or pd.isna(branch_norm) or not branch_norm:
        return str(name_norm)
    name_norm, branch_norm = str(name_norm), str(branch_norm)
    if branch_norm in name_norm:
        return name_norm
    return name_norm + branch_norm


def load_snapshot(snapshot: str, path: str) -> pd.DataFrame:
    """스냅샷 하나를 로딩해 3구 필터 + 표준 schema로 반환한다."""
    df = pd.read_csv(path, dtype=str, usecols=list(SEMAS_COLUMNS))
    if list(df.columns) != list(SEMAS_COLUMNS):
        # usecols는 순서를 보존하지만, 컬럼 자체가 없으면 read_csv가 이미 실패한다.
        df = df[list(SEMAS_COLUMNS)]
    df = df.rename(columns=SEMAS_COLUMNS)
    df = df[df["sgg_name"].isin(TARGET_GUS)].reset_index(drop=True)
    df.insert(0, "snapshot", snapshot)
    df["gu"] = df["sgg_name"]

    # 상호 정규화: 인허가와 동일한 규칙(names.normalize_name)을 적용한다.
    norm = [normalize_name(v) for v in df["name_raw"]]
    df["name_norm"] = [a for a, _ in norm]
    df["name_branch_from_name"] = [b for _, b in norm]  # 상호명 안에 있던 지점 표기
    # 지점명 컬럼 자체도 정규화 (별도 보존)
    branch_norm = [normalize_name(v)[0] for v in df["branch_raw"]]
    df["branch_norm"] = branch_norm
    df["name_branch_norm"] = [
        combine_name_branch(n, b)
        for n, b in zip(df["name_norm"], df["branch_norm"])
    ]

    # 좌표: 경도/위도(EPSG:4326) -> EPSG:5179. raw lon/lat 보존.
    lon = pd.to_numeric(df["lon"], errors="coerce")
    lat = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = lon
    df["lat"] = lat
    missing = (lon.isna() | lat.isna() | (lon <= 0) | (lat <= 0)).astype(bool)
    x = np.full(len(df), np.nan)
    y = np.full(len(df), np.nan)
    valid = ~missing.to_numpy()
    if valid.any():
        tx, ty = _TF_WGS_TO_STD.transform(
            lon.to_numpy(dtype=float)[valid], lat.to_numpy(dtype=float)[valid]
        )
        x[valid] = tx
        y[valid] = ty
    bbox = seoul_bbox_5179()
    in_seoul = (
        (x >= bbox["x_min"]) & (x <= bbox["x_max"])
        & (y >= bbox["y_min"]) & (y <= bbox["y_max"])
    )
    df["x_5179"] = x
    df["y_5179"] = y
    df["coord_missing"] = missing.to_numpy()
    df["coord_suspect"] = valid & ~in_seoul
    return df


def build_panel(semas_dir=SEMAS_DIR) -> pd.DataFrame:
    """7개 스냅샷을 로딩·표준화해 store × snapshot panel을 만든다."""
    snaps = list_snapshots(semas_dir)
    frames = []
    for snap, path in snaps.items():
        df = load_snapshot(snap, path)
        dup = df["sj_store_id"].duplicated().sum()
        if dup:
            raise AssertionError(f"{snap}: 상가업소번호 중복 {dup}건")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def panel_qa(panel: pd.DataFrame) -> list[str]:
    """스냅샷별 기본 QA 라인 생성."""
    lines = ["## 소진공 스냅샷 표준화 QA", ""]
    lines.append("| snapshot | 3구 행수 | " + " | ".join(TARGET_GUS)
                 + " | PNU 19자리 | 좌표 결측 | 좌표 suspect |")
    lines.append("|---|---|" + "---|" * (len(TARGET_GUS) + 3))
    for snap, g in panel.groupby("snapshot"):
        by_gu = g["gu"].value_counts()
        pnu_ok = (g["pnu"].str.len() == 19).mean()
        lines.append(
            f"| {snap} | {len(g):,} | "
            + " | ".join(f"{by_gu.get(x, 0):,}" for x in TARGET_GUS)
            + f" | {pnu_ok:.4f} | {int(g['coord_missing'].sum())} "
            f"| {int(g['coord_suspect'].sum())} |"
        )
    lines.append("")
    # 202503 좌표 이상 등 스냅샷 단위 이상 징후
    for snap, g in panel.groupby("snapshot"):
        rate = g["coord_suspect"].mean()
        if rate > 0.5:
            lines.append(
                f"- **경고: {snap} 스냅샷 좌표의 {rate:.1%}가 서울 bbox 밖** — "
                f"이 스냅샷 좌표는 좌표 기반 매칭에서 제외된다."
            )
    lines.append("")
    return lines


def run() -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("소진공 7개 스냅샷 표준화...")
    panel = build_panel()
    path = OUTPUT_DIR / "semas_panel.parquet"
    panel.to_parquet(path, index=False)
    print(f"저장: {path} ({len(panel):,} rows, snapshots="
          f"{panel['snapshot'].nunique()})")
    return panel


if __name__ == "__main__":
    run()
