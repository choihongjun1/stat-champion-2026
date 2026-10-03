"""Synthetic-only T5 rule, provenance and disclosure regression tests."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import random
from pathlib import Path

import pytest

from scripts import select_demo_stores as sel
from scripts import check_claims as claims
from src.serving import build_db as bd, public_report as pr
from tests.serving_synth import store, write_inputs
from tests.test_report_schema import _report_basic


def c(n, band='high', tailored=2, sensitive=False, factors=2, biz='일반음식점'):
    return sel.Candidate(f'SYN-DEMO-{n:03d}','마포구',biz,band,sensitive,factors,tailored)


def chosen(pool, label='A'):
    return sel.choose_cases(pool)[label]


@pytest.mark.parametrize('pool,stage,number',[
    ([c(1),c(2,tailored=1)],'기본',1),
    ([c(1,tailored=1)],'대안1',1),
    ([c(1,band='mid')],'대안2',1),
    ([c(1,band='low')],'없음',0),
    ([], '없음',0),
])
def test_a_rule_and_fallbacks(pool,stage,number):
    row=chosen(pool)
    assert row['rule_stage']==stage
    assert row['candidate_count']==number
    assert bool(row['candidate'])==bool(number)


@pytest.mark.parametrize('minimum',[0,1,2,4])
def test_b_uses_actual_high_minimum(minimum):
    pool=[c(1,tailored=minimum+2),c(2,tailored=minimum),
          c(3,tailored=minimum+1),c(4,band='low',tailored=0)]
    row=chosen(pool,'B')
    assert row['candidate']==pool[1]
    assert row['tailored_policy_count_min']==minimum
    assert row['candidate_count']==1 and row['rule_stage']=='기본'


def test_b_excludes_a_before_minimum_and_has_explicit_empty_value():
    pool=[c(1,tailored=2)]
    row=chosen(pool,'B')
    assert row['candidate'] is None and row['tailored_policy_count_min'] is None
    assert row['rule_stage']=='없음'
    result=sel.choose_cases(pool+[c(2,tailored=2)])
    assert result['A']['candidate']!=result['B']['candidate']
    assert result['B']['tailored_policy_count_min']==2


def test_c_prefers_a_industry_and_falls_back():
    pool=[c(1),c(2,band='low',tailored=0,biz='미용업'),c(3,band='low',tailored=0)]
    assert chosen(pool,'C')['candidate'].store_id==c(3).store_id
    row=chosen(pool[:-1],'C')
    assert row['candidate'].store_id==c(2).store_id and row['rule_stage']=='대안1'
    assert chosen([c(1)],'C')['rule_stage']=='없음'


def test_priority_before_random_top_ten():
    # Exactly ten outrank the unstable/less detailed candidate: seed cannot reach it.
    stable=[c(i,sensitive=False,factors=3) for i in range(1,11)]
    assert chosen(stable+[c(99,sensitive=True,factors=99)])['candidate'] in stable
    assert chosen(stable+[c(99,factors=1)])['candidate'] in stable
    many=[c(i,tailored=3,sensitive=True,factors=1) for i in range(1,11)]
    assert chosen(many+[c(99,tailored=2,sensitive=False,factors=99)])['candidate'] in many
    b=[c(i,tailored=0,factors=3) for i in range(1,11)]
    assert chosen(b+[c(99,tailored=0,sensitive=True,factors=99)],'B')['candidate'] in b
    assert chosen(b+[c(99,tailored=0,factors=1)],'B')['candidate'] in b
    low=[c(i,band='low',sensitive=False) for i in range(1,11)]
    assert chosen(low+[c(99,band='low',sensitive=True)],'C')['candidate'] in low


def test_stable_top10_reproducibility_and_case_rng_independence():
    a=[c(i) for i in range(1,21)]
    b=[c(i,tailored=0) for i in range(21,41)]
    low=[c(i,band='low',tailored=0) for i in range(41,61)]
    result=sel.choose_cases(a+b+low)
    assert result==sel.choose_cases(list(reversed(a+b+low)))
    assert result['A']['candidate']==random.Random(sel.SEEDS['A']).choice(sorted(a, key=lambda c: hashlib.sha256(
        f"{sel.SEEDS['A']}:{c.store_id}".encode()).hexdigest())[:10])
    changed=sel.choose_cases([c(70)]+b+low)
    assert result['B']==changed['B'] and result['C']==changed['C']
    assert len({r['candidate'].store_id for r in result.values()})==3
    assert all(r['top_count']==10 and r['candidate_count']==20 for r in result.values())


def policies():
    def p(pid,gu=None,biz=None):
        return dict(id=pid,name='(예시) 지원',operator='(예시) 기관',link='https://example.org',
                    announce_year=2026,collected_at='2026-10-01',eligibility_text='합성 자격',
                    conditions=dict(gu=gu,biz_type=biz,tenure_months_min=None,tenure_months_max=None),
                    unverifiable_conditions=[],related_factor_ids=[])
    return [p('common'),p('biz',biz=['일반음식점']),p('district',gu=['마포구'])]


def test_report_mapping_and_probability_invariance():
    record=_report_basic()
    source=policies()
    record['policies']=[]
    for p in source:
        match=dict(id=p['id'],name=p['name'],operator=p['operator'],link=p['link'],
                   eligibility_text=p['eligibility_text'],announce_year=p['announce_year'],collected_at=p['collected_at'],
                   match_status='matched',matched_by=[k for k in ('gu','biz_type') if p['conditions'][k]],
                   unverifiable_conditions=[],linked_factor_ids=[],check_note=None)
        if 'gu' not in match['matched_by']:
            record['policies'].append(match)
    pool=sel.candidates_from_reports([record],{record['store_id']},{p['id'] for p in source},source)
    altered=deepcopy(record)
    altered['risk'].update(probability_12m=0.72,ci_low=0.7,ci_high=0.8)
    assert pool==sel.candidates_from_reports([altered],{record['store_id']},{p['id'] for p in source},source)
    assert pool[0].displayed_factors==sum(f['display'] for f in record['factors'])
    altered['factors'][0]['interpretation_sensitive']=True
    altered['factors'][0]['sensitivity_label']='해석 민감'
    assert sel.candidates_from_reports([altered],{record['store_id']},{p['id'] for p in source},source)[0].sensitive


@pytest.fixture
def frozen(tmp_path):
    data=write_inputs(tmp_path/'input',[
        store(1,'마포구','샘플동','일반음식점',band='high'),
        store(2,'광진구','샘플동','미용업',band='high'),
        store(3,'마포구','샘플동','일반음식점',band='low')])
    policy=data/'policies.json'
    policy.write_text(json.dumps(policies(),ensure_ascii=False),encoding='utf-8')
    db=tmp_path/'report.sqlite'
    bd.build(data/'reports.jsonl',data/'serve_meta.json',data/'licenses.parquet',db,
             policies_path=policy,purpose='release',license_snapshot_date='2026-09-11')
    with sel.sqlite3.connect(db) as conn:
        reports=list(bd.iter_reports(conn))
    review=dict(contract_version='demo-review-0.1',db_sha256=sel.sha256_file(db),
                policies_sha256=sel.sha256_file(policy),policy_checked_at='2026-10-01',
                score_origin='2026Q2',as_of='2026-06-30',
                stores=[dict(store_id=r['store_id'],publication_guard_passed=True,claims_passed=True) for r in reports],
                policies=[dict(id=p['id'],public_eligible=True,apply_status='open',checked_at='2026-10-01') for p in policies()])
    review_path=tmp_path/'review.json'
    review_path.write_text(json.dumps(review),encoding='utf-8')
    return db,policy,review_path,review


def execute(frozen):
    db,policy,review,_=frozen
    return sel.read_frozen_inputs(db,policy,review,sel.sha256_file(policy),'2026-10-01')


@pytest.mark.parametrize('status',['matched','check_required'])
@pytest.mark.parametrize('common_status',['matched','check_required'])
def test_tailored_counts_both_statuses_but_not_common(frozen,status,common_status):
    db,policy,path,review=frozen
    original_pool,_=execute(frozen)
    with sel.sqlite3.connect(db) as conn:
        for pid,value in [('biz',status),('district',status),('common',common_status)]:
            needs_check=value=='check_required'
            conn.execute('UPDATE store_policies SET match_status=?, unverifiable_conditions_json=?, check_note=? WHERE policy_id=?',
                         (value,json.dumps(['합성 확인 조건'] if needs_check else []),
                          '합성 확인 필요' if needs_check else None,pid))
    review['db_sha256']=sel.sha256_file(db)
    path.write_text(json.dumps(review),encoding='utf-8')
    pool,_=execute(frozen)
    assert pool==original_pool
    assert sel.choose_cases(pool)==sel.choose_cases(original_pool)
    assert sorted(c.tailored_policy_count for c in pool)==[0,2,2]
    assert chosen(pool)['rule_stage']=='기본'
    assert chosen(pool,'B')['tailored_policy_count_min']==0


@pytest.mark.parametrize('extra',[0,1,3])
def test_changed_synthetic_policy_source_changes_b_minimum(tmp_path,extra):
    data=write_inputs(tmp_path/'input',[
        store(1,'마포구','샘플동','일반음식점',band='high'),
        store(2,'광진구','샘플동','미용업',band='high')])
    source=policies()
    for i in range(extra):
        policy=deepcopy(source[1])
        policy['id']=f'synthetic_extra_{i}'
        policy['conditions']['biz_type']=['일반음식점','미용업']
        policy['unverifiable_conditions']=['합성 확인 조건']
        source.append(policy)
    policy_path=data/'policies.json'
    policy_path.write_text(json.dumps(source,ensure_ascii=False),encoding='utf-8')
    db=tmp_path/'report.sqlite'
    bd.build(data/'reports.jsonl',data/'serve_meta.json',data/'licenses.parquet',db,
             policies_path=policy_path,purpose='release',license_snapshot_date='2026-09-11')
    with sel.sqlite3.connect(db) as conn:
        reports=list(bd.iter_reports(conn))
    pool=sel.candidates_from_reports(reports,{r['store_id'] for r in reports},
                                     {p['id'] for p in source},source)
    row=chosen(pool,'B')
    assert row['candidate'].biz_type=='미용업'
    assert row['tailored_policy_count_min']==extra


@pytest.mark.parametrize('minimum',[0,1,4,None])
def test_public_b_minimum_contract(minimum):
    provenance=dict(rule_version=sel.RULE_VERSION,db_sha256='a'*64,policies_sha256='b'*64,review_sha256='c'*64,
                    policy_checked_at='2026-10-01',policy_count=3,policy_collected_at=['2026-10-01'],policy_path='private')
    pool=[] if minimum is None else [c(1,tailored=minimum+2),c(2,tailored=minimum)]
    private,public=sel.output_documents(sel.choose_cases(pool),provenance,'d'*40)
    sel.validate_public_output(public)
    assert public['cases'][1]['tailored_policy_count_min']==minimum
    assert private['cases'][1]['tailored_policy_count_min']==minimum
    for invalid in (-1,True,'1'):
        altered=deepcopy(public)
        altered['cases'][1]['tailored_policy_count_min']=invalid
        with pytest.raises(sel.SelectionError,match='tailored minimum'):
            sel.validate_public_output(altered)


def test_readonly_sqlite_and_public_private_contract(frozen,tmp_path):
    db,policy,review_path,_=frozen
    before=db.read_bytes()
    pool,provenance=execute(frozen)
    assert db.read_bytes()==before
    private,public=sel.output_documents(sel.choose_cases(pool),provenance,'a'*40)
    assert all('store_id' not in r for r in private['cases'])
    assert len({r['store_id'] for r in sel.export_cases_document(sel.choose_cases(pool))['cases']})==3
    sel.validate_public_output(public)
    assert not pr.private_key_paths(public)
    encoded=json.dumps(public,ensure_ascii=False)
    for forbidden in ('store_id','name','address','probability_12m','ci_low','ci_high','interval_note','peer_median'):
        assert forbidden not in encoded
    for key in ('db_sha256','policies_sha256','review_sha256'):
        assert public['provenance'][key+'_prefix']==private['provenance'][key][:16]
        assert private['provenance'][key] not in encoded
    rules,allows=claims.load_config(sel.ROOT/'configs/claims_rules.json',sel.ROOT/'configs/claims_allowlist.json')
    path=tmp_path/'public.json'; path.write_text(encoded,encoding='utf-8')
    assert not claims.scan([path],rules,allows)['findings']
    private_path=tmp_path/'private.json'
    sel.write_outputs(private,public,private_path,path)
    sel.write_outputs(private,public,private_path,path)
    altered=deepcopy(public); altered['provenance']['policy_checked_at']='2026-10-02'
    with pytest.raises(sel.SelectionError,match='do not automatically reselect'):
        sel.write_outputs(private,altered,private_path,path)
    public['cases'][0]['store_id']='SYN-DEMO-001'
    with pytest.raises(sel.SelectionError,match='public case fields'):
        sel.validate_public_output(public)


def test_policy_mismatch_stops_before_opening_db(frozen,monkeypatch):
    db,policy,review,_=frozen
    expected=sel.sha256_file(policy)
    policy.write_bytes(policy.read_bytes()+b' ')
    monkeypatch.setattr(sel.sqlite3,'connect',lambda *a,**k:pytest.fail('DB opened'))
    with pytest.raises(sel.SelectionError,match='policy hash mismatch'):
        sel.read_frozen_inputs(db,policy,review,expected,'2026-10-01')


@pytest.mark.parametrize('change',['db_hash','policy_hash','date','unknown_store','guard','claims','closed','unknown','tailored_check_required'])
def test_review_provenance_and_publication_evidence(frozen,change):
    db,policy,path,review=frozen
    if change=='db_hash': review['db_sha256']='0'*64
    if change=='policy_hash': review['policies_sha256']='0'*64
    if change=='date': review['policy_checked_at']='2026-10-02'
    if change=='unknown_store': review['stores'][0]['store_id']='SYN-NOT-PRESENT'
    if change=='guard': review['stores'][0]['publication_guard_passed']=False
    if change=='claims': review['stores'][0]['claims_passed']=False
    if change in ('closed','unknown'):
        for p in review['policies']:
            if p['id']!='common': p['apply_status']=change
    if change=='tailored_check_required':
        with sel.sqlite3.connect(db) as conn:
            conn.execute("UPDATE store_policies SET match_status='check_required', unverifiable_conditions_json='[\"합성 확인 조건\"]',check_note='합성 확인 필요' WHERE policy_id='biz'")
        review['db_sha256']=sel.sha256_file(db)
    path.write_text(json.dumps(review),encoding='utf-8')
    if change in ('db_hash','policy_hash','date','unknown_store'):
        with pytest.raises(sel.SelectionError): execute(frozen)
    else:
        pool,_=execute(frozen)
        result=sel.choose_cases(pool)
        if change in ('guard','claims','closed','unknown'):
            assert result['A']['candidate'] is None
        else:
            assert result['A']['rule_stage']=='기본'
            assert result['A']['candidate'].tailored_policy_count==2


def test_cli_uses_future_regen_freeze_and_safe_errors(frozen,tmp_path,capsys,monkeypatch):
    db,policy,review,_=frozen
    config=tmp_path/'regen.json'
    config.write_text(json.dumps({'demo':{'policy':{'path':str(policy),'sha256':sel.sha256_file(policy),'checked_at':'2026-10-01'}}}),encoding='utf-8')
    args=['--db',str(db),'--review',str(review),'--config',str(config),
          '--private-out',str(tmp_path/'private.json'),'--public-out',str(tmp_path/'public.json')]
    monkeypatch.setattr(sel, 'FINAL_POLICY_SHA256', sel.sha256_file(policy))
    monkeypatch.setattr(sel, 'FINAL_REVIEW_SHA256', sel.sha256_file(review))
    monkeypatch.setattr(sel, 'FINAL_CHECKED_AT', '2026-10-01')
    original=sel.read_frozen_inputs
    def synthetic_read(*args):
        pool, provenance=original(*args)
        provenance['policy_count']=28
        return pool, provenance
    monkeypatch.setattr(sel, 'read_frozen_inputs', synthetic_read)
    assert sel.main(args)==0
    assert (tmp_path/'demo_cases_for_export.json').is_file()
    assert 'store_id' not in capsys.readouterr().out
    policy.write_bytes(policy.read_bytes()+b' ')
    assert sel.main(args)==2
    message=capsys.readouterr().err
    assert 'policy hash mismatch' in message and str(policy) not in message


def test_private_output_cannot_be_committed(tmp_path):
    provenance=dict(rule_version=sel.RULE_VERSION,db_sha256='a'*64,policies_sha256='b'*64,review_sha256='c'*64,
                    policy_checked_at='2026-10-01',policy_count=3,policy_collected_at=['2026-10-01'],policy_path='private')
    private,public=sel.output_documents(sel.choose_cases([]),provenance,'d'*40)
    with pytest.raises(sel.paths.UnsafeOutputPath):
        sel.write_outputs(private,public,sel.ROOT/'docs/private.json',tmp_path/'public.json')


def test_unfinalized_db_is_not_read(frozen):
    db,*_=frozen
    wal=Path(str(db)+'-wal')
    wal.write_bytes(b'synthetic journal')
    with pytest.raises(sel.SelectionError,match='not finalized'):
        execute(frozen)
    wal.unlink()


@pytest.mark.parametrize('kind',['missing_store_flag','missing_policy','bad_review_type'])
def test_incomplete_review_fails_closed(frozen,kind):
    db,policy,path,review=frozen
    if kind=='missing_store_flag': del review['stores'][0]['claims_passed']
    elif kind=='missing_policy': review['policies'].pop()
    else: review=[]
    path.write_text(json.dumps(review),encoding='utf-8')
    with pytest.raises(sel.SelectionError): execute(frozen)


@pytest.mark.parametrize('partial',[True,False])
def test_canonical_population_completeness(frozen,partial):
    db,policy,path,review=frozen
    pool,_=execute(frozen)
    assert len(pool)==3
    if partial:
        review['stores']=review['stores'][:1]
    else:
        review['stores'].append(dict(store_id='SYN-UNKNOWN',publication_guard_passed=True,claims_passed=True))
    path.write_text(json.dumps(review),encoding='utf-8')
    with pytest.raises(sel.SelectionError,match='canonical population'):
        execute(frozen)


def test_random_hash_precedes_top10_and_is_not_lexical():
    pool=[c(i) for i in range(1,101)]
    result=chosen(pool)
    ordered=sorted(pool,key=lambda c:hashlib.sha256(f"{sel.SEEDS['A']}:{c.store_id}".encode()).hexdigest())
    assert result['candidate']==random.Random(sel.SEEDS['A']).choice(ordered[:10])
    assert set(ordered[:10]) != set(pool[:10])
    assert any(c not in pool[:10] for c in ordered[:10])
    assert sel.RULE_VERSION=='0.3'


def test_adapter_mapping_and_private_path_guard(tmp_path):
    selection=sel.choose_cases([c(1,band='mid'),c(2,band='low')])
    adapter=sel.export_cases_document(selection)
    assert [r['rule_stage'] for r in adapter['cases']]==['alt2','none','base']
    assert all(set(r)=={'case_label','store_id','rule_stage','n_candidates','seed'} for r in adapter['cases'])
    provenance=dict(rule_version=sel.RULE_VERSION,db_sha256='a'*64,policies_sha256='b'*64,review_sha256='c'*64,
                    policy_checked_at='2026-10-03',policy_count=28,policy_collected_at=['2026-10-03'],policy_path='private')
    private,public=sel.output_documents(selection,provenance,'d'*40)
    with pytest.raises(sel.paths.UnsafeOutputPath):
        sel.write_outputs(private,public,tmp_path/'private.json',tmp_path/'public.json',
                          export_cases=adapter,export_path=sel.ROOT/'docs/demo_cases_for_export.json')


def test_cli_rejects_nonfinal_policy_freeze_before_db(frozen,tmp_path,monkeypatch):
    db,policy,review,_=frozen
    cfg=tmp_path/'config.json'; cfg.write_text('{}')
    monkeypatch.setattr(sel.sqlite3,'connect',lambda *a,**k:pytest.fail('DB opened'))
    assert sel.main(['--db',str(db),'--review',str(review),'--config',str(cfg),
                     '--policy-file',str(policy),'--policy-sha256',sel.sha256_file(policy),
                     '--policy-checked-at','2026-10-03'])==2


def test_direct_submission_consumer_integration(tmp_path):
    import os
    import subprocess
    import sys
    root=Path(os.environ.get('SUBMISSION_CHECKOUT',sel.ROOT.parent/'submission65'))
    if not (root/'tests/test_demo_submission_integration.py').is_file():
        pytest.skip('Set SUBMISSION_CHECKOUT to the #65 checkout for cross-PR integration')
    env=dict(os.environ,DEMO_SELECTOR_PATH=str(sel.ROOT/'scripts/select_demo_stores.py'),PYTHONUTF8='1')
    result=subprocess.run([sys.executable,'-m','pytest','tests/test_demo_submission_integration.py','-q'],
                          cwd=root,env=env,capture_output=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('gate',['count','review'])
def test_cli_final_count_and_review_gate(frozen,tmp_path,monkeypatch,gate):
    db,policy,review,_=frozen
    cfg=tmp_path/'config.json'; cfg.write_text('{}')
    monkeypatch.setattr(sel,'FINAL_POLICY_SHA256',sel.sha256_file(policy))
    monkeypatch.setattr(sel,'FINAL_CHECKED_AT','2026-10-01')
    if gate=='count':
        monkeypatch.setattr(sel,'FINAL_REVIEW_SHA256',sel.sha256_file(review))
    else:
        monkeypatch.setattr(sel.sqlite3,'connect',lambda *a,**k:pytest.fail('DB opened'))
    assert sel.main(['--db',str(db),'--review',str(review),'--config',str(cfg),
                     '--policy-file',str(policy),'--policy-sha256',sel.sha256_file(policy),
                     '--policy-checked-at','2026-10-01'])==2
    assert not (tmp_path/'demo_cases_for_export.json').exists()
