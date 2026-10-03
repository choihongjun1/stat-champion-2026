"""All identifiers/values are synthetic; no production artifacts are loaded."""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch
from threadpoolctl import threadpool_limits

from scripts.benchmark_diagnosis_speed import compare, explanation, fixture
from src.analysis.diagnosis_fast import factor_shapley_numpy, factor_shapley_processes
from src.models import diagnose


@pytest.fixture(scope='module')
def data():
    with threadpool_limits(limits=1):
        yield fixture(n=7, background=5, iterations=20)


def groups(model):
    return [[c for c in f['features'] if c in model.columns_] for f in diagnose.FACTORS
            if any(c in model.columns_ for c in f['features'])]


def test_exact_explanations_sensitivity_short_name_missing_and_serialization(data):
    traces = []
    original_predict = data[0].model_.predict_proba
    def capture(X):
        result = original_predict(X)
        traces.append(result.copy())
        return result
    with threadpool_limits(limits=1):
        with patch.object(data[0].model_, 'predict_proba', capture):
            baseline = explanation(data, diagnose.factor_shapley, sensitivity=True)
            old = traces[:]
            traces.clear()
            fast = explanation(data, factor_shapley_numpy, sensitivity=True)
        assert len(old) == len(traces)
        for a, b in zip(old, traces):
            np.testing.assert_array_equal(a, b)  # Every coalition, not only final f(x).
        assert compare(baseline, fast)['max_contribution_diff'] == 0
        on = fast[0]['long'].query("factor_id == 'online_attention'")
        assert on.iloc[0].missing_reason_code == 'online_unobservable'
        assert on.iloc[0].hold_reason == on.iloc[1].hold_reason == 'data_missing'


@pytest.mark.parametrize('chunk', [1, 3, 2000])
def test_nan_categories_order_unknown_extra_and_determinism(data, chunk):
    m, X, B, *_ = data
    X, B = X.copy(), B.copy()
    # category order differs between source tables; production aligns each.
    for c in m.categories_:
        X[c] = X[c].cat.reorder_categories(list(reversed(X[c].cat.categories)))
    X['ignored'] = np.nan
    X = X[list(reversed(X.columns))]
    X.index = np.arange(len(X)) * 3 + 50
    fc = groups(m)
    with threadpool_limits(limits=1):
        old = diagnose.factor_shapley(m, X, B, fc, chunk=chunk)
        new = factor_shapley_numpy(m, X, B, fc, chunk=chunk)
        again = factor_shapley_numpy(m, X, B, fc, chunk=chunk)
    np.testing.assert_array_equal(old[0], new[0])
    np.testing.assert_array_equal(new[0], again[0])
    assert old[1] == new[1] == again[1]


@pytest.mark.parametrize('method', ['baseline', 'numpy'])
def test_spawn_uneven_chunk_order_and_determinism(data, method):
    m, X, B, *_ = data
    fc = groups(m)
    with threadpool_limits(limits=1):
        old = diagnose.factor_shapley(m, X, B, fc)
        a = factor_shapley_processes(m, X, B, fc, workers=2, method=method)
        b = factor_shapley_processes(m, X, B, fc, workers=2, method=method)
    np.testing.assert_array_equal(old[0], a[0])
    np.testing.assert_array_equal(a[0], b[0])
    assert old[1] == a[1] == b[1]


def test_empty_and_validation(data):
    m, X, B, *_ = data
    out, base = factor_shapley_numpy(m, X.iloc[:0], B, groups(m))
    assert out.shape == (0, 8) and np.isfinite(base)
    with pytest.raises(ValueError):
        factor_shapley_numpy(m, X, B, groups(m), chunk=0)
    with pytest.raises(TypeError):
        factor_shapley_numpy(m, X.assign(area=X.area.astype('float32')), B, groups(m))
