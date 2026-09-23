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
    # --- 개별공시지가 strict as-of (DECISIONS.md 2026-09-19 / 2026-09-23 W2-0 I-3).
    # available_at(y) <= origin_end인 최대 연도 y의 `_valid` 값 하나. 소급·carry-forward 없음.
    "land_price": ("predictor", "land_price"),
    "land_price_year_used": ("meta", "land_price"),
    "land_price_feature_asof": ("meta", "land_price"),
    "land_price_available_at": ("meta", "land_price"),
    "land_price_source_snapshot": ("meta", "land_price"),
    # --- 상권 배정 (within-only). polygon membership 계열은 현재 경계(2023-06 상가 DB 기준)에
    # 약한 미래정보 경로가 있어 predictor로 쓰지 않는다 (DECISIONS.md 2026-09-23).
    "trdar_cd": ("provenance", "trdar_assignment"),
    "trdar_type": ("provenance", "trdar_assignment"),
    "in_polygon": ("provenance", "trdar_assignment"),
    "spatial_ambiguous": ("provenance", "trdar_assignment"),
    "spatial_match_method": ("provenance", "trdar_assignment"),
    # --- 상권분석 상권 단위 계열, origin 분기 T의 T-1 값 (DECISIONS.md 2026-09-23).
    "trdar_flow_pop": ("predictor", "trdar"),
    "trdar_change_index": ("predictor", "trdar"),
    "trdar_oper_months_avg": ("predictor", "trdar"),
    "trdar_close_months_avg": ("predictor", "trdar"),
    "trdar_resident_pop": ("predictor", "trdar"),
    "trdar_worker_pop": ("predictor", "trdar"),
    "trdar_facility_cnt": ("predictor", "trdar"),
    "trdar_quarter_used": ("meta", "trdar"),
    "trdar_source_snapshot": ("meta", "trdar"),
    "trdar_available_at": ("meta", "trdar"),
    "trdar_available_at_basis": ("meta", "trdar"),
    "trdar_value_asof": ("meta", "trdar"),
    "trdar_resident_value_asof": ("meta", "trdar"),
    "trdar_worker_value_asof": ("meta", "trdar"),
    "trdar_facility_value_asof": ("meta", "trdar"),
    "trdar_geometry_snapshot": ("provenance", "trdar_assignment"),
    "trdar_geometry_backcast_flag": ("provenance", "trdar_assignment"),
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

