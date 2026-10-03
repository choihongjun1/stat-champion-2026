# Diagnosis speed PoC (2026-10-03)

Experimental only. Based on origin/main bdc3a3e, branch
`perf/w3-diagnosis-speed-poc`. Neither production CLI imports the helper.
No model/calibration/cutoff/schema/serving contract changes, no real-data full run,
no merge/rebase/squash/force push. **Do not use for tonight's H4 regeneration.**

## Code evidence and call graph

* Diagnose CLI `main -> run` (`src/models/diagnose.py:654`): builds features,
  fits the selected detect-run parameters, loads validated manifest backgrounds,
  then `explain_s8 -> explain` twice (lines 616-625, 700).
* Serve CLI `main -> run` (`src/models/serve.py:211`): independently fits HGB
  (269), predicts raw probability, applies existing calibrator/cutoffs (270-285),
  optionally bootstraps (274-280), then calls the same `diagnose.explain_s8`
  (305). `diagnose_meta` validates provenance; it does not load cached contributions.
* `explain -> factor_shapley` (diagnose:420) and
  `explain -> online_drivers -> factor_shapley` (448, 290).
  The latter groups all other features into one participant, plus six online
  features: 128 coalitions. Active outer factors excluding all-NA land price: 256.
* `factor_shapley` (118-150): background index reset and base prediction once;
  target chunk repeats each target K times, background tiled in target-major
  order (137-138). For **every mask**, `big_x.copy()` (141), then absent-factor
  per-column Series assignment (145), then `predict_proba` (146).
  There is no coalition-axis batching. Each prediction is n_chunk * K rows;
  the reshape(n_chunk, K).mean(axis=1) retains background order. Default
  chunk=2000, K=256 gives 512,000 rows per coalition, not 256 small calls per store.
* `DetectModel.predict_proba -> _align` (detect:60-75) does another column
  selection/full copy (64) per prediction, then category-vocabulary alignment.
  Coalition construction does not concat/reindex; concat is downstream long-table
  assembly (diagnose:438). Index resets occur during chunk expansion.
* With C=ceil(N/2000), each Shapley call performs 1 + 2^F*C predictions:
  one base of K rows and 2^F per chunk. One `explain` uses
  (1+256C)+(1+128C)+1 = 3+384C calls including f(X), or 387 when C=1.
  S8 doubles this to 6+768C (774 when C=1); serve adds its initial raw prediction.
  Model refit/bootstrap costs lie outside this benchmark.

## Categorical safety and deterministic ordering

`features.build_X` outputs float64 and category; missing numeric values are NaN.
`DetectModel.fit` uses HGB `categorical_features="from_dtype"`, fixes categories
and drops all-NA columns. In installed sklearn 1.9.1,
`ensemble/_hist_gradient_boosting/gradient_boosting.py:_preprocess_X` uses a fitted
ColumnTransformer/OrdinalEncoder with category columns moved before numeric ones.
Thus raw DataFrame category codes are not a valid replacement for HGB's internal
encoding. This PoC uses the **public estimator predict_proba(DataFrame)**, not
private predictor APIs or an ndarray with guessed category codes.

The helper aligns X/background once, encodes fixed-vocabulary pandas category
codes (-1 remains missing), copies float64 arrays with the same target/background
ordering, overwrites absent-factor slices, and restores category dtype/vocabulary
immediately before prediction. Other numeric input dtypes are rejected.
Weight calculation, coalition order, per-background mean, weighted sum order,
argmax tie-breaking, downstream ranking and JSON generation remain identical.
Factors not covering all fitted columns retain those columns from X, as baseline.

HGB `_raw_predict` calls `_openmp_effective_n_threads` and passes that number to
tree prediction; process parallelism can oversubscribe that OpenMP pool. Windows
`spawn` workers explicitly limit thread pools to 1. Ordered executor.map returns
uneven chunks in submission order, without RNG calls. A fresh pool is created per
Shapley invocation; startup/serialization are included. Models/backgrounds are
copied once per worker by initializer, not shared memory. Parent+child RSS is
sampled every 10ms and counts shared pages repeatedly; it is an approximate
aggregate, not unique physical memory or guaranteed instantaneous peak.
The experimental process helper caps workers at 8, background size at 256 and
per-process target construction chunks at 128 to bound replicated working arrays.

## Skip and reuse decisions

No safe-skip implementation: `online_drivers` runs before data_missing masking.
`driver_feature`, `driver_text` and `driver_code` are stored before the later
mask (459-461, 481-499); `factors_json` still emits driver/code for hidden factors
(376-402). The online-review count is also computed/logged before the missing
override. Skipping rows would change emitted evidence and potentially counts,
even though display=false; preserving only visible text is insufficient.

Diagnose and serve recompute both backgrounds' outer-factor contributions and
online-driver Shapley for overlapping target rows. Overlap is not guaranteed:
diagnose consumes labeled master origin rows, serve consumes the score panel.
Feature content/order/category vocabulary and peer populations may differ.
Future cache candidate: cache raw per-row phi and online participant phi, keyed
by fitted-model artifact/hash (not parameters alone), training/feature hashes,
target feature-row hash, column/category schema, background manifest role/row
hash/order, code/runtime versions and Shapley contract. Validate same-run hashes;
assemble peer percentiles/ranks/QA holds on the actual target population. Keep
primary/sensitivity separately and regenerate S8 joins. Do not cache rendered
factors by store identifier alone. No architecture/cache changes in this PoC.

