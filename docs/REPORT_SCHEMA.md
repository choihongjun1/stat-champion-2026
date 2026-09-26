# W2-5 결과 스키마 (REPORT_SCHEMA)

- 상태: **schema 0.2 초안** (2026-09-26). 기계 판독 정의는 `src/serving/report_schema.json`(JSON Schema 2020-12),
  스키마로 표현할 수 없는 교차 규칙은 `src/serving/report_validation.py`가 검사한다. 이 문서와 두 파일이 다르면 코드가 틀린 것으로 보고 같은 PR에서 고친다.
- 근거: DECISIONS 2026-09-26 "W2-5 결과 스키마·서빙 구조", PR #36 `reports.jsonl`(schema 0.1, 모델 출력의 기준),
  PR #37 `SCHEMA_DIFF.md`(화면 요구사항), PR #38 `FACTOR_POLICY_LINKS.md`(정책 연결 규칙), Issue #29(동 리포트), Issue #35(예측용 master).
- 범위: 점포 리포트 레코드, 빌드 입력(서빙 출력·정책 원천·온라인 스냅샷), 검색 인덱스 행, 동 요약 행의 **형식**과 SQLite 정본 빌드(§11).
  검색 인덱스·동 요약(§12), 정적 JSON 번들(§13)까지 구현했다. 실명 점포 데이터의 공개 배포는 하지 않는다(§9).

## 0. 현재 상태와 의존성 (2026-09-26)

- **정본과 파생본**: SQLite(`outputs/serving/report.sqlite`)가 로컬 정본이고, 정적 JSON 번들은 정본에서만 만드는 파생본이다(§1·§11·§13).
- **입력·출력 버전**: PR #36 serve 출력 0.1은 입력 검증과 정본 구조까지만 된다(`final_contract = not_ready`).
  R1~R3(`missing_reason`·`hold_reason`·`영향 미미`)을 반영한 0.1.1 입력만 최종 0.2 리포트가 되고 정적 번들로 내보낼 수 있다(§8·§10·§11).
  **2026-09-26 현재 PR #36에는 R1~R3이 반영되지 않았다.**
- **실제 2026Q2 점포 리포트는 Issue #35(예측용 `master_score`)에 의존한다.** 이 PR은 실제 serve 실행·실제 전체 파이프라인을 돌리지 않았다.
  테스트는 합성 데이터, PR #36 가린 샘플(0.1 입력 검증, 저장소 밖에서 수동 확인), 인허가 기반 점포 수 실측으로만 했다.
- **정책 연결 규칙은 PR #38 `FACTOR_POLICY_LINKS.md` 초안을 따른다. PR #38은 아직 병합되지 않았다** — 규칙이 바뀌면 `build_db.match_policies`를 함께 고친다.
  실제 W2-7 정책 원천과 PR #21 온라인 존재감은 이 PR에서 결합하지 않았다(형식만 정의).
- **기술적 계약 통과 ≠ 공개 승인**: `technical_gate.passed`는 코드 검증 결과이고, `publication_approved`는 실명·주소·store_id·개별 위험도
  결합 데이터의 공개 승인이다. 승인 절차가 없어 항상 false이며, **실제 전체 점포 데이터의 공개 배포는 별도 승인 전 금지**다(§9·§13).
- **미정**: 동 요약 소표본 하한값 `min_cell_n`, 한 등급 100% 공개 칸(속성 노출) 처리, 리포트 파일 분할·정적 호스팅 방식(§14).

## 1. 산출물과 역할

