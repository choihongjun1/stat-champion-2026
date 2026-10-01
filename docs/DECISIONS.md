# Decision Log

분석·서비스 방향에 영향을 주는 확정 사항을 기록한다. 변경 시 기존 결정을 지우기보다 날짜와 변경 이유를 추가한다.

> **2026-10-01 — W3 결정 동결:** W3 실행 규칙(S1~S13, T1~T8, A1~A5)은 `docs/DECISIONS_W3.md`와 Issue #49에 있다. 이 문서의 이전 결정과 충돌하면 그 문서가 우선한다(특히 2026-09-13 가중 기본 → W3 DML은 무가중 주 분석).

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
  - 근거: 폐업 점포의 소진공 매칭률 55.5%에 불과, 관측 간격 2–4개월로 시점 해상도 낮음,
    소멸에 ID 재발급/DB 정비가 섞여 있음 — 특히 202603→202606 구간 소멸의 30.5%가
    동일 필지+정규화 상호의 신규 업소번호로 재등장.
- 대신 소진공 대조 결과를 라벨 **보조정보**로 생성한다.
  - `sj_status`: `confirmed_gone`(폐업일이 소진공 소멸 구간과 정합) / `admin_only`(매칭됐으나
    소멸 미관측 등 인허가 단독 근거) / `conflict_stale`(폐업 후에도 소진공에 잔존) /
    `unmatched`(소진공 미매칭). 경계 기준(허용 오차 일수 등)은 구현 시 확정하고 코드에 상수로 명시
  - `sj_gap_months`: 행정 폐업일과 소진공 소멸 구간의 차이(개월)
- 보조정보의 용도: 라벨 신뢰도 플래그, 신고 지연 분포 실측 참고, conflict 제외 민감도 분석.
  라벨 값 자체를 바꾸는 데 쓰지 않는다.
- 폐업 신고 지연을 고려해 **최근 라벨에 maturity window를 적용한다. 후보 범위는 3–6개월.**
  - audit의 선행 소멸 갭 중앙값 6개월은 행정 신고 지연 외에 소진공 갱신 주기, ID 재발급,
    entity resolution 오류가 혼합된 값일 수 있으므로 확정 컷오프의 근거로 삼지 않는다.
  - W1에서 월별 폐업 신고 건수의 tail stability(최근 개월 집계의 안정화 시점)를 확인한 뒤
    **W2 진입 전에 최종 cutoff를 확정**하고 이 문서에 기록한다.
  - → **확정됨: 1개월** (2026-09-18 항목 참조). 위 "3–6개월"은 확정 전 후보 범위이며
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
  - `available_at`: 그 정보가 현실에서 이용 가능해진 시점 (예: 공시지가는 기준일 1/1이 아니라 공시일 4–5월)

## 2026-09-13 — 모델링 데이터 이원화 (Base / Enriched)
- **Base**: 인허가 + 서울 상권분석서비스 + 공시지가 등 장기간 가용 데이터.
  여러 origin을 이용한 temporal validation을 Base에서 수행한다.
- **Enriched**: Base + 소진공 개별 사업체 feature + 시점 정합성이 확보된 온라인 feature.
  주력 origin은 2025-06 (소진공 스냅샷 3개 축적 + 12개월 라벨 창 2025-07–2026-06 확보).
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

- 실측(build_maturity_diagnostic_table): 완결된 최근 5개월(months_ago 1–5)의
  폐업 신고 건수가 trailing baseline 대비 78–174% 범위에서 정상 등락, flag 0건.
  유일하게 flag된 것은 당월(months_ago=0, 부분월)뿐.
- 결론: 인허가 폐업일자에는 다개월에 걸친 행정 신고 지연이 관측되지 않는다.
  2026-09-13 결정에서 가정한 "3–6개월 후보 범위"는 소진공-인허가 간 갭
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
    **527,934행 / 44,067 store_id / origin 18개(2021Q1–2025Q2) / event 61,149(11.58%)** 이다.
    컷오프 1개월이라는 결정 자체는 그대로 유효하다 — origin_end 기준으로 12개월 창을
    확보해야 하므로 최신 origin이 2025Q3이 아니라 2025Q2가 된다.
- 컷오프 완화 및 개업 연도 코호트별 KM 곡선 검증(2021–2022/2023/2024/2025)은
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
  - 후속: cc 51+ Tier3 표본 30–40건을 추가 판정한 뒤 강등 규칙 활성화 여부를 확정한다. 그 전까지
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
  2021–2023 대부분 분기의 실제 공표일은 미확정이다.

**규칙**
- **origin 분기 T 자체의 상권분석 값은 사용 금지.** T 종료 후 약 2개월 뒤 공표되므로 `origin_end` 시점에 없다.
- **상권 feature의 기본 분기는 T-1**이다. 관측된 최대 lag(83일)가 T-1 종료–origin_end 간격(90–92일)보다 짧다.
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
- 2021–2022 분기 CSV는 2023-10-30 재발행본이며 현재 경계 기준 재계산값일 가능성이 높다.
  과거 origin 시점에 실제 공개된 값과 같다고 보지 않으며, 보고서 한계·재현성 위험으로 기록한다.

**leakage와 measurement error의 구분**
- T-1 규칙을 지키면 feature 값이 가리키는 기간 자체는 origin 이전이다. 따라서 점포의 결과(폐업)를
  직접 쓰는 일반적인 label leakage와는 다르다.
- 현재 경계를 과거 origin에 소급하는 문제는 **주로 spatial misclassification / measurement error**다
  (당시와 다른 면적으로 집계, 당시와 다른 상권 배정, 사후 재계산된 판본).
- 단, 현재 경계는 **2023-06 상가 DB**를 바탕으로 정해졌다(골목·발달·전통시장·관광특구 기준시점 2023년 06월,
  2022년 표준단위구역 기반). 따라서 2021–2023 origin에서 `trdar_cd` 비결측 여부나 polygon membership에는
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
  - 근거: ER은 소진공 7개 스냅샷(2024-12–2026-06) union으로 계산된다. 모든 Base origin
    (2021Q1–2025Q2)에서 origin 이후 정보다. 실측 event_12m 비율 — 2021Q1 매칭 1.2% vs 미매칭 21.9%,
    2025Q2 9.9% vs 15.6%. 점포의 생존이 매칭 여부를 만든 결과이므로 predictor로 쓰면 누수다.
  - 2026-09-13 "master dataset 모집단"의 "매칭 여부와 match confidence를 별도 feature/metadata
    컬럼으로 기록"은 **metadata로 기록**한다는 뜻으로 확정한다. 매칭 실패 점포를 삭제하지 않는 원칙은 그대로다.
  - `W1_FREEZE.md` §8 predictor 금지 목록에 ER 컬럼을 추가했다.
- **(I-3) 공시지가는 strict as-of로 붙인다.** 행마다 `available_at(y) <= origin_end`인 최대 연도 y의
  `land_price_{y}_valid` 하나만 `land_price`로 쓴다. 소급(backcast)·carry-forward는 하지 않는다.
  - 결과: origin 2024Q2–2025Q1 → 2024년, 2025Q2 → 2025년, **2021Q1–2024Q1(13개 origin, 381,406행,
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
  점포 1–2개 코드는 매출 row가 100% 없다(소수 점포 매출 비공개/억제로 판단). 매출 합계는 항상 "매출이 공개된
  mapped code의 합계"(biz_type 전체의 하한/부분관측치)이며, `trdar_biz_sales_store_coverage`
  (매출 row가 있는 코드의 점포 수 / mapped code 전체 점포 수, 같은 T-1 점포 원천, 분모 0이면 NA)로 대표성을 남긴다.
- **strict / coverage threshold로 행을 지우거나 NA 처리하지 않는다.** coverage는 provenance/quality 정보로 보존한다.
- **점포당 매출** `trdar_biz_sales_per_store_observed`: 분자·분모 모두 매출 row가 있는 mapped code 집합.
  분모 0 또는 code set 불일치면 NA (inf·0 대체 금지).
- 매출건수 합계는 매출금액과 Spearman 0.894로 중복이 커 추가하지 않았다.
- 개업·폐업률은 원천 정의(건수 / 분기 말 전체 점포 수 × 100)로 합계 재계산한다. 원천 `폐업_률`도 100% 초과가
  있으므로(806행, 최대 500) 자르지 않는다.

## 2026-09-23 — 온라인 존재감: 블로그 월별 수집 범위와 origin 사용 조건 (PR #21)

배경: W2-1 온라인 존재감 수집(축A 요약값 + 축B 블로그 월별 시계열) 범위를 "1순위(영업중+2023년
이후 폐업, 44,121건)만" vs "전체(110,347건)" 중 정해야 했다. 1순위만 받으면 축B가 origin=2021Q1~
2022Q3에서 폐업(event=1) 쪽 결측률이 24.8~100.0%인 반면 비event 쪽은 0~7.3%로, "온라인 데이터
없음"이 사실상 event=1의 대리 지표가 되는 것이 라벨 패널 실측으로 확인됨.

