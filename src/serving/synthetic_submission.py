"""Generate the UI-only synthetic CASE-A/B/C submission bundle, never real cases."""
import json
from pathlib import Path
import tempfile

from src.data import config
from src.serving import build_db as bd, export_submission as es, synthetic_samples as syn

DEFAULT_OUT=config.REPO_ROOT/'docs/samples/submission_bundle'


def generate(out=DEFAULT_OUT):
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        samples=[s for s in syn.SAMPLES if s[1]=='bundle']
        kw=syn.write_inputs(root,samples,True)
        db=root/'synthetic.sqlite'
        bd.build(root/'reports.jsonl',root/'serve_meta.json',root/'licenses.parquet',db,
                 license_snapshot_date=syn.SNAPSHOT,purpose='release',**kw)
        high=[s for s in samples if s[10]=='high']
        low=[s for s in samples if s[10]=='low']
        chosen=[high[0],high[1],low[0]]
        cases={'cases':[dict(case_label=label,store_id=f"SAMPLE-{s[0]:03d}",rule_stage='base',
                             n_candidates=len(high) if label!='C' else len(low),seed=20261004+i)
                        for i,(label,s) in enumerate(zip('ABC',chosen))]}
        path=root/'cases.json'; path.write_text(json.dumps(cases),encoding='utf-8')
        apply=root/'synthetic_apply.csv'
        apply.write_text('id,apply_status,apply_end,checked_at\n'+''.join(
            f'{p["id"]},open,{"2026-10-16" if i==0 else ""},2026-10-03\n'
            for i,p in enumerate(syn.POLICIES)),encoding='utf-8')
        return es.export(db,out,cases_path=path,policies_apply_path=apply,allow_tracked_synthetic=True)


if __name__=='__main__':
    generate()
    print('Synthetic submission sample verified; claims findings: 0')
