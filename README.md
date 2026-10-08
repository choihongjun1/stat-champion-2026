# DASH — 서울 3구 음식점·미용업 폐업 위험 진단 서비스

통계최강자전 2026 AI·데이터 솔루션 부문 출품작의 코드입니다(팀 DASH: 박안석·최홍준·송채영·손유성).
서울 광진·마포·영등포구의 일반음식점·휴게음식점·미용업을 대상으로 **폐업 위험 탐지 → 위험 신호 진단 → 대응 방향 → 지원사업 안내**까지 이어지는 AI·데이터 솔루션 프로토타입입니다.

> 이 저장소(또는 제출 코드 zip)는 보고서 PDF의 근거 코드입니다. 분석 결과와 수치는 보고서를 기준으로 읽어 주세요.

## 1. Overview

- **입력:** 인허가 점포, 소상공인시장진흥공단 상가정보, 서울 상권분석서비스, 개별공시지가, 블로그 언급(온라인 존재감), 공개 지원사업 공고.
- **출력:** 점포의 향후 12개월 **상대 위험 등급**(낮음·주의·높음)과 같은 구·업종 안의 순위, 위험을 높이거나 낮추는 신호, 업종·지역으로 찾은 지원사업.
- **공개 범위:** 공개 화면은 **비식별 실제 사례 CASE-A/B/C**뿐입니다. 전체 점포 결과는 공개하지 않으며, **개별 확률과 예측 구간(CI)은 공개하지 않습니다.**
- **분리 원칙:** 지원사업은 위험요인과 연결하지 않고 업종·지역·공고 자격 조건으로만 안내합니다.

## 2. What is included

- 데이터 전처리·표준화, 상가정보 연결, 공간조인, 폐업 라벨 — `src/data/`
- master panel(학습용 `master_base`, 예측용 `master_score`) 생성 — `src/data/master*.py`, `docs/MASTER_SPEC.md`
- 폐업 위험 탐지(HGB·보정·등급 경계) — `src/models/`
- 위험요인 진단(Shapley) — `src/models/diagnose.py`
- 온라인 존재감 수집·feature, 경쟁지표 — `src/data/`
- 지원사업 데이터 — `data/policies/20261003/`
- serving DB·정적 번들·정적 화면 — `src/serving/`, `app/`
- submission 검증 도구(금지 표현·식별정보 검사, 최종 E2E) — `scripts/`
- 대응 방향 분석(MDIS, 조건부 연관) — `src/prescribe/`, `scripts/run_w3_dml.py`

## 3. Key design rules

1. **시간 누수 방지.** feature는 예측 기준 시점에 알 수 있던 정보만 쓰고, 상가정보 스냅샷 합본은 점포 연결(ER)에만 씁니다. 현재 시점의 온라인 정보를 과거 분기에 소급하지 않으며, 검증은 시간 분할로 합니다.
2. **라벨과 모집단.** 폐업 라벨은 인허가 폐업일자이고, 모집단은 인허가 전체 점포입니다(매칭 실패 점포를 지우는 complete-case 분석 금지).
3. **개별 확률·CI 비공개.** 공개 화면과 제출 번들에는 등급과 동종 순위만 있고 점포별 확률·예측 구간은 없습니다.
4. **인과로 해석하지 않음.** Shapley 신호는 모형 예측을 나눈 값이고, MDIS 분석(DML)은 조건부 연관입니다.
5. **지원사업은 위험요인과 분리.** `linked_factor_ids`는 항상 빈 배열이며, 업종·지역·공고 자격 조건으로만 찾습니다.
6. **짧은 상호.** `normalize_name` 기준 2자 이하 상호는 블로그 매칭이 부정확해 온라인 지표를 모든 시점에서 결측으로 두고, 화면에는 "온라인 관측 불가"로 표시합니다.
7. **해석 민감.** 비교 기준(배경) 두 개에서 **방향 또는 표시 상태**가 다른 요인은 "해석 민감"으로 표시합니다(S8).
8. **제출 범위와 확인 방법.** static submission은 CASE-A/B/C만 공개합니다. 정적 화면은 파일을 직접 여는 것이 아니라 **HTTP 서버**를 통해 확인합니다.

결정의 배경은 `docs/DECISIONS.md`, `docs/DECISIONS_W3.md`에 있습니다.

## 4. Environment

