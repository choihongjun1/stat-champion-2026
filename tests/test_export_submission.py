# -*- coding: utf-8 -*-
"""제출용 비식별 사례 번들(submission-static-0.1) 테스트 — 합성 데이터만 쓴다.

합성 점포는 실제 형식의 store_id(GR_/SR_/BT_…)·상호·도로명/지번 주소를 갖도록 만든다(tests.serving_synth).
그래서 내부 static_private 번들은 check_claims ID-*에 걸리고, 제출 번들은 걸리지 않아야 한다.
"""
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.data import config
from src.serving import build_db as bd
from src.serving import export_static as ex
from src.serving import export_submission as es
from src.serving import submission_report as sr
from src.serving.synthetic_samples import POLICIES
from tests.serving_synth import store, write_inputs
from tests.test_dong_summary import STORES

TRICKY_NAME = "리뷰가좋은가상식당"   # CL-13(후기·리뷰) 트리거 — 상호는 제출 번들에서 빠져야 한다
CHECK_CLAIMS = config.REPO_ROOT / "scripts" / "check_claims.py"
FORBIDDEN = ("store_id", "probability_12m", "ci_low", "ci_high", "interval_note", "peer_median", "contribution",
             "values", "driver", "explanation", "address_road", "address_jibun", "dong", "license_date",
             "peer_percentile", "model", "calibrated")


def _build(tmp_path, *, purpose="release", tricky=True, with_policies=True, name="rel.sqlite"):
    stores = list(STORES)
    if tricky:
        stores[0] = store(100, "마포구", "망원동", "일반음식점", "high", name=TRICKY_NAME)
    d = write_inputs(tmp_path / f"in_{name}", stores)
    if tricky:  # 첫 점포의 업력 설명문에 철회 수치 패턴(CL-09: "3.3%")이 들어가게 한다 — 기여 0.033
        lines = (d / "reports.jsonl").read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["factors"][0].update(contribution=0.033,
                                 explanation="업력 요인이 예측 위험도를 약 3.3%p 움직이는 쪽으로 기여했습니다.")
        lines[0] = json.dumps(rec, ensure_ascii=False)
        (d / "reports.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    kw = {}
    if with_policies:
        (d / "policies.json").write_text(json.dumps(POLICIES, ensure_ascii=False), encoding="utf-8")
        kw["policies_path"] = d / "policies.json"
    out = tmp_path / name
    bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out,
             license_snapshot_date="2026-09-11", purpose=purpose, **kw)
    return out, stores


def _ids(db):
    c = sqlite3.connect(db)
    try:
        rows = c.execute("SELECT s.store_id, r.band FROM stores s JOIN risk r USING (store_id) ORDER BY s.rowid")
        return [(sid, band) for sid, band in rows]
    finally:
        c.close()


def _cases(tmp_path, db, *, b_none=False):
    rows = _ids(db)
    high = [s for s, b in rows if b == "high"]
    low = [s for s, b in rows if b == "low"]
    cases = [{"case_label": "A", "store_id": high[0], "rule_stage": "base", "n_candidates": 6, "seed": 20261004},
             {"case_label": "B", "store_id": None if b_none else high[1], "rule_stage": "none" if b_none else "alt1",
              "n_candidates": 0 if b_none else 3, "seed": 20261005},
             {"case_label": "C", "store_id": low[0], "rule_stage": "base", "n_candidates": 9, "seed": 20261006}]
    p = tmp_path / "cases_private.json"
    p.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    return p


def _claims(path) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(CHECK_CLAIMS), str(path), "--fail-on", "warn", "--format", "json"],
                       capture_output=True, encoding="utf-8", cwd=config.REPO_ROOT)
    return r.returncode, json.loads(r.stdout)["summary"]


def _snapshot(d):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(Path(d).rglob("*")) if p.is_file()}


def _keys(v, out):
    if isinstance(v, dict):
        for k, x in v.items():
            out.add(k)
            _keys(x, out)
    elif isinstance(v, list):
        for x in v:
            _keys(x, out)
    return out


@pytest.fixture
def built(tmp_path):
    db, stores = _build(tmp_path)
    cases = _cases(tmp_path, db)
    out = tmp_path / "submission"
    summary = es.export(db, out, cases_path=cases)
    return dict(db=db, stores=stores, cases=cases, out=out, summary=summary, tmp=tmp_path)


