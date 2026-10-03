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

## Frozen policy review와 runtime store review

원본 `demo_review_policies_20261003.json`은 **정책 검수만** 동결한 파일이다.
`--policy-review`로 받으며 원본 전체 sha256을 그대로 검증한다. 정책 hash·확인일·정책 id 전체 집합·상태·조건을
대조한다. 원본의 db_sha256=null, stores=[]는 채우거나 수정하지 않는다. frozen 검수에서 DB/점포 gate는 요구하지 않는다.
기존 `--review`는 --policy-review의 별칭이며 runtime 파일과 합치는 사용법은 지원하지 않는다.

`--store-review`는 **실행 시 자동 생성**한 비공개 `demo-store-review-0.1` 파일이다.
실제 DB hash, 분기/기준일, canonical_count/review_count, 전 점포 flag, gate version과 코드/규칙 fingerprint를 담는다.
원본 frozen review의 고정 hash를 요구하지 않는다. 두 review의 전체 digest는 private provenance에 별도로 기록하고,
기존 review_sha256은 두 digest를 결합한 hash로 기록한다. 공개 allowlist와 #65 adapter는 그대로다.

### 자동 전 점포 검수

`--build-store-review`는 finalized read-only SQLite 전체를 읽어 동일한 결정적 gate를 적용한다.
수작업으로 stores나 flag를 채우지 않는다. #65 checkout의 기존 submission_report.project_case를
`--submission-root`에서 **읽기 전용**으로 불러온다. #65 코드/스키마를 수정하거나 복사하지 않는다.
기본 로컬 경로는 형제 submission65 checkout이며 Claude에서는 실제 #65 source checkout 경로를 명시한다.

- publication_guard_passed: #65 pure projection이 내부/제출 스키마 검증을 통과하고, 금지 키가 없으며,
  모든 canonical 점포 참조와 해당 점포의 상호·주소·법정동 byte search가 0이다.
  구·업종·peer_group에 포함되는 짧은 값의 예외는 #65의 기존 identifier gate와 같다.
- claims_passed: projection JSON에 #56의 기존 compiled rules와 allowlist 함수를 메모리에서 적용해 발견 0.
  JSON unicode 정규화, rule scope, 여러 줄 match의 inline allowlist 의미를 checker.scan과 동일하게 적용한다.
  실제 식별값이나 finding/context를 출력하지 않는다.
- rules/schema를 한 번만 읽고, 전체 canonical identifier는 Aho-Corasick matcher 한 번으로 구성한다.
  점포별 full bundle 생성·임시 파일 I/O·check_claims CLI 실행은 하지 않는다.
- 출력은 점포 참조 순서로 정렬한다. 입력 순서와 관계없이 동일한 DB/코드/규칙에서 같은 JSON을 만든다.
- selector는 runtime DB hash, row/count, missing=0/extra=0/duplicate=0을 검사한다.
  gate 결과를 다시 계산해 문서 전체와 대조하므로 사람이 flag를 바꿔 모집단을 줄일 수 없다.
  생성·검증 뒤 DB hash를 다시 확인한다. DB/코드/규칙이 달라졌으면 runtime review를 다시 생성해야 한다.

모집단은 **2026Q2 canonical serving 전체 대상 − 자동 공개 부적합 점포**다.
자동 gate → rule 0.3 선택 → 선택된 A/B/C 3건의 별도 사후 manual review 순서다.
manual review로 모집단을 사전에 축소하지 않는다. #65의 실제 최종 bundle 검사는 별도로 그대로 수행한다.
원천·runtime review·실제 점포정보는 저장소 밖 또는 git 무시 경로에만 두고 PR/로그에 남기지 않는다.

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

Claude의 최종 28건 DB/matching QA 이후 다음 순서로 실행한다. 실제 입력 경로는 비공개이며,
이 PR에서는 아래 명령을 실데이터로 실행하지 않았다.

```text
python scripts/select_demo_stores.py --db <final-db> --policy-review <original-frozen-policy-review> \
  --policy-file <policies.json> --policy-sha256 <최종 정책 해시> --policy-checked-at 2026-10-03 \
  --submission-root <read-only-PR65-source-checkout> \
  --store-review <private-runtime-review.json> --build-store-review
```

같은 입력에서 `--build-store-review`를 `--preflight-only`로 바꾸면 자동 gate와 전체 모집단을 검증하고,
**사례를 선택하거나 selection 파일을 만들지 않는다**. 최종 확인 뒤 이 두 mode flag를 모두 빼면 selection을 실행한다.
현재 실데이터 selection은 금지이며 Claude의 QA 완료와 실행 요청 이후에만 수행한다.

CLI 정책 옵션을 생략하면 기존 `regen_w3.json`의 demo.policy.path/sha256/checked_at을 읽는다.
이 PR은 #60 설정이나 정책 원천을 수정하지 않는다.

- outputs/demo/runtime_store_review.json: 자동 전 점포 검수. private이며 커밋하지 않는다.
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


### CLI 회귀 fixture

`--synthetic-fixture`는 tests/fixtures/demo_preflight의 고정된 합성 정책/검수 hash만 허용한다.
28건·확인일 검증을 유지하고 canonical 모든 점포가 SAMPLE 참조/(샘플) 상호/sample_synthetic 모형이어야 한다.
실제 DB가 이 모드에 들어오면 거부한다. 실제 실행에서는 이 flag를 사용하지 않는다.
fixture hash를 monkeypatch하지 않고 원본 형태(db_sha256=null, stores=[])와 별도 runtime review로
CLI build-review/preflight를 검증한다. 정책 내용 변경, runtime DB hash·누락·추가·중복·수작업 flag 변경을 거부한다.
