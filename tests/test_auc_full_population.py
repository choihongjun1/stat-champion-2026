"""Synthetic full-population protocol, leakage, checkpoint and aggregate guards."""
import copy
import json
import numpy as np
import pandas as pd
import pytest
from experiments import full_population as full
from src.models import detect
from scripts.benchmark_auc_improvement import SEED


def metrics():
    rows = []
    for phase, origins in (('validation', full.VALIDATION), ('final', full.FINAL)):
        for model, fs in full.MODELS:
            for i, origin in enumerate(origins):
                rows.append(dict(model=model, feature_set=fs, phase=phase, origin=origin,
                    n_train=100 + i, n_eval=50, positive_rate=.2,
                    auc=.6 + i * .001 + (.02 if model != 'HGB' else 0)))
    return rows


def test_frozen_model_and_temporal_contract():
    assert full.MODELS == (('HGB', 'baseline'), ('CatBoost-ordinal', 'temporal+B'))
    assert full.VALIDATION == ('2024Q1', '2024Q2', '2024Q3', '2024Q4')
    assert full.FINAL == ('2025Q1', '2025Q2')
    assert full.CATBOOST_PARAMS == dict(iterations=400, depth=6, learning_rate=.03,
        l2_leaf_reg=3, loss_function='Logloss', random_seed=SEED, thread_count=4,
        allow_writing_files=False, verbose=False)
    assert detect.ADOPTED_PARAMS['learning_rate'] == .03


def test_resume_never_refits_completed_origin(tmp_path):
    calls = []
    def evaluate():
        calls.append(1)
        return dict(auc=.63)
    first = full.Checkpoints(tmp_path, 'frozen')
    assert first.evaluate('final2025', evaluate) == dict(auc=.63)
    resumed = full.Checkpoints(tmp_path, 'frozen')
    assert resumed.evaluate('final2025', evaluate) == dict(auc=.63)
    assert calls == [1]
    with pytest.raises(ValueError, match='fingerprint'):
        full.Checkpoints(tmp_path, 'changed').evaluate('final2025', evaluate)
    assert calls == [1]


def test_interrupted_origin_is_not_automatically_reevaluated(tmp_path):
    store = full.Checkpoints(tmp_path, 'fixed')
    def interrupted():
        raise RuntimeError('synthetic interrupt')
    with pytest.raises(RuntimeError):
        store.evaluate('final2025', interrupted)
    with pytest.raises(ValueError, match='automatic refit forbidden'):
        store.evaluate('final2025', lambda: pytest.fail('refit forbidden'))
    assert json.loads((tmp_path / 'final2025.json').read_text())['status'] == 'started'


def test_aggregate_uses_paired_macro_auc_and_delta_variance():
    rows = metrics()
    candidate = [r for r in rows if r['model'] == 'CatBoost-ordinal']
    candidate[0]['auc'] += .04
    report = full.aggregate(rows, dict(validation=.02, final=.03))
    macros = {r['phase']: r for r in report['comparisons'] if r['origin'] == 'macro'}
    assert macros['validation']['delta'] == pytest.approx(.03)
    assert macros['validation']['delta_variance_ddof0'] == pytest.approx(.0003)
    assert macros['final']['delta'] == pytest.approx(.02)
    assert report['mean_delta_all_origins'] == pytest.approx((.06 + 5 * .02) / 6)
    assert report['production_impact'] == 'NO_PRODUCTION_CHANGE'
    assert report['conclusion'] == 'FULL_POPULATION_IMPROVEMENT_REPRODUCED'
    assert macros['final']['full_minus_sampled_delta'] == pytest.approx(-.01)


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'population'])
def test_aggregate_refuses_unpaired_or_incomplete_results(mutation):
    rows = metrics()
    if mutation == 'duplicate': rows.append(copy.deepcopy(rows[0]))
    if mutation == 'missing': rows.pop()
    if mutation == 'population': rows[-1]['n_eval'] += 1
    with pytest.raises(ValueError):
        full.aggregate(rows, dict(validation=.02, final=.03))


def test_online_independent_guard_rejects_future_source_inclusion(tmp_path):
    frame = pd.DataFrame(dict(store_id=['synthetic'], origin=['2024Q1'], origin_end=[pd.Timestamp('2024-03-31')]))
    table = frame.assign(online_blog_cnt_3m=2., online_feature_asof=pd.Timestamp('2024-03-31'),
                         online_available_at=pd.Timestamp('2024-03-31'))
    online = tmp_path / 'online.parquet'
    table.to_parquet(online)
    monthly = tmp_path / 'monthly.parquet'
    pd.DataFrame(dict(store_id=['synthetic', 'synthetic'], year_month=['2024-03', '2024-04'],
                      mention_count=[2, 9])).to_parquet(monthly)
    audit = full.online_audit(frame, online, monthly)
    assert audit['checked_non_missing_cells'] == 1 and audit['future_month_violations'] == 0
    table['online_blog_cnt_3m'] = 11.
    table.to_parquet(online)
    with pytest.raises(ValueError):
        full.online_audit(frame, online, monthly)
    table['online_available_at'] = pd.Timestamp('2024-04-30')
    table.to_parquet(online)
    with pytest.raises(ValueError):
        full.online_audit(frame, online, monthly)