# docs/MASTER_SPEC.md "정의" 텍스트. COLUMN_ROLES에 컬럼을 추가하면 여기도 추가한다
# (누락 시 write_master_spec_md가 KeyError로 멈춘다 - label_schema.VARIABLE_DEFINITIONS와 같은 규칙).
VARIABLE_DEFINITIONS: dict[str, str] = {
    "store_id": "점포 식별자 '{GR|SR|BT}_{관리번호}'. labels_base 그대로.",
    "source_type": "인허가 원천 파일 구분 (일반음식점/휴게음식점/미용업).",
    "origin": "관측 기준 분기. (store_id, origin)이 master의 행 단위 키.",
    "origin_start": "origin 분기 첫날 (참고용).",
    "origin_end": "origin 분기 말일. 모든 feature의 as-of 판정 기준 시점.",
    "event_12m": "origin_end < 폐업일자 <= origin_end+12개월이면 1. labels_base 값을 그대로 보존.",
    "maturity_cutoff_used_months": "라벨 성숙 컷오프(개월). DECISIONS.md 2026-09-18.",
    "age_months": "origin_end 기준 업력(개월) = origin_end - 인허가일자.",
    "biz_type": "업종 = 인허가 원천 파일 구분. 인허가 종류로 정해지는 값.",
    "area": "소재지면적(㎡).",
    "has_coord": "인허가 좌표 X/Y 모두 존재 여부.",
    "feature_asof": "인허가 기본 feature(age_months/biz_type/area/has_coord)의 기준 시점 = origin_end.",
    "available_at": "인허가 기본 feature의 이용 가능 시점 = origin_end.",
    "source_snapshot": "인허가 기본 feature의 원천 파일명.",
    "gu": ("자치구(광진/마포/영등포). 인허가 `개방자치단체코드`로 정한 행정구역 기반 정적 지역 변수로, "
           "origin과 무관하게 점포마다 고정이며 미래정보를 사용하지 않는다 (DECISIONS.md 2026-09-21). "
           "향후 모델링에서 포함/제외 ablation 대상."),
    "pnu": "필지 고유번호(19자리). 공시지가 결합 키. 식별자라 predictor로 쓰지 않는다.",
    "coord_missing": "인허가 좌표 결측 flag (공간배정 제외, 행 보존).",
    "coord_suspect": "인허가 좌표 이상 flag (공간배정 제외, 행 보존).",
    "gu_mismatch": "개방자치단체코드와 주소텍스트의 구가 다른 행 flag (QA).",
    "parse_status": "지번주소 파싱 상태 (ok / addr_missing / bunji_missing ...).",
    "land_price": ("개별공시지가(원/㎡). available_at(y) <= origin_end인 최대 연도 y의 0원 제외 값. "
                   "소급·carry-forward 없음 → origin 2021Q1~2024Q1은 구조적 NA."),
    "land_price_year_used": "land_price에 사용한 공시 연도. 사용 가능한 연도가 없으면 NA.",
    "land_price_feature_asof": "사용 연도의 가격 기준일 (y-01-01).",
    "land_price_available_at": "사용 연도의 결정·공시일 (y-04-30). landprice.LANDPRICE_META.",
    "land_price_source_snapshot": "사용 연도의 원천 파일명.",
    "trdar_cd": "within-only 상권 배정 코드. 미배정은 NA (nearest 미사용).",
    "trdar_type": "상권 구분(골목/발달/전통시장/관광특구). polygon membership 계열이라 provenance.",
    "in_polygon": "점포 좌표가 상권 polygon 내부인지.",
    "spatial_ambiguous": "polygon 복수 후보 flag (3구 실측 0건).",
    "spatial_match_method": "배정 사유 (within / unmatched_within / excluded_coord_missing / excluded_coord_suspect).",
    "trdar_flow_pop": "T-1 분기 총 유동인구 수 (길단위인구).",
    "trdar_change_index": "T-1 분기 상권변화지표 코드 (HH/HL/LH/LL).",
    "trdar_oper_months_avg": "T-1 분기 운영 영업 개월 평균 (상권변화지표).",
    "trdar_close_months_avg": "T-1 분기 폐업 영업 개월 평균 (상권변화지표).",
    "trdar_resident_pop": "T-1 분기 코드의 총 상주인구 수. 갱신 정체 계열 → trdar_resident_value_asof 참조.",
    "trdar_worker_pop": "T-1 분기 코드의 총 직장인구 수. 갱신 정체 계열 → trdar_worker_value_asof 참조.",
    "trdar_facility_cnt": "T-1 분기 코드의 집객시설 수. 갱신 정체 계열 → trdar_facility_value_asof 참조.",
    "trdar_quarter_used": "사용한 상권분석 분기 = origin - 1분기 (origin 분기 자체는 사용 금지).",
    "trdar_source_snapshot": "상권분석 원천 파일명@sha256 앞 16자리 + 수령일.",
    "trdar_available_at": "trdar_quarter_used의 확인된 published_at. 미확인이면 NA (날짜를 추정하지 않음).",
    "trdar_available_at_basis": "archive_confirmed / archive_inferred(날짜 NA) / unverified.",
    "trdar_value_asof": "분기 갱신 계열(유동인구·변화지표) 값의 기준 분기 = trdar_quarter_used.",
    "trdar_resident_value_asof": "상주인구 값이 마지막으로 바뀐 분기 (관측 시작 2021Q1 이전은 알 수 없음).",
    "trdar_worker_value_asof": "직장인구 값이 마지막으로 바뀐 분기.",
    "trdar_facility_value_asof": "집객시설 값이 마지막으로 바뀐 분기 (golmok 기준시점 표기 2020-12).",
    "trdar_geometry_snapshot": "상권 polygon 스냅샷 날짜 (2023-10-23).",
    "trdar_geometry_backcast_flag": "origin_end < geometry snapshot (현재 경계를 과거 origin에 적용).",
    "er_matched": "인허가↔소진공 ER 매칭 여부. 7개 스냅샷 union 결과라 predictor 금지 (W2-0 I-2).",
    "er_ambiguous": "ER 후보 복수로 미확정.",
    "match_tier": "ER tier (1~4). 미매칭 행에도 마지막 시도 tier가 남을 수 있다.",
    "match_confidence": "ER 신뢰도 high/medium/low.",
    "crowded_pnu": "Tier3 후보 51개 이상 PNU flag.",
    "sj_entity_id": "소진공 canonical entity id. Enriched 결합 키 후보 (Base에서는 조인 키로 쓰지 않음).",
    "unmatched_reason": "ER 미매칭 사유.",
}

# MASTER_SPEC.md에 그대로 싣는 운영 메모.
SPEC_NOTES = [
    "predictor는 role == predictor인 컬럼뿐이다. meta/provenance는 모델 입력으로 쓰지 않는다.",
    "`gu`: 행정구역(`개방자치단체코드`) 기반 정적 지역 변수이며 미래정보를 사용하지 않는다. "
    "3구 × 3업종이라 업종·상권 feature와 상관이 클 수 있으므로 **향후 모델링에서 포함/제외 ablation 대상**이다.",
    "`land_price`는 18개 origin 중 13개에서 구조적 NA다. 결측 패턴이 origin 시기와 겹치므로 "
    "모델 단계에서 포함/제외를 비교한다 (DECISIONS.md 2026-09-23 W2-0 I-3).",
    "상권 feature는 T-1 분기 값이다. T-2 sensitivity는 `attach_trdar_features(lag_quarters=2)`로 만든다. "
    "origin 2021Q1은 T-1(2020Q4)이 원천에 없어 전부 NA다.",
    "polygon membership 계열(trdar_cd, trdar_type, in_polygon, spatial_match_method)은 현재 경계의 약한 "
    "미래정보 경로 때문에 predictor로 쓰지 않는다 (DECISIONS.md 2026-09-23).",
    "업종 단위 상권 feature(점포·추정매출)는 W2-0 I-1 매핑 확정 전까지 포함하지 않는다.",
    "온라인 존재감은 Base에 없다. Enriched는 `(store_id, origin)` 유일 테이블을 "
    "`master.attach_enriched_table`로 m:1 결합한다.",
]

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
# 상권 T-1 기본 lag (분기). T-2는 sensitivity로만 쓴다.
TRDAR_LAG_QUARTERS = 1
# 보유 상권 polygon 스냅샷 (DATA_CATALOG.md §3-1). 이보다 이른 origin은 polygon backcast.
TRDAR_GEOMETRY_SNAPSHOT = "2023-10-23"

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
