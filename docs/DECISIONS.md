# Decision Log

분석·서비스 방향에 영향을 주는 확정 사항을 기록한다. 변경 시 기존 결정을 지우기보다 날짜와 변경 이유를 추가한다.

## 2026-09-10 — 최종 주제 확정
- 주제: 소상공인 폐업 위험 조기진단 및 개선방향 추천 AI 서비스
- 서비스 구조는 Detect → Diagnose → Prescribe로 구성한다.

## 2026-09-10 — 해석 원칙
- SHAP은 예측모형의 변수 기여도를 설명하는 용도로 사용하며 인과적 원인으로 해석하지 않는다.
- 예측모형에서 feature 값을 바꿔 얻은 위험 변화만으로 실제 개입 효과를 주장하지 않는다.
- 개선방향에는 근거 수준을 표시하고, 통계적으로 충분히 확인되지 않은 경우 효과를 단정하지 않는다.

## 2026-09-11 — Stage 3 범위 축소
- 한 달 프로젝트의 실행 가능성을 고려하여 모든 개선요인의 인과효과 검증을 목표로 하지 않는다.
- 사업자가 대응할 수 있고 데이터로 검증 가능한 요인 중 핵심 1개를 우선 선정해 분석한다.
- 추가 효과 분석은 핵심 분석 완료 후 시간과 데이터 조건이 허용할 때 확장한다.
- 데이터·비교집단·시점 정보가 충분하지 않으면 인과효과로 표현하지 않는다.

## 2026-09-11 — 온라인 정보 사용 원칙
- 네이버·카카오 등 온라인 정보는 실제 수집 가능성과 점포 매칭률을 먼저 검증한 뒤 사용 범위를 결정한다.
- 현재 시점의 온라인 정보를 과거 분기의 특성으로 소급하여 사용하지 않는다.

---

아래 2026-09-13 결정들은 로컬 raw 데이터 전수 audit(실측 수치는 `DATA_CATALOG.md`에 기록)에 근거한다.
audit 수치는 일회성 검증 결과이며, 여기 기록된 내용만이 공식 결정이다.

## 2026-09-13 — 폐업 라벨 설계
- **주 폐업 라벨은 인허가 데이터의 폐업일자를 사용한다** (일 단위, 폐업 건 결측 0%).
- **소진공 상가정보의 소멸(스냅샷 간 업소번호 사라짐)은 폐업 라벨 자체로 사용하지 않는다.**
  - 근거: 폐업 점포의 소진공 매칭률 55.5%에 불과, 관측 간격 2~4개월로 시점 해상도 낮음,
    소멸에 ID 재발급/DB 정비가 섞여 있음 — 특히 202603→202606 구간 소멸의 30.5%가
    동일 필지+정규화 상호의 신규 업소번호로 재등장.
- 대신 소진공 대조 결과를 라벨 **보조정보**로 생성한다.
  - `sj_status`: `confirmed_gone`(폐업일이 소진공 소멸 구간과 정합) / `admin_only`(매칭됐으나
    소멸 미관측 등 인허가 단독 근거) / `conflict_stale`(폐업 후에도 소진공에 잔존) /
    `unmatched`(소진공 미매칭). 경계 기준(허용 오차 일수 등)은 구현 시 확정하고 코드에 상수로 명시
  - `sj_gap_months`: 행정 폐업일과 소진공 소멸 구간의 차이(개월)
- 보조정보의 용도: 라벨 신뢰도 플래그, 신고 지연 분포 실측 참고, conflict 제외 민감도 분석.
  라벨 값 자체를 바꾸는 데 쓰지 않는다.
- 폐업 신고 지연을 고려해 **최근 라벨에 maturity window를 적용한다. 후보 범위는 3~6개월.**
  - audit의 선행 소멸 갭 중앙값 6개월은 행정 신고 지연 외에 소진공 갱신 주기, ID 재발급,
    entity resolution 오류가 혼합된 값일 수 있으므로 확정 컷오프의 근거로 삼지 않는다.
  - W1에서 월별 폐업 신고 건수의 tail stability(최근 개월 집계의 안정화 시점)를 확인한 뒤
    **W2 진입 전에 최종 cutoff를 확정**하고 이 문서에 기록한다.
  - → **확정됨: 1개월** (2026-09-18 항목 참조). 위 "3~6개월"은 확정 전 후보 범위이며
    더 이상 유효하지 않다.

## 2026-09-13 — master dataset 모집단
- **기준 모집단은 인허가 전체 점포다.** 인허가↔소진공 매칭 실패 점포를 삭제하지 않는다.
- 소진공 유래 feature만 missing으로 유지하고, 매칭 여부와 match confidence를
  별도 feature/metadata 컬럼으로 기록한다.
- **complete-case 분석(소진공 매칭 성공 표본만 사용)을 금지한다.**
  - 근거: 매칭률이 영업 점포 80.1% vs 폐업 점포 55.5%로 결과변수와 상관 —
    complete-case는 생존 편향을 직접 유발한다.

## 2026-09-13 — 시간 누수 방지 규칙
- 모든 feature는 해당 observation origin 시점에 **실제로 이용 가능했던 정보만** 사용한다.
- 소진공 7개 스냅샷의 union은 **entity resolution(점포 식별·매칭) 목적에만** 사용할 수 있다.
  특정 origin의 feature 계산에는 origin 이후 스냅샷을 절대 사용하지 않는다.
- 모든 주요 feature에 다음 메타정보를 관리한다.
  - `feature_asof`: feature 값의 기준 시점
  - `source_snapshot`: 값을 계산한 원천 스냅샷/파일
  - `available_at`: 그 정보가 현실에서 이용 가능해진 시점 (예: 공시지가는 기준일 1/1이 아니라 공시일 4~5월)

## 2026-09-13 — 모델링 데이터 이원화 (Base / Enriched)
- **Base**: 인허가 + 서울 상권분석서비스 + 공시지가 등 장기간 가용 데이터.
  여러 origin을 이용한 temporal validation을 Base에서 수행한다.
- **Enriched**: Base + 소진공 개별 사업체 feature + 시점 정합성이 확보된 온라인 feature.
  주력 origin은 2025-06 (소진공 스냅샷 3개 축적 + 12개월 라벨 창 2025-07~2026-06 확보).
  Enriched는 Base 대비 incremental performance로 평가한다.

## 2026-09-13 — 온라인 변수 사용 범위 확정
- 2026년 현재의 네이버/카카오 등록 여부·검색 순위는 과거 폐업 예측 feature로 사용할 수 없다
  (생존자 편향 + 시간 소급 금지 원칙).
- **작성일이 존재하는 블로그·카페 언급 데이터만** 월별 시계열로 재구성하여 과거 시점 feature로 사용할 수 있다.
- 현재 등록 여부·검색 순위는 서비스의 **현재 진단용 표시 정보**로만 사용한다.
- **온라인 등록의 staggered DiD는 인과분석 계획에서 제거한다.**
  근거: 등록 시점이 어떤 데이터에도 존재하지 않아 처치 시점 식별 불가.

