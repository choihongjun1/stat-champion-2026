"""All fixture text is synthetic; no external data is opened."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('claims_checker', ROOT/'scripts/check_claims.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

@pytest.fixture(autouse=True)
def isolated_scan_root(tmp_path, monkeypatch):
    # Each synthetic filesystem models a checkout; never inspect the real data tree.
    monkeypatch.setattr(checker, 'repository_root', lambda: tmp_path)


CASES = [
('CL-01','효과 확인','효과 미확인'),
('CL-02','인과 효과','조건부 연관성'),
('CL-03','폐업 확률','상대 위험 수준'),
('CL-04','38.5%로 예측','38.5점 예측 점수'),
('CL-05','29~47%','29~47점'),
('CL-06','폐업률 2배','폐업이 많이 관측된 집단'),
('CL-07','대기 시간','운영 시간'),
('CL-08','본인만 볼 수','공개 자료'),
('CL-09','3.3%','13.3% 8.81 112.60%'),
('CL-10','LightGBM','HGB lightgbm'),
('CL-11','창업기업실태조사','소상공인실태조사'),
('CL-12','신청 가능','상태 확인 필요'),
('CL-13','후기','블로그 언급 수'),
('CL-14','받을 수 있는 지원','자격 조건이 맞는 사업'),
('CL-15','원인은','모형 기여 신호'),
('CL-16','문을 닫는 경우가 더 많','상대 위험 수준'),
('ID-01','SR_0000000','SYN-000001'),
('ID-02','000-00-00000','000-000-0000'),
('ID-03','000-0000-0000','999-0000-0000'),
('ID-04','a'*32,'a'*40),
]


def config():
    return checker.load_config(ROOT/'configs/claims_rules.json', ROOT/'configs/claims_allowlist.json')


def findings(tmp_path, text, relative='sample.md', allowlist=None):
    path = tmp_path/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding='utf-8')
    rules, defaults = config()
    return checker.scan([path],rules,defaults if allowlist is None else allowlist,base=tmp_path)


@pytest.mark.parametrize('rid,positive,negative',CASES)
def test_each_rule_positive_and_negative(tmp_path,rid,positive,negative):
    assert rid in {f['rule_id'] for f in findings(tmp_path,positive)['findings']}
    assert rid not in {f['rule_id'] for f in findings(tmp_path,negative)['findings']}


@pytest.mark.parametrize('text,rid',[
('효과 확인되지 않았다',None),('AI가 폐업을 예측','CL-06'),
('8.8','CL-09'),('12.6%','CL-09'),('리뷰','CL-13'),('때문에 위험','CL-15'),
('29∼47%','CL-05'),('29–47%','CL-05'),('38%으로 예측','CL-04')])
def test_alternative_patterns(tmp_path,text,rid):
    found = findings(tmp_path,text)['findings']
    assert (rid in {f['rule_id'] for f in found}) if rid else not found


def test_allowlists_and_inline_scope(tmp_path):
    assert not findings(tmp_path,'1~99% 분위 winsorize','docs/DECISIONS_SYN.md')['findings']
    assert not findings(tmp_path,'폐업 확률','configs/claims_rules.json')['findings']
    found = findings(tmp_path,'29~47% 인과 효과 # claims-allow: CL-05')['findings']
    assert {f['rule_id'] for f in found} == {'CL-02'}
    assert findings(tmp_path,'29~47% # claims-allow: CL-050')['findings']
    assert findings(tmp_path,'29~47% # claims-allow: CL-05\n29~47%')['findings'][0]['line'] == 2


def test_docs_identifiers_not_exempt(tmp_path):
    assert findings(tmp_path,'SR_0000000 # claims-allow: ID-01','docs/CLAIMS.md')['findings']


@pytest.mark.parametrize('output_format',['text','json'])
def test_redaction_everywhere(tmp_path,capsys,output_format):
    secret = 'SR_0000000'
    phone = '000-0000-0000'
    business = '000-00-00000'
    path = tmp_path/(secret+'.md')
    path.write_text(secret+' '+phone+' '+business+' 효과 확인 '+'a'*32,encoding='utf-8')
    assert checker.main([str(path),'--format',output_format]) == 1
    output = capsys.readouterr().out
    for value in [secret,phone,business,'a'*32]:
        assert value not in output
    assert 'SR_*******' in output


def test_binary_bom_extension_decode_and_exclusion(tmp_path):
    (tmp_path/'bom.md').write_bytes(b'\xef\xbb\xbf'+ '폐업 확률'.encode())
    (tmp_path/'binary.md').write_bytes(b'\0'+ '폐업 확률'.encode())
    (tmp_path/'other.png').write_bytes(b'x')
    (tmp_path/'broken.md').write_bytes(b'\xff')
    (tmp_path/'data').mkdir()
    (tmp_path/'data/hidden.md').write_bytes(b'never open')
    rules,allowlist = config()
    result = checker.scan([tmp_path],rules,allowlist,base=tmp_path)
    assert result['summary']['skipped'] == dict(binary=1,decode=1,excluded_or_linked=1,extension=1)
    assert result['summary']['scanned_files'] == 1
    assert {f['rule_id'] for f in result['findings']} == {'CL-03','IO-DECODE'}


def test_data_rejected_before_any_read(tmp_path,monkeypatch):
    (tmp_path/'data').mkdir()
    path = tmp_path/'data/hidden.md'
    path.write_text('synthetic',encoding='utf-8')
    good = tmp_path/'good.md'
    good.write_text('safe',encoding='utf-8')
    monkeypatch.setattr(Path,'read_bytes',lambda _: pytest.fail('target opened'))
    assert checker.main([str(good),str(path)]) == 2
    assert checker.main([str(tmp_path/'data')]) == 2


def test_exit_status_and_json(tmp_path,capsys):
    path = tmp_path/'sample.md'
    for text,flags,status in [('안전',[],0),('폐업 확률',[],1),('신청 가능',[],0),
                              ('신청 가능',['--fail-on','warn'],1)]:
        path.write_text(text,encoding='utf-8')
        assert checker.main([str(path),'--format','json',*flags]) == status
        assert isinstance(json.loads(capsys.readouterr().out)['findings'],list)
    assert checker.main([str(tmp_path/'missing.md')]) == 2


@pytest.mark.parametrize('kind',['regex','severity','mask','ids','reason','allowlist'])
def test_bad_config_is_usage_error(tmp_path,kind):
    rules = json.loads((ROOT/'configs/claims_rules.json').read_text(encoding='utf-8'))
    allowlist = []
    if kind == 'regex': rules[0]['patterns'] = ['[']
    if kind == 'severity': rules[0]['severity'] = 'fatal'
    if kind == 'mask': rules[-1]['mask'] = False
    if kind == 'ids': rules[1]['id'] = rules[0]['id']
    if kind == 'reason': del rules[0]['reason']
    if kind == 'allowlist': allowlist = [dict(path_glob='sample.md',rule_ids=['UNKNOWN'],reason='synthetic')]
    rule_path = tmp_path/'rules.json'
    allow_path = tmp_path/'allow.json'
    rule_path.write_text(json.dumps(rules),encoding='utf-8')
    allow_path.write_text(json.dumps(allowlist),encoding='utf-8')
    path = tmp_path/'sample.md'
    path.write_text('safe',encoding='utf-8')
    assert checker.main([str(path),'--rules',str(rule_path),'--allowlist',str(allow_path)]) == 2


def test_locations_deduplication_and_default_cli(tmp_path):
    path = tmp_path/'sample.md'
    path.write_text('안전\n앞 폐업 확률',encoding='utf-8')
    rules,allowlist = config()
    result = checker.scan([tmp_path,path],rules,allowlist,base=tmp_path)
    assert len(result['findings']) == 1
    assert (result['findings'][0]['line'],result['findings'][0]['column']) == (2,3)
    process = subprocess.run([sys.executable,str(ROOT/'scripts/check_claims.py'),str(path)],
                             cwd=tmp_path,capture_output=True,encoding='utf-8',
                             env={**os.environ,'PYTHONUTF8':'1'})
    assert process.returncode == 1


def test_linked_directory_is_skipped(tmp_path,monkeypatch):
    target = tmp_path/'private'
    target.mkdir()
    (target/'sample.md').write_text('폐업 확률',encoding='utf-8')
    link = tmp_path/'link'
    link.mkdir()
    monkeypatch.setattr(checker,'linked',lambda path: path == link)
    rules,allowlist = config()
    result = checker.scan([link],rules,allowlist,base=tmp_path)
    assert not result['findings']
    assert result['summary']['skipped']['excluded_or_linked'] == 1


def test_recursion_prunes_before_opening_and_masks_boundary(tmp_path,monkeypatch):
    (tmp_path/'data').mkdir()
    (tmp_path/'data/private.md').write_text('synthetic',encoding='utf-8')
    secret = 'SR_' + '0'*300
    (tmp_path/'public.md').write_text(secret+' 효과 확인',encoding='utf-8')
    original = Path.read_bytes
    def guarded(path):
        assert 'data' not in path.parts
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',guarded)
    rules,allowlist = config()
    result = checker.scan([tmp_path],rules,allowlist,base=tmp_path)
    assert secret not in json.dumps(result)
    claim = next(f for f in result['findings'] if f['rule_id'] == 'CL-01')
    assert '0' not in claim['context']


def test_rules_are_read_from_custom_data(tmp_path):
    rule_path = tmp_path/'rules.json'
    rule_path.write_text(json.dumps([dict(id='CL-01',severity='warn',patterns=['합성표현'],
                                         reason='synthetic',mask=False)]),encoding='utf-8')
    allow_path = tmp_path/'allow.json'
    allow_path.write_text('[]',encoding='utf-8')
    path = tmp_path/'public.md'
    path.write_text('합성표현',encoding='utf-8')
    assert checker.main([str(path),'--rules',str(rule_path),'--allowlist',str(allow_path),'--fail-on','warn']) == 1


def test_only_root_data_is_pruned_before_open(tmp_path, monkeypatch):
    source = tmp_path / 'src/data/x.py'
    private = tmp_path / 'data/x.csv'
    source.parent.mkdir(parents=True)
    private.parent.mkdir()
    source.write_text('폐업 확률', encoding='utf-8')
    private.write_text('synthetic private fixture', encoding='utf-8')
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(tmp_path / 'data'), 'root data was opened'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    rules, allowlist = config()
    result = checker.scan([tmp_path], rules, allowlist, base=tmp_path)
    assert result['summary']['scanned_files'] == 1
    assert result['findings'][0]['file'] == 'src/data/x.py'
    assert result['findings'][0]['rule_id'] == 'CL-03'
    assert checker.main([str(private)]) == 2


def test_src_data_can_be_direct_target(tmp_path, capsys):
    source = tmp_path / 'src/data/x.py'
    source.parent.mkdir(parents=True)
    source.write_text('폐업 확률', encoding='utf-8')
    assert checker.main([str(source.parent), '--format', 'json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['summary']['scanned_files'] == 1
    assert result['findings'][0]['file'] == 'src/data/x.py'
    source.write_text('상대 위험 수준', encoding='utf-8')
    assert checker.main([str(source.parent)]) == 0


def test_root_discovery_uses_git_or_cwd(tmp_path, monkeypatch):
    # Reload to exercise the real root resolver rather than the autouse stub.
    local_spec = importlib.util.spec_from_file_location('root_checker', ROOT / 'scripts/check_claims.py')
    local = importlib.util.module_from_spec(local_spec)
    local_spec.loader.exec_module(local)
    monkeypatch.chdir(tmp_path)
    calls = []
    def success(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, str(tmp_path) + '\n')
    monkeypatch.setattr(local.subprocess, 'run', success)
    assert local.repository_root() == tmp_path.resolve()
    assert calls == [['git', 'rev-parse', '--show-toplevel']]
    def failure(args, **kwargs):
        raise subprocess.CalledProcessError(128, args)
    monkeypatch.setattr(local.subprocess, 'run', failure)
    assert local.repository_root() == tmp_path.resolve()


def test_resolved_alias_into_root_data_is_rejected(tmp_path, monkeypatch):
    alias = tmp_path / 'alias.csv'
    alias.write_text('synthetic', encoding='utf-8')
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == alias:
            return tmp_path / 'data/x.csv'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    monkeypatch.setattr(Path, 'read_bytes', lambda _: pytest.fail('target opened'))
    assert checker.main([str(alias)]) == 2


@pytest.mark.parametrize('folder', ['outputs', 'node_modules', '.next', '.venv', '__pycache__', '.git'])
def test_explicit_file_in_excluded_folder_is_scanned(tmp_path, capsys, folder):
    target = tmp_path / folder / 'x.md'
    target.parent.mkdir()
    target.write_text('폐업 확률', encoding='utf-8')
    assert checker.main([str(target), '--format', 'json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['summary']['scanned_files'] == 1
    assert result['findings'][0]['rule_id'] == 'CL-03'


def test_explicit_outputs_directory_prunes_nested_exclusions(tmp_path, capsys):
    output = tmp_path / 'outputs'
    nested = output / 'node_modules'
    nested.mkdir(parents=True)
    (output / 'x.md').write_text('폐업 확률', encoding='utf-8')
    (nested / 'hidden.md').write_text('폐업 확률', encoding='utf-8')
    assert checker.main([str(output), '--format', 'json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['summary']['scanned_files'] == 1
    assert result['summary']['skipped']['excluded_or_linked'] == 1
    assert len(result['findings']) == 1


def test_recursive_outputs_still_pruned(tmp_path, capsys):
    output = tmp_path / 'outputs'
    output.mkdir()
    (output / 'x.md').write_text('폐업 확률', encoding='utf-8')
    (tmp_path / 'safe.md').write_text('상대 위험 수준', encoding='utf-8')
    assert checker.main([str(tmp_path), '--format', 'json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['summary']['scanned_files'] == 1
    assert not result['findings']


@pytest.mark.parametrize('output_format', ['text', 'json'])
@pytest.mark.parametrize('kind', ['empty', 'binary', 'extension', 'decode', 'recursive_outputs'])
def test_zero_scanned_files_exit_3(tmp_path, capsys, kind, output_format):
    if kind == 'binary': (tmp_path / 'x.md').write_bytes(b'\0synthetic')
    elif kind == 'extension': (tmp_path / 'x.png').write_bytes(b'synthetic')
    elif kind == 'decode': (tmp_path / 'x.md').write_bytes(b'\xff')
    elif kind == 'recursive_outputs':
        (tmp_path / 'outputs').mkdir()
        (tmp_path / 'outputs/x.md').write_text('synthetic', encoding='utf-8')
    assert checker.main([str(tmp_path), '--format', output_format]) == 3
    output = capsys.readouterr().out
    assert '검사한 파일이 없습니다' in output
    if output_format == 'json':
        assert json.loads(output)['summary']['scanned_files'] == 0

@pytest.mark.parametrize('text,rid',[
    ('상호: 검증용가상식당', 'ID-05'),
    ('{"store_id":"case-17","name":"검증용가상식당"}', 'ID-05'),
    ('{"name":"검증용가상식당","store_id":"case-17"}', 'ID-05'),
    ('서울특별시 마포구 검증로 123-4', 'ID-06'),
    ('마포구 검증동 123', 'ID-06'),
    ('{"address":"검증용 상세주소"}', 'ID-06'),
    ('store_id: LIC-123456', 'ID-01'),
    ('sr_123456789', 'ID-01'),
    ('사업자등록번호: 1234567890', 'ID-02'),
    ('01012345678', 'ID-03'),
    ('02 1234 5678', 'ID-03'),
    ('+82-10-1234-5678', 'ID-03'),
    ('38%', 'CL-04'),
    ('{"probability_12m":0.38,"ci_low":0.29,"ci_high":0.47}', 'CL-04'),
    ('오탐-폐업 상관', 'CL-09'),
    ('정책: 온라인 관련 지원 정보', 'CL-17'),
    ('개별 점포 효과', 'CL-18'),
    ('위험 3%p 감소', 'CL-18'),
])
def test_w3_publication_regressions(tmp_path, text, rid):
    assert rid in {f['rule_id'] for f in findings(tmp_path, text)['findings']}


@pytest.mark.parametrize('text',[
    '향후 12개월 상대 위험 수준. 블로그 언급 수. 자격 조건이 맞는 지원사업.',
    '서울특별시 마포구 / 2026-10-02 / 1234567890 / 112.6% / 18.8',
    '{"store_id":"SAMPLE-001","name":"(샘플) 가상식당","address":"(샘플) 가상주소"}',
    '{"store_id":"SYN-001","name":"비식별 실제 사례 A","address":null}',
])
def test_safe_public_text_and_synthetic_values(tmp_path, text):
    assert not findings(tmp_path, text)['findings']


def test_sample_id_does_not_exempt_other_identifiers(tmp_path):
    result = findings(tmp_path, '{"store_id":"SAMPLE-001","name":"검증용가상식당",'
                               '"address":"마포구 검증로 123","phone":"010-1234-5678"}')
    assert {'ID-03','ID-05','ID-06'} <= {f['rule_id'] for f in result['findings']}


@pytest.mark.parametrize('relative,text,rid',[
    ('app/out/index.html','안전\n효과 <b>확인</b>', 'CL-01'),
    ('app/out/index.htm','폐업&nbsp;확률', 'CL-03'),
    ('bundle/report.json',json.dumps({'label':'폐업 확률'}), 'CL-03'),
    ('bundle/manifest.json','{"store_id":"SR_123456789"}', 'ID-01'),
    ('submission/report.md','상호: 검증용가상식당', 'ID-05'),
    ('docs/public.md','마포구 검증로 123', 'ID-06'),
])
def test_public_formats_and_encodings(tmp_path, relative, text, rid):
    result = findings(tmp_path, text, relative)
    matches = [f for f in result['findings'] if f['rule_id'] == rid]
    assert matches
    assert all(f['line'] <= text.count('\n') + 1 for f in matches)


@pytest.mark.parametrize('text',[
    '상호: 검증용가상식당 효과 확인',
    '마포구 검증로 123 효과 확인',
    '{"store_id":"case-17","name":"검증용가상식당"}',
])
def test_new_identifiers_are_redacted(tmp_path, text):
    output = json.dumps(findings(tmp_path, text), ensure_ascii=False)
    for secret in ['검증용가상식당','검증로','case-17']:
        assert secret not in output

@pytest.mark.parametrize('text,rid',[
    ('{"address_road":"검증용 상세주소"}', 'ID-06'),
    ('{"address_jibun":"검증용 상세주소"}', 'ID-06'),
    ('관리번호: 2026-000123', 'ID-07'),
    ('{"row_key":"abcd1234"}', 'ID-07'),
    ('PNU: 1234567890123456789', 'ID-07'),
])
def test_additional_export_identifier_fields(tmp_path, text, rid):
    assert rid in {f['rule_id'] for f in findings(tmp_path, text)['findings']}


def test_multitoken_business_name_is_fully_masked(tmp_path):
    result = findings(tmp_path, '상호: 검증용 가상 식당')
    assert '식당' not in json.dumps(result, ensure_ascii=False)


def test_report_schema_nested_store_name(tmp_path):
    result = findings(tmp_path, '{"store_id":"SAMPLE-001","store":{"biz_type":"일반음식점",'
                               '"name":"검증용가상식당"}}', 'report.json')
    assert 'ID-05' in {f['rule_id'] for f in result['findings']}
    assert '검증용가상식당' not in json.dumps(result, ensure_ascii=False)
