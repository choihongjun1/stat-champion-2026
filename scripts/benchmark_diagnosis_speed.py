"""Synthetic-only benchmark. python -m scripts.benchmark_diagnosis_speed --out work/speed.json

No production data input accepted. Full adopted HGB; K=256; 8 factors/7 online
participants. Timings include spawn/IPC and downstream explanation/serialization.
"""
from __future__ import annotations
import argparse
import cProfile
from contextlib import redirect_stdout
from functools import partial
import io
import json
import platform
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import psutil
import sklearn
from sklearn.isotonic import IsotonicRegression
from threadpoolctl import threadpool_info, threadpool_limits

from src.models import bands, detect, diagnose, features
from src.analysis.diagnosis_fast import factor_shapley_numpy, factor_shapley_processes


def fixture(n=128, background=256, iterations=400):
    rng = np.random.default_rng(20261003)
    cols = [c for f in diagnose.FACTORS if f['id'] != 'rent_level' for c in f['features']]
    raw = pd.DataFrame({c: rng.normal(size=2048 + n + 2 * background) for c in cols})
    for c in features.CATEGORICAL:
        if c in cols:
            raw[c] = rng.choice(['fixture_a', 'fixture_b', 'fixture_c'], len(raw))
    raw['age_months'] = rng.integers(0, 200, len(raw))
    for c in features.ONLINE_PREDICTORS:
        raw[c] = rng.integers(0, 15, len(raw)).astype(float)
    raw.loc[rng.random(len(raw)) < .12, 'area'] = np.nan
    raw.loc[rng.random(len(raw)) < .10, 'gu'] = None
    X = features.build_X(raw, cols)
    y = (rng.random(2048) < 1 / (1 + np.exp(-(.9 * X['area'].iloc[:2048].fillna(0).to_numpy()
                                            + .08 * X['online_blog_cnt_12m'].iloc[:2048].to_numpy() - 1.2)))).astype(int)
    model = detect.DetectModel(params={**detect.ADOPTED_PARAMS, 'max_iter': iterations}).fit(X.iloc[:2048], y)
    target = raw.iloc[2048:2048 + n].reset_index(drop=True).copy()
    target['store_id'] = [f'fixture_{i:04d}' for i in range(n)]
    target['origin'] = '2025Q2'
    target['trdar_cd'] = None
    # Upstream short-name policy: online all NA. Also partial truncated and unknown categories.
    target.loc[0, list(features.ONLINE_PREDICTORS)] = np.nan
    target.loc[1, features.ONLINE_PREDICTORS[0]] = np.nan
    target.loc[2, 'gu'] = 'unknown_fixture'
    Xt = features.build_X(target, cols, categories=model.categories_)
    meta = target[['store_id', 'origin', 'biz_type', 'gu', 'age_months']].copy()
    return model, Xt, X.iloc[-2 * background:-background], X.iloc[-background:], meta, target


def explanation(data, fn, *, sensitivity=False):
    model, Xt, B, Bs, meta, raw = data
    with patch.object(diagnose, 'factor_shapley', fn), redirect_stdout(io.StringIO()):
        args = (model, Xt, B, Bs, meta, raw) if sensitivity else (model, Xt, B, meta, raw)
        res = (diagnose.explain_s8 if sensitivity else diagnose.explain)(*args, truncated_stores={'fixture_0001'})
        # Exercise real ordering and displayed fields via production serializer.
        records = [diagnose.factors_json(res['long'], s, o, values=res['values'](s, o), with_sensitivity=sensitivity)
                   for s, o in zip(meta.store_id, meta.origin)]
        serialized = json.dumps(records, ensure_ascii=False)
    return res, serialized


def compare(reference, candidate):
    a, b = reference[0], candidate[0]
    pa, pb = a['meta'].probability_12m.to_numpy(), b['meta'].probability_12m.to_numpy()
    iso = IsotonicRegression(out_of_bounds='clip').fit([0, .2, .5, 1], [0, .1, .6, 1])
    caldiff = float(np.max(np.abs(iso.predict(pa) - iso.predict(pb))))
    probability = float(np.max(np.abs(pa - pb)))
    contribution = float(np.max(np.abs(a['long'].contribution.to_numpy() - b['long'].contribution.to_numpy())))
    band = int(np.sum(bands.assign_bands_absolute(iso.predict(pa), cut_mid=.2, cut_high=.6)
                      != bands.assign_bands_absolute(iso.predict(pb), cut_mid=.2, cut_high=.6)))
    direction = int(np.sum(a['long'].contribution.map(diagnose.direction_label).to_numpy()
                           != b['long'].contribution.map(diagnose.direction_label).to_numpy()))
    pd.testing.assert_frame_equal(a['long'], b['long'], check_exact=True)
    pd.testing.assert_frame_equal(a['by_category'], b['by_category'], check_exact=True)
    assert reference[1] == candidate[1] and probability <= 1e-12 and caldiff <= 1e-12 and contribution <= 1e-12
    assert a.get('s8_summary') == b.get('s8_summary') and band == direction == 0
    return dict(max_probability_diff=probability, max_calibrated_diff=caldiff, max_contribution_diff=contribution,
                band_mismatch=band, direction_mismatch=direction, display_mismatch=0, ordering_mismatch=0)


