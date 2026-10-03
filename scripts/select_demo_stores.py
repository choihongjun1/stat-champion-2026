"""Frozen T5/H2 demo selection from a read-only SQLite canonical report (no model runs)."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.serving import build_db as bd
from src.serving import paths, report_validation as rv

RULE_VERSION = '0.3'
FINAL_POLICY_SHA256 = '0cfb716fbe9272efc816d34d2213354d3ed6cdccbc9851080eee7f40c3eefc2a'
FINAL_REVIEW_SHA256 = '75d05611f9dc16ea267a5a251303a4d0dd6733f4022aea81aec0565d27529ac6'
FINAL_CHECKED_AT = '2026-10-03'
SEEDS = {'A': 20261004, 'B': 20261005, 'C': 20261006}
NO_CASE = '적합한 비식별 실제 사례 없음'
GU = {'광진구', '마포구', '영등포구'}
BIZ = {'일반음식점', '휴게음식점', '미용업'}


class SelectionError(ValueError):
    """Messages contain fixed categories only, never input values or private paths."""


def require(condition, message):
    if not condition:
        raise SelectionError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@dataclass(frozen=True)
class Candidate:
    # This type is private; only the explicit public output projection may be shared.
    store_id: str
    gu: str
    biz_type: str
    band: str
    sensitive: bool
    displayed_factors: int
    tailored_policy_count: int


def displayed_factor_count(factors):
    """Count display=true factors; report schema enforces display ⇔ hold_reason=null."""
    return sum(f['display'] is True for f in factors)


def candidates_from_reports(reports, approved_store_ids, approved_policy_ids, policy_sources):
    """Use existing matched_by/conditions, never risk probabilities or contribution magnitudes."""
    source = {p['id']: p for p in policy_sources}
    result, seen = [], set()
    for report in reports:
        require(not rv.validate_report(report), 'invalid canonical report')
        require(report['score_origin'] == '2026Q2' and report['as_of'] == '2026-06-30',
                'unexpected serving period')
        sid = report['store_id']
        require(sid not in seen, 'duplicate canonical record')
        seen.add(sid)
        require(report['policy_matching'] == 'performed', 'policy matching was not performed')
        if sid not in approved_store_ids:
            continue
        require(report['store']['gu'] in GU and report['store']['biz_type'] in BIZ,
                'unsupported public category')
        tailored = 0
        for match in report['policies']:
            pid = match['id']
            require(pid in source, 'matched policy absent from frozen source')
            cond = source[pid]['conditions']
            expected = {k for k in ('gu', 'biz_type') if cond[k] is not None}
            require(set(match['matched_by']) & {'gu', 'biz_type'} == expected,
                    'policy conditions and matching evidence differ')
            require(all(report['store'][k] in cond[k] for k in expected),
                    'policy matching evidence differs from store category')
            if pid not in approved_policy_ids:
                continue
            # Both schema-supported statuses display a card. Status describes
            # eligibility verification, not whether the policy is tailored.
            tailored += bool(expected)
        result.append(Candidate(sid, report['store']['gu'], report['store']['biz_type'],
                                report['risk']['band'],
                                any(f['interpretation_sensitive'] for f in report['factors']),
                                displayed_factor_count(report['factors']), tailored))
    require(approved_store_ids <= seen, 'review contains unknown store references')
    return result


def choose_cases(candidates):
    """Seeded hash across the entire pool before top ten, independent per-case RNGs."""
    require(len({c.store_id for c in candidates}) == len(candidates), 'duplicate candidate')
    selected, used = {}, set()

    def pick(label, pool, key, stage):
        ordered = sorted(pool, key=lambda c: (*key(c), hashlib.sha256(
            f'{SEEDS[label]}:{c.store_id}'.encode('utf-8')).hexdigest()))
        top = ordered[:10]
        chosen = random.Random(SEEDS[label]).choice(top) if top else None
        selected[label] = dict(candidate=chosen, rule_stage=stage if top else '없음',
                               candidate_count=len(ordered), top_count=len(top), seed=SEEDS[label])
        if chosen:
            used.add(chosen.store_id)

    pool, stage = [], '없음'
    for band, count, name in [('high', 2, '기본'), ('high', 1, '대안1'), ('mid', 2, '대안2')]:
        pool = [c for c in candidates if c.band == band and c.tailored_policy_count >= count]
        if pool:
            stage = name
            break
    pick('A', pool, lambda c: (-c.tailored_policy_count, c.sensitive, -c.displayed_factors), stage)
    high = [c for c in candidates if c.store_id not in used and c.band == 'high']
    minimum = min((c.tailored_policy_count for c in high), default=None)
    pool = [c for c in high if c.tailored_policy_count == minimum]
    pick('B', pool, lambda c: (c.sensitive, -c.displayed_factors), '기본')
    selected['B']['tailored_policy_count_min'] = minimum
    pool = [c for c in candidates if c.store_id not in used and c.band == 'low']
    a = selected['A']['candidate']
    same = [c for c in pool if a and c.biz_type == a.biz_type]
    pick('C', same or pool, lambda c: (c.sensitive,), '기본' if same else '대안1')
    return selected


def read_policy_snapshot(db, policy_file, review_file, expected_sha256, checked_at):
    require(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256),
            'full policy digest is required')
    date.fromisoformat(checked_at)
    # Hash gate happens before SQLite/report access. File contents are read once and reused.
    policy_bytes = Path(policy_file).read_bytes()
    require(hashlib.sha256(policy_bytes).hexdigest() == expected_sha256, 'policy hash mismatch; selection stopped')
    policies = json.loads(policy_bytes.decode('utf-8-sig'))
    if isinstance(policies, dict):
        policies = policies.get('policies')
    require(isinstance(policies, list) and bool(policies), 'invalid policy source')
    require(all(not rv.validate_def('policy_source', p) for p in policies), 'unsupported policy source contract')
    ids = {p['id'] for p in policies}
    require(len(ids) == len(policies), 'duplicate policy source identifier')
    require(not any(Path(str(db)+suffix).exists() for suffix in ('-wal','-journal')),
            'SQLite snapshot is not finalized')
    db_hash = sha256_file(db)
    review_bytes = Path(review_file).read_bytes()
    review = json.loads(review_bytes.decode('utf-8-sig'))
    require(isinstance(review, dict), 'invalid review document')
    require(review.get('contract_version') == 'demo-review-0.1', 'missing review contract')
    require(review.get('policies_sha256') == expected_sha256,
            'review provenance mismatch')
    require(review.get('policy_checked_at') == checked_at, 'policy confirmation date mismatch')
    require(review.get('score_origin') == '2026Q2' and review.get('as_of') == '2026-06-30', 'review period mismatch')
    require(isinstance(review.get('policies'), list), 'missing policy review')
    reviewed_policies = review['policies']
    require(len({p['id'] for p in reviewed_policies}) == len(reviewed_policies), 'duplicate policy review')
    require({p['id'] for p in reviewed_policies} == ids, 'incomplete policy review')
    require(all(type(p.get('public_eligible')) is bool and p.get('apply_status') in {'open','closed','unknown'}
                and p.get('checked_at') == checked_at for p in reviewed_policies), 'invalid policy review evidence')
    approved_policies = {p['id'] for p in reviewed_policies if p['public_eligible'] and p['apply_status'] == 'open'}
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        run = bd.read_run(conn)
        require(not bd.release_blockers(run), 'canonical release contract not ready')
        require(run['policies_sha256'] == expected_sha256 and run['n_policies'] == len(policies),
                'SQLite frozen policy provenance mismatch')
        stored_conditions = {pid:json.loads(value) for pid,value in
                             conn.execute('SELECT policy_id, conditions_json FROM policies')}
        require(stored_conditions == {p['id']:p['conditions'] for p in policies},
                'SQLite policy conditions differ from frozen source')
        reports = list(bd.iter_reports(conn))
    finally:
        conn.close()
    require(len(reports) == run['n_stores'] and bool(reports), 'canonical record count mismatch')
    require(all(not rv.validate_report(r) and r['score_origin']=='2026Q2' and r['as_of']=='2026-06-30'
                for r in reports), 'invalid canonical serving population')
    require(sha256_file(db) == db_hash, 'SQLite changed while reading')
    provenance = dict(rule_version=RULE_VERSION, db_sha256=db_hash, policies_sha256=expected_sha256,
                      review_sha256=hashlib.sha256(review_bytes).hexdigest(), policy_checked_at=checked_at,
                      policy_count=len(policies), policy_collected_at=sorted({p['collected_at'] for p in policies}),
                      policy_path=str(Path(policy_file).resolve()))
    return reports, approved_policies, policies, provenance


def validate_store_review(review,reports,db_hash):
    require(isinstance(review,dict), 'invalid store review document')
    require(review.get('db_sha256')==db_hash, 'store review DB provenance mismatch')
    require(review.get('score_origin')=='2026Q2' and review.get('as_of')=='2026-06-30', 'store review period mismatch')
    rows=review.get('stores')
    require(isinstance(rows,list), 'missing store review')
    require(all(isinstance(r,dict) and isinstance(r.get('store_id'),str) for r in rows), 'invalid store review row')
    ids=[r['store_id'] for r in rows]
    require(len(ids)==len(set(ids)), 'duplicate store review')
    canonical={r['store_id'] for r in reports}
    require(len(rows)==len(reports) and set(ids)==canonical, 'incomplete canonical population review')
    require(all(type(r.get('publication_guard_passed')) is bool and type(r.get('claims_passed')) is bool
                for r in rows), 'invalid store review evidence')
    return {r['store_id'] for r in rows if r['publication_guard_passed'] and r['claims_passed']}


def read_frozen_inputs(db,policy_file,review_file,expected_sha256,checked_at,*,store_review_file=None,gate=None):
    reports,approved_policies,policies,provenance=read_policy_snapshot(
        db,policy_file,review_file,expected_sha256,checked_at)
    runtime_path=store_review_file or review_file  # low-level combined synthetic test compatibility
    runtime_bytes=Path(runtime_path).read_bytes()
    runtime=json.loads(runtime_bytes.decode('utf-8-sig'))
    approved_stores=validate_store_review(runtime,reports,provenance['db_sha256'])
    if gate is not None:
        require(runtime==gate.review(reports,provenance['db_sha256']), 'runtime review differs from automatic gate')
    if store_review_file is not None:
        policy_digest=provenance['review_sha256']
        runtime_digest=hashlib.sha256(runtime_bytes).hexdigest()
        provenance.update(policy_review_digest=policy_digest,store_review_digest=runtime_digest)
        provenance['review_sha256']=hashlib.sha256(f'{policy_digest}:{runtime_digest}'.encode()).hexdigest()
    require(sha256_file(db)==provenance['db_sha256'], 'SQLite changed while reviewing')
    return candidates_from_reports(reports,approved_stores,approved_policies,policies),provenance


def output_documents(selection, provenance, commit):
    require(bool(re.fullmatch('[0-9a-f]{40}', commit)), 'invalid generation commit')
    public_provenance = {k + '_prefix': v[:16] for k,v in provenance.items() if k.endswith('_sha256')}
    public_provenance.update({k:provenance[k] for k in
                             ('rule_version','policy_checked_at','policy_count','policy_collected_at')})
    public = dict(contract_version='demo-selection-public-0.1', score_origin='2026Q2', as_of='2026-06-30',
                  generation_commit=commit, provenance=public_provenance, cases=[])
    private = {**public, 'contract_version':'demo-selection-private-0.1', 'provenance':dict(provenance), 'cases':[]}
    for label, item in selection.items():
        c = item['candidate']
        row = dict(case=label, label=f'비식별 실제 사례 {label}', gu=c.gu if c else None,
                   biz_type=c.biz_type if c else None, band=c.band if c else None,
                   status='선택' if c else NO_CASE, **{k:item[k] for k in ('rule_stage','candidate_count','top_count','seed')})
        if label == 'B':
            row['tailored_policy_count_min'] = item['tailored_policy_count_min']
        public['cases'].append(row)
        private['cases'].append(dict(row))
    return private, public


def validate_public_output(public):
    """Small allowlist contract for the selection summary (not a public store report)."""
    require(set(public) == {'contract_version','score_origin','as_of','generation_commit','provenance','cases'},
            'invalid public summary fields')
    require(public['contract_version'] == 'demo-selection-public-0.1'
            and public['score_origin'] == '2026Q2' and public['as_of'] == '2026-06-30', 'invalid public summary period')
    require(bool(re.fullmatch('[0-9a-f]{40}',public['generation_commit'])), 'invalid public commit')
    require(set(public['provenance']) == {'db_sha256_prefix','policies_sha256_prefix','review_sha256_prefix',
                                        'rule_version','policy_checked_at','policy_count','policy_collected_at'},
            'invalid public provenance fields')
    require(all(re.fullmatch('[0-9a-f]{16}',public['provenance'][key]) for key in
                ('db_sha256_prefix','policies_sha256_prefix','review_sha256_prefix')), 'invalid public digest prefix')
    require(public['provenance']['rule_version'] == RULE_VERSION, 'invalid public rule version')
    date.fromisoformat(public['provenance']['policy_checked_at'])
    for value in public['provenance']['policy_collected_at']:
        date.fromisoformat(value)
    require(type(public['provenance']['policy_count']) is int and public['provenance']['policy_count'] > 0,
            'invalid public policy count')
    require([c['case'] for c in public['cases']] == ['A','B','C'], 'invalid public case labels')
    for c in public['cases']:
        fields = {'case','label','gu','biz_type','band','status','rule_stage','candidate_count','top_count','seed'}
        if c['case'] == 'B':
            fields.add('tailored_policy_count_min')
            minimum = c.get('tailored_policy_count_min')
            require((type(minimum) is int and minimum >= 0 and c['status'] == '선택')
                    or (minimum is None and c['status'] == NO_CASE), 'invalid public tailored minimum')
        require(set(c) == fields,
                'invalid public case fields')
        require(c['label'] == '비식별 실제 사례 '+c['case'] and c['seed'] == SEEDS[c['case']], 'invalid public case seed')
        require(c['gu'] in GU | {None} and c['biz_type'] in BIZ | {None}
                and c['band'] in {'low','mid','high',None}, 'invalid public category')
        require(c['rule_stage'] in {'기본','대안1','대안2','없음'} and c['status'] in {'선택',NO_CASE}, 'invalid public stage')
        require(type(c['candidate_count']) is int and c['candidate_count'] >= 0
                and c['top_count'] == min(c['candidate_count'],10), 'invalid public candidate count')


def export_cases_document(selection):
    """The only private case-to-store mapping; directly consumed by #65 load_cases."""
    stages = {'기본': 'base', '대안1': 'alt1', '대안2': 'alt2', '없음': 'none'}
    return {'cases': [dict(case_label=label,
                          store_id=item['candidate'].store_id if item['candidate'] else None,
                          rule_stage=stages[item['rule_stage']],
                          n_candidates=item['candidate_count'], seed=item['seed'])
                      for label, item in selection.items()]}


