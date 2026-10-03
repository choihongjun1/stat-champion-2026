# AUC 0.7 feasibility study — experimental results

Production/H4 미변경. 8,000개 점포, 96,625개 과거 행의 제한 실험이다.
선택된 모형은 **CatBoost-ordinal + temporal+B**이며 validation AUC **0.620100**,
final AUC **0.655947**이다. 같은 enriched 표본 HGB 대비 delta는 validation **+0.019640**,
final **+0.027161**이다. 이것을 production 평균 0.627057에 더하면 안 된다.

## Baseline 재검증

- stored origin metrics 재집계: enriched **0.6270572378**, base **0.6092674621**.
- enriched 범위 **0.605646–0.645541**, 표준편차(ddof=0) **0.013461**; base 범위 **0.586820–0.624900**, 표준편차 **0.012337**.
- 평가 origin 2023Q1–2025Q2, 10개, 총 295,095개 점포-origin 행. n/positive rate는 hash 일치 master와 대조 완료.
- master/online SHA256 모두 production run_meta와 일치. adopted parameter 및 embargo=4/min_train_origins=4 일치.
- 제공된 OOF 원본 SHA256/590,190행 확인 완료. adopted config의 enriched 295,095행에서 10개 origin AUC를 재계산하여 stored metrics와 1e-12 이내 일치했다. base는 stored origin metrics 재집계 검증이다.
- 월별 online 원천 독립 시점 검증: 527,934행, 값이 있는 2,639,242칸 통과. 원천을 다시 쓰거나 production feature를 재생성하지 않았다.

## Origin 편차

enriched 10개 중 5개가 0.62–0.64, 0.68 이상은 0개다. 2023 평균 0.630072,
2024 평균 0.618001, 2025 평균 0.639141로 최근에 계속 하락하지 않는다.
12개월 online count 비결측률과 AUC의 origin 수준 Pearson 상관은 0.26186이다.
10개 시점의 단순 상관이므로 인과효과나 coverage 병목으로 해석하지 않는다.
`months_since_last` NA에는 언급 이력 없음도 포함된다. 모든 NA를 수집 실패로 해석하지 않았다.

## 실험 설계와 공정 비교

2024Q1–Q4 validation에서 feature group 및 model을 선택하고 selection.json을 저장한 뒤
2025Q1–Q2 final을 한 번 평가했다. 최종 결과에 따른 재튜닝 없음.
Production과 같은 rolling origin/4분기 embargo; fixed SHA256 entity sample, 모든 과거 행 유지.
표본 enriched HGB는 validation 0.600460, final 0.628786이다.
Full-population production HGB의 같은 기간 평균(0.618001/0.639141)과 다르다.
학습 표본 감소와 adopted min_samples_leaf=200 유지가 HGB 비교에 영향을 줄 수 있으므로
모델 간 delta가 full-population에서도 유지된다고 주장하지 않는다. CI/표본 반복 검증은 수행하지 않았다.

A1: 인구/점포당 매출/점포수의 4·8분기 slope 및 최근/평균 비율(9개).
A2: 같은 계열 volatility/peak decline(6개). A3: online 3개월 count의 같은 파생(5개).
B: 인허가 면적 결측(1개). temporal+B: A1+A2+A3+B(21개).
상권 밖/coverage count/미래 QA/ER/현재 검색 순위/구조적 지가 결측 proxy는 제외했다.
이는 제한적인 missingness 실험이며 모든 후보의 효과가 없다는 뜻이 아니다.
정확한 분기 lag, 최소 3개 관측값, 미래/backfill 없음; train-only 범주 사전/전체 NA 컬럼 제거.

## Enriched 결과

모든 delta는 같은 phase·표본 enriched HGB 기준이며 mean은 origin macro 평균이다.

