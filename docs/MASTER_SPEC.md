# master_base 명세 (MASTER_SPEC)

`python -m src.data.master`가 생성한다. 손으로 고치지 말고 `src/data/master_schema.py`를 고친 뒤 다시 생성한다.

- 관측 단위: `(store_id, origin)` / 527,934행 / store_id 44,067 / origin 18개 (2021Q1~2025Q2)
- predictor 13개 / 전체 컬럼 54개

## 메모

- predictor는 role == predictor인 컬럼뿐이다. meta/provenance는 모델 입력으로 쓰지 않는다.
- `gu`: 행정구역(`개방자치단체코드`) 기반 정적 지역 변수이며 미래정보를 사용하지 않는다. 3구 × 3업종이라 업종·상권 feature와 상관이 클 수 있으므로 **향후 모델링에서 포함/제외 ablation 대상**이다.
- `land_price`는 18개 origin 중 13개에서 구조적 NA다. 결측 패턴이 origin 시기와 겹치므로 모델 단계에서 포함/제외를 비교한다 (DECISIONS.md 2026-09-23 W2-0 I-3).
- 상권 feature는 T-1 분기 값이다. T-2 sensitivity는 `attach_trdar_features(lag_quarters=2)`로 만든다. origin 2021Q1은 T-1(2020Q4)이 원천에 없어 전부 NA다.
- polygon membership 계열(trdar_cd, trdar_type, in_polygon, spatial_match_method)은 현재 경계의 약한 미래정보 경로 때문에 predictor로 쓰지 않는다 (DECISIONS.md 2026-09-23).
- 업종 단위 상권 feature(점포·추정매출)는 W2-0 I-1 매핑 확정 전까지 포함하지 않는다.
- 온라인 존재감은 Base에 없다. Enriched는 `(store_id, origin)` 유일 테이블을 `master.attach_enriched_table`로 m:1 결합한다.

## 변수

