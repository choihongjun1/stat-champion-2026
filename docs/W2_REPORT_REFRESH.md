# PR #41 최신 main 계약 정리 (2026-10-02)

기준 main: `d572819` (#36/#51/#53/#55). 기존 `feature/w2-report-serving`의
`eae0531`을 보존한 격리 `codex/w2-report-serving-refresh` worktree에 main을 merge했다.
DECISIONS·requirements 충돌은 독립된 양쪽 기록/의존성을 모두 보존했다.
최종 커밋은 기존 #41 head로 fast-forward push하며 force push/PR merge는 하지 않는다.

## 비교 결과

| 기존 TODO / 계약 | 최신 main 상태 | 이번 반영 |
|---|---|---|
| #36 R1~R3 표시/결측 코드·영향 미미 | 이미 해결 | 별도 구현 없이 기존 입력 계약 유지 |
| #35 미래 score 패널 필요 | master_score 코드가 main에 존재 | 실데이터 재생성은 외부 PC 작업, 기다리지 않음 |
| driver_code 생산자 미구현 | diagnose가 6종 코드 생산, serve가 그대로 전달 | 소비자는 코드 정본 사용, 문구 재분류 제거 |
| default/legacy params 허용 | #51 adopted, canonical HGB, serve adopted fail-fast | release 빌드·export 재검증에 연결 |
| 단일 배경 | #53 S8 두 배경과 sensitivity 계산 | 두 seed·행/index 해시·운영 여부 검증 |
| band_cutoffs 이름 불일치 | #36은 정본 3키, fallback은 cutoff_provenance | 추가 키 거부, release에 base_rate까지 필수 |
| 짧은 상호 처리 미결정 | #55 전 origin 온라인 predictor NA 구현 | 소비자는 data_missing/online_unobservable 유지 |
| sensitivity 출력 옵션 | main reports는 기본 미노출, label도 미출력 | S8 serve 기본 두 필드 노출, diagnose serializer에 label 추가 |

## 버전·factor 계약

최종 리포트/정적 bundle/search/dong/manifest는 **0.3**. serve 입력은 별도 계약 **0.2**를 유지한다.
required 필드가 늘어났으므로 최종 0.2로 두지 않는다.

- `interpretation_sensitive`: boolean, 모든 최종 factor에 필수.
- `sensitivity_label`: false → `""`, true → `"해석 민감"`. JSON Schema 교차 검증.
- 주 배경 contribution/direction/display/explanation을 그대로 보존한다. sensitivity는 보조 경고이며 값 대체가 아니다.
- `driver_code`: 최종 factor에 키 필수, 온라인은 6종 enum, 다른 factor는 null.
  release serve 입력 온라인 factor에는 코드 필수. 코드가 있으면 문구와 독립적으로 그대로 사용한다.
  enum은 decline/lapse/absent/unobservable/no_change/presence. 정책 연결은 표시되고 기여가 양수이며 앞의 3종일 때만.
- 코드 없는 개발 입력만 기존 문구 fallback 사용. sensitivity 없는 옛 serve 0.2는 dev 빌드만 허용하고
  `final_contract=not_ready`로 두어 정적 export를 막는다. 민감도 false를 지어내지 않는다.
- 모델의 위험 확률·등급·배경 규칙은 바꾸지 않았다. 개발용 무작위 배경은 sensitivity 필드를 만들지 않으며 release에서 거부한다.

## adopted/S8 release gate와 provenance

release build는 `params_name=params_contract=adopted`, canonical
`sklearn.ensemble.HistGradientBoostingClassifier`, detect provenance의 adopted와 run_meta sha256,
S8 version `S8-2026-10-01`·참조·stratified 256·comparison을 요구한다.
배경 전체와 primary/sensitivity 각각 operational=true, seed 20260931/20261001, stratified 256,
rows_sha256/index_sha256(64자리 hex), s8_summary/cutoff_provenance를 검사한다.
배경 파일 내용의 해시 대조는 upstream diagnose/serve가 수행하며, builder는 그 provenance 필수값/형식을 검사한다.
band_cutoffs는 정확히 cut_mid/cut_high/base_rate. 인허가 기준일·최종 schema 검증도 계속 필요하다.
export는 DB에 남은 메타 계약과 output_schema_version을 다시 확인한다.

기존 `runs.serve_meta_json`에 **입력 전체를 원문 값 그대로** 보존한다:
params_name/model_class/params_contract/model_params, detect_run_provenance(run_meta sha256 등), s8_rule,
두 배경 seed·rows/index 해시, s8_summary, cutoff_provenance, band_cutoffs. 기존 별도
serve_meta_sha256·band_cutoffs_json도 유지한다. runs 컬럼 변경은 필요 없다.
factors에는 nullable interpretation_sensitive/sensitivity_label 컬럼을 추가한다.
옛 DB는 없는 컬럼을 null로 읽을 수 있으나 새 최종 계약/export는 통과하지 못한다. 원자적 build로 재생성해야 한다.
driver_code 컬럼이 없는 옛 DB의 개발 조립은 기존 문구 fallback을 유지한다.

정적 meta.provenance에는 params_name/model_class/params_contract, S8 rule_version과 두 seed만 노출한다.
내부 경로·해시·상세 비교 요약은 정적 meta에 복사하지 않는다.

검증: 고정 requirements 환경에서 전체 `pytest -q --tb=short` **835 passed, 1 skipped**, 407.23초.
계산 스레드는 OMP/OPENBLAS/MKL=1로 제한했다. report/serve input/adopted·legacy/S8/sensitivity/driver/cutoffs/
SQLite/dong/export/publication guard/합성 샘플 바이트 재현성, 소규모 serve-like→DB→static E2E와
구버전 sensitivity 변환을 포함한다. skip은 기존 테스트의 환경 조건이며 실제 데이터 실행 검증은 아니다.
471 warnings는 기존 pandas/NumPy·지리 처리 경고 등이며 실패는 없다. main 대비 diff에는 새 실데이터 산출물 없음.

## #29 법정동 fallback·min_cell_n

search_index와 dong_summary는 같은 SQLite stores.dong(인허가 법정동)을 쓴다.
동 키는 언제나 `(gu, dong)`이며 dongs.json에도 구를 포함한다.
dong_summary는 gu/dong/biz_type(null=전체)/n_stores/band_share.low,mid,high/top_risk_biz_types/as_of를
이미 제공한다. 숨긴 칸의 count/share는 null이며 순위는 전체 행에만 있다.
검색 실패 → 주소 검색 → dongs.json에서 구+법정동 선택 → dong_summary 행 필터를 지원한다.

REPORT_SCHEMA §12의 기존 인허가 실측: 2026-06-30, 194 업종 칸·67 법정동·28,681점포(+동 결측 30).
당시 search_index 정합성 실측은 name_norm 불일치 0·행정동 표기 0·**3구 법정동 이름 중복 0건**이다.
이 PC에는 licenses_3gu.parquet/원본 인허가가 없어 새 실측을 주장하지 않는다.
합성 테스트는 같은 dong 이름이 여러 gu에 있는 경우도 `(gu,dong)`으로 분리하는 것을 검증한다.

| 후보 | small+보완 숨김 / 194칸 | 숨김 비율 | 숨긴 칸 점포 / 28,681 | UI 영향 동 |
|---|---|---|---|---|
| 3 | 8+4=12 | 6.19% | 65 (0.23%) | 7/67 |
| 5 | 16+5=21 | 10.82% | 111 (0.39%) | 11/67 |
| 10 | 31+5=36 | 18.56% | 242 (0.84%) | 18/67 |
| 20 | 47+7=54 | 27.84% | 728 (2.54%) | 26/67 |
| 30 | 60+9=69 | 35.57% | 1,162 (4.05%) | 33/67 |

근거는 기존 인허가 근사이며 실제 score 패널과 등급 편중 검토는 아직 없다.
10은 유용성/숨김의 **검토용 provisional 후보**로만 제안하며 확정 기본값을 추가하지 않는다.
코드에는 암묵적 숫자 기본값이 없고 export의 `--min-cell-n`은 계속 필수,
상태 기본은 provisional. 숨긴 업종 1칸의 전체−공개 역산은 보완 숨김으로 막는다.
한 등급 100% 칸은 이 하한만으로 막을 수 없어 dong_summary_public_ready=false를 유지한다.

## publication guard와 #48 후속

technical_gate 통과는 공개 승인이 아니다. publication_approved는 항상 false.
실명/주소/store_id/개별 위험도 실제 공개는 별도 승인 전 금지.
실데이터 출력은 private·gitignored 경로만, docs/app/public/dist/site/www는 차단한다.
docs/samples 예외는 sample_synthetic 모델·SAMPLE-NNN·샘플 상호의 완전 합성 번들뿐이다.
가린 실제 결과는 합성으로 취급하지 않는다. 기존 guard 테스트를 유지한다.

#48 head `77bba42`의 코드는 이번 작업에서 수정하지 않는다. 필요한 변경:

1. app/src/lib/reportTypes.ts의 Report._schema_version을 0.3으로 갱신.
   Factor에 interpretation_sensitive/sensitivity_label/driver_code 타입을 추가.
2. BundleMeta에 provenance를 추가하고 meta/search/dongs/dong/report/manifest 버전·run_id가 같은지 확인.
   band_cutoffs의 cut_mid/cut_high 이름과 파일명/리포트 경로는 그대로다.
3. ReportView에서 민감도 배지를 보조 표시하고 주 배경 값·display/hold/missing 코드를 유지.
   ownerText에서 driver 문구로 별도 코드 분류를 하지 말 것.
4. public/bundle의 **합성 사본만** 새 0.3 bundle로 갱신. 실제 결과 복사 금지.
5. DongSummaryRow 타입에 score_origin/as_of를 추가하고 동 리포트 기준일을 표시.
   `(gu,dong)` 필터와 suppressed/null 처리를 유지, dongs.json의 label/구분키는 동일.
6. 로더는 dong_summary의 provisional/public readiness와 publication_approved를 함께 고려.
   검색 실패·주소·동 선택 흐름과 합성 중복 검색을 재확인.

## 실데이터 serve 완료 후 명령 (저장소 루트)

```powershell
$serveDir = 'outputs/serve/2026Q2_enriched' # 최신 serve의 기본 출력, 실제 --out이 다르면 해당 경로 지정
$snapshotDate = '2026-09-11' # 예시가 아닌 실제 인허가 수령 기준일로 반드시 교체
python -m src.serving.build_db --serve-dir $serveDir --licenses outputs/standardized/licenses_3gu.parquet --license-snapshot-date $snapshotDate --purpose release --out outputs/serving/report.sqlite
python -m src.serving.export_static --db outputs/serving/report.sqlite --out outputs/serving/static_private --min-cell-n 10 --min-cell-n-status provisional
```

10은 임시 검토값이며 공개 승인이나 결정된 하한이라는 뜻이 아니다.
serve 출력에는 sensitivity pair가 있어야 한다. 이 변경 이후 S8 serve는 기본으로 출력한다.
이 변경 이전 main으로 재생성 중인 실행은 `--expose-sensitivity`를 켜도 label을 JSON에 쓰지 않았으므로,
추론/재학습 없이 diagnosis.parquet의 **원천 sensitivity_label**을 reports에 결합해야 한다.
`src.serving.upgrade_sensitivity`로 결합 가능(아래 명령). 없는 값을 false로 대체하지 않으며 새 폴더에 쓴다.

```powershell
python -m src.serving.upgrade_sensitivity --serve-dir outputs/serve/2026Q2_enriched --out outputs/serve/2026Q2_enriched_report03
# 위 build_db의 $serveDir을 outputs/serve/2026Q2_enriched_report03로 지정
```

정책/온라인 입력이 준비되면 build_db에 --policies, --online-presence를 명시한다.
입력 파일·수령일이 미확정이라 실제 폴더/날짜만 사용자가 지정해야 한다.
실데이터 SQLite/json은 commit하지 않는다. 대규모 학습/diagnose/serve는 이 작업에서 실행하지 않았다.
