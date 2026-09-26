# -*- coding: utf-8 -*-
"""W2-5 정적 번들 내보내기·합성 샘플 테스트."""
import json
import shutil
from pathlib import Path

import pytest

from src.data import config
from src.serving import export_static as ex
from src.serving import report_validation as rv
from src.serving import search_index as si
from src.serving import synthetic_samples as ss
from tests.serving_synth import make_db, store
from tests.test_dong_summary import STORES

SAMPLES_DIR = config.REPO_ROOT / "docs" / "samples" / "w2-5"


@pytest.fixture
def rel_db(tmp_path):
    return make_db(tmp_path, STORES, purpose="release", name="rel.sqlite")


@pytest.fixture
def bundle(tmp_path, rel_db):
    out = tmp_path / "static"
    ex.export(rel_db, out, min_cell_n=5)
    return out


def _run_of(bundle_dir):
    m = json.loads((Path(bundle_dir) / "meta.json").read_text(encoding="utf-8"))
    return {k: m[k] for k in ("run_id", "score_origin", "as_of", "n_stores")}


def _snapshot(d):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(Path(d).rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------
def test_normal_export(bundle):
    run = _run_of(bundle)
    assert ex.verify_bundle(bundle, run) == []
    meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert meta["technical_gate"] == {"passed": True, "blockers": []}
    assert meta["publication_approved"] is False and "공개 승인 아님" in meta["publication_note"]
    assert meta["data_kind"] == "real"
    assert meta["dong_summary_public_ready"] is False  # 하한값 provisional
    assert len(list((bundle / "reports").glob("*.json"))) == len(STORES) == meta["n_stores"]
    idx = json.loads((bundle / "search_index.json").read_text(encoding="utf-8"))
    assert {e["store_id"] for e in idx["entries"]} == {p.stem for p in (bundle / "reports").glob("*.json")}
    assert "band" not in json.dumps(idx["entries"], ensure_ascii=False)
    paths = [f["path"] for f in manifest["files"]]
    assert paths == sorted(paths) and "manifest.json" not in paths
    assert all(f["run_id"] == meta["run_id"] and f["schema_version"] == "0.2" for f in manifest["files"])
    # 파일명·경로에 상호·주소가 없다 (store_id만)
    assert all(not any(ch in p for ch in "가상점포 ") for p in paths)


def test_not_ready_or_dev_db_is_refused(tmp_path):
    not_ready = make_db(tmp_path, STORES, version="0.1", name="a.sqlite")
    with pytest.raises(ex.ExportError, match="기술적 계약 미통과"):
        ex.export(not_ready, tmp_path / "s1", min_cell_n=5)
    dev = make_db(tmp_path, STORES, name="b.sqlite")  # serve 0.2 입력이 통과했지만 개발용 빌드
    with pytest.raises(ex.ExportError, match="개발용 빌드"):
        ex.export(dev, tmp_path / "s2", min_cell_n=5)
    assert not (tmp_path / "s1").exists() and not (tmp_path / "s2").exists()


def test_dong_summary_public_blockers(tmp_path, rel_db):
    m = ex.export(rel_db, tmp_path / "s", min_cell_n=5, min_cell_n_status="decided")
    # 하한값을 정해도 한 등급 100%인 공개 칸(예: 합정동 일반음식점 전부 mid)이 있으면 공개용이 아니다
    assert m["dong_summary_public_ready"] is False
    assert any("등급 쏠림" in b for b in m["dong_summary_blockers"])
    assert not any("미확정" in b for b in m["dong_summary_blockers"])


def test_missing_and_duplicate_reports_are_detected(tmp_path, bundle):
    run = _run_of(bundle)
    b1 = tmp_path / "b1"
    shutil.copytree(bundle, b1)
    next((b1 / "reports").glob("*.json")).unlink()
    errs = " ".join(ex.verify_bundle(b1, run))
    assert "store_id 불일치" in errs and "manifest 목록과 디스크" in errs
    b2 = tmp_path / "b2"
    shutil.copytree(bundle, b2)
    src = next((b2 / "reports").glob("*.json"))
    shutil.copy(src, b2 / "reports" / "GR_9999999-000-2020-99999.json")  # 같은 점포가 다른 파일명으로 한 번 더
    assert any("파일명" in e for e in ex.verify_bundle(b2, run))


def test_index_report_mismatch_is_detected(tmp_path, bundle):
    b = tmp_path / "b"
    shutil.copytree(bundle, b)
    p = b / "search_index.json"
    idx = json.loads(p.read_text(encoding="utf-8"))
    idx["entries"] = idx["entries"][1:]
    idx["n_entries"] -= 1
    p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    errs = " ".join(ex.verify_bundle(b, _run_of(bundle)))
    assert "검색 인덱스와 상세 리포트 store_id 불일치" in errs and "sha256" in errs


def test_run_id_mismatch_is_detected(tmp_path, bundle):
    b = tmp_path / "b"
    shutil.copytree(bundle, b)
    p = b / "dongs.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["run_id"] = "0" * 16
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert any("실행 식별자 불일치" in e for e in ex.verify_bundle(b, _run_of(bundle)))
    other = {**_run_of(bundle), "run_id": "f" * 16}  # 다른 정본 기준으로 검사
    assert any("run_id" in e for e in ex.verify_bundle(bundle, other))


def test_failure_during_write_keeps_previous_bundle(tmp_path, rel_db, bundle, monkeypatch):
    before = _snapshot(bundle)
    calls = {"n": 0}
    orig = Path.write_bytes

    def flaky(self, data):
        calls["n"] += 1
        if calls["n"] == 7:
            raise OSError("디스크 오류 (테스트)")
        return orig(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(OSError):
        ex.export(rel_db, bundle, min_cell_n=5)
    monkeypatch.setattr(Path, "write_bytes", orig)
    assert _snapshot(bundle) == before
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".static.")] == []


