# DASH — 서울 3구 음식점·미용업 폐업 위험 진단 서비스

통계최강자전 2026 AI·데이터 솔루션 부문 출품작의 코드입니다. 팀 DASH(박안석·최홍준·송채영·손유성)가 만들었습니다.

서울 광진·마포·영등포구의 일반음식점·휴게음식점·미용업 인허가 점포를 대상으로, 사장님이 자기 가게의 **향후 12개월 상대 위험 수준**과 그 신호를 보고, 업종·지역 조건에 맞는 지원사업을 찾을 수 있게 하는 서비스입니다.

> 이 저장소(또는 제출 코드 zip)는 보고서 PDF의 근거 코드입니다. 분석 결과와 수치는 보고서를 기준으로 읽어 주세요.

## 서비스 흐름

| 단계 | 하는 일 | 화면에 보이는 것 | 코드 |
|---|---|---|---|
| 1. 탐지 | 인허가·상권·지가·블로그 언급 등으로 12개월 폐업 위험 순위를 매긴다. 모형은 scikit-learn `HistGradientBoostingClassifier`(HGB) | 위험 등급(낮음·주의·높음)과 같은 구·업종 안의 순위. **개인 확률·예측 구간은 보여 주지 않는다** | `src/models/detect.py`, `train_detect.py`, `bands.py` |
| 2. 진단 | 정확한 interventional Shapley로 위험 신호를 나눈다. 비교 기준(배경) 두 개에서 방향이 다른 요인은 "해석 민감"으로 표시 | 위험을 높이는/낮추는 신호와 방향. 숫자 기여도는 보여 주지 않으며, 신호는 원인이 아니다 | `src/models/diagnose.py`, `background.py` |
| 3. 대응 방향 | 통계청 MDIS 소상공인실태조사 2023으로 전자상거래 매출과 영업이익률의 조건부 연관성을 double machine learning으로 추정 | 근거가 충분하지 않아 **"확인 불가"**로 표시하고 개선 방향을 제안하지 않는다 | `src/prescribe/dml_ecommerce.py`, `scripts/run_w3_dml.py` |
| 4. 지원사업 | 위험요인과 연결하지 않고 업종·지역·공고 조건으로만 찾는다(2026-10-03 원문 확인 기준) | 접수 상태·마감·확인일, 저희가 알 수 없는 자격 조건 수 | `src/serving/build_db.py`, `policy_apply.py` |

결정과 그 이유는 `docs/DECISIONS_W3.md`(W3 결정 동결: 사전 규칙 S1–S13, 팀 결정 T1–T8, 추가 결정 A1–A5)에, 보고서·화면에서 쓰는 표현과 쓰지 않는 표현은 `docs/CLAIMS.md`에 있습니다.

## 제출물 구성

| 공고 요건 | 제출물 |
|---|---|
| 보고서(PDF) | 보고서 PDF (본체) |
| 프로토타입 또는 결과물 | 비식별 실제 사례 A·B·C의 정적 화면(`app/out`, HTTP 서버로 실행) + 화면 캡처 + 2분 영상 |
| 활용한 코드 파일 | 이 코드 zip (아래 "포함하지 않은 것" 참고) |

전체 점포 결과는 공개하지 않습니다. 실제 점포는 "비식별 실제 사례 A·B·C"와 구·업종으로만 표시합니다.

## 포함하지 않은 것

- **원자료:** 인허가·상가정보·공시지가·상권 자료 원본, MDIS 마이크로데이터(통계청 이용 조건에 따라 원자료 미포함), 블로그 수집 결과. 출처와 수집 방법은 `docs/DATA_CATALOG.md`에 있습니다.
- **산출물:** `outputs/`(모형·진단·서빙 결과, 실제 점포 번들)
- **수작업 검수 원자료:** `data/manual/`(실제 상호가 들어 있어 제외). 이 때문에 `src/data/matching_validation.py`의 상호 매칭 정밀도 재검증은 재현할 수 없습니다.
- **과거 시험 샘플:** W2 시험 서빙 결과와 화면 개발용 더미(`docs/samples/serve_2025Q2_trial/`, `docs/samples/w2-6_dummy/`)

따라서 **원자료부터 결과까지의 재현은 이 zip만으로는 할 수 없습니다.** 대신 아래처럼 합성 데이터로 코드와 화면 계약을 확인할 수 있습니다.

## 합성 데이터로 확인하기

### 1. Python 테스트

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

- Python 3.13에서 확인했습니다(2026-10-05, 1,235건 통과·1건 skip — skip은 원자료가 있어야 도는 테스트).
- **한국어 Windows에서는 UTF-8 모드로 실행하세요.** 그렇지 않으면 한글 출력을 읽는 테스트 1건이 실패합니다.
  - PowerShell: `$env:PYTHONUTF8 = "1"` 후 실행
  - 명령 프롬프트: `set PYTHONUTF8=1` 후 실행

### 2. 제출 화면 (합성 사례)

```bash
cd app
npm ci
node scripts/export-submission.mjs --src ../docs/samples/submission_bundle
python -m http.server 8000 --directory out
```

브라우저에서 `http://localhost:8000/`을 엽니다.

