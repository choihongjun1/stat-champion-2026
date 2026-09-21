# -*- coding: utf-8 -*-
"""W1 산출물 invariant 검사 (W2 진입 게이트).

`docs/W1_FREEZE.md`에 고정한 규칙과 golden count를 **실제 산출물로** 검사한다.
W2 master build 앞에 이 함수를 호출하면, 입력이 freeze 시점과 달라졌을 때
모델링 전에 멈춘다.

실행:
    python -m src.data.w1_invariants

검사는 두 종류로 나눈다.
    STRUCTURAL : 값이 달라져도 항상 성립해야 하는 관계 (키 유일성, 시간 정합성, join 무결성).
                 원본이 갱신돼도 깨지면 안 된다 — 깨지면 버그다.
    GOLDEN     : freeze 시점의 실측 수치. 원본 데이터가 갱신되면 **정상적으로** 달라질 수
                 있으므로, 실패는 "재검토하고 freeze 문서를 갱신하라"는 신호다.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data.config import OUTPUT_DIR, REPO_ROOT

LABELS_PATH = REPO_ROOT / "outputs" / "labels" / "labels_base.parquet"
LICENSES_PATH = OUTPUT_DIR / "licenses_3gu.parquet"
SPATIAL_PATH = REPO_ROOT / "outputs" / "spatial" / "spatial_joined.parquet"
ER_PATH = REPO_ROOT / "outputs" / "matching" / "license_semas_matches.parquet"

# docs/W1_FREEZE.md §6 (main 5279604 재계산값)
GOLDEN = {
    "licenses_rows": 110_347,
    "labels_rows": 527_934,
    "labels_stores": 44_067,
    "labels_events": 61_149,
    "labels_origins": 18,
    "er_matched": 27_430,
    "er_tier": {1: 22_821, 2: 3_046, 3: 1_389, 4: 174},
    "er_ambiguous": 1_344,
    "spatial_within": 82_407,
    "land_price_zero_flag": 10,
}

LABEL_WINDOW_MONTHS = 12


class InvariantError(AssertionError):
    """invariant 위반. 어떤 검사가 왜 깨졌는지 메시지에 담는다."""


def _check(results: list[tuple[str, str, bool, str]], kind: str, name: str,
           ok: bool, detail: str) -> None:
    results.append((kind, name, ok, detail))


def check_structural(labels: pd.DataFrame, lic: pd.DataFrame, spatial: pd.DataFrame,
                     er: pd.DataFrame) -> list[tuple[str, str, bool, str]]:
    r: list[tuple[str, str, bool, str]] = []

    # --- 키 유일성
    _check(r, "STRUCTURAL", "labels (store_id, origin) unique",
           not labels.duplicated(["store_id", "origin"]).any(),
           f"dup={int(labels.duplicated(['store_id', 'origin']).sum())}")
    for name, df in (("licenses", lic), ("spatial", spatial), ("er", er)):
        _check(r, "STRUCTURAL", f"{name} store_id unique", bool(df["store_id"].is_unique),
               f"dup={int(df['store_id'].duplicated().sum())}")

    # --- 시간 정합성 (라벨 leakage)
    m = labels.merge(lic[["store_id", "license_date", "close_date"]], on="store_id", how="left")
    n_unmatched = int(m["license_date"].isna().sum())
    _check(r, "STRUCTURAL", "labels ⊆ licenses", n_unmatched == 0, f"unmatched={n_unmatched}")

    closed_before = int((m["close_date"].notna() & (m["close_date"] <= m["feature_asof"])).sum())
    _check(r, "STRUCTURAL", "close_date <= feature_asof 행 없음", closed_before == 0,
           f"violations={closed_before}")

    window_end = m["feature_asof"] + pd.DateOffset(months=LABEL_WINDOW_MONTHS)
    expected_event = (
        m["close_date"].notna() & (m["close_date"] > m["feature_asof"]) & (m["close_date"] <= window_end)
    ).astype(int)
    mismatch = int((expected_event != m["event_12m"]).sum())
    _check(r, "STRUCTURAL", "event_12m == (feature_asof, +12M] 폐업", mismatch == 0,
           f"mismatch={mismatch}")

    late_license = int((m["license_date"] > m["feature_asof"]).sum())
    _check(r, "STRUCTURAL", "origin_end 이후 인허가 행 없음", late_license == 0,
           f"violations={late_license}")

    observed_until = max(lic["license_date"].max(), lic["close_date"].max())
    last_window = labels["origin_end"].max() + pd.DateOffset(months=LABEL_WINDOW_MONTHS)
    _check(r, "STRUCTURAL", "마지막 label window <= 관측 한계", bool(last_window <= observed_until),
           f"window_end={last_window.date()} observed={observed_until.date()}")

    _check(r, "STRUCTURAL", "available_at == feature_asof",
           bool((labels["available_at"] == labels["feature_asof"]).all()), "")
    _check(r, "STRUCTURAL", "age_months >= 0", bool((labels["age_months"] >= 0).all()),
           f"negatives={int((labels['age_months'] < 0).sum())}")

    # --- join 무결성 (W2 master 조립 시나리오)
    joined = labels
    for name, right, cols in (
        ("licenses", lic, ["store_id", "business_type"]),
        ("spatial", spatial, ["store_id", "in_polygon"]),
        ("er", er, ["store_id", "matched"]),
    ):
        before = len(joined)
        joined = joined.merge(right[cols], on="store_id", how="left", suffixes=("", f"_{name}"))
        _check(r, "STRUCTURAL", f"+{name} join 후 행수 불변", len(joined) == before,
               f"{before} -> {len(joined)}")
    _check(r, "STRUCTURAL", "join 후 (store_id, origin) unique",
           not joined.duplicated(["store_id", "origin"]).any(), "")
    for name, col in (("licenses", "business_type"), ("spatial", "in_polygon"), ("er", "matched")):
        n_null = int(joined[col].isna().sum())
        _check(r, "STRUCTURAL", f"{name} 키 미매칭 0", n_null == 0, f"null={n_null}")

    # --- 공시지가 0 처리 (raw 보존 / valid는 NA)
    for year in (2024, 2025, 2026):
        raw_col, valid_col = f"land_price_{year}", f"land_price_{year}_valid"
        if raw_col in spatial.columns and valid_col in spatial.columns:
            zero_rows = spatial[raw_col] == 0
            _check(r, "STRUCTURAL", f"{valid_col}: raw 0 -> NA",
                   bool(spatial.loc[zero_rows, valid_col].isna().all()),
                   f"zero_rows={int(zero_rows.sum())}")
    if "land_price_zero_flag" in spatial.columns:
        any_zero = pd.concat(
            [spatial[f"land_price_{y}"] == 0 for y in (2024, 2025, 2026)
             if f"land_price_{y}" in spatial.columns], axis=1
        ).any(axis=1)
        _check(r, "STRUCTURAL", "land_price_zero_flag == (어느 연도든 0)",
               bool((spatial["land_price_zero_flag"] == any_zero).all()), "")

    # --- spatial 배정 규칙 (within-only)
    assigned_without_within = int(
        (spatial["trdar_cd"].notna() & (spatial["in_polygon"] != True)).sum()  # noqa: E712
    )
    _check(r, "STRUCTURAL", "trdar_cd는 within일 때만 채워짐", assigned_without_within == 0,
           f"violations={assigned_without_within}")

    # --- ER 보존 규칙
    _check(r, "STRUCTURAL", "ER이 인허가 전체 행 보존", len(er) == len(lic),
           f"er={len(er)} lic={len(lic)}")
    _check(r, "STRUCTURAL", "unmatched 행의 sj_entity_id 결측",
           bool(er.loc[~er["matched"], "sj_entity_id"].isna().all()), "")
    return r


def check_golden(labels: pd.DataFrame, lic: pd.DataFrame, spatial: pd.DataFrame,
                 er: pd.DataFrame) -> list[tuple[str, str, bool, str]]:
    r: list[tuple[str, str, bool, str]] = []
    pairs = [
        ("licenses rows", len(lic), GOLDEN["licenses_rows"]),
        ("labels rows", len(labels), GOLDEN["labels_rows"]),
        ("labels stores", labels["store_id"].nunique(), GOLDEN["labels_stores"]),
        ("labels events", int(labels["event_12m"].sum()), GOLDEN["labels_events"]),
        ("labels origins", labels["origin"].nunique(), GOLDEN["labels_origins"]),
        ("er matched", int(er["matched"].sum()), GOLDEN["er_matched"]),
        ("er ambiguous", int(er["ambiguous"].sum()), GOLDEN["er_ambiguous"]),
        ("spatial within", int(spatial["in_polygon"].sum()), GOLDEN["spatial_within"]),
    ]
    tier_counts = er.loc[er["matched"], "match_tier"].value_counts().to_dict()
    for tier, expected in GOLDEN["er_tier"].items():
        pairs.append((f"er tier{tier}", int(tier_counts.get(float(tier), 0)), expected))
    if "land_price_zero_flag" in spatial.columns:
        pairs.append(("land_price_zero_flag", int(spatial["land_price_zero_flag"].sum()),
                      GOLDEN["land_price_zero_flag"]))
    for name, actual, expected in pairs:
        _check(r, "GOLDEN", name, actual == expected, f"actual={actual:,} expected={expected:,}")
    return r


def load_artifacts() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    missing = [p for p in (LABELS_PATH, LICENSES_PATH, SPATIAL_PATH, ER_PATH) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "W1 산출물이 없습니다: " + ", ".join(str(p) for p in missing)
            + " — standardize → semas → matching → spatial_join → run_labels_stage1 순으로 실행하세요."
        )
    return (pd.read_parquet(LABELS_PATH), pd.read_parquet(LICENSES_PATH),
            pd.read_parquet(SPATIAL_PATH), pd.read_parquet(ER_PATH))


def run(strict_golden: bool = True) -> pd.DataFrame:
    labels, lic, spatial, er = load_artifacts()
    results = check_structural(labels, lic, spatial, er) + check_golden(labels, lic, spatial, er)
    table = pd.DataFrame(results, columns=["종류", "검사", "통과", "상세"])

    failed_structural = table[(table["종류"] == "STRUCTURAL") & ~table["통과"]]
    failed_golden = table[(table["종류"] == "GOLDEN") & ~table["통과"]]
    print(table.to_string(index=False))
    print(f"\nSTRUCTURAL {int((table['종류'] == 'STRUCTURAL').sum()) - len(failed_structural)}"
          f"/{int((table['종류'] == 'STRUCTURAL').sum())} 통과, "
          f"GOLDEN {int((table['종류'] == 'GOLDEN').sum()) - len(failed_golden)}"
          f"/{int((table['종류'] == 'GOLDEN').sum())} 통과")

    if len(failed_structural):
        raise InvariantError("STRUCTURAL invariant 위반:\n" + failed_structural.to_string(index=False))
    if len(failed_golden):
        msg = ("GOLDEN count 불일치 (원본 갱신이면 정상일 수 있음 - docs/W1_FREEZE.md를 갱신하세요):\n"
               + failed_golden.to_string(index=False))
        if strict_golden:
            raise InvariantError(msg)
        print("[경고] " + msg)
    return table


if __name__ == "__main__":
    run()