| 산출물 | 역할 | 위치 | 저장소 커밋 |
|---|---|---|---|
| `reports.jsonl` (PR #36 serve) | **빌드 입력.** 모형 추론·진단 결과. W2-5는 이 형식에만 의존하고 모델 코드를 import하지 않는다 | `outputs/serve/…` | 안 함 |
| 정책 원천 `policies.json` (W2-7) | **빌드 입력.** `$defs/policy_source` | 팀 공유 | 원천 정책 정보만이라 가능 (W2-7 결정) |
| 인허가 표준화 `licenses_3gu.parquet` | **빌드 입력.** 상호·주소·동·인허가일·현재 상태의 단일 출처 | `outputs/standardized/` | 안 함 |
| SQLite (`report.sqlite`) | **로컬 정본.** 위 입력을 합친 결과 + 실행 provenance | `outputs/serving/` | 안 함 |
| 정적 JSON 번들 | **파생본.** SQLite에서만 생성하며 브라우저가 서버 없이 읽는다 (§13) | `outputs/serving/static_private/` (로컬 비공개) | 안 함 (합성 샘플만 `docs/samples/w2-5/`) |

- 정적 JSON은 SQLite를 거치지 않고 만들지 않는다. 정적 JSON을 고쳐 SQLite에 되돌리지 않는다.
- 실명·주소·store_id와 위험도가 연결된 실제 결과는 저장소에 커밋하지 않는다. 정적 배포 전에는 검증 게이트(§9)를 통과해야 한다.

## 2. 레코드 구조 (점포 1곳)

```
{
  "_schema_version": "0.2",
  "store_id": "GR_…",               // 공개 샘플은 SAMPLE-NNN
  "score_origin": "2026Q2",
  "as_of": "2026-06-30",            // score_origin 분기 말일
  "store": {…, "status": {…}},
  "risk": {…},
  "factors": [ … ],                 // 기여 큰 순
  "unavailable_categories": ["비용"],
  "prescriptions": [],              // W3 전까지 빈 배열 또는 unavailable 항목만
  "online_presence": {…} | null,
  "policy_matching": "performed" | "not_performed",
  "policies": [ … ],
  "disclaimer": "…"
}
```

모든 키는 **항상 존재한다** (`required`). 값이 없을 수 있는 필드는 `null`을 허용하며, 키를 생략하는 방식은 쓰지 않는다.
정의에 없는 키는 허용하지 않는다(`additionalProperties: false`) — 화면이 모르는 필드에 의존하지 않게 한다.

## 3. 블록별 필드

표의 **출처**: `serve` = PR #36 `reports.jsonl`, `build` = W2-5 빌드가 붙임, `lic` = 인허가 표준화 테이블, `W2-7` = 정책 원천.

### 3-1. 최상위

| 필드 | 타입 | null | 출처 | 규칙 |
|---|---|---|---|---|
| `_schema_version` | `"0.2"` | 아니오 | build | 계약 버전. serve 입력은 `"0.1"` |
| `store_id` | string | 아니오 | serve | `{GR\|SR\|BT}_{관리번호}` / 샘플 `SAMPLE-NNN` / 더미 `DUMMY-…` |
| `score_origin` | `YYYYQn` | 아니오 | build | serve 실행의 `serve_meta.json` `score_origin`. 우선 대상 2026Q2 |
| `as_of` | date | 아니오 | serve | `score_origin`의 분기 말일과 같아야 한다 (검증) |
| `unavailable_categories` | category[] | 아니오 (빈 배열 가능) | serve | 모형에 활성 요인이 하나도 없는 유형. 이 유형의 요인은 `factors`에 없어야 한다 (검증) |
| `policy_matching` | `performed`/`not_performed` | 아니오 | build | 정책 원천으로 매칭을 실제로 계산했는지. `not_performed`면 `policies`는 빈 배열이어야 하며(스키마), 화면은 "해당 정책 없음"이 아니라 "정책 정보 준비 중"으로 표시한다 |
| `disclaimer` | string | 아니오 | serve | 진단 화면 하단 고정 문구 |

### 3-2. `store`

| 필드 | 타입 | null | 출처 | 규칙 |
|---|---|---|---|---|
| `biz_type` | enum 일반음식점/휴게음식점/미용업 | 아니오 | serve | 인허가 종류 |
| `gu` | enum 광진구/마포구/영등포구 | 아니오 | serve | `개방자치단체코드` 기준 (DECISIONS 2026-09-21) |
| `dong` | string | 예 | lic | **법정동** (인허가 지번주소에서 파싱, `bjd_code`와 1:1). 행정동이 아니다. 파싱 실패 시 null |
| `name` | string | 예 | lic | 인허가 사업장명 원문. 인허가에 없으면 null |
| `address_road` | string | 예 | lic | 도로명 주소 원문. 화면은 도로명 우선, 없으면 지번 |
| `address_jibun` | string | 예 | lic | 지번 주소 원문 |
| `license_date` | date | 예 | lic | 인허가일. `as_of` 이후면 오류 (검증) |
| `mdis_industry_code` | `"56"`/`"96"` | 예 | build | 고정 매핑 확정 전까지 **null** (§10 미정) |
| `status` | object | 아니오 | lic | 아래 |

`store.status` — 기준일 영업과 현재 상태를 구분한다 (D6).

| 필드 | 타입 | null | 규칙 |
|---|---|---|---|
| `open_at_as_of` | `true` | 아니오 | 리포트는 as_of 당시 영업 점포(인허가일 ≤ as_of, 폐업일 없음 또는 > as_of)에만 만든다. 모집단 수는 하드코딩하지 않고 score 패널(#35)에서 센다 |
| `current` | `open`/`closed`/`unknown` | 아니오 | 인허가 스냅샷 시점 상태. `close_date`가 있으면 `closed`. 폐업일이 없는데 상태명이 폐업인 행(실측 6건)은 `unknown` |
| `close_date` | date | 예 | `current=open`이면 null, `closed`면 필수. as_of 이전이면 오류 |
| `license_snapshot_date` | date | 예 | 인허가 원천 파일 기준일 |

### 3-3. `risk` (serve 그대로)

| 필드 | 타입 | null | 규칙 |
|---|---|---|---|
| `probability_12m` | 0~1 | 아니오 | as_of 이후 12개월 안 폐업 예측 확률 |
| `ci_low`, `ci_high` | 0~1 | 아니오 | **점포 단위 부트스트랩 재학습 예측의 5·95 백분위**. 신뢰구간이 아니다. `ci_low ≤ p ≤ ci_high` (검증) |
| `interval_note` | string | 아니오 | 구간 설명 고정 문구. "신뢰구간·신뢰수준" 표현 금지 (검증) |
| `band` | `low`/`mid`/`high` | 아니오 | 절대 확률 컷오프 (컷오프 값은 실행 메타에 둔다). 표시명은 화면이 정한다 |
| `percentile` | 0~100 정수 | 예 | 같은 score_origin·자치구·업종 안 위험도 백분위, 높을수록 위험 |
| `peer_group` | string | 아니오 | `"{gu} {biz_type}"`와 같아야 한다 (검증). **업력 조건 없음** |
| `peer_median` | 0~1 | 아니오 | peer_group 위험도 중앙값 |
| `model` | string | 아니오 | 예: `detect_v0_enriched` |
| `calibrated` | bool | 아니오 | 보정 적용 여부 (현재 false). 화면 노출 불필요 |

화면 규칙: 구간은 "예측이 흔들릴 수 있는 범위"로만 설명한다. "폐업 확률이 N~M% 사이일 확률이 95%" 같은 해석을 쓰지 않는다
(부트스트랩 B=20, 구간 커버리지를 통계적으로 검증하지 않았다 — DECISIONS 2026-09-25 W2-2).

### 3-4. `factors[]`

| 필드 | 타입 | null | 출처 | 규칙 |
|---|---|---|---|---|
| `factor_id` | enum (§5) | 아니오 | serve | 레코드 안에서 유일 (검증) |
| `name`, `category`, `actionability` | string / enum | 아니오 | serve | `factor_id`별 고정값과 같아야 한다 (§5, 검증) |
| `contribution` | −1~1 | 아니오 | serve | 요인 단위 Shapley 기여 (확률 단위). 예측 분해이지 인과효과가 아니다. 배열은 이 값 내림차순 (검증) |
| `direction` | `위험 증가`/`위험 감소`/`영향 미미` | 아니오 | serve | \|기여\| < 0.001이면 `영향 미미`, 아니면 부호 (검증). **0.1에는 `영향 미미`가 없다 — 변경 요청 R3** |
| `peer_percentile` | 0~100 정수 | 예 | serve | 같은 업종·자치구·업력대 안 이 요인 기여의 백분위. 높을수록 위험 기여가 큼. 업력대 비교는 여기에만 쓴다 |
| `explanation` | string | 아니오 | serve | 계산 결과 서술형 문장. "때문에·원인·고치면·개선하면·줄어듭니다" 금지 (검증) |
| `driver` | string | 예 | serve | 온라인 요인의 주된 근거. 다른 요인은 null (검증) |
| `values` | object | 아니오 (빈 객체 가능) | serve | 판단에 쓴 원래 feature 값 (number/string/bool/null) |
| `display` | bool | 아니오 | serve | 아래 §4 |
| `data_missing` | bool | 아니오 | serve | 아래 §4 |
| `missing_reason` | enum | 예 | serve | 아래 §4. **0.1에 없음 — 변경 요청 R1** |
| `hold_reason` | enum | 예 | serve | 아래 §4. **0.1에 없음 — 변경 요청 R2** |
| `display_note` | string | 예 | serve | 내부용 문장. 화면 분기·문구에 쓰지 않는다 |

## 4. 표시 상태 구분

서로 다른 네 가지를 섞지 않는다. 화면 분기는 **코드 필드**(`display`, `data_missing`, `missing_reason`, `hold_reason`,
`unavailable_categories`)로만 하고 `explanation`·`display_note` 문장을 파싱하지 않는다.

| 상태 | 단위 | 조건 | 뜻 | 권장 화면 |
|---|---|---|---|---|
| 표시 | 점포×요인 | `display=true` (이때 `data_missing=false`, `missing_reason=null`, `hold_reason=null`) | 진단문을 그대로 보여준다 | 기여 순 목록. `영향 미미`는 접기 |
| 데이터 없음 | 점포×요인 | `display=false`, `data_missing=true`, `missing_reason` ≠ null, `hold_reason=null` | 요인 feature가 이 점포에서 전부 결측. 기여는 값이 아니라 결측 자체에서 나오므로 요인 진단으로 읽으면 안 된다 | 회색 "데이터 없음" 배지 + `missing_reason` 문구 |
| 검토 대기 | 점포×요인 | `display=false`, `data_missing=false`, `hold_reason` ≠ null | 계산은 됐지만 표시를 보류 (현재 `online_review` = 언급이 많은 쪽에서 위험이 높게 나온 온라인 요인, 오탐 검수 #28 전) | 숨김 |
| 판단 불가 | 레코드(유형) | `unavailable_categories`에 유형이 있음 | 모형에 그 유형의 활성 요인이 없음 (현재 "비용" — 공시지가가 학습 구간에 없음) | 유형 자리에 "판단 불가". 0%p로 그리지 않는다 |

- 표시 보류된 요인의 `contribution`도 합계에 포함된다 (Σ기여 + 기준값 = 예측 확률).
- `missing_reason` 코드 (serve `diagnose.missing_reasons`의 문구와 1:1):

| 코드 | serve 0.1 문구 | 해당 요인 |
|---|---|---|
| `outside_trdar` | 상권 경계 밖 | 상권 요인 4개 |
| `trdar_quarter_missing` | 해당 분기 상권 자료 없음 | `trdar_population`, `trdar_vitality` |
| `no_biz_data` | 해당 상권에 이 업종 자료 없음 | `peer_competition` |
| `no_sales_disclosed` | 해당 상권에 이 업종 매출 공개 자료 없음 | `peer_sales` |
| `trdar_unknown` | 상권 데이터 없음 (trdar_cd를 알 수 없음) | 상권 요인 4개 |
| `online_unobserved` | 관측 불가 | `online_attention` |
| `unknown` | 데이터 없음 | 그 밖 |

W2-5 빌드는 이 코드를 **serve 출력의 원천 값으로만** 받는다. 0.1 출력에는 이 필드가 없으므로 `explanation`에서 역추출하지 않고,
R1이 반영된 serve 출력이 나오기 전까지 최종 리포트를 만들지 않는다(검증 실패).

## 5. 요인과 정책 연결

`factor_id` 고정표 (진단 W2-3 요인 매핑표의 계약 사본, `report_validation.FACTOR_CONTRACT`):

| factor_id | name | category | actionability |
|---|---|---|---|
| `tenure` | 업력 | 사업체 구조 | external |
| `store_profile` | 업종·점포 규모 | 사업체 구조 | external |
| `district` | 자치구 | 입지·수요 | external |
| `trdar_population` | 상권 유동·배후 인구 | 입지·수요 | external |
| `trdar_vitality` | 상권 변화·영업 지속 | 입지·수요 | external |
| `online_attention` | 온라인 언급(블로그) | 입지·수요 | owner |
| `peer_competition` | 동종 업종 경쟁·개폐업 | 경쟁 | external |
| `peer_sales` | 동종 업종 매출 수준 | 경쟁 | external |
| `rent_level` | 임대료 수준(공시지가) | 비용 | policy (현재 비활성) |

진단 쪽 표가 바뀌면(경쟁지표 추가 등) 이 표·스키마 enum·`FACTOR_CONTRACT`를 같은 PR에서 바꾸고 `_schema_version`을 올린다.

### 정책 (`policies[]`, `$defs/policy_match`)

W2-7 정책 원천(`$defs/policy_source`, FACTOR_POLICY_LINKS.md §3)을 입력으로 W2-5 빌드가 계산한다 (D4).

| 필드 | 타입 | null | 규칙 |
|---|---|---|---|
| `id`, `name`, `operator`, `eligibility_text` | string | 아니오 | 원천 그대로. id는 `^[a-z0-9_]+$`, 레코드 안 유일 |
| `link` | URL | 예 | `http(s)://` |
| `announce_year` | int | 예 | |
| `collected_at` | date | 아니오 | 정책 수집일 |
| `match_status` | `matched`/`check_required` | 아니오 | 데이터로 확인할 수 없는 조건이 하나라도 있으면 `check_required` |
| `matched_by` | (`gu`/`biz_type`/`tenure`)[] | 아니오 | 자격 매칭에 쓴 조건 |
| `unverifiable_conditions` | string[] | 아니오 | `check_required`면 1개 이상, `matched`면 빈 배열 (스키마) |
| `linked_factor_ids` | factor_id[] | 아니오 (빈 배열 가능) | 정책 `related_factor_ids` ∩ 이 점포에서 `display=true`이고 `contribution > 0`인 요인 (검증). 검토 대기·데이터 없음·위험을 낮춘 요인·비활성 요인에는 연결하지 않는다 |
| `check_note` | string | 예 | 확인 필요 안내 문구 |

- 요인 연결은 인과 주장이 아니다. 화면 문구는 "이 요인과 관련된 지원사업"이며 효과를 약속하지 않는다.
- 업력 조건은 `store.license_date`와 `as_of`로 계산한다 (진단 업력대와 같은 경계, FACTOR_POLICY_LINKS.md §3).
  업력(개월) = as_of와 인허가일의 연·월 차이(라벨·master `age_months`와 같은 식), `tenure_months_min`·`max`는 경계 포함.
  인허가일이 없으면 업력을 추정하지 않고 `check_required`로 두며 `unverifiable_conditions`에 "업력 조건 (인허가일 정보 없음)"을 넣는다.
- 자치구·업종 조건이 맞지 않거나 업력 조건 밖이면 그 정책은 점포에 붙지 않는다.
- 자유 문자열 `related_factor`(더미)는 허용하지 않는다.

## 6. 처방 (`prescriptions[]`)

W3 DML 분석 전에는 효과 수치를 만들지 않는다. schema 0.2는 **빈 배열** 또는 `status: "unavailable"` 항목만 허용한다.

| 필드 | 0.2 규칙 |
|---|---|
| `id`, `title`, `actionability` | 필수 |
| `related_factor_ids` | factor_id[] |
| `status` | `"unavailable"`만 |
| `unavailable_reason` | 필수 문구 (예: "W3 효과 분석 전") |
| `evidence_level`, `effect_value`, `effect_summary` | **null만** — 근거 등급 척도는 W3에서 DECISIONS로 확정한 뒤 스키마 버전을 올린다 |
| `source`, `caveat` | nullable 문자열 |

## 7. 온라인 존재감 (`online_presence`)

수집 시점(현재) 스냅샷이며 **표시 전용**이다. 예측·진단 feature가 아니고 과거 시점에 소급하지 않는다 (DECISIONS 2026-09-13).
원천은 PR #21 수집 결과(미병합, store_id 형식 변환 필요). 수집·매칭 결과가 없으면 블록 전체가 `null`.

| 필드 | 타입 | null |
|---|---|---|
| `basis` | `"current_snapshot"` | 아니오 |
| `collected_at` | date | 아니오 |
| `naver_local_registered`, `kakao_registered` | bool | 예 (미수집·오류) |
| `naver_blog_total_12m` | int ≥ 0 | 예 |
| `first_date_truncated` | bool | 예 |
| `note` | string | 아니오 |

## 8. 보조 정의 (같은 스키마 파일의 `$defs`)

### 검색 인덱스 `search_index_entry` / `search_index_file`
- 행: `store_id, name, name_norm, biz_type, gu, dong(법정동), address_road, address_jibun` — 모두 인허가 공개 정보.
  **위험도·확률·등급·기여·정책은 담지 않는다** (추가 키 금지로 강제). 상호·주소·동은 nullable.
- 파일: `_schema_version, run_id, score_origin, as_of, release_ready, release_blockers, n_entries, entries`.
- 검색 흐름: 상호(`name_norm`) → 주소(도로명·지번) → 동 목록 선택(`"{dong} ({gu})"`) → 동 요약. 구현·규칙은 §12.
- 3구 안에서 같은 법정동 이름이 두 구에 걸치는 경우는 0건이다 (2026-09-26 인허가 실측, 3구 70개 동).

### 동 요약 `dong_list_entry` / `dong_summary_row` / `dong_summary_file` (Issue #29)
- 동 목록 행: `gu, dong, label` — 식별자는 `(gu, dong)`, 표시명 `"망원동 (마포구)"`.
- 요약 행: `gu, dong(법정동), biz_type(null = 동 전체), score_origin, as_of, n_stores, suppressed, suppression_reason,
  band_share{low,mid,high}, top_risk_biz_types, note`.
  - 숨긴 행은 `n_stores`·`band_share`·`top_risk_biz_types`가 null이고 `suppression_reason`이
    `small_cell`(점포 수 < 하한) 또는 `complementary`(역산 방지 추가 숨김)다 (스키마).
  - `top_risk_biz_types`는 동 전체 행에만 있다 (업종 행은 null). 정의는 §12.
  - `note`는 "모형 예측을 모은 값이며 개별 가게 진단이 아닙니다"를 항상 포함한다.
- 파일: `run_id, score_origin, as_of, release_ready, release_blockers, min_cell_n, min_cell_n_status(provisional/decided),
  risk_ranking_rule, n_stores_total, n_stores_without_dong, duplicate_dong_names, dongs, rows`.
- 점포 리포트와 **같은 정본(run_id)·같은 band 컷오프**로 집계한다. store_id·점포별 확률은 담지 않는다.

### 빌드 입력 `serve_record_v0_1` / `serve_record_v0_1_1`
- `serve_record_v0_1`: PR #36이 현재 내보내는 0.1 레코드. 입력 검증·정본 구조 확인용이며 최종 리포트가 되지 못한다.
- `serve_record_v0_1_1`: R1~R3(필수)과 R4(선택)를 반영하도록 요청한 형식. 이 입력만 최종 0.2 검증 대상이다.

### 빌드 입력 `online_presence_source`
`online_presence.jsonl` 1줄 = `{"store_id": …, "online_presence": {…§7…}}`. PR #21 수집 결과를 이 형식으로 바꿔 넣는다.
리포트 대상이 아닌 점포의 행은 무시하고 그 수를 `runs`에 남긴다.

## 9. 공개 범위와 정적 배포 검증 게이트

**A. 기술적 계약 통과와 B. 공개 승인은 다른 것이다.** A(`technical_gate`)는 아래 게이트 1~3이 코드로 확인됐다는 뜻이고,
B(`publication_approved`)는 실명·주소·store_id·개별 위험도 결합 데이터를 공개해도 된다는 팀 결정이다.
B는 아직 없으며 코드에 승인 수단도 없다 — 정적 번들의 `publication_approved`는 항상 false다.

- 저장소: 실명·주소·store_id와 위험도가 연결된 실제 결과는 커밋하지 않는다. 가린 샘플(`SAMPLE-NNN`, 상호·주소 가림, 면적·인허가일 뭉갬)만 둔다.
- 실제 데이터의 웹 공개 범위는 별도 결정한다. 정적 JSON 배포 전 게이트(다음 단계 구현):
  1. 정본이 `--purpose release`로 만들어졌고 `release_blockers(run)`이 비어 있음
     (최종 0.2 검증 통과, 인허가 기준일 입력 + 원천 데이터갱신일자와 대조 완료)
  2. 모든 리포트가 `validate_report` 통과 (스키마 + 교차 규칙)
  3. 검색 인덱스에 위험 관련 키 없음, 동 요약의 소표본 칸 숨김·역산 불가, 하한값 `decided`
  4. 공개 범위 결정 전에는 실명 리포트 파일을 배포 디렉터리에 만들지 않는다
- `search_index`·`dong_summary`는 `purpose="release"`일 때 1·3을 강제하고, 개발용 산출물에는 `release_ready=false`와 이유를 넣는다.

## 10. 필드별 계약 비교 (PR #36 출력 ↔ PR #37 화면 더미 ↔ 0.2)

| 필드 | PR #36 (0.1) | PR #37 더미 | 0.2 결정 | 누가 바꾸나 |
|---|---|---|---|---|
| `_schema_version` | "0.1" | 없음 (`_dummy`) | "0.2" | W2-5 빌드 |
| `score_origin` | 없음 (`serve_meta`에만) | 없음 | 필수 | W2-5 빌드 |
| `store.name/address_*/license_date` | `--licenses` 시 있음 | 있음 (`address_jibun`은 인덱스에만) | 모두 필수 키, nullable. 인허가 테이블이 단일 출처 | W2-5 빌드 |
| `store.dong` | 있음, README는 "행정동" | 있음 | **법정동**으로 명시 | PR #36 README 문구 수정 (R5) |
| `store.mdis_industry_code` | 없음 | 있음 | 키는 두되 null (매핑 미정) | 결정 후 W2-5 빌드 |
| `store.status` | 없음 | 없음 | 신설 (기준일 영업 vs 현재) | W2-5 빌드 / 화면 |
| `risk.peer_group` | "{gu} {biz_type}" | "…업력 유사 구간" | serve 기준 | 화면 |
| `risk.calibrated` | false | true | serve 값 그대로, 화면 노출 불필요 | 화면 |
| `risk.interval_note` | 있음 | 없음 | 필수, 신뢰구간 표현 금지 | 화면 |
| `factors` 단위 | 요인 8개 + `factor_id` | 개별 변수, id 없음 | 요인 단위 | 화면 |
| `factors[].peer_percentile` 방향 | 높을수록 위험 기여 큼 | 낮을수록 나쁨처럼 사용 | serve 기준 | 화면 |
| `factors[].direction` | 부호만 (0.0에 "위험 증가" 가능) | — | `영향 미미` 추가 | PR #36 (R3) |
| `factors[].missing_reason` | 내부 계산만, 출력 없음 | 없음 | 코드 enum, 필수 키 | PR #36 (R1) |
| `factors[].hold_reason` | 없음 | 없음 | 코드 enum, 필수 키 | PR #36 (R2) |
| `factors[].display/data_missing` | 있음 | 없음 | §4 | 화면 |
| 비용 유형 | `unavailable_categories` | 기여값 있음 | "판단 불가" | 화면 |
| `prescriptions` | 없음 | 효과·등급 값 있음 | 빈 배열 또는 unavailable만 | 화면 (효과 문구 제거) |
| `online_presence` | 없음 | 있음 | `basis` 추가, nullable 블록 | W2-5 빌드 / 화면 |
| `policies[].related_factor` | 없음 | 이름 문자열 | `linked_factor_ids`(factor_id) | 화면 / W2-7 |
| `policies[].eligibility` | 없음 | 있음 | `eligibility_text` | 화면 |
| `policies[].unverifiable_conditions` | 없음 | `check_note`만 | 신설 | 화면 |

### PR #36 출력 변경 요청

| id | 변경 | 필수 여부 | 이유 |
|---|---|---|---|
| R1 | `factors[].missing_reason`을 §4 코드로 출력 (`diagnose.missing_reasons`의 문구 → 코드). `data_missing=false`면 null | **필수** | 원천 값 없이 설명문을 파싱하지 않는다 |
| R2 | `factors[].hold_reason` 출력 (`online_review`, 표시 중이거나 데이터 없음이면 null) | **필수** | "검토 대기"를 `data_missing=false`로부터 추측하지 않는다. 보류 규칙이 늘어나도 화면 분기가 유지된다 |
| R3 | `direction`: \|기여\| < 0.001이면 `영향 미미` | **필수** | 반올림된 0.0에 "위험 증가"가 붙는다 (가린 샘플 SAMPLE-019 `district`) |
| R4 | 각 줄에 `score_origin` 출력 | 권장 | 지금은 `serve_meta.json`에서 가져온다. 줄 단위로 있으면 입력 섞임을 막는다 |
| R5 | `README_W2-6.md`의 `dong` 설명 "행정동" → "법정동" | **필수** (문서) | 인허가 `dong`은 지번주소 기준 법정동이다 (D1) |

R1~R3 반영 후 serve 출력의 `_schema_version`은 0.1에서 올린다(예: 0.1.1). W2-5 빌드는 그 버전만 최종 리포트 입력으로 받는다.

### PR #37 화면 변경 사항

1. 요인 단위(8개, `factor_id`)로 표시하고 개별 변수 목록은 `values` "근거 데이터 보기"로 옮긴다.
2. `peer_percentile`은 높을수록 위험 기여가 크다. `risk.percentile`의 "상위 N%" = 100 − percentile.
3. `peer_group`에서 업력 조건을 뺀다 ("광진구 미용업").
4. 표시 상태 4종(§4)을 코드 필드로 분기한다. `영향 미미`는 접는다.
5. 비용 유형은 "판단 불가"로 표시하고 0%p로 그리지 않는다.
6. 불확실성 구간은 `interval_note` 문구만 쓴다 ("신뢰구간" 표기 금지).
7. 처방 카드에서 효과 수치·근거 등급을 뺀다 (W3 전 unavailable 상태).
8. 정책: `related_factor`(이름) → `linked_factor_ids`, `eligibility` → `eligibility_text`, `unverifiable_conditions` 추가.
9. `store.status.current`가 `closed`/`unknown`이면 "기준일 이후 폐업 신고됨/상태 확인 필요" 안내를 붙인다.
10. 동 표시는 법정동 `"{dong} ({gu})"`. 동 요약 화면은 "개별 가게 진단 아님" 문구(`note`)를 항상 보여준다.
11. 공개용 더미는 PR #37의 교체된 익명 더미만 쓰고, 교체 전 초기 더미(실제 점포와 겹친 값)는 쓰지 않는다.

## 11. SQLite 정본 빌드 (`src/serving/build_db.py`)

### 실행

```bash
python -m src.serving.build_db --serve-dir outputs/serve/<run> --license-snapshot-date YYYY-MM-DD
```

| 옵션 | 기본값 | 내용 |
|---|---|---|
| `--serve-dir` | — | PR #36 serve 출력 폴더 (`reports.jsonl`, `serve_meta.json`). 또는 `--reports`·`--serve-meta`로 따로 준다 |
| `--licenses` | `outputs/standardized/licenses_3gu.parquet` | 인허가 표준화 테이블 (상호·주소·법정동·인허가일·폐업일·상태의 단일 출처) |
| `--policies` | 없음 | W2-7 정책 원천 JSON (`policy_source` 배열 또는 `{"policies": [...]}`). 없으면 매칭하지 않고 `policy_matching = not_performed` |
| `--online-presence` | 없음 | `online_presence_source` JSONL. 없으면 모든 점포의 `online_presence = null` |
| `--license-snapshot-date` | 없음 | 인허가 원천 파일 기준일. 주지 않으면 `store.status.license_snapshot_date = null` |
| `--purpose` | `dev` | `release`면 기준일 필수, 기준일이 원천 데이터갱신일자 최댓값 이후인지 대조 가능해야 하며, 최종 0.2 검증 통과가 필수 |
| `--allow-downgrade` | 꺼짐 | 기존 정본이 `final_contract=passed`면 통과하지 못한 결과로 덮어쓰지 않는다. 의도한 경우에만 켠다 |
| `--out` | `outputs/serving/report.sqlite` | 정본 경로. 저장소 안이면 git 무시 대상이어야 하며 `docs/` 아래는 금지 |

인허가 기준일 provenance (`runs`): `license_snapshot_date_basis`(`not_provided`/`user_supplied` — 입력했다는 사실)와
`license_snapshot_check`(`not_checked`/`consistent` — 입력일 ≥ 원천 `데이터갱신일자` 최댓값 `license_max_updated_date`)를 따로 둔다.
원천 파일의 실제 수령일은 parquet에 없어 검증할 수 없으므로 `consistent`는 "원천과 모순되지 않음"까지만 뜻한다.
입력일이 데이터갱신일자 최댓값보다 이르면 빌드를 멈춘다.

### 검증 순서 (하나라도 실패하면 정본을 만들지 않는다)

1. serve 입력: 모든 줄이 같은 `_schema_version`이고 `serve_record_v0_1`/`_v0_1_1`을 통과, store_id 중복 없음,
   줄 수 = `serve_meta.n_stores`(누락 검출), 모든 `as_of` = `serve_meta.as_of` = `score_origin` 분기 말일, 줄에 `score_origin`이 있으면 일치.
2. 인허가 결합: 인허가 테이블 store_id 유일, reports의 모든 점포가 인허가에 있음(1:1), serve가 붙인 store 필드가 있으면 인허가 값과 일치,
   인허가일 ≤ as_of, 폐업일이 있으면 > as_of (as_of 당시 영업 점포).
3. 정책 원천·온라인 입력: 각 스키마 통과, id·store_id 중복 없음, 업력 min ≤ max.
4. 최종 0.2: 정본에서 다시 조립한 레코드(`assemble_report`)를 `validate_report`로 검사.
   - 입력 0.1.1 → 한 건이라도 실패하면 빌드 실패, 통과하면 `runs.final_contract = passed`.
   - 입력 0.1 → R1~R3 값이 없으므로(NULL, 추측하지 않음) `runs.final_contract = not_ready`, 실패 건수만 기록. **정적 배포 대상이 아니다.**

빌드는 같은 폴더의 임시 파일에 한 트랜잭션으로 쓰고 모든 검증을 통과한 뒤에만 정본 경로로 교체한다. 실패하면 임시 파일을 지우고 기존 정본은 그대로 둔다.
같은 입력이면 `run_id`(입력 해시 기반)와 DB 내용(`iterdump`)이 같다 — 빌드 시각 같은 비결정 값은 저장하지 않는다.

### 테이블

| 테이블 | 키 | 내용 |
|---|---|---|
| `runs` | `run_id` | 1행. builder·스키마 버전, 입력 schema 버전, score_origin, as_of, 점포 수, 입력 파일 sha256(reports·serve_meta·licenses·policies·online), serve_meta 전문, 모델명, detect 실행, band 컷오프, serve_meta의 licenses sha256, 인허가 기준일, 정책 매칭 여부, 온라인 행 수·무시 수, `final_contract`(`passed`/`not_ready`)·사유·실패 건수 |
| `stores` | `store_id` | 인허가 기준 점포 정보 + 현재 상태 + `unavailable_categories`·`disclaimer` |
| `risk` | `store_id` | serve risk 블록 |
| `factors` | `(store_id, factor_id)`, `(store_id, rank)` 유일 | serve factors (rank = 입력 순서 = 기여 큰 순), `values_json`, `missing_reason`·`hold_reason`(0.1 입력이면 NULL) |
| `prescriptions` | `(store_id, prescription_id)` | 현재 비어 있음. `status = 'unavailable'`, 효과·등급 NULL을 CHECK로 강제 |
| `policies` | `policy_id` | 정책 원천 그대로 |
| `store_policies` | `(store_id, policy_id)` | 매칭 결과 (`match_status`, `matched_by`, `unverifiable_conditions`, `linked_factor_ids`, `check_note`, `sort_order`) |
| `online_presence` | `store_id` | 현재 스냅샷 (리포트 대상 점포만) |

인덱스: `stores(gu, dong, biz_type)`, `stores(name_norm)`, `risk(band)`, `factors(factor_id)`, `store_policies(policy_id)`.

## 12. 검색 인덱스와 동 요약 (`src/serving/search_index.py`, `src/serving/dong_summary.py`)

### 실행

```bash
python -m src.serving.search_index --db outputs/serving/report.sqlite --out outputs/serving/dev/search_index.json
python -m src.serving.dong_summary build --db outputs/serving/report.sqlite --min-cell-n <K> --out outputs/serving/dev/dong_summary.json
python -m src.serving.dong_summary profile --as-of 2026-06-30
```

둘 다 정본(SQLite)만 읽는다. JSON 출력은 git 무시 경로만 허용한다(`guard_output_path`). 정적 배포(`export_static`)는 다음 단계다.

### 검색 규칙
- 인덱스 = `stores` 전체(= as_of 당시 영업 점포, 리포트가 있는 점포). store_id 유일, 행 수 = `runs.n_stores`,
  store_id 집합 = `stores` = `risk`(상세 리포트와 같은 id)를 검증한다. `name_norm`이 `normalize_name(name)`과 다르면 멈춘다.
- 상호: 검색어를 인허가 표준화와 같은 `src.data.names.normalize_name`으로 정규화한다(지점명은 빠진다).
  일치 단계 0 지점명까지 같음 → 1 정규화 상호 같음 → 2 앞부분 → 3 포함. 같은 상호의 점포는 **모두** 돌려준다.
- 주소: NFKC·대문자, 쉼표·괄호는 경계로 남기고 공백·문장부호 제거('-' 유지), 앞의 '서울특별시/서울시/서울' 제거.
  도로명·지번 둘 다 본다. 일치 뒤에 숫자가 이어지는 경우(`가상로 1` → `가상로 12`)는 뒤로 보낸다.
- 정규화 후 2자 미만 검색어는 결과 없음. 상호·주소가 없는 점포는 해당 검색에서 빠질 뿐 인덱스에는 남는다.
- 정렬: (일치 단계, 구, 법정동(null 마지막), 정규화 상호(null 마지막), store_id) — 항상 같은 순서.

### 동 요약 규칙
- 집계 대상 = 정본 `stores` ⋈ `risk` (한 run_id·score_origin). 법정동이 없는 점포는 제외하고 수를 남긴다(추정 배정 안 함).
- 행정동 표기(`…숫자+동`, 예 '망원1동')가 있으면 멈춘다. 동명이동은 `duplicate_dong_names`에 남기고 `(gu, dong)`으로 구분한다.
- 검증: 업종 칸의 등급별 점포 수 합 = 칸 점포 수, 업종 칸 합 = 동 전체, 동 합계 + 동 없음 = `runs.n_stores`,
  동 전체 등급 합 = `risk` 등급 분포.
- **위험 업종 순위(`top_risk_biz_types`)**: 동 전체 행에서, 숨기지 않은 업종 칸 중 high 등급 점포가 1곳 이상인 업종을
  high 비율(high 점포 수 / 점포 수) 내림차순 → mid+high 비율 → 점포 수 → 업종 순으로 나열한다.
  **평균 예측 확률이 아니라 등급 비율**이다(전부 mid인 칸은 평균 확률이 높아도 순위에 없다). 숨긴 업종은 순위에 넣지 않는다.
  band는 절대 확률 컷오프로 정한 모형 예측 등급이며 실제 폐업률이 아니다.
- 소표본 숨김: 점포 수 < `min_cell_n`인 칸과 동 전체를 숨긴다(`small_cell`). 동 전체가 공개되는데 숨긴 업종 칸이 1개뿐이면
  가장 작은 공개 칸을 추가로 숨긴다(`complementary`). 공개 행만으로 역산되는 칸이 남으면 멈춘다(`recoverable_cells`).
  **`min_cell_n`은 미정**이다. 코드에 기본값이 없고, `provisional`인 동안 공개 배포용으로 만들 수 없다.

### 소표본 하한값 실측 (2026-09-26, 위험도 없이 점포 수만)
as_of 당시 영업 점포(인허가일 ≤ as_of < 폐업일 또는 폐업일 없음)를 인허가에서 센 근사 모집단. 2025-06-30 근사치는 master_base
2025Q2 패널(29,101점포, 법정동 결측 42)과 동×업종 칸 분포가 정확히 같았다. 2026Q2 실제 score 패널은 #35 이후 다시 잰다.

| as_of | 칸 | 동 | 점포 | 최소 | 5백분위 | 10백분위 | 중앙값 | 최대 |
|---|---|---|---|---|---|---|---|---|
| 2026-06-30 | 194 | 67 | 28,681 (+법정동 결측 30) | 1 | 3 | 6 | 55.5 | 1,783 |
| 2025-06-30 | 192 | 67 | 29,059 (+법정동 결측 42) | 1 | 3 | 6 | 58 | 1,847 |

2026-06-30 후보 하한값별 영향 (업종 칸 194개 기준):

| 하한 | small 칸 | 추가 숨김 칸 | 숨긴 동 전체 | 영향 동 | 숨긴 칸의 점포 |
|---|---|---|---|---|---|
| 3 | 8 | 4 | 2 | 7 | 65 |
| 5 | 16 | 5 | 2 | 11 | 111 |
| 10 | 31 | 5 | 5 | 18 | 242 |
| 20 | 47 | 7 | 7 | 26 | 728 |
| 30 | 60 | 9 | 8 | 33 | 1,162 |

역산 검토: 숨긴 업종 칸이 동마다 1개뿐이면 동 전체 − 공개 칸으로 정확히 복원된다(점포 수와 등급별 수 모두) —
추가 숨김으로 막았다. 남은 위험: 공개된 작은 칸에서 등급이 한쪽으로 몰리면(예: 5곳 모두 high) 그 칸의 모든 점포 등급이 드러난다.
하한값 선택과 함께 판단할 사항으로 남긴다.

## 13. 정적 JSON 번들 (`src/serving/export_static.py`)

### 실행

```bash
python -m src.serving.export_static --db outputs/serving/report.sqlite --min-cell-n <K>
```

- 입력은 SQLite 정본뿐이다. 검색 인덱스·동 요약은 `search_index`·`dong_summary` 모듈을 그대로 쓴다.
- 기본 출력은 로컬 비공개 `outputs/serving/static_private/`. git 작업 트리(이 저장소, 별도 프론트 저장소 등) 안이면
  저장소 기준 경로에 `docs`·`app`·`public`·`dist`·`site`·`www`가 있으면 거부하고, git 무시 경로만 허용한다(이 저장소는 `outputs/` 아래만).
  예외는 모든 점포가 합성 샘플(`SAMPLE-NNN`, `(샘플)` 상호)인 번들을 `docs/samples/` 아래에 쓸 때뿐이다.
- 실명 점포 번들의 공개 웹 배포는 구현하지 않았다 (B 미결정).

### 파일 구조

```
<bundle>/
  meta.json            static_meta — 프론트가 처음 읽는 작은 파일 (run_id·score_origin·as_of·data_kind·band 컷오프·
                       policy_matching·report_path_template·files·technical_gate·dong_summary_public_ready·publication_approved)
  search_index.json    search_index_file — 검색용 (위험 정보 없음)
  dongs.json           dong_list_file — 동 선택 목록 "{동} ({구})"
  dong_summary.json    dong_summary_file — 동×업종 등급 분포 (숨김 적용)
  reports/{store_id}.json   점포별 최종 0.2 리포트 1건씩 (파일명은 store_id만, 상호·주소 없음)
  manifest.json        static_manifest — 파일별 path·sha256·bytes·rows·schema_version·run_id (자기 자신 제외)
```

검색 인덱스와 상세 리포트를 분리해 브라우저는 인덱스 하나와 고른 점포의 리포트 1개만 내려받는다.

### 내보내기 조건과 검증
- **A 미통과면 번들을 만들지 않는다**: 정본 `release_blockers`가 비어 있어야 한다 — `final_contract=passed`,
  `build_purpose=release`, 인허가 기준일 입력·원천 대조(`license_snapshot_check=consistent`). `not_ready`·개발용 정본은 거부.
- 동 요약 공개 가능(`dong_summary_public_ready`)은 따로 판단한다. 다음 중 하나라도 있으면 false다:
  하한값 `provisional`, 역산 가능한 숨김 칸(`recoverable_cells`), 한 등급이 100%인 공개 칸(속성 노출, `homogeneous_cells`).
- 임시 디렉터리에 쓴 뒤 디스크에서 되읽어 `verify_bundle`로 검증하고 통과해야 교체한다:
  meta·manifest·인덱스·동 목록·동 요약의 스키마, 모든 파일의 run_id·score_origin·as_of = 정본, manifest 목록 = 디스크 파일·sha256 일치,
  리포트 파일명 = store_id·0.2 검증, 리포트 store_id 집합 = 검색 인덱스 = 정본 점포 수(누락·중복 없음), 동 요약 역산 불가.
  실패하면 임시 디렉터리를 지우고 기존 번들은 그대로 둔다. 번들이 아닌 기존 폴더는 덮어쓰지 않는다.
- 같은 정본·같은 인자면 모든 파일과 manifest가 바이트 단위로 같다 (시각 등 비결정 값 없음).

### 크기 (2026-09-26 측정, 합성 28,832점포 — 요인 8개·정책 3건·온라인 스냅샷 포함)
| 파일 | 크기 | gzip | 비고 |
|---|---|---|---|
| `meta.json` | 0.8 KB | 0.6 KB | 첫 요청 |
| `search_index.json` | 8.7 MB | 0.49 MB | 검색 화면 진입 시 1회 (정적 호스팅의 gzip/brotli 전송 전제) |
| `dongs.json` / `dong_summary.json` | 1.1 KB / 25 KB | 0.3 KB / 2.2 KB | 합성 동 13개 기준 — 실제 67개 동이면 수배 |
| `reports/{store_id}.json` | 평균 6.5 KB (최대 6.8 KB) | 약 1.8 KB | 점포 선택 시 1개 |
| `manifest.json` | 6.1 MB | 1.3 MB | 무결성 기록용 — 브라우저가 받을 필요 없음 |
| 리포트 합계 | 186 MB, 28,832개 파일 | | 호스팅 파일 수 제한 확인 필요 |
| (참고) SQLite 정본 | 136 MB | | 로컬만 |

같은 규모에서 정본 빌드 약 3분, 내보내기(쓰기 + 전 리포트 0.2 재검증 + 해시 대조)는 약 10분(616초, 기존 번들 교체 포함 — 3,000점포는 47초). 시간 대부분은 리포트 JSON Schema 검증(건당 약 5 ms)이다.

### 프론트 연동 예시

```js
const base = "/data";                                   // 정적 호스팅 경로 (공개 범위 결정 전에는 로컬 개발 서버만)
const meta = await (await fetch(`${base}/meta.json`)).json();
if (meta.data_kind === "real" && !meta.publication_approved) {
  // 실제 점포 데이터는 공개 승인 전 — 로컬 개발 화면에서만 사용
}
const index = await (await fetch(`${base}/${meta.files.search_index}`)).json();
// 1) 상호 → 2) 주소 → 3) 동 선택 (search_index.py 규칙: 정규화 상호 일치 단계, 여러 건이면 모두 목록으로)
const hits = index.entries.filter(e => e.name_norm && e.name_norm.includes(normalizeName(query)));
const url = `${base}/${meta.report_path_template.replace("{store_id}", encodeURIComponent(hits[0].store_id))}`;
const report = await (await fetch(url)).json();        // 최종 0.2 리포트
// 결과 없음 → dongs.json에서 동 선택 → dong_summary.json의 (gu, dong) 행 (note 문구 항상 표시, suppressed 행은 수치 없이)
```

### 합성 샘플 10건

```bash
python -m src.serving.synthetic_samples
```

완전 합성 입력(`SAMPLE-001`~`010`, `(샘플)` 상호, `가상로` 주소, `(예시)` 정책)으로 `build_db --purpose release` → `export_static`을 실행해
`docs/samples/w2-5/`를 다시 만든다(같은 파일이 나온다 — 테스트로 확인). `policy_matching`이 실행(run) 단위 값이라
`bundle/`(SAMPLE-001~009, 정책 매칭 performed)과 `bundle_no_policy/`(SAMPLE-010, not_performed) 두 번들로 나눈다.
프론트 진입 파일은 각 번들의 `meta.json`이다. 구성·경우는 `docs/samples/w2-5/README.md`·`sample_cases.json`.

## 14. 미정 사항

- `mdis_industry_code` 고정 매핑(일반·휴게음식점 → 56, 미용업 → 96)을 쓸지 — 처방(W3)이 필요할 때 결정.
- 동 요약 숨김 하한값 `min_cell_n` — 위 실측을 근거로 팀 결정, #35 score 패널로 재확인 후 DECISIONS 기록.
- 공개된 작은 칸의 등급 쏠림(속성 노출) 처리 방식 — 현재는 한 등급 100% 칸이 있으면 동 요약을 공개 불가로 표시만 한다.
- 실명 점포 데이터의 공개 범위(B)와 승인 절차.
- 약 2.9만 개 리포트 파일을 그대로 올릴지, 묶음 파일로 나눌지 — 정적 호스팅 서비스의 파일 수 제한 확인 후.
- band 표시명(낮음/주의/높음)과 컷오프 문구 — 화면 결정.
- `ci_low`/`ci_high` 필드명 변경(`interval_low/high`) 여부 — 이름이 신뢰구간으로 읽힐 수 있으나 PR #36 호환을 위해 0.2에서는 유지.
- 실제 데이터 웹 공개 범위 (§9).
