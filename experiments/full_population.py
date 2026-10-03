"""One fixed full-population confirmation of #68; aggregate-only, research-only.

No sampling, tuning, calibration, cutoff search, production training entrypoint,
serving, diagnosis, selection, or submission/export entrypoint is invoked.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from threadpoolctl import threadpool_limits
from experiments.auc_features import temporal_features, SOURCES
from scripts.benchmark_auc_improvement import fit_predict, matrix, SEED
from src.models import features, detect
from src.models.splits import rolling_origin_folds
from src.models.train_detect import load_master, attach_online
from src.data.online_features import load_monthly, assert_no_future_posts

ROOT = Path(__file__).resolve().parents[1]
MODELS = (('HGB', 'baseline'), ('CatBoost-ordinal', 'temporal+B'))
VALIDATION = ('2024Q1', '2024Q2', '2024Q3', '2024Q4')
FINAL = ('2025Q1', '2025Q2')
CATBOOST_PARAMS = dict(iterations=400, depth=6, learning_rate=.03, l2_leaf_reg=3,
                      loss_function='Logloss', random_seed=SEED, thread_count=4,
                      allow_writing_files=False, verbose=False)
KST = timezone(timedelta(hours=9))


def now():
    return datetime.now(KST).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def peak_mb():
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(kernel.GetCurrentProcess()),
                                                  ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize / 2**20
        return None
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**20 if os.sys.platform == 'darwin' else 1024)


class Checkpoints:
    """Complete model/origin records are immutable; uncertain interrupted fits fail closed."""
    def __init__(self, root, run_fingerprint):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.fingerprint = run_fingerprint

    def evaluate(self, key, function):
        path = self.root / (key + '.json')
        if path.exists():
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved['fingerprint'] != self.fingerprint:
                raise ValueError('checkpoint fingerprint mismatch')
            if saved['status'] != 'completed':
                raise ValueError('interrupted origin has uncertain evaluation state; automatic refit forbidden')
            return saved['result']
        atomic_json(path, dict(status='started', fingerprint=self.fingerprint, started_at=now()))
        result = function()
        atomic_json(path, dict(status='completed', fingerprint=self.fingerprint, result=result, ended_at=now()))
        return result


def aggregate(results, sampled):
    """Origin macro metrics and paired deltas; never adds deltas to production AUC."""
    rows = pd.DataFrame(results)
    expected = {(model, fs, origin) for model, fs in MODELS for origin in VALIDATION + FINAL}
    actual = list(zip(rows.model, rows.feature_set, rows.origin))
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('incomplete or duplicate origin metrics')
    comparisons = []
    metrics = []
    for phase in ('validation', 'final'):
        part = rows[rows.phase.eq(phase)]
        base = part[part.model.eq('HGB')].set_index('origin')
        candidate = part[part.model.eq('CatBoost-ordinal')].set_index('origin')
        if not (base.n_eval == candidate.n_eval).all() or not (base.positive_rate == candidate.positive_rate).all():
            raise ValueError('models evaluated different population')
        delta = candidate.auc - base.auc
        for origin, value in delta.items():
            comparisons.append(dict(phase=phase, origin=origin, delta=float(value),
                                    winner='CatBoost-ordinal' if value > 0 else 'HGB' if value < 0 else 'tie'))
        for model, fs in MODELS:
            z = part[part.model.eq(model)]
            metrics.append(dict(phase=phase, model=model, feature_set=fs, mean_auc=float(z.auc.mean()),
                                min_auc=float(z.auc.min()), max_auc=float(z.auc.max()),
                                std_auc_ddof0=float(z.auc.std(ddof=0))))
        comparisons.append(dict(phase=phase, origin='macro', delta=float(delta.mean()),
            delta_variance_ddof0=float(delta.var(ddof=0)), delta_min=float(delta.min()), delta_max=float(delta.max()),
            candidate_wins=int((delta > 0).sum()), origins=len(delta), sampled_delta=sampled[phase],
            full_minus_sampled_delta=float(delta.mean() - sampled[phase]),
            delta_retention_fraction=float(delta.mean() / sampled[phase])))
    d = [r['delta'] for r in comparisons if r['origin'] != 'macro']
    macros = [r for r in comparisons if r['origin'] == 'macro']
    # Descriptive rule frozen before evaluation: both phases positive, >=5/6 wins,
    # and at least half the sampled improvement retained in EACH phase.
    if all(r['delta'] > 0 and r['delta_retention_fraction'] >= .5 for r in macros) and sum(x > 0 for x in d) >= 5:
        conclusion = 'FULL_POPULATION_IMPROVEMENT_REPRODUCED'
    elif any(r['delta'] > 0 for r in macros):
        conclusion = 'FULL_POPULATION_IMPROVEMENT_PARTIALLY_REPRODUCED'
    else:
        conclusion = 'FULL_POPULATION_IMPROVEMENT_NOT_REPRODUCED'
    return dict(metrics=metrics, comparisons=comparisons, mean_delta_all_origins=float(np.mean(d)),
                delta_variance_all_origins_ddof0=float(np.var(d)), delta_range=[float(min(d)), float(max(d))],
                conclusion=conclusion, interpretation='Descriptive paired macro AUC, no statistical significance claim',
                production_impact='NO_PRODUCTION_CHANGE')


def sampled_deltas():
    data = json.loads((ROOT / 'docs/AUC_FEASIBILITY_AGGREGATES.json').read_text(encoding='utf-8'))
    z = pd.DataFrame(data['summary'])
    z = z[(z.input_family == 'enriched') & (z.subset == 'all') & (z.model == 'CatBoost-ordinal')
          & (z.feature_set == 'temporal+B')]
    return dict(zip(z.phase, z.delta))


def verify_baseline(meta, frame, directory):
    if meta['params'] != detect.ADOPTED_PARAMS or meta['embargo'] != 4 or meta['min_train_origins'] != 4:
        raise ValueError('adopted production configuration mismatch')
    if meta['primary_feature_set'] != 'enriched':
        raise ValueError('expected enriched adopted reference')
    stored = pd.read_csv(directory / 'oof_metrics_by_origin.csv')
    summary = pd.read_csv(directory / 'sensitivity_summary.csv').set_index('feature_set')
    for fs in ('base', 'enriched'):
        origins = stored[stored.feature_set.eq(fs)]
        if len(origins) != 10 or origins.origin.duplicated().any():
            raise ValueError('baseline origin contract mismatch')
        if abs(origins.auc.mean() - summary.loc[fs, 'all_auc_mean']) >= 1e-12:
            raise ValueError('baseline macro mismatch')
        for row in origins.itertuples():
            y = frame.loc[frame.origin.eq(row.origin), 'event_12m']
            if len(y) != row.n or abs(y.mean() - row.base_rate) >= 1e-12:
                raise ValueError('baseline population or label mismatch')
    return stored


def online_audit(frame, online_path, monthly_path):
    panel = frame[['store_id', 'origin', 'origin_end']]
    online = pd.read_parquet(online_path)
    if online.duplicated(['store_id', 'origin']).any() or len(online) != len(panel):
        raise ValueError('online population mismatch')
    aligned = panel[['store_id', 'origin']].merge(online, on=['store_id', 'origin'], how='left', validate='1:1')
    if aligned.online_feature_asof.isna().any():
        raise ValueError('missing online population keys')
    result = assert_no_future_posts(aligned, panel, load_monthly(monthly_path))
    return dict(checked_non_missing_cells=result['cells_checked'], rows=result['rows'],
                future_month_violations=0, leakage_violations=0, method='independent monthly searchsorted reaggregation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('master', 'online', 'monthly', 'baseline_dir', 'out'):
        parser.add_argument('--' + field.replace('_', '-'), type=Path, required=True)
    parser.add_argument('--oof', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to((ROOT / 'outputs/experiments').resolve()):
        parser.error('output must be inside this worktree outputs/experiments; production destinations forbidden')
    if out.exists() and any(out.iterdir()) and not args.resume:
        parser.error('nonempty run directory; use resume, never overwrite or adaptive rerun')
    start = time.monotonic(); started = now()
    meta = json.loads((args.baseline_dir / 'run_meta.json').read_text(encoding='utf-8'))
    hashes = {name: digest(path) for name, path in dict(master=args.master, online=args.online, monthly=args.monthly).items()}
    if hashes['master'] != meta['master_sha256'] or hashes['online'] != meta['online_sha256']:
        raise ValueError('production input hash mismatch')
    sources = ['experiments/full_population.py', 'experiments/auc_features.py', 'scripts/benchmark_auc_improvement.py',
               'src/models/detect.py', 'src/models/features.py', 'src/models/splits.py', 'src/models/train_detect.py',
               'src/data/master_schema.py', 'src/data/online_features.py']
    versions = {p: importlib.metadata.version(p) for p in ('numpy', 'pandas', 'scikit-learn', 'catboost', 'pyarrow', 'threadpoolctl')}
    frozen = dict(input_hashes=hashes, source_hashes={p: digest(ROOT / p) for p in sources}, versions=versions,
                  hgb_params=detect.ADOPTED_PARAMS, catboost_params=CATBOOST_PARAMS, models=MODELS,
                  validation=VALIDATION, final=FINAL, embargo=4, min_train_origins=4,
                  selection='Existing #68 2024 winner locked: CatBoost-ordinal temporal+B; no search',
                  sampled_deltas=sampled_deltas(), classification='both positive, >=5/6 wins, each retains >=50% sampled delta',
                  reference_meta_sha256=digest(args.baseline_dir / 'run_meta.json'),
                  reference_metrics_sha256=digest(args.baseline_dir / 'oof_metrics_by_origin.csv'),
                  reference_summary_sha256=digest(args.baseline_dir / 'sensitivity_summary.csv'),
                  oof_recorded_sha256=meta['oof_predictions_sha256'])
    fp = fingerprint(frozen)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / 'run_meta.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['fingerprint'] != fp:
            raise ValueError('resume input/code/config/version fingerprint mismatch')
        if manifest.get('status') == 'completed':
            print('Already completed; no model/origin reevaluated.', flush=True)
            return
        started = manifest['started_at']
    else:
        manifest = dict(fingerprint=fp, frozen=frozen, started_at=started, status='running',
            git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            main_reference_sha=subprocess.check_output(['git', 'rev-parse', 'origin/main'], cwd=ROOT, text=True).strip(),
            input_paths={name: str(path.resolve()) for name, path in vars(args).items() if isinstance(path, Path)},
            oof_verification='recorded provenance and prior #68 recomputation audit; original file unavailable' if not args.oof
                              else 'original hash and adopted prediction-level verification in this run',
            population='all production eligible historical master rows; no sampling')
        atomic_json(manifest_path, manifest)
    results = []
    session_start = now()
    try:
        frame = load_master(args.master)
        stored = verify_baseline(meta, frame, args.baseline_dir)
        if args.oof:
            if digest(args.oof) != meta['oof_predictions_sha256']:
                raise ValueError('OOF hash mismatch')
            oof = pd.read_parquet(args.oof, columns=['origin', 'config', 'y', 'p_oof'])
            if len(oof) != meta['oof_predictions_rows']:
                raise ValueError('OOF rows mismatch')
            oof = oof[oof.config.eq('현 설정')]
            for r in stored[stored.feature_set.eq('enriched')].itertuples():
                z = oof[oof.origin.eq(r.origin)]
                if len(z) != r.n or abs(roc_auc_score(z.y, z.p_oof) - r.auc) >= 1e-12:
                    raise ValueError('OOF adopted AUC mismatch')
            del oof
        audit = online_audit(frame, args.online, args.monthly)
        frame = attach_online(frame, args.online)
        extra, groups = temporal_features(frame)
        raw = features.select_features(frame.columns, 'enriched')
        if raw != meta['feature_sets']['enriched']:
            raise ValueError('production feature contract mismatch')
        features.assert_predictors_only(raw)
        selected = raw + groups['A1'] + groups['A2'] + groups['A3'] + groups['B']
        if len(raw) != 25 or len(selected) != 46 or groups['B'] != ['exp_area_missing']:
            raise ValueError('frozen #68 feature family mismatch')
        # Full-population future mutation, including target values, must not change earlier derived cells.
        causal = frame[['store_id', 'origin', 'area', *SOURCES, 'online_blog_cnt_3m']].copy()
        later = causal.origin.isin(FINAL)
        causal.loc[later, list(SOURCES) + ['online_blog_cnt_3m']] = 999999.
        causal['event_12m'] = 1
        altered, _ = temporal_features(causal)
        pd.testing.assert_frame_equal(extra.loc[~later], altered.loc[~later])
        del causal, altered
        frame = pd.concat([frame, extra], axis=1)
        del extra
        sets = {'baseline': raw, 'temporal+B': selected}
        folds = rolling_origin_folds(frame, min_train_origins=4, embargo=4)
        folds = {f.test_origins[0]: f for f in folds}
        if any(o not in folds for o in VALIDATION + FINAL):
            raise ValueError('missing frozen validation/final origins')
        audit.update(category_leakage_violations=0, target_leakage_violations=0,
                     full_population_future_invariance_passed=True, area_missingness='experiment-only, no promotion',
                     derived_features=selected[len(raw):], category_audits=[])
        manifest.update(rows=len(frame), stores=int(frame.store_id.nunique()), features=sets,
                        folds=[folds[o].describe() for o in VALIDATION + FINAL], preparation_seconds=time.monotonic()-start)
        atomic_json(manifest_path, manifest)
        atomic_json(out / 'leakage_audit.json', audit)
        print(json.dumps(dict(stage='prepared', rows=len(frame), stores=manifest['stores'],
                             seconds=round(time.monotonic()-start, 2), peak_mb=peak_mb())), flush=True)
        checkpoints = Checkpoints(out / 'checkpoints', fp)
        for phase, origins in (('validation', VALIDATION), ('final', FINAL)):
            for model, fs in MODELS:
                for origin in origins:
                    fold = folds[origin]
                    def evaluate():
                        fit_start = time.monotonic(); fit_at = now()
                        train, evaluate_mask = fold.masks(frame)
                        cats = features.fit_categories(frame.loc[train], raw)
                        # Verify vocabularies against training only, never union with evaluation categories.
                        for col, vocabulary in cats.items():
                            actual = sorted(frame.loc[train, col].dropna().astype(str).unique().tolist())
                            if vocabulary != actual:
                                raise ValueError('category vocabulary leakage')
                        xt = matrix(frame.loc[train], sets[fs], cats, raw)
                        xe = matrix(frame.loc[evaluate_mask], sets[fs], cats, raw)
                        y_train = frame.loc[train, 'event_12m'].to_numpy()
                        y = frame.loc[evaluate_mask, 'event_12m'].to_numpy()
                        with threadpool_limits(limits=4):
                            predictions = fit_predict(model, xt, y_train, xe)
                        auc = float(roc_auc_score(y, predictions))
                        subgroup = []
                        businesses = frame.loc[evaluate_mask, 'biz_type'].astype(str).to_numpy()
                        for biz in sorted(set(businesses)):
                            mask = businesses == biz
                            if len(np.unique(y[mask])) == 2:
                                subgroup.append(dict(biz_type=biz, n=int(mask.sum()), positive_rate=float(y[mask].mean()),
                                                     auc=float(roc_auc_score(y[mask], predictions[mask]))))
                        return dict(model=model, feature_set=fs, phase=phase, origin=origin,
                            n_train=int(train.sum()), n_eval=int(evaluate_mask.sum()),
                            train_positive_rate=float(y_train.mean()), positive_rate=float(y.mean()), auc=auc,
                            seconds=time.monotonic()-fit_start, started_at=fit_at, ended_at=now(),
                            process_peak_mb=peak_mb(), feature_count=len(sets[fs]), subgroups=subgroup,
                            train_only_category_count={c: len(v) for c, v in cats.items()},
                            category_leakage_violations=0)
                    result = checkpoints.evaluate(model + '_' + fs.replace('+', '_') + '_' + origin, evaluate)
                    results.append(result)
                    table = pd.DataFrame([{k: v for k, v in r.items() if k not in ('subgroups', 'train_only_category_count')}
                                          for r in results])
                    temp = out / 'origin_metrics.csv.tmp'; table.to_csv(temp, index=False); os.replace(temp, out / 'origin_metrics.csv')
                    print(json.dumps({k: result[k] for k in ('model', 'feature_set', 'origin', 'seconds', 'auc', 'process_peak_mb')}), flush=True)
        for name, path in dict(master=args.master, online=args.online, monthly=args.monthly).items():
            if digest(path) != hashes[name]:
                raise ValueError('input changed during experiment')
        report = aggregate(results, sampled_deltas())
        report['subgroup_metrics'] = [dict(model=r['model'], origin=r['origin'], phase=r['phase'], **s)
                                      for r in results for s in r['subgroups']]
        report['hgb_reference_max_auc_difference'] = max(abs(r['auc'] - float(stored[(stored.feature_set=='enriched')
                         & (stored.origin==r['origin'])].auc.iloc[0])) for r in results if r['model']=='HGB')
        atomic_json(out / 'aggregate.json', report)
        manifest.update(status='completed', ended_at=now())
        audit['category_audits'] = [dict(model=r['model'], origin=r['origin'],
             train_only_category_count=r['train_only_category_count'], category_leakage_violations=0) for r in results]
        atomic_json(out / 'leakage_audit.json', audit)
        runtime = dict(status='completed', started_at=started, ended_at=manifest['ended_at'],
                       session_started_at=session_start, session_seconds=time.monotonic()-start,
                       total_fit_seconds=sum(r['seconds'] for r in results), process_peak_mb=peak_mb(),
                       completed_model_origins=len(results), peak_memory_scope='process lifetime working set, not isolated fit',
                       origins=[{k: r[k] for k in ('model', 'origin', 'seconds', 'process_peak_mb')} for r in results])
        atomic_json(out / 'runtime.json', runtime)
        lines = ['# Full-population confirmatory AUC experiment', '', 'NO_PRODUCTION_CHANGE', '',
                 f"Conclusion: {report['conclusion']}", '',
                 f"Population: {len(frame)} historical rows / {manifest['stores']} stores; no sampling.", '',
                 '| Phase | Origin | Model | n_train | n_eval | Positive rate | AUC | Seconds |',
                 '|---|---|---|---:|---:|---:|---:|---:|']
        lines += [f"| {r['phase']} | {r['origin']} | {r['model']} | {r['n_train']} | {r['n_eval']} | {r['positive_rate']:.6f} | {r['auc']:.6f} | {r['seconds']:.2f} |" for r in results]
        lines += ['', 'Paired macro deltas (not production AUC projections):', '']
        lines += [f"- {r['phase']}: full delta {r['delta']:+.6f}; sampled delta {r['sampled_delta']:+.6f}; full minus sampled {r['full_minus_sampled_delta']:+.6f}." for r in report['comparisons'] if r['origin']=='macro']
        lines += ['', 'Same frozen #68 parameters/features, train-only vocabulary, embargo=4, 2024 validation and 2025Q1/Q2 final.',
                  'No final-driven tuning. Full population is a confirmation on the previously used historical cohort, not a new prospective holdout.',
                  'Online cells independently reaggregated from monthly source; geometry/QA/ER/rank missingness indicators not added.',
                  'Adopted model/cutoff/calibration/serving/CASE-A/B/C/frozen submission unchanged; no production export regenerated.',
                  'Original OOF verification: ' + manifest['oof_verification'], '']
        (out / 'comparison.md').write_text('\n'.join(lines), encoding='utf-8')
        atomic_json(manifest_path, manifest)
        print(json.dumps(dict(status='completed', conclusion=report['conclusion'], seconds=runtime['session_seconds'])), flush=True)
    except Exception:
        manifest.update(status='partial', stopped_at=now(), conclusion='FULL_POPULATION_RUN_INCOMPLETE')
        atomic_json(manifest_path, manifest)
        atomic_json(out / 'runtime.json', dict(status='partial', started_at=started, stopped_at=now(),
                    session_seconds=time.monotonic()-start, completed_model_origins=len(results), process_peak_mb=peak_mb()))
        if not (out / 'leakage_audit.json').exists():
            atomic_json(out / 'leakage_audit.json', dict(status='invalid_or_unverified', leakage_violations=None))
        raise


if __name__ == '__main__':
    main()
