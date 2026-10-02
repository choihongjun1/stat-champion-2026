"""Small serve-like input -> canonical SQLite -> private static bundle."""
import copy
import json
import sqlite3

import pytest

from src.serving import build_db as bd, export_static as ex, report_validation as rv
from tests.serving_synth import write_inputs, store


def test_contract_constants_match_latest_main_producers():
    from src.models import background, detect
    from src.serving import release_contract as contract
    assert contract.MODEL_CLASS == detect.MODEL_CLASS
    assert contract.S8_VERSION == background.S8_RULE["rule_version"]
    assert contract.SEEDS == background.S8_RULE["seeds"]


def inputs(tmp_path):
    d = write_inputs(tmp_path / "input", [store(1, "마포구", "망원동", "미용업", "high")])
    records = bd.read_jsonl(d / "reports.jsonl")
    meta = json.loads((d / "serve_meta.json").read_text(encoding="utf-8"))
    return d, records, meta


@pytest.mark.parametrize("mutate, match", [
    (lambda m: m.update(params_name="default"), "params_name"),
    (lambda m: m.update(params_name="legacy_default"), "params_name"),
    (lambda m: m.update(params_name="legacy_unnamed"), "params_name"),
    (lambda m: m.pop("params_name"), "params_name"),
    (lambda m: m.update(params_contract="override (비운영)"), "params_contract"),
    (lambda m: m.update(model_class="HGB"), "model_class"),
    (lambda m: m["detect_run_provenance"].pop("run_meta_sha256"), "run_meta_sha256"),
    (lambda m: m["detect_run_provenance"].update(run_meta_sha256="fake"), "sha256"),
    (lambda m: m["detect_run_provenance"].update(params_name="default"), "params_name"),
    (lambda m: m["s8_rule"].update(rule_version="old"), "S8 version"),
    (lambda m: m["s8_rule"].update(n_background=16), "256"),
    (lambda m: m["background"].update(operational=False), "operational"),
    (lambda m: m["background"]["backgrounds"].pop("sensitivity"), "sensitivity"),
    (lambda m: m["background"]["backgrounds"]["primary"].update(seed=20261001), "20260931"),
    (lambda m: m["background"]["backgrounds"]["sensitivity"].update(seed=20260931), "20261001"),
    (lambda m: m["background"]["backgrounds"]["sensitivity"].update(operational=False), "operational"),
    (lambda m: m["background"]["backgrounds"]["primary"].pop("rows_sha256"), "rows_sha256"),
    (lambda m: m["background"]["backgrounds"]["sensitivity"].pop("index_sha256"), "index_sha256"),
    (lambda m: m.pop("s8_summary"), "s8_summary"),
    (lambda m: m.pop("cutoff_provenance"), "cutoff_provenance"),
    (lambda m: m["band_cutoffs"].pop("base_rate"), "base_rate"),
    (lambda m: m["band_cutoffs"].update(high_fallback=True), "band_cutoffs"),
])
def test_operational_release_rejects_legacy_and_incomplete_provenance(tmp_path, mutate, match):
    d, records, meta = inputs(tmp_path)
    mutate(meta)
    (d / "serve_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    out = tmp_path / "report.sqlite"
    with pytest.raises(bd.BuildError, match=match):
        bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out,
                 purpose="release", license_snapshot_date="2026-09-11")
    assert not out.exists()


@pytest.mark.parametrize("flag,label,ok", [(False, "", True), (True, "해석 민감", True),
                                          (False, "해석 민감", False), (True, "", False)])
def test_sensitivity_pair_schema_and_primary_values(tmp_path, flag, label, ok):
    _, records, meta = inputs(tmp_path)
    rec = records[0]
    original = copy.deepcopy(rec["factors"][0])
    rec["factors"][0].update(interpretation_sensitive=flag, sensitivity_label=label)
    assert (rv.validate_def("serve_record_v0_2", rec) == []) is ok
    if ok:
        assert bd.validate_serve_input(records, meta, purpose="release") == "0.2"
    assert {k: rec["factors"][0][k] for k in ("contribution", "direction", "display")} == {
        k: original[k] for k in ("contribution", "direction", "display")}


def test_old_sensitivity_input_is_dev_not_static_ready(tmp_path):
    d, records, meta = inputs(tmp_path)
    for f in records[0]["factors"]:
        f.pop("interpretation_sensitive")
        f.pop("sensitivity_label")
    assert bd.validate_serve_input(records, meta) == "0.2"
    with pytest.raises(bd.BuildError, match="sensitivity pair"):
        bd.validate_serve_input(records, meta, purpose="release")
    (d / "reports.jsonl").write_text(json.dumps(records[0], ensure_ascii=False)+"\n", encoding="utf-8")
    run = bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", tmp_path / "old.sqlite")
    assert run["final_contract"] == "not_ready"
    with pytest.raises(ex.ExportError, match="기술적 계약"):
        ex.export(tmp_path / "old.sqlite", tmp_path / "static", min_cell_n=10)


def test_small_pipeline_preserves_all_provenance_and_blocks_publication(tmp_path):
    d, _, meta = inputs(tmp_path)
    db = tmp_path / "report.sqlite"
    bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", db,
             purpose="release", license_snapshot_date="2026-09-11")
    with sqlite3.connect(db) as conn:
        run = bd.read_run(conn)
        assert json.loads(run["serve_meta_json"]) == meta
        rec = next(bd.iter_reports(conn))
        assert rec["_schema_version"] == "0.3" and rv.validate_report(rec) == []
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE factors DROP COLUMN interpretation_sensitive")
        conn.execute("ALTER TABLE factors DROP COLUMN sensitivity_label")
        assert next(bd.iter_reports(conn))["factors"][0]["interpretation_sensitive"] is None
        conn.rollback()
    bundle = tmp_path / "static_private"
    manifest = ex.export(db, bundle, min_cell_n=10)
    public = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
    assert public["technical_gate"]["passed"] and public["publication_approved"] is False
    assert manifest["publication_approved"] is False
    assert public["provenance"]["primary_seed"] == 20260931
    assert public["provenance"]["sensitivity_seed"] == 20261001
    assert "rows_sha256" not in json.dumps(public)
    assert ex.verify_bundle(bundle, {k: public[k] for k in ("run_id", "score_origin", "as_of", "n_stores")}) == []


@pytest.mark.parametrize("code", rv.ONLINE_DRIVER_CODES)
def test_release_driver_code_required_and_canonical(tmp_path, code):
    _, records, meta = inputs(tmp_path)
    f = records[0]["factors"][0]
    name, category, actionability = rv.FACTOR_CONTRACT["online_attention"]
    f.update(factor_id="online_attention", name=name, category=category, actionability=actionability,
             driver="화면 문구 변경", driver_code=code)
    assert bd.validate_serve_input(records, meta, purpose="release") == "0.2"
    f.pop("driver_code")
    f["driver"] = "블로그 언급 이력 없음"
    with pytest.raises(bd.BuildError, match="driver_code"):
        bd.validate_serve_input(records, meta, purpose="release")
