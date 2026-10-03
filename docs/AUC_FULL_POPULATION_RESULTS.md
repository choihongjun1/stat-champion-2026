# Full-population confirmatory experiment — PR #68

**FULL_POPULATION_IMPROVEMENT_PARTIALLY_REPRODUCED**

## 1. Execution

Completed. 2026-10-04T01:18:12.855866+09:00 to 2026-10-04T01:20:24.551063+09:00 (KST); 131.703s total, 107.235s fitting.
All 44,067 eligible historical master entities / 527,934 rows; no sampling or complete-case deletion.
12 model/origin checkpoints; 2025Q1/Q2 evaluated once per fixed model. Validation results did not change settings.

## 2. Inputs and reference

- Fetched main/reference: `4585e99486880eb28d88df6628dc3a93dd965acf`.
- Local main remained `d572819bccb9a09fb5138f239db25ec6006b20b4`; no checkout/reset or commit on main.
- PR #68 at start: `17e6a0117007a2194f43dc554fa53fbad2c111de`; clean worktree.
- Execution code commit: `df1714cef81681ee06de77a820122f92c606e329`; results commit follows without changing executable code.
- Existing sampled result files: `docs/AUC_FEASIBILITY_AGGREGATES.json`, `docs/AUC_FEASIBILITY_RESULTS.md`.
- Original sampled run directory is not locally available; committed aggregates retain origin-level metrics and runtimes.
- master SHA256: `4a5d15a057ad7827e84397f731bcd70d5ca0a9996daa97174259277a7ac34ea9` (read-only, unchanged after run).
- online SHA256: `2d0f8cc3227cb95118061d679f224ec70ef4fb73ee00a4c098a8596be0688820` (read-only, unchanged after run).
- monthly SHA256: `a0a746038b769f1f408fd57d6555b5e79f244d4ba00d97b956afc2880dbd73d0` (read-only, unchanged after run).
- OOF recorded SHA256: `19c73ea24b1c274c03366e47cb577e296cef69f539195c840110484f7ff92eec`. Original parquet unavailable locally, so this hash was **not reread** in this run.
- Prior #68 audit recomputed adopted enriched OOF AUC on 590,190 source rows. Here the freshly fitted full-population HGB matches all six reference origin AUCs exactly (maximum difference 0.0), independently checking the comparator.
- Production provenance: adopted enriched detect_v0; matching master/online hashes, stored macro metrics, origin counts and label rates; embargo=4/min_train_origins=4.

## 3. Frozen models

HGB: sklearn HistGradientBoostingClassifier; log_loss, learning_rate=0.03, max_iter=400, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0, early_stopping=False, random_state=20260922. 25 enriched baseline predictors.
CatBoost ordinal: iterations=400, learning_rate=0.03, depth=6, l2_leaf_reg=3, Logloss, random_seed=20260922, thread_count=4, allow_writing_files=False. 46 predictors: same 25 plus frozen A1/A2/A3/B temporal and area-missingness features.
Both settings and features are unchanged from #68. Existing 2024 winner reused; no alternative family, search, subgroup tuning, calibration or cutoff changes. Four threads per fit.
Versions: numpy=2.5.1, pandas=2.3.3, scikit-learn=1.9.1, catboost=1.2.10, pyarrow=25.0.1, threadpoolctl=3.7.0.

## 4. Validation

| Origin | n_train | n_eval | Positive rate | HGB AUC | CatBoost AUC | Delta | HGB s | CatBoost s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024Q1 | 232,839 | 29,797 | 0.127362 | 0.628098 | 0.630605 | +0.002507 | 5.203 | 9.454 |
| 2024Q2 | 262,348 | 29,397 | 0.118720 | 0.609180 | 0.613703 | +0.004523 | 5.515 | 10.156 |
| 2024Q3 | 292,078 | 29,421 | 0.117535 | 0.610842 | 0.616662 | +0.005820 | 5.953 | 11.094 |
| 2024Q4 | 321,905 | 29,391 | 0.126399 | 0.623882 | 0.628587 | +0.004705 | 6.437 | 12.047 |

Macro mean: HGB **0.618001**, CatBoost **0.622389**, delta **+0.004389**.
Delta variance (ddof=0): 0.0000014262; range [+0.002507, +0.005820].

## 5. Final

| Origin | n_train | n_eval | Positive rate | HGB AUC | CatBoost AUC | Delta | HGB s | CatBoost s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025Q1 | 351,609 | 29,218 | 0.118044 | 0.632740 | 0.635129 | +0.002388 | 6.985 | 12.938 |
| 2025Q2 | 381,406 | 29,101 | 0.114635 | 0.645541 | 0.653985 | +0.008443 | 7.609 | 13.844 |

