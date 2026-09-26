# 진단 요인 ↔ 정책·처방 연결 규칙 (초안 v0.1)

- 작성: 박안석 (W2-3) · 2026-09-26 · 상태: **초안 — W2-7(손유성)·W2-5(최홍준) 확인 필요**
- 목적: W2-7 `policies.json`과 W2-5 결과 스키마가 **같은 연결 키(`factor_id`)**를 쓰도록 진단 쪽 기준을 정한다.
- 근거: W2-3 요인 매핑표 (`src/models/diagnose.py` `FACTORS`, DECISIONS 2026-09-25 W2-3 항목 — 둘 다 PR #34),
  화면 더미와의 형식 차이 (`docs/samples/w2-6_dummy/SCHEMA_DIFF.md` §4, PR #37)

## 1. 원칙

1. **정책은 두 가지 경로로만 붙는다.**
   - **자격 매칭**: 자치구·업종·업력 같은 **자격 조건**이 맞는 정책. 진단 결과와 무관하게 붙는다.
   - **요인 연결**: 그 점포에서 **위험을 올린 요인**과 관련된 정책. 자격 매칭된 정책 중 `related_factor_ids`가
     그 요인과 겹치는 것만 요인 옆에 함께 보여준다.
2. **요인 연결은 인과 주장이 아니다.** 요인 기여도는 예측모형의 변수 기여도다(DECISIONS 2026-09-10).
   화면 문구는 "이 요인과 관련된 지원사업"이며, "이 사업을 이용하면 위험이 줄어듭니다"라고 쓰지 않는다.
3. **표시하지 않는 요인에는 연결하지 않는다.** `display=false`(온라인 검토 대기·데이터 없음)인 요인,
   기여가 0 이하인 요인, 비활성 요인(`rent_level`)에는 정책을 연결하지 않는다.
4. **확인할 수 없는 조건은 자동 매칭하지 않는다.** 매출액·종업원 수처럼 데이터에 없는 조건은
   `match_status: "check_required"`로 두고 조건을 문구로 보여준다 (더미의 기존 방식 유지).

## 2. 요인별 연결 기준

| factor_id | 이름 | 유형 | 조치 가능성 | 위험이 올라간 경우의 뜻 (관측 서술) | 연결 가능한 정책 유형 (예시) |
|---|---|---|---|---|---|
| `tenure` | 업력 | 사업체 구조 | external | 폐업이 상대적으로 많이 관측되는 업력 구간 | 초기 창업 안정화·경영 컨설팅 |
| `store_profile` | 업종·점포 규모 | 사업체 구조 | external | 업종·면적·좌표 정보 조합이 위험 쪽 | 경영 컨설팅, 시설 개선 |
| `district` | 자치구 | 입지·수요 | external | 자치구 전반의 폐업 수준이 높은 편 | 해당 구 소상공인 지원사업 |
| `trdar_population` | 상권 유동·배후 인구 | 입지·수요 | external | 상권 유동·배후 인구 지표가 위험 쪽 | 골목상권·상권 활성화 사업 |
| `trdar_vitality` | 상권 변화·영업 지속 | 입지·수요 | external | 상권 변화 지표·영업 지속 기간이 위험 쪽 | 골목상권·상권 활성화 사업 |
| `online_attention` | 온라인 언급(블로그) | 입지·수요 | **owner** | 블로그 언급이 줄었거나 없음 (근거 `driver` 참조) | 온라인 판로·홍보 지원 |
| `peer_competition` | 동종 업종 경쟁·개폐업 | 경쟁 | external | 같은 상권 같은 업종의 점포 수·개폐업이 위험 쪽 | 업종 전환·재창업 지원, 경영 컨설팅 |
| `peer_sales` | 동종 업종 매출 수준 | 경쟁 | external | 같은 상권 같은 업종 매출 지표가 위험 쪽 | 경영 컨설팅, 판로 지원 |
| `rent_level` | 임대료 수준(공시지가) | 비용 | policy | **비활성** — 학습 구간에 값이 없어 모형 미사용 | 연결 안 함 (임차료 지원은 **자격 매칭으로만**) |

- `online_attention`은 **근거(`driver`)가 "언급 감소·끊김·없음"일 때만** 연결한다. "언급이 많음"이 근거인 경우는
  표시 보류(#28) 대상이라 연결하지 않는다.
- 요인별 정책 수는 화면에서 **요인당 최대 2개**를 권장한다 (나머지는 "전체 지원사업" 목록으로).

### 더미(PR #37)의 `related_factor` 이름 → `factor_id`

| 더미 값 | factor_id | 비고 |
|---|---|---|
| 업력 구간 | `tenure` | |
| 영업장 면적 | `store_profile` | |
| 상권 유동인구 | `trdar_population` | |
| 온라인 노출 채널 수 | `online_attention` | 모형 요인은 블로그 언급만. 지도 플랫폼 등록 여부는 `online_presence`(현재 스냅샷, 표시 전용)이며 요인이 아님 |
| 필지 공시지가 수준 | (없음) | `rent_level` 비활성 → 자격 매칭으로만 표시 |

## 3. `policies.json` 형식 제안 (W2-7)

정책 1건:

```json
{
  "id": "sbiz_online_2026",
  "name": "소상공인 온라인 판로지원 사업",
  "operator": "소상공인시장진흥공단",
  "link": "https://…",
  "announce_year": 2026,
  "collected_at": "2026-09-20",
  "eligibility_text": "업력 1년 이상 소상공인",
  "conditions": {
    "gu": null,
    "biz_type": ["일반음식점", "휴게음식점", "미용업"],
    "tenure_months_min": 12,
    "tenure_months_max": null
  },
  "unverifiable_conditions": [],
  "related_factor_ids": ["online_attention"]
}
```

- `conditions`의 `null`은 "조건 없음". 목록은 "이 중 하나".
- `unverifiable_conditions`: 데이터로 확인할 수 없는 조건 (예: `["연매출 3억 원 이하"]`). 하나라도 있으면
  `match_status`는 `check_required`.
- `related_factor_ids`: §2 표의 id만 허용. 빈 목록이면 자격 매칭으로만 표시.
- 업력은 서빙 결과의 인허가일(`store.license_date`, PR #36)과 기준 시점(`as_of`)으로 계산한다
  (진단의 업력대 구간과 같은 기준: 1년 미만 / 1–3년 / 3–5년 / 5–10년 / 10년 이상.
  경계는 개월 수로 12·36·60·120개월 **이하**가 아래 구간 — `diagnose.py` `AGE_BANDS`, PR #34).

## 4. 점포 결과에 붙는 모양 (W2-5)

```json
"policies": [
  {
    "id": "sbiz_online_2026",
    "name": "…", "operator": "…", "link": "…", "eligibility_text": "…",
    "match_status": "matched",
    "matched_by": ["biz_type", "tenure"],
    "linked_factor_ids": ["online_attention"],
    "check_note": null
  }
]
```

- `linked_factor_ids` = 정책의 `related_factor_ids` ∩ 그 점포에서 **표시되고(display=true) 기여가 양수인 요인**
  (`online_attention`은 §2의 근거 조건 추가). 비어 있으면 "전체 지원사업" 목록에만 나온다.
- 정렬: 요인 연결 있음 → 연결된 요인의 기여 큰 순 → `matched` → `check_required`.

## 5. 확인 요청

- **@손유성 (W2-7)**: §3 형식으로 `policies.json`을 만들 수 있는지, 조건 항목이 더 필요한지
  (예: 청년·여성 대표 여부처럼 데이터에 없는 조건은 `unverifiable_conditions`로).
- **@최홍준 (W2-5)**: §4 모양을 결과 스키마의 `policies[]`로 받을 수 있는지. 매칭 계산(§3 → §4)을
  W2-5 빌드 단계에서 할지, W2-7 쪽 스크립트가 점포별 결과를 넘길지.
- 처방(`prescriptions`)은 근거 A등급(DML)이 W3 작업이라 이 문서 범위 밖. 같은 `related_factor_ids` 규칙을 쓰면 된다.
