# W3-15 시연 사례 선택 (H2/H3)

Issue #49의 기존 T5(5945674299), 보완 제안(5949343653) 및 사용자 H2/H3 요청을 그대로 코드에 고정했다.
규칙 버전은 T5-H2-2026-10-02-0.1이다. 팀 확인/게시 전 준비용이며 결과를 보고 규칙을 바꾸지 않는다.
H2 게시 초안은 [T5_SUPPLEMENT_COMMENT.md](drafts/T5_SUPPLEMENT_COMMENT.md)에 있고 실제 게시하지 않았다.

## 정책 경로와 현재 blocker

main의 build_db는 --policies로 명시한 JSON 배열 또는 policies 배열 래퍼를 읽는다.
기본값은 None이며 정책 입력이 없으면 policy_matching=not_performed이다.
모델 serve는 이 원천을 읽지 않는다. 매칭은 build_db, 정적 export는 SQLite에 저장된 매칭 결과를 읽는다.
현재 main/로컬 checkout에는 실제 정책 원천이나 그 파일의 고정 경로가 없다.
따라서 실제 경로·현재 건수·해시·확인일은 미검증이며 #54의 18→16 정정과 일치한다고 단정할 수 없다.
collected_at은 원천의 수집일이고 serving as_of는 점포 기준 분기 말일이다.
현행 policy_source에는 apply_status/checked_at이 없어 수집일만으로 모집 상태 재확인을 대신할 수 없다.

#60 브랜치와 regen_w3.json은 수정하지 않았다. #60에 최종 정책 경로, 전체 sha256, 확인일 및 A/B/C 보완 확정 기록을
반영해야 한다. 사용자 확인/손유성 확인, 실제 정책 파일, finalized SQLite 및 공개 검수 증거가 실제 실행 전 조건이다.
현재 외부 노트북의 작업트리/실데이터에는 접근하거나 변경하지 않았다.

## 입력 계약

scripts/select_demo_stores.py는 release-ready SQLite 정본을 읽기 전용으로 연다.
모델·diagnose·serve를 import하거나 실행하지 않고 위험도·등급·매칭을 재계산하지 않는다.
DB 파일 해시와 run의 policies_sha256/n_policies, 저장된 정책 conditions 및 원천의 조건을 대조한다.
WAL/journal이 남아 있는 DB는 확정 snapshot으로 보지 않고 중단한다.

별도 비공개 검수 JSON(--review)의 계약은 다음과 같다. 실제 ID/해시는 공개하거나 커밋하지 않는다.

- contract_version: demo-review-0.1
- score_origin: 2026Q2, as_of: 2026-06-30
- db_sha256, policies_sha256: 전체 64자리 (로컬 파일)
- policy_checked_at: 고정한 공식 공고 확인일
- stores: 점포 참조와 publication_guard_passed/claims_passed boolean. 둘 다 true인 점포만 허용.
  누락 점포는 허용하지 않는다. blanket publication_approved=false를 개인 검수 결과로 추측하지 않는다.
- policies: 원천 전체 정책 참조와 public_eligible boolean, apply_status(open/closed/unknown), checked_at.
  open이며 public_eligible=true인 카드만 표시/개수 판정에 사용한다. 마감·상태 불명은 제외한다.

이 파일은 실제 공개/문구 검수를 완료한 사람이 준비하는 증거다. 스크립트가 원문 모집 상태나 실명 비식별을 자동 승인하지 않는다.
생성 시점 DB와 정책 해시에 묶고, 정책 확인일을 CLI/config와 대조한다. 원천에 신규 purpose/apply_* 필드가 생겨
현행 스키마에 맞지 않으면 추측해 무시하지 않고 중단한다. H5/#54 계약 조율 후 별도 수정이 필요하다.

## 고정 규칙과 helper 정의

- 맞춤: 원천 conditions.gu 또는 conditions.biz_type이 null이 아니고 매칭 결과 matched_by의 gu/biz_type 증거와 일치.
  matched만 개수에 넣고 check_required는 넣지 않는다. 위험요인 linked_factor_ids로 맞춤을 판정하지 않는다.
