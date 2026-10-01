"""All registry inputs are synthetic files under tmp_path."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_numbers_registry.py"
spec = importlib.util.spec_from_file_location("registry", SCRIPT)
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def definition(id="N-TEST", **kwargs):
    return {"id": id, "label": "합성 수치", "metric": "합성 지표", "window": "합성 창",
            "sample": "합성 표본", "stage": "detect", "artifact": "test.json",
            "extract": {"type": "json", "path": ["value"]}, "format": "{:.4f}",
            "status": "확정", "rule_ref": "A4", **kwargs}


def write_config(tmp_path, entries):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


def test_json_array_ci_format_sha_and_time(tmp_path):
    artifact = tmp_path / "test.json"
    artifact.write_text(json.dumps({"results": [{"ate": .01234, "lo": -.02, "hi": .05}]}), encoding="utf-8")
    extract = lambda key: {"type": "json", "path": ["results", 0, key]}
    entry = definition(extract=extract("ate"), ci={"low": extract("lo"), "high": extract("hi")})
    table, failures, warnings = registry.build_registry([entry], tmp_path, commit="a" * 40 + " +dirty")
    row = table.iloc[0]
    assert row["표시값"] == "0.0123"
    assert row["CI"] == "[-0.0200, 0.0500]"
    assert row["artifact sha256(앞 12자)"] == hashlib.sha256(artifact.read_bytes()).hexdigest()[:12]
    assert row["artifact 수정 시각"].endswith("+00:00")
    assert row["생성 commit"] == "a" * 40 + " +dirty"
    assert failures == 0 and warnings == []


@pytest.mark.parametrize("agg,expected", [("mean", 2), ("min", 1), ("max", 3), ("count", 2)])
def test_csv_aggregations(tmp_path, agg, expected):
    content = b"group,value\n01,1\n01,3\n02,99\n"
    assert registry.extract_value(content, {"type": "csv_agg", "filter": {"group": "01"}, "column": "value", "agg": agg}) == expected


def test_csv_cell_and_percent():
    value = registry.extract_value(b"model,value\na,0.125\nb,0.5\n", {"type": "csv_cell", "filter": {"model": "a"}, "column": "value"})
    assert registry._format(value, "{:.1%}") == "12.5%"


@pytest.mark.parametrize("content", [b"model,value\nb,1\n", b"model,value\na,1\na,2\n"])
def test_csv_cell_requires_exactly_one_row(content):
    with pytest.raises(registry.ExtractionError):
        registry.extract_value(content, {"type": "csv_cell", "filter": {"model": "a"}, "column": "value"})


@pytest.mark.parametrize("extract", [
    {"type": "csv_agg", "column": "value", "agg": "sum"},
    {"type": "csv_agg", "column": "value", "agg": "mean", "expected_rows": 10},
    {"type": "csv_agg", "column": "value", "agg": "mean", "expected_values": {"origin": ["2023Q1"]}},
    {"type": "csv_cell", "column": "absent"},
    {"type": "csv_cell", "column": "value", "filter": {"origin": "TBD"}},
])
def test_csv_schema_and_window_guards(extract):
    with pytest.raises(registry.ExtractionError):
        registry.extract_value(b"origin,value\n2024Q1,1\n", extract)


@pytest.mark.parametrize("value", [None, "text", True, float("inf"), float("nan"), [1], {"nested": 1}])
def test_json_nonfinite_or_nonscalar_rejected(value):
    with pytest.raises(registry.ExtractionError):
        registry.extract_value(json.dumps({"value": value}).encode(), {"type": "json", "path": ["value"]})


def test_missing_and_allowed_status(tmp_path):
    entries = [definition(), definition("N-FUTURE", artifact="TBD", status="재생성 후 생성")]
    table, failures, _ = registry.build_registry(entries, tmp_path, allow_missing_status="재생성 후 생성")
    assert failures == 1
    assert table.iloc[0]["표시값"].startswith("MISSING:")
    assert table.iloc[1]["표시값"].startswith("MISSING(예정):")


def test_extract_failure_retains_hash_and_invalid_ci_not_partial(tmp_path):
    (tmp_path / "test.json").write_text('{"value":1, "lo":2,"hi":1}', encoding="utf-8")
    entry = definition(ci={"low": {"type": "json", "path": ["lo"]}, "high": {"type": "json", "path": ["hi"]}})
    table, failures, _ = registry.build_registry([entry], tmp_path)
    assert failures == 1 and table.iloc[0]["CI"] == ""
    assert table.iloc[0]["표시값"].startswith("MISSING")
    assert re.fullmatch(r"[0-9a-f]{12}", table.iloc[0]["artifact sha256(앞 12자)"])
    _, failures, _ = registry.build_registry([entry], tmp_path, allow_missing_status="확정")
    assert failures == 0


def test_forbidden_alias_compares_formatted_values(tmp_path):
    (tmp_path / "test.json").write_text('{"value":0.12341,"other":0.12342}', encoding="utf-8")
    entries = [definition("N-A", forbidden_alias=["N-B"]), definition("N-B", extract={"type": "json", "path": ["other"]}, forbidden_alias=["N-A"])]
    _, failures, warnings = registry.build_registry(entries, tmp_path)
    assert failures == 0 and len(warnings) == 1
    assert "N-A / N-B" in warnings[0]


@pytest.mark.parametrize("artifact", ["../outside.json", "TBD"])
def test_no_path_escape(tmp_path, artifact):
    table, failures, _ = registry.build_registry([definition(artifact=artifact)], tmp_path)
    assert failures == 1 and table.iloc[0]["표시값"].startswith("MISSING")


@pytest.mark.parametrize("mutation", ["duplicate", "status", "field", "alias", "words"])
def test_config_errors(tmp_path, mutation):
    entries = [definition()]
    if mutation == "duplicate": entries *= 2
    elif mutation == "status": entries[0]["status"] = "bad"
    elif mutation == "field": del entries[0]["metric"]
    elif mutation == "alias": entries[0]["forbidden_alias"] = ["N-ABSENT"]
    elif mutation == "words": entries[0]["label"] = "효과"
    with pytest.raises(ValueError):
        registry.load_definitions(write_config(tmp_path, entries))


def test_git_commit_clean_and_dirty(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-qm", "synthetic"], check=True)
    assert re.fullmatch(r"[0-9a-f]{40} \+clean", registry.generation_commit(tmp_path))
    (tmp_path / "synthetic.txt").write_text("synthetic", encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{40} \+dirty", registry.generation_commit(tmp_path))


def test_cli_outputs_and_exit_codes(tmp_path, capsys):
    config = write_config(tmp_path, [definition()])
    out = tmp_path / "registry"
    args = ["--config", str(config), "--outputs-root", str(tmp_path), "--out-dir", str(out)]
    assert registry.main(args) == 1
    (tmp_path / "test.json").write_text('{"value":0.1}', encoding="utf-8")
    assert registry.main(args) == 0
    md = (out / "numbers_registry.md").read_text(encoding="utf-8")
    assert md.startswith(registry.HEADER)
    assert "0.1000" in md
    assert not any(word in md for word in ("효과", "유의"))
    csv = pd.read_csv(out / "numbers_registry.csv", keep_default_na=False)
    assert csv.loc[0, "표시값"] == 0.1
    config = write_config(tmp_path, [definition(status="재생성 후 생성", artifact="TBD")])
    assert registry.main(args + ["--allow-missing-status", "재생성 후 생성"]) == 0
    assert "MISSING(예정)" in (out / "numbers_registry.md").read_text(encoding="utf-8")


def test_cli_invalid_config_exit_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        registry.main(["--config", str(tmp_path / "missing.json")])
    assert exc.value.code == 2


def test_initial_definitions_load_without_artifacts():
    entries = registry.load_definitions(SCRIPT.parents[1] / "configs/numbers_w3.json")
    assert len(entries) == 13
    lookup = {x["id"]: x for x in entries}
    assert lookup["N-DML-56-ATE"]["extract"]["path"] == ["results", {"sample": "56 주"}, "analyses", "1_main", "ate"]
    assert lookup["N-45-DIFF8"]["extract"]["filter"]["scope"] == "선택값=고정값 origin 8개 (2023Q3~2025Q2)"
    assert lookup["N-AUC-MEAN10"]["extract"]["expected_rows"] == 10
    assert "TBD" not in json.dumps(entries, ensure_ascii=False)
    assert "N-AUC-POOLED" not in lookup
    for id in ("N-PANEL", "N-PANEL-ROWS", "N-PANEL-EVENTS", "N-PANEL-EVENT-RATE"):
        assert lookup[id]["artifact"] == "master/master_base.parquet"
        assert lookup[id]["status"] == "확정"
    assert lookup["N-AUC-POOLED-PRE"]["status"] == "잠정(재생성 전)"
    assert lookup["N-AUC-MEAN10-PRE"]["status"] == "잠정(재생성 전)"


def test_oversized_json_number_is_missing(tmp_path):
    (tmp_path / "test.json").write_text('{"value":' + "1" + "0" * 500 + '}', encoding="utf-8")
    table, failures, _ = registry.build_registry([definition()], tmp_path)
    assert failures == 1 and table.iloc[0]["표시값"].startswith("MISSING")


def test_malformed_window_guard_is_missing(tmp_path):
    (tmp_path / "test.csv").write_text("origin,value\n2023Q1,0.5\n", encoding="utf-8")
    entry = definition(artifact="test.csv", extract={"type": "csv_agg", "column": "value", "agg": "mean", "expected_values": "bad"})
    table, failures, _ = registry.build_registry([entry], tmp_path)
    assert failures == 1 and table.iloc[0]["표시값"].startswith("MISSING")


def test_csv_count_text_and_empty_selection():
    content = b"group,value\na,synthetic\na,\nb,other\n"
    extract = {"type": "csv_agg", "column": "value", "agg": "count", "filter": {"group": "a"}}
    assert registry.extract_value(content, extract) == 1
    extract["filter"] = {"group": "absent"}
    assert registry.extract_value(content, extract) == 0


@pytest.mark.parametrize("op,column,expected", [("rows", None, 4), ("nunique", "store_id", 3), ("sum", "event_12m", 2), ("mean", "event_12m", .5)])
def test_parquet_stat_reads_only_required_column(tmp_path, monkeypatch, op, column, expected):
    path = tmp_path / "panel.parquet"
    pd.DataFrame({"store_id": ["SYN-000001", "SYN-000001", "SYN-000002", "SYN-000003"],
                  "event_12m": [1, 0, 1, 0], "unused": ["synthetic"] * 4}).to_parquet(path, index=False)
    original = pd.read_parquet
    calls = []
    def guarded(path, **kwargs):
        calls.append(kwargs["columns"])
        assert kwargs["columns"] == [column]
        return original(path, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", guarded)
    extract = {"type": "parquet_stat", "op": op}
    if column is not None: extract["column"] = column
    table, failures, _ = registry.build_registry([definition(artifact="panel.parquet", extract=extract)], tmp_path)
    assert failures == 0
    assert table.iloc[0]["표시값"] == f"{expected:.4f}"
    assert calls == ([] if op == "rows" else [[column]])
    assert table.iloc[0]["artifact sha256(앞 12자)"] == hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    assert "SYN-" not in table.to_csv(index=False)


def test_parquet_missing_column_is_missing(tmp_path):
    pd.DataFrame({"value": [1]}).to_parquet(tmp_path / "x.parquet", index=False)
    entry = definition(artifact="x.parquet", extract={"type": "parquet_stat", "op": "sum", "column": "absent"})
    table, failures, _ = registry.build_registry([entry], tmp_path)
    assert failures == 1 and table.iloc[0]["표시값"].startswith("MISSING")


@pytest.mark.parametrize("matches", [0, 1, 2])
def test_json_object_selector_exactly_one(matches):
    content = json.dumps({"results": [{"sample": "56 주", "value": .25}] * matches + [{"sample": "전체 보조", "value": .8}]}).encode()
    extract = {"type": "json", "path": ["results", {"sample": "56 주"}, "value"]}
    if matches == 1:
        assert registry.extract_value(content, extract) == .25
    else:
        with pytest.raises(registry.ExtractionError):
            registry.extract_value(content, extract)


def test_dml_definitions_select_reordered_samples(tmp_path):
    entries = registry.load_definitions(SCRIPT.parents[1] / "configs/numbers_w3.json")
    entries = [e for e in entries if e["id"].startswith("N-DML-")]
    path = tmp_path / "prescribe/dml_results.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"results": [
        {"sample": "전체 보조", "analyses": {"1_main": {"ate": .8, "ci_low": .7, "ci_high": .9}}},
        {"sample": "56 주", "analyses": {"1_main": {"ate": .2, "ci_low": .1, "ci_high": .3, "n": 20, "n_treated": 5}}},
    ]}), encoding="utf-8")
    table, failures, _ = registry.build_registry(entries, tmp_path)
    assert failures == 0
    rows = table.set_index("id")
    assert rows.loc["N-DML-56-ATE", "표시값"] == "0.2000"
    assert rows.loc["N-DML-ALL-ATE", "표시값"] == "0.8000"
    assert rows.loc["N-DML-56-ATE", "CI"] == "[0.1000, 0.3000]"
    assert rows.loc["N-DML-56-N", "표시값"] == "20"
    assert rows.loc["N-DML-56-NTREAT", "표시값"] == "5"


def test_initial_alias_pairs(tmp_path):
    entries = registry.load_definitions(SCRIPT.parents[1] / "configs/numbers_w3.json")
    lookup = {e["id"]: e for e in entries}
    ids = ("N-AUC-MEAN10", "N-AUC-MEAN10-PRE", "N-AUC-POOLED-PRE")
    assert lookup[ids[0]]["forbidden_alias"] == [ids[2]]
    assert lookup[ids[1]]["forbidden_alias"] == [ids[2]]
    assert set(lookup[ids[2]]["forbidden_alias"]) == {ids[0], ids[1]}
    (tmp_path / "test.json").write_text('{"value":0.5}', encoding="utf-8")
    synthetic = [definition(id, forbidden_alias=lookup[id]["forbidden_alias"]) for id in ids]
    _, failures, warnings = registry.build_registry(synthetic, tmp_path)
    assert failures == 0 and len(warnings) == 2