- Python 3.12 이상, Node.js 22 이상
- 한국어 Windows에서는 UTF-8 모드로 실행하세요(PowerShell `$env:PYTHONUTF8 = "1"`, 명령 프롬프트 `set PYTHONUTF8=1`). 그렇지 않으면 한글 출력을 읽는 테스트가 실패합니다.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS·Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
cd app
npm ci
cd ..
```

## 5. Quick start

원자료 없이 **합성 샘플**로 코드와 화면 계약을 확인합니다. 아래 순서로, 3번만 `app/`에서 실행합니다.

**1. 테스트** (저장소 루트). 원자료나 zip에서 뺀 샘플이 있어야 도는 테스트만 정상 skip됩니다.

```bash
python -m pytest -q
```

**2. 합성 제출 번들을 정적 화면으로 export**

```bash
cd app
node scripts/export-submission.mjs --src ../docs/samples/submission_bundle
```

**3. HTTP 서버로 열기** (`app/`에서)

```bash
python -m http.server 8000 --directory out
```

브라우저에서 `http://localhost:8000/`을 엽니다. `out/index.html`을 파일로 직접 열면 데이터를 불러오지 못합니다. 웹 글꼴은 외부(CDN)에서 받으므로 인터넷이 없으면 기본 글꼴로 보입니다. 이 샘플은 합성 값(`meta.json`의 `data_kind: "synthetic"`)이며, 화면에 "비식별 실제 사례"라는 문구가 나와도 실제 점포가 아닙니다.

**4. 금지 표현·식별정보 검사 — 저장소 루트에서 실행** (3번을 했다면 `cd ..`)

```bash
python scripts/check_claims.py README.md docs/samples/submission_bundle app/src --fail-on warn
```

제출물에 쓰지 않기로 한 표현(`docs/CLAIMS.md`)과 식별정보 패턴을 찾습니다. 규칙은 `configs/claims_rules.json`, 설명은 `docs/CHECK_CLAIMS.md`에 있습니다. 소스 코드 전체에는 걸지 않습니다(열 이름 등이 검출됩니다).

## 6. Real-data pipeline

원자료는 포함하지 않으므로 참고용입니다. 인자는 `--help`와 문서를 보세요(표의 명령은 저장소 루트에서 실행).

| 단계 | 실행 명령·모듈 | 문서 |
|---|---|---|
| 1. 인허가 전처리·주소·PNU·좌표 | `python -m src.data.standardize` | `docs/DATA_CATALOG.md` §1 |
| 2. 상가정보 ER | `python -m src.data.semas` → `python -m src.data.matching` | `docs/DATA_CATALOG.md` §2 |
| 3. 공간조인·공시지가·라벨 | `python -m src.data.spatial_join`, `python -m src.data.landprice`, `python scripts/run_labels_stage1.py` | `docs/LABEL_SPEC.md`, §3–4 |
| 4. **온라인 수집** (API 키 필요, 키 이름은 `.env.example`) | `python src/data/build_targets.py` → `python src/data/collect_online_presence.py` → `python src/data/collect_blog_monthly.py` | `docs/DATA_CATALOG.md` §6 |
| 5. 온라인 feature export | `python src/data/export_online_features.py` → `python -m src.data.online_features` | `docs/DATA_CATALOG.md` §6 |
| 6. master_base / master_score | `python -m src.data.master`, `python -m src.data.master_score` | `docs/MASTER_SPEC.md` |
| 7. detect | `python -m src.models.train_detect` | `docs/DECISIONS_W3.md` S9·S10·S13 |
| 8. diagnose | `python -m src.models.background` → `python -m src.models.diagnose` | `docs/DECISIONS_W3.md` S8 |
| 9. serving DB | `python -m src.models.serve` → `python -m src.serving.build_db` | `docs/REPORT_SCHEMA.md` |
| 10. submission bundle | `python scripts/select_demo_stores.py` → `python -m src.serving.export_submission` → `node app/scripts/export-submission.mjs` → `python scripts/w3_16_submission_e2e.py` | `docs/DEMO_SELECTION.md`, `docs/SUBMISSION_BUNDLE.md` |

별도: MDIS 전처리·대응 방향 분석 `scripts/run_mdis_stage_a.py`, `scripts/run_w3_dml.py`(`docs/MDIS_CODEBOOK.md`), 보고서 수치 등록부 `scripts/build_numbers_registry.py`(`configs/numbers_w3.json`).

## 7. Repository structure

