"""Uniform, private canonical review using #65 projection and #56 compiled rules."""
from collections import deque
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from scripts import check_claims

REVIEW_VERSION = 'demo-store-review-0.1'
GATE_VERSION = 'submission-auto-gate-0.1'


class IdentifierMatcher:
    """Aho-Corasick: one linear byte-text scan, rather than N identifier searches per row."""
    def __init__(self, identifiers):
        self.edges=[{}]; self.fail=[0]; self.hit=[False]
        for value in sorted(identifiers):
            node=0
            for char in value:
                if char not in self.edges[node]:
                    self.edges[node][char]=len(self.edges)
                    self.edges.append({}); self.fail.append(0); self.hit.append(False)
                node=self.edges[node][char]
            self.hit[node]=True
        queue=deque(self.edges[0].values())
        while queue:
            node=queue.popleft()
            for char,child in self.edges[node].items():
                queue.append(child); fallback=self.fail[node]
                while fallback and char not in self.edges[fallback]:
                    fallback=self.fail[fallback]
                self.fail[child]=self.edges[fallback].get(char,0)
                self.hit[child] |= self.hit[self.fail[child]]

    def contains(self,text):
        node=0
        for char in text:
            while node and char not in self.edges[node]:
                node=self.fail[node]
            node=self.edges[node].get(char,0)
            if self.hit[node]: return True
        return False


def claims_passed(text,rules,allowlist):
    """In-memory JSON detection equivalent to check_claims.scan; emits no findings/values."""
    text=re.sub(r'\\u([0-9a-fA-F]{4})',
                lambda m:chr(int(m.group(1),16)).replace('\n',' ').replace('\r',' '),text)
    for rule in rules:
        if rule.get('scope')=='public_report' and not re.search(r'"_public_contract_version"\s*:',text):
            continue
        for pattern in rule['compiled']:
            for match in pattern.finditer(text):
                start=text.rfind('\n',0,match.start())+1
                end=text.find('\n',match.end())
                if end<0: end=len(text)
                lines=text[start:end].splitlines() or ['']
                if not all(check_claims.allowed('cases/CASE-A.json',rule['id'],line,allowlist) for line in lines):
                    return False
    return True


class SubmissionGate:
    def __init__(self,submission_root,repo_root):
        root=Path(submission_root)
        source=root/'src/serving/submission_report.py'
        schema=root/'src/serving/submission_schema.json'
        spec=importlib.util.spec_from_file_location('_demo_submission_projection',source)
        self.projection=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.projection)
        if self.projection.CONTRACT_VERSION!='submission-static-0.1':
            raise ValueError('unsupported submission projection contract')
        self.rules,self.allowlist=check_claims.load_config(
            Path(repo_root)/'configs/claims_rules.json',Path(repo_root)/'configs/claims_allowlist.json')
        inputs=[source,schema,Path(check_claims.__file__),Path(__file__),
                Path(repo_root)/'configs/claims_rules.json',Path(repo_root)/'configs/claims_allowlist.json']
        self.fingerprint=hashlib.sha256(b''.join(hashlib.sha256(p.read_bytes()).digest() for p in inputs)).hexdigest()

    def review(self,reports,db_hash):
        records=list(reports)
        ids=[r['store_id'] for r in records]
        if len(ids)!=len(set(ids)): raise ValueError('duplicate canonical record')
        matcher=IdentifierMatcher(ids)
        rows=[]
        for record in sorted(records,key=lambda r:r['store_id']):
            publication,claims=False,False
            try:
                case=self.projection.project_case(record,label='A',data_kind='real',
                    rule_stage='base',n_candidates=1,seed=20261004)
            except ValueError:
                pass  # Schema-invalid projections are unsuitable; never print validation data.
            else:
                text=json.dumps(case,ensure_ascii=False,indent=2)+'\n'
                st=record['store']; allowed=(st['gu'],st['biz_type'],record['risk']['peer_group'])
                local=[v for v in (st.get('name'),st.get('address_road'),st.get('address_jibun'),st.get('dong'))
                       if isinstance(v,str) and len(v)>=2 and not any(v in a for a in allowed)]
                publication=(not self.projection.forbidden_key_paths(case)
                             and not matcher.contains(text) and not any(v in text for v in local))
                claims=claims_passed(text,self.rules,self.allowlist)
            rows.append(dict(store_id=record['store_id'],publication_guard_passed=publication,claims_passed=claims))
        return dict(contract_version=REVIEW_VERSION,gate_version=GATE_VERSION,gate_sha256=self.fingerprint,
                    db_sha256=db_hash,score_origin='2026Q2',as_of='2026-06-30',
                    canonical_count=len(records),review_count=len(rows),stores=rows)
