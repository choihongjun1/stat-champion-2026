import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("s10_gate", Path(__file__).resolve().parents[1] / "scripts/s10_lift_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def meta(low=1.5):
    return {"band_provenance": {"high_lift_test": 2.0, "high_lift_ci95": [low, 2.5],
            "test_origins": ["2024Q1", "2024Q2"],
            "high_lift_ci": {"unit": "store_id", "n_boot": 1000, "seed": 20260927}}}


@pytest.mark.parametrize("low,decision", [(1.4999, "not_allowed"), (1.5, "allowed"), (1.5001, "allowed")])
def test_boundary(low, decision):
    result = gate.evaluate(meta(low))
    assert result["decision"] == decision
    assert result["wording"] == gate.BASE_WORDING + ("(약 2배)" if decision == "allowed" else "")


@pytest.mark.parametrize("field", ["high_lift_test", "high_lift_ci95", "test_origins", "high_lift_ci"])
def test_missing(field):
    value = meta()
    del value["band_provenance"][field]
    with pytest.raises(ValueError):
        gate.evaluate(value)


@pytest.mark.parametrize("key,value", [("seed", 1), ("n_boot", 999), ("unit", "row")])
def test_provenance(key, value):
    item = meta()
    item["band_provenance"]["high_lift_ci"][key] = value
    with pytest.raises(ValueError):
        gate.evaluate(item)


@pytest.mark.parametrize("field,value", [("high_lift_test", float("nan")), ("high_lift_ci95", [float("nan"), 2]),
                                         ("high_lift_ci95", [1, float("inf")]), ("high_lift_test", True),
                                         ("high_lift_ci95", [3, 2])])
def test_invalid_numbers(field, value):
    item = meta()
    item["band_provenance"][field] = value
    with pytest.raises(ValueError):
        gate.evaluate(item)


def test_cli_provenance_and_stale_output(tmp_path):
    source = tmp_path / "run_meta.json"
    source.write_text(json.dumps(meta()), encoding="utf-8")
    out = tmp_path / "out"
    argv = ["--run-meta", str(source), "--out-dir", str(out)]
    assert gate.main(argv) == 0
    result = json.loads((out / "s10_gate.json").read_text(encoding="utf-8"))
    assert result["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["generating_commit"]
    text = (out / "s10_gate.md").read_text(encoding="utf-8")
    assert all(word not in text for word in ("효과", "유의", "확률"))
    source.write_text('{}', encoding="utf-8")
    assert gate.main(argv) == 1
    assert not list(out.glob("s10_gate.*"))
