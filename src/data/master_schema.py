# -*- coding: utf-8 -*-
"""W2-0 master dataset 스키마 상수.

master의 모든 컬럼은 COLUMN_ROLES에 역할과 함께 등록한다. 등록되지 않은 컬럼이 master에
들어가거나, 등록된 컬럼이 빠지면 `master.validate_schema()`가 멈춘다.

역할:
    id         : 관측 단위 식별자 (store_id, origin 등)
    label      : 결과변수
    predictor  : 모델 입력으로 쓸 수 있는 feature (origin 시점 정합성 확인된 것만)
    meta       : predictor의 시점 메타 (feature_asof / available_at / source_snapshot 계열)
    provenance : QA·sensitivity·해석용. **모델 입력으로 쓰지 않는다**

모델 단계는 role == "predictor"인 컬럼만 가져간다 (`predictor_columns()`).
"""
from __future__ import annotations

KEY = ["store_id", "origin"]

# 조인 전후로 값이 한 글자도 바뀌면 안 되는 labels_base 컬럼.
LABEL_COLUMNS = [
    "store_id", "source_type", "origin", "origin_start", "origin_end", "event_12m",
    "age_months", "biz_type", "area", "has_coord", "feature_asof", "source_snapshot",
    "available_at", "maturity_cutoff_used_months",
]

# column -> (role, group). 순서가 master의 컬럼 순서다.
COLUMN_ROLES: dict[str, tuple[str, str]] = {
    # --- labels_base (W1 동결)
    "store_id": ("id", "panel"),
    "source_type": ("id", "panel"),
    "origin": ("id", "panel"),
    "origin_start": ("id", "panel"),
    "origin_end": ("id", "panel"),
    "event_12m": ("label", "panel"),
    "maturity_cutoff_used_months": ("meta", "panel"),
    "age_months": ("predictor", "license"),
    "biz_type": ("predictor", "license"),
    "area": ("predictor", "license"),
    "has_coord": ("predictor", "license"),
    "feature_asof": ("meta", "license"),
    "available_at": ("meta", "license"),
    "source_snapshot": ("meta", "license"),
    # --- 점포 정적 속성 (spatial_joined / license_semas_matches)
    "gu": ("predictor", "license"),
    "pnu": ("provenance", "license"),
    "coord_missing": ("provenance", "license"),
    "coord_suspect": ("provenance", "license"),
    "gu_mismatch": ("provenance", "license"),
    "parse_status": ("provenance", "license"),
    # --- 상권 배정 (within-only). polygon membership 계열은 현재 경계(2023-06 상가 DB 기준)에
    # 약한 미래정보 경로가 있어 predictor로 쓰지 않는다 (DECISIONS.md 2026-09-23).
    "trdar_cd": ("provenance", "trdar_assignment"),
    "trdar_type": ("provenance", "trdar_assignment"),
    "in_polygon": ("provenance", "trdar_assignment"),
    "spatial_ambiguous": ("provenance", "trdar_assignment"),
    "spatial_match_method": ("provenance", "trdar_assignment"),
    # --- ER. 7개 스냅샷(2024-12~2026-06) union으로 계산한 값이라 모든 Base origin에 대해
    # origin 이후 정보를 담는다 → provenance/metadata 전용 (DECISIONS.md 2026-09-23 W2-0 I-2).
    "er_matched": ("provenance", "er"),
    "er_ambiguous": ("provenance", "er"),
    "match_tier": ("provenance", "er"),
    "match_confidence": ("provenance", "er"),
    "crowded_pnu": ("provenance", "er"),
    "sj_entity_id": ("provenance", "er"),
    "unmatched_reason": ("provenance", "er"),
}

# 어떤 경우에도 predictor가 될 수 없는 컬럼 (W1_FREEZE.md §8 + W2-0 I-2).
FORBIDDEN_PREDICTORS = {
    # origin 이후 행정상태 / 갱신 시점
    "close_date", "close_date_raw", "status_code", "status_name",
    "detail_status_code", "detail_status_name", "last_modified_raw", "data_updated_raw",
    # 소진공 전체 관측구간
    "entity_first_snapshot", "entity_last_snapshot", "first_snapshot", "last_snapshot",
    "n_snapshots", "has_gap", "id_reissued",
    # ER 결과 (7개 스냅샷 union)
    "matched", "er_matched", "ambiguous", "er_ambiguous", "match_tier", "match_method",
    "match_confidence", "match_score", "name_score", "candidate_count", "crowded_pnu",
    "sj_entity_id", "sj_store_id", "best_sj_store_id", "best_sj_entity_id", "unmatched_reason",
    # 공시지가 wide 컬럼 (origin 시점에 공시되지 않은 연도 포함)
    "land_price_2024", "land_price_2025", "land_price_2026",
    "land_price_2024_valid", "land_price_2025_valid", "land_price_2026_valid",
    "land_price_zero_flag", "land_price_match",
    # 라벨
    "event_12m",
}
FORBIDDEN_PREDICTOR_PREFIXES = ("nearest_", "second_nearest_", "sj_status", "sj_gap")


def predictor_columns(columns=None) -> list[str]:
    """role == predictor인 컬럼. columns를 주면 그 안에 있는 것만."""
    cols = [c for c, (role, _) in COLUMN_ROLES.items() if role == "predictor"]
    if columns is not None:
        present = set(columns)
        cols = [c for c in cols if c in present]
    return cols


def forbidden_predictors_in_registry() -> list[str]:
    """predictor로 등록됐지만 금지 목록에 걸리는 컬럼 (비어 있어야 한다)."""
    return [
        c for c in predictor_columns()
        if c in FORBIDDEN_PREDICTORS or c.startswith(FORBIDDEN_PREDICTOR_PREFIXES)
    ]
