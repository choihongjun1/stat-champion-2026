# 상호 매칭 검수 안내 (#28, 2026-09-27, 2026-09-28 PR #39 리뷰 M1–M5 반영)

## 목적
질문 두 개를 **판정표 하나로 한 번에** 검수한다. 판정 방법은 같고, 뽑는 기준과 집계만 다르다.

1. **온라인 요인 표시 보류 점포.** W2-3 진단에서 블로그 언급이 많은 쪽에서 위험이 높게 나온 점포는 상호 오탐(다른 가게 글이
   섞임) 가능성 때문에 온라인 요인을 화면에 표시하지 않고 보류해 두었다(`display=false`). 이 점포들의 매칭 글이 실제로 그 가게
   글인지 확인해 **오탐률**을 추정하고, 오탐 점포는 온라인 feature를 NA로 둔 뒤 #33·#34를 다시 돌린다.
2. **짧은 상호의 영업/폐업 오탐률 차이.** DATA_CATALOG §6(PR #21)의 "짧은 상호 100건 영업/폐업 층화 blind 검수" 계획을 여기에
   합쳤다. 짧은 상호는 폐업률이 높아서, 폐업 쪽 오탐이 더 높으면 온라인 feature에 **라벨과 상관된 노이즈**가 있다는 뜻이다.

## 검수 대상 (2026-09-28 재생성, 전체 122곳, 2026Q2 serve 기준)
| 그룹 | 모집단 | 뽑는 방법 | 대상 수 |
|---|---|---|---|
| `priority` | 2026Q2 serve(`outputs/serve/2026Q2/`)의 온라인 요인 검토 대기(`hold_reason == "online_review"`) 802곳 | 예측 확률 상위 | 3 |
| `random` | 위와 같음 (priority 제외) | seed 고정 무작위 | 20 |
| `short_name` | **M2(PR #39 리뷰):** 짧은 상호이면서 블로그 언급이 1건 이상**이고 master_base 대상 점포**(모델이 실제로 쓰는 점포)인 2,325곳 (영업 1,354 · 폐업 971) — master_base로 한정하기 전 모집단은 8,148곳(영업 1,524 · 폐업 6,624) | 영업·폐업 층별 seed 고정 무작위 | 100 (영업 50 · 폐업 50) |

- **짧은 상호** = PR #21 `collect_online_presence.py`(커밋 f026602)의 `normalize_name`으로 정규화한 뒤 2자 이하. 블로그 언급
  매칭에 쓴 규칙과 똑같다 (`name_match_review.match_name_norm`, 테스트로 고정). 인허가 표의 `name_norm`과는 다른 규칙이다.
  - 3구 전체 짧은 상호는 9,816곳으로 DATA_CATALOG의 9,817곳과 1곳 다르다 (수집 입력 시점 차이로 보임).
- 언급이 없는 점포는 판정할 글이 없어 짧은 상호 모집단에서 뺀다.
- **M2(모델 모집단 기준 층화):** short_name의 목적은 "이 오탐이 모델 feature(NA 처리)에 영향을 주는가"이므로, 모집단을
  실제로 모델이 학습·서빙에 쓰는 `master_base` 대상 점포로 한정한다(`--master outputs/master/master_base.parquet`).
  이전 표본(모집단 8,148곳)은 대부분 모델 대상이 아닌 점포였다 — 폐업 층 6,624곳 중 master_base 대상은 971곳뿐이었다.
- 짧은 상호 표본은 표시 보류 점포 선정과 **따로** 뽑는다. 두 그룹에 모두 뽑힌 점포는 대상 목록에 **한 번만** 싣고, 키에는 속한
  그룹을 모두 적는다 (예: `random;short_name`). 이번 생성에서는 1곳이 겹쳤다.
- 이전 회차(2026-09-27, master_base 제한 전) 목록은 `outputs/review/_round1/`에 보관한다. 회차 간 판정 재사용은
  아래 "회차가 바뀔 때(재검수 부담 축소)" 참고.

## blind 원칙
- 대상 목록·판정표 어디에도 **위험도·등급·폐업 여부·폐업일을 넣지 않는다.** 판정은 글 내용만 보고 한다.
- **판정자는 점포가 어느 그룹인지 모른다.** 대상 목록·판정표에는 선정 그룹(priority/random/short_name)도, 짧은 상호의 층
  (영업/폐업)도 없고, 행 순서도 섞여 있다. 점포는 `review_id`(R001…)로만 구분한다.
- 선정 그룹·층은 선정 그룹 키(`name_match_key.csv`)에만 있다. 박안석이 따로 보관하고 집계 때만 쓴다. **전달하지 않는다.**
- 대상 목록에는 상호가 들어 있으므로 **저장소가 아니라 팀 드라이브로만** 주고받는다.

## 순서
아래 1~3단계는 **첫 회차**(대상 목록 → 판정표 → 집계) 절차다. 이미 판정받은 회차가 있고 대상만 바뀐 경우(현재
2026Q2 serve 기준 122곳)는 아래 "회차가 바뀔 때"의 round2 → 판정 → merge → summarize를 따른다.

| 단계 | 누가 | 명령 | 결과 |
|---|---|---|---|
| 1. 대상 목록 | 박안석 | `targets` | `name_match_targets.csv` → 드라이브로 전달 (`name_match_key.csv`·`name_match_key_meta.json`은 보관) |
| 2. 판정표 생성·판정 | 손유성 | `sheet` 후 `verdict` 칸 채우기 | `name_match_sheet.csv` → 드라이브로 반환 |
| 3. 집계 | 박안석 | `summarize --key name_match_key.csv` | 그룹별 글·점포 단위 오탐률, 짧은 상호 폐업−영업 차이, 오탐 점포 목록 |

첫 회차에 전달할 파일은 `name_match_targets.csv` **하나뿐**이다. 판정표는 대상 122곳 × 점포당 최대 3건이라 최대
366줄이다 (날짜 필터 안 글이 3건 미만인 점포는 그만큼 줄이 적다).

### 1단계 실행 (분석 쪽)
```
python -m src.analysis.name_match_review targets \
    --diagnosis outputs/serve/2026Q2/diagnosis.parquet \
    --licenses outputs/standardized/licenses_3gu.parquet \
    --mentions outputs/online/online_mentions_monthly.parquet \
    --master outputs/master/master_base.parquet \
    --out outputs/review/name_match_targets.csv
```
- 기본값: priority 3, random 20, 짧은 상호 층별 50 (`--n-short-per-stratum`), seed 20260927.
- `--master`를 주면 M2(모델 모집단 제한)가 적용된다. 없으면 이전처럼 미제한(경고 출력).
- 키 옆에 `name_match_key_meta.json`이 생긴다. seed·모집단 크기(점포 정보 없음)에 더해 **M4(재현성)**: 입력 파일
  (diagnosis·licenses·mentions·master) sha256과 serve 기준(`serve_score_origin`·`serve_as_of`·`serve_meta_sha256`)을 남긴다.
  집계 때 짧은 상호 가중치로도 쓴다.
- 메타에 짝인 대상 목록·키의 sha256(`targets_sha256`·`key_sha256`)도 남긴다. round2·summarize는 이 값이 실제 파일과
  다르면 멈춘다(다른 회차 파일이 섞이면 가중치·serve 기준이 틀린다). 이 기능 이전에 만든 메타(2026-09-28 전달본 포함)는
  해시가 없어 경고만 낸다.
- 2026-09-28 전달본의 `mentions_sha256`은 `data/01_interim/online/online_mentions_monthly.parquet`와 같다 (위 예시 경로
  `outputs/online/…`는 파일 해시가 다르지만 짧은 상호 모집단 1,354 / 971은 같다).

### 2단계 실행 (원본 `blog_items.jsonl.gz`가 있는 컴퓨터, 저장소 루트에서)
```
python -m src.analysis.name_match_review sheet \
    --targets name_match_targets.csv \
    --items data/00_raw/online/blog_items.jsonl.gz \
    --key name_match_key.csv --licenses outputs/standardized/licenses_3gu.parquet \
    --master outputs/master/master_base.parquet \
    --per-store 3 --out name_match_sheet.csv
```
- 원본이 여러 파일로 나뉘어 있으면 `--items`에 모두 적는다 (`blog_items_*.jsonl.gz`).
- 점포별로 매칭된 글 중 최대 3건을 뽑는다 (`--per-store` 기본값 3). 최신순이 아니라 **seed 고정 무작위**이며, 최근 12개월 글을 먼저 채운다.
- **M1(날짜 필터, PR #39 리뷰):** `--key`(선정 그룹 키)를 주면 뽑기 전에 글 날짜를 그룹에 맞게 제한한다. 판정표에는
  날짜가 그대로 보이지만(그룹·층은 안 보인다), **모형이 실제로 볼 수 있는 시점의 글만** 뽑아 blind는 유지한 채 통계적
  타당성을 맞춘다.
  - `short_name`: 인허가일 ~ 폐업일(영업 중이면 무제한), `--master`를 주면 그 점포가 master_base에 등장하는
    구간(첫 origin − 3개월 ~ 마지막 origin 말)과 교집합. 폐업 이후 다른 가게 글이 폐업 층 오탐률을 구조적으로
    높이는 문제, 인허가 이전 글이 다른 가게일 위험을 막는다.
  - `priority`/`random`: 하한 없음, serve 기준일(`--serve-as-of`, 기본은 키 메타의 `serve_as_of`) 이후 글은 제외 —
    모형이 예측 시점 이후의 정보를 실제로는 못 봤다.
  - 제외된 글 수를 그룹·층별로 `{sheet}_meta.json`에 남긴다.
  - `--key` 없이 실행하면 M1을 생략하고 경고한다 (예전처럼 날짜 제한 없음, 하위 호환용).
- 같은 폴더에 `name_match_sheet_README.md`(판정 안내)와 `{sheet}_meta.json`(입력 해시·M1 제외 건수)이 함께 생긴다.
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
- **M3(PR #39 리뷰): `verdict`는 세 값 중 하나로 반드시 채운다.** 판단할 수 없으면 빈칸이 아니라 **"판단불가"**로 적는다.
  빈칸이 하나라도 있으면 `summarize`가 멈춘다 — 미입력을 조용히 빼고 집계하지 않는다.

## 집계 (3단계)
- 그룹별 글·점포 단위 오탐률. 신뢰구간(Wilson 95%)은 무작위 표본(`random`, `short_name`과 그 층)에만 붙인다.
  두 그룹에 속한 점포는 두 그룹 모두에 센다.
- 짧은 상호: 영업·폐업 층별 오탐률과 **차이(폐업 − 영업)의 95% CI (Newcombe)** → `name_match_short_strata.csv`.
  CI 하한이 0보다 크면 폐업 쪽 오탐이 유의하게 높다 → 온라인 feature에 라벨과 상관된 노이즈가 있다.
  - `short_name` 전체 오탐률은 **층별 모집단 비율(영업 1,354 : 폐업 971, M2 반영)로 가중한 값**이다. 95% CI는 층별 Wilson
    구간을 같은 가중으로 결합한다(MOVER, Newcombe 차이 구간과 같은 방식). 표본은 층별 50곳씩이라 비가중 값은 폐업을 덜
    반영한다 — 참고로만 함께 적는다. 가중치는 `name_match_key_meta.json`에서 읽고, 없으면 비가중 값만 내고 경고한다.
  - **M2(업종 구성 비교, PR #39 리뷰):** `--key`를 주면 층별 업종 구성표를 `name_match_short_biz_composition.csv`에 낸다 —
    업종 차이가 층 오탐률 격차에 섞여 들어오는지 확인용 (예: 폐업 층에 특정 업종이 몰려 있으면 그 업종의 매칭 특성이
    "폐업이라서"가 아니라 "그 업종이라서"일 수 있다).
  - 판단은 점포 단위로 한다. 글 단위 CI는 같은 점포 글끼리의 상관을 무시해 좁게 나온다.

## 회차가 바뀔 때 (재검수 부담 축소)
모집단·날짜 필터(M1·M2)가 바뀌어 대상을 다시 뽑아야 할 때, 이미 판정받은 (점포, 글) 쌍을 다시 보내지 않는다.

경로는 로컬 저장소 기준 예시다. 지난 회차 판정표·대상 목록은 `data/01_interim/review/`, 이번 회차 3종
(`name_match_targets.csv`·`name_match_key.csv`·`name_match_key_meta.json`)은 `data/`, 출력은 `outputs/review/round2/`
(gitignore)에 둔다. 지난 회차 key가 따로 없으면 지난 회차 대상 목록(`review_id`, `store_id`가 있다)을 `--old-key`로 쓴다.

**1) round2 — 재사용 판정 추리기 + 부족분 새로 뽑기 (분석 쪽, 원본이 있는 컴퓨터)**
```
python -m src.analysis.name_match_review round2 \
    --old-sheet data/01_interim/review/name_match_sheet.csv --old-key data/01_interim/review/name_match_targets.csv \
    --new-targets data/name_match_targets.csv --new-key data/name_match_key.csv \
    --licenses outputs/standardized/licenses_3gu.parquet --master outputs/master/master_base.parquet \
    --raw data/00_raw/online/blog_items.jsonl.gz --out outputs/review/round2
```
- **`--licenses`·`--master`는 필수다.** 빠지면 날짜 필터(M1)가 꺼져 재사용·새 추출이 조용히 달라지므로(실데이터
  56건·22곳 → 73건·25곳) 멈춘다. priority/random 상한(serve 기준일)은 `--serve-as-of` 또는 key 메타의 `serve_as_of`에서
  읽고, 둘 다 없으면 멈춘다.
- 새 회차 대상 중 이전 판정표에 있던 (점포, 글 link) 쌍은 판정을 그대로 재사용한다 (날짜 필터를 옛 판정에도 다시 적용
  — 필터로 빠지는 글은 재사용하지 않는다).
- **보충(top-up)**: 점포×그룹마다 그 그룹 구간 안의 유효 판정 글이 **3건 미만**이면, 원본에서 그 구간 안 글 중 이미
  판정한 글이 아닌 것으로 3건까지 seed 고정 무작위로 채운다. 구간 안 글이 모자라면 있는 만큼만 채운다. 겹침 점포는
  그룹마다 따로 판단하고, 뽑은 글은 합집합(중복 없이)으로 싣는다.
- 출력 (`outputs/review/round2/`):

  | 파일 | 내용 | 누구 |
  |---|---|---|
  | `name_match_sheet_round2.csv` | 새로 판정할 글만 (blind — store_id·그룹·층 없음) | **판정자에게 이 파일만 전달** |
  | `name_match_reused_round2.csv` | 재사용 판정 (store_id 포함) | 분석 쪽 보관 |
  | `name_match_round2_manifest.csv` | 이번 회차 판정 대상 글 전체(재사용+새)와 글마다 날짜가 드는 그룹 | 분석 쪽 보관 (merge 검증 기준) |
  | `name_match_targets_round2.csv` | 보충이 필요했던 점포 목록 (store_id 포함) | 분석 쪽 보관 — **판정자에게 보내지 않는다** |
  | `round2_report.json` | 재사용·보충·매칭 없음 건수, 날짜 필터 적용 여부·그룹별 제외 건수 (점포 식별 정보 없음) | 분석 쪽 |

  `name_match_targets_round2.csv`에는 store_id(인허가 관리번호)가 있어 외부에서 영업 상태를 조회할 수 있다 — blind를
  지키려고 판정자에게는 판정표만 보낸다(판정표에 상호·구·동·업종이 이미 있다).
- 원본 `blog_items.jsonl.gz` 없이(`--raw` 생략) 돌리면 새 글을 뽑을 수 없어 판정표가 0행이다 (원본 공유 필요).
- 2026-10-01 로컬 실데이터: 대상 122곳 중 재사용 56건·22곳, 새 판정표 225줄·90곳, 매칭 없음 19 점포×그룹
  (short_name 영업 6 · 폐업 13), 옛 판정 73건 중 17건과 원본 10,033건 중 4,998건이 날짜 필터로 빠짐. 같은 입력으로 다시
  돌리면 출력이 같다.

**2) 판정** — 판정자가 `name_match_sheet_round2.csv`의 `verdict`를 모두 채워 돌려준다. **행을 지우거나 link·review_id를
고치지 않는다** (판단할 수 없으면 '판단불가'). 돌려받은 파일은 round2 출력을 덮어쓰지 않게 다른 폴더
(예: `outputs/review/round2_returned/`)에 둔다.

**3) merge → 4) summarize (분석 쪽)**
```
python -m src.analysis.name_match_review merge \
    --reused outputs/review/round2/name_match_reused_round2.csv \
    --new-sheet outputs/review/round2_returned/name_match_sheet_round2.csv --new-key data/name_match_key.csv \
    --out outputs/review/round2/name_match_merged.csv
python -m src.analysis.name_match_review summarize --targets data/name_match_targets.csv \
    --key data/name_match_key.csv --sheet outputs/review/round2/name_match_merged.csv \
    --licenses outputs/standardized/licenses_3gu.parquet --master outputs/master/master_base.parquet \
    --separate-no-match --out outputs/review/summary/round2
```
- merge는 `--reused`와 같은 폴더의 `name_match_round2_manifest.csv`로 판정본을 검증한다(`--manifest`로 바꿀 수 있다,
  없으면 멈춘다). **멈추는 경우**: round2 판정표의 글이 판정본에서 빠졌다(행 삭제), 판정본에 round2가 만들지 않은 글이
  있다(link 수정·행 추가), 이번 회차 key에 없는 review_id가 있다, verdict 미입력이 있다, 재사용 파일이 manifest와
  다르다, post_date를 채우지 못했다. 판정 누락은 '매칭 없음'으로 넘어가지 않는다.
- **매칭 없음(점포×그룹의 구간 안 글 0건)**은 에러가 아니다 — merge가 건너뛰고 meta에 **점포×그룹 단위**로 남긴다
  (`n_no_match_store_groups`, `no_match_store_groups`, `no_match_by_group`). 겹침 점포는 한 그룹만 매칭 없음일 수 있다.
  summarize `--separate-no-match`가 이 점포×그룹을 '매칭 없음'으로 따로 센다(오탐률 분모에는 넣지 않는다 — 기본값은
  꺼짐이며, 끄면 '판정불가'에 섞여 센다).
- merge 출력은 summarize 입력 그대로다(`item_no`, `post_date` 포함). `--old-sheet/--old-key`는 재사용 파일에
  `post_date`가 없을 때(이 기능 이전의 round2 산출물)만 필요하다.
- summarize는 key 메타의 `targets_sha256`·`key_sha256`이 `--targets`·`--key`와 다르면 멈춘다 (해시가 없는 이전 메타는 경고).

## 한계
- **글 날짜로 영업/폐업을 짐작할 수 있다.** 판정표의 `post_date`가 오래전에 끊긴 점포는 폐업 점포일 가능성이 높아 보인다.
  층 정보를 판정표에서 뺐어도 이 단서는 남으므로, 짧은 상호 층 차이는 완전한 blind 비교가 아니다.
- **인허가일보다 이전 날짜의 글은 다른가게로 판정하는 근거가 된다.** 가게가 생기기 전 글은 같은 상호의 다른 가게이거나 일반
  단어일 가능성이 높다. 판정표에는 인허가일이 없으므로 판정자가 따로 확인해야 하며, 이 근거를 쓴 경우 `note`에 적는다.

## 결과 돌려주기
채운 `name_match_sheet.csv`(회차가 바뀌었으면 `name_match_sheet_round2.csv`)를 행을 지우거나 고치지 않은 채로
**팀 드라이브**에 올려 주세요 (상호가 들어 있어 저장소·이슈·PR에는 올리지 않는다).
집계 결과(오탐률)는 #28에 숫자만 남긴다.
