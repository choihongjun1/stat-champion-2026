# W3-15 시연 사례 선택 — T5 rule 0.3

2026-10-03 최신 #49/#64 검토 의견과 사용자 요청을 반영한다. 실제 A/B/C 선택은 아직 실행하지 않았다.
Claude가 H1 final run DB에 최종 28건을 `build_db --policies`로 결합하고 matching QA를 끝낸 뒤 실행한다.
production 모델, train/diagnose/serve/background는 다시 실행하지 않는다.

## 동결 정책과 실행 게이트

최종 원천 파일은 저장소 밖에 둔다. 팀 Drive 원천을 커밋하지 않는다.

| 입력 | sha256 |
|---|---|
| policies.json | 0cfb716fbe9272efc816d34d2213354d3ed6cdccbc9851080eee7f40c3eefc2a |
| policies_apply.csv (#65) | 90a2b2134f54d4860481c8f84944ee615177250373abf9cd4bc119590eef5be3 |
| demo_review_policies_20261003.json | 75d05611f9dc16ea267a5a251303a4d0dd6733f4022aea81aec0565d27529ac6 |

확인일은 2026-10-03, 정책 수는 28이다. CLI는 위 policy/review 해시와 확인일을 검사하고,
DB의 정책 해시·건수·원천 conditions·매칭 증거를 대조한다. 이전 27건을 허용하지 않는다.
해시 검사는 DB 접근 전에 한다. SQLite는 finalized read-only snapshot이어야 한다.
`read_frozen_inputs` 함수는 합성 테스트용으로 다른 digest도 받지만 실제 CLI는 최종 freeze만 받는다.
정책 조건 계약은 기존 report 0.3을 그대로 사용한다.

## 전체 후보 모집단과 검수

모집단은 **2026Q2 canonical serving 전체 대상 − 공개 부적합 점포**다.
`set(review.stores) == set(canonical stores)`를 강제한다. 부분 입력·추가 점포·중복·필수 flag 누락을 거부한다.
canonical record 수와 run.n_stores도 같아야 한다. 목록 일부만 넣어 후보를 축소할 수 없다.

현재 `demo-review-0.1`의 전 점포 `publication_guard_passed`/`claims_passed` 계약을 유지한다.
이 목록은 위험 등급으로 사람이 골라 채우는 목록이 아니며, 전 점포에 같은 submission projection,
식별 누수 0, claims 0 규칙을 적용한 검수 증거로 준비해야 한다. 선택된 A/B/C 3건의 수작업 확인은 사후 확인이다.
실제 공개 적합성 최종 검사는 #65 submission projection의 바이트 검사와 check_claims에서 수행한다.
최종 review 파일이 정책 검수만 포함하거나 stores가 일부뿐이면 실행은 중단된다. 고정 파일을 몰래 보완하지 않는다.

## 선택 규칙

- tailored: 공개 가능·open으로 검수된 표시 정책 중 gu 또는 biz_type 조건으로 표시된 정책 수.
  matched와 check_required 모두 포함하며 common은 제외한다. 조건 둘이 있어도 1건이다.
- A: high + tailored >=2. 없으면 high + >=1(alt1), 다음 mid + >=2(alt2), 다음 none.
  tailored 내림차순 → 민감 false → 표시 요인 수 내림차순.
- B: A를 제외한 high의 **실제 최소 tailored count**. 기대 최소값 1을 하드코딩하지 않는다.
  민감 false → 표시 요인 수 내림차순. 실제 최소값은 요약 B 행의 tailored_policy_count_min에 기록한다.
- C: A/B와 다른 low. 가능하면 A와 같은 업종, 없으면 업종 무관(alt1). 민감 false 우선.
- 마지막 동률 키는 `sha256(f"{seed}:{store_id}")`. 전체 후보에 적용한 다음 상위 10개를 자른다.
  그 안에서 기존 독립 Random(seed).choice를 유지한다. A=20261004, B=20261005, C=20261006.
  store_id lexical 정렬은 사용하지 않는다. 입력 순서가 달라도 결과가 같다.
- 개인 확률·CI·contribution 크기를 선택 키에 쓰지 않으며 결과를 보고 기준을 바꾸지 않는다.

## 실행과 #65 연결

최종 QA 이후 저장소 루트에서 다음 입력을 제공한다. 실제 입력 경로는 비공개다.

```text
python scripts/select_demo_stores.py --db <final-db> --review <frozen-review> \
  --policy-file <policies.json> --policy-sha256 <위의 최종 정책 해시> --policy-checked-at 2026-10-03
```

CLI 정책 옵션을 생략하면 기존 `regen_w3.json`의 demo.policy.path/sha256/checked_at을 읽는다.
이 PR은 #60 설정이나 정책 원천을 수정하지 않는다.

- outputs/demo/demo_selection_private.json: 전체 provenance, 선택 메타. 점포와 case 매핑을 포함하지 않는다.
- outputs/demo/demo_selection_public.json: 구·업종·band·단계·후보 수·top 수·seed·rule_version=0.3,
  정책 확인일/건수, 해시 앞 16자. 식별정보·개인 확률·CI 없음.
- **outputs/demo/demo_cases_for_export.json**: 점포와 case 매핑이 들어가는 유일한 비공개 출력.
  `--export-out`으로 변경 가능하며 기본은 private-out과 같은 폴더. git 무시/저장소 밖 경로만 허용.

```json
{"cases": [{"case_label": "A", "store_id": "<private-reference>", "rule_stage": "base", "n_candidates": 12, "seed": 20261004}]}
```

실제 파일은 A/B/C 세 행이다. 기본→base, 대안1→alt1, 대안2→alt2, 없음→none.
none이면 store_id=null, n_candidates=0. #65 `load_cases`/`--cases`에 파일을 그대로 전달한다.
기존과 다른 증거/결과를 덮어쓰지 않는다. 세 출력 경로의 충돌과 입력 덮어쓰기를 거부한다.
stdout은 건수만, 오류는 고정 분류만 출력한다.

## 합성 검증

partial review 거부, canonical completeness, seeded hash-before-top10, lexical 편향 제거,
rule 0.3, B 실제 최소값, matched/check_required, common 제외, A/B/C distinct를 검증한다.
`tests/test_select_demo_stores.py` 연결 테스트는 #65 checkout을 `SUBMISSION_CHECKOUT`으로 지정한다
(로컬 기본: 형제 submission65). #65의 integration test에 실제 selector 출력 파일을 그대로 넣어
load_cases와 전체 submission export/claims/identifier 게이트를 실행한다. CI 단독 checkout에는 상대 PR이 없어
이 교차 테스트만 skip할 수 있으며 별도의 합성 adapter 계약 검증은 항상 실행한다.
