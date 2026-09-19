# W1 Task — MDIS 전처리 · 폐업 라벨 구축

담당: 박안석
기간: W1 (2026-09-14 ~ 09-20)
관련 문서: `DECISIONS.md` (2026-09-13 전체), `DATA_CATALOG.md` §1 §2 §5, `ANALYSIS_PLAN.md` §1 §3

> 이 문서는 작업 명세다. 여기 규칙과 `DECISIONS.md`가 충돌하면 `DECISIONS.md`를 따르고,
> 충돌 사실을 팀에 보고한 뒤 이 문서를 갱신한다.

---

## 0. 산출물

| 순서 | 산출물 | 경로 | 소비처 |
|---|---|---|---|
| A | `mdis_stage_a.parquet` | `outputs/mdis/` | Stage 3 DML (W3) |
| A | `mdis_stage_b.parquet` | `outputs/mdis/` | Stage 3 DML 강도 분석 (W3) |
| A | `MDIS_CODEBOOK.md` | `docs/` | 보고서 변수 사전 |
| B | `labels_base.parquet` | `outputs/labels/` | Detect 모델 (W2) |
| B | `labels_enriched.parquet` | `outputs/labels/` | Detect 모델 Enriched (W2) |
| B | `LABEL_SPEC.md` | `docs/` | 라벨 정의 명세 |

재사용 코드는 `src/data/` 로 이동한다. notebook에만 로직을 남기지 않는다.

---

## A. MDIS 전처리

### A-1. 목적

Stage 3 인과분석(DML)의 입력 테이블을 만든다. `DECISIONS.md` 2026-09-13 "생존/모델링 방법 범위"에 따라
MDIS DML은 Stage 3의 핵심 레버 1개다.

### A-2. 입력

```
$PROJECT_DATA_ROOT/00_raw/MDIS/2023_연간자료_등록기반_20260910_85675.csv
encoding: cp949 / 40,000행 × 156열
```

### A-3. 절차

1. **컬럼명 먼저 출력한다.** 파일설계서의 표준항목명과 실제 CSV 헤더가 다를 수 있으므로
   위치(항목번호)가 아닌 **컬럼명으로만 접근**한다. (`DATA_CATALOG.md` §1의 인허가 교훈과 동일 원칙)

2. 산업중분류코드 `47`, `56`, `96` 필터 → **5,042행** (assert)

3. 변수 추출

   | 역할 | 변수 |
   |---|---|
   | 결과 | 경영_영업이익, 경영_매출금액 |
   | 처치 | 경영_전자상거래_매출실적여부, 경영_전자상거래_매출비율 |
   | 공변량 | 산업대분류·중분류, 행정구역시도코드, 창업연월, 합계종사자수, 영업비용_임차료, 부채여부, 판매처별매출_소비자비율 |
   | 보조 레버 후보 | 창업준비활동 4종 (사업계획서·시장조사·동종업종경험·창업교육) |
   | 가중치 | 사업체수가중값 |

4. 파생변수
   - `tenure_months` = (TENURE_BASE_YEAR×12 + TENURE_BASE_MONTH) − (창업연도×12 + 창업월), 기준 2023-12
     > 주의: 이 변수는 MDIS 전처리(A-3)에서 산출되는 tenure_months이며,
     > 라벨 구축(B-2)의 age_months(인허가일자 기준 업력)와는 별개 변수다.
     > 두 변수는 서로 다른 데이터셋에서 서로 다른 방식으로 계산된다.
   - `profit_margin` = 경영_영업이익 / 경영_매출금액 (매출 0이면 NaN + `data_flag=1`)
   - `is_seoul` = (행정구역시도코드 == '11')

5. 이상치 처리
   - 영업이익 하위 1% / 상위 99% Winsorizing, `profit_outlier` 플래그 생성
   - **적자(음수)는 제거하지 않는다.** 실제 정보이며 5.5% 존재 (`DATA_CATALOG.md` §5)

6. 처치 변수
   - `treat_binary` = (경영_전자상거래_매출실적여부 == '1') → 분포 857 / 4,185 assert
   - `treat_cont` = 경영_전자상거래_매출비율. 실적 없음은 **결측이 아니라 명시적 0**

7. 출력
   - `mdis_stage_a.parquet` : 5,042행 전체
   - `mdis_stage_b.parquet` : `treat_binary == 1` 인 857행
   - `docs/MDIS_CODEBOOK.md` : 변수명 / 원본 컬럼명 / 타입 / 결측률 / 정의

### A-4. 검증 (콘솔 + 문서 기록)

