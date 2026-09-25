# W2-6 화면 더미 데이터 (Figma 작업용)

- 작성: 송채영 (W2-6 화면). Figma 화면 구현에 쓴 **더미 데이터**입니다. 모든 레코드에 `"_dummy": true`.
- 가게 이름·주소·store_id·위험도는 **모두 지어낸 값**이며 실제 점포·실제 예측과 무관합니다.
- 가게 이름·주소·store_id는 실제와 겹치지 않도록 바꾼 값 (원본 더미 일부가 실제 점포와 일치해 교체함, 인허가 데이터와 대조 완료)
- 정책명 앞의 (예시)는 지어낸 사업명이라는 표시
- 실제 서빙 출력 샘플(가린 값)은 `docs/samples/serve_2025Q2_trial/`(PR #36)에 있고,
  두 형식의 차이와 맞출 방향은 `SCHEMA_DIFF.md`에 정리했습니다.

| 파일 | 내용 |
|---|---|
| `sample_reports.json` | 점포 10곳의 결과 화면용 레코드 (risk·factors·prescriptions·online_presence·policies) |
| `sample_search_index.json` | 점포 검색용 인덱스 (상호·업종·구·동·주소) |
