# 상호 매칭 검수 안내 (#28, 2026-09-27)

## 목적
질문 두 개를 **판정표 하나로 한 번에** 검수한다. 판정 방법은 같고, 뽑는 기준과 집계만 다르다.

1. **온라인 요인 표시 보류 점포.** W2-3 진단에서 블로그 언급이 많은 쪽에서 위험이 높게 나온 점포는 상호 오탐(다른 가게 글이
   섞임) 가능성 때문에 온라인 요인을 화면에 표시하지 않고 보류해 두었다(`display=false`). 이 점포들의 매칭 글이 실제로 그 가게
   글인지 확인해 **오탐률**을 추정하고, 오탐 점포는 온라인 feature를 NA로 둔 뒤 #33·#34를 다시 돌린다.
2. **짧은 상호의 영업/폐업 오탐률 차이.** DATA_CATALOG §6(PR #21)의 "짧은 상호 100건 영업/폐업 층화 blind 검수" 계획을 여기에
   합쳤다. 짧은 상호는 폐업률이 높아서, 폐업 쪽 오탐이 더 높으면 온라인 feature에 **라벨과 상관된 노이즈**가 있다는 뜻이다.

## 검수 대상 (2026-09-27 생성, 전체 123곳, 2026Q2 serve 기준)
| 그룹 | 모집단 | 뽑는 방법 | 대상 수 |
|---|---|---|---|
| `priority` | 2026Q2 serve(`outputs/serve/2026Q2/`)의 온라인 요인 검토 대기(`display=false`, `data_missing=false`) 802곳 | 예측 확률 상위 | 3 |
| `random` | 위와 같음 (priority 제외) | seed 고정 무작위 | 20 |
| `short_name` | 3구 인허가 점포 중 **짧은 상호**이면서 블로그 언급(`online_mentions_monthly`)이 1건 이상인 8,148곳 (영업 1,524 · 폐업 6,624) | 영업·폐업 층별 seed 고정 무작위 | 100 (영업 50 · 폐업 50) |

- **짧은 상호** = PR #21 `collect_online_presence.py`(커밋 f026602)의 `normalize_name`으로 정규화한 뒤 2자 이하. 블로그 언급
  매칭에 쓴 규칙과 똑같다 (`name_match_review.match_name_norm`, 테스트로 고정). 인허가 표의 `name_norm`과는 다른 규칙이다.
  - 3구 전체 짧은 상호는 9,816곳으로 DATA_CATALOG의 9,817곳과 1곳 다르다 (수집 입력 시점 차이로 보임).
- 언급이 없는 점포는 판정할 글이 없어 짧은 상호 모집단에서 뺀다.
- 짧은 상호 표본은 표시 보류 점포 선정과 **따로** 뽑는다. 두 그룹에 모두 뽑힌 점포는 대상 목록에 **한 번만** 싣고, 키에는 속한
  그룹을 모두 적는다 (예: `random;short_name`). 이번 생성에서는 겹친 점포가 없었다.
- 처음 목록은 2025Q2 시험 serve(검토 대기 573곳)로 뽑았고, 2026Q2 serve로 다시 뽑았다. 짧은 상호 100곳은 serve 출력과
  무관하고 별도 난수열로 뽑으므로 두 번 모두 같다 (확인함). 이전 목록은 `outputs/review/_2025Q2/`에 보관한다.

## blind 원칙
- 대상 목록·판정표 어디에도 **위험도·등급·폐업 여부·폐업일을 넣지 않는다.** 판정은 글 내용만 보고 한다.
- **판정자는 점포가 어느 그룹인지 모른다.** 대상 목록·판정표에는 선정 그룹(priority/random/short_name)도, 짧은 상호의 층
  (영업/폐업)도 없고, 행 순서도 섞여 있다. 점포는 `review_id`(R001…)로만 구분한다.
- 선정 그룹·층은 선정 그룹 키(`name_match_key.csv`)에만 있다. 박안석이 따로 보관하고 집계 때만 쓴다. **전달하지 않는다.**
- 대상 목록에는 상호가 들어 있으므로 **저장소가 아니라 팀 드라이브로만** 주고받는다.

## 순서
| 단계 | 누가 | 명령 | 결과 |
|---|---|---|---|
| 1. 대상 목록 | 박안석 | `targets` | `name_match_targets.csv` → 드라이브로 전달 (`name_match_key.csv`·`name_match_key_meta.json`은 보관) |
| 2. 판정표 생성·판정 | 손유성 | `sheet` 후 `verdict` 칸 채우기 | `name_match_sheet.csv` → 드라이브로 반환 |
| 3. 집계 | 박안석 | `summarize --key name_match_key.csv` | 그룹별 글·점포 단위 오탐률, 짧은 상호 폐업−영업 차이, 오탐 점포 목록 |

전달할 파일은 `name_match_targets.csv` **하나뿐**이다. 판정표는 대상 123곳 × 점포당 최대 3건이라 최대 369줄이고,
블로그 언급 수(`online_mentions_monthly`)로 셈하면 **약 350줄**이다 (언급이 3건 미만인 점포는 그만큼 줄이 적다).

### 1단계 실행 (분석 쪽)
```
python -m src.analysis.name_match_review targets \
    --diagnosis outputs/serve/2026Q2/diagnosis.parquet \
    --licenses outputs/standardized/licenses_3gu.parquet \
    --mentions outputs/online/online_mentions_monthly.parquet \
    --out outputs/review/name_match_targets.csv
```
- 기본값: priority 3, random 20, 짧은 상호 층별 50 (`--n-short-per-stratum`), seed 20260927.
- 키 옆에 `name_match_key_meta.json`(seed, 모집단 크기 — 점포 정보 없음)이 생긴다. 집계 때 짧은 상호 가중치로 쓴다.

### 2단계 실행 (원본 `blog_items.jsonl.gz`가 있는 컴퓨터, 저장소 루트에서)
```
python -m src.analysis.name_match_review sheet \
    --targets name_match_targets.csv \
    --items data/raw/online/blog_items.jsonl.gz \
    --per-store 3 --out name_match_sheet.csv
```
- 원본이 여러 파일로 나뉘어 있으면 `--items`에 모두 적는다 (`blog_items_*.jsonl.gz`).
- 점포별로 매칭된 글 중 최대 3건을 뽑는다 (`--per-store` 기본값 3). 최신순이 아니라 **seed 고정 무작위**이며, 최근 12개월 글을 먼저 채운다.
- 같은 폴더에 `name_match_sheet_README.md`(판정 안내)가 함께 생긴다.
- 판정표 열: `review_id, name_raw, gu, dong, biz_type, item_no, post_date, blog_name, title, description, link, verdict, note`
  - `blog_name`은 원본에 블로그명이 없어 **링크에서 뽑은 블로그 ID**다.
  - 매칭 글이 없는 점포는 `item_no = 0`, 제목 "매칭 글 없음" 한 줄 — 판정하지 않고 비워 둔다.

## 판정 기준 (`verdict`)
| 값 | 언제 | 예시 |
|---|---|---|
| **해당가게** | 글이 이 점포(상호·동·업종)에 대한 글 | 같은 동의 같은 업종 가게 방문기, 메뉴·위치가 맞음 |
| **다른가게** | 같은 상호의 **다른 지점**이거나 다른 지역 가게 | "○○ 강남점 후기"인데 대상은 마포구 점포 |
| **다른가게** | 상호가 **일반 단어**로 쓰인 글 | 상호가 흔한 낱말이라 가게와 무관한 글에 그 낱말이 나옴 |
| **판단불가** | 지역·메뉴·사진 정보로도 이 점포인지 알 수 없음 | 상호만 나오고 위치 단서가 없는 짧은 글 |

- 필요하면 `note`에 짧게 근거를 적는다 (예: "다른 구 지점", "일반 명사로 쓰임").
- 점포 판정은 집계 단계에서 자동으로 한다: 판정 가능한 글(해당가게·다른가게) 중 **다른가게 비율 ≥ 50%면 오탐 점포.**
  모두 판단불가면 그 점포는 판정불가로 따로 센다.

## 집계 (3단계)
- 그룹별 글·점포 단위 오탐률. 신뢰구간(Wilson 95%)은 무작위 표본(`random`, `short_name`과 그 층)에만 붙인다.
  두 그룹에 속한 점포는 두 그룹 모두에 센다.
- 짧은 상호: 영업·폐업 층별 오탐률과 **차이(폐업 − 영업)의 95% CI (Newcombe)** → `name_match_short_strata.csv`.
  CI 하한이 0보다 크면 폐업 쪽 오탐이 유의하게 높다 → 온라인 feature에 라벨과 상관된 노이즈가 있다.
  - `short_name` 전체 오탐률은 **층별 모집단 비율(영업 1,524 : 폐업 6,624)로 가중한 값**이다. 95% CI는 층별 Wilson 구간을
    같은 가중으로 결합한다(MOVER, Newcombe 차이 구간과 같은 방식). 표본은 층별 50곳씩이라 비가중 값은 폐업을 덜 반영한다 —
    참고로만 함께 적는다. 가중치는 `name_match_key_meta.json`에서 읽고, 없으면 비가중 값만 내고 경고한다.
  - 판단은 점포 단위로 한다. 글 단위 CI는 같은 점포 글끼리의 상관을 무시해 좁게 나온다.

## 한계
- **글 날짜로 영업/폐업을 짐작할 수 있다.** 판정표의 `post_date`가 오래전에 끊긴 점포는 폐업 점포일 가능성이 높아 보인다.
  층 정보를 판정표에서 뺐어도 이 단서는 남으므로, 짧은 상호 층 차이는 완전한 blind 비교가 아니다.
- **인허가일보다 이전 날짜의 글은 다른가게로 판정하는 근거가 된다.** 가게가 생기기 전 글은 같은 상호의 다른 가게이거나 일반
  단어일 가능성이 높다. 판정표에는 인허가일이 없으므로 판정자가 따로 확인해야 하며, 이 근거를 쓴 경우 `note`에 적는다.

## 결과 돌려주기
채운 `name_match_sheet.csv`를 **팀 드라이브**에 올려 주세요 (상호가 들어 있어 저장소·이슈·PR에는 올리지 않는다).
집계 결과(오탐률)는 #28에 숫자만 남긴다.