**결정: 축B(블로그 월별) 수집은 전체(110,347건) 대상으로 진행한다.**

근거:
- 축소 수집은 온라인 feature를 2022Q4 이후 11개 origin으로 영구 제한하는 결정이 되며, 나중에
  범위를 넓히려면 그때 다시 같은 시간(키 7개 기준 약 1.4일)이 들고, W3 시점엔 그 여유가 없다.
- 추석 연휴(9/24~26) 동안 API가 무인으로 도는 구조라 추가 소요의 실질 비용이 낮다.
- 오래된 origin의 게시물 삭제 편향(`DATA_CATALOG.md` §6 "알려진 한계")이 실제로 얼마나 심한지는
  전체를 받아봐야 측정 가능하다 — 안 받으면 배제 여부조차 판단할 근거가 없다.

**조건**: 2021~2022Q3 origin의 온라인 feature는 **origin별 삭제 편향 실측을 통과한 뒤에만** 모델에
투입한다. 무조건 전량 사용이 아니라, 받아서 검사하고 그 결과로 포함 여부를 정한다. 실측 방법은
W2-4 이벤트 스터디(마지막 언급일-폐업일 갭)와 함께 설계한다.

## 2026-09-23 — 온라인 존재감 수집을 main 인프라(io_license/config)로 정합

배경: `feature/online-presence-collection`이 3구 필터를 주소텍스트로 자체 구현하고 store_id를
원본 관리번호 그대로 썼던 것이 확인됨(merge-base가 45커밋 뒤처진 상태에서 작업). 2026-09-21
결정(개방자치단체코드 정본)과 `standardize.py`의 store_id 규칙을 모르고 재구현한 결과.

**결정: `build_targets.py`가 `io_license.load_license_raw()` + `filter_target_gu()`를 직접
호출해 모집단(110,347건)을 만들고, store_id는 `standardize.py`와 동일한 `{prefix}_{관리번호}`
(GR/SR/BT)를 쓴다.**

영향:
- 기존 수집분(온라인 존재감 축A, 당시 100,890건)을 새 store_id로 마이그레이션하고, 정본 밖
  주소텍스트 전용 11건은 제외했다 (마이그레이션 시점 기준 — 전체 모집단의 불일치 20건 중
  이미 수집된 부분만 해당). 정본에는 있으나 수집 전이었던 건은 잔여 수집 대상에 자연히 포함된다.
- 축A `naver_blog_total`/`naver_cafe_total`은 상호명 매칭 필터 이후 건수(≤100, 1페이지 한도)이며
  네이버 API 응답의 원본 `total`과 다르다. 이후 수집분부터 `naver_blog_api_total`/
  `naver_cafe_api_total` 컬럼으로 원본 total을 같이 남긴다. **이미 수집된 행에는 이 필드가 없고,
  복원하려면 재수집이 필요하다 — 비용 대비 실익이 낮아 재수집하지 않기로 함** (모델에 실제로
  쓰이는 것은 축B 월별 데이터이며, 그쪽은 최초 설계부터 `blog_api_total`을 보존하고 있음).

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
- **시간 분할 + 라벨 성숙 embargo 4분기.** origin t 예측은 t−5 이하로 학습한다(t−1–t−4 비움). 수식상
  경계(s ≤ t−4)보다 한 분기 보수적이며, 폐업 신고 지연(성숙 컷오프 1개월)을 흡수한다. embargo 없는
  분할은 성능을 부풀린다(feature 4개 실험에서 AP 상대 +23.6%). random split·점포 홀드아웃은 대조군이다.
- **성능·민감도·보정은 rolling OOF 예측으로 한다** (학습 origin ≥ 4개가 되는 2023Q1부터 매 origin).
  상권 polygon 스냅샷(2023-10-23) 이후 origin(2023Q4–)만의 성능을 따로 보고한다.
- **민감도 feature set**: base / no_trdar / no_land_price / no_gu / license_only. T-2 상권은
  `attach_trdar_features(lag_quarters=2)`로 만든 master를 `--master`로 넣어 같은 평가를 돌린다.
