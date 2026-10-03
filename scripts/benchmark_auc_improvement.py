"""Bounded opt-in feasibility benchmark; writes aggregates only, never production assets."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault('OMP_NUM_THREADS', '4')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from threadpoolctl import threadpool_limits
from experiments.auc_features import temporal_features
from src.models import features, detect
from src.models.splits import rolling_origin_folds
from src.models.train_detect import load_master, attach_online

SEED=20260922


def sample_entities(df, limit):
    ids = df.store_id.drop_duplicates().tolist()
    ids.sort(key=lambda v: hashlib.sha256(('auc-study-v1:'+str(v)).encode()).digest())
    return df[df.store_id.isin(ids[:limit])].copy().reset_index(drop=True)


def matrix(df, cols, cats, raw_cols):
    raw = [c for c in cols if c in raw_cols]
    x = features.build_X(df, raw, categories=cats)
    for c in cols:
        if c not in raw_cols:
            if not c.startswith('exp_'):
                raise ValueError('Non-allowlisted derived input')
            x[c]=df[c].astype(float)
    return x


def fit_predict(kind, xt, y, xe):
    keep=xt.columns[~xt.isna().all()]
    xt,xe=xt[keep].copy(),xe[keep].copy()
    categorical=[c for c in xt if isinstance(xt[c].dtype,pd.CategoricalDtype)]
    if kind=='HGB':
        return detect.DetectModel(dict(detect.ADOPTED_PARAMS)).fit(xt,y).predict_proba(xe)
    if kind.startswith('CatBoost'):
        from catboost import CatBoostClassifier
        for c in categorical:
            if kind=='CatBoost-native':
                xt[c]=xt[c].astype(object).fillna('__MISSING__').astype(str)
                xe[c]=xe[c].astype(object).fillna('__MISSING__').astype(str)
            else:
                xt[c]=xt[c].cat.codes.astype(float).replace(-1,np.nan)
                xe[c]=xe[c].cat.codes.astype(float).replace(-1,np.nan)
        model=CatBoostClassifier(iterations=400,depth=6,learning_rate=.03,
            l2_leaf_reg=3,loss_function='Logloss',random_seed=SEED,
            thread_count=4,allow_writing_files=False,verbose=False)
        model.fit(xt,y,cat_features=categorical if kind=='CatBoost-native' else [])
    elif kind=='LightGBM':
        from lightgbm import LGBMClassifier
        model=LGBMClassifier(n_estimators=400,learning_rate=.03,num_leaves=31,
            min_child_samples=200,reg_lambda=1.,random_state=SEED,n_jobs=4,
            deterministic=True,force_col_wise=True,verbosity=-1)
        model.fit(xt,y)
    else:
        raise ValueError('Unknown model')
    return model.predict_proba(xe)[:,1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--master',type=Path,required=True)
    ap.add_argument('--baseline-dir',type=Path,required=True)
    ap.add_argument('--online',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--max-stores',type=int,default=8000)
    a=ap.parse_args()
    if not 1000<=a.max_stores<=10000:
        ap.error('Study permits 1,000–10,000 entities only')
    if a.out.exists() and any(a.out.iterdir()):
        ap.error('Use a new empty output directory; no overwrite or adaptive rerun')
    meta=json.loads((a.baseline_dir/'run_meta.json').read_text(encoding='utf-8'))
    assert meta['params']==detect.ADOPTED_PARAMS
    assert meta['embargo']==4 and meta['min_train_origins']==4
    assert hashlib.file_digest(a.master.open('rb'),'sha256').hexdigest()==meta['master_sha256']
    stored=pd.read_csv(a.baseline_dir/'oof_metrics_by_origin.csv')
    summary=pd.read_csv(a.baseline_dir/'sensitivity_summary.csv').set_index('feature_set')
    for fs in ('base','enriched'):
        assert abs(stored.loc[stored.feature_set.eq(fs),'auc'].mean()-summary.loc[fs,'all_auc_mean'])<1e-12
    full=load_master(a.master)
    for row in stored.loc[stored.feature_set.eq('base')].itertuples():
        y=full.loc[full.origin.eq(row.origin),'event_12m']
        assert len(y)==row.n and abs(y.mean()-row.base_rate)<1e-12
    base='base'
    if a.online:
        assert hashlib.file_digest(a.online.open('rb'),'sha256').hexdigest()==meta['online_sha256']
        full=attach_online(full,a.online)
        base='enriched'
    df=sample_entities(full,a.max_stores)
    if len(df)>=len(full):
        raise ValueError('Full population training forbidden')
    del full
    extra,groups=temporal_features(df)
    df=pd.concat([df,extra],axis=1)
    raw=features.select_features(df.columns,base)
    assert raw==meta['feature_sets'][base]
    sets={'baseline':raw,'A1':raw+groups['A1'],'A2':raw+groups['A2'],
          'B':raw+groups['B'],'temporal+B':raw+groups['A1']+groups['A2']+groups['A3']+groups['B']}
    if groups['A3']:
        sets['A3']=raw+groups['A3']
    folds=rolling_origin_folds(df,min_train_origins=4,embargo=4)
    val=[f for f in folds if f.test_origins[0].startswith('2024')]
    test=[f for f in folds if f.test_origins[0] in ('2025Q1','2025Q2')]
    a.out.mkdir(parents=True,exist_ok=True)
    manifest=dict(seed=SEED,base=base,rows=len(df),stores=int(df.store_id.nunique()),
        master_sha256=meta['master_sha256'],online_sha256=meta['online_sha256'] if a.online else None,
        versions={p:importlib.metadata.version(p) for p in ('numpy','pandas','scikit-learn','catboost','lightgbm')},
        features=sets,validation=[f.describe() for f in val],final=[f.describe() for f in test],
        sample_rule='SHA256 auc-study-v1:<entity>, smallest hashes; no outcome filtering',
        model_configs='One fixed configuration each; HGB adopted, CatBoost native/ordinal, LightGBM',
        selection='HGB feature group by 2024 macro AUC, then alternative models on baseline and selected group. Lock winner before final evaluation.',
        maturity='age>=24 sensitivity only; not verified label-maturity upper bound',
        production_baseline='Stored metrics aggregation verified; prediction parquet not required by this runner')
    (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    rows=[]
    def run(model,fs,fold_list,phase):
        for fold in fold_list:
            start=time.monotonic()
            tr,te=fold.masks(df)
            cats=features.fit_categories(df.loc[tr],raw)
            xt=matrix(df.loc[tr],sets[fs],cats,raw)
            xe=matrix(df.loc[te],sets[fs],cats,raw)
            y=df.loc[te,'event_12m'].to_numpy()
            with threadpool_limits(limits=4):
                p=fit_predict(model,xt,df.loc[tr,'event_12m'].to_numpy(),xe)
            masks={'all':np.ones(len(y),dtype=bool),'age_ge24':df.loc[te,'age_months'].to_numpy()>=24}
            for subset,mask in masks.items():
                rows.append(dict(model=model,feature_set=fs,phase=phase,origin=fold.test_origins[0],subset=subset,
                    n=int(mask.sum()),positive_rate=float(y[mask].mean()),auc=float(roc_auc_score(y[mask],p[mask])),
                    feature_count=len(sets[fs]),seconds=time.monotonic()-start))
            pd.DataFrame(rows).to_csv(a.out/'origin_metrics.csv',index=False)
            print(json.dumps(dict(model=model,features=fs,phase=phase,origin=fold.test_origins[0],seconds=round(time.monotonic()-start,1))),flush=True)
    for fs in sets:
        run('HGB',fs,val,'validation')
    v=pd.DataFrame(rows).query("subset=='all'")
    best=v.groupby('feature_set',sort=True).auc.mean().idxmax()
    configs=[('HGB',fs) for fs in sets]
    for model in ('CatBoost-native','CatBoost-ordinal','LightGBM'):
        for fs in dict.fromkeys(['baseline',best]):
            configs.append((model,fs))
            run(model,fs,val,'validation')
    v=pd.DataFrame(rows).query("subset=='all'")
    scores=v.groupby(['model','feature_set'],sort=True).auc.mean()
    winner=list(scores.idxmax())
    (a.out/'selection.json').write_text(json.dumps(dict(best_hgb_features=best,winner=winner,validation_auc=float(scores.max()),locked_before_final=True),indent=2),encoding='utf-8')
    for model,fs in configs:
        run(model,fs,test,'final')
    result=pd.DataFrame(rows)
    agg=result.groupby(['phase','subset','model','feature_set']).agg(mean_auc=('auc','mean'),min_auc=('auc','min'),max_auc=('auc','max'),std_auc=('auc','std'),feature_count=('feature_count','first')).reset_index()
    refs=agg.query("model=='HGB' and feature_set=='baseline'").set_index(['phase','subset']).mean_auc
    agg['delta']=agg.apply(lambda r:r.mean_auc-refs.loc[(r.phase,r.subset)],axis=1)
    agg.to_csv(a.out/'summary.csv',index=False)
    print('Completed; final evaluation was not used for selection.',flush=True)


if __name__=='__main__':
    main()
