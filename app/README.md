# DASH 웹 (W2-6 화면)

사장님이 가게를 검색하면 상대 위험 수준 리포트를 보여주는 웹 프로토타입입니다.
Next.js + TypeScript + Tailwind CSS. 백엔드 없이 W2-5 **정적 JSON 번들**(공개 계약 `public-static-0.1`)을 읽기만 합니다.

## 데이터 (중요)

- `public/bundle/`(SAMPLE-001~009), `public/bundle_no_policy/`(SAMPLE-010)는 main `docs/samples/w2-5/`의 **합성 샘플 번들 사본**입니다. 모든 값은 지어낸 값입니다.
- **실제 점포 번들은 공개 승인(`publication_approved`) 전에는 `public/`에 넣지도, 커밋하지도, 배포하지도 않습니다** (REPORT_SCHEMA §9). 비식별 시연 번들만 별도 경로에 넣습니다.
- 번들 위치는 환경변수 `NEXT_PUBLIC_BUNDLE_BASE`로 바꿉니다 (기본 `/bundle`).
  - 정책 매칭 미실행 화면 확인: `NEXT_PUBLIC_BUNDLE_BASE=/bundle_no_policy npm run dev`
- `meta.json`의 `data_kind`가 `synthetic_sample`이면 합성 배너, 그 밖이면 비식별 시연 배너를 보여주고 위치는 구까지만, 검색 결과는 주소 없이 표시합니다.
- **제출용 비식별 사례 번들(`submission-static-0.1`, PR #65·REPORT_SCHEMA §15)**: `meta.json`에 `_submission_contract_version`이 있으면 첫 화면이 검색 대신 사례 A/B/C 목록이 되고, `/case/CASE-A/` 등이 같은 리포트 화면으로 사례를 그립니다. 계약에 없는 값(상호·주소·업력·영업 상태·요인 기여값)은 화면에 만들지 않고, `summary_text`는 표시하지 않습니다.

## 실행

```bash
npm install
npm run dev
```

http://localhost:3000 — 테스트 검색어: `가상식당`(2건), `가상카페 화양동`, `가상로`(주소로 찾기), `없는가게`(결과 없음)

정적 HTML 시험: `STATIC_EXPORT=1 npm run build` → `out/` 생성 → `npx serve out`으로 엽니다. (`out/index.html` 더블클릭은 데이터를 불러오지 못합니다.)

### 제출용 정적 export (W3-16)

```bash
npm run export:submission
```

- 입력은 `../outputs/w3_submission/final`(동결된 W3-15 제출 번들, `--src`로 변경)이며 **읽기만** 합니다. 계약 버전·manifest(sha256 앞 12자·바이트)·파일 목록이 맞지 않거나 경로에 `private`가 있으면 멈춥니다.
- `public/submission/`(git 무시)에 잠깐 복사해 `STATIC_EXPORT=1 NEXT_PUBLIC_BUNDLE_BASE=/submission`으로 빌드한 뒤 복사본을 지웁니다.
- `out/`에서 제출 범위 밖 경로(합성 W2 번들 `bundle*`, 점포 리포트·동 화면 자리표시 `report/_`·`dong/_/_`)를 지우고, `out/submission`이 원본과 바이트 단위로 같은지 확인합니다.
- `publication_approved`는 읽지도 바꾸지도 않습니다. 최종 검사는 저장소 루트의 `scripts/w3_16_submission_e2e.py`(렌더 텍스트·main의 `check_claims`·전체 store_id 역검색·번들 무결성)로 합니다.

금지어 검사 (저장소 루트에서): `python scripts/check_claims.py app/src --fail-on warn`

## 폴더 구조

| 경로 | 내용 |
| --- | --- |
| `src/app/page.tsx` | 첫 화면 (온보딩 → 검색) |
| `src/app/report/[storeId]/page.tsx` | 가게 리포트 경로 (정적 export용 경로 목록 포함) |
| `src/app/case/[caseId]/page.tsx` | 제출용 비식별 사례 A/B/C 경로 (`CasePage` → 같은 `ReportView`) |
| `src/lib/screenModel.ts` | 리포트·사례 → 화면 모델 변환 (화면은 이 모델만 읽음) |
| `scripts/export-submission.mjs` | 제출용 정적 export |
| `src/app/dong/[gu]/[dong]/page.tsx` | 동 화면 — 공개 준비 전이라 "동 리포트는 준비 중이에요" |
| `src/components/report/ReportPage.tsx` | 로딩(최소 1.2초) → 리포트 |
| `src/components/report/ReportView.tsx` | 리포트 화면 (피그마 146:6440) |
| `src/lib/reportTypes.ts` | 공개 계약 타입 |
| `src/lib/ownerText.ts` | 위험요인 → 사장님 문장 변환 |
| `src/lib/actions.ts` | 대응 방향 두 축 (분석 근거 / 지원사업) |
| `src/lib/search.ts` | 상호명(+동)·주소 검색 |

## 화면 규칙 요약 (#48 검토 M1~M8, Issue #49)

- 위험은 등급(낮음·주의·높음)과 동종 순위("위험 상위 N%", 순위 50 미만은 "위험 하위 N%")만 보여줍니다. 개인 확률·범위는 공개 리포트에 없습니다.
- 위험요인은 숫자 없이 크기 단계로, 문장은 "모형은 이 점을 … 신호로 봤어요" 틀로 씁니다. 온라인 지표는 "블로그 언급"입니다.
- 대응 방향은 분석 근거("확인 불가")와 지원사업 두 축입니다.
- 지원사업은 위험요인과 연결하지 않고(`linked_factor_ids`는 읽지 않음) 업종·지역·공고 자격 기준으로만 보여줍니다. 운영기관·접수 상태(`apply_status`: 접수 중/접수 마감/접수 상태 확인 필요)·마감일(`apply_end`)·확인일(`checked_at`)을 표시합니다. 신청할 수 있다고 단정하는 문구는 쓰지 않습니다 (T4).
- 카드 문구는 #54 합의(DECISIONS_W3 8-4)를 따릅니다: `matched` "사장님 가게 정보로 보면 신청 조건에 맞아요", `check_required` "조건 N개는 저희가 알 수 없어요. 공고에서 한 번 확인해 주세요"(N = `unverified_condition_count`). `check_required`가 섞여 있으므로 묶음 제목은 "자격 조건이 맞는"이 아니라 "가게 업종·지역으로 찾은 지원사업"입니다.
- 화면 분기는 코드 필드(`display`, `hold_reason`, `missing_reason`, `unavailable_categories`, `policy_matching`, `match_status`)로만 합니다.
