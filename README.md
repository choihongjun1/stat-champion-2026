# 2026 통계최강자전

## 프로젝트
소상공인 폐업 위험 조기진단 및 개선방향 추천 AI 서비스

## 한 문장 정의
“이 가게는 위험합니다”에서 멈추지 않고, 왜 위험한지와 무엇을 바꿀 수 있는지까지 알려주는 서비스입니다.

## Core Pipeline
1. **Detect** — 폐업 위험 탐지
2. **Diagnose** — 주요 위험요인 진단
3. **Prescribe** — 검증 가능한 개선방향 및 지원정책 제시

## Repository Structure
- `src/data/` : 데이터 수집·전처리·패널 구축
- `src/models/` : 폐업 위험 탐지(Detect)·위험요인 진단(Diagnose)·서빙 출력 — W2-2/W2-3 PR(#32~#36)로 추가 예정 (미병합)
- `src/serving/` : 결과 스키마 검증, SQLite 정본, 검색 인덱스, 동 요약, 정적 JSON 번들 (`docs/REPORT_SCHEMA.md`)
- 개선방향 검증(Prescribe, W3)은 아직 코드가 없다
- `app/` : 웹 프로토타입
- `notebooks/` : 탐색적 분석 및 실험
- `tests/` : 테스트 코드
- `docs/` : 분석 설계, 데이터 카탈로그, 주요 의사결정 기록
- `outputs/` : 로컬 산출물 디렉터리(대용량 결과물은 Git에 커밋하지 않음)

## Data Policy
원본 데이터, 대용량 중간 산출물, API key와 secret은 저장소에 커밋하지 않습니다. 데이터 출처와 사용 상태는 `docs/DATA_CATALOG.md`에서 관리합니다.

## AI-assisted Development
Claude Code를 주요 개발 도구로 사용할 예정입니다. 작업 전 `CLAUDE.md`, `docs/DECISIONS.md`, `docs/ANALYSIS_PLAN.md`를 먼저 확인합니다.
