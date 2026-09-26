"""W2-5 정적 JSON 내보내기 — SQLite 정본 → 서버 없는 프론트가 fetch하는 파생 번들.

SQLite가 정본이고 이 번들은 파생본이다. 모델 추론·진단 계산은 하지 않으며, 검색 인덱스·동 요약은
`search_index.build_index`·`dong_summary.build_summary`를 그대로 쓴다.

두 가지를 구분한다 (DECISIONS 2026-09-26 W2-5 정적 내보내기):
- **A. 기술적 계약 통과** (`technical_gate`): 정본 `release_blockers`가 비어 있음(최종 0.2 통과, 배포용 빌드, 인허가 기준일 대조),
  모든 리포트 0.2 검증, 검색·집계 불변식. A를 통과하지 못하면 번들을 만들지 않는다.
- **B. 공개 승인** (`publication_approved`): 실명·주소·store_id·개별 위험도 결합 데이터의 공개 범위는 정해지지 않았다.
  이 모듈에는 승인 수단이 없고 값은 항상 false다. A 통과는 B가 아니다.

출력 위치: 기본은 로컬 비공개 `outputs/serving/static_private/`. git 작업 트리(이 저장소·프론트 저장소 등) 안이면
저장소 기준 경로에 `docs`·`app`·`public`·`dist`·`site`·`www`가 있으면 거부하고 git 무시 경로만 허용한다
(이 저장소에서는 `outputs/` 아래만). 예외는 모든 점포가 합성 샘플(SAMPLE-NNN, '(샘플)' 상호)인
번들을 `docs/samples/` 아래에 쓰는 경우뿐이다 (`allow_tracked_synthetic=True`).

원자성: 같은 폴더의 임시 디렉터리에 쓰고 되읽어 검증한 뒤 교체한다. 실패하면 기존 번들은 그대로다.

실행:
    python -m src.serving.export_static --db outputs/serving/report.sqlite --min-cell-n <K> \\
        [--min-cell-n-status provisional] [--out outputs/serving/static_private]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from src.data import config
from src.serving import build_db as bd
from src.serving import dong_summary as ds
from src.serving import report_validation as rv
from src.serving import search_index as si

EXPORT_VERSION = "w2-5-export-0.1"
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "serving" / "static_private"
PUBLIC_DIR_NAMES = {"docs", "app", "public", "dist", "site", "www"}
SAMPLE_ID_RE = re.compile(r"^SAMPLE-\d{3}$")
SAMPLE_NAME_PREFIX = "(샘플)"
SYNTHETIC_MODEL = "sample_synthetic"  # synthetic_samples가 만든 합성 입력의 모형 이름
REPORT_PATH_TEMPLATE = "reports/{store_id}.json"
FILES = {"search_index": "search_index.json", "dongs": "dongs.json", "dong_summary": "dong_summary.json"}
PUBLICATION_NOTE = ("공개 승인 아님: 실명·주소·store_id와 개별 위험도가 결합된 데이터의 공개 범위는 아직 정해지지 않았다. "
                    "technical_gate 통과는 기술 검증일 뿐 공개 승인이 아니다.")
SYNTHETIC_NOTE = ("합성 샘플: 점포·상호·주소·위험도·요인 기여·정책은 모두 지어낸 값이며 실제 점포·실제 추정 결과·실제 통계가 아니다. "
                  "공개 승인 대상 데이터가 아니다.")


class ExportError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 데이터 종류·출력 경로
def data_kind(conn: sqlite3.Connection) -> str:
    """전부 합성일 때만 synthetic_sample: store_id가 SAMPLE-NNN, 상호가 '(샘플)'로 시작, 모형 이름이 합성 생성기의 것.
    가린 실제 모형 출력(PR #36 serve 샘플처럼 SAMPLE-NNN·'(샘플)'로 가렸지만 위험도·기여는 실제 추정치)은 real이다."""
    rows = conn.execute("SELECT s.store_id, s.name, r.model FROM stores s JOIN risk r USING (store_id)").fetchall()
    synthetic = bool(rows) and all(SAMPLE_ID_RE.match(sid) and name and name.startswith(SAMPLE_NAME_PREFIX)
                                   and model == SYNTHETIC_MODEL for sid, name, model in rows)
    return "synthetic_sample" if synthetic else "real"


def _git_root(path: Path) -> Path | None:
    """path가 속한 git 작업 트리의 루트 (이 저장소든 프론트 저장소든). 없으면 None."""
    anc = path
    while not anc.exists():
        anc = anc.parent
    r = subprocess.run(["git", "-C", str(anc), "rev-parse", "--show-toplevel"], capture_output=True,
                       encoding="utf-8", errors="strict")
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def guard_export_path(out: Path, kind: str, allow_tracked_synthetic: bool = False) -> None:
    """git 작업 트리 안이면: 공개·문서 디렉터리 이름(docs·app·public·dist·site·www)이 경로에 있으면 거부, 이 저장소에서는
    outputs/ 아래만, 그리고 git 무시 경로만 허용. 합성 샘플만 docs/samples/ 아래 예외."""
    out = Path(out).resolve()
    repo = config.REPO_ROOT.resolve()
    samples_root = repo / "docs" / "samples"
    if kind == "synthetic_sample" and allow_tracked_synthetic and out.is_relative_to(samples_root) \
            and out != samples_root:
        return
    root = _git_root(out)
    if root is None:
        return  # git 작업 트리 밖 (로컬 비공개 경로)
    rel = out.relative_to(root)
    public = PUBLIC_DIR_NAMES & {p.lower() for p in rel.parts}
    if public:
        raise ExportError(f"공개·추적 디렉터리({sorted(public)})에는 정적 번들을 쓰지 않는다: {out}")
    if root == repo and not out.is_relative_to(repo / "outputs"):
        raise ExportError(f"저장소 안에서는 outputs/ 아래에만 쓴다: {out}")
    probe = (rel / "manifest.json").as_posix()
    r = subprocess.run(["git", "check-ignore", "-q", probe], cwd=root, capture_output=True)
    if r.returncode != 0:
        raise ExportError(f"git 무시 대상이 아닌 경로 — 실제 점포 데이터가 커밋될 수 있다: {root} / {probe}")


# ---------------------------------------------------------------------------
# 공개용 동 요약 차단 사유 (B와 별개로, A 통과 후에도 동 요약만의 조건)
def homogeneous_cells(rows: list[dict]) -> list[tuple]:
    """공개된 행 중 한 등급이 100%인 칸 — 그 칸 모든 점포의 등급이 드러난다(속성 노출)."""
    return [(r["gu"], r["dong"], r["biz_type"]) for r in rows
            if not r["suppressed"] and max(r["band_share"].values()) >= 1.0]


def dong_public_blockers(summary: dict) -> list[str]:
    out = []
    if summary["min_cell_n_status"] != "decided":
        out.append(f"소표본 하한값 미확정 (min_cell_n={summary['min_cell_n']}, {summary['min_cell_n_status']})")
    leak = ds.recoverable_cells(summary["rows"])
    if leak:
        out.append(f"역산 가능한 숨김 칸 {len(leak)}개")
    homo = homogeneous_cells(summary["rows"])
    if homo:
        out.append(f"등급 쏠림(한 등급 100%) 공개 칸 {len(homo)}개")
    return out


# ---------------------------------------------------------------------------
def _dump(obj, indent) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _head(run: dict) -> dict:
    return {"_schema_version": rv.SCHEMA_VERSION, "run_id": run["run_id"], "score_origin": run["score_origin"],
            "as_of": run["as_of"]}


def render(conn: sqlite3.Connection, *, min_cell_n: int, min_cell_n_status: str, indent=None) -> dict[str, bytes]:
    """정본 → {상대 경로: 파일 바이트} (manifest 포함). 기술적 계약을 통과하지 못하면 ExportError."""
    run = bd.read_run(conn)
    blockers = bd.release_blockers(run)
    if blockers:
        raise ExportError("기술적 계약 미통과 — 정적 번들을 만들지 않는다: " + " / ".join(blockers))
    kind = data_kind(conn)
    head = _head(run)

    index = si.build_index(conn, purpose="release")
    summary = ds.build_summary(conn, min_cell_n=min_cell_n, min_cell_n_status=min_cell_n_status,
                               purpose="release" if min_cell_n_status == "decided" else "dev")
    dong_blockers = dong_public_blockers(summary)
    reports = list(bd.iter_reports(conn))

    files: dict[str, bytes] = {
        FILES["search_index"]: _dump(index, indent),
        FILES["dongs"]: _dump({**head, "dongs": summary["dongs"]}, indent),
        FILES["dong_summary"]: _dump(summary, indent),
    }
    rows = {FILES["search_index"]: index["n_entries"], FILES["dongs"]: len(summary["dongs"]),
            FILES["dong_summary"]: len(summary["rows"])}
    for rec in reports:
        path = REPORT_PATH_TEMPLATE.format(store_id=rec["store_id"])
        if path in files:
            raise ExportError(f"상세 리포트 중복: {rec['store_id']}")
        files[path] = _dump(rec, indent)
        rows[path] = 1

    gate = {"technical_gate": {"passed": True, "blockers": []},
            "dong_summary_public_ready": not dong_blockers, "dong_summary_blockers": dong_blockers,
            "publication_approved": False,
            "publication_note": SYNTHETIC_NOTE if kind == "synthetic_sample" else PUBLICATION_NOTE}
    band_cutoffs = json.loads(run["band_cutoffs_json"]) if run["band_cutoffs_json"] else None
    meta = {**head, "data_kind": kind, "n_stores": run["n_stores"],
            "band_cutoffs": {k: band_cutoffs[k] for k in ("mid", "high")} if band_cutoffs else None,
            "policy_matching": run["policy_matching"], "report_path_template": REPORT_PATH_TEMPLATE,
            "files": dict(FILES), **gate}
    files["meta.json"] = _dump(meta, indent)
    rows["meta.json"] = 1

    manifest = {**head, "export_version": EXPORT_VERSION, "data_kind": kind, "n_stores": run["n_stores"], **gate,
                "files": [{"path": p, "sha256": _sha(files[p]), "bytes": len(files[p]), "rows": rows[p],
                           "schema_version": rv.SCHEMA_VERSION, "run_id": run["run_id"]} for p in sorted(files)]}
    errs = rv.validate_def("static_meta", meta) + rv.validate_def("static_manifest", manifest)
    if errs:
        raise ExportError("meta/manifest 스키마 위반:\n  - " + "\n  - ".join(errs[:10]))
    files["manifest.json"] = _dump(manifest, indent)
    return files


# ---------------------------------------------------------------------------
# 번들 검증 (디스크에서 되읽어 확인 — 교체 전 임시 디렉터리, 또는 기존 번들 점검)
def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_bundle(bundle: Path, run: dict) -> list[str]:
    bundle = Path(bundle)
    errs: list[str] = []
    try:
        manifest = _load(bundle / "manifest.json")
        meta = _load(bundle / "meta.json")
        index = _load(bundle / FILES["search_index"])
        dongs = _load(bundle / FILES["dongs"])
        summary = _load(bundle / FILES["dong_summary"])
    except (OSError, json.JSONDecodeError) as e:
        return [f"번들 필수 파일을 읽을 수 없다: {e}"]

    errs += [f"manifest: {e}" for e in rv.validate_def("static_manifest", manifest)]
    errs += [f"meta: {e}" for e in rv.validate_def("static_meta", meta)]
    errs += [f"search_index: {e}" for e in rv.validate_def("search_index_file", index)]
    errs += [f"dongs: {e}" for e in rv.validate_def("dong_list_file", dongs)]
    errs += [f"dong_summary: {e}" for e in rv.validate_def("dong_summary_file", summary)]
    head = _head(run)
    for name, obj in (("manifest", manifest), ("meta", meta), ("search_index", index), ("dongs", dongs),
                      ("dong_summary", summary)):
        diff = {k: obj.get(k) for k in head if obj.get(k) != head[k]}
        if diff:
            errs.append(f"{name}: 실행 식별자 불일치 {diff} (정본 {head})")
    if manifest.get("n_stores") != run["n_stores"] or meta.get("n_stores") != run["n_stores"]:
        errs.append("n_stores가 정본 점포 수와 다르다")

    # manifest ↔ 디스크 (파일마다 한 번만 읽는다)
    listed = {f["path"]: f for f in manifest.get("files", [])}
    on_disk = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()} - {"manifest.json"}
    if set(listed) != on_disk:
        errs.append(f"manifest 목록과 디스크 파일 불일치: 목록에만 {sorted(set(listed) - on_disk)[:5]}, "
                    f"디스크에만 {sorted(on_disk - set(listed))[:5]}")
    data = {p: (bundle / p).read_bytes() for p in sorted(on_disk)}
    for p, f in listed.items():
        if p in data and _sha(data[p]) != f["sha256"]:
            errs.append(f"sha256 불일치: {p}")
        if f.get("run_id") != run["run_id"]:
            errs.append(f"manifest 파일 항목 run_id 불일치: {p}")

    # 상세 리포트: 파일명 = store_id, 0.2 검증, 기준 시점, 검색 인덱스와 같은 집합
    report_ids = []
    for p in (p for p in data if p.startswith("reports/")):
        fp = Path(p)
        try:
            rec = json.loads(data[p])
        except json.JSONDecodeError as e:
            errs.append(f"{p}: JSON 오류 {e}")
            continue
        sid = rec.get("store_id")
        if fp.stem != sid:
            errs.append(f"리포트 파일명 {fp.name} ≠ store_id {sid}")
        report_ids.append(sid)
        if (rec.get("score_origin"), rec.get("as_of")) != (run["score_origin"], run["as_of"]):
            errs.append(f"{sid}: score_origin/as_of 불일치")
        errs += [f"{sid}: {e}" for e in rv.validate_report(rec)[:3]]
    if len(report_ids) != len(set(report_ids)):
        errs.append("상세 리포트 store_id 중복")
    index_ids = [e["store_id"] for e in index.get("entries", [])]
    if set(report_ids) != set(index_ids):
        errs.append(f"검색 인덱스와 상세 리포트 store_id 불일치: 인덱스에만 {len(set(index_ids) - set(report_ids))}, "
                    f"리포트에만 {len(set(report_ids) - set(index_ids))}")
    if not len(report_ids) == len(index_ids) == run["n_stores"]:
        errs.append(f"건수 불일치: 리포트 {len(report_ids)} / 인덱스 {len(index_ids)} / 정본 {run['n_stores']}")
    if ds.recoverable_cells(summary.get("rows", [])):
        errs.append("동 요약에 역산 가능한 숨김 칸")
    return errs