def write_outputs(private, public, private_path, public_path, *, export_cases=None, export_path=None):
    validate_public_output(public)
    targets = [Path(private_path).resolve(), Path(public_path).resolve()]
    documents = [private, public]
    if export_cases is not None:
        require(export_path is not None, 'export destination required')
        targets.append(Path(export_path).resolve())
        documents.append(export_cases)
    require(len(set(targets)) == len(targets), 'private and public destinations must differ')
    for target in targets:
        paths.check_private_output(target)  # public summary also defaults to ignored local outputs
    # Do not reselect over earlier evidence from a different policy/input snapshot.
    for target, doc in zip(targets, documents):
        if target.exists():
            require(load_json(target) == doc, 'existing selection differs; do not automatically reselect')
    temporary = []
    try:
        for target, doc in zip(targets, documents):
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f'.{target.name}.tmp-{os.getpid()}')
            temp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
            temporary.append(temp)
        for temp, target in zip(temporary, targets):
            os.replace(temp, target)
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--policy-review','--review',dest='policy_review',type=Path,required=True)
    parser.add_argument('--store-review',type=Path)
    parser.add_argument('--submission-root',type=Path,default=ROOT)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--build-store-review',action='store_true')
    mode.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--synthetic-fixture',action='store_true',help='Pinned synthetic fixture only; real records rejected')
    parser.add_argument('--config',type=Path,default=ROOT/'configs/regen_w3.json')
    parser.add_argument('--policy-file',type=Path)
    parser.add_argument('--policy-sha256')
    parser.add_argument('--policy-checked-at')
    parser.add_argument('--private-out',type=Path,default=ROOT/'outputs/demo/demo_selection_private.json')
    parser.add_argument('--export-out',type=Path)
    parser.add_argument('--public-out',type=Path,default=ROOT/'outputs/demo/demo_selection_public.json')
    args=parser.parse_args(argv)
    try:
        config=load_json(args.config)
        require(isinstance(config, dict) and isinstance(config.get('demo',{}), dict), 'invalid freeze configuration')
        frozen=config.get('demo',{}).get('policy',{})
        require(isinstance(frozen, dict), 'invalid policy freeze configuration')
        policy_file=args.policy_file or (ROOT/frozen['path'] if 'path' in frozen else None)
        digest=args.policy_sha256 or frozen.get('sha256')
        checked=args.policy_checked_at or frozen.get('checked_at')
        require(policy_file is not None and digest is not None and checked is not None, 'policy freeze is incomplete')
        expected_policy,expected_review=FINAL_POLICY_SHA256,FINAL_REVIEW_SHA256
        if args.synthetic_fixture:
            from scripts.demo_fixture_freeze import POLICY_SHA256, REVIEW_SHA256
            expected_policy,expected_review=POLICY_SHA256,REVIEW_SHA256
        require(digest == expected_policy and checked == FINAL_CHECKED_AT, 'final 28-policy freeze required')
        require(sha256_file(args.policy_review) == expected_review, 'frozen policy review hash mismatch')
        reports,approved_policies,policies,snapshot=read_policy_snapshot(args.db,policy_file,args.policy_review,digest,checked)
        require(snapshot['policy_count']==28, 'final policy count must be 28')
        if args.synthetic_fixture:
            require(all(re.fullmatch(r'SAMPLE-\d+',r['store_id']) and
                        (r['store'].get('name') or '').startswith('(샘플)') and
                        r['risk']['model']=='sample_synthetic' for r in reports), 'synthetic fixture records required')
        from scripts.demo_review_gate import SubmissionGate
        gate=SubmissionGate(args.submission_root,ROOT)
        if args.build_store_review:
            runtime=gate.review(reports,snapshot['db_sha256'])
            require(sha256_file(args.db)==snapshot['db_sha256'], 'SQLite changed while reviewing')
            target=args.store_review or ROOT/'outputs/demo/runtime_store_review.json'
            require(target.resolve() not in {p.resolve() for p in
                    (args.db,Path(policy_file),args.policy_review,args.config)}, 'output must not replace input')
            paths.check_private_output(target)
            if target.exists():
                require(load_json(target)==runtime, 'existing runtime review differs')
            target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_name(f'.{target.name}.tmp-{os.getpid()}')
            try:
                temp.write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
                os.replace(temp,target)
            finally:
                temp.unlink(missing_ok=True)
            print('automatic private store review written; canonical count: '+str(len(reports)))
            return 0
        require(args.store_review is not None, 'separate runtime store review required')
        runtime_bytes=args.store_review.read_bytes()
        runtime=json.loads(runtime_bytes.decode('utf-8-sig'))
        approved_stores=validate_store_review(runtime,reports,snapshot['db_sha256'])
        require(runtime==gate.review(reports,snapshot['db_sha256']), 'runtime review differs from automatic gate')
        require(sha256_file(args.db)==snapshot['db_sha256'], 'SQLite changed while reviewing')
        provenance=dict(snapshot)
        policy_digest=provenance['review_sha256']; runtime_digest=hashlib.sha256(runtime_bytes).hexdigest()
        provenance.update(policy_review_digest=policy_digest,store_review_digest=runtime_digest)
        provenance['review_sha256']=hashlib.sha256(f'{policy_digest}:{runtime_digest}'.encode()).hexdigest()
        candidates=candidates_from_reports(reports,approved_stores,approved_policies,policies)
        if args.preflight_only:
            print('demo preflight passed; canonical count: '+str(len(reports)))
            return 0
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip()
        require(provenance['policy_count'] == 28, 'final policy count must be 28')
        selection=choose_cases(candidates)
        private,public=output_documents(selection,provenance,commit)
        export_path=args.export_out or args.private_out.with_name('demo_cases_for_export.json')
        require(not ({args.db.resolve(),Path(policy_file).resolve(),args.policy_review.resolve(),args.store_review.resolve(),args.config.resolve()}
                     & {args.private_out.resolve(),args.public_out.resolve(),export_path.resolve()}), 'output must not replace input')
        write_outputs(private,public,args.private_out,args.public_out,
                      export_cases=export_cases_document(selection),export_path=export_path)
    except SelectionError as exc:
        print('selection error: '+str(exc),file=sys.stderr)
        return 2
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,subprocess.SubprocessError,paths.UnsafeOutputPath):
        print('selection error: invalid input, evidence, or output path',file=sys.stderr)
        return 2
    print('demo selection written; selected cases: '+str(sum(c['status']=='선택' for c in public['cases'])))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
