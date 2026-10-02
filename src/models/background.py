# -*- coding: utf-8 -*-
"""Shapley 배경 표본 — Issue #49 S8 규칙의 생성·저장·재현 (#34).

**S8 (Issue #49, 결과 확인 전 등록 2026-10-01 12:02 KST)** — `S8_RULE`:
- 배경 두 개: 업종×자치구 층화 256개, seed `20260931`(주 배경, primary)과 `20261001`(민감도 배경, sensitivity).
  둘 다 기준 origin(2025Q2)의 학습 구간(origin ≤ t−5) 행에서 같은 규칙으로 뽑는다. 같은 입력이면 항상 같은 배경이다.
- 두 배경에서 요인의 **방향**(위험 증가/감소/영향 미미) 또는 **표시 상태**(display)가 다르면 그 해석을 "해석 민감"으로
  표시한다(`diagnose.attach_sensitivity`). 배경 하나를 "가장 안정적"이라고 고르지 않는다.
- 무작위 1,024 배경은 **대조용**이다(배경 선택에 쓰지 않는다): 2026Q2 서빙 대상 200곳, seed `20261002`, 위험 등급별
  low 66·mid 66·high 68(부족분은 다음 등급으로) — `CHECK_1024`, `check_sample`, `draw_check_background`.
- "반전 요인 표시 0건"은 UI 전파 검사라고만 부르고 통계적 안정성의 증명으로 쓰지 않는다.

배경은 **설명(요인 기여·driver·표시 보류)만** 바꾼다. 위험 확률·등급은 모형 예측이라 배경과 무관하다.
Shapley 기여는 예측 분해이지 인과효과가 아니다.

저장: 배경은 학습 패널의 행 목록이다. store_id를 파일에 남기지 않도록 행마다 `sha256(store_id|origin)`(앞 32자)만
저장하고, 파일 sha256과 행 키 내용 해시(index_sha256)를 manifest에 적는다. 진단·서빙은 manifest를 읽어 해시를 확인한 뒤
학습 패널에서 같은 키의 행을 같은 순서로 찾는다 — 파일 없음·해시 불일치·행 수/행 키 불일치·행 누락이면 멈춘다.

위치: outputs/diagnosis/background/ (gitignore). manifest = background_manifest.json,
행 목록 = background_rows_primary.csv · background_rows_sensitivity.csv
만들기: `python -m src.models.background create --master outputs/master/master_base.parquet` (표본 추출만, Shapley 계산 없음)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config

DEFAULT_DIR = config.REPO_ROOT / "outputs" / "diagnosis" / "background"
DEFAULT_MANIFEST = DEFAULT_DIR / "background_manifest.json"
ROWS_FILE = "background_rows.csv"  # 이전(단일 배경) 형식
ROLES = ("primary", "sensitivity")
KEY_LEN = 32

S8_RULE = {
    "rule_ref": "Issue #49 S8 (결과 확인 전 등록 2026-10-01 12:02 KST)",
    "rule_version": "S8-2026-10-01",
    "method": "stratified",
    "strata": "biz_type×gu (층 크기 비례, 층당 최소 1, 남는/부족한 수는 무작위로 맞춤)",
    "n_background": 256,
    "seeds": {"primary": 20260931, "sensitivity": 20261001},
    "pool": "기준 origin의 학습 구간(origin ≤ t−EMBARGO−1) 전체 행 (train_detect.load_master 정렬 순서)",
    "reference_origin": "2025Q2",
    "comparison": "두 배경의 요인 direction(|기여| < 0.001이면 '영향 미미', 그 외 위험 증가/감소) 또는 display가 다르면 "
                  "interpretation_sensitive=True ('해석 민감')",
    "ui_check_note": "'반전 요인 표시 0건'은 UI 전파 검사이며 통계적 안정성의 증명이 아니다",
}
CHECK_1024 = {
    "rule_ref": "Issue #49 S8",
    "purpose": "대조용 — 배경 선택에 쓰지 않는다",
    "method": "random", "n_background": 1024, "seed": 20261002,
    "sample_origin": "2026Q2", "sample_n": 200, "per_band": {"low": 66, "mid": 66, "high": 68},
    "band_order": ["low", "mid", "high"],
    "carry": "등급 점포 수가 배분보다 적으면 전수, 부족분은 다음 등급으로(low→mid→high). 등급 안에서는 store_id 순서로 정렬 후 "
             "seed 고정 무작위. 1,024 배경은 같은 seed의 별도 생성기로 학습 구간에서 무작위 추출",
}
# 기존 결과(문서 근거, 새 계산 없음) — DECISIONS 2026-10-01 #34 항목
PRIOR_EVIDENCE = (
    "이전 채택 배경(층화 256·seed 20260931) vs 무작위 1,024(seed 20261024), 2025Q2 1,500곳(작성자 #36 d4f1de0): 온라인 표시 "
    "라벨 변경 25.9%, 위험 감소↔증가 4.1%(전부 감소→증가), online_review 판정 변경 0.5%, 1순위 일치 92.3%, 온라인 부호 일치 83.7% "
    "(층화 256 5시드 중 최저). 무작위 1,024(seed 20261024) 재계산은 작성자 기록(|온라인 기여|≥0.001 1,350곳, online_review 27곳)과 "
    "일치(2026-10-01 spot-check). 무작위 1,024 자체의 시드 간 안정성은 측정하지 않았다.")


def row_keys(store_ids, origins) -> np.ndarray:
    return np.array([hashlib.sha256(f"{s}|{o}".encode("utf-8")).hexdigest()[:KEY_LEN]
                     for s, o in zip(store_ids, origins)], dtype=object)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def index_sha256(df: pd.DataFrame, idx) -> str:
    """배경 행 키(저장 순서)를 줄바꿈으로 이은 문자열의 sha256 — 행 파일 바이트와 무관한 내용 해시."""
    rows = df.iloc[np.asarray(idx)]
    return hashlib.sha256("\n".join(row_keys(rows["store_id"].astype(str), rows["origin"].astype(str))).encode()).hexdigest()


def sample_stratified(pool_idx: np.ndarray, strata: pd.Series, n: int, seed: int) -> np.ndarray:
    """층화 추출 — 각 층에서 크기 비례로 뽑고(최소 1), 남는/부족한 만큼 무작위로 맞춘다 (배경 실험과 같은 알고리즘:
    seed 20260931 결과가 작성자 manifest 파일 해시 `ffedb33b…`와 같다)."""
    rng = np.random.default_rng(seed)
    s = strata.loc[pool_idx]
    counts = s.value_counts()
    quota = np.maximum(1, np.round(counts / counts.sum() * n)).astype(int)
    picked = []
    for key, q in quota.items():
        cand = np.asarray(pool_idx)[(s.values == key)]
        picked.append(rng.choice(cand, min(q, len(cand)), replace=False))
    out = np.unique(np.concatenate(picked))
    if len(out) > n:
        out = rng.choice(out, n, replace=False)
    elif len(out) < n:
        rest = np.setdiff1d(pool_idx, out)
        out = np.concatenate([out, rng.choice(rest, n - len(out), replace=False)])
    return out


def strata_of(df: pd.DataFrame) -> pd.Series:
    return df["biz_type"].astype(str) + "×" + df["gu"].astype(str)


def draw_s8(df: pd.DataFrame, train_mask: np.ndarray, rule: dict = S8_RULE) -> dict[str, np.ndarray]:
    """S8 두 배경의 위치(df 기준). 같은 df·mask면 항상 같은 결과."""
    pool = np.flatnonzero(np.asarray(train_mask))
    if len(pool) < rule["n_background"]:
        raise ValueError(f"학습 구간 행 {len(pool)}개가 배경 크기 {rule['n_background']}보다 적다")
    st = strata_of(df)
    return {role: sample_stratified(pool, st, rule["n_background"], rule["seeds"][role]) for role in ROLES}


def _write_rows(df: pd.DataFrame, idx, path: Path) -> None:
    rows = df.iloc[np.asarray(idx)]
    pd.DataFrame({"row_key": row_keys(rows["store_id"].astype(str), rows["origin"].astype(str))}).to_csv(
        path, index=False, lineterminator="\n")


def save_background(df: pd.DataFrame, idx: np.ndarray, out_dir: Path = DEFAULT_DIR, *, meta: dict | None = None) -> dict:
    """단일 배경(이전 형식) 저장 — 시험·비교용. S8 운영 배경은 `save_s8`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(df, idx, out_dir / ROWS_FILE)
    manifest = {"file": ROWS_FILE, "sha256": _sha256(out_dir / ROWS_FILE), "n": int(len(idx)),
                "key": f"sha256(store_id|origin)[:{KEY_LEN}]", **(meta or {})}
    (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
    return manifest


def save_s8(df: pd.DataFrame, idx_by_role: dict[str, np.ndarray], out_dir: Path = DEFAULT_DIR, *,
            meta: dict | None = None, rule: dict = S8_RULE) -> dict:
    """S8 두 배경을 저장하고 manifest를 쓴다. 반환: manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bgs = {}
    for role in ROLES:
        f = f"background_rows_{role}.csv"
        _write_rows(df, idx_by_role[role], out_dir / f)
        bgs[role] = {"file": f, "sha256": _sha256(out_dir / f), "n": int(len(idx_by_role[role])),
                     "seed": rule["seeds"][role], "index_sha256": index_sha256(df, idx_by_role[role])}
    manifest = {"format": "s8", "key": f"sha256(store_id|origin)[:{KEY_LEN}]", "backgrounds": bgs,
                **{k: rule[k] for k in ("rule_ref", "rule_version", "method", "strata", "n_background", "pool",
                                        "comparison", "ui_check_note")},
                "check_1024": CHECK_1024, "prior_evidence": PRIOR_EVIDENCE,
                "affects": "설명(요인 기여·driver·표시 보류)만 — 위험 확률·등급은 바꾸지 않는다", **(meta or {})}
    (out_dir / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")
    return manifest


def load_background(manifest_path: Path, df: pd.DataFrame, role: str = "primary") -> tuple[np.ndarray, dict]:
    """manifest가 가리키는 배경(role)을 df(학습 패널)에서 찾아 위치(저장 순서)를 돌려준다. 반환: (위치, manifest).
    S8 형식(`backgrounds`)과 이전 단일 형식(primary만)을 모두 읽는다."""
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"배경 manifest가 없다: {manifest_path} — "
                                "`python -m src.models.background create --master <master_base>`로 S8 배경을 만든다")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "backgrounds" in manifest:
        if role not in manifest["backgrounds"]:
            raise ValueError(f"manifest에 {role} 배경이 없다")
        entry = manifest["backgrounds"][role]
    elif role == "primary":
        entry = manifest
    else:
        raise ValueError(f"이전 형식(단일 배경) manifest에는 {role} 배경이 없다 — S8 manifest를 다시 만든다")
    rows_path = manifest_path.parent / entry["file"]
    if not rows_path.exists():
        raise FileNotFoundError(f"배경 행 파일이 없다: {rows_path}")
    got = _sha256(rows_path)
    if got != entry["sha256"]:
        raise ValueError(f"배경 행 파일 해시가 manifest와 다르다: {got} ≠ {entry['sha256']} ({rows_path})")
    want = pd.read_csv(rows_path, dtype=str)["row_key"].to_numpy()
    if "n" in entry and len(want) != int(entry["n"]):
        raise ValueError(f"배경 행 수가 manifest와 다르다: {len(want)} ≠ {entry['n']}")
    pos = pd.Series(np.arange(len(df)), index=row_keys(df["store_id"].astype(str), df["origin"].astype(str)))
    pos = pos[~pos.index.duplicated()]
    missing = [k for k in want if k not in pos.index]
    if missing:
        raise ValueError(f"배경 행 {len(missing)}/{len(want)}개가 이 학습 패널에 없다")
    idx = pos.loc[want].to_numpy()
    if entry.get("index_sha256") and index_sha256(df, idx) != entry["index_sha256"]:
        raise ValueError("배경 행 키 내용 해시가 manifest의 index_sha256과 다르다")
    return idx, manifest


def create(master_path: Path, out_dir: Path = DEFAULT_DIR, *, origin: str | None = None, rule: dict = S8_RULE) -> dict:
    """S8 두 배경을 뽑아 저장한다 (Shapley 계산 없음). 반환: manifest."""
    from src.models import splits, train_detect

    df = train_detect.load_master(Path(master_path))
    origins = splits.sorted_origins(df)
    origin = origin or rule["reference_origin"]
    t = origins.index(origin)
    train_mask = df["origin"].isin(origins[: t - train_detect.EMBARGO]).to_numpy()
    idx = draw_s8(df, train_mask, rule)
    return save_s8(df, idx, out_dir, rule=rule, meta={
        "pool_origin": origin, "pool_origins": f"{origins[0]}~{origins[t - train_detect.EMBARGO - 1]}",
        "pool_rows": int(train_mask.sum()), "source_master_sha256": train_detect.sha256(Path(master_path))})


# ---------------------------------------------------------------------------- S8 무작위 1,024 대조 (준비)
def check_sample(risk: pd.DataFrame, rule: dict = CHECK_1024) -> pd.DataFrame:
    """2026Q2 서빙 대상(risk: store_id, band)에서 대조 표본을 뽑는다. 등급별 배분(low 66·mid 66·high 68), 부족분은 다음
    등급으로 이월. 같은 입력이면 같은 표본. 반환: 고른 행(store_id, band) — 등급 순서·store_id 순서."""
    if risk["store_id"].duplicated().any():
        raise ValueError("대조 표본 입력에 store_id 중복")
    rng = np.random.default_rng(rule["seed"])
    out, carry = [], 0
    for band in rule["band_order"]:
        g = risk[risk["band"] == band].sort_values("store_id").reset_index(drop=True)
        quota = rule["per_band"][band] + carry
        take = min(quota, len(g))
        carry = quota - take
        pick = np.sort(rng.choice(len(g), take, replace=False)) if take else np.array([], dtype=int)
        out.append(g.iloc[pick])
    return pd.concat(out, ignore_index=True)[["store_id", "band"]]


def draw_check_background(df: pd.DataFrame, train_mask: np.ndarray, rule: dict = CHECK_1024) -> np.ndarray:
    """대조용 무작위 1,024 배경 (같은 seed의 별도 생성기, 학습 구간에서)."""
    pool = np.flatnonzero(np.asarray(train_mask))
    return np.random.default_rng(rule["seed"]).choice(pool, min(rule["n_background"], len(pool)), replace=False)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#34 Shapley 배경 — Issue #49 S8 (표본 추출만, Shapley 계산 없음)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="S8 두 배경(층화 256 seed 20260931·20261001)을 만들어 manifest 저장")
    c.add_argument("--master", type=Path, default=config.MASTER_BASE_PATH)
    c.add_argument("--origin", default=None, help=f"학습 구간 기준 origin (기본 {S8_RULE['reference_origin']})")
    c.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    s = sub.add_parser("check-sample", help="S8 무작위 1,024 대조용 200곳 표본 (서빙 risk: store_id, band)")
    s.add_argument("--risk", type=Path, required=True, help="2026Q2 서빙 risk 표 (parquet, store_id·band)")
    s.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "create":
        m = create(a.master, a.out_dir, origin=a.origin)
        for role, e in m["backgrounds"].items():
            print(f"{role}: {e['n']}개 (seed {e['seed']}, 학습 구간 {m['pool_origins']}) sha256 {e['sha256'][:12]}…")
    else:
        risk = pd.read_parquet(a.risk, columns=["store_id", "band"]).drop_duplicates("store_id")
        smp = check_sample(risk)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        smp.to_csv(a.out, index=False)
        print(f"대조 표본 {len(smp)}곳 {smp['band'].value_counts().to_dict()} → {a.out}")


if __name__ == "__main__":
    main()