def test_verification_failure_keeps_previous_bundle(tmp_path, rel_db, bundle, monkeypatch):
    before = _snapshot(bundle)
    monkeypatch.setattr(ex, "verify_bundle", lambda *a, **k: ["강제 실패"])
    with pytest.raises(ex.ExportError, match="강제 실패"):
        ex.export(rel_db, bundle, min_cell_n=5)
    assert _snapshot(bundle) == before


def test_real_data_cannot_go_to_tracked_or_public_paths(tmp_path):
    import subprocess
    root = config.REPO_ROOT
    front = tmp_path / "frontend"  # 별도 프론트 저장소
    front.mkdir()
    subprocess.run(["git", "init", "-q", str(front)], check=True)
    for path in (root / "docs" / "samples" / "w2-5" / "real", root / "src" / "static",
                 root / "app" / "public" / "data", root / "outputs" / "serving" / "public" / "x",
                 front / "public" / "data", front / "data"):
        with pytest.raises(ex.ExportError):
            ex.guard_export_path(path, "real", allow_tracked_synthetic=True)
    ex.guard_export_path(ex.DEFAULT_OUT, "real")
    ex.guard_export_path(tmp_path / "private" / "static", "real")  # git 작업 트리 밖
    ex.guard_export_path(root / "docs" / "samples" / "w2-5" / "bundle", "synthetic_sample", allow_tracked_synthetic=True)
    with pytest.raises(ex.ExportError):  # 합성이라도 명시적으로 허용하지 않으면 docs/ 금지
        ex.guard_export_path(root / "docs" / "samples" / "w2-5" / "bundle", "synthetic_sample")


def test_masked_real_outputs_are_not_synthetic(tmp_path):
    """SAMPLE-NNN·'(샘플)'로 가렸어도 실제 모형 출력이면 real — docs/samples에 쓸 수 없다."""
    import sqlite3
    masked = [store(i, "마포구", "망원동", "미용업", name=f"(샘플) 미용업 {i}") for i in range(1, 4)]
    for s in masked:
        s["store_id"] = f"SAMPLE-{int(s['store_id'][-5:]):03d}"
    db = make_db(tmp_path, masked, purpose="release")
    conn = sqlite3.connect(db)
    try:
        assert ex.data_kind(conn) == "real"
    finally:
        conn.close()
    with pytest.raises(ex.ExportError):
        ex.guard_export_path(SAMPLES_DIR / "masked", "real", allow_tracked_synthetic=True)


def test_existing_non_bundle_directory_is_not_overwritten(tmp_path, rel_db):
    target = tmp_path / "somewhere"
    target.mkdir()
    (target / "notes.txt").write_text("사용자 파일", encoding="utf-8")
    with pytest.raises(ex.ExportError, match="정적 번들이 아니라"):
        ex.export(rel_db, target, min_cell_n=5)
    assert (target / "notes.txt").exists()


def test_reexport_is_reproducible(tmp_path, rel_db):
    a = ex.export(rel_db, tmp_path / "a", min_cell_n=5)
    b = ex.export(rel_db, tmp_path / "b", min_cell_n=5)
    assert a == b
    assert _snapshot(tmp_path / "a") == _snapshot(tmp_path / "b")