def timed(data, fn):
    done = threading.Event()
    peak = [0]
    def monitor():
        while not done.is_set():
            processes = [psutil.Process()] + psutil.Process().children(recursive=True)
            rss = 0
            for p in processes:
                try:
                    rss += p.memory_info().rss
                except psutil.Error:
                    pass
            peak[0] = max(peak[0], rss)
            done.wait(.01)
    thread = threading.Thread(target=monitor)
    thread.start()
    start = time.perf_counter()
    try:
        result = explanation(data, fn)
    finally:
        seconds = time.perf_counter() - start
        done.set()
        thread.join()
    return result, seconds, peak[0] / 2**20


def repeated(data, fn, repetitions):
    observations = [timed(data, fn) for _ in range(repetitions)]
    for result, _, _ in observations[1:]:
        compare(observations[0][0], result)
    seconds = [row[1] for row in observations]
    return observations[0][0], float(np.median(seconds)), max(row[2] for row in observations), seconds


def phase_baseline(model, X, background, factor_cols, *, chunk=2000, phases):
    """Benchmark-only copy of the baseline, with disjoint perf_counter buckets."""
    def measured(name, fn):
        t = time.perf_counter()
        value = fn()
        phases[name] = phases.get(name, 0.) + time.perf_counter() - t
        return value
    F, K = len(factor_cols), len(background)
    B = measured('construction', lambda: background.reset_index(drop=True))
    base = float(measured('predict_wrapper', lambda: model.predict_proba(B)).mean())
    masks = np.arange(2 ** F)
    size = np.array([bin(m).count('1') for m in masks])
    w = diagnose._shapley_weights(F)
    out = np.zeros((len(X), F))
    for s0 in range(0, len(X), chunk):
        Xc = measured('construction', lambda: X.iloc[s0:s0 + chunk].reset_index(drop=True))
        n = len(Xc)
        big_x = measured('construction', lambda: Xc.iloc[np.repeat(np.arange(n), K)].reset_index(drop=True))
        big_b = measured('construction', lambda: B.iloc[np.tile(np.arange(K), n)].reset_index(drop=True))
        v = np.empty((n, len(masks)))
        for m in masks:
            t = time.perf_counter()
            df = big_x.copy()
            for k in range(F):
                if not (m >> k) & 1:
                    for c in factor_cols[k]:
                        df[c] = big_b[c]
            phases['construction'] += time.perf_counter() - t
            p = measured('predict_wrapper', lambda: model.predict_proba(df))
            t = time.perf_counter()
            v[:, m] = p.reshape(n, K).mean(axis=1)
            phases['aggregation'] = phases.get('aggregation', 0.) + time.perf_counter() - t
        t = time.perf_counter()
        for k in range(F):
            without = masks[(masks >> k) & 1 == 0]
            out[s0:s0 + n, k] = (w[size[without]] * (v[:, without | (1 << k)] - v[:, without])).sum(axis=1)
        phases['aggregation'] += time.perf_counter() - t
    return out, base


