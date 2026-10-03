"""Synthetic application-status join and submission sample regression."""
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from src.data import config
from src.serving import export_submission as es, policy_apply as pa, submission_report as sr
from tests.test_export_submission import _build, _cases


def application_fixture(tmp_path, db):
    with sqlite3.connect(db) as conn:
        ids = [p for (p,) in conn.execute('SELECT policy_id FROM policies ORDER BY policy_id')]
    path = tmp_path/'synthetic_apply.csv'
    path.write_text('id,apply_status,apply_end,checked_at\n'+''.join(
        f'{pid},open,{"2026-10-16" if i == 0 else ""},2026-10-03\n' for i,pid in enumerate(ids)),encoding='utf-8')
    return path, ids


def test_csv_join_fields_and_hash_provenance(tmp_path):
    db,_ = _build(tmp_path)
    path,ids = application_fixture(tmp_path,db)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rows,actual,date = pa.load_policy_apply(path,set(ids),expected_sha256=digest,checked_at='2026-10-03')
    assert actual == digest and date == '2026-10-03'
    assert rows[ids[0]]['apply_end'] == '2026-10-16'
    assert rows[ids[1]]['apply_end'] is None
    out=tmp_path/'bundle'
    es.export(db,out,cases_path=_cases(tmp_path,db),policies_apply_path=path,
              policies_apply_sha256=digest,policy_checked_at='2026-10-03')
    with sqlite3.connect(db) as conn:
        policy_hash=es.bd.read_run(conn)['policies_sha256']
    meta=json.loads((out/'meta.json').read_text(encoding='utf-8'))
    assert meta['provenance']['policies_sha256_12'] == policy_hash[:12]
    assert meta['provenance']['policies_apply_sha256_12'] == digest[:12]
    assert meta['provenance']['policy_checked_at'] == date
    for file in (out/'cases').glob('*.json'):
        case=json.loads(file.read_text(encoding='utf-8'))
        for policy in case['policies']:
            assert all(policy[k] == rows[policy['id']][k] for k in ('apply_status','apply_end','checked_at'))
            assert policy['linked_factor_ids'] == []
    assert es.verify_bundle(out)==[]
    assert es.claims_findings(out)['findings']==[]


@pytest.mark.parametrize('change',['missing','duplicate','blank','unknown','bad_date','bad_status','checked','hash'])
def test_csv_rejects_invalid_id_status_date_or_hash(tmp_path,change):
    db,_ = _build(tmp_path)
    path,ids=application_fixture(tmp_path,db)
    lines=path.read_text().splitlines()
    if change=='missing': lines.pop()
    if change=='duplicate': lines.append(lines[1])
    if change=='blank': lines[1]=lines[1].replace(ids[0],'',1)
    if change=='unknown': lines[1]=lines[1].replace(ids[0],'synthetic_unknown',1)
    if change=='bad_date': lines[1]=lines[1].replace('2026-10-16','2026-02-30')
    if change=='bad_status': lines[1]=lines[1].replace(',open,',',invalid,')
    if change=='checked': lines[1]=lines[1].replace('2026-10-03','2026-10-04')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with pytest.raises(ValueError):
        pa.load_policy_apply(path,set(ids),checked_at='2026-10-03',
                             expected_sha256='0'*64 if change=='hash' else None)


def test_final_gate_rejects_nonfinal_db_and_preserves_output(tmp_path):
    db,_=_build(tmp_path); path,_=application_fixture(tmp_path,db)
    out=tmp_path/'bundle'; out.mkdir(); (out/'keep').write_text('synthetic')
    with pytest.raises(es.SubmissionError,match='final 28-policy'):
        es.export(db,out,cases_path=_cases(tmp_path,db),policies_apply_path=path,final_policy_gate=True)
    assert (out/'keep').read_text()=='synthetic'


def test_submission_schema_rejects_linked_factors_and_duplicate_policy_ids():
    from tests.test_submission_report import _case
    case=_case()
    assert case['policies']
    linked=copy.deepcopy(case); linked['policies'][0]['linked_factor_ids']=['tenure']
    assert sr.validate_case(linked)
    case['policies'].append(copy.deepcopy(case['policies'][0]))
    assert 'duplicate submission policy id' in sr.validate_case(case)


def test_checked_in_synthetic_sample():
    root=config.REPO_ROOT/'docs/samples/submission_bundle'
    assert es.verify_bundle(root)==[]
    assert es.claims_findings(root)['findings']==[]
    meta=json.loads((root/'meta.json').read_text(encoding='utf-8'))
    assert meta['data_kind']=='synthetic'
    assert [r['public_id'] for r in meta['cases']]==['CASE-A','CASE-B','CASE-C']
    for file in (root/'cases').glob('*.json'):
        case=json.loads(file.read_text(encoding='utf-8'))
        assert case['data_kind']=='synthetic'
        assert sr.validate_case(case)==[] and sr.forbidden_key_paths(case)==[]
        assert all(p['linked_factor_ids']==[] and p['checked_at']=='2026-10-03' for p in case['policies'])
        assert 'SAMPLE-' not in file.read_text(encoding='utf-8')


@pytest.mark.parametrize('status',['open','closed','unknown'])
def test_application_status_is_data_not_ui_policy(tmp_path,status):
    db,_=_build(tmp_path); path,ids=application_fixture(tmp_path,db)
    path.write_text(path.read_text().replace(',open,',f',{status},'),encoding='utf-8')
    rows,_,_=pa.load_policy_apply(path,set(ids))
    assert all(r['apply_status']==status for r in rows.values())


def test_csv_mismatch_preserves_existing_bundle(tmp_path):
    db,_=_build(tmp_path); path,_=application_fixture(tmp_path,db)
    out=tmp_path/'bundle'; cases=_cases(tmp_path,db)
    es.export(db,out,cases_path=cases,policies_apply_path=path)
    before={p.relative_to(out):p.read_bytes() for p in out.rglob('*') if p.is_file()}
    with pytest.raises(es.SubmissionError,match='hash mismatch'):
        es.export(db,out,cases_path=cases,policies_apply_path=path,policies_apply_sha256='0'*64)
    assert before=={p.relative_to(out):p.read_bytes() for p in out.rglob('*') if p.is_file()}
