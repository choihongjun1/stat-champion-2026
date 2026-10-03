# AUC feasibility study (frozen before candidate evaluation)

Production/H4 remains unchanged. This opt-in study never calibrates, searches cutoffs,
diagnoses, serves, or exports records. User authorization: offline small/medium benchmark.

Baseline gate: adopted parameters, master SHA256, stored macro AUC, origin sample sizes
and positive rates must match production artifacts. Prediction-level OOF verification
requires the original parquet separately. No full-population fitting is permitted.

Select at most 8,000 entities by a fixed outcome-independent SHA256 ordering; retain all
their historical rows. Every configuration receives the same rows, labels and embargo=4
rolling splits. Validation: 2024Q1–Q4; final: 2025Q1–Q2. Freeze features and parameters
before validation; select HGB feature group once, then compare alternative models on
baseline and that group. Lock winner before final results. No final-driven retuning.

A1: slopes over exact 4/8 calendar quarters and current/4-quarter mean for population,
sales per store and store counts. A2: four-quarter volatility and peak decline. A3:
same temporal summaries of blog count, only if hash-matching online input is supplied.
Require at least three finite observations; no imputation/backfill across time or stores.
Existing master as-of validity is inherited; derived features cannot repair historical
source collection bias. They add no later-origin observations.

B: area missing only, from the existing license predictor. Exclude polygon/outside-area,
sales coverage, competition coverage and observed-count proxies for prohibited geometry;
exclude structural land-price period flags, current online presence and truncation QA.
Short-name policy and observability cannot be inferred from generic online NaNs.
Thus B tests a narrow safe indicator, not every suggested missingness hypothesis.

Models: adopted HGB unchanged; CatBoost 400 iterations/.03/depth6/l2=3, native and
train-vocabulary ordinal categories; LightGBM 400/.03/31 leaves/min child200/lambda1.
One fixed config each. Train-only category vocabularies and all-NA column removal.
Numeric NaNs preserved; categorical missing/unseen mapped to a fixed token for CatBoost.
Four threads per fit. Dependencies for alternatives are optional, experiment-only.

Age>=24 subset metrics are a population sensitivity, not proof of mature labels or a
noise-free upper bound. Earlier production origins provide a separate descriptive
recency comparison. Closure-date ambiguity cannot be excluded without reliable metadata.

Report origin macro mean/min/max and delta against the same sampled HGB baseline;
never subtract full-population production AUC from a sample candidate as a fair delta.
No entity identifiers, predictions, names or addresses are written. Aggregate results
only. Final evaluation origins were previously used by production; this is exploratory,
not a newly prospective untouched cohort. Any production adoption needs external validation.

## Reproduction

Install `python -m pip install -r experiments/requirements-auc.txt` in an isolated environment.
Run `python scripts/audit_auc_baseline.py --master <master> --run <detect-dir> --out <audit-dir>`
with optional `--oof <original-oof-parquet>` for prediction-level verification.
Run `python scripts/benchmark_auc_improvement.py --master <master> --baseline-dir <detect-dir>
--online <online-features> --out <new-private-directory> --max-stores 8000`.
Omit `--online` for the separate base-only control study. Do not adapt settings after final results.
The output directory must be empty; all outputs are aggregates. Numeric artifacts may be
copied to a user deliverable directory; never copy row-level inputs or predictions into Git.

## Authorized full-population confirmation (2026-10-04)

The full-population follow-up supersedes the earlier sample-size restriction ONLY for
`python -m experiments.full_population`. The original bounded runner remains unchanged.
Use all eligible master rows/history, the hash-matching enriched online table and the
same label, min_train_origins=4, embargo=4, 2024Q1-Q4 validation and 2025Q1-Q2 final.
No sampling or new hyperparameter/feature search is performed. The existing validation
winner is frozen: CatBoost-ordinal + temporal+B (46 features), compared against adopted
HGB enriched baseline (25 features). Configurations and seed match #68 unchanged.
No optional third model is needed for this primary confirmation.

Independent full-population monthly online reaggregation, future-feature/target mutation
invariance, train-only categorical vocabularies, hashes, reference population counts and
label rates gate training. No geometry/QA/ER/current-rank missingness indicators are added.
Area missingness remains experiment-only. Future reconstruction/collection bias remains
an inherited limitation; these temporal guards do not establish prospective validity.

Checkpoint each model/origin atomically under worktree outputs/experiments/auc_full_population.
Resume requires identical input/source/config/version fingerprint. Complete origins are never
refit; an uncertain interrupted origin fails closed rather than silently repeating final
assessment. Metrics and predefined biz_type subgroup AUCs are aggregates only; predictions
and identifiers are not written. No pooled AUC is introduced (original protocol uses macro AUC).
Peak RAM is process-lifetime peak working set, not a per-fit incremental measurement.

The descriptive conclusion rule is fixed before full evaluation: REPRODUCED requires
positive phase deltas, CatBoost wins in at least 5 of 6 origins, and each phase retains at
least 50% of the sampled improvement. PARTIALLY_REPRODUCED covers positive delta in at
least one phase without meeting that criterion; otherwise NOT_REPRODUCED. This is an
operational description, not a statistical significance test or a production adoption rule.
Failed/incomplete runs are RUN_INCOMPLETE and do not receive a performance conclusion.
All production model/cutoff/calibration/serving/selection/submission paths are unchanged.
