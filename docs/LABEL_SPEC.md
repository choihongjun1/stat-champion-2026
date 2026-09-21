# 라벨 정의 명세 (LABEL_SPEC)

`docs/W1_MDIS_AND_LABEL.md` §B-2 산출물.

## 라벨 정의

| 변수명                         | 타입             |   결측률(%) | 정의                                                                                                                                                                                                                                     |
|:----------------------------|:---------------|---------:|:---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| store_id                    | object         |     0    | 식별자 = 'LIC_' + 개방자치단체코드 + '_' + 관리번호. 3구 combined 기준 중복 0건(실측 확인, 업종 간 교차 충돌 포함).                                                                                                                                                      |
| source_type                 | object         |     0    | 원천 파일 구분 (미용업/일반음식점/휴게음식점).                                                                                                                                                                                                            |
| origin                      | object         |     0    | 관측 기준 분기 (예: '2021Q1'). Long Panel의 행 단위 키(store_id, origin) 중 하나.                                                                                                                                                                     |
| origin_start                | datetime64[ns] |     0    | origin 분기의 첫날. 참고용 - 적격 조건/라벨 창 판정에는 쓰이지 않는다(origin_end 참조).                                                                                                                                                                           |
| origin_end                  | datetime64[ns] |     0    | origin 분기의 마지막날. feature_asof/age_months뿐 아니라 적격 조건(인허가일자<=origin_end<(폐업일자 또는 무한대))과 12개월 라벨 창 기준 시점이기도 하다 - origin_start를 쓰면 feature_asof 시점에 이미 폐업한 점포가 라벨에 남는 시간 누수가 생기므로 전부 origin_end로 통일했다.                                     |
| event_12m                   | int64          |     0    | origin_end 이후 12개월 이내(origin_end < 폐업일자 <= origin_end+12개월) 폐업일자 존재 시 1, 아니면 0. feature_asof(=origin_end) 기준으로 통일해 시간 누수를 방지한다.                                                                                                        |
| age_months                  | int32          |     0    | 파생변수. origin_end 기준 (origin_end - 인허가일자) 개월수. 음수 불가(assert) - 패널 진입 조건상 인허가일자<=origin_end가 이미 보장됨.                                                                                                                                     |
| biz_type                    | object         |     0    | 파생변수 = source_type. 업태구분명(세부 자유텍스트)은 W2 세분화 시 원본에서 별도 사용.                                                                                                                                                                              |
| area                        | Float64        |     0.23 | 파생변수 = 소재지면적 숫자 변환값.                                                                                                                                                                                                                   |
| has_coord                   | bool           |     0    | 파생변수 = 좌표정보(X)/좌표정보(Y) 모두 결측 아님 여부.                                                                                                                                                                                                    |
| feature_asof                | datetime64[ns] |     0    | 해당 행 feature 값의 기준 시점 = origin_end.                                                                                                                                                                                                    |
| source_snapshot             | object         |     0    | 값을 계산한 원천 파일 식별자 = label_schema.SOURCE_SNAPSHOT[source_type].                                                                                                                                                                          |
| available_at                | datetime64[ns] |     0    | feature가 현실에서 이용 가능해진 시점. age_months/biz_type/area/has_coord는 origin 시점에 즉시 확인 가능하므로 feature_asof와 동일값. 주의: event_12m 자체의 신고 지연 리스크는 이 컬럼이 아니라 cohort 선정 단계의 maturity_cutoff_months로 통제한다 (행 단위 available_at을 event_12m에 별도로 부여하지 않음). |
| maturity_cutoff_used_months | int64          |     0    | 이번 실행에 실제로 적용된 성숙 컷오프(개월). 잠정값 여부는 label_schema.PROVISIONAL_MATURITY_CUTOFF_MONTHS와 비교해 확인 가능.                                                                                                                                         |

## 경계 기준 상수

| 상수                                     | 값                                                        |
|:---------------------------------------|:---------------------------------------------------------|
| DISTRICT_CODES                         | {'광진구': '3040000', '마포구': '3130000', '영등포구': '3180000'}  |
| ENCODING_ERRORS_POLICY                 | {'미용업': 'strict', '일반음식점': 'replace', '휴게음식점': 'strict'} |
| EXPECTED_DISTRICT_FILTERED_ROWS        | {'일반음식점': 76453, '휴게음식점': 20484, '미용업': 13418}           |
| MATURITY_CUTOFF_CANDIDATE_RANGE_MONTHS | (3, 6)                                                   |
| PROVISIONAL_MATURITY_CUTOFF_MONTHS     | 1                                                        |
| MIN_ORIGIN_QUARTER                     | 2021Q1                                                   |
| LONG_PANEL_WINDOW_MONTHS               | 12                                                       |

## 제외 사유별 건수

| 사유            |     건수 |
|:--------------|-------:|
| 미용업 3구 외 제외   | 445229 |
| 일반음식점 3구 외 제외 | 461064 |
| 휴게음식점 3구 외 제외 | 626085 |

## 성숙 컷오프 분석 결과

- 권고 컷오프: 1개월 (DECISIONS.md 후보 범위 3~6개월과 별도로 확인 필요 - 실측 데이터 기반 신규 근거)
- 불안정 tail 월: ['2026-09']
- 이번 실행에 실제 적용한 잠정 컷오프: 1개월 (labels_base.parquet의 maturity_cutoff_used_months 컬럼과 일치)
