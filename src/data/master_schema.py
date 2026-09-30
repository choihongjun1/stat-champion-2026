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
    # --- 상권분석 업종 단위 계열 (점포·추정매출), T-1, observed partial (W2-0 I-1 최종).
    # coverage·partial 메타는 feature 품질 정보라 provenance로 보존한다 (predictor 아님).
    "trdar_biz_store_cnt_observed": ("predictor", "trdar_biz"),
    "trdar_biz_franchise_cnt_observed": ("predictor", "trdar_biz"),
    "trdar_biz_open_rate_observed": ("predictor", "trdar_biz"),
    "trdar_biz_close_rate_observed": ("predictor", "trdar_biz"),
    "trdar_biz_sales_amt_observed": ("predictor", "trdar_biz"),
    "trdar_biz_sales_per_store_observed": ("predictor", "trdar_biz"),
    "trdar_biz_store_n_codes_observed": ("provenance", "trdar_biz"),
    "trdar_biz_store_n_codes_expected": ("provenance", "trdar_biz"),
    "trdar_biz_store_code_coverage": ("provenance", "trdar_biz"),
    "trdar_biz_store_is_partial": ("provenance", "trdar_biz"),
    "trdar_biz_sales_n_codes_observed": ("provenance", "trdar_biz"),
    "trdar_biz_sales_n_codes_expected": ("provenance", "trdar_biz"),
    "trdar_biz_sales_code_coverage": ("provenance", "trdar_biz"),
    "trdar_biz_sales_is_partial": ("provenance", "trdar_biz"),
    "trdar_biz_sales_store_coverage": ("provenance", "trdar_biz"),
    "trdar_biz_source_snapshot": ("meta", "trdar_biz"),
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
    "trdar_biz_store_cnt_observed": (
        "T-1 분기, 해당 biz_type mapped code 중 **점포 row가 있는 코드만**의 전체 점포 수(일반+프랜차이즈) 합. "
        "row 없는 코드를 0으로 채우지 않는다 (row 부재는 점포 0이라는 강한 실증 근거가 있으나 공식 명세 아님). "
        "관측 코드가 없으면 NA."),
    "trdar_biz_franchise_cnt_observed": "T-1 분기, 점포 row가 있는 mapped code의 프랜차이즈 점포 수 합.",
    "trdar_biz_open_rate_observed": (
        "T-1 분기 개업률(%) = 점포 row가 있는 mapped code의 개업 점포 수 합 / 같은 코드의 전체 점포 수 합 × 100. "
        "분모 0이면 NA. 원천 정의상 100%를 넘을 수 있다."),
    "trdar_biz_close_rate_observed": (
        "T-1 분기 폐업률(%) = 점포 row가 있는 mapped code의 폐업 점포 수 합 / 같은 코드의 전체 점포 수 합 × 100. "
        "분모 0이면 NA. 분기 중 폐업이 분기 말 점포 수보다 많으면 100%를 넘는다(원천 `폐업_률`도 동일)."),
    "trdar_biz_sales_amt_observed": (
        "T-1 분기, **매출이 공개된(row가 있는) mapped code만**의 `당월_매출_금액` 합. 매출 row 부재는 0이 아니라 "
        "소수 점포 코드 비공개로 판단하므로 biz_type 전체 매출이 아니라 **하한/부분관측치**다. "
        "과소 정도는 trdar_biz_sales_store_coverage 참조."),
    "trdar_biz_sales_per_store_observed": (
        "T-1 분기 점포당 매출 = 매출 row가 있는 mapped code의 매출 합 / **같은 코드**의 전체 점포 수 합. "
        "전체 biz_type 기준 점포당 매출이 아니라 매출이 공개된 mapped code 집합 기준이다. "
        "분모 0·code set 불일치면 NA (inf·0 대체 없음)."),
    "trdar_biz_store_n_codes_observed": "점포 원천에서 row가 있는 mapped code 수.",
    "trdar_biz_store_n_codes_expected": "해당 biz_type의 mapped code 수 (일반음식점 7 / 휴게음식점 3 / 미용업 3).",
    "trdar_biz_store_code_coverage": "점포 code coverage = observed / expected (0~1).",
    "trdar_biz_store_is_partial": "점포 observed < expected.",
    "trdar_biz_sales_n_codes_observed": "매출 원천에서 row가 있는 mapped code 수.",
    "trdar_biz_sales_n_codes_expected": "해당 biz_type의 mapped code 수.",
    "trdar_biz_sales_code_coverage": "매출 code coverage = observed / expected (0~1).",
    "trdar_biz_sales_is_partial": "매출 observed < expected.",
    "trdar_biz_sales_store_coverage": (
        "매출 row가 있는 mapped code의 점포 수 합 / 해당 biz_type mapped code 전체 점포 수 합 (같은 T-1 점포 원천). "
        "매출 공개 코드에 대응하는 점포 row가 모두 관측된 경우에만 계산하며, 대응 점포 row가 하나라도 없으면 NA. "
        "분모 0이면 NA. 매출 합계가 biz_type 점포의 몇 %를 대표하는지 나타낸다."),
    "trdar_biz_source_snapshot": "점포·추정매출 원천 파일명@sha256 앞 16자리 + 수령일.",
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
    "업종 단위 상권 feature(`trdar_biz_*`, W2-0 I-1): biz_type 그룹 매핑 — 일반음식점 = CS100001·002·003·004·"
    "007·008·009 / 휴게음식점 = CS100005·006·010 / 미용업 = CS200028·029·030. CS 코드 중복 배정 없음 "
    "(CS100006 패스트푸드점·CS100010 커피-음료는 휴게음식점에만). `업태구분명`·`위생업태명`·소진공 cat3는 "
    "origin 시점 값임을 검증할 수 없어 결합 key로 쓰지 않는다. T-1 분기, observed partial 집계.",
    "**점포 row 부재 (WARNING)**: 점포 원천에서 mapped code의 row 부재는 시계열 전이, 명시적 0 row, 매출 원천과의 "
    "교차검증 및 연도별 패턴상 점포 0을 의미하는 것으로 해석할 강한 실증 근거가 있다. 다만 원천 공식 명세로 "
    "확인된 규칙은 아니므로 raw row를 임의 생성하거나 0으로 imputation하지 않고 observed-row 집계와 coverage "
    "metadata를 유지한다. 분석적 해석(structural zero 근거 있음)과 물리적 처리(missing row를 0 row로 만들지 "
    "않음)를 구분한다. 실측 근거와 예외(서울 전체에서 점포>0 → row 없음 직행 15건)는 `outputs/master/qa_report.md` "
    "'row 부재 실증' 절.",
    "**매출 row 부재 ≠ 0**: 매출 0원 row는 원천에 없고 점포 1~2개 코드는 매출 row가 100% 없다(소수 점포 비공개로 "
    "판단). `*_observed` 매출 합계는 biz_type 전체 매출의 하한/부분관측치일 수 있다. coverage 기준으로 행을 "
    "지우거나 NA 처리하지 않고 coverage를 품질 정보로 보존한다.",
    "broad biz_type 매핑 한계: 휴게음식점 편의점(1,158개 점포)·일반음식점 '까페'·미용업 '메이크업업' 등은 실제 세부 "
    "업종과 다른 그룹 값을 받는다. 현재 시점 업태로 예외를 판정하면 시간 기준 불확실성이 생기므로 예외를 두지 않는다.",
    "상권분석 2021~22 CSV는 2023-10-30 재발행본이다(WARNING). 점포·추정매출 계열의 공표일은 계열별로 따로 "
    "확인하지 않았고 golmok 서비스 전체 업데이트 일정과 같다고 본다(WARNING).",
    "온라인 존재감은 Base에 없다. Enriched는 `(store_id, origin)` 유일 테이블을 "
    "`master.attach_enriched_table`로 m:1 결합한다.",
    # --- 예측용 master_score (Issue #35, DECISIONS.md 2026-09-26 "W2-0 예측용 master_score")
    "**예측용 `master_score` (Issue #35)**: `python -m src.data.master_score --origin 2026Q2` → "
    "`outputs/master/master_score.parquet` + `score_meta.json` + `qa_report_score.md`. master_base와 같은 원본 인허가 "
    "로더·적격 판정(`labels.build_long_panel`: 인허가일 ≤ origin_end, 폐업일 없음 또는 > origin_end)과 같은 결합 함수"
    "(`master.attach_*`)를 쓰며, origin은 하나뿐이고 라벨·성숙 컬럼(`event_12m`, `maturity_cutoff_used_months`)이 없다. "
    "나머지 컬럼·이름·dtype·결측 규칙은 master_base와 같다 (`SCORE_EXCLUDED_COLUMNS`).",
    "master_score 시간 정합성: 인허가 feature는 origin_end 기준, 상권은 T-1(2026Q2 → 2026Q1, 2026Q2 값은 2026-08-18 "
    "공표라 사용 금지), 공시지가는 strict as-of(2026Q2 → 2026년 값, 공시 2026-04-30). 2026Q1 상권 공표일은 원천에서 "
    "확인되지 않아 `archive_inferred`(NaT)로 남는다 — 공식 일정(분기 후 약 2개월) 기준 추정이며 검증된 날짜가 아니다. "
    "면적·좌표 유무는 인허가 현재 스냅샷 값이라 as_of 당시 값이라고 단정할 수 없다 (master_base와 같은 한계). "
    "온라인 feature는 결합하지 않는다 — PR #33 `online_features --panel`로 따로 만들어 서빙 `--online-score`로 붙인다.",
    "master_score 모집단 QA: 원천에서 매번 다시 센다(하드코딩 없음). 2026-09-26 실측 2026Q2 = 28,711점포 "
    "(현재 스냅샷 영업 28,832와의 차이 = as_of 이후 폐업 572곳 포함, as_of 이후 개업 693곳 제외). "
    "영업상태명이 '폐업'인데 폐업일자가 없는 점포는 학습 패널과 같이 포함하고 QA에 건수만 기록한다(영업 확인 아님). "
    "역검증: 같은 코드로 만든 2025Q2 score 패널은 master_base 2025Q2(라벨 제외)와 행·값·결측·dtype이 같아야 한다 "
    "(`--compare-master`). 2026-09-26 실측: 29,101행·store_id 집합·68개 컬럼의 값·결측 마스크·dtype 모두 동일 "
    "(저장된 parquet끼리 비교 — parquet은 datetime64[s]를 [ms]로 저장하므로 메모리 값과 비교하지 않는다). "
    "같은 날 master_base 재생성 결과는 이전 파일과 sha256까지 같았다.",
    "master_score = PR #36 serve `--score` 입력: origin 하나(`origin` 컬럼, 예 \"2026Q2\"), store_id 유일, "
    "`predictor_columns()` 전부(`land_price` 포함 — serve가 검증되지 않은 feature로 제외), 진단에 쓰는 `gu`·`biz_type`·"
    "`age_months`·`trdar_cd`. 서비스 검색 대상(현재 영업 점포 등) 정의는 이 패널이 아니라 후속 서빙 통합에서 정한다.",
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


# 예측용 master_score(Issue #35)에서 빼는 라벨·성숙 컬럼. 나머지는 COLUMN_ROLES 그대로다.
SCORE_EXCLUDED_COLUMNS = ("event_12m", "maturity_cutoff_used_months")
# PR #36 serve가 예측용 패널에서 직접 읽는 메타 (predictor 전체와 함께 있어야 한다).
SCORE_REQUIRED_META = ("store_id", "origin", "gu", "biz_type", "age_months", "trdar_cd")


def score_columns() -> list[str]:
    """master_score 컬럼 순서 = master_base 컬럼 순서에서 라벨·성숙 컬럼만 뺀 것."""
    return [c for c in COLUMN_ROLES if c not in SCORE_EXCLUDED_COLUMNS]


def forbidden_predictors_in_registry() -> list[str]:
    """predictor로 등록됐지만 금지 목록에 걸리는 컬럼 (비어 있어야 한다)."""
    return [
        c for c in predictor_columns()
        if c in FORBIDDEN_PREDICTORS or c.startswith(FORBIDDEN_PREDICTOR_PREFIXES)
    ]