- 정적 화면은 **HTTP 서버로 열어야 합니다.** `out/index.html`을 파일로 직접 열면 데이터를 불러오지 못합니다.
- 웹 글꼴은 외부(CDN)에서 받습니다. 인터넷이 없으면 기본 글꼴로 보입니다.
- `docs/samples/submission_bundle/`은 **합성 샘플**(`meta.json`의 `data_kind: "synthetic"`)입니다. 화면 제목이나 설명에 "비식별 실제 사례"라는 문구가 나와도 이 샘플의 값은 모두 지어낸 값이며 실제 점포가 아닙니다.
- 개발용 검색 화면(W2 합성 샘플)은 `npm run dev`로 볼 수 있습니다. 자세한 내용은 `app/README.md`를 참고하세요.

### 3. 금지 표현·식별정보 검사

```bash
python scripts/check_claims.py docs/samples/submission_bundle app/src --fail-on warn
```

제출 화면·번들·보고서에 쓰지 않기로 한 표현(개인 확률 등, 목록은 `docs/CLAIMS.md` 2절)과 식별정보 패턴을 찾습니다. 규칙은 `configs/claims_rules.json`, 설명은 `docs/CHECK_CLAIMS.md`에 있습니다. 이 검사는 제출물용이라 소스 코드 전체에 걸면 열 이름(`store_id`)이나 합성 샘플의 가상 주소도 검출됩니다.

## 원자료가 있을 때의 실행 순서

원자료는 포함하지 않으므로 참고용입니다. 각 명령의 인자는 `--help`와 해당 문서를 보세요.

| 순서 | 명령 | 문서 |
|---|---|---|
| 1 | `scripts/run_labels_stage1.py` — 인허가 패널·12개월 폐업 라벨 | `docs/LABEL_SPEC.md` |
| 2 | `python -m src.data.master`, `python -m src.data.master_score`, `python -m src.data.online_features` — 피처 마스터·온라인 지표 | `docs/MASTER_SPEC.md`, `docs/DATA_CATALOG.md` |
| 3 | `python -m src.models.train_detect` — 탐지 모형·보정·등급 경계 | `docs/DECISIONS_W3.md` S9·S10·S13 |
| 4 | `python -m src.models.background`, `python -m src.models.diagnose` — 위험 신호 진단(S8) | S8 |
| 5 | `python -m src.models.serve`, `python -m src.serving.build_db` — 서빙 결과·정본 DB·지원사업 연결 | `docs/REPORT_SCHEMA.md` |
| 6 | `scripts/select_demo_stores.py` — 비식별 사례 A·B·C 선택(사전 규칙·고정 seed) | `docs/DEMO_SELECTION.md` |
| 7 | `python -m src.serving.export_submission` → `app/scripts/export-submission.mjs` → `scripts/w3_16_submission_e2e.py` — 제출 번들·정적 화면·최종 검사 | `docs/SUBMISSION_BUNDLE.md` |
| 별도 | `scripts/run_mdis_stage_a.py`, `scripts/run_w3_dml.py` — MDIS 전처리·대응 방향 분석 | `docs/MDIS_CODEBOOK.md` |
| 별도 | `scripts/build_numbers_registry.py`, `scripts/s10_lift_gate.py`, `scripts/figures_w3.py` — 보고서 수치 등록부·문구 조건·그림 | `configs/numbers_w3.json` |

## 저장소 구조

| 경로 | 내용 |
|---|---|
| `src/data/` | 원자료 정리, 주소·좌표 표준화, 상가정보 연결, 라벨, 피처, MDIS 전처리 |
| `src/models/` | 탐지(HGB)·보정·등급, Shapley 진단, 서빙 |
| `src/prescribe/` | 대응 방향 분석(DML) |
| `src/analysis/` | 보조 분석(상호 길이 부록, 구성 비교, 민감도) |
| `src/serving/` | 정본 DB, 공개·제출 번들, 스키마 검증, 합성 샘플 |
| `scripts/` | 실행 진입점, 금지 표현 검사, 수치 등록부, 사례 선택, 최종 E2E 검사 |
| `app/` | 웹 화면(Next.js, 정적 export) |
| `configs/` | 금지 표현 규칙, 수치 등록부 정의, 재생성 설정 |
| `tests/` | 합성 데이터 테스트 |
| `docs/` | 결정 기록, 명세, 데이터 카탈로그 |

## 문서 안내

- **현재 기준 문서:** `docs/DECISIONS_W3.md`, `docs/CLAIMS.md`, `docs/SUBMISSION_BUNDLE.md`, `docs/REPORT_SCHEMA.md`, `docs/DEMO_SELECTION.md`, `docs/DATA_CATALOG.md`, `docs/CHECK_CLAIMS.md`
- **당시 기록:** `docs/DECISIONS.md`(9월부터의 결정 이력), `docs/ANALYSIS_PLAN.md`(초기 계획), `docs/W1_*.md`, `docs/W2_*.md`. 작성 시점의 판단이 그대로 남아 있어 현재 구현과 다른 부분이 있습니다. 서로 다르면 `docs/DECISIONS_W3.md`가 우선합니다.

## AI 활용

분석 설계와 검토에는 Claude, 코드 작성·실행에는 Claude Code, 독립 코드 검토에는 Codex를 사용했습니다. 작업 원칙은 `CLAUDE.md`에 있습니다. 결과 수치와 결정은 팀원이 확인하고 `docs/DECISIONS_W3.md`와 Issue에 기록했습니다.
