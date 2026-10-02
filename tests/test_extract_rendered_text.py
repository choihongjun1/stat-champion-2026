import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("extractor", ROOT / "scripts/extract_rendered_text.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


@pytest.fixture
def bundle(tmp_path):
    out = tmp_path / "out"
    shutil.copytree(ROOT / "tests/fixtures/rendered_out", out)
    return out


def run(bundle, tmp_path, extra=()):
    dump = tmp_path / "dump"
    code = app.main(["--out-dir", str(bundle), "--dump-dir", str(dump), *extra])
    return code, json.loads((dump / "rendered_text_report.json").read_text(encoding="utf-8")), dump


def clean(bundle):
    path = bundle / "bundle/reports/SAMPLE-X.json"
    path.write_text(path.read_text(encoding="utf-8").replace("확률 38%", "예측 점수"), encoding="utf-8")


def test_static_json_mapping(bundle, tmp_path):
    code, report, dump = run(bundle, tmp_path)
    assert code == 1
    found = next(x for x in report["findings"] if x["rule_id"] == "CL-04")
    assert 'reports/SAMPLE-X.json:$["risk"]["note"]' in found["source"]
    text = "".join(x.read_text(encoding="utf-8") for x in (dump / "extracted").glob("*.txt"))
    assert "합성 대체 텍스트" in text and "합성 접근성 이름" in text
    assert "스크립트 전용 문구" not in text and "스타일 전용 문구" not in text
    assert report["input_folder_sha256"] == app.folder_hash(bundle)


def test_capture_without_sidecar(bundle, tmp_path):
    clean(bundle)
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "sample.png").write_bytes(b"synthetic png")
    code, report, _ = run(bundle, tmp_path, ["--captures", str(captures)])
    assert code == 4 and report["judgement"] == "부분 검사"
    assert report["unchecked_captures"] == ["capture:sample.png"]


def test_capture_sidecar_and_fail_on(bundle, tmp_path):
    clean(bundle)
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "sample.png").write_bytes(b"synthetic png")
    (captures / "sample.txt").write_text("신청 가능", encoding="utf-8")
    assert run(bundle, tmp_path, ["--captures", str(captures)])[0] == 0
    code, report, _ = run(bundle, tmp_path, ["--captures", str(captures), "--fail-on", "warn"])
    assert code == 1 and report["findings"][0]["source"] == "capture:sample.png:sidecar"


def test_playwright_unavailable(bundle, tmp_path, monkeypatch, capsys):
    clean(bundle)
    def missing(*args):
        raise ModuleNotFoundError("playwright.sync_api")
    monkeypatch.setattr(app, "rendered", missing)
    code, report, _ = run(bundle, tmp_path, ["--render"])
    assert code == 3 and report["judgement"] == "부분 검사"
    assert "렌더 모드 사용 불가: playwright 미설치" in capsys.readouterr().err


def test_render_failure_partial(bundle, tmp_path, monkeypatch):
    clean(bundle)
    monkeypatch.setattr(app, "rendered", lambda *args: [("합성 페이지", [], "TimeoutError")])
    code, report, _ = run(bundle, tmp_path, ["--render"])
    assert code == 4 and len(report["extraction_failures"]) == 1


def test_routes_from_template_and_index(bundle):
    assert app.report_routes(bundle, "/report/{store_id}/") == ["/report/SAMPLE-X/"]


def test_empty_returns_three(tmp_path):
    out = tmp_path / "empty"
    out.mkdir()
    assert run(out, tmp_path)[0] == 3


def test_invalid_json_and_old_dumps(bundle, tmp_path):
    clean(bundle)
    assert run(bundle, tmp_path)[0] == 0
    (bundle / "bundle/reports/SAMPLE-X.json").write_text("{", encoding="utf-8")
    code, report, dump = run(bundle, tmp_path)
    assert code == 4
    assert len(report["extraction_failures"]) == 1
    assert "예측 점수" not in "".join(p.read_text(encoding="utf-8") for p in (dump / "extracted").glob("*.txt"))


def test_render_fetch_when_available(bundle, tmp_path):
    api = pytest.importorskip("playwright.sync_api")
    with api.sync_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("Chromium 미설치; 설치하지 않음")
    clean(bundle)
    code, report, dump = run(bundle, tmp_path, ["--render"])
    assert code == 0
    text = "".join(p.read_text(encoding="utf-8") for p in (dump / "extracted").glob("*.txt"))
    assert "렌더 합성 본문" in text
    assert not report["extraction_failures"]
