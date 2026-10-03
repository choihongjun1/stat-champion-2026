# T5 rule 0.3 보완 — 게시 전 초안

최종 28건/확인일 2026-10-03을 기준으로 한다. 실제 선택은 Claude의 final DB/matching QA 이후다.
모집단은 canonical 전체 − 자동 공개 부적합이며 부분 runtime review.stores는 거부한다.
A/C의 확정 우선순위를 유지하고, B는 A 제외 high의 실제 최소 tailored count를 계산한다.
matched/check_required 포함, common 제외. 기대 최소값 1을 임계값으로 고정하지 않는다.
모든 동률 후보에 사례별 seed의 sha256 키를 부여한 뒤 기존 top10/독립 RNG를 적용한다.
rule_version=0.3. #65에는 비공개 demo_cases_for_export.json을 수작업 변환 없이 전달한다.

상세 계약과 최종 해시 gate는 [DEMO_SELECTION.md](../DEMO_SELECTION.md)를 따른다.
이 초안은 Issue에 게시하지 않았다. 실제 사례 선택과 model 재실행도 하지 않았다.

Frozen 정책 검수의 전체 hash는 유지하고 runtime store review를 별도 자동 생성한다.
두 파일의 hash 모순을 해결했으며 manual review는 선택된 3건의 사후 확인에만 사용한다.
