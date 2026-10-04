"""CLI regressions: immutable policy review + automatic runtime review, synthetic only."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import select_demo_stores as sel, demo_review_gate as gate_module, check_claims
from scripts.demo_fixture_freeze import POLICY_SHA256, REVIEW_SHA256
from src.serving import build_db as bd, synthetic_samples as syn

FIXTURE=sel.ROOT/'tests/fixtures/demo_preflight'
SUBMISSION=Path(os.environ.get('SUBMISSION_CHECKOUT',sel.ROOT))


@pytest.fixture
def cli_snapshot(tmp_path):
    if not (SUBMISSION/'src/serving/submission_report.py').is_file():
        pytest.skip('Read-only #65 source checkout required; set SUBMISSION_CHECKOUT')
    data=tmp_path/'input'
    kw=syn.write_inputs(data,[s for s in syn.SAMPLES if s[1]=='bundle'],False)
    kw['policies_path']=FIXTURE/'synthetic_policies.json'
    db=tmp_path/'canonical.sqlite'
    bd.build(data/'reports.jsonl',data/'serve_meta.json',data/'licenses.parquet',db,
             license_snapshot_date=syn.SNAPSHOT,purpose='release',**kw)
    config=tmp_path/'config.json'; config.write_text('{}')
    runtime=tmp_path/'runtime_store_review.json'
    args=['--db',str(db),'--policy-review',str(FIXTURE/'synthetic_policy_review.json'),
          '--store-review',str(runtime),'--submission-root',str(SUBMISSION),'--config',str(config),
          '--policy-file',str(FIXTURE/'synthetic_policies.json'),'--policy-sha256',POLICY_SHA256,
          '--policy-checked-at','2026-10-03','--synthetic-fixture']
    return db,runtime,args


def run_cli(args):
    return subprocess.run([sys.executable,str(sel.ROOT/'scripts/select_demo_stores.py'),*args],
                          cwd=sel.ROOT,env=dict(os.environ,PYTHONUTF8='1'),capture_output=True,encoding='utf-8')


def test_frozen_and_runtime_cli_preflight_without_monkeypatch(cli_snapshot,tmp_path):
    db,runtime,args=cli_snapshot
    original=db.read_bytes()
    frozen=(FIXTURE/'synthetic_policy_review.json').read_bytes()
    assert hashlib.sha256(frozen).hexdigest()==REVIEW_SHA256
    assert json.loads(frozen)['db_sha256'] is None and json.loads(frozen)['stores']==[]
    assert run_cli(args+['--build-store-review']).returncode==0
    first=runtime.read_bytes()
    assert run_cli(args+['--build-store-review']).returncode==0 and runtime.read_bytes()==first
    result=run_cli(args+['--preflight-only'])
    assert result.returncode==0,result.stderr
    assert 'preflight passed' in result.stdout
    assert db.read_bytes()==original and (FIXTURE/'synthetic_policy_review.json').read_bytes()==frozen
    assert not (tmp_path/'demo_cases_for_export.json').exists()


@pytest.mark.parametrize('change',['policy_content','policy_bytes','runtime_db','missing','extra','duplicate','manual_flag','gate_digest','count'])
def test_cli_gates_reject_modified_evidence(cli_snapshot,tmp_path,change):
    db,runtime,args=cli_snapshot
    assert run_cli(args+['--build-store-review']).returncode==0
    args=list(args)
    if change.startswith('policy_'):
        path=tmp_path/'changed_policy_review.json'
        content=(FIXTURE/'synthetic_policy_review.json').read_bytes()
        if change=='policy_content':
            obj=json.loads(content);obj['policies'][0]['public_eligible']=False
            content=json.dumps(obj).encode()
        else: content+=b' '
        path.write_bytes(content);args[args.index('--policy-review')+1]=str(path)
    else:
        obj=json.loads(runtime.read_text(encoding='utf-8'))
        if change=='runtime_db': obj['db_sha256']='0'*64
        if change=='missing': obj['stores'].pop()
        if change=='extra': obj['stores'].append(dict(obj['stores'][0],store_id='SYNTHETIC-EXTRA'))
        if change=='duplicate': obj['stores'].append(dict(obj['stores'][0]))
        if change=='manual_flag': obj['stores'][0]['publication_guard_passed']=False
        if change=='gate_digest': obj['gate_sha256']='0'*64
        if change=='count': obj['canonical_count']-=1
        runtime.write_text(json.dumps(obj),encoding='utf-8')
    result=run_cli(args+['--preflight-only'])
    assert result.returncode==2
    assert 'selection error:' in result.stderr
    assert all(r['store_id'] not in result.stderr+result.stdout for r in json.loads(runtime.read_text())['stores'])


def test_uniform_builder_order_and_checker_parity(cli_snapshot,tmp_path):
    db,runtime,args=cli_snapshot
    with sel.sqlite3.connect(db) as conn:
        reports=list(bd.iter_reports(conn))
    gate=gate_module.SubmissionGate(SUBMISSION,sel.ROOT)
    first=gate.review(reports,sel.sha256_file(db))
    assert first==gate.review(list(reversed(reports)),sel.sha256_file(db))
    assert len(first['stores'])==len(reports)
    assert all(r['publication_guard_passed'] and r['claims_passed'] for r in first['stores'])
    path=tmp_path/'cases/CASE-A.json';path.parent.mkdir()
    # Same JSON normalization, scopes, compiled patterns and allowlist semantics as #56.
    for content in [dict(text='합성 안전 문구'),dict(text='블로그 언급으로 매출이 3.3% 증가'),
                    dict(text='GR_3000000-000-2020-00001'),dict(text='리뷰'),
                    dict(text='리뷰 claims-allow: CL-13')]:
        text=json.dumps(content,ensure_ascii=True,indent=2)+'\n'
        path.write_text(text,encoding='utf-8')
        findings=check_claims.scan([path],gate.rules,gate.allowlist,base=tmp_path)['findings']
        assert gate_module.claims_passed(text,gate.rules,gate.allowlist)==(not findings)
    changed=deepcopy(reports)
    # #65 permits schema-valid peer labels, then explicitly rejects identifier bytes.
    changed[0]['store']['gu']=reports[-1]['store_id']
    changed[0]['risk']['peer_group']=f"{changed[0]['store']['gu']} {changed[0]['store']['biz_type']}"
    reviewed=gate.review(changed,sel.sha256_file(db))
    assert not next(r for r in reviewed['stores'] if r['store_id']==changed[0]['store_id'])['publication_guard_passed']
    altered=deepcopy(reports);altered[0]['policies'][0]['name']='리뷰'
    reviewed=gate.review(altered,sel.sha256_file(db))
    assert not next(r for r in reviewed['stores'] if r['store_id']==altered[0]['store_id'])['claims_passed']


def test_synthetic_selection_rule_and_adapter_unchanged(cli_snapshot,tmp_path):
    db,runtime,args=cli_snapshot
    assert run_cli(args+['--build-store-review']).returncode==0
    private=tmp_path/'private.json';public=tmp_path/'public.json';adapter=tmp_path/'demo_cases_for_export.json'
    result=run_cli(args+['--private-out',str(private),'--public-out',str(public),'--export-out',str(adapter)])
    assert result.returncode==0,result.stderr
    rows=json.loads(adapter.read_text())['cases']
    assert len({r['store_id'] for r in rows})==3
    assert all(set(r)=={'case_label','store_id','rule_stage','n_candidates','seed'} for r in rows)
    assert [r['seed'] for r in rows]==[20261004,20261005,20261006]
    assert json.loads(public.read_text(encoding='utf-8'))['provenance']['rule_version']=='0.3'


def test_synthetic_profile_cannot_accept_real_canonical_identifiers(cli_snapshot):
    db,runtime,args=cli_snapshot
    with sel.sqlite3.connect(db) as conn:
        conn.execute("UPDATE risk SET model='detect_v0_enriched'")
    result=run_cli(args+['--build-store-review'])
    assert result.returncode==2 and not runtime.exists()
    assert 'synthetic fixture records required' in result.stderr


def test_runtime_private_path_and_input_protection(cli_snapshot):
    db,runtime,args=cli_snapshot
    args=list(args);args[args.index('--store-review')+1]=str(sel.ROOT/'docs/runtime_store_review.json')
    assert run_cli(args+['--build-store-review']).returncode==2
    args[args.index('--store-review')+1]=str(db)
    assert run_cli(args+['--build-store-review']).returncode==2


def test_identifier_matcher_overlaps_and_unicode():
    patterns={'SYN-ID-A','ID-A','가상참조','ABA','BAB'}
    matcher=gate_module.IdentifierMatcher(patterns)
    for text in ['nothing','xxSYN-ID-Ayy','x가상참조y','ABABA','AB','BAB','xyzID-A']:
        assert matcher.contains(text)==any(p in text for p in patterns)