# ---------------------------------------------------------------------------
def test_bundle_scope_and_public_ids(built):
    out = built["out"]
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == ["cases/CASE-A.json", "cases/CASE-B.json", "cases/CASE-C.json", "manifest.json", "meta.json"]
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["_submission_contract_version"] == sr.CONTRACT_VERSION == "submission-static-0.1"
    assert [s["public_id"] for s in meta["cases"]] == ["CASE-A", "CASE-B", "CASE-C"]
    assert all(s["selection_status"] == "selected" for s in meta["cases"])
    assert meta["publication_approved"] is False and meta["technical_gate"]["passed"] is True
    assert not {"search_index.json", "dong_summary.json", "dongs.json"} & set(files)  # R5: 제출 범위 밖
    for lab in "ABC":
        case = json.loads((out / "cases" / f"CASE-{lab}.json").read_text(encoding="utf-8"))
        assert case["public_id"] == f"CASE-{lab}" and case["case_title"] == f"비식별 실제 사례 {lab}"
        assert sr.validate_case(case) == []


def test_identifiers_are_absent_from_every_byte(built):
    out, db = built["out"], built["db"]
    blob = b"".join(p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()).decode("utf-8")
    names = "".join(p.relative_to(out).as_posix() for p in out.rglob("*"))
    c = sqlite3.connect(db)
    stores = c.execute("SELECT store_id, name, address_road, address_jibun, dong FROM stores").fetchall()
    c.close()
    for sid, name, road, jibun, dong in stores:
        assert sid not in blob and sid not in names                       # 실제 store_id: 본문·파일명·manifest 경로
    chosen = {c["store_id"] for c in json.loads(built["cases"].read_text(encoding="utf-8"))["cases"]}
    for sid, name, road, jibun, dong in stores:
        if sid in chosen:
            for v in (name, road, jibun, dong):
                assert v is None or v not in blob                         # 상호·도로명·지번·법정동
    assert TRICKY_NAME not in blob


def test_forbidden_keys_and_quantities_are_absent(built):
    out = built["out"]
    for p in out.rglob("*.json"):
        obj = json.loads(p.read_text(encoding="utf-8"))
        assert not _keys(obj, set()) & set(FORBIDDEN), p.name
        assert sr.forbidden_key_paths(obj) == []
        text = p.read_text(encoding="utf-8")
        assert "%p" not in text and not re.search(r"\d+(\.\d+)?\s*%", text)   # 확률 단위 값 없음
        assert not re.search(r"[0-9a-f]{40,}", text)                          # 전체 sha256 미노출
    for lab in "ABC":
        case = json.loads((out / "cases" / f"CASE-{lab}.json").read_text(encoding="utf-8"))
        for f in case["factors"]:
            assert not re.search(r"\d", f["summary_text"])
            assert set(f) == {"factor_id", "name", "category", "actionability", "direction", "level_text",
                              "summary_text", "display", "data_missing", "missing_reason", "hold_reason",
                              "driver_code", "interpretation_sensitive", "sensitivity_label"}


def test_kept_fields_match_internal_record(built):
    out, db = built["out"], built["db"]
    chosen = {c["case_label"]: c["store_id"] for c in json.loads(built["cases"].read_text(encoding="utf-8"))["cases"]}
    conn = sqlite3.connect(db)
    try:
        for lab, sid in chosen.items():
            rec = bd.assemble_report(conn, sid)
            case = json.loads((out / "cases" / f"CASE-{lab}.json").read_text(encoding="utf-8"))
            assert case["risk"] == {k: rec["risk"][k] for k in ("band", "percentile", "peer_group")}
            assert case["store"] == {"gu": rec["store"]["gu"], "biz_type": rec["store"]["biz_type"]}
            assert (case["score_origin"], case["as_of"], case["disclaimer"]) == (
                rec["score_origin"], rec["as_of"], rec["disclaimer"])
            assert [f["factor_id"] for f in case["factors"]] == [f["factor_id"] for f in rec["factors"]]
            for f, g in zip(case["factors"], rec["factors"]):
                assert (f["interpretation_sensitive"], f["sensitivity_label"]) == (
                    g["interpretation_sensitive"], g["sensitivity_label"])
                assert f["display"] == g["display"] and f["direction"] == (g["direction"] if g["display"] else None)
            assert case["policies"] == rec["policies"] and case["policy_matching"] == rec["policy_matching"]
            assert case["selection"]["seed"] in (20261004, 20261005, 20261006)
    finally:
        conn.close()
    assert any(json.loads((out / "cases" / f"CASE-{lab}.json").read_text(encoding="utf-8"))["policies"]
               for lab in "ABC")  # 합성 정책 구조가 실제로 실린다


def test_check_claims_zero_on_submission_but_not_on_static_private(built):
    code, summary = _claims(built["out"])
    assert code == 0 and summary["by_rule"] == {} and summary["scanned_files"] == 5
    # 같은 정본의 내부 전체 번들은 식별정보·오탐 패턴이 그대로다 — 내부용으로만 유지
    private = built["tmp"] / "static_private"
    ex.export(built["db"], private, min_cell_n=5)
    code, summary = _claims(private)
    assert code == 1
    assert {"ID-01", "ID-05", "ID-06", "CL-09", "CL-13"} <= set(summary["by_rule"])


