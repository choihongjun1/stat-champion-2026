"""제출용 비식별 사례 번들 — SQLite 정본 → `submission-static-0.1` (H1/H8, Issue #49 A3·T3·T5).

`export_static`(전체 점포 static_private, public-static-0.1)과 **별개**다. 그 번들·스키마·SQLite는 읽기만 한다.

대상 범위 (R5): 비식별 실제 사례 A/B/C 상세 3건까지와 공통 meta·manifest. 전체 리포트 28,711건, 검색 인덱스,
동 요약(소표본 하한 미확정·R5)은 넣지 않는다.

사례 지정은 저장소 밖의 비공개 JSON(`--cases`)으로만 받는다(store_id가 들어 있다 — H3 `demo_selection_private.json`):
    {"cases": [{"case_label": "A", "store_id": "...", "rule_stage": "base", "n_candidates": 12, "seed": 20261004},
               {"case_label": "B", "store_id": null, "rule_stage": "none", "n_candidates": 0, "seed": 20261005}]}
사례를 아직 고르지 않았으면 `--cases`를 생략한다 → 세 칸 모두 `pending_selection`인 meta만 만든다(사례 0건).

게이트 (하나라도 걸리면 번들을 만들지 않는다, 기존 번들은 그대로):
1. 정본 `release_blockers` 없음(export_static과 같은 기술적 계약)
2. 사례마다 내부 report 0.3 검증 → 제출 계약(`submission_report.validate_case`) 검증
3. 금지 키 재귀 검사, 정본의 **모든** store_id와 선택 점포의 상호·주소·법정동이 번들 바이트에 없음
4. #56 `scripts/check_claims.py` 규칙 전체를 `--fail-on warn`과 같은 기준으로 적용 — 발견 0건
technical_gate 통과는 공개 승인이 아니다(`publication_approved=false`, 승인은 팀 결정).

실행:
    python -m src.serving.export_submission --db outputs/serving/report.sqlite \\
        [--cases <비공개 cases.json>] [--out outputs/serving/submission_public] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from src.data import config
from src.serving import build_db as bd
from src.serving import export_static as ex
from src.serving import submission_report as sr

DEFAULT_OUT = config.REPO_ROOT / "outputs" / "serving" / "submission_public"
CLAIMS_SCRIPT = config.REPO_ROOT / "scripts" / "check_claims.py"
CLAIMS_RULES = config.REPO_ROOT / "configs" / "claims_rules.json"
CLAIMS_ALLOWLIST = config.REPO_ROOT / "configs" / "claims_allowlist.json"
SCOPE = ("제출용 비식별 사례 번들: 비식별 실제 사례 A/B/C(T5) 상세와 공통 meta만 담는다. "
         "전체 점포 리포트·검색 인덱스·동 요약은 제출 범위가 아니다(R5).")
EXCLUDED = ["전체 점포 상세 리포트(static_private)", "검색 인덱스", "동 요약(R5·소표본 하한 미확정)",
            "상호·주소·법정동·store_id·인허가일·영업 상태", "개인 확률·예측구간·동종 중앙값(T3)",
            "요인 기여값·원 feature 값·근거 문구", "처방·온라인 존재감"]
PUBLICATION_NOTE = ("공개 승인 아님: 제출 계약(technical_gate) 통과는 기술 검증일 뿐이다. "
                    "제출 여부는 팀이 결정한다.")
GATE_CHECKS = ["release_blockers 없음", "내부 report 0.3 검증", "submission-static-0.1 검증", "금지 키 0",
               "store_id·상호·주소 바이트 검사", "check_claims(--fail-on warn 기준) 발견 0"]


class SubmissionError(RuntimeError):
    pass


def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha12(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


# ---------------------------------------------------------------------------
def load_cases(path: Path | None) -> list[dict]:
    """비공개 사례 지정 → 정규화한 3칸(A·B·C 순서). 파일이 없으면 세 칸 모두 pending_selection."""
    if path is None:
        return [{"case_label": lab, "store_id": None, "status": "pending_selection", "rule_stage": "none",
                 "n_candidates": None, "seed": None} for lab in sr.CASE_LABELS]
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("cases") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise SubmissionError("cases 파일은 {\"cases\": [...]} 형식이어야 한다")
    by_label = {}
    for it in items:
        lab, stage, sid = it.get("case_label"), it.get("rule_stage"), it.get("store_id")
        if lab not in sr.CASE_LABELS or lab in by_label:
            raise SubmissionError("case_label은 A·B·C 중 하나이고 한 번씩만 나와야 한다")
        if stage not in sr.RULE_STAGES:
            raise SubmissionError(f"rule_stage는 {sr.RULE_STAGES} 중 하나")
        if (stage == "none") != (sid is None):
            raise SubmissionError("rule_stage=none일 때만 store_id가 없다")
        by_label[lab] = {"case_label": lab, "store_id": sid,
                         "status": "no_suitable_case" if stage == "none" else "selected", "rule_stage": stage,
                         "n_candidates": it.get("n_candidates"), "seed": it.get("seed")}
    chosen = [c["store_id"] for c in by_label.values() if c["store_id"] is not None]
    if len(chosen) != len(set(chosen)):
        raise SubmissionError("A·B·C는 서로 다른 점포여야 한다")
    return [by_label.get(lab) or {"case_label": lab, "store_id": None, "status": "pending_selection",
                                  "rule_stage": "none", "n_candidates": None, "seed": None}
            for lab in sr.CASE_LABELS]


def _claims_module():
    spec = importlib.util.spec_from_file_location("_check_claims_for_submission", CLAIMS_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def claims_findings(bundle: Path) -> dict:
    """#56 규칙 전체(ID-*·CL-*)를 번들 폴더에 적용한다. → check_claims scan 결과(dict)."""
    mod = _claims_module()
    rules, allowlist = mod.load_config(str(CLAIMS_RULES), str(CLAIMS_ALLOWLIST))
    return mod.scan([str(bundle)], rules, allowlist, base=Path(bundle))


