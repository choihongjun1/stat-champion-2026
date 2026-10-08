"""저장소의 지원사업 원천(data/policies/20261003)이 코드에 고정된 hash·확인일·스키마와 맞는지 확인한다."""
import hashlib

from src.data import config
from src.serving import build_db as bd, policy_apply as pa

POLICY_DIR = config.REPO_ROOT/'data'/'policies'/'20261003'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_policy_files_match_frozen_hashes():
    assert sha256(POLICY_DIR/'policies.json') == pa.FINAL_POLICY_SHA256
    assert sha256(POLICY_DIR/'policies_apply.csv') == pa.FINAL_APPLY_SHA256


def test_committed_policy_files_pass_loaders():
    policies = bd.load_policy_source(POLICY_DIR/'policies.json')
    ids = {p['id'] for p in policies}
    assert len(policies) == len(ids) == 28
    rows, _, checked_at = pa.load_policy_apply(POLICY_DIR/'policies_apply.csv', ids,
                                               expected_sha256=pa.FINAL_APPLY_SHA256,
                                               checked_at=pa.FINAL_CHECKED_AT)
    assert set(rows) == ids and checked_at == pa.FINAL_CHECKED_AT