## 2026-09-13 — 생존/모델링 방법 범위
- **주 모델은 12개월 폐업 risk prediction**으로 한다.
- Fine-Gray competing risk는 메인 방법론에서 제외하고 optional 분석으로 강등한다.
  근거: competing event(업종전환·이전)의 시점·유형 식별이 현 데이터로 부실
  (인허가는 이전 식별 불가, 소진공 소멸은 ID 재발급 오염).
- **MDIS DML은 인과분석의 핵심 레버로 유지한다** (Stage 3 "핵심 1개 우선" 원칙의 그 1개).

## 2026-09-18 — 라벨 성숙 컷오프 확정: 1개월

- 실측(build_maturity_diagnostic_table): 완결된 최근 5개월(months_ago 1~5)의
  폐업 신고 건수가 trailing baseline 대비 78~174% 범위에서 정상 등락, flag 0건.
  유일하게 flag된 것은 당월(months_ago=0, 부분월)뿐.
- 결론: 인허가 폐업일자에는 다개월에 걸친 행정 신고 지연이 관측되지 않는다.
  2026-09-13 결정에서 가정한 "3~6개월 후보 범위"는 소진공-인허가 간 갭
  (선행 소멸 갭 중앙값 6개월)을 인허가 자체의 신고 지연으로 오인한 것이었다.
  두 갭은 별개의 현상이다 — 전자는 인허가↔소진공 매칭의 시점 불일치이고,
  후자는 인허가 자체의 행정 처리 속도다.
- **최종 컷오프: 1개월.** 즉 origin 후보 생성 시 데이터 최종 관측월의
  직전월까지는 안정적으로 신뢰 가능(당월만 제외).
- 영향: origin 후보 18→19분기, 최신 origin 2025Q2→2025Q3,
  Long Panel 527,058행/43,733 store_id → 556,161행/44,631 store_id로 확장.
  - **2026-09-22 갱신**: 위 수치는 라벨 창을 origin_start에 걸던 시점의 값이다.
    이후 시간 누수 수정(적격·라벨 창을 origin_end로 통일)과 3구 모집단 기준 변경
    (주소텍스트 → 개방자치단체코드, 2026-09-21 결정)을 반영한 실측은
    **527,934행 / 44,067 store_id / origin 18개(2021Q1~2025Q2) / event 61,149(11.58%)** 이다.
    컷오프 1개월이라는 결정 자체는 그대로 유효하다 — origin_end 기준으로 12개월 창을
    확보해야 하므로 최신 origin이 2025Q3이 아니라 2025Q2가 된다.
- 컷오프 완화 및 개업 연도 코호트별 KM 곡선 검증(2021~2022/2023/2024/2025)은
  라벨 구현 PR을 참조.

## 2026-09-19 — 인허가↔소진공 ER 매칭 규칙 (W1 ER PR 리뷰 반영)
근거: 고정 검증 표본 140건(Tier3 점수 구간 4×20, Tier4 거리 구간 3×20)의 1차 판정과 전체
매칭 재실행. 판정은 파일 정보(상호·PNU·주소·거리·후보 수)만 본 보수적 screening이며 ground truth가
아니다. precision은 yes/(yes+no)로 계산하고 uncertain은 억지로 나누지 않았다.
- **Tier1/Tier2는 현행 유지.** sanity 표본 16건 모두 PNU 일치·후보 유일(cc=1)이며 오매칭 없음.
  exact 문자열 일치라 대형 multi-tenant PNU에서도 후보가 유일할 때만 매칭된다.
- **Tier4 최종 규칙(확정): 반경 30m 이내 + 상호가 exact 또는 containment + name_score 0.75 이상.**
  1자 치환형처럼 fuzzy 유사도만으로는 Tier4 매칭하지 않는다 (`TIER4_REQUIRE_EXACT_OR_CONTAINMENT`).
  score 하한 0.75는 containment라도 아주 짧은 부분문자열(예: '곰' ⊂ '곰집식당')을 막는 역할로 유지한다.
  규칙 변경으로 바뀐 13행을 수작업 검토한 뒤 이 PR의 최종 Tier4 규칙으로 확정했다.
  - 근거: Tier4 표본의 no 5건이 전부 같은 길이 1자 치환(나라헤어↔유나헤어, 서강국시↔서강낚시,
    백조식당↔백세식당, 천명식당↔순천식당)이었고, 치환형 표본 6건 중 yes는 0건이었다.
    exact·containment 54건에서는 no가 0건이다. SequenceMatcher 기준 4자 상호의 1자 치환은 정확히
    0.75라 현행 threshold를 그대로 통과한다 — 같은 필지(Tier3)에서는 오타일 가능성이 높지만 인접
    필지에서는 이름이 비슷한 이웃 점포일 가능성이 높다.
  - 반경 축소는 대안이 되지 못했다: 20m로 줄여도 no 2건이 남고 yes 15건을 잃는다. 따라서 30m를 유지한다.
  - 전체 영향: Tier4 matched 180 → 174. 치환형 9건이 매칭에서 빠졌고(1차 분류: 명백한 오매칭 6, 음역
    변형으로 동일 점포 가능성이 높은 U린헤어↔유린헤어 1, 불명 2), 치환형 경쟁 후보가 사라져 ambiguous 3건이
    exact 매칭으로 해소됐다. Tier1·2·3 결과는 한 건도 바뀌지 않았다.
- **Tier3 threshold 0.75는 잠정값으로 유지한다. 혼잡 PNU 조건은 표시만 하고 매칭에는 적용하지 않는다.**
  - 관찰: 후보 51개 이상 PNU(몰·백화점·복합건물)의 Tier3는 yes 5 / no 5 / uncertain 1(precision 50%),
    50개 이하는 yes 40 / no 2 / uncertain 7(95%). no의 주된 원인은 '타임스퀘어점'·'건대스타시티점' 같은
    공통 접미사가 서로 다른 점포의 유사도를 끌어올린 경우다.
  - 전역 threshold 상향은 채택하지 않는다: 0.80은 no 3건을 지우는 대신 yes 14건을 잃는다.
  - 가장 나은 후보 규칙(`cc>50이면 0.90 요구, 미달 시 ambiguous`)은 precision을 0.865→0.953으로 올리지만
    근거가 cc 51+ 표본 11건뿐이고 모집단 Tier3 매칭 268건(19%)을 바꾼다. 업종 정합률·경쟁 인허가 점유율
    같은 모집단 proxy는 이 실패 유형(주로 인허가 대상 업종이 아닌 상대와의 충돌)을 잡지 못해 보강 근거가
    되지 못했다. 따라서 `crowded_pnu` 표시만 남기고(`TIER3_CROWDED_CC=50`), 강등 규칙은
    `TIER3_CROWDED_MIN_SCORE`로 켤 수 있게 두되 기본은 비활성이다.
  - 후속: cc 51+ Tier3 표본 30~40건을 추가 판정한 뒤 강등 규칙 활성화 여부를 확정한다. 그 전까지
    `crowded_pnu`는 후속 단계에서 별도 sensitivity 집합으로 비교할 수 있도록 flag로만 보존한다.
    ER에서는 자동 제외·강등하지 않는다.