| 경로 | 내용 |
|---|---|
| `src/data/` | 원자료 정리, 주소·좌표 표준화, 상가정보 연결, 라벨, 피처, 온라인 수집, MDIS 전처리 |
| `src/models/` | 탐지(HGB)·보정·등급, Shapley 진단, 서빙 |
| `src/serving/` | 정본 DB, 공개·제출 번들, 스키마 검증, 지원사업 연결, 합성 샘플 |
| `scripts/` | 실행 진입점, 금지 표현 검사, 사례 선택, 최종 E2E 검사, 코드 zip 생성 |
| `app/` | 웹 화면(Next.js, 정적 export) |
| `docs/` | 결정 기록, 명세, 데이터 카탈로그, 합성 샘플(`docs/samples/`) |
| `data/policies/20261003/` | 공개 공고 기반 지원사업 원천(저장소에 포함되는 유일한 데이터) |
| `tests/` | 합성 데이터 테스트 |

그 밖에 `src/prescribe/`(DML), `src/analysis/`(보조 분석), `configs/`(검사 규칙·재생성 설정)가 있습니다.

## 8. Support-program source

공개 공고 기반 지원사업 원천은 `data/policies/20261003/`에 포함되어 있으며(기준일 2026-10-03), 출처·선정 기준·hash는 `docs/DATA_CATALOG.md` §7을 참고하세요. 점포 정보가 없는 공고 정리본이며, 인허가·상가정보·MDIS 같은 원자료는 포함하지 않습니다.

## 9. Submission and reproducibility

| 공고 요건 | 제출물 |
|---|---|
| 보고서 | 보고서 PDF (본체) |
| 프로토타입·결과물 | CASE-A/B/C의 정적 화면(HTTP 서버로 실행) + 비식별 캡처 + 2분 영상 |
| 활용한 코드 | 이 코드 zip |

```bash
python scripts/make_code_zip.py --ref <태그 또는 커밋> --out ../dash_code.zip
```

- zip은 `scripts/make_code_zip.py`로 만들며(`--out`은 저장소 밖), 커밋된 내용만 담깁니다.
- 제출 제외 범위는 `.gitattributes`의 `export-ignore`로 고정돼 있습니다: `data/manual/`(수작업 검수 원자료), `outputs/`(모형·서빙 결과), `notebooks/`, `docs/drafts/`, 과거 시험 샘플(`docs/samples/serve_2025Q2_trial/`, `docs/samples/w2-6_dummy/`). 상호 매칭 정밀도 재검증은 이 제외 때문에 재현할 수 없습니다.
- zip 생성 뒤 스크립트가 금지 경로·금지 파일, 필수 파일, 풀린 zip 안의 `check_claims`를 검사합니다. 하나라도 FAIL이면 종료 코드 1이며 그 zip은 제출하지 않습니다.
- 지원사업 원천 `data/policies/20261003/policies.json`, `data/policies/20261003/policies_apply.csv`는 필수 파일입니다.
- **원자료부터 결과까지의 재현은 이 zip만으로는 할 수 없습니다.** 최종 공개 범위는 CASE-A/B/C이며, 전체 점포 결과는 포함하지 않습니다. 자세한 계약은 `docs/SUBMISSION_BUNDLE.md`를 보세요.

## 10. Documentation

| 문서 | 내용 |
|---|---|
| `docs/ANALYSIS_PLAN.md` | 분석 계획(초기) |
| `docs/DATA_CATALOG.md` | 데이터 출처·시점·지원사업 원천 |
| `docs/DECISIONS.md` | 9월부터의 주요 결정 이력 |
| `docs/DECISIONS_W3.md` | W3 최종 결정(사전 규칙·팀 결정) |
| `docs/REPORT_SCHEMA.md` | report schema |
| `docs/CLAIMS.md` | 공개 표현·claims 기준 |
| `docs/CHECK_CLAIMS.md` | 금지 표현·식별정보 검사기 |
| `docs/SUBMISSION_BUNDLE.md`, `docs/DEMO_SELECTION.md` | 제출 번들 계약, 사례 선택 규칙 |
| `docs/MASTER_SPEC.md`, `docs/LABEL_SPEC.md`, `docs/MDIS_CODEBOOK.md` | master·라벨·MDIS 명세 |

`docs/DECISIONS.md`와 `docs/W1_*.md`·`docs/W2_*.md`는 작성 시점의 기록이라 현재 구현과 다를 수 있으며, 다르면 `docs/DECISIONS_W3.md`가 우선합니다. 분석 설계·검토에는 Claude, 코드 작성·실행에는 Claude Code, 독립 코드 검토에는 Codex를 썼고 작업 원칙은 `CLAUDE.md`에 있습니다.
