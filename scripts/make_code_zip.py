"""제출용 코드 zip을 만들고 들어가면 안 되는 파일이 없는지 바로 검사한다 (표준 라이브러리만 사용).

    python scripts/make_code_zip.py --ref <태그 또는 커밋> --out <저장소 밖 경로>/dash_code.zip

`git archive`로 만들므로 커밋된 내용만 들어가고 `.gitattributes`의 `export-ignore` 제외 범위가 적용된다.
검사 항목: 금지 경로·금지 파일, 필수 파일, 풀린 zip 안의 check_claims 실행.
하나라도 실패하면 종료 코드 1로 끝난다. `--out`이 저장소 안이면 종료 코드 2로 거부한다.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ZIP_PREFIX = "dash-code/"

# .gitattributes의 export-ignore 경로와 같아야 한다 (tests/test_make_code_zip.py가 대조한다).
FORBIDDEN_PREFIXES = (
    "data/manual/",
    "outputs/",
    "notebooks/",
    "docs/drafts/",
    "docs/samples/serve_2025Q2_trial/",
    "docs/samples/w2-6_dummy/",
)
FORBIDDEN_SUFFIXES = (".sqlite", ".db", ".parquet", ".pyc")
REQUIRED = (
    "README.md",
    "CLAUDE.md",
    "requirements.txt",
    ".env.example",
    "scripts/check_claims.py",
    "configs/claims_rules.json",
    "app/package.json",
    "app/package-lock.json",
    "docs/samples/submission_bundle/meta.json",
    "docs/samples/submission_bundle/manifest.json",
    "src/serving/policy_apply.py",
    "data/policies/20261003/policies.json",
    "data/policies/20261003/policies_apply.csv",
)
CLAIMS_TARGETS = ("README.md", "docs/samples/submission_bundle", "app/src")


def _is_forbidden_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    lower = name.lower()
    return lower.endswith(FORBIDDEN_SUFFIXES) or ".sqlite-" in lower


def check_names(names, required=REQUIRED) -> list[str]:
    """zip 안 파일 경로(접두사 제외) 목록의 실패 사유 목록. 비어 있으면 통과."""
    names = [n for n in names if n and not n.endswith("/")]
    failures = []
    for prefix in FORBIDDEN_PREFIXES:
        hits = [n for n in names if n.startswith(prefix)]
        if hits:
            failures.append(f"금지 경로 {prefix} 파일 {len(hits)}개 (예: {hits[0]})")
    bad = [n for n in names if _is_forbidden_file(n)]
    if bad:
        failures.append(f"금지 파일 {len(bad)}개 (예: {bad[0]})")
    present = set(names)
    for req in required:
        if req not in present:
            failures.append(f"필수 파일 없음: {req}")
    return failures


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", required=True, help="태그 또는 커밋")
    parser.add_argument("--out", required=True, type=Path, help="저장소 밖 zip 경로")
    parser.add_argument("--keep-extracted", action="store_true", help="검사용으로 푼 폴더를 지우지 않는다")
    args = parser.parse_args(argv)

    if _inside_repo(args.out):
        print("오류: --out이 저장소 안입니다. 저장소 밖 경로를 지정하세요.", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)

    print("알림: git archive는 커밋된 내용만 담습니다. 커밋하지 않은 변경은 zip에 반영되지 않습니다.")
    done = subprocess.run(
        ["git", "archive", "--format=zip", f"--prefix={ZIP_PREFIX}", "-o", str(args.out), args.ref],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if done.returncode != 0:
        print("오류: git archive 실패\n" + done.stderr.strip(), file=sys.stderr)
        return 2

    with zipfile.ZipFile(args.out) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        names = [i.filename[len(ZIP_PREFIX):] if i.filename.startswith(ZIP_PREFIX) else i.filename for i in infos]
        total = sum(i.file_size for i in infos)
        digest = hashlib.sha256(args.out.read_bytes()).hexdigest()

        results = []  # (항목, 통과, 설명)
        name_failures = check_names(names)
        forbidden = [f for f in name_failures if f.startswith(("금지 경로", "금지 파일"))]
        missing = [f for f in name_failures if f.startswith("필수 파일")]
        results.append(("금지 경로·금지 파일 0건", not forbidden, "; ".join(forbidden)))
        results.append(("필수 파일 존재", not missing, "; ".join(missing)))

        tmp = Path(tempfile.mkdtemp(prefix="dash_zip_check_"))
        try:
            zf.extractall(tmp)
            root = tmp / ZIP_PREFIX.rstrip("/")
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            if (root / "scripts" / "check_claims.py").is_file():
                run = subprocess.run(
                    [sys.executable, "scripts/check_claims.py", *CLAIMS_TARGETS, "--fail-on", "warn"],
                    cwd=root, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace",
                )
                results.append(("풀린 zip의 check_claims (README·제출 번들·app/src) 종료 코드 0", run.returncode == 0,
                                f"종료 코드 {run.returncode}" if run.returncode else ""))
            else:
                results.append(("풀린 zip의 check_claims", False, "scripts/check_claims.py 없음"))
        finally:
            if args.keep_extracted:
                print(f"풀린 폴더: {tmp / ZIP_PREFIX.rstrip('/')}")
            else:
                shutil.rmtree(tmp, ignore_errors=True)

    print(f"zip: {args.out.name}  파일 {len(names)}개, 압축 전 {total:,} bytes")
    print(f"sha256: {digest}")
    top = Counter(n.split("/", 1)[0] if "/" in n else "(루트)" for n in names)
    for key, count in sorted(top.items()):
        print(f"  {key}: {count}개")
    ok = True
    for label, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        ok &= passed
    if not ok:
        print("이 zip은 제출하지 마세요.")
        return 1
    print("모든 검사 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
