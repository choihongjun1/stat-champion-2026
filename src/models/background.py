# -*- coding: utf-8 -*-
"""Shapley 배경 표본의 저장·재현 (#34 리뷰 ①).

배경은 학습 패널의 행 목록이다. store_id를 파일에 남기지 않도록 행마다 `sha256(store_id|origin)`(앞 32자)만
저장하고, 파일 자체의 sha256을 manifest에 적는다. 서빙은 manifest를 읽어 파일 해시를 확인한 뒤, 학습 패널에서
같은 키의 행을 같은 순서로 찾아 배경으로 쓴다 — 파일이 없거나 해시가 다르거나 행을 못 찾으면 멈춘다.

위치: outputs/diagnosis/background/ (gitignore). manifest = background_manifest.json, 행 목록 = background_rows.csv
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config

DEFAULT_DIR = config.REPO_ROOT / "outputs" / "diagnosis" / "background"
DEFAULT_MANIFEST = DEFAULT_DIR / "background_manifest.json"
ROWS_FILE = "background_rows.csv"
KEY_LEN = 32


def row_keys(store_ids, origins) -> np.ndarray:
    return np.array([hashlib.sha256(f"{s}|{o}".encode("utf-8")).hexdigest()[:KEY_LEN]
                     for s, o in zip(store_ids, origins)], dtype=object)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_background(df: pd.DataFrame, idx: np.ndarray, out_dir: Path = DEFAULT_DIR, *, meta: dict | None = None) -> dict:
    """df.iloc[idx]의 (store_id, origin) 키를 순서대로 저장하고 manifest를 쓴다. 반환: manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = df.iloc[np.asarray(idx)]
    pd.DataFrame({"row_key": row_keys(rows["store_id"].astype(str), rows["origin"].astype(str))}).to_csv(
        out_dir / ROWS_FILE, index=False, lineterminator="\n")
    manifest = {"file": ROWS_FILE, "sha256": _sha256(out_dir / ROWS_FILE), "n": int(len(rows)),
                "key": f"sha256(store_id|origin)[:{KEY_LEN}]", **(meta or {})}
    (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
    return manifest


def load_background(manifest_path: Path, df: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """manifest가 가리키는 배경을 df(학습 패널)에서 찾아 위치(저장 순서)를 돌려준다. 반환: (위치, manifest)."""
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"배경 manifest가 없다: {manifest_path} — "
                                "`python -m src.analysis.shapley_background_stability`로 배경을 확정한다")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows_path = manifest_path.parent / manifest["file"]
    if not rows_path.exists():
        raise FileNotFoundError(f"배경 행 파일이 없다: {rows_path}")
    got = _sha256(rows_path)
    if got != manifest["sha256"]:
        raise ValueError(f"배경 행 파일 해시가 manifest와 다르다: {got} ≠ {manifest['sha256']} ({rows_path})")
    want = pd.read_csv(rows_path, dtype=str)["row_key"].to_numpy()
    pos = pd.Series(np.arange(len(df)), index=row_keys(df["store_id"].astype(str), df["origin"].astype(str)))
    pos = pos[~pos.index.duplicated()]
    missing = [k for k in want if k not in pos.index]
    if missing:
        raise ValueError(f"배경 행 {len(missing)}/{len(want)}개가 이 학습 패널에 없다")
    return pos.loc[want].to_numpy(), manifest
