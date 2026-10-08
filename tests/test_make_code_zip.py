import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_code_zip", ROOT / "scripts/make_code_zip.py")
mcz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcz)


def good_names():
    return list(mcz.REQUIRED) + ["src/models/detect.py", "docs/CLAIMS.md"]


def test_clean_list_passes():
    assert mcz.check_names(good_names()) == []


def test_forbidden_path_env_and_parquet_are_caught():
    assert any("금지 경로" in f for f in mcz.check_names(good_names() + ["data/manual/x.csv"]))
    assert any("금지 파일" in f for f in mcz.check_names(good_names() + [".env"]))
    assert any("금지 파일" in f for f in mcz.check_names(good_names() + ["src/x.parquet"]))
    assert any("금지 파일" in f for f in mcz.check_names(good_names() + ["app/.env.local"]))


def test_env_example_is_allowed():
    assert ".env.example" in mcz.REQUIRED
    assert mcz.check_names(good_names()) == []


def test_missing_required_file_fails():
    names = [n for n in good_names() if n != "CLAUDE.md"]
    failures = mcz.check_names(names)
    assert failures == ["필수 파일 없음: CLAUDE.md"]


def test_gitattributes_export_ignore_matches_forbidden_prefixes():
    prefixes = set()
    for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "export-ignore":
            assert parts[0].endswith("/**"), parts[0]
            prefixes.add(parts[0][:-2])
    assert prefixes == set(mcz.FORBIDDEN_PREFIXES)


def test_out_inside_repo_is_rejected():
    assert mcz.main(["--ref", "HEAD", "--out", str(ROOT / "dash_code_test.zip")]) == 2
    assert not (ROOT / "dash_code_test.zip").exists()


def test_policy_source_files_are_required():
    # 지원사업 원천(#72)은 제출 zip에 반드시 들어간다
    for name in ("data/policies/20261003/policies.json", "data/policies/20261003/policies_apply.csv"):
        assert name in mcz.REQUIRED
        assert mcz.check_names([n for n in good_names() if n != name]) == [f"필수 파일 없음: {name}"]