- **보정은 첫 검증 origin 기준 학습 가능 구간의 OOF 예측으로 적합**하고, 검증 구간 ECE가 개선될 때만
  적용한다. raw·calibrated 지표를 둘 다 남긴다 (origin별 base rate 변동이 커서 보정이 오히려 나빠질 수
  있다 — 실측 상대 27% 변동). → **2026-09-28 갱신**: 선택(적용 여부 판단)과 최종 평가에 같은 구간을 쓰는
  절차는 선택 편향이 생긴다(#32 리뷰) — 아래 "보정 3구간·후보 비교" 항목의 fit/select/test 분리로 바뀌었다.
- **불확실성 구간 = 점포 단위 부트스트랩 재학습의 5–95 백분위 = 90% 구간** (95% CI가 아니다, 기본 B=20). Venn-ABERS는 보정 표본이
  크면 폭이 사실상 0이라(평균 0.0013, 구간 커버 0/10) 화면에 쓰지 않는다. 화면 문구는 "예측이 얼마나
  흔들리는가"의 구간이며 "폐업 확률의 범위"가 아니다. 점추정이 구간 밖에 놓이면 구간을 넓혀 포함시킨다.
- **band = 절대 확률 컷오프.** high = {p ≥ c} 집단의 실측 위험이 보정 구간 평균의 2배 이상이 되는 가장
  낮은 컷오프, mid = 예측 확률이 평균의 1.2배 이상. (mid를 구간 평균 lift로 찾으면 평균 미만 점포가 섞여
  컷오프가 base rate 아래로 내려가 mid가 61%가 됐다 — 실데이터 첫 실행.) 백분위 컷오프는 isotonic 동점 때문에 의도한 비율을 만들지 못하고(q90 → 15.1%),
  "상위 N%" 동어반복이라 쓰지 않는다. 상대 위치는 `percentile`(같은 origin·자치구·업종 내)이 맡는다.
  **high 등급의 예측 확률은 실제보다 높게 나오는 경향이 있다** — 정의상 "실측이 평균의 2배 이상"인
  가장 낮은 컷오프를 잡으므로, high로 잡히는 점포들의 개별 예측 평균은 그 실측보다 흔히 높다
  (2026-09-28 실측, base·현 설정·platt 적용 후: high 등급 test 구간 평균 예측 0.248 vs 실측 0.240 —
  적용 전(raw) 기준으로는 평균 예측 0.297 vs 실측 0.240으로 격차가 더 컸다. 아래 "보정 3구간" 항목).
  화면에 "high 등급의
  확률 수치는 실제보다 높게 표시되는 경향이 있다"는 설명을 함께 보여준다.
- **공시지가(`land_price`)는 시간 분할 검증에서 학습에 한 번도 들어가지 못한다.** 값이 있는 origin이
  2024Q2–2025Q2뿐이고, 검증 가능한 마지막 origin(2025Q2)의 학습 구간이 2024Q1까지이기 때문이다.
  검증되지 않은 feature를 서빙 모형에만 넣지 않도록, **서빙 모형은 검증과 같은 feature set으로 학습**하고
  land_price는 라벨이 쌓여 검증 가능해질 때까지 Base 서빙 입력에서 제외한다.

## 2026-09-25 — 온라인 feature 사용 판정(#26)과 Enriched 탐지 모형 채택
근거: `src/data/online_features.py`, `src/analysis/online_deletion_bias.py`,
`python -m src.models.train_detect --online ... --primary enriched` 실데이터 결과.

- **(#26) 18개 origin 모두 온라인 feature를 쓴다 (불통과 0건).** 오래된 origin(2021Q1–2022Q3)의
  보유율 격차(비폐업 − 폐업)는 기준선(2022Q4–2025Q2 평균 0.0597)보다 오히려 작다(전 origin에서 95% 구간
  상한 < 0). 최근 4개 origin만 기준선으로 둔 보조 판정과 업종별 판정도 불통과가 없다.
  격차는 분기당 +0.49%p씩 커지지만, enriched의 성능 향상은 같은 기간 분기당 −0.0007 AUC로 오히려 줄었다
  (2023년 +0.022 → 최근 +0.017). 수집 시점(2026-09) 비대칭이 성능을 부풀렸다면 반대 방향이어야 하므로
  향상은 실제 신호로 본다.
  → **2026-10-01 갱신**: 이 "통과"는 오래된 origin을 **쓰기로 한 운영 판정**이지 "삭제 편향이 없다"는 결론이 아니다.
  오래된 origin일수록 비폐업(event_12m=0) 집단에 이후 폐업 점포가 더 섞이는 관측 기간 차이(구성 효과, 아래 2026-09-29
  항목 2)와 게시물 삭제 편향이 같은 격차에 섞여 있어 둘을 분리하지 못한다. 하류 영향이 작다는 근거는 마스킹 민감도
  (2021Q1–2022Q3 전 점포 NA에도 2023Q4~ AUC 0.6222 → 0.6219)다.
- **주 탐지 모형은 enriched(base 19개 + 온라인 6개)다.** rolling OOF 10개 origin 모두에서 base보다 높다
  (평균 AUC 0.6029 → 0.6219, AP 0.1764 → 0.1872). 온라인 묶음의 permutation 기여(0.029)는 상권 묶음(0.0055)의
  5배를 넘고, 대부분 `online_blog_months_since_last`에서 나온다. 보정은 적용하지 않는다(raw ECE 0.0111).
  → **2026-09-28 갱신**: 보정 방법론이 fit/select/test 3구간·후보 3개로 바뀌었다(아래 "PR #32 리뷰 반영"
  항목). enriched를 이 방법론으로 다시 돌린 결과는 별도로 기록한다(`detect_v0_enriched` 재실행).
- **절단 점포 결측은 `na`(미관측 구간 NA)를 유지한다.** `first_date_truncated`는 수집 시점의 누적 게시물 수로
  정해지는 미래 정보이므로 **predictor로 쓰지 않고 결측 처리에만 쓴다** — 결측 규칙은 이슈 #25 합의(QA 오류·미수집 → 전 월 NA, 절단 점포의 미관측 구간 → NA, 그 외 빈 달 → 0)를 따른다.
  결측 점포의 폐업률(6–9%)이 관측 점포(11–13%)보다 낮아 결측이 생존 신호가 될 수 있다.
  → **2026-09-29 갱신**: "관측된 글만 센 하한값(`lower_bound`)과의 AUC 차이 0.0015 = 이 경로의 누수 상한"이라는
  표현은 틀렸다(두 방식은 결측 패턴과 관측값이 동시에 바뀌어 차이를 한 경로로 귀속할 수 없다) — 삭제하고
  아래 "PR #33 리뷰 반영" 항목의 3방향 민감도로 바꿨다.
  → **2026-10-01 갱신**: 절단 전용 플래그로 다시 재면 절단 경로는 **유의한 정보 경로**다 — 아래 2026-10-01 항목.
  기본값(`na`)은 그대로 두되 한계로 기록하고, 처리 정책은 #44와 함께 정한다.
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
- **peer 비교 = 같은 origin·업종·자치구·업력대(1년 미만/1–3/3–5/5–10/10년 이상) 안에서 요인 기여의 백분위.**
  표본 30개 미만이면 자치구 → 업력대 순으로 조건을 푼다. "유사 상권" 대신 자치구를 쓰는 이유는 상권 유형
  (`trdar_type`)이 polygon membership 계열이라 predictor·비교 기준에서 제외했기 때문이다(2026-09-23).
- **진단문**: "○○이 예측 위험도를 약 N%p 높이는(낮추는) 쪽으로 기여했습니다." + peer 백분위가 70 이상일 때만
  "같은 업종·자치구·업력대 점포 중 상위 M% 수준입니다."를 붙인다. 요인별 판단 근거 값(`values`)을 함께 내고,
  온라인 요인은 6개 신호 중 기여가 가장 큰 것을 같은 방식(Shapley)으로 골라 관측값으로 서술해
  어떤 신호가 작용했는지 드러낸다("주된 근거: …"). "때문에", "원인", "고치면" 같은 인과 표현은 쓰지 않는다.
- **온라인 요인 표시 보류**: 온라인 요인이 위험을 올리는데 주된 근거가 언급 있음·많음·최근 언급·증가이면
  진단문에서 표시를 보류한다(`display=false`). 사업자가 할 수 있는 일로 번역되지 않고, 이름 오탐(#28)이나
  유행 상권 인기 점포 효과일 수 있기 때문이다. 실측: 최고 위험 3개 점포(마포구, 2020–23 개업)는 12개월 언급 101–131건이 주된 근거였고 모두 검색 결과 상한에 걸렸으며,
  그중 흔한 단어로 된 4글자 상호(API total 2,900)는 오탐 가능성이 높다 (점포 목록은 팀 드라이브). 기여값은 바꾸지 않는다.
- **팀 확인 필요**: (1) 온라인 언급을 입지·수요에 둘지 별도 유형으로 둘지 (2) 현재 owner 요인은 온라인 언급뿐이고
  policy 요인(공시지가)은 기여가 0이라, W2-7 정책 매칭이 연결할 요인이 사실상 온라인 하나다.

## 2026-09-26 — W2-0 예측용 master_score (Issue #35)
근거: Issue #35, 2026Q2 모집단 실측, 2025Q2 역검증(master_base와 동일). 구현 `src/data/master_score.py`,
명세 `docs/MASTER_SPEC.md` 메모, 산출물 `outputs/master/master_score.parquet`·`score_meta.json`·`qa_report_score.md`(커밋하지 않음).

- **예측용 master는 별도 모듈로 만든다** (`python -m src.data.master_score --origin 2026Q2`). master.py의 학습용 생성 경로와
  master_base 산출물은 바꾸지 않는다 (재실행 결과 master_base.parquet 바이트 동일 확인).
- **모집단·적격 판정은 labels_base와 같은 함수**(원본 인허가 로더 → `개방자치단체코드` 3구 필터 → `labels.build_long_panel`)를 쓴다:
  인허가일 ≤ origin_end이고 폐업일이 없거나 > origin_end. 기준일 이후 폐업한 점포는 당시 영업이었으므로 보존하고, 기준일 이후 개업
  점포는 제외한다. `build_long_panel`이 만드는 `event_12m`(기준일 이후 폐업 = 미래 정보)은 즉시 버린다. 점포 수는 하드코딩하지 않는다.
- **영업상태명 '폐업'인데 폐업일자가 없는 점포는 포함한다** (학습 패널과 같은 날짜 규칙). QA에 건수를 따로 기록하며 실제 영업이
  확인된 것으로 보지 않는다 (2026Q2 실측 6곳).
- **as_of 판정에는 원천 관측 여유가 필요하다**: 원천 최종 관측일 ≥ as_of + 성숙 컷오프(1개월, 2026-09-18 결정). 부족하면 멈춘다.
- **컬럼은 master_base에서 `event_12m`·`maturity_cutoff_used_months`만 뺀 것**이며 이름·dtype·결측 규칙이 같다
  (`master_schema.SCORE_EXCLUDED_COLUMNS`). origin 값은 분기 문자열("2026Q2") — PR #36 serve 입력 계약.
- **시간 정합성은 기존 규칙 그대로**: 상권 T-1(2026Q2 → 2026Q1, 2026Q2 원천은 2026-08-18 공표라 사용하지 않음),
  2026Q1 공표일은 원천에서 확인되지 않아 `archive_inferred`(NaT)로 두며 검증된 날짜로 표기하지 않는다. 공시지가는 strict as-of로
  2026년 값(공시 2026-04-30)을 붙이되, 탐지 모형은 검증되지 않은 feature라 쓰지 않는다(2026-09-25 W2-2 결정, 서빙이 제외).
- **온라인 feature는 master_score에 결합하지 않는다.** PR #33 `online_features --panel <master_score>`로 별도 테이블을 만들어 서빙
  `--online-score`로 붙인다. PR #21 현재 등록 스냅샷은 예측 feature로 쓰지 않는다. 온라인 원천의 DATA_CATALOG 등록은 PR #21 몫으로 남는다.
- **역검증을 회귀 기준으로 둔다**: 같은 코드로 라벨이 있는 origin(2025Q2)을 만들면 master_base 같은 origin(라벨 제외)과
  행·store_id·값·결측·dtype이 같아야 한다 (`--compare-master`, 2026-09-26 실측 동일).

## 2026-09-26 — W2 경쟁지표 6종 정의 (인허가 기반, Base 확장 후보)
근거: PR #31 후속 요청, 2026-09-25 "W2 경쟁지표 개발과 모델링 병렬 진행" 결정의 구체화.
구현 `src/data/competition_features.py`, 산출물 `outputs/competition/`(커밋하지 않음), 원천·결측은 `DATA_CATALOG.md` §1-1.

- **별도 테이블로 만든다.** master_base·master_score와 Base predictor 19개(`master_schema.COLUMN_ROLES`)는 바꾸지 않는다.
  모델 단계가 `(store_id, origin)` m:1로 붙인다(`master.attach_enriched_table`과 같은 방식). 모델 비교는 별도 PR.
- **기준일 t = origin_end, 영업 판정은 `labels.build_long_panel`과 같다**: 인허가일 ≤ t 이고 (폐업일 없음 또는 폐업일 > t).
  현재 영업상태명은 쓰지 않는다. 기준일 당일 개업은 영업 중, 당일 폐업은 영업 아님(폐업 집계에는 포함).
  `event_12m`은 읽지 않는다. 실측: 이 판정으로 만든 영업 모집단 = master_base 18개 origin·master_score 2026Q2의 키 집합.
- **경쟁 모집단은 3개 구 × 3개 업종(일반음식점·휴게음식점·미용업) 인허가 점포뿐이다.** "같은 필지의 모든 사업체"가 아니다.
- **모든 점포 수는 자기 점포를 제외한다.** 개업 건수도 자기 개업을 제외한다(자기 개업 여부는 업력 12개월 미만과 같아
  업력과 기계적으로 연결되기 때문).
- **정의** (창 = (t−12개월, t])
  - `comp_pnu_cnt`: 같은 PNU의 t 당시 영업 점포 수
  - `comp_pnu_same_type_cnt`: 같은 PNU·같은 `biz_type`
  - `comp_dong_same_type_cnt`: 같은 법정동·같은 `biz_type`
  - `comp_dong_open_4q`: 같은 법정동·업종에서 인허가일이 창 안인 점포 수
  - `comp_dong_close_4q`: 같은 법정동·업종에서 폐업일이 창 안인 점포 수 (t 이후 폐업은 쓰지 않는다)
  - `comp_dong_density_yoy`: 같은 법정동·업종 **전체** 영업 점포 수(자기 포함)의 전년 대비 증감률
    (N_t − N_{t−12개월}) / N_{t−12개월}, 분모 0이면 NA. **이름과 달리 면적당 밀도가 아니라 점포 수 증감률이다**
    (PR #31 계약의 이름을 유지). 법정동 면적 원천은 쓰지 않는다.
- **N_t − N_{t−12개월} = 창 안 개업 전체 − 창 안 폐업 전체는 항등식이다**(실측 위반 0행). 따라서 `density_yoy`는
  open·close와 정보가 겹치고, 동·업종 규모로 나눈 부분만 새로 더해진다. 모델 비교에서 이 중복을 감안한다.
- **동 키는 10자리 법정동 코드 `bjd_code`** (= PNU 앞 10자리). 동 이름으로 집계하지 않는다. 행정동과 섞지 않는다.
- **위치 키가 없으면 0이 아니라 NA다.** PNU 없음 → PNU 지표 NA, `bjd_code` 없음 → 동 지표 NA. 위치 없는 점포(195곳)는
  다른 점포의 경쟁 점포로도 세지 않는다(경쟁 점포 수는 그만큼 하한).
- **자치구(개방자치단체코드 기준)와 `bjd_code` 시군구가 다른 점포는 PNU·동 지표를 모두 NA로 두고 집계에서 뺀다**
  (`comp_location_status="gu_bjd_mismatch"`). 다른 자치구로 옮기지 않는다. 실측 4곳 = 표준화 단계 `gu_mismatch` 4곳.
- **시점 한계 (provenance로 남긴다)**
  - 위치(PNU·법정동)는 인허가 파일의 현재 스냅샷 주소를 과거 origin에 소급한 것이다(`comp_location_basis`).
    이전(移轉)은 식별할 수 없다(`DATA_CATALOG.md` §1 한계와 같음).
  - `comp_feature_asof = origin_end`는 논리적 관측 기준일일 뿐이다. 인허가·폐업 기록이 그날 실제로 공개돼 있었는지
    (사후 신고·정정 포함)는 확인되지 않았으므로 `comp_available_at`은 NA, `comp_available_at_basis="unverified"`로 둔다.
    원천 최종 관측일(`comp_raw_last_observed`)은 raw 추출 시점의 하한이다.
  - 날짜 기반 부분은 미래 원천 행에 불변이다(t 이후 인허가 행 삭제·t 이후 폐업일 제거 후 재계산 결과 동일, 실측·테스트).
    원천 스냅샷에서 사라진 과거 레코드(말소 등)는 확인할 수 없다.

## 2026-09-28 — W2-2 PR #32 리뷰 반영: 분할 비교·보정 3구간·확률 계약·OOF 저장
근거: choihongjun1 PR #32 리뷰(2026-09-26 18:26, 2026-09-27 00:25). 최종 모형 설정(base HGB 기본 하이퍼파라미터)은
바꾸지 않는다 — 튜닝 설정(`learning_rate=0.03, max_leaf_nodes=15`, feat/w2-2-benchmark HPO) 채택은 Issue #45
결정 대기다. 아래 비교는 두 설정 모두로 냈다.

- **분할 비교표(`split_comparison.csv`)에 평가 모집단을 명시한다.** `random_split`·점포 홀드아웃은 전체 18개
  origin에서 무작위로 뽑은 **약 20%**(`splits.random_split_baseline`/`store_holdout_split`의 `test_frac=0.2`)를
  평가한 값이라 "2025Q2 검증 결과"가 아니다 — `test_scope` 열에 이를 적는다. 네 분할 모두 최신 origin(오늘
  데이터는 2025Q2)만 추린 성능(`*_last_origin` 열)도 함께 낸다 — `time_split` 두 설정은 test가 이미 그 origin
  하나뿐이라 전체 성능과 같게 나오고, `random_split`·점포 홀드아웃은 이 열로 비로소 2025Q2만의 성능을 볼 수
  있다. 현 설정·튜닝 설정 모두로 돌려 `params` 열로 구분한다 (`src/models/train_detect.split_comparison`).
- **보정을 fit/select/test 3구간으로 분리한다** (`train_detect.calibration_windows`) — 선택과 최종 평가를
  같은 구간에서 하면 선택 편향이 생긴다(위 리뷰). 3구간은 서로 겹치지 않고 순서대로 이어진다:
  - **fit**(보정기 학습, 4개 origin) → **select**(적용 여부 선택, 4개 origin) → **test**(최종 보고,
    `TEST_SIZE`=2개 origin, 화면에 실제로 나가는 검증 구간).
  - 오늘 데이터(2021Q1~2025Q2, 18개 origin)에서는 fit=2023Q1–Q4, select=2024Q1–Q4, test=2025Q1–Q2와 같다.
  - **select 구간(2024Q1–Q4)의 라벨은 모형 개발 시점(오늘)에는 이미 확정돼 있다** — origin_end + 12개월
    성숙 기준으로 2025Q4까지의 사건을 알아야 하는데 오늘(2026-09)이 이미 그 뒤이기 때문이다. 이건 **서빙
    시점 규칙(embargo 4분기, 서빙 당시 아직 안 지난 origin은 못 쓴다)과는 별개**다 — 모형을 "지금" 개발·평가할
    때는 과거 origin의 라벨을 다 볼 수 있지만, 서빙은 그 시점에 성숙한 라벨까지만으로 학습한 모형을 쓴다.
- **후보 3개: raw(원 확률) / isotonic / Platt(로지스틱 재보정, `calibration.PlattCalibrator`).** Platt은
  logit(p)에 기울기·절편을 적합하는 매끄러운 보정으로, isotonic(계단형)보다 select 구간처럼 표본이 작을 때
  덜 흔들릴 수 있어 후보에 넣었다.
- **선택 규칙(미리 고정, `train_detect.choose_calibration`):** fit 구간으로 세 후보를 적합하고, **select
  구간 Brier가 가장 낮은 후보**를 고른다. raw가 아닌 후보를 골랐어도, raw 대비 Brier 차이가 점포 단위
  클러스터 부트스트랩(`calibration.bootstrap_brier_diff`, 기본 1,000회) 95% CI상 **유의하지 않으면
  (0을 포함하면) raw를 유지한다** — select 구간(4개 origin)은 표본이 상대적으로 작아 우연한 차이로
  잘못 채택할 수 있어서다. 선택 이유는 `calibration_decision.json`에 사람이 읽을 수 있는 문장으로 남긴다.
- **결과(2025Q2 master_base, base feature set, 2026-09-28 실측):**
  - **현 설정: `platt` 채택.** select 구간(2024Q1–Q4, 118,006행) Brier 0.10649(raw) → 0.10608(platt),
    차이 −0.00041, 부트스트랩 95% CI [−0.00059, −0.00022] (유의). test 구간(2025Q1–Q2, 58,319행)에서는
    Brier 0.10113(raw) → 0.10137(platt·근소 악화), ECE 0.0117 → 0.0207(악화), 보정 기울기 0.730 → 1.254(1을
    지나쳐 과잉보정). **select 구간에서 유의했던 개선이 held-out test 구간에서는 재현되지 않는다** — Brier가
    작은 확률 대다수(폐업 안 함)에 덜 민감해, 꼬리(고위험) 쪽 재보정 오류를 못 잡아낼 수 있다. 미리 고정한
    규칙을 그대로 따랐지만 이 한계는 남는다.
    - **화면에 미치는 영향**: high 등급(2,019곳, 3.46%)은 platt이 단조 변환이라 raw 기준과 **완전히 같은
      점포 집합**이지만, mid로 잡히는 점포가 raw 기준 16.8%(9,776곳) → platt 기준 28.9%(16,838곳)로 크게
      늘고 low는 79.8% → 67.7%로 줄었다(raw에서 mid 문턱 바로 아래였던 점포들이 platt의 확대(기울기>1)로
      문턱을 넘음). **등급 분포가 실무적으로 크게 바뀌므로, 이 채택을 최종 반영할지는 위 held-out 한계와
      함께 팀 판단이 필요하다** (Issue #45와 별개로 남기는 열린 질문).
  - **튜닝 설정: `raw` 유지.** select 구간에서 platt의 Brier가 raw보다 낮아 보였지만(0.105842 vs 0.105960,
    차이 −0.00012) 부트스트랩 95% CI [−0.00027, +0.00003]가 0을 포함해 유의하지 않았다. test 구간 raw는
    Brier 0.100613, ECE 0.0065, 보정 기울기 0.937.
    - **2026-10-01 정정(#32 재검증)**: 이전 수치(select 0.105847 vs 0.105975, CI [−0.00030, +0.00004])는
      `TUNED_PARAMS`가 두 값만 담고 있어 나머지가 sklearn 기본값(`max_iter=100`, `min_samples_leaf=20`,
      `l2_regularization=0`, `early_stopping='auto'`, `random_state=None`)으로 학습된 **다른 모형·비결정적 실행**의
      결과였다. `TUNED_PARAMS = {**DEFAULT_PARAMS, learning_rate=0.03, max_leaf_nodes=15}`로 고치고(#47과 같은 방식)
      다시 돌린 값으로 교체했다. 결론(raw 유지)은 같다. 현 설정 결과는 바뀌지 않았다(OOF 예측값 완전 일치).
      실제 학습 설정은 `run_meta.model_params_by_config`에 config별로 남는다.
  - 전체 표는 `outputs/models/detect_v0/calibration_window_report.csv`, 선택 근거는 `calibration_decision.json`.
- **high 등급의 평균 예측 확률이 실측 폐업률보다 높게 나오는 경향은 platt 적용 후에도 남는다** (다만
  격차는 줄었다): test 구간 high 행 평균 예측 0.248 vs 실측 0.240(platt 적용, 실제 서빙 값) — 같은 fit
  구간을 raw로 컷오프를 잡으면 0.297 vs 0.240으로 격차가 더 크다. `band_profile.csv`의 high 행
  (`pred_mean` vs `obs_rate`). 화면에는 이 경향을 설명하는 문구를 함께 보여준다 (위 "band" 항목 참고).
- **`probability_12m`/`ci_low`/`ci_high`/`band`/`calibrated` 계약** (필드명은 W2-5 PR #41
  `report_schema.json` `$defs/risk`와 대조해 맞춤 — 사용자 요청의 "calibration_applied"는 실제 스키마
  필드명이 아니라 `calibrated`다):

  | 필드 | 정의 | 산출식 |
  |---|---|---|
  | `probability_12m` | 12개월 내 폐업 예측 확률(점추정) | `calibrated=true`면 선택된 보정기(`chosen.predict`)를 원 모형 확률(`p_oof`)에 적용한 값, `false`면 원 모형 확률 그대로 |
  | `ci_low`/`ci_high` | 점포 단위 부트스트랩 재학습(B회) 예측의 5/95 백분위(= 90% 구간) — **신뢰구간이 아니라 "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지"의 범위** | 부트스트랩 원값에 `calibrated=true`면 같은 보정기를 적용. 점추정이 구간 밖에 놓이면 구간을 넓혀 포함시킨다(좁히지 않음) |
  | `band` | low/mid/high 절대 확률 등급 | `probability_12m`(보정 적용 여부 반영된 값)에 `bands.assign_bands_absolute` — 컷오프는 **fit 구간** OOF(선택된 보정기 적용)로 `bands.suggest_cutoffs`가 정함 |
  | `calibrated` | 보정을 적용했는지 (`chosen != "raw"`) | 위 선택 규칙의 결과. `false`면 `probability_12m`은 원 모형 확률과 같다 |

  **보정을 적용하면(`calibrated=true`) 진단(W2-3 Shapley)은 여전히 보정 *전* 모형 확률을 분해한다** —
  `diagnose.factor_shapley`가 `model.predict_proba`(원 모형)를 직접 쓰고, `serve.py`가
  `res["meta"]["probability_12m"] == p_raw`(보정 전)임을 어서션으로 확인한다. **이미 서빙 쪽에
  `serve_meta.diagnosis_scale`** 필드로 이 관계를 기록해 둔다("calibrated와 다름 (보정 전 확률)" /
  "risk 확률과 같음") — 이 PR에서 새로 만들 필요는 없다. 화면 문구는 이 필드를 근거로 "위험요인
  기여도의 합은 보정 전 확률 기준이며, 화면에 보이는 위험도(%)와 다를 수 있습니다"를 위험도 표시
  근처에 각주로 보여주는 방향을 제안한다 (기여도 목록이 아니라 위험도 수치 옆).
- **OOF 예측을 저장한다** (`outputs/models/detect_v0.../oof_predictions.parquet`, gitignore) —
  `store_id, origin, config(현 설정/튜닝), p_oof, y`. `run_meta.json`에 파일 sha256과 행 수를 남긴다.
  생존분석(C-index 등)은 이 PR 범위 밖이며 **후속 PR**로 진행한다.

## 2026-09-29 — PR #33 리뷰 반영: 절단 결측 누수 경로·#26 구성 효과·시점 메타 분리
근거: `src/analysis/online_truncation_sensitivity.py`(신규), `src/data/online_features.py`,
`src/models/train_detect.py`(`attach_online`), rolling OOF 10개 origin 실데이터.

### 1. 절단 결측 누수 — "AUC 차이 0.0015 = 누수 상한" 표현을 폐기
> **2026-10-01 갱신 — 이 절의 두 해석은 틀렸다(아래 2026-10-01 항목으로 대체).** ① "NA 플래그"(온라인 feature 중
> 하나라도 NA)는 절단 대리 지표가 아니었다 — 실데이터에서 그 NA 행의 대부분은 절단이 아니라 "언급이 한 번도 없음"
> (`months_since_last`가 정의상 NA)이다. 그래서 "플래그 효과 유의하지 않음"은 절단 경로의 크기를 잰 결과가 아니다.
> ② (iii) `lower_bound`는 "절단 경로 차단"이 아니다 — 절단 점포의 과거 창을 과소 집계하므로 수집 시점 절단 영향이 값에
> 남는다. 아래 표의 "튜닝" 행은 `TUNED_PARAMS`가 두 값만 담던 때(#32 20cbd95 이전)의 값이라 다시 돌린 값과 다르다.

이전 표현("`lower_bound`와의 AUC 차이 0.0015 = 이 결측 경로의 누수 상한")은 틀렸다. `na`/`lower_bound`는
**결측 패턴과 관측값을 동시에** 바꾸므로, 두 설정의 AUC 차이를 결측 경로 하나에 귀속할 수 없다. 대신
명시한다: **절단 여부(수집 시점 2026-09의 누적 게시물 수로 정해지는 미래 정보)는 온라인 feature의 NA
패턴을 통해 정보 경로로 쓰인다** — HistGradientBoosting이 NaN을 그대로 분기에 쓰므로, "이 점포는 절단
됐다"는 사실 자체가 (약하지만) 생존과 상관된 신호로 학습에 들어갈 수 있다.

**민감도 3가지(rolling OOF 10개 origin, 현 설정·튜닝 설정)**. (i) 현행 / (ii) 절단 점포 평가 제외
(예측은 (i)와 같음, `first_date_truncated`인 점포만 평가에서 뗀 것) / (iii) 절단 경로 차단
(`truncated_policy="lower_bound"` — 미관측 구간을 하한값으로 채워 NA 표시가 절단과 무관해지게 함).
추가로 base(온라인 6개 없음) + "온라인 결측 여부" 이진 플래그 1개만 더한 설정으로, 결측 *패턴만* 남겼을
때의 효과를 직접 잰다.

| params | scenario | 평균 AUC(origin별) | 합산 AUC(pooled) |
|---|---|---|---|
| 현 설정 | base | 0.6029 | 0.6013 |
| 현 설정 | base, 절단 점포 제외 평가 | **0.6037** | 0.6020 |
| 현 설정 | base + NA 플래그 1개 | 0.6044 | 0.6028 |
| 현 설정 | (i) 현행 enriched | 0.6219 | 0.6205 |
| 현 설정 | (ii) enriched, 절단 점포 제외 평가 | **0.6210** | 0.6195 |
| 현 설정 | (iii) enriched, 절단 경로 차단 | 0.6204 | 0.6190 |
| 튜닝 | base | 0.6142 | 0.6130 |
| 튜닝 | base, 절단 점포 제외 평가 | 0.6149 | 0.6136 |
| 튜닝 | base + NA 플래그 1개 | 0.6141 | 0.6130 |
| 튜닝 | (i) 현행 enriched | 0.6269 | 0.6260 |
| 튜닝 | (ii) enriched, 절단 점포 제외 평가 | 0.6265 | 0.6255 |
| 튜닝 | (iii) enriched, 절단 경로 차단 | 0.6254 | 0.6245 |

리뷰어 인용 수치 재현: **절단 제외 시 base 0.6037 → enriched 0.6210** — 위 표(굵게 표시)와 정확히 일치.
**"NA 플래그만 추가" +0.0028**은 합산 AUC 기준 +0.0015(현 설정, 점포 단위 부트스트랩 95% CI
[−0.0001, +0.0031] — **유의하지 않음**)로 재현됐다. 방향은 같지만 크기가 리뷰어 인용값의 절반 정도이고
튜닝 설정에서는 방향조차 반대(−0.00004, 역시 유의하지 않음)다 — 정확한 재현이 아니라 근사 재현으로
기록한다(플래그 구성 방식 차이일 수 있다).

**(i) vs (iii) 합산 AUC 차이 부트스트랩 95% CI**(점포 단위, B=1000): 현 설정 diff=0.00149,
CI [0.00024, 0.00267] — **유의**(iii이 유의하게 나쁘다). 튜닝 설정 diff=0.00152, CI [0.00064, 0.00240] —
역시 유의. → **사전에 정한 규칙("(iii)이 성능을 유의하게 떨어뜨리지 않으면 기본값 전환 검토")에 따라
기본값은 바꾸지 않는다.** `--truncated-policy lower_bound` 코드 옵션은 이미 있으므로(`online_features.py`)
추가 구현 없이 그대로 두고, 실제 기본값 변경은 하지 않는다(어차피 #44·#45 결정과 함께 적용할 사안이며,
지금 결과로는 전환할 근거도 없다).

**한계**: 이 결측-경로 정보량(≈0.0015 AUC)은 작지만 0은 아니다 — #26이 "삭제 편향으로 격차가 벌어진
origin 0건"이라 판정한 것과 별개로, NA 패턴 자체는 여전히 약한 정보 경로다. 짧은 상호 처리(#44)는 이
PR에서 다루지 않는다(요청대로 범위 밖).

### 2. #26 해석 보강 — event_12m=0 집단의 구성 효과
**2021Q1 기준, event_12m=0(그 origin에서 12개월 내 폐업 아님) 집단 중 37.9%는 이후(12개월 지나서) 실제로
폐업한다**(재현값. 사용자가 인용한 수치는 37.1% — 근사 일치, 원본 소스 기준 차이로 보인다.
`outputs/standardized/licenses_3gu.parquet`의 `close_date`를 `labels_base` 2021Q1 패널과 조인해 계산,
경계 위반 0건으로 라벨 정의와 정합 확인). 즉 "폐업 아님" 집단은 "생존"이 아니라 "그 시점까지는 폐업
아님"이며, 오래된 origin일수록 이 안에 미래 폐업 점포가 더 많이 섞여 있다(관측 기간이 길기 때문).
이게 #26의 "보유율 격차" 진단에 주는 구성 효과: event=0 집단의 온라인 보유율이 실제로는 "진짜 생존
점포"와 "아직 폐업 전인 점포"가 섞인 평균이라, 오래된 origin에서 격차가 희석되거나 왜곡될 수 있다.

**민감도(재현)**: 2021Q1–2022Q3 origin의 온라인 feature를 `mask_origins`로 전 점포 일괄 NA 처리했을 때
(이 구간이 #26 삭제 편향 판정에 불통과했다고 가정한 것과 같은 조치) enriched의 2023Q4~ 구간 평균 AUC는
**0.6222 → 0.6219**(리뷰어 인용값과 정확히 일치)로 거의 변하지 않는다. 이 구간은 학습 데이터로만 쓰이고
(rolling OOF의 평가 origin이 아니므로) 직접 평가되지 않는데도, 마스킹의 하류 영향이 사실상 없다는 뜻이다
— #26이 "18개 origin 모두 통과"로 판정한 결정의 하방 위험이 작다는 근거로 덧붙인다.

### 3. 시점 메타 분리 — `online_available_at` ≠ `origin_end`
> **2026-10-01 갱신 — 대체됨.** 수집 시각을 `online_available_at`에 넣는 방식은 축B `available_at` = 게시월 말일 정의
> (2026-09-29 #23 항목, DATA_CATALOG §6)와 충돌해 되돌렸다: `online_available_at` = 마지막 게시월 말일, 수집 시각은
> `online_collected_at`(KST tz-naive)으로 분리. 아래 "검사 대상을 `online_feature_asof`로 옮겼다"도 구성상 항상 통과하는
> 검사였다 — 실제 검사는 `assert_no_future_posts`(월별 원천 재집계 대조). 아래 2026-10-01 항목 참고.
**문제**: `online_features.build_online_features`가 `online_available_at`에 `origin_end`를 그대로
넣고 있었다. 그러면 `attach_online`의 시점 검사(`online_available_at > origin_end` 금지)가 항상
자기 자신과 비교하는 셈이라 아무것도 검증하지 못한다(항상 0건 통과).

**수정**: 두 시점을 분리했다.
- `online_feature_asof`(=origin_end, 그대로) — feature 창 정의의 경계. **보장하는 것**: 창에 들어간
  게시물의 *내용 시점*(게시월)이 origin_end를 넘지 않는다. `attach_online`이 이제 이 컬럼으로 검사한다
  (이전엔 `online_available_at`으로 검사했다 — 검사 대상 컬럼을 바꿨다).
- `online_available_at` — QA `collected_at`(점포별 실제 원문 수집 시점, ≈2026-09-23)을 그대로 쓴다.
  **보장하지 않는 것**: 이 온라인 데이터 소스가 그 historical origin 시점에 실제로 존재/조회 가능했다는
  것. 원문은 2026-09에 한 번 수집했으므로 과거 모든 origin에 대해 `online_available_at > origin_end`가
  항상 성립하며, 이는 시점 누수가 아니라 **회고적 재구성**(retrospective reconstruction)이라는 뜻이다.
  이 필드가 실제 실시간 가용성을 보장하는 경우는 **수집일 이후의 미래 origin뿐**이다.

즉 이 PR 이후 `attach_online`이 실제로 보장하는 것은 "게시물 내용 시점 ≤ origin_end" 하나뿐이고,
"그 시점에 이 데이터 소스가 운영상 존재했는가"는 애초에 검증 대상이 아니다(백테스트의 근본적 전제이며,
#26·이 문서의 1번 항목이 그 위험의 일부만 진단한다). QA에 `collected_at`이 없는 합성 테스트 등에서는
`online_available_at`이 NaT다(더 이상 origin_end로 대체하지 않는다).

### 4. 이 PR로 바뀌는 산출물
| 대상 | 바뀌는가 | 비고 |
|---|---|---|
| 온라인 predictor 6개 값(`online_blog_*`) | 아니오 | 결측 규칙·집계 로직 불변 |
| `risk_scores.parquet` / `probability_12m` 등 | 아니오 | predictor가 안 바뀌므로 모형 산출물도 불변 |
| `online_available_at` 컬럼 값 | **예** | `origin_end` → 실제 수집 시점(QA `collected_at`). 메타 컬럼이라 predictor·서빙 로직에는 안 쓰인다 |
| `attach_online`의 시점 검사 대상 컬럼 | **예** | `online_available_at` → `online_feature_asof`(검사 결과 자체는 이전에도 항상 통과였으므로 동작 변화 없음) |
| `outputs/online/online_features*.parquet`(기존 파일) | 재생성 전까지 메타만 구식 | predictor 값은 그대로 맞다. `online_available_at`만 옛 의미(origin_end)로 남아 있다 — provenance 정확도 문제일 뿐 정확성 문제는 아니다 |

**#34·#36 재생성 필요 여부: 없음.** 이 PR은 predictor 값이나 모형 확률·등급·기여도 산출을 바꾸지 않는다
(메타 컬럼 하나의 값과, 이미 항상 통과였던 검사의 대상 컬럼만 바꿨다). 온라인 feature 파일의
`online_available_at`을 새 의미로 갱신하려면 `python -m src.data.online_features`를 다시 돌려야 하지만,
이건 **실데이터 2025Q2·2026Q2 전체 재생성(#44·#45 결정 후 일괄 예정)에 자연히 포함**시키면 된다 —
지금 별도로 하지 않는다.

## 2026-09-29 — `first_date_truncated`의 사용 범위 (현황 기록, Issue #25/#33)

**이 항목은 결정이 아니라 현재 상태 기록이다.** 절단 정보가 결측 경로로 미래 정보를 전달하는지는
아직 검증 중이므로, "결측 처리에만 쓰므로 안전"이라고 확정하지 않는다.

- `first_date_truncated`(축B QA, 수집 시점 누적 게시물 수가 200건 상한에 걸렸는지) **자체는
  predictor로 사용하지 않는다.** `export_online_features.py`의 어느 출력에도 실리지 않는다.
- 현재는 월별 sparse 파일의 **결측 재구성에만 쓰인다** — 절단 점포의 `oldest_raw_postdate`
  이전 달을 0이 아니라 NA로 두는 판정(Issue #25). 구현은 이 PR이 아니라 `#33`에 있다.
- **미확정**: 이 결측 경로의 시점 안전성은 `#33`에서 검증·결정 중이다. 절단 여부는 2026-09
  수집 시점 정보이고 실데이터에서 온라인 결측이 사실상 절단으로 발생하므로, NA 패턴 자체가
  미래 정보를 전달할 수 있다는 리뷰가 열려 있다. 최종 처리 방식은 `#33`에서 정한다.

## 2026-09-29 — 온라인 축B `source_snapshot` 원천 식별 (Issue #23 마무리)
- 축B(`online_mentions_monthly.parquet`) `source_snapshot` = raw 파일명 + **raw 파일 바이트 sha256** + `collection_run_id` + 수집 당시 git SHA.
  `feature_asof` = `available_at` = 게시월 말일, 축A 정의, as-of 규칙(`available_at > origin_end` → NA)은 그대로다.
- raw checksum은 **export 시점의 최종 raw 전체**로 계산한다. `--resume`은 같은 raw에 이어 쓰므로 run 종료 시점 checksum은
  나중에 검증할 파일이 남지 않는다. manifest `input_checksum_sha256`은 입력 대상 목록의 해시라 raw 식별에 쓰지 않는다.
  manifest 형식은 바꾸지 않았다(기존 수집분 재수집 불필요).
- run id·git SHA를 찾지 못한 행이 있으면 export를 멈춘다. git SHA는 run의 모든 manifest 기록에서 읽는다(중단 후 이어 받은 run 대응).
- 한계는 그대로: 축B는 과거 글을 수집 시점에 관측한 값이라 삭제된 글이 빠져 있다(#26). 구현·실측은 `DATA_CATALOG.md` §6.

## 2026-09-30 — #45 구현 이슈: 컷오프 정의 문장·fallback 기록·provenance (기본 동작 불변)
- **정의(코드 `bands.CUT_MID_DEFINITION`·`CUT_HIGH_DEFINITION`, run_meta·serve_meta와 같은 문장)**:
  - cut_mid = 1.2 × base_rate — 보정 창 OOF 관측 폐업률의 1.2배인 **개별 예측 확률 임계값**이다("실측 lift 1.2배가 되는 컷오프"가 아니다).
  - cut_high = 보정 창 OOF 예측 확률의 **50~99.5 백분위를 200등분한 격자**를 낮은 쪽부터 훑어, {p ≥ c} 집단이 **200곳 이상**이고 그
    관측 폐업률이 base_rate의 **2배 이상**인 첫 c. 그런 c가 없으면 예측 확률 95백분위로 fallback.
- **fallback 발생 여부**를 `run_meta.band_provenance.high_fallback`(cut_high의 p95 대체)·`mid_fallback`(cut_mid ≥ cut_high여서 base_rate로 대체)에 기록한다.
- **provenance**(`run_meta.band_provenance`, serve가 `serve_meta.cutoff_provenance`로 옮김): 보정 창 origin 목록, base_rate, cut_mid, cut_high, fallback,
  검증 구간 high 비율, high lift와 점포 단위 부트스트랩 95% CI, 확률 척도(raw/calibrated).
  - **2026-10-01 보완(#32 재검증)**: 창 규칙 `window_rule`(현재 `fixed` — 보정 fit 구간 고정 창. #45/#47의 동적 창과 구분),
    컷오프 창 행 수 `n_cutoff_rows`, 목표 lift(`target_high_lift` 2.0 / `target_mid_lift` 1.2), high 최소 집단 `high_min_group_n`(200),
    후보 격자 `high_grid`(50~99.5 백분위·200점·fallback 95백분위), high lift CI 설정 `high_lift_ci`(단위 store_id, B, seed, 백분위 2.5/97.5)를
    함께 남긴다. 값은 `bands`의 상수에서 읽으므로 코드와 기록이 어긋나지 않는다. 기본 동작(컷오프 값)은 바뀌지 않는다.

## 2026-09-30 — PR #34 리뷰 반영: Shapley 배경 안정성·절단 점포 온라인 요인·업력대 경계·문서 정합성
근거: `src/analysis/shapley_background_stability.py`(c87384c), 실행 로그(300점포 2025Q2, 시드 5개). 모든 수치는
이미 저장된 로그·산출물에서 옮겼다(새 계산 없음). **실데이터 전체 재생성(2025Q2·2026Q2)은 #44·#45 결정 후 한 번에
진행하며, 그때 1순위 비율·표시 보류 수를 갱신한다.**

### ① 배경 표본 안정성 (기준 = 무작위 256개 5시드 기여 평균, 대상 300점포)
| 배경 | 1순위 일치율 평균 (최소) | 온라인 부호 일치율 평균 (최소) | 기준값 범위 | 초/1,000점포 |
|---|---|---|---|---|
| 무작위 16 (현행) | 0.750 (0.647) | 0.844 (0.733) | 0.100–0.142 | 37 |
| 무작위 64 | 0.870 (0.850) | 0.911 (0.850) | 0.099–0.122 | 112 |
| 무작위 128 | 0.883 (0.847) | 0.894 (0.770) | 0.097–0.130 | 213 |
| 무작위 256 | 0.940 (0.923) | 0.966 (0.930) | 0.109–0.118 | 517 |
| 층화(업종×자치구) 16 | 0.757 (0.727) | 0.775 (0.690) | 0.086–0.135 | 45 |
| 층화 64 | 0.871 (0.810) | 0.921 (0.850) | 0.110–0.136 | 130 |
| **층화 128** | **0.921 (0.907)** | **0.946 (0.913)** | 0.109–0.121 | 242 |
| 층화 256 (시드 1개) | 0.903 | 0.893 | 0.118 | 494 |
- **권장: 층화 128.** 5시드 모두 두 일치율 ≥ 90%인 가장 빠른 설정이다(무작위 128은 평균 88%로 미달, 무작위 256은
  충족하지만 2배 느림). 현행 16개는 1순위 일치 65–81%로 불안정하다.
- **한계**: (a) 기준이 무작위 256 평균이라 무작위 256 행은 자기 참조로 높게 나온다. (b) 연구 프로세스가 층화 256 두 번째
  시드에서 중단돼 k-means 대표 배경은 실행하지 못했고 요약 CSV·`chosen_background.csv`도 저장되지 않았다 — 권장값은 로그
  집계이며 **아직 서빙에 채택·저장하지 않았다**(재현 인덱스 저장은 재실행 시). (c) 1,000점포당 242초라 전체 29,101점포는
  약 2시간.

### ② 절단 점포 온라인 요인 (feat/w2-serve c38d07e)
전부든 일부든 온라인 feature가 결측인 절단 점포는 `display=false`, `hold_reason=data_missing`,
`missing_reason=online_unobservable`로 보류, 기여값·가법성은 보존한다. 2025Q2 온라인 테이블 기준(저장된 feature 표 집계):
절단 점포 3,931곳 중 전부 결측 272(이미 보류 대상, #36 시험 실행 272와 일치)·일부 결측 **229**(새로 보류)·결측 없음
834(그대로). 리뷰어 인용 48곳과 다르다 — 정의(어느 feature까지 세었는지) 차이로 보이며 전체 재생성 때 진단 산출물로
다시 센다. 2025Q2 서빙 재실행은 프로세스 중단으로 완료되지 않아 진단 산출물 기준 수치는 없다.

### ③ 업력대·months_since_last
- 업력대 경계 `(lo, hi]` → `[lo, hi)`: 12·36·60·120개월 경계 테스트 추가. **구간이 바뀐 점포 수는 아직 집계하지
  않았다**(전체 재생성 때 산출).
- months_since_last NA: has_ever가 확정 0이면 "이력 없음", has_ever도 NA(절단으로 관측 시작 이전을 모름)면 "관측 불가".
  2025Q2 저장된 feature 표: NA 13,712곳 중 **이력 없음 13,419 · 관측 불가 293**. 리뷰어의 7곳과는 범위가 다르다(검증
  표본 기준으로 보이며 이쪽은 전 점포).

### ④ 문서 정합성
- #34 단독 vs #36 최종 차이표는 `diagnose.py` 모듈 docstring과 PR 본문에 둔다(영향 미미 |기여|<0.001, hold_reason·
  missing_reason 코드, driver_code, 절단 점포 보류).
- 비용 요인(임대료 수준)은 **기여 0이 아니라 계산 대상 제외**(`비용_available=False`)다.
- "최고 위험 3개 점포" = 검토 대기(`hold_reason=online_review`) 점포 중 `probability_12m` 상위 3곳, 동률은 store_id
  오름차순(#39 priority와 같은 기준, `name_match_review.select_targets`).

## 2026-10-01 — PR #33 재검증 반영: 절단 경로 재측정·clean 실험·온라인 시점 계약
근거: `src/analysis/online_truncation_sensitivity.py`, `src/data/online_features.py`(`assert_no_future_posts`),
`src/models/train_detect.py`(`attach_online`), 실데이터 rolling OOF 10개 origin(2023Q1–2025Q2), 점포 클러스터 부트스트랩 1,000회.
온라인 predictor 6개의 값·결측 규칙은 바뀌지 않았다(새 코드로 다시 만든 표가 이전 표와 predictor 값 완전 일치).

### 1. 절단 경로는 유의한 정보 경로다 (2026-09-29 항목 1의 결론 정정)
- 옛 "NA 플래그"(온라인 feature 중 하나라도 NA)는 절단 대리 지표가 아니었다: 그 NA 행 293,362개 중 **94.7%(277,794)가
  "언급이 한 번도 없음"**(`months_since_last`가 정의상 NA)이고 절단 점포 행은 5.3%(15,568)뿐이다(`any_na_composition`).
- **절단 전용 플래그**(절단 점포이면서 온라인 feature 중 하나라도 NA, 전체 행의 2.95%)만 base에 더해 다시 쟀다:

  | params | base 합산 AUC | base + 절단 플래그 | 차이 [95% CI] |
  |---|---|---|---|
  | 현 설정 | 0.6013 | 0.6040 | **+0.0027 [+0.0013, +0.0040]** |
  | 튜닝 (0.03, 15) | 0.6112 | 0.6141 | **+0.0029 [+0.0016, +0.0042]** |

  → 수집 시점(2026-09) 절단 여부는 결측 패턴을 통해 **작지만 유의한** 미래 정보 경로다. `na` 기본값은 유지하되 이 한계를
  명시한다. 절단 점포의 폐업률(8.98%)은 비절단(11.71%)보다 낮다.
- `lower_bound`는 **절단 경로 차단이 아니다** — 절단 점포의 미관측 구간을 관측된 글만 센 하한으로 채워 NA는 없어지지만,
  수집 시점 인기(>200건)로 정해지는 절단 점포의 과거 창이 과소 집계되어 절단 영향이 값에 남는다.
  (i) na − (iii) lower_bound: 현 설정 +0.0015 [+0.0002, +0.0027], 튜닝 +0.0011 [+0.0002, +0.0020] — 두 방식은 결측 패턴과
  값이 함께 바뀌므로 이 차이를 경로 크기로 해석하지 않는다.

### 2. clean 실험 — 절단 점포를 학습·평가 모두에서 제외
| params | base | enriched | enriched − base [95% CI] |
|---|---|---|---|
| 현 설정 (전체 점포, 참고) | 0.6013 | 0.6205 | +0.0192 [+0.0168, +0.0217] |
| 현 설정 (clean) | 0.6029 | 0.6197 | **+0.0168 [+0.0143, +0.0193]** |
| 튜닝 (0.03, 15) (전체 점포, 참고) | 0.6112 | 0.6289 | +0.0177 [+0.0153, +0.0200] |
| 튜닝 (0.03, 15) (clean) | 0.6131 | 0.6281 | **+0.0150 [+0.0128, +0.0171]** |

- 합산 AUC. clean에서도 10개 origin 모두 enriched가 높다(`truncation_sensitivity_by_origin.csv`). 온라인 feature의 효과는 절단
  경로가 없는 표본에서도 유지되며, 전체 점포 효과(+0.019) 중 약 0.002–0.003이 절단 점포 쪽에서 온다.
- clean은 민감도 실험이다 — 모집단에서 절단 점포를 지우지 않는다(CLAUDE.md complete-case 금지).
- 평균 AUC(origin별): base 0.6029 → enriched 0.6219(현 설정, 기존 값 그대로 재현). 튜닝 행은 #32 `20cbd95`에서 `TUNED_PARAMS`를
  전체 설정으로 고친 뒤의 값이다(2026-09-29 표의 튜닝 행은 옛 부분 설정 값).
- 처리 정책(절단 경로를 한계로 두고 쓸지, 절단과 무관한 처리를 찾을지)은 **#44(짧은 상호)와 함께** 정한다.

### 3. 온라인 시점 계약 (2026-09-29 항목 3 대체)
| 컬럼 | 의미 | 불변식 |
|---|---|---|
| `online_feature_asof` | origin_end — feature 창의 기준일 | = origin_end |
| `online_available_at` | 창에 들어갈 수 있는 **마지막 게시월의 말일**(축B `available_at` = 게시월 말일, 2026-09-29 #23 항목·DATA_CATALOG §6과 같은 정의) | ≤ origin_end |
| `online_collected_at` | 실제 원문 수집 시각, UTC ISO8601 → **KST tz-naive**(#21 `export_online_features._to_naive_kst`와 같은 처리) | 검사하지 않음 (과거 origin은 모두 뒤 — 회고적 재구성) |

- **실제 시점 검사**: `online_features.assert_no_future_posts`가 월별 원천에서 표와 **다른 계산 경로**(정렬 키 + searchsorted
  누적합)로 origin_end가 속한 달까지의 게시월만 다시 집계해, 값이 있는 칸을 모두 대조한다(창을 한 달만 미래로 밀어도 멈춤).
  CLI가 표를 쓰기 전에 항상 실행한다 — 실데이터 na 2,805,181칸, lower_bound 2,876,979칸 일치.
- `attach_online`은 원천 없이 표만 받으므로: `online_feature_asof == origin_end`(같은 origin 정의), 새 형식 표의
  `online_available_at ≤ origin_end`를 확인한다. 이전 형식 표(`online_collected_at` 없음)는 경고 후 그대로 읽는다 —
  이미 만든 #34·#36 산출물이 깨지지 않게. 전체 재생성(#44·#45 결정 후) 때 새 형식으로 바뀐다.
- 영향 범위: 하위 브랜치(#34 diagnose, #36 serve, #41 report)는 온라인 메타 컬럼을 읽지 않는다(`attach_online`만 호출).
  predictor 값·모형 산출물은 바뀌지 않는다.

### 4. #26 해석과 #44 의존성
- #26 "18개 origin 통과"는 **운영 판정**으로만 둔다 — 관측 기간 차이(구성 효과)와 삭제 편향이 같은 격차에 섞여 있어
  "삭제 편향이 없다"고 결론 내리지 않는다(2026-09-25 항목 갱신 참고).
- 짧은 상호(≤2자) 온라인 feature 처리(keep/NA)는 **#44 미결정** — 이 PR은 정하지 않는다. 기존 민감도(`benchmark
  --short-name-sensitivity`, 튜닝 (0.03, 15), 10개 origin 합산, 위 새 온라인 표로 재실행):

  | 비교 | 합산 AUC 차이 [95% CI] |
  |---|---|
  | HGB enriched: na − keep | −0.0006 [−0.0018, +0.0007] (유의하지 않음) |
  | HGB enriched − base: keep / na | +0.0177 [+0.0152, +0.0201] / +0.0172 [+0.0146, +0.0195] |
  | logit enriched: na − keep | +0.0010 [+0.0003, +0.0018] |

  짧은 상호 점포(17,703행)만 보면 HGB enriched의 base 대비 AUC 이득이 keep +0.0177 → na −0.0026으로 사라진다(전체 성능은
  거의 같다). 어느 쪽이 맞는지는 매칭 오탐률(#39 round2 판정)로 정한다 — #44.
- 서빙 모형 설정은 #45/#47 결정((0.03, 31))을 따르며 이 PR은 바꾸지 않는다. 경쟁지표(#43)도 건드리지 않는다.