# ---------------------------------------------------------------------------
def render(conn: sqlite3.Connection, cases: list[dict], db_sha12: str) -> tuple[dict[str, bytes], list[str]]:
    """→ ({상대 경로: 바이트}, 바이트에 나오면 안 되는 문자열 목록). 계약 위반이면 SubmissionError."""
    run = bd.read_run(conn)
    blockers = bd.release_blockers(run)
    if blockers:
        raise SubmissionError("기술적 계약 미통과 — 제출 번들을 만들지 않는다: " + " / ".join(blockers))
    kind = ex.data_kind(conn)
    known = {sid for (sid,) in conn.execute("SELECT store_id FROM stores")}
    secrets = set(known)
    files: dict[str, bytes] = {}
    slots = []
    for c in cases:
        slot = {"case_label": c["case_label"], "public_id": sr.public_id(c["case_label"]),
                "selection_status": c["status"], "rule_stage": c["rule_stage"], "n_candidates": c["n_candidates"], "seed": c["seed"], "path": None}
        if c["status"] == "selected":
            if c["store_id"] not in known:
                raise SubmissionError(f"사례 {c['case_label']}: 정본에 없는 점포")
            rec = bd.assemble_report(conn, c["store_id"], run)
            st = rec["store"]
            # 공개 허용 값(구·업종·동종 집단 이름) 안에 들어가는 짧은 문자열은 식별 문자열로 보지 않는다
            allowed = (st["gu"], st["biz_type"], rec["risk"]["peer_group"])
            secrets.update(v for v in (st.get("name"), st.get("address_road"), st.get("address_jibun"),
                                       st.get("dong")) if isinstance(v, str) and len(v) >= 2
                           and not any(v in a for a in allowed))
            try:
                case = sr.project_case(rec, label=c["case_label"], data_kind=kind, rule_stage=c["rule_stage"],
                                       n_candidates=c["n_candidates"], seed=c["seed"])
            except ValueError as e:
                raise SubmissionError(f"사례 {c['case_label']}: {e}") from e
            slot["path"] = f"cases/{slot['public_id']}.json"
            files[slot["path"]] = _dump(case)
        slots.append(slot)

    source = json.loads(run["serve_meta_json"])
    bg = source.get("background", {}).get("backgrounds", {})
    meta = {"_submission_contract_version": sr.CONTRACT_VERSION, "source_schema_version": run["output_schema_version"],
            "run_id": run["run_id"], "score_origin": run["score_origin"], "as_of": run["as_of"], "data_kind": kind,
            "scope": SCOPE, "excluded": list(EXCLUDED), "cases": slots,
            "provenance": {"params_name": source.get("params_name"), "model_class": source.get("model_class"),
                           "params_contract": source.get("params_contract"),
                           "s8_rule_version": source.get("s8_rule", {}).get("rule_version"),
                           "primary_seed": bg.get("primary", {}).get("seed"),
                           "sensitivity_seed": bg.get("sensitivity", {}).get("seed"),
                           "source_db_sha256_12": db_sha12},
            "policy_matching": run["policy_matching"],
            "technical_gate": {"passed": True, "checks": list(GATE_CHECKS)},
            "publication_approved": False, "publication_note": PUBLICATION_NOTE}
    errs = sr.validate_def("submission_meta", meta)
    if errs:
        raise SubmissionError("meta 스키마 위반: " + " / ".join(errs[:5]))
    files["meta.json"] = _dump(meta)
    manifest = {"_submission_contract_version": sr.CONTRACT_VERSION, "run_id": run["run_id"],
                "score_origin": run["score_origin"], "as_of": run["as_of"],
                "files": [{"path": p, "sha256_12": _sha12(files[p]), "bytes": len(files[p])} for p in sorted(files)]}
    errs = sr.validate_def("submission_manifest", manifest)
    if errs:
        raise SubmissionError("manifest 스키마 위반: " + " / ".join(errs[:5]))
    files["manifest.json"] = _dump(manifest)
    return files, sorted(secrets)


