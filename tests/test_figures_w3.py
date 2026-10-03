import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("figures", ROOT / "scripts/figures_w3.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


@pytest.fixture
def font_file():
    value = os.environ.get("W3_TEST_FONT")
    if value:
        app.font(Path(value))
        return Path(value)
    try:
        return Path(app.font().get_file())
    except app.MissingInput:
        pytest.skip("CJK 글꼴 없음(Noto Sans CJK KR/KR/CJK, Malgun Gothic); 설치하지 않음")


def run(tmp_path, font_file, only, extra=()):
    out = tmp_path / "figures"
    code = app.main(["--font-file", str(font_file), "--only", only, "--out-dir", str(out), *extra])
    report = json.loads((out / "figures_report.json").read_text(encoding="utf-8"))
    return code, out, report


def sidecar(out, name):
    assert (out / f"{name}.png").is_file()
    assert (out / f"{name}.svg").is_file()
    return json.loads((out / f"{name}.json").read_text(encoding="utf-8"))


def auc_files(tmp_path):
    paths = [tmp_path / "adopted.csv", tmp_path / "previous.csv"]
    for path, start in zip(paths, (.6, .5)):
        pd.DataFrame({"origin": app.ORIGINS, "auc": [start+i/1000 for i in range(10)], "feature_set": "enriched"}).to_csv(path, index=False)
    return paths


def test_f1_input_means_and_hash(tmp_path, font_file):
    adopted, previous = auc_files(tmp_path)
    argv = ["--adopted-metrics", str(adopted), "--previous-metrics", str(previous)]
    code, out, _ = run(tmp_path, font_file, "F1", argv)
    assert code == 0
    info = sidecar(out, "F1")
    assert info["values"]["series"][0]["mean10"] == pytest.approx(.6045)
    assert info["values"]["series"][0]["ci95"] is None
    assert info["inputs"][0]["sha256"] == hashlib.sha256(adopted.read_bytes()).hexdigest()
    frame = pd.read_csv(adopted)
    frame["auc"] += .05
    frame.to_csv(adopted, index=False)
    assert run(tmp_path, font_file, "F1", argv)[0] == 0
    assert sidecar(out, "F1")["values"]["series"][0]["mean10"] == pytest.approx(.6545)
    assert "ROUND_HALF_UP" in info["rounding"]


@pytest.mark.parametrize("decision", [None, "not_allowed", "allowed"])
def test_f2_gate(tmp_path, font_file, decision):
    path, meta, gate = [tmp_path / name for name in ("bands.csv", "run_meta.json", "gate.json")]
    # Producer columns plus explicit saved CI extension, not a recalculated CI.
    pd.DataFrame({"band": ["low", "mid", "high"], "n": [20, 10, 10], "obs_rate": [.04, .08, .2],
                  "stored_lower": [.02, .06, .15], "stored_upper": [.07, .1, .25]}).to_csv(path, index=False)
    meta.write_text(json.dumps({"band_provenance": {"base_rate": .3, "high_lift_test": 2, "high_lift_ci95": [1.5, 2.5]}}), encoding="utf-8")
    argv = ["--band-profile", str(path), "--run-meta", str(meta), "--band-ci-low-col", "stored_lower", "--band-ci-high-col", "stored_upper"]
    if decision:
        gate.write_text(json.dumps({"decision": decision, "input_sha256": hashlib.sha256(meta.read_bytes()).hexdigest()}), encoding="utf-8")
        argv += ["--s10-gate", str(gate)]
    code, out, _ = run(tmp_path, font_file, "F2", argv)
    assert code == 0
    info = sidecar(out, "F2")
    assert ("약 2배" in info["strings"]) == (decision == "allowed")
    assert info["values"]["obs_rate"] == [.04, .08, .2]
    assert info["values"]["overall_mean"] == pytest.approx(.09)


def test_f2_current_producer_without_ci_has_no_error_bars(tmp_path, font_file):
    path, meta = tmp_path / "bands.csv", tmp_path / "run_meta.json"
    pd.DataFrame({"band": ["low", "mid", "high"], "n": [2, 2, 2], "obs_rate": [.1, .2, .3]}).to_csv(path, index=False)
    meta.write_text(json.dumps({"band_provenance": {}}), encoding="utf-8")
    code, out, report = run(tmp_path, font_file, "F2", ["--band-profile", str(path), "--run-meta", str(meta)])
    assert code == 0 and report["figures"]["F2"]["status"] == "generated"
    info = sidecar(out, "F2")
    assert info["values"]["ci95"] is None
    assert "미저장" in info["values"]["note"]
    assert "약 2배" not in info["strings"]


def test_f2_only_one_ci_column_is_not_generated(tmp_path, font_file):
    path, meta = tmp_path / "bands.csv", tmp_path / "run_meta.json"
    pd.DataFrame({"band": ["low", "mid", "high"], "n": [2, 2, 2], "obs_rate": [.1, .2, .3]}).to_csv(path, index=False)
    meta.write_text("{}", encoding="utf-8")
    _, _, report = run(tmp_path, font_file, "F2", ["--band-profile", str(path), "--run-meta", str(meta), "--band-ci-low-col", "lo"])
    assert report["figures"]["F2"]["status"] == "입력 없음"


def test_font_without_hangul_returns_three(tmp_path):
    import matplotlib
    dejavu = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
    adopted, previous = auc_files(tmp_path)
    code, out, report = run(tmp_path, dejavu, "F1", ["--adopted-metrics", str(adopted), "--previous-metrics", str(previous)])
    assert code == 3
    assert report["font"]["cmap_check_passed"] is False and report["font"]["missing_glyphs"]


def test_report_records_font_and_cmap(tmp_path, font_file):
    adopted, previous = auc_files(tmp_path)
    _, out, report = run(tmp_path, font_file, "F1", ["--adopted-metrics", str(adopted), "--previous-metrics", str(previous)])
    assert report["font"]["cmap_check_passed"] is True
    assert report["font"]["sha256"] == hashlib.sha256(font_file.read_bytes()).hexdigest()
    assert sidecar(out, "F1")["font"]["name"] == report["font"]["name"]


def test_f3_saved_named_samples_ci(tmp_path, font_file):
    path = tmp_path / "dml.json"
    results = []
    for sample, ate in (("전체 보조", -.02), ("56 주", .03)):
        results.append({"sample": sample, "analyses": {"1_main": {"ate": ate, "ci_low": -.04, "ci_high": .08},
                                                       "2_weighted": {"ate": ate+.01, "ci_low": -.02, "ci_high": .09}}})
    path.write_text(json.dumps({"meta": {}, "results": results}), encoding="utf-8")
    code, out, _ = run(tmp_path, font_file, "F3", ["--dml", str(path)])
    assert code == 0
    rows = sidecar(out, "F3")["values"]["rows"]
    assert rows[0]["sample"] == "56 주" and rows[0]["ci95"] == [-.04, .08]
    assert rows[2]["sample"] == "전체 보조"


def test_f4_missing_and_saved_scores(tmp_path, font_file):
    code, out, report = run(tmp_path, font_file, "F4")
    assert code == 0 and report["figures"]["F4"]["status"] == "입력 없음"
    assert "저장하지 않음" in report["figures"]["F4"]["reason"]
    scores = tmp_path / "scores.csv"
    pd.DataFrame({"saved_ps": [.2, .3, .6, .7], "saved_d": [0, 0, 1, 1]}).to_csv(scores, index=False)
    code, out, _ = run(tmp_path, font_file, "F4", ["--propensity", str(scores), "--ps-col", "saved_ps", "--treat-col", "saved_d"])
    assert code == 0
    info = sidecar(out, "F4")
    assert info["values"]["truncate_lines"] == [.05, .95]
    assert [x["n"] for x in info["values"]["histograms"]] == [2, 2]


def test_f5_saved_fraction_mapping_and_original_summary(tmp_path, font_file):
    path = tmp_path / "summary.csv"
    pd.DataFrame({"method": ["stratified"], "n_background": [256], "n_seeds": [5], "top1_median": [.7], "sign_median": [.8]}).to_csv(path, index=False)
    assert run(tmp_path, font_file, "F5", ["--s8-summary", str(path)])[2]["figures"]["F5"]["status"] == "입력 없음"
    pd.DataFrame({"saved_factor": ["온라인", "입지"], "saved_rate": [.12, .08], "saved_n": [200, 200]}).to_csv(path, index=False)
    code, out, _ = run(tmp_path, font_file, "F5", ["--s8-summary", str(path), "--s8-factor-col", "saved_factor", "--s8-rate-col", "saved_rate", "--s8-n-col", "saved_n"])
    assert code == 0
    info = sidecar(out, "F5")
    assert info["values"]["rates"] == [.12, .08]
    assert info["values"]["sample_n"] == 200


def test_bad_label_returns_one(tmp_path, font_file):
    adopted, previous = auc_files(tmp_path)
    assert run(tmp_path, font_file, "F1", ["--adopted-metrics", str(adopted), "--previous-metrics", str(previous), "--adopted-label", "확률"])[0] == 1


def test_missing_font_returns_three(tmp_path, monkeypatch):
    def missing(*args):
        raise app.MissingInput("missing")
    monkeypatch.setattr(app, "font", missing)
    assert app.main(["--only", "F4", "--out-dir", str(tmp_path)]) == 3


def test_rounding():
    assert app.rounded(.1235) == "0.124"
    assert app.rounded(-.1235) == "-0.124"


def test_f2_reserves_headroom_for_annotation(tmp_path):
    import types
    import matplotlib.pyplot as plt
    path, meta, gate = [tmp_path / name for name in ("bands.csv", "run_meta.json", "gate.json")]
    pd.DataFrame({"band": ["low", "mid", "high"], "n": [20, 10, 10], "obs_rate": [.04, .08, .2],
                  "lo": [.02, .06, .15], "hi": [.07, .1, .25]}).to_csv(path, index=False)
    meta.write_text("{}", encoding="utf-8")
    gate.write_text(json.dumps({"decision": "allowed", "input_sha256": hashlib.sha256(meta.read_bytes()).hexdigest()}), encoding="utf-8")
    args = types.SimpleNamespace(band_profile=path, run_meta=meta, s10_gate=gate, band_ci_low_col="lo", band_ci_high_col="hi")
    fig, ax = plt.subplots(figsize=(9, 5))
    try:
        values, _ = app.f2(args, ax)
        fig.canvas.draw()
        assert values["s10_allowed"] is True
        assert ax.get_ylim()[1] >= .25 * 1.15
        note = next(t for t in ax.texts if t.get_text() == "약 2배")
        box, axes_box = note.get_window_extent(), ax.get_window_extent()
        assert axes_box.y0 <= box.y0 and box.y1 <= axes_box.y1
    finally:
        plt.close(fig)
