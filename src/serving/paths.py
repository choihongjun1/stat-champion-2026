"""W2-5 출력 경로 방어 — 실명 점포·위험도가 결합된 산출물(SQLite 정본, 정적 번들)이 git에 커밋될 수 있는 곳에 생기지 않게 한다.

이 저장소뿐 아니라 **다른 git 작업 트리**(프론트 저장소 등)도 같은 규칙을 적용한다:
- git 작업 트리 밖(로컬 비공개 경로) → 허용
- git 작업 트리 안 → 그 저장소 기준 경로에 공개·문서 디렉터리 이름(`docs`·`app`·`public`·`dist`·`site`·`www`)이 있으면 거부,
  그리고 그 저장소의 `git check-ignore`로 무시되는 경로만 허용
"""
from __future__ import annotations

import subprocess
from pathlib import Path

PUBLIC_DIR_NAMES = frozenset({"docs", "app", "public", "dist", "site", "www"})


class UnsafeOutputPath(RuntimeError):
    pass


def git_root(path: Path) -> Path | None:
    """path(존재하지 않아도 됨)가 속한 git 작업 트리의 루트. 작업 트리 밖이면 None."""
    anc = Path(path).resolve()
    while not anc.exists():
        anc = anc.parent
    r = subprocess.run(["git", "-C", str(anc), "rev-parse", "--show-toplevel"], capture_output=True,
                       encoding="utf-8", errors="strict")
    out = (r.stdout or "").strip()
    return Path(out).resolve() if r.returncode == 0 and out else None


def check_private_output(out: Path, probe_name: str | None = None) -> Path | None:
    """out이 커밋될 수 없는 경로인지 확인한다. 위반이면 UnsafeOutputPath. → out이 속한 git 루트(없으면 None).

    probe_name: out이 디렉터리일 때 그 안의 파일 이름으로 무시 여부를 확인한다 (예: 'manifest.json')."""
    out = Path(out).resolve()
    root = git_root(out)
    if root is None:
        return None
    rel = out.relative_to(root)
    public = PUBLIC_DIR_NAMES & {p.lower() for p in rel.parts}
    if public:
        raise UnsafeOutputPath(f"공개·문서 디렉터리({sorted(public)})에는 쓰지 않는다: {root} / {rel.as_posix()}")
    probe = (rel / probe_name).as_posix() if probe_name else rel.as_posix()
    r = subprocess.run(["git", "check-ignore", "-q", probe], cwd=root, capture_output=True)
    if r.returncode != 0:
        raise UnsafeOutputPath(f"git 무시 대상이 아닌 경로 — 실제 점포 데이터가 커밋될 수 있다: {root} / {probe}")
    return root