def test_static_private_sqlite_and_serve_inputs_are_untouched(tmp_path):
    db, _ = _build(tmp_path)
    private = tmp_path / "static_private"
    ex.export(db, private, min_cell_n=5)
    serve_dir = tmp_path / "in_rel.sqlite"
    before = (_snapshot(private), hashlib.sha256(db.read_bytes()).hexdigest(), _snapshot(serve_dir))
    es.export(db, tmp_path / "submission", cases_path=_cases(tmp_path, db))
    es.export(db, tmp_path / "x", dry_run=True)
    assert (_snapshot(private), hashlib.sha256(db.read_bytes()).hexdigest(), _snapshot(serve_dir)) == before
    # 내부 번들 스키마는 그대로(public-static-0.1, 전체 점포)
    meta = json.loads((private / "meta.json").read_text(encoding="utf-8"))
    assert meta["public_contract_version"] == "public-static-0.1" and len(list((private / "reports").iterdir())) == len(STORES)


def test_pending_and_no_suitable_case(tmp_path):
    db, _ = _build(tmp_path)
    s = es.export(db, tmp_path / "pending")
    assert s["cases"] == {"A": "pending_selection", "B": "pending_selection", "C": "pending_selection"}
    assert sorted(p.name for p in (tmp_path / "pending").rglob("*") if p.is_file()) == ["manifest.json", "meta.json"]
    s = es.export(db, tmp_path / "b_none", cases_path=_cases(tmp_path, db, b_none=True))
    assert s["cases"]["B"] == "no_suitable_case" and not (tmp_path / "b_none" / "cases" / "CASE-B.json").exists()
    assert _claims(tmp_path / "b_none")[0] == 0


def test_dry_run_writes_nothing(tmp_path):
    db, _ = _build(tmp_path)
    out = tmp_path / "never"
    s = es.export(db, out, cases_path=_cases(tmp_path, db), dry_run=True)
    assert s["dry_run"] is True and s["claims_findings"] == 0 and not out.exists()


def test_refuses_dev_db_unknown_store_and_bad_cases(tmp_path):
    dev, _ = _build(tmp_path, purpose="dev", name="dev.sqlite")
    with pytest.raises(es.SubmissionError, match="기술적 계약 미통과"):
        es.export(dev, tmp_path / "d")
    db, _ = _build(tmp_path)
    bad = tmp_path / "bad.json"
    for cases, msg in (
            ([{"case_label": "A", "store_id": "GR_9999999-000-2020-99999", "rule_stage": "base"}], "정본에 없는"),
            ([{"case_label": "A", "store_id": "x", "rule_stage": "base"},
              {"case_label": "A", "store_id": "y", "rule_stage": "base"}], "한 번씩"),
            ([{"case_label": "D", "store_id": "x", "rule_stage": "base"}], "A·B·C"),
            ([{"case_label": "A", "store_id": None, "rule_stage": "base"}], "rule_stage=none"),
            ([{"case_label": "A", "store_id": "x", "rule_stage": "best"}], "rule_stage")):
        bad.write_text(json.dumps({"cases": cases}), encoding="utf-8")
        with pytest.raises(es.SubmissionError, match=msg):
            es.export(db, tmp_path / "e", cases_path=bad)
    sid = _ids(db)[0][0]
    bad.write_text(json.dumps({"cases": [{"case_label": "A", "store_id": sid, "rule_stage": "base"},
                                         {"case_label": "B", "store_id": sid, "rule_stage": "base"}]}),
                   encoding="utf-8")
    with pytest.raises(es.SubmissionError, match="서로 다른 점포"):
        es.export(db, tmp_path / "e", cases_path=bad)


def test_failed_gate_keeps_previous_bundle(built, monkeypatch):
    before = _snapshot(built["out"])
    monkeypatch.setattr(es, "claims_findings", lambda b: {"findings": [{"rule_id": "ID-01"}],
                                                          "summary": {"scanned_files": 5, "by_rule": {"ID-01": 1}}})
    with pytest.raises(es.SubmissionError, match="check_claims"):
        es.export(built["db"], built["out"], cases_path=built["cases"])
    assert _snapshot(built["out"]) == before


def test_tracked_paths_are_refused(tmp_path):
    db, _ = _build(tmp_path)
    with pytest.raises(es.SubmissionError, match="outputs/"):
        es.export(db, config.REPO_ROOT / "docs" / "submission_should_not_exist")
    assert not (config.REPO_ROOT / "docs" / "submission_should_not_exist").exists()