- 공통: 위 두 조건이 모두 null인 사업. 업력 조건은 별개이며 맞춤 사업 수에 넣지 않는다.
  B는 표시 가능 정책이 1건 이상이고 표시되는 모든 정책이 공통이어야 한다.
  맞춤 check_required가 표시되는 점포는 “공통만”이 아니므로 B에서 제외한다.
- 표시 가능 진단 요인 수: factors에서 display=true인 수. 기존 스키마의 display⇔hold_reason=null 계약을 사용한다.
- interpretation_sensitive: 모든 factors 중 하나라도 true이면 점포를 true로 판정한다.
  설명 안정성 우선이며 위험도 우열은 아니다.
- A: high+맞춤 matched≥2 → high+≥1 → mid+≥2 → 없음.
  맞춤 수 내림차순, 민감 false, 표시 요인 수 내림차순.
- B: A 제외, high+맞춤 matched=0+공통만 표시 → 없음.
  민감 false, 표시 요인 수 내림차순.
- C: A/B 제외, low 중 A와 같은 업종 우선. 없으면 업종 무관(대안1), 없으면 없음.
  민감 false만 우선한다. C에 요인 수 우선순위를 추가하지 않는다.
- 마지막 동률은 비공개 점포 참조의 문자열 오름차순으로 정렬하고 상위 min(10,후보 수) 중 독립 Random(seed).choice로 1곳.
  seed는 A=20261004, B=20261005, C=20261006. 입력 순서/set/dict 순서에 의존하지 않는다.
  A 변경이 난수 상태를 통해 B/C에 영향을 주지 않는다. A의 업종이 바뀌어 C 후보풀이 달라지는 것은 같은 업종 규칙의 의도다.
  개인 확률·구간과 contribution 크기는 정렬/선택에 사용하지 않는다.

## 실행과 출력

저장소 루트에서 --db와 --review를 지정한다. 정책 freeze는 CLI의 --policy-file, --policy-sha256, --policy-checked-at로
모두 지정하거나 regen_w3.json의 demo.policy.path/sha256/checked_at을 읽게 한다.
상대 설정 경로는 checkout 루트 기준이다. main의 TBD demo.rule 문자열을 자동 해석하지 않는다.
전체 해시는 비공개 config로 전달하고 로그/명령 캡처에 공유하지 않는다.
#60에는 demo.policy의 이 세 필드를 넣는 계약을 제안하며 이 PR에서 설정을 미리 확정하지 않는다.

- outputs/demo/demo_selection_private.json: 선택 사례, 비공개 점포 참조, 전체 입력 해시·정책 파일 경로. git 무시 경로만 허용.
- outputs/demo/demo_selection_public.json: 사례 라벨/구/업종/band/단계/후보 수/top 수/seed, 기준 분기·일,
  입력 해시 앞 16자, 정책 확인일/수집일·건수, 규칙 버전, 생성 commit. 점포 참조·상호·상세주소·확률/구간은 포함하지 않는다.
  이 요약은 public report 자체가 아니므로 별도 demo-selection-public-0.1 allowlist validator를 적용한다.
  #59 공개 report 원칙과 #56 claims 검사 취지를 보존하며 공개 report로 위장하지 않는다.
- 공개 요약의 hash prefix는 로컬 private provenance의 전체값 앞 16자와 일치한다. commit은 공개 코드의 Git revision이다.
- policy hash mismatch는 DB 열기/선택/출력 전에 종료 코드 2. DB 정책 hash 또는 검수 hash/확인일 불일치도 중단.
- 같은 증거의 반복 실행은 같은 결과. 기존 출력과 다른 증거/결과를 자동 덮어쓰지 않는다.
  리허설/최종 등 다른 snapshot은 별도 비공개 출력 경로에 기록하고 서로 대체하지 않는다.
- stdout은 선택 건수만, 오류는 고정 분류만 출력한다. 실제 경로/점포 값/전체 해시는 출력하지 않는다.

실제 정책 파일과 검수 증거가 없어 이번 작업에서 실데이터 추출은 실행하지 않았다.
합성 데이터로만 테스트하고 전체 pytest를 실행한다. 장시간 재생성에는 영향을 주지 않는다.
