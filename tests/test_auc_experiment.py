import numpy as np
import pandas as pd
from experiments.auc_features import temporal_features
from scripts.benchmark_auc_improvement import matrix, sample_entities
from src.models import features


def fixture():
    return pd.DataFrame({'store_id':['synthetic']*6,'origin':['2022Q1','2022Q2','2022Q3','2022Q4','2023Q1','2023Q3'],
                         'area':[1.,1.,1.,1.,1.,np.nan],'trdar_flow_pop':[1.,2.,3.,4.,5.,7.]})


def test_future_and_target_invariance():
    d=fixture()
    x,g=temporal_features(d,['trdar_flow_pop'])
    mutated=d.copy()
    mutated.loc[4:,'trdar_flow_pop']=9999
    mutated['event_12m']=1
    mutated['closure_date']='2099-01-01'
    z,_=temporal_features(mutated,['trdar_flow_pop'])
    pd.testing.assert_frame_equal(x.iloc[:4],z.iloc[:4])
    assert all(c.startswith('exp_') for cols in g.values() for c in cols)


def test_gaps_order_and_entity_isolation():
    d=fixture()
    x,_=temporal_features(d,['trdar_flow_pop'])
    assert x.loc[5,'exp_trdar_flow_pop_slope4']==1.
    shuffled=d.sample(frac=1,random_state=3)
    z,_=temporal_features(shuffled,['trdar_flow_pop'])
    pd.testing.assert_frame_equal(x,z.sort_index())
    other=d.assign(store_id='other',trdar_flow_pop=100.)
    combined=pd.concat([d,other],ignore_index=True)
    z,_=temporal_features(combined,['trdar_flow_pop'])
    pd.testing.assert_frame_equal(x,z.iloc[:len(d)])


def test_no_backfill_and_train_categories():
    d=fixture()
    d.loc[:2,'trdar_flow_pop']=np.nan
    x,_=temporal_features(d,['trdar_flow_pop'])
    assert x.loc[:2].filter(like='trdar_flow_pop').isna().all().all()
    tr=pd.DataFrame({'gu':['train'],'area':[1.]})
    te=pd.DataFrame({'gu':['unseen'],'area':[2.]})
    cats=features.fit_categories(tr,['gu','area'])
    z=matrix(te,['gu','area'],cats,['gu','area'])
    assert z.gu.isna().all() and cats=={'gu':['train']}


def test_sampling_independent_of_target_and_row_order():
    d=pd.DataFrame({'store_id':[f'synthetic-{i}' for i in range(30)],'event_12m':[0]*30})
    first=set(sample_entities(d,10).store_id)
    assert first==set(sample_entities(d.sample(frac=1,random_state=1).assign(event_12m=1),10).store_id)


def test_online_future_posts_cannot_change_prior_temporal_features():
    d=fixture().assign(online_blog_cnt_3m=[0.,2.,4.,6.,8.,12.])
    x,g=temporal_features(d,['trdar_flow_pop'])
    assert len(g['A3'])==5
    assert x.loc[3,'exp_online_blog_cnt_3m_slope4']==2.
    d.loc[4:,'online_blog_cnt_3m']=10000.
    z,_=temporal_features(d,['trdar_flow_pop'])
    pd.testing.assert_frame_equal(x.iloc[:4],z.iloc[:4])