def verify_bundle(bundle: Path, secrets: list[str] | None = None) -> list[str]:
    """디스크에서 되읽어 확인: 스키마·금지 키·manifest 해시·식별 문자열·check_claims."""
    bundle = Path(bundle)
    errs: list[str] = []
    try:
        meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"필수 파일을 읽을 수 없다: {e}"]
    errs += [f"meta: {e}" for e in sr.validate_def("submission_meta", meta)]
    errs += [f"manifest: {e}" for e in sr.validate_def("submission_manifest", manifest)]
    on_disk = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    listed = {f["path"]: f for f in manifest.get("files", [])}
    if set(listed) != on_disk - {"manifest.json"}:
        errs.append("manifest 목록과 디스크 파일 불일치")
    expected_cases = {s["path"] for s in meta.get("cases", []) if s.get("path")}
    if expected_cases != {p for p in on_disk if p.startswith("cases/")}:
        errs.append("meta.cases와 사례 파일 불일치")
    for p in sorted(on_disk):
        data = (bundle / p).read_bytes()
        if p in listed and _sha12(data) != listed[p]["sha256_12"]:
            errs.append(f"sha256 불일치: {p}")
        obj = json.loads(data)
        errs += [f"{p}{k}: 금지 키" for k in sr.forbidden_key_paths(obj)]
        if p.startswith("cases/"):
            errs += [f"{p}: {e}" for e in sr.validate_case(obj)[:5]]
            if obj.get("run_id") is not None:
                errs.append(f"{p}: 사례 파일에 run_id 금지")
        text = data.decode("utf-8")
        hits = sum(1 for s in (secrets or []) if s in text)
        if hits:
            errs.append(f"{p}: 정본 점포 식별 문자열 {hits}종 발견")
    found = claims_findings(bundle)
    if found["summary"]["scanned_files"] != len(on_disk):
        errs.append(f"check_claims 검사 파일 수 {found['summary']['scanned_files']} ≠ {len(on_disk)}")
    if found["findings"]:
        errs.append(f"check_claims 발견 {found['summary']['by_rule']}")
    return errs


# ---------------------------------------------------------------------------
def export(db_path: Path, out_dir: Path = DEFAULT_OUT, *, cases_path: Path | None = None,
           dry_run: bool = False) -> dict:
    """정본 → 제출 번들. → 요약 dict. 실패하면 SubmissionError이며 기존 번들은 바뀌지 않는다.
    dry_run=True면 임시 폴더에서 만들어 전부 검증한 뒤 지운다(아무것도 남기지 않는다)."""
    db_path, out = Path(db_path), Path(out_dir)
    cases = load_cases(cases_path)
    db_sha12 = hashlib.sha256(db_path.read_bytes()).hexdigest()[:12]
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        kind = ex.data_kind(conn)
        if not dry_run:
            try:
                ex.guard_export_path(out, kind)
            except ex.ExportError as e:
                raise SubmissionError(str(e)) from e
            if out.exists() and not (out / "manifest.json").is_file():
                raise SubmissionError(f"기존 경로가 제출 번들이 아니라 덮어쓰지 않는다: {out}")
        files, secrets = render(conn, cases, db_sha12)
    finally:
        conn.close()

    parent = Path(tempfile.mkdtemp(prefix="submission-dry-")) if dry_run else out.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".{out.name}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        for rel, data in files.items():
            fp = tmp / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
        errs = verify_bundle(tmp, secrets)
        if errs:
            raise SubmissionError("제출 번들 검증 실패:\n  - " + "\n  - ".join(errs[:10]))
        if not dry_run:
            ex._swap(tmp, out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
        if dry_run:
            shutil.rmtree(parent, ignore_errors=True)
    meta = json.loads(files["meta.json"])
    return {"out": None if dry_run else str(out), "dry_run": dry_run, "files": sorted(files),
            "cases": {s["case_label"]: s["selection_status"] for s in meta["cases"]}, "data_kind": meta["data_kind"],
            "contract": sr.CONTRACT_VERSION, "claims_findings": 0}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="제출용 비식별 사례 번들 (submission-static-0.1)")
    ap.add_argument("--db", type=Path, default=bd.DEFAULT_OUT)
    ap.add_argument("--cases", type=Path, default=None, help="비공개 사례 지정 JSON (store_id 포함, 저장소 밖)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true", help="임시 폴더에서 만들고 검증만 한 뒤 지운다")
    a = ap.parse_args(argv)
    s = export(a.db, a.out, cases_path=a.cases, dry_run=a.dry_run)
    where = "(dry-run, 기록 안 함)" if s["dry_run"] else f"→ {s['out']}"
    print(f"제출 번들 {s['contract']} {where}  data_kind={s['data_kind']}")
    print(f"  파일 {len(s['files'])}개: {', '.join(s['files'])}")
    print(f"  사례: {s['cases']}")
    print("  게이트: 금지 키 0 · 식별 문자열 0 · check_claims 발견 0 — 공개 승인 아님")


if __name__ == "__main__":
    main()
