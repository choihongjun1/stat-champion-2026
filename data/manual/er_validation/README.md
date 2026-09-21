# ER validation 수작업 입력

`python -m src.data.matching_validation`이 읽는 **입력 데이터**다. 파이프라인 산출물이
아니라 사람이 만든 근거 자료라서 git으로 추적한다(`.gitignore`에 예외 규칙 있음).
이 파일이 없으면 Tier 규칙을 확정한 근거를 새 클론에서 재생산할 수 없다.

| 파일 | 역할 | 출처 |
|---|---|---|
| `match_precision_sample_v1.csv` | precision 판정에 쓴 **고정 표본 140건** (Tier3 점수 4구간 × 20, Tier4 거리 3구간 × 20) | 규칙 변경 전 매칭 결과에서 층화추출. 파이프라인을 다시 돌리면 표본이 바뀌므로 판정 당시 파일을 고정 |
| `match_validation_sample_v1.csv` | Tier1/2 sanity 표본 **56건** | 동일 |
| `precision_labels_firstpass_claude.csv` | 위 표본에 대한 **행 단위 1차 판정** (`yes` / `no` / `uncertain`) | 파일 정보(상호·PNU·주소·거리·후보 수)만 보고 내린 보수적 screening. ground truth가 아니다 |

## 규칙

- **이 파일들을 파이프라인이 덮어쓰지 않는다.** 표본을 새로 뽑으면 `_v2` 등 새 이름으로
  추가하고, 기존 판정과 섞지 않는다.
- 판정 파일은 `precision_labels_*.csv` glob으로 찾으며, 여러 개면 이름 순 마지막을 쓴다.
- baseline(규칙 변경 전 전체 매칭)은 **파일로 보관하지 않는다.** `build_baseline()`이
  현재 코드로 재생성한다(Tier4 구조 조건만 끈 cascade). 재실행 비용은
  `outputs/matching/validation/license_semas_matches_baseline.parquet` 캐시로 줄인다.
- 판정 라벨을 근거로 인용할 때는 screening이라는 한계를 함께 적는다
  (`DECISIONS.md` 2026-09-19 참조).