Future small candidates: reuse the empty coalition base, compute full coalition
from f(X), cache repeated rows, or tune chunk sizes/internal HGB thread counts.
Every candidate still needs exact mean/reduction-order checks and held-evidence
tests before acceptance. Synthetic speedup is not production adoption evidence.

## Reproduction and limits

Use `pip install -r scripts/requirements-diagnosis-speed.txt` (repository-pinned
requirements plus benchmark-only psutil 7.2.2).
`python -m scripts.benchmark_diagnosis_speed --threads 10 --repeats 2 --out work/speed.json`
uses synthetic 32/64/128 targets, K=256, full adopted HGB parameters (400 trees),
one primary background. This host has 16 logical/10 physical cores; the installed
HGB default effective thread count is 10 (verified with `_openmp_effective_n_threads`).
The final benchmark retains 10 threads in the serial baseline/numpy candidates
and caps each process worker at 1. Workers 2/4/6/8 are compared on the same 32 targets,
including separate baseline+process and numpy+process controls. No real data is
read or accepted by the script. Calibration/bands use a fixed synthetic isotonic
fixture and cutoffs, not the operational calibrator. Tests also cover the second
background, all coalition probabilities, driver fields, sensitivity flags,
serialized order, NaN, category order/unknowns and short-name policy output.

Timings are hardware/workload-specific; `--repeats` records every observation and
reports median runtime/max sampled RSS. Profiler adds
overhead. The detailed timing breakdown is measured separately with a
benchmark-only instrumented copy of baseline; cumulative nested pandas timings
must not be summed. Sampled process memory includes startup and IPC. Measured
speedup applies only to this explanation/serialization fixture; model refits,
bootstrap, data I/O and real-world tree depth/distribution are outside scope.
Runtime includes RSS monitoring overhead; process enumeration costs can grow
with worker count. Two observations are insufficient to establish a stable 1.7%
gain. These measurements do not justify production adoption.

The benchmark patches diagnose.factor_shapley only inside a scoped test harness.
This is not a production feature flag and is not thread-safe as an application
integration; no production path calls the harness. Use production defaults for H4.

## Measured results

Windows, 16 logical/10 physical cores. 모델은 full adopted HGB(400 iterations), 배경 256, 바깥 요인 8개/온라인 참가자 7개. 모든 후보가 같은 모델/합성 fixture를 사용했습니다. 기본 serial HGB 10 threads, 각 process 1 thread. 다른 테스트가 끝난 후 후보마다 2번 측정한 중앙값이며 RSS는 두 관측의 최대값입니다. 실제 상호/주소/store_id 및 실데이터를 사용하지 않았습니다. synthetic isotonic calibrator/고정 cutoff의 검증이며 operational calibrator로 실행한 결과가 아닙니다.

|점포|후보|중앙값 초|speedup|peak RSS MiB|확률 diff|기여 diff|band/방향/display mismatch|
|---:|---|---:|---:|---:|---:|---:|---|
|32|baseline|15.229|1.000x|180.4|0|0|0/0/0|
|32|numpy|14.072|1.082x|178.6|0|0|0/0/0|
|32|baseline_processes_2|21.822|0.698x|509.9|0|0|0/0/0|
|32|numpy_processes_2|21.352|0.713x|508.1|0|0|0/0/0|
|32|baseline_processes_4|21.311|0.715x|836.6|0|0|0/0/0|
|32|numpy_processes_4|20.951|0.727x|832.8|0|0|0/0/0|
|32|baseline_processes_6|22.343|0.682x|996.6|0|0|0/0/0|
|32|numpy_processes_6|21.872|0.696x|989.3|0|0|0/0/0|
|32|baseline_processes_8|22.449|0.678x|1154.9|0|0|0/0/0|
|32|numpy_processes_8|22.091|0.689x|1152.1|0|0|0/0/0|
|64|baseline|25.670|1.000x|195.1|0|0|0/0/0|
|64|numpy|24.468|1.049x|191.0|0|0|0/0/0|
|128|baseline|47.766|1.000x|216.7|0|0|0/0/0|
|128|numpy|46.988|1.017x|207.1|0|0|0/0/0|

프로파일 outer Shapley 8.105초, online detail Shapley 4.074초, serialization 0.072초. pandas 누적 copy 0.670초, 열 대입 0.476초, concat 0.002초. 누적 시간은 중복 가능하므로 합산하지 않습니다. 프로파일은 timing benchmark와 별도 측정이며 instrumentation overhead가 포함됩니다.

초기 1-thread pilot의 4.88x는 기본 HGB 10-thread baseline과의 비교가 아니므로 채택 근거/전체 시간 환산에 사용하지 않았습니다. 실데이터 성능과 오늘 밤 H4 실행 시간은 미측정입니다.


Validation: all test files across three invocations: 973 passed, 1 skipped (149 related + 817 remaining + 7 new). No failures.
