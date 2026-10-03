# submission-static-0.1: #64 → #65 실행 계약

실제 사례 생성은 Claude가 H1 final DB에 28건 정책을 결합하고 matching QA를 끝낸 뒤 수행한다.
이번 변경은 합성 코드/테스트/샘플이며 실제 A/B/C를 선택하거나 제출 bundle을 생성하지 않았다.

## 직접 연결

#64의 비공개 `outputs/demo/demo_cases_for_export.json`을 `--cases`로 그대로 전달한다.
case_label, store_id, rule_stage(base/alt1/alt2/none), n_candidates, seed 계약이며 수작업 변환이 없다.
점포 연결 파일과 실제 정책/신청 상태/검수 파일은 저장소 밖 또는 git 무시 경로에만 보관한다.

```text
python -m src.serving.export_submission --db <final-db> \
  --cases <demo_cases_for_export.json> --policies-apply <policies_apply.csv> \
  --out outputs/serving/submission_public --dry-run
```

QA 통과 후 같은 명령에서 --dry-run을 빼면 저장한다. train/diagnose/serve/background 재실행은 필요 없다.
CLI에 cases가 있으면 final_policy_gate를 강제한다. DB 정책 28건과 정책 해시, CSV 해시/확인일이
최종 고정값과 같아야 한다. 정책 원천 JSON은 DB build 시 검증하고 export는 canonical provenance를 읽는다.

| 원천 | sha256 |
|---|---|
| policies.json | 0cfb716fbe9272efc816d34d2213354d3ed6cdccbc9851080eee7f40c3eefc2a |
| policies_apply.csv | 90a2b2134f54d4860481c8f84944ee615177250373abf9cd4bc119590eef5be3 |

checked_at=2026-10-03. 원천 파일은 커밋하지 않는다. 테스트/샘플에는 합성 fixture만 사용한다.
공개 meta.provenance에는 policies_sha256_12, policies_apply_sha256_12, policy_checked_at을 담는다.
기존 공개 hash-prefix 계약을 유지하며 전체 해시는 비공개 gate 입력과 코드의 동결 기준에 남긴다.

## 정책 카드 계약

CSV 필수 header: id, apply_status, apply_end, checked_at (추가 열은 읽지 않는다).
id는 canonical 정책 id와 1:1이어야 하며 누락·빈 id·중복·알 수 없는 id를 거부한다.
apply_status는 open/closed/unknown, apply_end는 ISO 날짜 또는 빈 칸(출력 null), checked_at은 ISO 날짜다.
CSV 해시·확인일·날짜 유효성·전체 id 집합을 export 전에 검사한다.

카드는 apply_status/apply_end/checked_at을 그대로 제공하며 **모든 linked_factor_ids=[]**로 강제한다.
키를 삭제하지 않는다(T4). 원 report 0.3과 원천 정책은 변경하지 않는다.
예산 소진 마감과 날짜 마감을 표현할 수 있지만 #54의 기준일 및 프론트 문구 정책은 여기서 확정하지 않는다.

저수준 Python API는 과거 테스트/미확인 상태 projection 호환을 위해 CSV 미제공 시 unknown/null/null을 제공한다.
실제 실행은 final_policy_gate=True 또는 위 CLI를 사용해야 하며 CSV를 생략할 수 없다.
submission consumer SHOULD NOT display summary_text directly.
화면은 level_text + direction으로 자체 해요체 문구를 만든다. summary_text schema/tone은 그대로다.

## 화면용 합성 샘플

[submission_bundle/meta.json](samples/submission_bundle/meta.json)과 cases/CASE-A.json, CASE-B.json, CASE-C.json,
manifest.json은 모두 submission-static-0.1, data_kind=synthetic이다. 실제 점포 정보·확률·CI·정책-요인 관계 없음.
신청 상태·날짜도 합성값이며 실제 정책을 나타내지 않는다.
`python -m src.serving.synthetic_submission`으로 합성 입력만 사용해 재생성하고 모든 gate를 검사한다.

## 연결 테스트

항상 실행: #64 producer에서 생성한 합성 adapter fixture를 load_cases에 직접 입력.
교차 PR 전체 연결: `DEMO_SELECTOR_PATH`를 #64 scripts/select_demo_stores.py로 지정하고
`python -m pytest tests/test_demo_submission_integration.py -q`.
기본 로컬 경로는 형제 demo15 checkout이다. 상대 PR이 없는 단독 CI에서는 live 교차 테스트만 skip한다.
교차 테스트는 selector가 파일을 쓰고 load_cases/export가 변환 없이 읽어 claims/identifier 0을 확인한다.
