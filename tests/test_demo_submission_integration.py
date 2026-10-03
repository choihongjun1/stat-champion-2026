"""Direct #64 producer -> #65 consumer; no intermediate transformations."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from src.data import config
from src.serving import export_submission as es
from tests.test_export_submission import _build
from tests.test_policy_apply_submission import application_fixture


def test_selector_fixture_direct_input():
    cases=es.load_cases(config.REPO_ROOT/'tests/fixtures/demo_cases_for_export.synthetic.json')
    assert [c['case_label'] for c in cases]==['A','B','C']
    assert all(c['status']=='selected' for c in cases)


def test_live_selector_output_direct_load_and_export(tmp_path,monkeypatch):
    selector=Path(os.environ.get('DEMO_SELECTOR_PATH',
        config.REPO_ROOT/'scripts/select_demo_stores.py'))
    if not selector.is_file():
        pytest.skip('Set DEMO_SELECTOR_PATH to the #64 checkout to run the cross-PR integration')
    spec=importlib.util.spec_from_file_location('demo_selector_integration',selector)
    mod=importlib.util.module_from_spec(spec)
    import sys
    monkeypatch.setattr(sys,"path",list(sys.path))
    monkeypatch.setitem(sys.modules,spec.name,mod)
    spec.loader.exec_module(mod)
    db,_=_build(tmp_path)
    with es.sqlite3.connect(db) as conn:
        reports=list(es.bd.iter_reports(conn))
    from src.serving.synthetic_samples import POLICIES
    candidates=mod.candidates_from_reports(reports,{r['store_id'] for r in reports},
                                           {p['id'] for p in POLICIES},POLICIES)
    selection=mod.choose_cases(candidates)
    document=mod.export_cases_document(selection)
    path=tmp_path/'demo_cases_for_export.json'
    provenance=dict(rule_version=mod.RULE_VERSION,db_sha256='a'*64,policies_sha256='b'*64,
                    review_sha256='c'*64,policy_checked_at='2026-10-03',policy_count=28,
                    policy_collected_at=['2026-10-03'],policy_path='synthetic')
    private,public=mod.output_documents(selection,provenance,'d'*40)
    mod.write_outputs(private,public,tmp_path/'private.json',tmp_path/'public.json',
                      export_cases=document,export_path=path)
    loaded=es.load_cases(path)
    assert [r['store_id'] for r in loaded]==[r['store_id'] for r in document['cases']]
    assert all(c['status']=='selected' for c in loaded)
    apply,_=application_fixture(tmp_path,db)
    out=tmp_path/'submission'
    result=es.export(db,out,cases_path=path,policies_apply_path=apply)
    assert result['claims_findings']==0 and es.verify_bundle(out)==[]
    secrets=[r['store_id'] for r in reports]
    assert es.verify_bundle(out,secrets)==[]
