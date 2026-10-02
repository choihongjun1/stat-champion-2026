import json

import pandas as pd
import pytest

from src.serving import build_db as bd, upgrade_sensitivity as us
from tests.serving_synth import store, write_inputs


def source(tmp_path):
    d = write_inputs(tmp_path / "in", [store(1, "마포구", "망원동", "미용업")])
    recs = bd.read_jsonl(d / "reports.jsonl")
    rows = []
    for f in recs[0]["factors"]:
        f.pop("interpretation_sensitive")
        f.pop("sensitivity_label")
        flag = f["factor_id"] == "tenure"
        rows.append({"store_id": recs[0]["store_id"], "origin": "2026Q2", "factor_id": f["factor_id"],
                     "interpretation_sensitive": flag, "sensitivity_label": "해석 민감" if flag else "",
                     **{k: f[k] for k in ("contribution", "direction", "display")}})
    (d / "reports.jsonl").write_text(json.dumps(recs[0], ensure_ascii=False)+"\n", encoding="utf-8")
    pd.DataFrame(rows).to_parquet(d / "diagnosis.parquet", index=False)
    return d, recs


def test_upgrade_uses_source_pair_only_and_builds_release(tmp_path):
    d, before = source(tmp_path)
    original = (d / "reports.jsonl").read_bytes()
    out = tmp_path / "upgraded"
    meta = us.upgrade(d, out)
    assert (d / "reports.jsonl").read_bytes() == original
    after = bd.read_jsonl(out / "reports.jsonl")
    for old, new in zip(before[0]["factors"], after[0]["factors"]):
        assert {k: new[k] for k in old} == old
    assert after[0]["factors"][0]["sensitivity_label"] == "해석 민감"
    assert meta["report_sensitivity_upgrade"]["source_reports_sha256"] == bd.sha256(d / "reports.jsonl")
    run = bd.build(out / "reports.jsonl", out / "serve_meta.json", d / "licenses.parquet", tmp_path / "report.sqlite",
                   purpose="release", license_snapshot_date="2026-09-11")
    assert run["final_contract"] == "passed"
    with pytest.raises(bd.BuildError, match="destination"):
        us.upgrade(d, out)


@pytest.mark.parametrize("change", ["duplicate", "missing", "label", "null", "integer", "primary", "conflict"])
def test_upgrade_rejects_bad_or_conflicting_source(tmp_path, change):
    d, recs = source(tmp_path)
    diag = pd.read_parquet(d / "diagnosis.parquet")
    if change == "duplicate":
        diag = pd.concat([diag, diag.head(1)])
    elif change == "missing":
        diag = diag.iloc[1:]
    elif change == "label":
        diag.loc[0, "sensitivity_label"] = ""
    elif change == "null":
        diag["interpretation_sensitive"] = None
    elif change == "integer":
        diag["interpretation_sensitive"] = 1
    elif change == "primary":
        diag.loc[0, "contribution"] = 0.2
    else:
        recs[0]["factors"][0].update(interpretation_sensitive=False, sensitivity_label="")
        (d / "reports.jsonl").write_text(json.dumps(recs[0], ensure_ascii=False)+"\n", encoding="utf-8")
    diag.to_parquet(d / "diagnosis.parquet", index=False)
    out = tmp_path / "upgraded"
    with pytest.raises(bd.BuildError):
        us.upgrade(d, out)
    assert not out.exists()