| 변수명                          | role       | group            | 타입             |   결측률(%) | 정의                                                                                                                                                 |
|:-----------------------------|:-----------|:-----------------|:---------------|---------:|:---------------------------------------------------------------------------------------------------------------------------------------------------|
| store_id                     | id         | panel            | object         |     0    | 점포 식별자 '{GR|SR|BT}_{관리번호}'. labels_base 그대로.                                                                                                       |
| source_type                  | id         | panel            | object         |     0    | 인허가 원천 파일 구분 (일반음식점/휴게음식점/미용업).                                                                                                                    |
| origin                       | id         | panel            | object         |     0    | 관측 기준 분기. (store_id, origin)이 master의 행 단위 키.                                                                                                      |
| origin_start                 | id         | panel            | datetime64[ns] |     0    | origin 분기 첫날 (참고용).                                                                                                                                |
| origin_end                   | id         | panel            | datetime64[ns] |     0    | origin 분기 말일. 모든 feature의 as-of 판정 기준 시점.                                                                                                          |
| event_12m                    | label      | panel            | int64          |     0    | origin_end < 폐업일자 <= origin_end+12개월이면 1. labels_base 값을 그대로 보존.                                                                                   |
| maturity_cutoff_used_months  | meta       | panel            | int64          |     0    | 라벨 성숙 컷오프(개월). DECISIONS.md 2026-09-18.                                                                                                            |
| age_months                   | predictor  | license          | int32          |     0    | origin_end 기준 업력(개월) = origin_end - 인허가일자.                                                                                                         |
| biz_type                     | predictor  | license          | object         |     0    | 업종 = 인허가 원천 파일 구분. 인허가 종류로 정해지는 값.                                                                                                                 |
| area                         | predictor  | license          | Float64        |     0.23 | 소재지면적(㎡).                                                                                                                                          |
| has_coord                    | predictor  | license          | bool           |     0    | 인허가 좌표 X/Y 모두 존재 여부.                                                                                                                               |
| feature_asof                 | meta       | license          | datetime64[ns] |     0    | 인허가 기본 feature(age_months/biz_type/area/has_coord)의 기준 시점 = origin_end.                                                                            |
| available_at                 | meta       | license          | datetime64[ns] |     0    | 인허가 기본 feature의 이용 가능 시점 = origin_end.                                                                                                             |
| source_snapshot              | meta       | license          | object         |     0    | 인허가 기본 feature의 원천 파일명.                                                                                                                            |
| gu                           | predictor  | license          | object         |     0    | 자치구(광진/마포/영등포). 인허가 `개방자치단체코드`로 정한 행정구역 기반 정적 지역 변수로, origin과 무관하게 점포마다 고정이며 미래정보를 사용하지 않는다 (DECISIONS.md 2026-09-21). 향후 모델링에서 포함/제외 ablation 대상. |
| pnu                          | provenance | license          | object         |     0.18 | 필지 고유번호(19자리). 공시지가 결합 키. 식별자라 predictor로 쓰지 않는다.                                                                                                  |
| coord_missing                | provenance | license          | bool           |     0    | 인허가 좌표 결측 flag (공간배정 제외, 행 보존).                                                                                                                    |
| coord_suspect                | provenance | license          | bool           |     0    | 인허가 좌표 이상 flag (공간배정 제외, 행 보존).                                                                                                                    |
| gu_mismatch                  | provenance | license          | bool           |     0    | 개방자치단체코드와 주소텍스트의 구가 다른 행 flag (QA).                                                                                                                |
| parse_status                 | provenance | license          | object         |     0    | 지번주소 파싱 상태 (ok / addr_missing / bunji_missing ...).                                                                                                |
| land_price                   | predictor  | land_price       | Float64        |    72.64 | 개별공시지가(원/㎡). available_at(y) <= origin_end인 최대 연도 y의 0원 제외 값. 소급·carry-forward 없음 → origin 2021Q1~2024Q1은 구조적 NA.                                  |
| land_price_year_used         | meta       | land_price       | Int64          |    72.25 | land_price에 사용한 공시 연도. 사용 가능한 연도가 없으면 NA.                                                                                                          |
| land_price_feature_asof      | meta       | land_price       | datetime64[ns] |    72.25 | 사용 연도의 가격 기준일 (y-01-01).                                                                                                                           |
| land_price_available_at      | meta       | land_price       | datetime64[ns] |    72.25 | 사용 연도의 결정·공시일 (y-04-30). landprice.LANDPRICE_META.                                                                                                 |
| land_price_source_snapshot   | meta       | land_price       | object         |    72.25 | 사용 연도의 원천 파일명.                                                                                                                                     |
| trdar_cd                     | provenance | trdar_assignment | object         |    22.43 | within-only 상권 배정 코드. 미배정은 NA (nearest 미사용).                                                                                                       |
| trdar_type                   | provenance | trdar_assignment | object         |    22.43 | 상권 구분(골목/발달/전통시장/관광특구). polygon membership 계열이라 provenance.                                                                                        |
| in_polygon                   | provenance | trdar_assignment | bool           |     0    | 점포 좌표가 상권 polygon 내부인지.                                                                                                                            |
| spatial_ambiguous            | provenance | trdar_assignment | bool           |     0    | polygon 복수 후보 flag (3구 실측 0건).                                                                                                                     |
| spatial_match_method         | provenance | trdar_assignment | object         |     0    | 배정 사유 (within / unmatched_within / excluded_coord_missing / excluded_coord_suspect).                                                               |
| trdar_flow_pop               | predictor  | trdar            | Float64        |    26.66 | T-1 분기 총 유동인구 수 (길단위인구).                                                                                                                           |
| trdar_change_index           | predictor  | trdar            | object         |    26.66 | T-1 분기 상권변화지표 코드 (HH/HL/LH/LL).                                                                                                                    |
| trdar_oper_months_avg        | predictor  | trdar            | Float64        |    26.66 | T-1 분기 운영 영업 개월 평균 (상권변화지표).                                                                                                                       |
| trdar_close_months_avg       | predictor  | trdar            | Float64        |    26.66 | T-1 분기 폐업 영업 개월 평균 (상권변화지표).                                                                                                                       |
| trdar_resident_pop           | predictor  | trdar            | Float64        |    27.01 | T-1 분기 코드의 총 상주인구 수. 갱신 정체 계열 → trdar_resident_value_asof 참조.                                                                                      |
| trdar_worker_pop             | predictor  | trdar            | Float64        |    26.69 | T-1 분기 코드의 총 직장인구 수. 갱신 정체 계열 → trdar_worker_value_asof 참조.                                                                                        |
| trdar_facility_cnt           | predictor  | trdar            | Float64        |    27.13 | T-1 분기 코드의 집객시설 수. 갱신 정체 계열 → trdar_facility_value_asof 참조.                                                                                        |
| trdar_quarter_used           | meta       | trdar            | object         |     0    | 사용한 상권분석 분기 = origin - 1분기 (origin 분기 자체는 사용 금지).                                                                                                  |
| trdar_source_snapshot        | meta       | trdar            | object         |     0    | 상권분석 원천 파일명@sha256 앞 16자리 + 수령일.                                                                                                                   |
| trdar_available_at           | meta       | trdar            | datetime64[ns] |    77.82 | trdar_quarter_used의 확인된 published_at. 미확인이면 NA (날짜를 추정하지 않음).                                                                                      |
| trdar_available_at_basis     | meta       | trdar            | object         |     0    | archive_confirmed / archive_inferred(날짜 NA) / unverified.                                                                                          |
| trdar_value_asof             | meta       | trdar            | object         |    26.66 | 분기 갱신 계열(유동인구·변화지표) 값의 기준 분기 = trdar_quarter_used.                                                                                                 |
| trdar_resident_value_asof    | meta       | trdar            | object         |    27.01 | 상주인구 값이 마지막으로 바뀐 분기 (관측 시작 2021Q1 이전은 알 수 없음).                                                                                                     |
| trdar_worker_value_asof      | meta       | trdar            | object         |    26.69 | 직장인구 값이 마지막으로 바뀐 분기.                                                                                                                               |
| trdar_facility_value_asof    | meta       | trdar            | object         |    27.13 | 집객시설 값이 마지막으로 바뀐 분기 (golmok 기준시점 표기 2020-12).                                                                                                      |
| trdar_geometry_snapshot      | provenance | trdar_assignment | datetime64[s]  |     0    | 상권 polygon 스냅샷 날짜 (2023-10-23).                                                                                                                    |
| trdar_geometry_backcast_flag | provenance | trdar_assignment | bool           |     0    | origin_end < geometry snapshot (현재 경계를 과거 origin에 적용).                                                                                             |
| er_matched                   | provenance | er               | bool           |     0    | 인허가↔소진공 ER 매칭 여부. 7개 스냅샷 union 결과라 predictor 금지 (W2-0 I-2).                                                                                        |
| er_ambiguous                 | provenance | er               | bool           |     0    | ER 후보 복수로 미확정.                                                                                                                                     |
| match_tier                   | provenance | er               | float64        |     0.8  | ER tier (1~4). 미매칭 행에도 마지막 시도 tier가 남을 수 있다.                                                                                                       |
| match_confidence             | provenance | er               | object         |    37.4  | ER 신뢰도 high/medium/low.                                                                                                                            |
| crowded_pnu                  | provenance | er               | boolean        |    65.77 | Tier3 후보 51개 이상 PNU flag.                                                                                                                          |
| sj_entity_id                 | provenance | er               | object         |    37.4  | 소진공 canonical entity id. Enriched 결합 키 후보 (Base에서는 조인 키로 쓰지 않음).                                                                                   |
| unmatched_reason             | provenance | er               | object         |    62.6  | ER 미매칭 사유.                                                                                                                                         |