| model            | feature_set   |   feature_count |   mean_auc_final |   mean_auc_validation |   delta_final |   delta_validation |   min_auc_final |   min_auc_validation |   max_auc_final |   max_auc_validation |
|:-----------------|:--------------|----------------:|-----------------:|----------------------:|--------------:|-------------------:|----------------:|---------------------:|----------------:|---------------------:|
| CatBoost-native  | baseline      |              25 |         0.649803 |              0.617923 |      0.021017 |           0.017463 |        0.641013 |             0.598096 |        0.658593 |             0.632473 |
| CatBoost-native  | temporal+B    |              46 |         0.656014 |              0.619718 |      0.027228 |           0.019258 |        0.647957 |             0.602708 |        0.664071 |             0.635135 |
| CatBoost-ordinal | baseline      |              25 |         0.656147 |              0.617998 |      0.027362 |           0.017537 |        0.648070 |             0.602646 |        0.664225 |             0.635571 |
| CatBoost-ordinal | temporal+B    |              46 |         0.655947 |              0.620100 |      0.027161 |           0.019640 |        0.650989 |             0.605954 |        0.660906 |             0.636149 |
| HGB              | A1            |              34 |         0.626248 |              0.599010 |     -0.002538 |          -0.001450 |        0.619585 |             0.591840 |        0.632910 |             0.611152 |
| HGB              | A2            |              31 |         0.632208 |              0.598621 |      0.003423 |          -0.001840 |        0.624395 |             0.582318 |        0.640022 |             0.614785 |
| HGB              | A3            |              30 |         0.627694 |              0.600138 |     -0.001092 |          -0.000323 |        0.623019 |             0.584447 |        0.632368 |             0.616330 |
| HGB              | B             |              26 |         0.628786 |              0.600460 |      0.000000 |           0.000000 |        0.623566 |             0.584229 |        0.634005 |             0.615594 |
| HGB              | baseline      |              25 |         0.628786 |              0.600460 |      0.000000 |           0.000000 |        0.623566 |             0.584229 |        0.634005 |             0.615594 |
| HGB              | temporal+B    |              46 |         0.631368 |              0.603816 |      0.002582 |           0.003356 |        0.626377 |             0.593310 |        0.636359 |             0.621688 |
| LightGBM         | baseline      |              25 |         0.627996 |              0.602311 |     -0.000790 |           0.001851 |        0.624300 |             0.593351 |        0.631692 |             0.617210 |
| LightGBM         | temporal+B    |              46 |         0.632881 |              0.608222 |      0.004095 |           0.007762 |        0.628825 |             0.599522 |        0.636937 |             0.623448 |

validation 선택과 final 관측 최고치는 다를 수 있다. 채택 후보는 selection.json의 사전 선택을 따른다.
Final에서만 높은 모형을 새 winner로 선택하지 않았다. base-only 대조군도 별도 CSV에 포함했다.

2024Q1–2025Q2 여섯 origin 전체의 **설명용** macro 평균(선택/검증 구분은 위 표 유지):

| model            | feature_set   |   mean_auc |   min_auc |   max_auc |     delta |
|:-----------------|:--------------|-----------:|----------:|----------:|----------:|
| CatBoost-native  | baseline      |   0.628550 |  0.598096 |  0.658593 |  0.018648 |
| CatBoost-native  | temporal+B    |   0.631817 |  0.602708 |  0.664071 |  0.021914 |
| CatBoost-ordinal | baseline      |   0.630714 |  0.602646 |  0.664225 |  0.020812 |
| CatBoost-ordinal | temporal+B    |   0.632049 |  0.605954 |  0.660906 |  0.022147 |
| HGB              | A1            |   0.608089 |  0.591840 |  0.632910 | -0.001813 |
| HGB              | A2            |   0.609817 |  0.582318 |  0.640022 | -0.000085 |
| HGB              | A3            |   0.609323 |  0.584447 |  0.632368 | -0.000579 |
| HGB              | B             |   0.609902 |  0.584229 |  0.634005 |  0.000000 |
| HGB              | baseline      |   0.609902 |  0.584229 |  0.634005 |  0.000000 |
| HGB              | temporal+B    |   0.613000 |  0.593310 |  0.636359 |  0.003098 |
| LightGBM         | baseline      |   0.610873 |  0.593351 |  0.631692 |  0.000971 |
| LightGBM         | temporal+B    |   0.616442 |  0.599522 |  0.636937 |  0.006540 |

Production origin별 상세:

| origin   | feature_set   |     n |   base_rate |      auc |
|:---------|:--------------|------:|------------:|---------:|
| 2023Q1   | base          | 29509 |    0.113830 | 0.586820 |
| 2023Q2   | base          | 29730 |    0.128860 | 0.614886 |
| 2023Q3   | base          | 29827 |    0.129681 | 0.620509 |
| 2023Q4   | base          | 29704 |    0.125337 | 0.624900 |
| 2024Q1   | base          | 29797 |    0.127362 | 0.610133 |
| 2024Q2   | base          | 29397 |    0.118720 | 0.593857 |
| 2024Q3   | base          | 29421 |    0.117535 | 0.599230 |
| 2024Q4   | base          | 29391 |    0.126399 | 0.604236 |
| 2025Q1   | base          | 29218 |    0.118044 | 0.613575 |
| 2025Q2   | base          | 29101 |    0.114635 | 0.624529 |
| 2023Q1   | enriched      | 29509 |    0.113830 | 0.605646 |
| 2023Q2   | enriched      | 29730 |    0.128860 | 0.635470 |
| 2023Q3   | enriched      | 29827 |    0.129681 | 0.638804 |
| 2023Q4   | enriched      | 29704 |    0.125337 | 0.640368 |
| 2024Q1   | enriched      | 29797 |    0.127362 | 0.628098 |
| 2024Q2   | enriched      | 29397 |    0.118720 | 0.609180 |
| 2024Q3   | enriched      | 29421 |    0.117535 | 0.610842 |
| 2024Q4   | enriched      | 29391 |    0.126399 | 0.623882 |
| 2025Q1   | enriched      | 29218 |    0.118044 | 0.632740 |
| 2025Q2   | enriched      | 29101 |    0.114635 | 0.645541 |

