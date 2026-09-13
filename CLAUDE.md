# CLAUDE.md

## Project
2026 통계최강자전 AI·데이터 솔루션 부문

주제: 소상공인 폐업 위험 조기진단 및 개선방향 추천 AI 서비스

## Core Pipeline
1. Detect: 폐업 위험 탐지
2. Diagnose: 위험요인 진단
3. Prescribe: 검증 가능한 개선방향 제시

## Source of Truth
작업 전 아래 문서를 우선 확인한다.
1. `docs/DECISIONS.md`
2. `docs/ANALYSIS_PLAN.md`
3. `docs/DATA_CATALOG.md`
4. `README.md`

문서 간 내용이 충돌하면 임의로 선택하지 말고 충돌 지점을 먼저 보고한다.

## Statistical Principles
- 시간 순서를 반드시 보존한다.
- 미래 정보를 과거 시점의 feature로 사용하지 않는다.
- 현재 시점의 온라인 정보를 과거 분기의 feature로 소급하지 않는다.
- SHAP 값은 인과효과로 해석하지 않는다.
- 예측모형의 feature를 임의로 변경한 결과를 인과적 counterfactual로 표현하지 않는다.
- 아직 폐업하지 않은 사업체는 survival analysis에서 censoring을 고려한다.
- random split 결과만으로 최종 성능을 주장하지 않는다.
- temporal validation을 반드시 수행한다.
- 통계적으로 확인되지 않은 효과를 유의한 것처럼 해석하지 않는다.
- 개선방향은 근거 수준을 명시한다.
- 데이터와 식별 조건이 충분하지 않으면 인과효과를 단정하지 않는다.

## Scope Rules
- Stage 3는 모든 위험요인의 효과를 검증하려 하지 않는다.
- 실제로 대응 가능하고 데이터로 검증 가능한 요인 중 핵심 1개를 우선 분석한다.
- 추가 요인 검증은 핵심 분석이 완료된 뒤 확장한다.
- 복잡한 방법론을 추가하는 것보다 재현성, 누수 방지, 해석의 정확성을 우선한다.

## Development Rules
- 원본 데이터는 Git에 commit하지 않는다.
- API key와 secret은 `.env`에서 관리하고 `.env.example`에는 키 이름만 남긴다.
- 데이터 처리 과정은 가능한 한 스크립트로 재현 가능하게 작성한다.
- notebook에만 핵심 로직을 남기지 말고 재사용 코드는 `src/`로 이동한다.
- 분석 결과를 바꿀 수 있는 중요한 결정은 `docs/DECISIONS.md`에 기록한다.
- 새로운 데이터는 사용 전 `docs/DATA_CATALOG.md`에 기록한다.
- 한 번의 PR에는 가능한 한 하나의 목적만 담는다.

## Before Implementing
분석 방법을 임의로 추가하지 않는다. `docs/ANALYSIS_PLAN.md`와 `docs/DECISIONS.md`를 먼저 확인한다.

데이터 구조상 기존 분석 계획을 적용하기 어렵다면 임의로 우회하지 말고, 문제점과 가능한 대안을 먼저 제시한다.