# ---------------------------------------------------------------------------
# 합성 샘플 10건 (docs/samples/w2-5)
REQUIRED_CASES = {"band_low", "band_mid", "band_high", "duplicate_name", "address_distinct", "online_presence_absent",
                  "data_missing", "review_hold", "cost_unavailable", "closed_after_as_of", "policy_not_performed",
                  "policy_check_required"}


def _bundles():
    return [SAMPLES_DIR / "bundle", SAMPLES_DIR / "bundle_no_policy"]


def test_samples_are_valid_synthetic_bundles():
    ids = []
    for b in _bundles():
        assert ex.verify_bundle(b, _run_of(b)) == []
        meta = json.loads((b / "meta.json").read_text(encoding="utf-8"))
        assert meta["data_kind"] == "synthetic_sample" and meta["publication_approved"] is False
        assert "합성 샘플" in meta["publication_note"]
        for p in sorted((b / "reports").glob("*.json")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            assert rv.validate_report(rec) == []
            assert rec["store"]["name"].startswith("(샘플)") and rec["prescriptions"] == []
            assert all(f["explanation"].endswith("[합성 예시]") for f in rec["factors"])
            ids.append(rec["store_id"])
    assert sorted(ids) == [f"SAMPLE-{i:03d}" for i in range(1, 11)]
    cases = json.loads((SAMPLES_DIR / "sample_cases.json").read_text(encoding="utf-8"))
    assert REQUIRED_CASES <= {c for s in cases["samples"] for c in s["cases"]}


def test_sample_search_to_detail_flow():
    cases = json.loads((SAMPLES_DIR / "sample_cases.json").read_text(encoding="utf-8"))
    for b in _bundles():
        idx = json.loads((b / "search_index.json").read_text(encoding="utf-8"))
        meta = json.loads((b / "meta.json").read_text(encoding="utf-8"))
        for e in idx["entries"]:  # 모든 샘플: 상호 검색 → store_id → 상세 파일
            hits = si.search_name(idx["entries"], e["name"])
            assert hits[0]["store_id"] == e["store_id"]
            rec = json.loads((b / meta["report_path_template"].format(store_id=e["store_id"])).read_text(encoding="utf-8"))
            assert rec["store_id"] == e["store_id"]
    idx = json.loads((SAMPLES_DIR / "bundle" / "search_index.json").read_text(encoding="utf-8"))
    for chk in cases["search_checks"]:
        fn = si.search_name if chk["kind"] == "name" else si.search_address
        got = [h["store_id"] for h in fn(idx["entries"], chk["query"])]
        if "expect" in chk:
            assert got == chk["expect"]
        else:
            assert got[0] == chk["expect_first"]


def test_committed_samples_match_generator(tmp_path):
    ss.generate(tmp_path)
    for name in ("bundle", "bundle_no_policy"):
        assert _snapshot(tmp_path / name) == _snapshot(SAMPLES_DIR / name)
    assert (tmp_path / "sample_cases.json").read_bytes() == (SAMPLES_DIR / "sample_cases.json").read_bytes()


def test_sample_policy_and_status_scenarios():
    rec = {p.stem: json.loads(p.read_text(encoding="utf-8")) for b in _bundles() for p in (b / "reports").glob("*.json")}
    assert rec["SAMPLE-010"]["policy_matching"] == "not_performed" and rec["SAMPLE-010"]["policies"] == []
    p8 = {p["id"]: p for p in rec["SAMPLE-008"]["policies"]}
    assert p8["sample_online_support"]["match_status"] == "check_required"
    assert "업력 조건 (인허가일 정보 없음)" in p8["sample_online_support"]["unverifiable_conditions"]
    assert rec["SAMPLE-007"]["store"]["status"]["current"] == "closed"
    assert rec["SAMPLE-005"]["online_presence"] is None
    assert rec["SAMPLE-004"]["online_presence"]["naver_local_registered"] is False
    hold = next(f for f in rec["SAMPLE-006"]["factors"] if f["factor_id"] == "online_attention")
    assert (hold["display"], hold["hold_reason"]) == (False, "online_review")
    assert all(r["unavailable_categories"] == ["비용"] for r in rec.values())


def test_reexport_over_existing_bundle(tmp_path, rel_db):
    """같은 경로에 다시 내보내도 파일 잠금 없이 교체되고 옛 번들·임시 폴더가 남지 않는다 (Windows 포함)."""
    out = tmp_path / "static"
    first = ex.export(rel_db, out, min_cell_n=5)
    second = ex.export(rel_db, out, min_cell_n=5)
    assert first == second and ex.verify_bundle(out, _run_of(out)) == []
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".static")) == []