def profile(data):
    profiler = cProfile.Profile()
    calls = []
    phases = {}
    original = partial(phase_baseline, phases=phases)
    def measured(*args, **kwargs):
        t = time.perf_counter()
        result = original(*args, **kwargs)
        calls.append({'participants': len(args[3]), 'seconds': time.perf_counter() - t})
        return result
    profiler.enable()
    start = time.perf_counter()
    explanation(data, measured)
    total = time.perf_counter() - start
    profiler.disable()
    stats = profiler.getstats()
    # Exclusive time is additive; cumulative nested times must not be summed.
    pandas_time = sum(s.inlinetime for s in stats if hasattr(s.code, 'co_filename') and 'pandas' in s.code.co_filename)
    pandas_ops = {op: sum(s.totaltime for s in stats if hasattr(s.code, 'co_filename')
                         and 'pandas/core/frame.py' in s.code.co_filename.replace('\\', '/')
                         and s.code.co_name == op) for op in ('copy', '__setitem__', 'assign')}
    # DataFrame.copy is inherited from NDFrame; report that entry separately.
    pandas_ops['copy'] = sum(s.totaltime for s in stats if hasattr(s.code, 'co_filename')
                              and 'pandas/core/generic.py' in s.code.co_filename.replace('\\', '/')
                              and s.code.co_name == 'copy')
    pandas_ops['concat'] = sum(s.totaltime for s in stats if hasattr(s.code, 'co_filename')
                                and 'pandas/core/reshape/concat.py' in s.code.co_filename.replace('\\', '/')
                                and s.code.co_name == 'concat')
    predict_time = sum(s.totaltime for s in stats if hasattr(s.code, 'co_filename')
                       and s.code.co_name == 'predict_proba' and '_hist_gradient_boosting' in s.code.co_filename)
    hgb_parts = {op: sum(s.totaltime for s in stats if hasattr(s.code, 'co_filename')
                        and s.code.co_name == op and '_hist_gradient_boosting' in s.code.co_filename)
                 for op in ('_preprocess_X', '_predict_iterations')}
    align_time = sum(s.totaltime for s in stats if hasattr(s.code, 'co_name') and s.code.co_name == '_align')
    serialization = sum(s.totaltime for s in stats if hasattr(s.code, 'co_name')
                        and (s.code.co_name == 'factors_json' or
                             (s.code.co_name == 'dumps' and 'json' in s.code.co_filename)))
    model_predict = max(0, predict_time)
    rows = sorted([{'function': s.code.co_name, 'file': Path(s.code.co_filename).name,
                    'self_seconds': s.inlinetime, 'cumulative_seconds': s.totaltime}
                   for s in stats if hasattr(s.code, 'co_filename')], key=lambda x: -x['self_seconds'])[:15]
    phases['downstream_and_serialization'] = total - sum(phases.values())
    return dict(total_seconds=total, shapley_calls=calls, phases_seconds=phases,
                phases_percent={k: 100*v/total for k, v in phases.items()}, pandas_exclusive_seconds=pandas_time,
                pandas_exclusive_percent=100*pandas_time/total, model_predict_seconds=model_predict,
                model_predict_percent=100*model_predict/total, wrapper_align_seconds=align_time,
                serialization_seconds=serialization,
                pandas_operations_cumulative_seconds=pandas_ops,
                hgb_parts_seconds=hgb_parts,
                downstream_seconds=total-sum(c['seconds'] for c in calls), top_self=rows)


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--sizes', nargs='+', type=int, default=[32, 64, 128])
    ap.add_argument('--workers', nargs='*', type=int, default=[2, 4, 6, 8])
    ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--repeats', type=int, default=1)
    a = ap.parse_args()
    if any(n < 3 or n > 128 for n in a.sizes):
        ap.error('synthetic sample sizes must be 3..128')
    if not 1 <= a.repeats <= 5 or not 1 <= a.threads <= 16 or any(not 1 <= w <= 8 for w in a.workers):
        ap.error('require repeats 1..5, threads 1..16 and workers 1..8')
    with threadpool_limits(limits=a.threads):
        # Keep the fitted model/fixture fixed across size/thread comparisons.
        data = fixture(128)
        report = dict(python=platform.python_version(), sklearn=sklearn.__version__, numpy=np.__version__,
                      platform=platform.system(), logical_cpus=psutil.cpu_count(), physical_cpus=psutil.cpu_count(logical=False),
                      pandas=pd.__version__, threads=a.threads, threadpools=threadpool_info(), background=256,
                      model_params=data[0].params, synthetic_only=True, repeats=a.repeats, rows=[])
        for n in a.sizes:
            small = (data[0], data[1].iloc[:n], data[2], data[3], data[4].iloc[:n], data[5].iloc[:n])
            ref, elapsed, memory, observations = repeated(small, diagnose.factor_shapley, a.repeats)
            report['rows'].append(dict(n=n, candidate='baseline', seconds=elapsed, speedup=1., peak_rss_mib=memory,
                                       observed_seconds=observations,
                                       **compare(ref, ref)))
            candidates = [('numpy', factor_shapley_numpy)]
            # Worker scaling on the same smallest sample, including pure-process control.
            if n == min(a.sizes):
                for workers in a.workers:
                    for method in ('baseline', 'numpy'):
                        candidates.append((f'{method}_processes_{workers}', partial(factor_shapley_processes,
                                                                                 workers=workers, method=method)))
            for name, fn in candidates:
                result, seconds, peak, observations = repeated(small, fn, a.repeats)
                row = dict(n=n, candidate=name, seconds=seconds, speedup=elapsed/seconds, peak_rss_mib=peak,
                           observed_seconds=observations,
                           **compare(ref, result))
                report['rows'].append(row)
                print(json.dumps(row), flush=True)
            if n == min(a.sizes):
                report['profile'] = profile(small)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
