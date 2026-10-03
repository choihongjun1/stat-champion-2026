"""Opt-in input-construction PoC; production diagnose/serve never import this.

Only build_X's float64/category contract is supported. No sklearn private
prediction APIs or recoded ndarray model inputs are used.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from src.models.diagnose import _shapley_weights


def factor_shapley_numpy(model, X, background, factor_cols, *, chunk=2000):
    F = len(factor_cols)
    if not 1 <= F <= 12 or chunk < 1 or len(background) < 1:
        raise ValueError("require 1..12 factors, positive chunk and background")
    # Align once using the production category vocabulary, including unknown -> NA.
    A, B = model._align(X), model._align(background)
    columns = list(model.columns_)
    index = {c: j for j, c in enumerate(columns)}
    groups = [[index[c] for c in cs if c in index] for cs in factor_cols]
    dtypes = list(A.dtypes)
    for c in columns:
        if A[c].dtype != B[c].dtype:
            raise TypeError("X/background dtype mismatch")
        if not isinstance(A[c].dtype, pd.CategoricalDtype) and A[c].dtype != np.dtype('float64'):
            raise TypeError("PoC requires build_X float64/category inputs")

    def encode(frame):
        return np.column_stack([frame[c].cat.codes.to_numpy() if isinstance(frame[c].dtype, pd.CategoricalDtype)
                                else frame[c].to_numpy() for c in columns])

    def restore(matrix):
        return pd.DataFrame({c: pd.Categorical.from_codes(matrix[:, j].astype(np.int64), dtype=dtypes[j])
                             if isinstance(dtypes[j], pd.CategoricalDtype) else matrix[:, j]
                             for j, c in enumerate(columns)}, copy=False)

    a, b = encode(A), encode(B)
    K = len(B)
    base = float(model.model_.predict_proba(B)[:, 1].mean())
    w = _shapley_weights(F)
    masks = np.arange(2 ** F)
    size = np.array([bin(m).count('1') for m in masks])
    out = np.zeros((len(A), F))
    for s0 in range(0, len(A), chunk):
        n = len(a[s0:s0 + chunk])
        big_x = np.repeat(a[s0:s0 + chunk], K, axis=0)
        big_b = np.tile(b, (n, 1))
        matrix = np.empty_like(big_x)
        v = np.empty((n, len(masks)))
        for m in masks:
            np.copyto(matrix, big_x)
            for k, js in enumerate(groups):
                if not (m >> k) & 1:
                    matrix[:, js] = big_b[:, js]
            # Public estimator API, same column order and categorical metadata.
            v[:, m] = model.model_.predict_proba(restore(matrix))[:, 1].reshape(n, K).mean(axis=1)
        for k in range(F):
            without = masks[(masks >> k) & 1 == 0]
            out[s0:s0 + n, k] = (w[size[without]] * (v[:, without | (1 << k)] - v[:, without])).sum(axis=1)
    return out, base


_worker_state = None


def _initialize(model, background, factors, method):
    global _worker_state
    _worker_state = model, background, factors, method


def _calculate(X):
    from src.models.diagnose import factor_shapley
    model, background, factors, method = _worker_state
    # HGB OpenMP is internally parallel; cap every spawned process to one thread.
    with threadpool_limits(limits=1):
        fn = factor_shapley_numpy if method == 'numpy' else factor_shapley
        return fn(model, X, background, factors, chunk=128)


def factor_shapley_processes(model, X, background, factor_cols, *, workers=2, method='numpy'):
    """Windows spawn, ordered map, one background/model copy per worker.

    Pool lifetime includes startup; no RNG, global production monkeypatch or cache.
    Splits are bounded to worker count, keeping pending payload memory bounded.
    """
    if not 1 <= workers <= 8 or len(background) > 256 or method not in ('numpy', 'baseline'):
        raise ValueError('invalid worker count/method')
    if len(X) == 0:
        return factor_shapley_numpy(model, X, background, factor_cols)
    parts = [X.iloc[ix] for ix in np.array_split(np.arange(len(X)), min(workers, len(X)))]
    with ProcessPoolExecutor(max_workers=min(workers, len(X)), mp_context=mp.get_context('spawn'),
                             initializer=_initialize, initargs=(model, background, factor_cols, method)) as pool:
        results = list(pool.map(_calculate, parts))
    if any(base != results[0][1] for _, base in results):
        raise RuntimeError('worker background base mismatch')
    return np.concatenate([phi for phi, _ in results]), results[0][1]
