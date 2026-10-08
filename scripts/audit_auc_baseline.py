"""Read-only production aggregate audit, optionally recompute AUC from original OOF."""
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
from sklearn.metrics import roc_auc_score


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--master',type=Path,required=True)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--oof',type=Path)
    a=ap.parse_args()
    meta=json.loads((a.run/'run_meta.json').read_text(encoding='utf-8'))
    assert hashlib.file_digest(a.master.open('rb'),'sha256').hexdigest()==meta['master_sha256']
    d=pd.read_parquet(a.master,columns=['origin','event_12m','maturity_cutoff_used_months'])
    m=pd.read_csv(a.run/'oof_metrics_by_origin.csv')
    s=pd.read_csv(a.run/'sensitivity_summary.csv').set_index('feature_set')
    report={}
    for name in ('base','enriched'):
        z=m[m.feature_set.eq(name)].sort_values('origin')
        assert len(z)==10 and z.origin.is_unique
        assert abs(z.auc.mean()-s.loc[name,'all_auc_mean'])<1e-12
        for r in z.itertuples():
            y=d.loc[d.origin.eq(r.origin),'event_12m']
            assert len(y)==r.n and abs(y.mean()-r.base_rate)<1e-12
        report[name]=dict(mean=z.auc.mean(),std_ddof0=z.auc.std(ddof=0),minimum=z.auc.min(),maximum=z.auc.max(),n=int(z.n.sum()),origins=z.origin.tolist())
    report['verification']='Stored metrics macro mean, master hash, origin n and event rates matched. No model refit or prediction-level recomputation.'
    if a.oof:
        assert hashlib.file_digest(a.oof.open('rb'),'sha256').hexdigest()==meta['oof_predictions_sha256']
        pred=pd.read_parquet(a.oof,columns=['origin','config','y','p_oof'])
        assert len(pred)==meta['oof_predictions_rows']
        pred=pred[pred.config.eq('현 설정')]
        assert len(pred)>0
        for r in m[m.feature_set.eq(meta['primary_feature_set'])].itertuples():
            z=pred[pred.origin.eq(r.origin)]
            assert len(z)==r.n and abs(roc_auc_score(z.y,z.p_oof)-r.auc)<1e-12
        report['verification']='Original OOF hash/rows and adopted-config origin AUC recomputed successfully; master hash/n/event rates and macro summaries match.'
    report['maturity_cutoff_values']=sorted(d.maturity_cutoff_used_months.dropna().unique().tolist())
    a.out.mkdir(parents=True,exist_ok=True)
    m[m.feature_set.isin(['base','enriched'])].to_csv(a.out/'production_origins.csv',index=False)
    (a.out/'baseline_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