- **ER에는 인허가 영업기간과 소진공 관측구간의 겹침 조건을 넣지 않는다.** ER은 "같은 점포인가"만
  판단한다. 90일 겹침을 hard filter로 넣으면 현재 매칭 27,430건 중 6,105건이 영향을 받고, SEMAS 관측
  시작(2024-12)·종료(2026-06)에 따른 경계 효과가 있다. 90일 미만을 자동으로 미매칭 처리하지 않는다.
  - `W1_MDIS_AND_LABEL.md` §B-3의 "영업기간과 존재구간 1분기 이상 겹침" 조건과는 충돌한다.
    (이전 판에서 이 조건의 위치를 `LABEL_SPEC.md`로 잘못 적었다 — `LABEL_SPEC.md`는 생성 문서라
    겹침 조건을 담고 있지 않다.) **2026-09-22 기준 여전히 미합의**이며, 선택지 A(ER hard filter) /
    B(B-3 QA·provenance)와 영향은 `W1_MDIS_AND_LABEL.md` §B-3 "B-3 겹침 조건 (미합의)" 표에 있다.
    기준 단위도 1분기 vs 90일로 통일되지 않았다.

## 2026-09-19 — 소진공 재발급 provenance와 sj_status 책임 분리
- **`sj_entity_id`를 동일 실체 추적 단위로 유지한다.** 재발급 전후 업소번호를 다른 entity로 다시
  나누지 않으며, 재발급 여부는 `id_reissued` provenance로만 남긴다 (unambiguous 1:1 링크만 병합,
  ambiguous 132건은 병합하지 않음 — 실측 병합 0건).
- **폐업 보조정보는 업소번호 기준이 아니라 `sj_entity_id` 기준 관측구간(`entity_first_snapshot`,
  `entity_last_snapshot`)을 사용한다.** 업소번호 기준 `last_snapshot`을 쓰면 재발급만으로 4,700개
  업소번호가 '소멸'한 것처럼 보인다.
- 소진공 소멸은 주 폐업 라벨이 아니다. 주 라벨은 인허가 `close_date`다 (2026-09-13 결정 유지).
- **`sj_status`는 ER PR에서 만들지 않는다.** B-3가 `match_tier`, `match_confidence`, `close_date`,
  `unmatched_reason`, entity 관측구간을 조합해 파생한다. handoff 컬럼은 `DATA_CATALOG.md` §2-1에 둔다.
- entity 관측구간·`id_reissued`는 7개 스냅샷 union에서 나온 값이다. 라벨 보조정보·ER 검증에만 쓰고
  과거 origin의 prediction feature로 쓰지 않는다 (2026-09-13 시간 누수 규칙).

## 2026-09-19 — 상권 공간배정 규칙 (W1 공간조인 PR)
- **Base spatial assignment는 within-only로 확정한다.** 점포 좌표가 상권 polygon 내부일 때만
  `trdar_cd`를 확정하고, within 미매칭 점포의 상권 feature는 NA로 둔다.
- **nearest 배정은 검증 표본으로 평가한 뒤 base assignment에서 제외했다.**
  근거: 42건 검증 표본과 SHP 재계산에서 d1이 짧은 사례에도 비슷한 거리의 경쟁 polygon이
  존재했다(예: d1 8.23m / d2 16.55m, gap 8.32m). 전체로도 d1 ≤ 20m인 4,526건 중 1,323건
  (29.2%)이 gap < 20m다. 따라서 absolute nearest-distance 단독 threshold는 base assignment
  근거로 충분하지 않다고 판단했다.
- **d1 / d2 / gap / ratio는 QA·sensitivity provenance로만 보존한다.** 상권 배정에 사용하지 않는다.
  `nearest_candidate_high_conf_provisional`(d1 ≤ 20m AND gap ≥ 20m)은 **provisional QA 기준**이며
  통계적으로 확정된 최종 threshold가 아니다. ratio는 보조 지표로 저장만 하고 threshold를 두지 않는다.
- ER의 좌표 반경 30m와 spatial nearest 거리는 **전혀 다른 개념**이며 값을 재사용하지 않는다.
- `coord_missing` / `coord_suspect` 점포는 공간배정에서 제외하되 행은 보존한다(모집단 유지).
- polygon 복수 후보는 임의 선택하지 않고 ambiguous로 보존한다.
- 판정 한계: d1이 짧다는 사실만으로 배정이 옳다고 판정하지 않았다. 도로 건너편·서로 다른 상권
  사이·대형 단지·polygon gap에서는 짧은 거리도 의미상 ambiguous하며, 현 표본으로 nearest 배정의
  의미적 ground truth를 확보했다고 보지 않는다.
- **상권 polygon의 historical geometry consistency는 여전히 미검증**이다(단일 스냅샷만 보유).
  현재 결과는 현재 geometry 기준 assignment이며, 과거 origin feature로 쓸 때의 시간 정합성은
  별도 과제로 남긴다.
  → **2026-09-23 갱신**: 과거 판본은 존재했으나 확보할 수 없어 "검증 불가"로 확정했다.
  처리 규칙은 2026-09-23 "상권분석 available_at 및 polygon backcast 규칙" 항목.
- 상권 영역 SHP의 invalid geometry 6건은 raw를 수정하지 않고 코드에서 `make_valid`로 처리한다.

## 2026-09-19 — 개별공시지가 시점 메타데이터 확정
- 연도별 `feature_asof`(기준일)와 `available_at`(결정·공시일)을 **별개로 확정**한다.
  - 2024: feature_asof 2024-01-01 / available_at 2024-04-30
  - 2025: feature_asof 2025-01-01 / available_at 2025-04-30
  - 2026: feature_asof 2026-01-01 / available_at 2026-04-30
- 의미: `feature_asof`는 가격의 기준일, `available_at`은 당시 예측자가 그 값을 실제로 알 수 있게 된
  날짜다. **prediction origin이 해당 연도 available_at 이전이면 그 연도 land_price 사용은 leakage다.**
  예) origin 2026-03-31 → `land_price_2026` 사용 금지(이전 연도 값 사용) / origin ≥ 2026-04-30 → 사용 가능.
- 근거는 서울시 「연도별 개별공시지가 결정·공시」 보도자료다. 연도별 출처 URL은
  `docs/DATA_CATALOG.md`의 "available_at 출처" 표와 코드의
  `src/data/landprice.py: AVAILABLE_AT_SOURCES`에 기록한다.
  (2024년분은 서울시 원 페이지 직접 접근이 되지 않아 국회도서관 지방의정포털에 보존된
  서울특별시청 원 보도자료를 사용한다.)
- origin별 feature selection 로직은 W1 공간조인 PR 범위 밖이며, W2가 위 메타데이터로 판단한다.
- **2026년 공시지가 0원 필지는 값을 보존한다.** NA 변환·임의 대체를 하지 않으며, 처리 규칙은
  별도 결정 대상으로 남긴다. (실측: 서울 raw 336건, 그중 3구 점포에 결합된 것 10건)

## 2026-09-20 — MDIS 업종 모집단 결정 (Issue #13 Part A)

Issue #13에서 제기된 문제: Stage B(전자상거래 매출비중 연속형 분석, 857건)의 산업중분류
분포가 47(소매업) 638건(74.4%) / 56(음식점·주점업) 182건(21.2%) / 96(개인서비스업)
37건(4.3%)으로 불균형하여, 96 단독으로는 개별 CATE 추정이 불안정하다는 점이 지적됨.