Macro mean: HGB **0.639141**, CatBoost **0.644557**, delta **+0.005416**.
Delta variance (ddof=0): 0.0000091656; range [+0.002388, +0.008443].

## 6. Paired and sampled comparison

| Phase | Sampled delta | Full delta | Full minus sampled | Delta retained |
|---|---:|---:|---:|---:|
| validation | +0.019640 | +0.004389 | -0.015251 | 22.3% |
| final | +0.027161 | +0.005416 | -0.021746 | 19.9% |

Six-origin paired mean delta +0.004731; variance 0.0000042404; range [+0.002388, +0.008443].
CatBoost wins 6/6 origins: validation 4/4 and final 2/2. Improvement direction agrees across phases, but the limited-sample advantage contracts by roughly 78–80%. 2025Q2 delta is larger than 2025Q1; earlier origins also have positive aggregate deltas, so the improvement is not confined to the latest origin.
The business-type diagnostics below were predefined and descriptive; no settings changed after observing them.

| Phase | Business type | Mean within-business delta | Min | Max |
|---|---|---:|---:|---:|
| final | 미용업 | +0.002679 | -0.008781 | +0.014139 |
| final | 일반음식점 | +0.005550 | +0.004856 | +0.006243 |
| final | 휴게음식점 | +0.005298 | -0.003728 | +0.014324 |
| validation | 미용업 | -0.008940 | -0.019545 | -0.004689 |
| validation | 일반음식점 | +0.005709 | +0.004075 | +0.007136 |
| validation | 휴게음식점 | -0.004825 | -0.019246 | +0.004170 |

General restaurants improve consistently within business type. Beauty and refreshment restaurants have negative validation means and mixed final origins; aggregate wins do not imply uniform gains for every business. No subgroup optimization followed.
No sampled delta was added to a production AUC. Pooled AUC was not in the original experiment protocol and is not introduced. The sampled/full cohorts share historical observations; this is fixed-configuration confirmation on the same historical period, not independent prospective validation. No uncertainty interval or significance claim is made.

## 7. Leakage audit

Independent monthly-source reaggregation: **2,639,242 nonmissing cells**, **527,934 rows**.
Future-month inclusions 0; leakage violations 0; category leakage 0; target leakage 0. Full-population future-value/target mutation preserved prior derived features. Category vocabularies came only from each training fold; unknown evaluation categories retained missing handling.
Exact calendar lags, minimum observation rules, no future backfill; no geometry/QA/ER/current search-rank missingness feature added. Existing production predictor contract retained. Area missingness remains experiment-only. Retrospective source collection/geometry snapshot biases remain inherited limitations; temporal guards do not eliminate them.

## 8. Runtime, memory and resume

Preparation 24.328s; total 131.703s; total fitting 107.235s. Peak process working set **2.750 GiB**; the same cumulative process peak is recorded per origin, not an incremental fit peak.
Origin runtimes are in the validation/final tables. Completed --resume returned without fits or evaluations, and all checkpoint/result bytes remained unchanged. Unit tests also confirm no duplicate fit and fail-closed behavior for an uncertain interrupted evaluation.
44 tests passed: original experiment, new confirmation/checkpoint/aggregate/leakage guards, existing online and trdar tests. Production source was untouched; full production pytest was not required by the authorized protocol.

## 9. Conclusion

**FULL_POPULATION_IMPROVEMENT_PARTIALLY_REPRODUCED**

Positive direction reproduces across all six origins, but the magnitude shrinks substantially (retaining 22.3% validation / 19.9% final). This is a sample/time-dependent improvement. It does not support production replacement. The descriptive classification rule was committed before evaluation and was not altered after final results.

## 10. Production impact

**NO_PRODUCTION_CHANGE**

- adopted model unchanged; cutoff unchanged; calibration unchanged; serving unchanged.
- CASE-A/B/C unchanged; no selection/reselection; adapter untouched; frozen submission untouched.
- no production train/diagnose/serve/Shapley/export regenerated; no schema changes.
- 390 available local production/result artifact byte hashes unchanged. Standard local W3 freeze directories were absent and were neither read nor written.
- main unchanged; Draft maintained; no merge/ready/force push.

Full aggregate-only artifacts, checkpoints and logs are under this worktree `outputs/experiments/auc_full_population/` and its adjacent `_logs`/`_integrity` directories (gitignored). Run metadata records code/input hashes, versions, timestamps and provenance. Original inputs and individual predictions/entities are never committed.