선택 모형과 sampled baseline의 origin별 상세:

| phase      | origin   | model            | feature_set   |    n |   positive_rate |      auc |
|:-----------|:---------|:-----------------|:--------------|-----:|----------------:|---------:|
| validation | 2024Q1   | HGB              | baseline      | 5461 |        0.128365 | 0.599701 |
| validation | 2024Q2   | HGB              | baseline      | 5396 |        0.118792 | 0.584229 |
| validation | 2024Q3   | HGB              | baseline      | 5387 |        0.117134 | 0.602318 |
| validation | 2024Q4   | HGB              | baseline      | 5375 |        0.128930 | 0.615594 |
| validation | 2024Q1   | CatBoost-ordinal | temporal+B    | 5461 |        0.128365 | 0.622991 |
| validation | 2024Q2   | CatBoost-ordinal | temporal+B    | 5396 |        0.118792 | 0.605954 |
| validation | 2024Q3   | CatBoost-ordinal | temporal+B    | 5387 |        0.117134 | 0.615306 |
| validation | 2024Q4   | CatBoost-ordinal | temporal+B    | 5375 |        0.128930 | 0.636149 |
| final      | 2025Q1   | HGB              | baseline      | 5332 |        0.117592 | 0.623566 |
| final      | 2025Q2   | HGB              | baseline      | 5280 |        0.110227 | 0.634005 |
| final      | 2025Q1   | CatBoost-ordinal | temporal+B    | 5332 |        0.117592 | 0.650989 |
| final      | 2025Q2   | CatBoost-ordinal | temporal+B    | 5280 |        0.110227 | 0.660906 |

## Label quality sensitivity

모든 master 행의 maturity_cutoff_used_months=1이다. 추가 maturity 등급/폐업일 불확실성을
구분할 신뢰할 만한 기록을 사용하지 못했으므로 **정식 mature-label AUC는 산출하지 않았다**.
2025 origin 제외 production enriched 평균은 **0.624036**로 전체 0.627057보다 높지 않다.
이는 recency sensitivity이며 라벨 정제 효과가 아니다.
age>=24 subset 결과는 아래와 같고 업력/모집단 민감도이며 label noise upper bound로 주장하지 않는다.

| model            | feature_set   |   mean_auc |   min_auc |   max_auc |
|:-----------------|:--------------|-----------:|----------:|----------:|
| CatBoost-native  | baseline      |   0.642633 |  0.634788 |  0.650478 |
| CatBoost-native  | temporal+B    |   0.648662 |  0.638672 |  0.658652 |
| CatBoost-ordinal | baseline      |   0.650651 |  0.641382 |  0.659919 |
| CatBoost-ordinal | temporal+B    |   0.651418 |  0.643033 |  0.659803 |
| HGB              | A1            |   0.618083 |  0.613209 |  0.622958 |
| HGB              | A2            |   0.626587 |  0.618801 |  0.634372 |
| HGB              | A3            |   0.620463 |  0.615780 |  0.625146 |
| HGB              | B             |   0.622874 |  0.617495 |  0.628253 |
| HGB              | baseline      |   0.622874 |  0.617495 |  0.628253 |
| HGB              | temporal+B    |   0.625814 |  0.625737 |  0.625891 |
| LightGBM         | baseline      |   0.620864 |  0.617704 |  0.624023 |
| LightGBM         | temporal+B    |   0.623861 |  0.619867 |  0.627855 |

## 검증과 한계

테스트: experiment 5개, existing online/trdar 31개, models 31개, 총 67개 통과.
기존 NumPy timedelta deprecation warning 1개. 데이터와 코드 기본 경로 변경 없음.
CatBoost 1.2.10, LightGBM 4.7.0, sklearn 1.9.1, pandas 2.3.3, numpy 2.5.1.
설치/실험은 격리 worktree의 .venv에서만 수행. records/predictions/실존 식별자는 산출물·Git에 저장하지 않았다.
Full train/diagnose/serve/Shapley/export/calibration/cutoff 실행 없음. merge/rebase/squash/force push 없음.

## 판단 및 후속 검증

**판단: 0.7 unlikely with current data (이번 제한 실험 범위).**
HGB feature 추가의 validation 최대 0.603816/final 최대 0.632208,
alternative model validation 최대 0.620100/final 관측 최대 0.656147이다.
선택 모형의 6-origin 설명용 평균도 0.632049이며 0.7 근거는 없다.
이는 수학적인 성능 상한 또는 모든 미실험 feature의 불가능성을 증명하지 않는다.
CatBoost의 개선은 추가 검증 가치가 있으나 validation/final 차이와 표본 의존성이 크다.
현재 production 교체를 권고하지 않는다. 향후 별도 승인 범위에서 더 큰 표본의 paired 검증,
entity-cluster 불확실성, 추가 시점 외부 검증, calibration/운영 계약 검증이 필요하다.
오늘 밤 H4에는 적용하지 않는다.