- 처치군/대조군 공변량 평균 비교 + SMD
- 성향점수 겹침 확인용 기술통계
- 변수별 결측률 (30% 초과 변수는 팀 보고)
- 영업이익률 분포 — 가중 / 무가중 각각

### A-5. 주의

- 가중치는 `사업체수가중값` 1종뿐이다 (`DATA_CATALOG.md` §5). 가중 결과를 기본으로 하되
  무가중 결과를 병기해 민감도를 확인한다. 가중치 미사용을 기본으로 삼지 않는다.
- 지역은 시도 단위까지만 존재한다. **자치구 분석 불가.** 전국 5,042건으로 학습하고
  서울(474건)은 부분집합 평가로만 사용한다.
- 이 단계에서 DML을 돌리지 않는다. W3 작업이다.

---

## B. 폐업 라벨 구축

### B-1. 핵심 원칙 (`DECISIONS.md` 2026-09-13 "폐업 라벨 설계")

1. **주 라벨은 인허가 폐업일자다.** 일 단위, 폐업 건 결측 0%.
2. **소진공 소멸은 폐업 라벨로 사용하지 않는다.** 보조정보로만 생성한다.
3. 모집단은 **인허가 전체 점포**다. 소진공 매칭 실패 점포를 삭제하지 않는다. complete-case 금지.
4. 모든 feature는 observation origin 시점에 이용 가능했던 정보만 사용한다.

### B-2. 단계 1 — 인허가 단독 패널 (주소 정규화 불필요, 즉시 착수)

**입력**

```
$PROJECT_DATA_ROOT/00_raw/인허가/식품_일반음식점.csv      (cp949, 39열)
$PROJECT_DATA_ROOT/00_raw/인허가/식품_휴게음식점.csv      (cp949, 39열)
$PROJECT_DATA_ROOT/00_raw/인허가/생활_미용업.csv          (cp949, 37열)
```

**로딩 함정** (`DATA_CATALOG.md` §1)

- 일반음식점 파일에 CP949로 디코딩되지 않는 바이트 존재 → `encoding_errors` 정책 필요
- **컬럼 구성·순서가 3개 파일에서 다름** → 컬럼명 매핑표를 만들어 표준화. 위치 인덱싱 금지
- **영업 중 행의 폐업일자가 NaN이 아니라 공백 문자열** → `strip()` 후 결측 처리
- 3구 필터 실측값: 일반음식점 76,453 / 휴게음식점 20,484 / 미용업 13,418

**절차**

1. 3개 파일 로드 → 컬럼명 표준화 → 서울 + 광진·마포·영등포 필터
2. `store_id` = `"LIC_" + 개방자치단체코드 + "_" + 관리번호` (3구 범위 중복 0건 확인됨)
3. 날짜 정리: 인허가일자·폐업일자 → datetime. 공백 문자열 처리 후 결측률 보고
4. **Long Panel 생성**
   - origin(기준분기) 후보: 2021Q1 ~ (라벨 성숙 컷오프를 적용한 최신 분기)
   - 각 (store_id, origin)에 대해 origin 시작 시점에 영업 중일 때만 행 생성
     `인허가일자 <= origin_start AND (폐업일자 결측 OR 폐업일자 > origin_start)`
   - `event_12m = 1` : origin 말 기준 12개월 내 폐업일자 존재
   - `event_12m = 0` : 12개월 시점에 영업 지속
   - 12개월 관측 창을 확보하지 못하는 origin은 행을 만들지 않는다
5. 기본 feature (전부 origin 시점 정보)
   - `age_months` (origin 말 기준 업력), `biz_type`, `area`, `has_coord`
   - 각 feature에 `feature_asof` 기록 (`DECISIONS.md` 시간 누수 방지 규칙)

**라벨 성숙 컷오프 (미확정 — W1에서 결정)**

`DECISIONS.md`는 3~6개월 후보 범위만 정하고 W2 진입 전 확정하도록 했다.
본 작업에서 **월별 폐업 신고 건수의 tail stability**를 실측해 근거를 제출한다.

- 방법: 폐업일자 기준 월별 집계를 그리고, 최근 개월에서 건수가 안정화되는 시점을 찾는다
- 산출: 그래프 1장 + 권고 컷오프 개월 수 + 근거 3줄 → `DECISIONS.md` 갱신 안건으로 제출

**검증**

- 전체 행수 / 고유 store_id 수
- origin별 행수 (시간이 갈수록 증가해야 정상)
- origin별 `event_12m` 비율
- 광진구 일반음식점 Kaplan-Meier 곡선 → `outputs/figures/km_gwangjin.png`
  - 개업 1~2년차 위험이 가장 높게 나오면 정상. 반대면 라벨 오류 의심

