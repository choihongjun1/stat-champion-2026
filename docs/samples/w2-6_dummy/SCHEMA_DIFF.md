# 화면 더미(W2-6) ↔ 실제 서빙 출력(W2-2/W2-3) 형식 차이

기준: 화면 더미 `docs/samples/w2-6_dummy/sample_reports.json` / 서빙 출력 `src/models/serve.py`(PR #36)의 `reports.jsonl`
(가린 샘플: `docs/samples/serve_2025Q2_trial/`(PR #36)). 최종 형식은 W2-5 스키마에서 확정합니다.

범례 — **맞춤(서빙)**: 서빙 쪽을 더미에 맞춰 바꿈 / **맞춤(화면)**: 화면 쪽 수정 필요 / **논의**: 팀 결정 필요

## 1. store 블록
| 필드 | 더미 | 서빙 | 방향 |
|---|---|---|---|
| 상호 | `name` | `name` | 같음 |
| 도로명 주소 | `address_road` | `address_road` | 반영 완료(PR #36) |
| 지번 주소 | (검색 인덱스에 `address_jibun`) | `address_jibun` | 반영 완료(PR #36) |
| 동 | `dong` | `dong` | 같음 |
| 인허가일 | `license_date` | `license_date` | 반영 완료(PR #36) |
| MDIS 산업코드 | `mdis_industry_code` | 없음 | **논의**: 처방(W2-4) 쪽에서 붙일지, 서빙이 업종→코드 고정 매핑으로 붙일지 |

## 2. risk 블록
| 필드 | 더미 | 서빙 | 방향 |
|---|---|---|---|
| `peer_group` | "광진구 미용업 업력 유사 구간" | "광진구 미용업" | **맞춤(화면)**: 위험도 백분위는 같은 분기·자치구·업종 안에서 계산 (업력 조건 없음). 업력대 비교는 요인별 `peer_percentile`에만 적용 |
| `calibrated` | true | false | **맞춤(화면)**: 검증 결과 보정 미적용(보정 시 오히려 나빠짐). 화면 노출 불필요 |
| `model` | detect_v0 | detect_v0_enriched | 값만 다름 (온라인 feature 포함 모형) |
| `interval_note` | 없음 | 있음 | **맞춤(화면)**: 구간 설명 문구 — "예측이 흔들릴 수 있는 범위" (폐업 확률의 범위 아님) |

## 3. factors[] — 가장 큰 차이
| 항목 | 더미 | 서빙 | 방향 |
|---|---|---|---|
| 단위 | 개별 변수 (예: "상권 내 같은 업종 점포 수", "…폐업률", "…개업률") | 변수를 묶은 **요인 8개** (예: "동종 업종 경쟁·개폐업") | **맞춤(화면)**: 요인 단위로 표시. 목록은 아래 표 |
| `factor_id` | 없음 | 있음 (고정 id) | **맞춤(화면)**: 처방·정책 연결 키로 사용 |
| `peer_percentile` 의미 | 기여가 큰 요인에 21, 27 등 **낮은 값** (낮을수록 나쁨으로 보임) | **높을수록 위험 기여가 큼** (70 이상이면 설명문에 "상위 N%" 문장) | **맞춤(화면)**: 방향 반대 — 화면 문구 확인 필요 |
| 비용 유형 (공시지가) | 기여값 있음 | 요인 없음 + `unavailable_categories: ["비용"]` | **맞춤(화면)**: 학습 구간에 값이 없어 모형에서 제외. "판단 불가"로 표시 (0 아님) |
| `display`, `data_missing`, `display_note` | 없음 | 있음 | **맞춤(화면)**: `display=false` 요인은 숨김(검토 대기) 또는 회색 "데이터 없음" 배지 |
| `driver`, `values` | 없음 | 있음 | 선택: 온라인 요인 근거 문장, "근거 데이터 보기" |
| `explanation` 문체 | "…많은 편" (값 비교) | "…위험도를 약 N%p 높이는 쪽으로 기여했습니다." (기여 서술) | **논의**: 두 문장을 함께 보여줄지 |
| 요인 개수 | 3~4개 | 8개 전부 (기여 큰 순) | **맞춤(화면)**: 상위 N개만 펼치고 나머지 접기 권장 |

서빙 요인 8개 (`factor_id` — 이름 — 유형)
- `tenure` 업력 — 사업체 구조 / `store_profile` 업종·점포 규모 — 사업체 구조
- `district` 자치구 — 입지·수요 / `trdar_population` 상권 유동·배후 인구 — 입지·수요
- `trdar_vitality` 상권 변화·영업 지속 — 입지·수요 / `online_attention` 온라인 언급(블로그) — 입지·수요
- `peer_competition` 동종 업종 경쟁·개폐업 — 경쟁 / `peer_sales` 동종 업종 매출 수준 — 경쟁
- (`rent_level` 임대료 수준(공시지가) — 비용: 현재 비활성)

유형은 4개: 사업체 구조 / 입지·수요 / 경쟁 / 비용. 더미의 "상권 내 같은 업종 점포당 매출"은 서빙에서 **경쟁** 유형(`peer_sales`)입니다.

## 4. 처방·정책 연결 (W2-4 / W2-7)
더미 `policies[].related_factor`가 이름 문자열이라 서빙 요인과 연결되지 않습니다. `factor_id`로 바꾸는 것을 제안합니다.

| 더미 related_factor | 제안 factor_id | 비고 |
|---|---|---|
| 업력 구간 | `tenure` | |
| 영업장 면적 | `store_profile` | |
| 상권 유동인구 | `trdar_population` | |
| 온라인 노출 채널 수 | `online_attention` (부분) | 서빙 온라인 요인은 **블로그 언급**만. 지도 플랫폼 등록 여부는 `online_presence`(현재 스냅샷, 예측 미사용) |
| 필지 공시지가 수준 | `rent_level` | 현재 비활성 → 연결 요인 없음으로 표시 |

## 5. 서빙에 없는 블록
`prescriptions`(W2-4), `policies`(W2-7), `online_presence`(현재 시점 스냅샷 — 표시 전용)는 서빙 출력에 없습니다.
화면에서 합치는 키는 `store_id`입니다.

## 6. 검색 인덱스 (`sample_search_index.json`)
서빙과 무관한 인허가 기반 목록입니다. 실제 인덱스는 인허가 데이터(`licenses_3gu`)에서 같은 필드(`store_id, name, name_norm,
biz_type, gu, dong, address_road, address_jibun`)로 만들 수 있습니다. 위험도가 들어가지 않으므로 공개 인허가 정보 범위입니다.