선택지:
- (a) 주 추정을 56(+96)으로 한정, 47 포함 결과는 민감도 분석으로 병기
- (b) 3개 업종(47/56/96) 모집단 유지, 업종별 CATE 보고, 처방에는 해당 업종 추정치만 사용

**결정: (b) 채택.**

근거:
- PR #8(A-4 검증)에서 이미 동일 문제를 확인하고 "96은 ATE에는 포함하되 개별 CATE 추정
  대상에서는 제외" 권고를 코드북에 남긴 바 있음(선반영).
- (a)처럼 47(소매업)을 제외하면 처치군의 74.4%를 차지하는 핵심 업종을 분석 대상에서
  빼게 되어, 우리 서비스 대상 업종(소매 포함)과 불일치.
- 96의 표본 부족은 모집단 자체를 좁혀서 해결할 문제가 아니라, CATE 보고 범위를
  조정해서(개별 추정 제외, 전체 평균엔 포함) 해결.

영향:
- **`INDUSTRY_CODES` 불변**(47/56/96 그대로 유지). 코드 변경 없음.
- W3에서 CATE를 업종별로 보고할 때 96은 신뢰구간이 넓다는 점을 명시하고, 처방(Prescribe)
  엔진이 96 업종 점포에 개별 CATE 기반 추천을 주지 않도록 설계에 반영 필요(W3 작업 시 참고).

관련: Issue #13, PR #8, `docs/MDIS_CODEBOOK.md` "A-4 검증 결과" 절.