### B-3. 단계 2 — 소진공 보조정보 (주소 정규화 산출물 필요)

**목적은 라벨 생성이 아니라 라벨 신뢰도 표시다.**

**입력**: 단계 1 결과 + `address_normalized_*.parquet` + 소진공 7개 스냅샷

**절차**

1. 소진공 스냅샷을 업소번호 기준으로 이어 존재구간 테이블 생성
   (업소번호, 최초관측, 최종관측, 업종, PNU, 정규화상호)
   - **union은 entity resolution 목적으로만 사용한다.** origin 이후 스냅샷을 feature 계산에 쓰지 않는다
2. 인허가 ↔ 소진공 매칭
   - 주 키: PNU + 정규화 상호(지점명 포함)
   - 보조: 상호 유사도 0.75 이상, 좌표 20m 이내
   - **필수 조건: 인허가 영업기간과 소진공 존재구간이 1분기 이상 겹칠 것**
     (겹침 조건 없이 매칭하면 동일 필지의 이전 점포와 신규 점포를 혼동)
3. 보조 컬럼 생성
   - `sj_status` ∈ {`confirmed_gone`, `admin_only`, `conflict_stale`, `unmatched`}
     - 경계 기준(허용 오차 일수)은 코드 내 상수로 명시하고 문서에 기록
   - `sj_gap_months` : 행정 폐업일과 소진공 소멸 구간의 차이(개월)
   - `match_confidence` : 매칭 근거(주키/유사도/좌표)와 점수
4. **매칭 실패 점포를 삭제하지 않는다.** `sj_status = unmatched`로 남긴다

**주의: 202603→202606 구간**

소멸 7,845 / 신규 11,166으로 급증했고, 소멸의 30.5%가 동일 필지·동일 상호의 새 업소번호였다.
**이 구간의 소멸·신규를 실제 폐업·개업으로 해석하지 않는다.** 보조정보 생성 시 이 구간에
별도 플래그를 부여한다.

**검증**

- `sj_status` 분포
- 매칭률 — 영업 점포와 폐업 점포를 **분리해서** 보고
  (실측 기준선: 영업 80.1% vs 폐업 55.5% — 이 격차가 complete-case 금지 근거)
- `sj_gap_months` 분포 히스토그램 → 신고 지연 실측치 (보고서 인용용)

### B-4. Base / Enriched 분리 (`DECISIONS.md` 2026-09-13)

| 데이터셋 | 구성 | origin | 용도 |
|---|---|---|---|
| **Base** | 인허가 + 상권분석 + 공시지가 | 다수 origin (2021~) | temporal validation 수행 |
| **Enriched** | Base + 소진공 개별 feature + 시점 정합 온라인 feature | 주력 2025-06 | Base 대비 incremental 평가 |

본 작업에서는 **라벨 테이블을 두 벌로 나누지 않고**, 하나의 라벨 테이블에
`sj_status` 등 보조 컬럼을 붙여 W2에서 Base/Enriched를 구성할 수 있게 한다.

---

## C. 완료 기준 (W1 종료 시점)

```
[ MDIS ]
□ mdis_stage_a.parquet 5,042행 / mdis_stage_b.parquet 857행
□ docs/MDIS_CODEBOOK.md
□ SMD 표 + 결측률 표

[ 라벨 ]
□ labels_base.parquet (인허가 단독, Long Panel)
□ 라벨 성숙 컷오프 근거 그래프 + 권고안
□ 광진구 일반음식점 KM 곡선
□ origin별 event_12m 비율 표
□ (주소 정규화 완료 시) sj_status / sj_gap_months 보조 컬럼

[ 문서 ]
□ docs/LABEL_SPEC.md — 라벨 정의, 경계 기준 상수, 제외 사유별 건수
□ DATA_CATALOG.md 전처리 이력 갱신
□ 성숙 컷오프 확정안을 DECISIONS.md 갱신 안건으로 제출
```

---

## D. 하지 않는 것

- 소진공 소멸로 폐업 라벨 생성
- 소진공 매칭 실패 점포 삭제 / complete-case 구성
- 현재 시점 온라인 정보(등록 여부·검색 순위)를 과거 origin feature로 사용
- Fine-Gray 경쟁위험 모형 구현 (optional로 강등됨)
- DML 실행 (W3 작업)
- origin 이후 스냅샷을 해당 origin의 feature 계산에 사용
