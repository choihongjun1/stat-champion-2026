# W2-5 합성 샘플 번들 (최종 0.2 계약)

**모든 값은 지어낸 값이다.** 점포(`SAMPLE-001`~`010`, 상호 `(샘플) …`, 주소 `가상로`), 위험도·등급, 요인 기여, 온라인 존재감,
정책(`(예시) …`)은 실제 점포·실제 추정 결과·실제 통계가 아니다. 실제 결과에서 가져와 가린 것이 아니라 처음부터 합성했다.
화면 개발·계약 확인용이며 수치를 보고서·시연에 쓰지 않는다.

- 생성: `python -m src.serving.synthetic_samples` (합성 입력 → `build_db --purpose release` → `export_static`). 다시 실행하면 같은 파일이 나온다.
- 리포트 스키마는 `_dummy` 키를 허용하지 않는다. 합성 표시는 각 번들 `meta.json`·`manifest.json`의
  `data_kind: "synthetic_sample"`·`publication_note`, `sample_cases.json`, 설명문 끝의 `[합성 예시]`에 있다.
- 처방(`prescriptions`)은 W3 인과분석 전이라 빈 배열이다. 근거 등급·효과 수치는 없다.
- 동 요약은 시연용 provisional 하한 2로 만들었다 (실제 하한값 아님). 점포가 적어 대부분 숨김으로 나온다.

| 폴더 | 점포 | 정책 매칭 |
|---|---|---|
| `bundle/` | SAMPLE-001~009 | performed (합성 정책 원천 3건) |
| `bundle_no_policy/` | SAMPLE-010 | not_performed (정책 원천 없음 — `policies=[]`는 "해당 정책 없음"이 아니다) |

정책 매칭 여부는 실행(run) 단위 값이라 한 번들에 섞을 수 없어 둘로 나눴다.

**프론트 진입 파일은 각 번들의 `meta.json`이다.** 여기서 `files`(검색 인덱스·동 목록·동 요약 경로)와
`report_path_template`(`reports/{store_id}.json`)을 읽는다. 기본 화면 개발은 `bundle/`로 하고,
정책 매칭이 실행되지 않은 경우의 화면("정책 정보 준비 중")만 `bundle_no_policy/`로 확인한다.
각 번들의 `manifest.json`은 무결성 기록이라 프론트가 읽을 필요가 없다.

## 샘플 구성

| store_id | 상호 | 등급 | 보여주는 경우 |
|---|---|---|---|
| SAMPLE-001 | (샘플) 가상식당 망원점 | low | 상호 중복 검색 ("가상식당" → 001·002) |
| SAMPLE-002 | (샘플) 가상식당 합정점 | mid | 상호 중복 검색 |
| SAMPLE-003 | (샘플) 가상카페 | high | 주소 구분 검색 ("광진구 가상로 1") · 온라인 요인 연결 정책 |
| SAMPLE-004 | (샘플) 가상미용실 | low | 주소 구분 검색 ("광진구 가상로 12") · 온라인 존재감 없음 (미등록·언급 0) |
| SAMPLE-005 | (샘플) 상권밖식당 | mid | 데이터 없음 요인 4개 (`hold_reason=data_missing`, `out_of_trdar`) · 온라인 존재감 미수집 (`null`) · 도로명 주소 없음 |
| SAMPLE-006 | (샘플) 검토대기카페 | high | 검토 대기 요인 (`online_review`) — 정책 연결 안 함 |
| SAMPLE-007 | (샘플) 기준일후폐업식당 | mid | 기준일 이후 폐업 (`status.current = closed`) |
| SAMPLE-008 | (샘플) 인허가일미상미용실 | low | 정책 조건 확인 필요 (인허가일 없음 → 업력 조건 `check_required`) |
| SAMPLE-009 | (샘플) 매출미공개카페 | high | 데이터 없음 (`sales_unpublished`, `online_unobservable`) |
| SAMPLE-010 | (샘플) 정책미실행식당 | low | 정책 매칭 미실행 |

모든 샘플: 비용 유형 판단 불가(`unavailable_categories: ["비용"]`), 정책 `(예시) 임차료 부담 완화`는 매출 조건 때문에 `check_required`.

## 검색 → 상세 조회

```js
const meta = await (await fetch(`${base}/meta.json`)).json();
const index = await (await fetch(`${base}/${meta.files.search_index}`)).json();
// 상호·주소 검색은 src/serving/search_index.py의 규칙을 따른다 (결과 여러 건이면 목록으로 보여준다)
const hit = index.entries.find(e => e.name_norm === "가상식당");
const report = await (await fetch(`${base}/${meta.report_path_template.replace("{store_id}", hit.store_id)}`)).json();
```