# ---------------------------------------------------------------------------
# serve_meta.band_cutoffs — PR #36 실제 출력 형태 (d6cfeb9)
def _serve_meta_like_pr36(tmp_path, n_stores):
    """PR #36과 같은 경로로 만든 serve_meta: train_detect가 bands.suggest_cutoffs 결과를 band_cutoffs.csv로 쓰고,
    serve가 read_csv().iloc[0].to_dict()로 읽어 serve_meta.json에 json.dumps(default=str)로 넣는다."""
    import pandas as pd
    cut = {"cut_mid": 0.1493378246, "cut_high": 0.2142019871, "base_rate": 0.1244481612}  # suggest_cutoffs 키 (값은 합성)
    pd.DataFrame([cut]).to_csv(tmp_path / "band_cutoffs.csv", index=False)
    cut_read = pd.read_csv(tmp_path / "band_cutoffs.csv").iloc[0].to_dict()           # numpy.float64 값
    meta = {"score_origin": "2026Q2", "as_of": "2026-06-30", "n_stores": n_stores, "primary_feature_set": "enriched",
            "train_origins": ["2021Q1", "2025Q1"], "n_train_rows": 1000, "band_cutoffs": cut_read, "n_boot": 20,
            "n_background": 16, "detect_run": "outputs/models/detect_v0_enriched", "detect_master_sha256": "0" * 64,
            "master": "outputs/master/master_base.parquet", "master_sha256": "0" * 64,
            "score": "outputs/master/master_score.parquet", "score_sha256": "0" * 64, "online_score": None,
            "licenses": None, "licenses_sha256": None, "n_stores_without_name": None, "calibrated": False,
            "features_used": ["age_months"], "excluded_unvalidated": ["land_price"],
            "diagnosis_scale": "risk 확률과 같음", "band_share": {"low": 0.78, "mid": 0.15, "high": 0.07},
            "display_held_online": 0, "display_held_missing": {}, "seconds": 1.0}
    return json.dumps(meta, ensure_ascii=False, indent=2, default=str), cut


def _build_with_meta(tmp_path, meta_text, name="rel.sqlite"):
    from src.serving import build_db as bd
    from tests.serving_synth import write_inputs
    d = write_inputs(tmp_path / f"in_{name}", STORES)
    (d / "serve_meta.json").write_text(meta_text, encoding="utf-8")
    out = tmp_path / name
    bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out,
             license_snapshot_date="2026-09-11", purpose="release")
    return out


def test_pr36_serve_meta_band_cutoffs_flow_to_static_meta(tmp_path):
    import sqlite3
    from src.serving import build_db as bd
    text, cut = _serve_meta_like_pr36(tmp_path, len(STORES))
    db = _build_with_meta(tmp_path, text)
    conn = sqlite3.connect(db)
    try:
        stored = json.loads(bd.read_run(conn)["band_cutoffs_json"])
    finally:
        conn.close()
    assert stored == cut                                   # serve 원문 그대로 (base_rate 포함)
    ex.export(db, tmp_path / "static", min_cell_n=5)
    meta = json.loads((tmp_path / "static" / "meta.json").read_text(encoding="utf-8"))
    assert meta["band_cutoffs"] == {"cut_mid": cut["cut_mid"], "cut_high": cut["cut_high"]}   # base_rate는 내보내지 않음
    assert rv.validate_def("static_meta", meta) == []


@pytest.mark.parametrize("bad, msg", [
    ({"mid": 0.1493, "high": 0.2142}, "band_cutoffs"),                 # 예전 합성 fixture 이름 — 호환하지 않는다
    ({"cut_mid": 0.1493}, "band_cutoffs"),
    ({"cut_mid": 0.25, "cut_high": 0.2142}, "cut_mid < cut_high"),
    ({"cut_mid": 0.1493, "cut_high": 0.2142, "cut_low": 0.05}, "band_cutoffs"),
    (None, "band_cutoffs가 없다"),
])
def test_band_cutoffs_contract_violations_stop_build(tmp_path, bad, msg):
    from src.serving import build_db as bd
    text, _ = _serve_meta_like_pr36(tmp_path, len(STORES))
    meta = json.loads(text)
    if bad is None:
        meta.pop("band_cutoffs")
    else:
        meta["band_cutoffs"] = bad
    with pytest.raises(bd.BuildError, match=msg):
        _build_with_meta(tmp_path, json.dumps(meta))
    assert not (tmp_path / "rel.sqlite").exists()