# ---------------------------------------------------------------------------
def _swap(tmp: Path, out: Path) -> None:
    if not out.exists():
        os.replace(tmp, out)
        return
    old = out.with_name(f".{out.name}.old-{os.getpid()}")
    if old.exists():
        shutil.rmtree(old)
    os.replace(out, old)
    try:
        os.replace(tmp, out)
    except BaseException:
        os.replace(old, out)
        raise
    shutil.rmtree(old)


def export(db_path: Path, out_dir: Path = DEFAULT_OUT, *, min_cell_n: int, min_cell_n_status: str = "provisional",
           allow_tracked_synthetic: bool = False, indent=None) -> dict:
    """정본 → 정적 번들. manifest(dict)를 돌려준다. 실패하면 ExportError이며 기존 번들은 바뀌지 않는다."""
    db_path, out = Path(db_path), Path(out_dir)
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        run = bd.read_run(conn)
        kind = data_kind(conn)
        guard_export_path(out, kind, allow_tracked_synthetic)
        if out.exists() and not (out / "manifest.json").is_file():
            raise ExportError(f"기존 경로가 정적 번들이 아니라 덮어쓰지 않는다: {out}")
        files = render(conn, min_cell_n=min_cell_n, min_cell_n_status=min_cell_n_status, indent=indent)
    finally:
        conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        for rel, data in files.items():
            fp = tmp / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
        errs = verify_bundle(tmp, run)
        if errs:
            raise ExportError("정적 번들 검증 실패:\n  - " + "\n  - ".join(errs[:10]))
        _swap(tmp, out)
    except BaseException:
        if tmp.exists():
            shutil.rmtree(tmp)
        raise
    return json.loads(files["manifest.json"])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-5 정적 JSON 번들 (로컬 비공개)")
    ap.add_argument("--db", type=Path, default=bd.DEFAULT_OUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-cell-n", type=int, required=True, help="동 요약 소표본 하한값 (미확정 — 반드시 명시)")
    ap.add_argument("--min-cell-n-status", choices=("provisional", "decided"), default="provisional")
    a = ap.parse_args(argv)
    m = export(a.db, a.out, min_cell_n=a.min_cell_n, min_cell_n_status=a.min_cell_n_status)
    total = sum(f["bytes"] for f in m["files"])
    print(f"정적 번들 → {a.out}  (run_id {m['run_id']}, 점포 {m['n_stores']:,}, 파일 {len(m['files']):,}, "
          f"{total / 1e6:.1f} MB)")
    print(f"  기술적 계약: 통과 · 동 요약 공개 가능: {m['dong_summary_public_ready']} "
          f"{m['dong_summary_blockers'] or ''}")
    print(f"  공개 승인: {m['publication_approved']} — {m['publication_note']}")


if __name__ == "__main__":
    main()