참고: `docs/MDIS_CODEBOOK.md`의 "Stage B 업종 분포 및 제약" 절은 현재
`feature/mdis-validation` 브랜치에만 존재하며(#8, 미머지), 그 브랜치가 main으로
rebase될 때 이 결정을 참조하는 문구를 함께 추가한다 (M5 SMD 재계산과 같은 시점에 처리).

## 2026-09-20 — 소진공 202503 좌표 처리 (Issue #14)
근거: 재다운로드 원본 재검증 (실측은 `DATA_CATALOG.md` §2-2).
- **202503 좌표는 배포본 자체의 이상으로 판단한다.** 재다운로드본과 로컬 파일이 동일하고, 파싱·열 밀림이
  없으며, 전 행(서울 100%, 전국 17개 시도 동일 패턴)의 좌표만 약 150km 밀려 있고 식별·주소 정보는 정상이다.
  배포처에서의 발생 원인은 미확정으로 둔다.
- **현재 처리 방침을 유지한다.**
  - 202503 행 자체는 삭제하지 않고 스냅샷 정보를 유지한다. 식별·주소·업종이 정상이므로 entity resolution
    (업소번호 연속성·재발급 탐지)에는 계속 사용한다.
  - 202503 좌표는 `coord_suspect`로 표시하고 좌표 기반 매칭(Tier4)과 공간분석에 사용하지 않는다.
    좌표가 필요한 경우 같은 entity의 다른 스냅샷(202412·202506 등) 좌표를 쓴다.
- **affine 역변환으로 202503 좌표를 복원하지 않는다.** 수치상 잔차는 약 1m 이하지만 원천 정정이 아닌 추정값이며,
  공통 업소번호는 다른 스냅샷 좌표로 대체할 수 있다. 복원이 필요해지면 별도로 결정한다.
- 서울 외 지역으로 분석을 넓히면 서울 범위 검사만으로는 이 이상을 막을 수 없다(밀린 좌표가 한반도 안에 떨어짐).
  그때는 시도별 범위 검사 또는 스냅샷 간 좌표 일관성 검사를 추가한다.

## 2026-09-21 — 3구 모집단 정의 기준: `개방자치단체코드`가 정본, 주소텍스트는 QA

배경: 문서(`DATA_CATALOG.md`)는 "주소텍스트로 필터하고 `개방자치단체코드` 단독 필터는
금지"라고 기록했는데, 표준화 코드(`src/data/io_license.py`)는 `gov_code.isin(TARGET_GU_CODES)`
방식의 코드 기준 필터를 쓰고 있어 같은 모집단을 두 가지로 정의하고 있었다.

**결정: 모집단 포함/제외는 `개방자치단체코드`로 결정한다. 주소텍스트는 검증(QA) 용도로만 쓴다.**

근거:
- `개방자치단체코드`는 관할 자치단체를 나타내는 **구조화된 행정 필드**라 값 집합이 닫혀 있고,
  원본이 재배포돼도 같은 기준이 그대로 적용된다 — 모집단 정의에 필요한 재현성·일관성이 높다.
- 주소텍스트는 지번/도로명 표기 차이, 오탈자, 행정구역 표기 흔들림에 영향을 받는다.
  부분 문자열 매칭 규칙을 바꾸면 모집단 크기가 따라 움직이므로 정의 기준으로 불안정하다.
- 두 필드의 불일치 20건은 **어느 쪽이 틀렸는지 원본만으로 판정할 수 없다.** 판정 불가한
  차이 때문에 모집단 정의를 불안정한 쪽에 맡기지 않는다.
- 불일치 행은 **삭제하지 않고 `gu_mismatch` 등 플래그로 보존**한다(`DECISIONS.md` 2026-09-13
  "master dataset 모집단"의 삭제 금지 원칙과 동일). 민감도 분석에서 이 플래그로 영향을 확인한다.

실측 근거(2026-09-21 재확인):

| 기준 | 행수 |
|---|---|
| `개방자치단체코드` (정본) | 110,347 |
| 주소텍스트 (QA 대조) | 110,355 |
| 교집합 | 110,341 (주소텍스트에만 14 / 코드에만 6) |

영향:
- `src/data/io_license.py`의 현행 코드 기준 필터를 **변경하지 않는다.** 표준화 산출물
  `licenses_3gu.parquet` 110,347행이 정본 모집단이다.
- `DATA_CATALOG.md` §1의 "코드 단독 필터 금지" 문구를 이 결정에 맞춰 수정했다.
- 라벨 파이프라인(W1 B-2, PR #6)도 이 기준에 맞췄다(2026-09-22): 3구 필터를
  `개방자치단체코드` 기준으로 바꾸고 `store_id`를 `{GR|SR|BT}_{관리번호}`로 통일했다.
  주소텍스트는 `labels.diagnose_district_filter()`의 QA 대조로만 남는다.
  기준 변경으로 라벨 패널은 527,978 → 527,934행(-44), 44,070 → 44,067 store_id(-3),
  event 61,159 → 61,149(-10)로 바뀌었다. 주소텍스트에만 잡히던 14개 점포(패널 기여 68행)가
  빠지고, 코드에만 잡히던 6개 점포(24행)가 들어온 결과다.

## 2026-09-22 — 개별공시지가 0원 필지 처리 (Issue #9)
근거: 2026 raw 전수 조사 (실측은 `DATA_CATALOG.md` §4-1).
- **0원은 유효한 지가가 아니라 값이 비어 있는 상태로 본다.** 0원은 2026년에만 나오고(2024·2025는 0건),
  339건 중 311건이 직전 연도에 정상 지가를 가졌으며, 2026 신규 PNU 1,782건 중 0원은 28건(1.6%)뿐이다.
  "미평가 신규 필지"로 설명되지 않는다.
- **raw 값은 그대로 보존한다.** `land_price_{year}`는 0을 유지하고, NA 변환·임의 대체를 하지 않는다.
- **feature로는 0을 제외한 `land_price_{year}_valid`를 사용한다.** 0인 행은 해당 연도만 NA가 되고
  다른 연도 값은 살아 있다. 어느 연도든 0이 있으면 `land_price_zero_flag=True`로 표시한다.
- `land_price_match`의 정의는 바꾸지 않는다 (PNU 결합 성공 여부이며, 0도 결합 성공이다).
- **0을 이전 연도 값으로 대체(carry-forward)하지 않는다.** 대체가 필요하면 origin별 available_at
  규칙과 함께 W2 feature 설계에서 별도로 결정하고, 대체 여부를 flag로 남긴다.
- **미확정**: 0이 된 행정적 사유는 데이터만으로 특정할 수 없다. 배포처 확인 대상으로 남긴다.

## 2026-09-23 — 상권분석 available_at 및 polygon backcast 규칙
근거: 공식 출처(golmok 서비스 소개·데이터 출처 페이지, 열린데이터광장 데이터셋 페이지)와 그 Wayback 보존본,
로컬 raw 전수 실측. 실측·출처 상세는 `DATA_CATALOG.md` §3, §3-0, §3-A, §3-B, §3-1.

**공표 시점 (판정: WARNING, BLOCKER 아님)**
- 공식 일정은 "매 분기 2개월 후 업데이트 예정"(Q1→5월 말, Q2→8월 말, Q3→11월 말, Q4→다음 해 2월 말)이다.
- 기준시점과 함께 확인된 lag는 2021Q4 62일 / 2022Q4 59일 / 2024Q2 54일 / 2024Q4 51일 / 2025Q2 57일 /
  2026Q2 49일이다. 분기 대응을 공식 일정으로 추정한 사례까지 포함하면 최대 83일(2025Q4)이다.
- WARNING 사유: 일정이 "예정"이고, 추정 사례 기준 Q4→다음 해 Q1 origin의 여유가 7일 수준이며,
  2021~2023 대부분 분기의 실제 공표일은 미확정이다.

**규칙**
- **origin 분기 T 자체의 상권분석 값은 사용 금지.** T 종료 후 약 2개월 뒤 공표되므로 `origin_end` 시점에 없다.
- **상권 feature의 기본 분기는 T-1**이다. 관측된 최대 lag(83일)가 T-1 종료~origin_end 간격(90~92일)보다 짧다.
- **T-2 sensitivity를 수행한다.**
- **`trdar_available_at`에는 실제로 확인된 published_at만 기록한다.** 확인되지 않은 분기에 임의 날짜를
  만들지 않는다(NA + basis 표시). 추정 매핑 사례도 published_at으로 쓰지 않는다.
- 행이 없는 상권×분기는 0이 아니라 NA로 둔다. 점포 컬럼은 `점포_수`→`일반_점포_수`,
  `유사_업종_점포_수`→`전체_점포_수`로 맞춘다(`DATA_CATALOG.md` §3-0).
- 상주·직장·집객처럼 분기마다 갱신되지 않는 계열은 분기 코드가 아니라 값이 실제로 바뀐 분기를
  `trdar_value_asof`로 남긴다.

**polygon historical consistency (판정: WARNING, BLOCKER 아님)**
- 과거 polygon 판본(2019-11, 2022-04 구/신, 2023-08)은 존재했으나 현재 공식 경로로 확보할 수 없고,
  API에도 geometry 이력이 없다. **historical geometry consistency는 검증 불가**로 확정한다.
  IoU·centroid shift·area difference 비교는 수행하지 않으며, 과거 경계를 임의 복원·추정하지 않는다.
- 현재 geometry snapshot = **2023-10-23**(DBF 헤더 2023-10-20). 이 경계를 과거 origin에 적용하는 것을
  **polygon backcast**로 부르고 `trdar_geometry_backcast_flag`로 표시한다.
- 2021~2022 분기 CSV는 2023-10-30 재발행본이며 현재 경계 기준 재계산값일 가능성이 높다.
  과거 origin 시점에 실제 공개된 값과 같다고 보지 않으며, 보고서 한계·재현성 위험으로 기록한다.

**leakage와 measurement error의 구분**
- T-1 규칙을 지키면 feature 값이 가리키는 기간 자체는 origin 이전이다. 따라서 점포의 결과(폐업)를
  직접 쓰는 일반적인 label leakage와는 다르다.
- 현재 경계를 과거 origin에 소급하는 문제는 **주로 spatial misclassification / measurement error**다
  (당시와 다른 면적으로 집계, 당시와 다른 상권 배정, 사후 재계산된 판본).
- 단, 현재 경계는 **2023-06 상가 DB**를 바탕으로 정해졌다(골목·발달·전통시장·관광특구 기준시점 2023년 06월,
  2022년 표준단위구역 기반). 따라서 2021~2023 origin에서 `trdar_cd` 비결측 여부나 polygon membership에는
  "2023년까지 점포 밀도가 유지된 지역"이라는 **약한 미래정보 경로가 존재할 가능성**이 있다.
- 그러므로 **polygon membership 관련 변수(within 여부·`trdar_cd` 결측 지시자·상권 구분·polygon 면적)는
  Base의 핵심 predictor로 의존하지 않고 sensitivity/provenance 관점에서 다룬다.** 상권 수치 feature(T-1)는
  Base에 쓰되 backcast flag를 provenance로 남긴다. 구체 sensitivity 설계(예: geometry snapshot 이후 origin만
  평가)는 W2에서 정한다.

**W2-0 provenance 컬럼 (이름만 확정, 구현은 W2-0)**

| 컬럼 | 의미 |
|---|---|
| `trdar_quarter_used` | 사용한 상권분석 분기 코드 (기본 T-1) |
| `trdar_source_snapshot` | 원천 파일명 + sha256 (+ 수령일) |
| `trdar_available_at` | 확인된 published_at. 미확인이면 NA |
| `trdar_available_at_basis` | `archive_confirmed`(날짜 기록) / `archive_inferred`(날짜 NA — 추정 날짜는 기록하지 않음) / `unverified` |
| `trdar_value_asof` | 값의 실제 기준 분기 (갱신 정체 계열은 마지막 변화 분기) |
| `trdar_geometry_snapshot` | `2023-10-23` |
| `trdar_geometry_backcast_flag` | `origin_end < trdar_geometry_snapshot` |

불변식: `trdar_quarter_used < origin`, `trdar_available_at`이 존재하면 `trdar_available_at <= origin_end`.
provenance 컬럼은 predictor로 쓰지 않는다.

## 2026-09-23 — W2-0 master_base 구성 결정
근거: W2-0 계획 검토(labels_base·spatial_joined·ER 산출물 실측). 구현은 `src/data/master.py`,
컬럼 역할의 단일 출처는 `src/data/master_schema.py: COLUMN_ROLES`.

- **(I-2) ER 결과는 predictor가 아니라 provenance/metadata 전용이다.** `er_matched`(원 `matched`),
  `er_ambiguous`, `match_tier`, `match_confidence`, `crowded_pnu`, `sj_entity_id`, `unmatched_reason`.
  - 근거: ER은 소진공 7개 스냅샷(2024-12~2026-06) union으로 계산된다. 모든 Base origin
    (2021Q1~2025Q2)에서 origin 이후 정보다. 실측 event_12m 비율 — 2021Q1 매칭 1.2% vs 미매칭 21.9%,
    2025Q2 9.9% vs 15.6%. 점포의 생존이 매칭 여부를 만든 결과이므로 predictor로 쓰면 누수다.
  - 2026-09-13 "master dataset 모집단"의 "매칭 여부와 match confidence를 별도 feature/metadata
    컬럼으로 기록"은 **metadata로 기록**한다는 뜻으로 확정한다. 매칭 실패 점포를 삭제하지 않는 원칙은 그대로다.
  - `W1_FREEZE.md` §8 predictor 금지 목록에 ER 컬럼을 추가했다.
- **(I-3) 공시지가는 strict as-of로 붙인다.** 행마다 `available_at(y) <= origin_end`인 최대 연도 y의
  `land_price_{y}_valid` 하나만 `land_price`로 쓴다. 소급(backcast)·carry-forward는 하지 않는다.
  - 결과: origin 2024Q2~2025Q1 → 2024년, 2025Q2 → 2025년, **2021Q1~2024Q1(13개 origin, 381,406행,
    72.2%)은 구조적 NA**로 둔다. 2026년 값은 어느 Base origin에서도 쓸 수 없다(0원 이슈 영향 없음).
  - 구조적 결측이 origin 시기와 겹친다는 점(temporal validation 분포 차이)은 모델 단계에서
    포함/제외 비교로 다룬다.
- **(I-1) 업종 단위 상권 feature(점포·추정매출)는 보류한다.** 원천 키가
  `(분기, 상권, 서비스_업종)`이라 biz_type ↔ 서비스업종 매핑이 필요하며, 매핑 확정 후 별도 커밋으로 추가한다.
  W2-0 Base는 상권 단위 계열(길단위인구·상권변화지표·상주·직장·집객)만 T-1로 붙인다.
- 온라인 존재감은 master_base에 넣지 않는다. Enriched는 `(store_id, origin)` 유일 테이블을 m:1로
  LEFT JOIN하는 인터페이스(`master.attach_enriched_table`)로 확장한다.

## 2026-09-23 — W2-0 I-1 최종: 업종 단위 상권 feature 매핑·집계 규칙
근거: 업종 코드 체계 실측(인허가 업태 47종 / 상권분석 서비스업종 100종 / 소진공 cat3 247종 — 공통 코드 없음)과
row 부재 패턴 실측. 상세 수치는 `outputs/master/qa_report.md` "업종 단위 상권 feature" 절. 구현은
`src/data/trdar_features.py: BIZ_CODE_MAP / aggregate_biz`, `master.attach_trdar_biz_features`.
위 "W2-0 master_base 구성 결정"의 (I-1) 보류를 이 항목으로 대체한다.

- **매핑 key는 `biz_type`(인허가 종류)이다.** `업태구분명`·`위생업태명`·소진공 cat3는 origin 시점 값임을
  검증할 수 없으므로 predictor 결합 key로 쓰지 않는다.
  - 일반음식점 = CS100001·002·003·004·007·008·009 / 휴게음식점 = CS100005·006·010 / 미용업 = CS200028·029·030
  - CS100006(패스트푸드점)·CS100010(커피-음료)은 휴게음식점에만 둔다. **중복 배정하지 않는다.**
  - 휴게음식점 편의점 1,158개 점포 등에 예외를 두지 않는다. broad biz_type 매핑의 한계로 문서화한다.
- **점포 원천: observed partial.** row가 있는 mapped code만 합한다. 점포 원천에서 mapped code의 row 부재는
  시계열 전이, 명시적 0 row, 매출 원천과의 교차검증 및 연도별 패턴상 점포 0을 의미하는 것으로 해석할 강한 실증
  근거가 있다. 다만 원천 공식 명세로 확인된 규칙은 아니므로 raw row를 임의 생성하거나 0으로 imputation하지 않고
  observed-row 집계와 coverage metadata(`trdar_biz_store_n_codes_observed/_expected/_code_coverage/_is_partial`)를
  유지한다. (분석적 해석 = structural zero 근거 있음 / 물리적 처리 = missing row를 0 row로 만들지 않음)
  - 실측(서울 전체): 점포>0 이후 사라진 전이 2,041건 중 2,026건이 명시적 0 row를 거쳤다(예외 15건).
    점포 row가 없는데 매출 row가 있는 셀 0건. 3구 상권 한정으로는 예외 0건.
- **매출 원천: observed partial + 점포 기준 coverage.** 매출 row 부재는 0이 아니다 — 매출 0원 row가 원천에 없고,
  점포 1~2개 코드는 매출 row가 100% 없다(소수 점포 매출 비공개/억제로 판단). 매출 합계는 항상 "매출이 공개된
  mapped code의 합계"(biz_type 전체의 하한/부분관측치)이며, `trdar_biz_sales_store_coverage`
  (매출 row가 있는 코드의 점포 수 / mapped code 전체 점포 수, 같은 T-1 점포 원천, 분모 0이면 NA)로 대표성을 남긴다.
- **strict / coverage threshold로 행을 지우거나 NA 처리하지 않는다.** coverage는 provenance/quality 정보로 보존한다.
- **점포당 매출** `trdar_biz_sales_per_store_observed`: 분자·분모 모두 매출 row가 있는 mapped code 집합.
  분모 0 또는 code set 불일치면 NA (inf·0 대체 금지).
- 매출건수 합계는 매출금액과 Spearman 0.894로 중복이 커 추가하지 않았다.
- 개업·폐업률은 원천 정의(건수 / 분기 말 전체 점포 수 × 100)로 합계 재계산한다. 원천 `폐업_률`도 100% 초과가
  있으므로(806행, 최대 500) 자르지 않는다.

## 2026-09-25 — W2 경쟁지표 개발과 모델링 병렬 진행
근거: PR #31(W2-0 master_base) 리뷰 후속 논의. 위 2026-09-23 W2-0 결정들은 그대로 유효하다.

- **경쟁지표 6종은 별도 모듈·별도 PR로 개발한다.** master_base(PR #31)에는 넣지 않는다.
  - 인허가 기반 feature로 설계한다. origin_end 시점에 이용 가능한 인허가 정보만 사용한다(시간 누수 방지 규칙 동일).
  - Base 결합 전 검증을 거친다: `(store_id, origin)` m:1 결합, 행수·label·event 비율 불변, temporal leakage 0.
  - 구체 지표 정의는 해당 PR에서 확정하고 이 문서에 기록한다.
- **W2-2 baseline은 현재 master_base의 predictor 19개로 먼저 진행한다.** 경쟁지표 완성을 기다리지 않는다.
  - 경쟁지표 추가 효과는 baseline과 **동일한 split·평가 조건**에서 비교한다(incremental 평가).
- **W2-2에서 정할 것**
  - predictor registry: 모델 입력은 `master_schema.COLUMN_ROLES`의 role == predictor 컬럼을 기준으로 관리한다.
  - 상권 feature ablation: 상권 단위·업종 단위 feature 포함/제외 비교 (`gu`, 공시지가, 업종 품질 메타 ablation 메모는
    `docs/MASTER_SPEC.md` 참조).
  - 2023Q4 이후 민감도 분석: 상권 polygon 스냅샷(2023-10-23) 이후 origin(backcast flag False)만으로 평가한다
    (2026-09-23 polygon backcast 결정의 후속).
  - 범주형 처리 기준: `biz_type`, `gu`, `trdar_change_index` 등 범주형 predictor의 인코딩 방식.
- **온라인 존재감은 Base에서 제외한다.** Enriched에서 별도로 검증한 뒤 `(store_id, origin)` 단위로 결합한다
  (2026-09-13 온라인 변수 사용 범위, 2026-09-23 W2-0 결정 유지).

## 2026-09-25 — W2-2 Stage 1 탐지 모형 검증·보정·구간·등급 규칙
근거: `labels_base`(feature 4개) 실험과 합성 master 규모 검증. 구현은 `src/models/`, 실행은
`python -m src.models.train_detect`.

- **입력은 `master_schema.predictor_columns()`뿐이다** (`src/models/features.py`). "메타를 뺀 나머지 전부"
  방식은 ER 매칭 결과·상권 배정 컬럼을 입력에 섞어 누수를 만든다. 결측 지시자(`*_isna`)는 만들지 않는다 —
  상권 feature 결측은 polygon 소속 여부, 공시지가 결측은 origin 시기를 그대로 드러내기 때문이다.
  NaN은 HistGradientBoosting이 직접 처리한다. 학습 구간에서 값이 전부 NA인 컬럼은 그 fold에서만 뺀다.
- **시간 분할 + 라벨 성숙 embargo 4분기.** origin t 예측은 t−5 이하로 학습한다(t−1~t−4 비움). 수식상
  경계(s ≤ t−4)보다 한 분기 보수적이며, 폐업 신고 지연(성숙 컷오프 1개월)을 흡수한다. embargo 없는
  분할은 성능을 부풀린다(feature 4개 실험에서 AP 상대 +23.6%). random split·점포 홀드아웃은 대조군이다.
- **성능·민감도·보정은 rolling OOF 예측으로 한다** (학습 origin ≥ 4개가 되는 2023Q1부터 매 origin).
  상권 polygon 스냅샷(2023-10-23) 이후 origin(2023Q4~)만의 성능을 따로 보고한다.
- **민감도 feature set**: base / no_trdar / no_land_price / no_gu / license_only. T-2 상권은
  `attach_trdar_features(lag_quarters=2)`로 만든 master를 `--master`로 넣어 같은 평가를 돌린다.
- **보정(isotonic)은 첫 검증 origin 기준 학습 가능 구간의 OOF 예측으로 적합**하고, 검증 구간 ECE가
  개선될 때만 적용한다. raw·calibrated 지표를 둘 다 남긴다 (origin별 base rate 변동이 커서 보정이
  오히려 나빠질 수 있다 — 실측 상대 27% 변동).
- **불확실성 구간 = 점포 단위 부트스트랩 재학습의 5~95 백분위** (기본 B=20). Venn-ABERS는 보정 표본이
  크면 폭이 사실상 0이라(평균 0.0013, 구간 커버 0/10) 화면에 쓰지 않는다. 화면 문구는 "예측이 얼마나
  흔들리는가"의 구간이며 "폐업 확률의 범위"가 아니다. 점추정이 구간 밖에 놓이면 구간을 넓혀 포함시킨다.
- **band = 절대 확률 컷오프.** high = {p ≥ c} 집단의 실측 위험이 보정 구간 평균의 2배 이상이 되는 가장
  낮은 컷오프, mid = 예측 확률이 평균의 1.2배 이상. (mid를 구간 평균 lift로 찾으면 평균 미만 점포가 섞여
  컷오프가 base rate 아래로 내려가 mid가 61%가 됐다 — 실데이터 첫 실행.) 백분위 컷오프는 isotonic 동점 때문에 의도한 비율을 만들지 못하고(q90 → 15.1%),
  "상위 N%" 동어반복이라 쓰지 않는다. 상대 위치는 `percentile`(같은 origin·자치구·업종 내)이 맡는다.
- **공시지가(`land_price`)는 시간 분할 검증에서 학습에 한 번도 들어가지 못한다.** 값이 있는 origin이
  2024Q2~2025Q2뿐이고, 검증 가능한 마지막 origin(2025Q2)의 학습 구간이 2024Q1까지이기 때문이다.
  검증되지 않은 feature를 서빙 모형에만 넣지 않도록, **서빙 모형은 검증과 같은 feature set으로 학습**하고
  land_price는 라벨이 쌓여 검증 가능해질 때까지 Base 서빙 입력에서 제외한다.

## 2026-09-25 — 온라인 feature 사용 판정(#26)과 Enriched 탐지 모형 채택
근거: `src/data/online_features.py`, `src/analysis/online_deletion_bias.py`,
`python -m src.models.train_detect --online ... --primary enriched` 실데이터 결과.

- **(#26) 18개 origin 모두 온라인 feature를 쓴다 (불통과 0건).** 오래된 origin(2021Q1~2022Q3)의
  보유율 격차(비폐업 − 폐업)는 기준선(2022Q4~2025Q2 평균 0.0597)보다 오히려 작다(전 origin에서 95% 구간
  상한 < 0). 최근 4개 origin만 기준선으로 둔 보조 판정과 업종별 판정도 불통과가 없다.
  격차는 분기당 +0.49%p씩 커지지만, enriched의 성능 향상은 같은 기간 분기당 −0.0007 AUC로 오히려 줄었다
  (2023년 +0.022 → 최근 +0.017). 수집 시점(2026-09) 비대칭이 성능을 부풀렸다면 반대 방향이어야 하므로
  향상은 실제 신호로 본다.
- **주 탐지 모형은 enriched(base 19개 + 온라인 6개)다.** rolling OOF 10개 origin 모두에서 base보다 높다
  (평균 AUC 0.6029 → 0.6219, AP 0.1764 → 0.1872). 온라인 묶음의 permutation 기여(0.029)는 상권 묶음(0.0055)의
  5배를 넘고, 대부분 `online_blog_months_since_last`에서 나온다. 보정은 적용하지 않는다(raw ECE 0.0111).
- **절단 점포 결측은 `na`(미관측 구간 NA)를 유지한다.** `first_date_truncated`는 수집 시점의 누적 게시물 수로
  정해지는 미래 정보이므로 **predictor로 쓰지 않고 결측 처리에만 쓴다** — 결측 규칙은 이슈 #25 합의(QA 오류·미수집 → 전 월 NA, 절단 점포의 미관측 구간 → NA, 그 외 빈 달 → 0)를 따른다.
  결측 점포의 폐업률(6~9%)이 관측 점포(11~13%)보다 낮아 결측이 생존 신호가 될 수 있다. 관측된 글만 센
  하한값(`lower_bound`)으로 바꾸면 AUC가 0.0015 낮다 — 이 결측 경로의 기여 상한으로 보고 한계로 기록한다.
- 등급(enriched): mid 0.1493 / high 0.2142 → low 78.1%(lift 0.82) · mid 14.7%(1.45) · high 7.2%(2.06).

## 2026-09-25 — W2-3 Stage 2 진단: 요인 매핑·기여 계산·peer 비교·A/P/N (초안, 팀 확인 대상)
근거: `src/models/diagnose.py`, 합성 데이터 검증. 실행 `python -m src.models.diagnose --online ... --primary enriched`.

- **기여 계산 = 요인 단위 정확 Shapley (interventional, 학습 구간 배경 표본 16개).** 요인(feature 묶음) 수가
  10개 이하라 2^F 조합을 전부 계산한다. 확률 척도이며 Σ기여 + base = 예측 확률이 정확히 성립한다.
  SHAP 라이브러리(TreeExplainer)는 HistGradientBoosting 범주형 분기를 해석하지 못해 쓰지 않는다
  (shap 0.51 실측: 기여 합 오차 최대 7.8 log-odds). 기여는 예측 분해이지 인과효과가 아니다.
- **진단 모형 = 해당 origin의 rolling 학습 규칙(t−5 이하)으로 학습한 모형**이라 risk_scores 예측과 같다.
  학습 구간에 값이 없는 요인(현재 공시지가)은 진단에서 빠진다.
- **요인 매핑 (ANALYSIS_PLAN §2 4개 유형)**

| 요인 | 유형 | A/P/N | feature |
|---|---|---|---|
| 업력 | 사업체 구조 | external | age_months |
| 업종·점포 규모 | 사업체 구조 | external | biz_type, area, has_coord |
| 자치구 | 입지·수요 | external | gu |
| 상권 유동·배후 인구 | 입지·수요 | external | trdar_flow_pop, trdar_resident_pop, trdar_worker_pop, trdar_facility_cnt |
| 상권 변화·영업 지속 | 입지·수요 | external | trdar_change_index, trdar_oper_months_avg, trdar_close_months_avg |
| 온라인 언급(블로그) | 입지·수요 | owner | online_blog_* 6개 |
| 동종 업종 경쟁·개폐업 | 경쟁 | external | trdar_biz_store_cnt/franchise_cnt/open_rate/close_rate_observed |
| 동종 업종 매출 수준 | 경쟁 | external | trdar_biz_sales_amt/sales_per_store_observed |
| 임대료 수준(공시지가) | 비용 | policy | land_price (학습 구간에 값이 없어 현재 진단에서 제외 → 비용 유형은 `비용_available=False`, 화면에 0이 아니라 "판단 불가"로 표시) |

  모든 predictor는 정확히 한 요인에 속해야 하며, 새 predictor(경쟁지표 등)가 추가되면 매핑하지 않으면 실행이 멈춘다.
- **peer 비교 = 같은 origin·업종·자치구·업력대(1년 미만/1~3/3~5/5~10/10년 이상) 안에서 요인 기여의 백분위.**
  표본 30개 미만이면 자치구 → 업력대 순으로 조건을 푼다. "유사 상권" 대신 자치구를 쓰는 이유는 상권 유형
  (`trdar_type`)이 polygon membership 계열이라 predictor·비교 기준에서 제외했기 때문이다(2026-09-23).
- **진단문**: "○○이 예측 위험도를 약 N%p 높이는(낮추는) 쪽으로 기여했습니다." + peer 백분위가 70 이상일 때만
  "같은 업종·자치구·업력대 점포 중 상위 M% 수준입니다."를 붙인다. 요인별 판단 근거 값(`values`)을 함께 내고,
  온라인 요인은 6개 신호 중 기여가 가장 큰 것을 같은 방식(Shapley)으로 골라 관측값으로 서술해
  어떤 신호가 작용했는지 드러낸다("주된 근거: …"). "때문에", "원인", "고치면" 같은 인과 표현은 쓰지 않는다.
- **온라인 요인 표시 보류**: 온라인 요인이 위험을 올리는데 주된 근거가 언급 있음·많음·최근 언급·증가이면
  진단문에서 표시를 보류한다(`display=false`). 사업자가 할 수 있는 일로 번역되지 않고, 이름 오탐(#28)이나
  유행 상권 인기 점포 효과일 수 있기 때문이다. 실측: 최고 위험 3개 점포(연남동, 2020~23 개업)는 12개월 언급
  101~131건이 주된 근거였고 그중 "고시고시"(API total 2,900)는 오탐 가능성이 높다. 기여값은 바꾸지 않는다.
- **팀 확인 필요**: (1) 온라인 언급을 입지·수요에 둘지 별도 유형으로 둘지 (2) 현재 owner 요인은 온라인 언급뿐이고
  policy 요인(공시지가)은 기여가 0이라, W2-7 정책 매칭이 연결할 요인이 사실상 온라인 하나다.

## 2026-09-25 — W2-2/W2-3 서빙(현재 시점 점포) 규칙
구현: `src/models/serve.py`. 실행: `python -m src.models.serve --score <예측용 master> --primary enriched ...`

- **서빙 모형의 학습 구간은 검증과 같은 embargo 규칙을 따른다**: score origin s → 라벨 origin ≤ s−5분기.
  검증 구간 마지막 origin을 라벨 없이 넣으면 `train_detect` risk_scores의 확률·등급·백분위가 정확히 같다(테스트).
- **보정기·등급 컷오프는 `train_detect` 실행 결과를 그대로 쓴다** (`run_meta.json`, `calibrator.pkl`,
  `band_cutoffs.csv`). 서빙에서 다시 정하지 않는다. feature set이 다르면 멈춘다.
- **검증 구간의 학습에 한 번도 값이 없던 feature는 서빙에서도 뺀다** (검증 마지막 origin의 학습 구간 기준).
  score origin이 뒤로 가면 학습 구간에 land_price 값이 생기지만, 검증되지 않았으므로 넣지 않는다
  (2026-09-25 W2-2 항목의 "서빙 모형은 검증과 같은 feature set" 규칙의 구현).
- **진단은 `diagnose.explain`을 공유한다** — 요인 매핑·Shapley·peer 비교·온라인 표시 보류 규칙이 W2-3과 같다.
  요인 기여도는 보정 전 확률 척도에서 합산된다. 보정이 적용된 실행이면 화면 확률과 합이 달라지며
  `serve_meta.json`의 `diagnosis_scale`에 기록한다 (현재 실데이터 실행은 보정 미적용이라 같다).
- 출력 `reports.jsonl`은 W2-5 결과 스키마의 risk·factors 블록(점포당 1줄)이다. 처방(W2-4)·정책(W2-7) 블록은 붙이지 않는다.
- **데이터 없음 표시 보류 (W2-3·서빙 공통).** 요인에 속한 feature가 그 점포에서 전부 결측이면(상권 경계 밖
  점포의 상권 4개 요인, 온라인 관측 불가) `data_missing=True, display=False`로 두고 진단문은
  "이 점포는 ○○ 데이터가 없어(상권 경계 밖) 이 요인은 진단하지 않습니다."로 바꾼다. 이때 기여는 값이 아니라
  결측 자체(= 상권 밖 위치라는 정보)에서 나오므로 "동종 업종 경쟁이 위험을 낮췄다"는 문장은 사실과 다르다.
  기여값과 가법성은 그대로 둔다 (실데이터 2025Q2: 상권 밖 6,563점포, 요인의 38%가 |기여| ≥ 0.5%p, 최대 2.9%p).
