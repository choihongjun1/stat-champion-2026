"""W3-16 제출 화면 최종 E2E — 제출용 비식별 사례 번들(submission-static-0.1)로 만든 #48 정적 export(app/out) 검사.

    python scripts/w3_16_submission_e2e.py --out app/out --dump-dir <out 밖 폴더> \
        --db outputs/serving/report_with_policies.sqlite \
        --cases outputs/w3_demo/private/demo_cases_for_export.json \
        --submission outputs/w3_submission/final [--checker scripts/check_claims.py] [--render]

검사 (어느 하나라도 실패·미검사면 exit 1, 사용 오류 2):
  A. 화면 텍스트 — --render면 Playwright(설치된 Chrome)로 /, /case/CASE-{A,B,C}/를 실제로 렌더링하고
     '나머지 요인 보기'를 펼친 뒤 innerText·접근성 속성·렌더 DOM·전체 화면 캡처(+텍스트 sidecar)를 dump-dir에 남긴다.
     렌더 텍스트에서 check_required 안내 수, 개인 확률·구간·점수 표현, 지원사업-위험요인 연결 표현을 확인한다.
  B. authoritative check_claims — 지정한 checker를 그대로 실행한다(규칙·allowlist를 바꾸지 않는다).
     대상: out/, 렌더 텍스트·DOM·캡처 sidecar, 제출 디렉터리. --fail-on warn.
  C. 식별자 역검색 — 정본 DB의 전체 store_id(원 관리번호 형태 포함)와 선택 사례의 상호·주소·법정동·인허가일을
     out/ 전 파일(HTML·JS·JSON·RSC·정적 자산 바이트), 렌더 텍스트·DOM, 캡처 PNG 텍스트 청크, 제출 디렉터리에서 찾는다.
  D. 번들 무결성 — out/submission과 제출 디렉터리 바이트 일치, manifest 재계산, submission_schema.json 검증, 금지 키, linked_factor_ids=[],
     private 파일이 out/에 없는지.

비공개 값(상호·주소·store_id)은 결과 파일·표준 출력에 쓰지 않는다 — 건수와 마스킹한 위치만 남긴다.
publication_approved는 읽기만 하고 바꾸지 않는다.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import zlib

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "src/serving/submission_schema.json"
ROUTES = ["/", "/case/CASE-A/", "/case/CASE-B/", "/case/CASE-C/"]
CHECK_REQUIRED_COPY = "저희가 알 수 없어요. 공고에서 한 번 확인해 주세요."
MATCHED_COPY = "사장님 가게 정보로 보면 신청 조건에 맞아요."
POLICY_HEAD = "찾은 지원사업이에요"
FACTOR_LABELS = ["영업 기간", "업종과 가게 크기", "가게가 있는 구", "주변을 오가는 사람", "동네 상권 흐름", "블로그 언급",
                 "주변 같은 업종 가게 수", "주변 같은 업종 매출", "임대료 수준"]
# 제출 계약에서 빠진 키 (REPORT_SCHEMA §15 '뺌'). 정책 카드의 name/operator는 공고 정보라 제외 목록에 없다.
FORBIDDEN_KEYS = {"store_id", "dong", "address_road", "address_jibun", "license_date", "status", "close_date",
                  "mdis_industry_code", "online_presence", "prescriptions", "probability_12m", "ci_low", "ci_high",
                  "interval_note", "peer_median", "model", "calibrated", "contribution", "peer_percentile", "values",
                  "driver", "explanation", "display_note", "unverifiable_conditions", "check_note", "name_norm"}
# 렌더 텍스트 보조 검사 (check_claims를 대신하지 않는다)
SCREEN_FORBIDDEN = {
    "individual_probability": re.compile(r"(폐업|생존)\s*(확률|가능성)|확률\s*\d|\d+(\.\d+)?\s*%\s*(의\s*)?(확률|가능성)"),
    "interval": re.compile(r"\d+(\.\d+)?\s*%?\s*[~～–-]\s*\d+(\.\d+)?\s*%|신뢰\s*구간|예측\s*구간|\bCI\b"),
    "score_threshold": re.compile(r"(?i)\b(logit|threshold|score|probability|proba)\b|위험\s*점수|기준값|컷오프|임계"),
    "cause_assertion": re.compile(r"폐업\s*원인|원인은|때문에\s*(폐업|위험)"),  # claims-allow: CL-15
    "policy_eligible_claim": re.compile(r"신청\s*가능|자격\s*조건이\s*맞는|추천\s*(사업|지원)|적합한\s*(사업|지원)"),
}
ALLOWED_PERCENT = re.compile(r"위험 상위 \d+%")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


@contextmanager
def serve(folder: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(folder), **k)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join()


def render(out: Path, dump: Path, channel: str, timeout_ms: int) -> list[dict]:
    from playwright.sync_api import sync_playwright

    pages = []
    (dump / "rendered").mkdir(parents=True, exist_ok=True)
    (dump / "dom").mkdir(exist_ok=True)
    (dump / "captures").mkdir(exist_ok=True)
    with serve(out) as base, sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel)
        try:
            for route in ROUTES:
                name = "home" if route == "/" else route.strip("/").split("/")[-1]
                page = browser.new_page(viewport={"width": 375, "height": 812})
                page.add_init_script("sessionStorage.setItem('dash_splash_seen', '1')")
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(type(e).__name__))
                rec = {"route": route, "name": name, "ok": False}
                try:
                    resp = page.goto(base + route, wait_until="networkidle", timeout=timeout_ms)
                    if resp is None or resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status if resp else 'none'}")
                    page.locator("main h1").first.wait_for(state="visible", timeout=timeout_ms)
                    more = page.get_by_role("button", name=re.compile("나머지 요인"))
                    if more.count():
                        more.first.click()
                    page.wait_for_timeout(300)
                    text = page.locator("body").inner_text()
                    attrs = page.evaluate("""() => Array.from(document.querySelectorAll('[alt],[title],[aria-label],[placeholder]'))
                        .flatMap(e => ['alt','title','aria-label','placeholder'].filter(k => e.hasAttribute(k)).map(k => e.getAttribute(k)))""")
                    dom = page.content()
                    (dump / "rendered" / f"{name}.txt").write_text(text + "\n" + "\n".join(attrs) + "\n", encoding="utf-8")
                    (dump / "dom" / f"{name}.html").write_text(dom, encoding="utf-8")
                    png = dump / "captures" / f"{name}.png"
                    page.screenshot(path=str(png), full_page=True)
                    png.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
                    rec.update(ok=not errors, text=text, attrs=attrs, page_errors=errors)
                except Exception as exc:  # 미검사는 통과로 보지 않는다
                    rec["error"] = type(exc).__name__ + ": " + str(exc)[:200]
                finally:
                    page.close()
                pages.append(rec)
        finally:
            browser.close()
    return pages


def screen_checks(pages: list[dict], cases: dict[str, dict]) -> dict:
    res = {"pages": len(pages), "pages_ok": sum(p["ok"] for p in pages), "per_case": {}, "forbidden_hits": {}, "failures": []}
    for p in pages:
        if not p["ok"]:
            res["failures"].append(f"{p['route']}: 렌더 실패 {p.get('error') or p.get('page_errors')}")
            continue
        text = p["text"]
        for rule, rx in SCREEN_FORBIDDEN.items():
            n = len(rx.findall(text))
            res["forbidden_hits"][rule] = res["forbidden_hits"].get(rule, 0) + n
        stray = [m for m in re.findall(r"\d+(?:\.\d+)?\s*%", text)]
        allowed = ALLOWED_PERCENT.findall(text)
        res["forbidden_hits"]["percent_other_than_peer_rank"] = res["forbidden_hits"].get("percent_other_than_peer_rank", 0) + len(stray) - len(allowed)
        if p["name"].startswith("CASE-"):
            c = cases[p["name"]]
            n_check = sum(x["match_status"] == "check_required" for x in c["policies"])
            n_match = sum(x["match_status"] == "matched" for x in c["policies"])
            # 지원사업 영역 = 제목부터 하단 기준일 문단 전까지 (하단은 데이터 출처로 '블로그 언급 수'를 말한다)
            policy_part = text[text.index(POLICY_HEAD):] if POLICY_HEAD in text else ""
            policy_part = re.split(r"\d{4}년 \d+월 \d+일 기준이에요", policy_part)[0]
            per = {
                "policies": len(c["policies"]),
                "check_required_in_bundle": n_check,
                "check_required_notice_rendered": text.count(CHECK_REQUIRED_COPY),
                "check_required_counts_rendered": sorted(int(m) for m in re.findall(r"조건 (\d+)개는 " + re.escape(CHECK_REQUIRED_COPY), text)),
                "check_required_counts_bundle": sorted(x["unverified_condition_count"] for x in c["policies"] if x["match_status"] == "check_required"),
                "matched_in_bundle": n_match,
                "matched_notice_rendered": text.count(MATCHED_COPY),
                "policy_names_rendered": sum(x["name"] in policy_part for x in c["policies"]),
                "factor_label_in_policy_section": sum(policy_part.count(l) for l in FACTOR_LABELS),
                "link_markers_in_policy_section": len(re.findall(r"→|때문에|해결|대응하는 (사업|지원)|관련 지원", policy_part)),  # claims-allow: CL-17
                "title_rendered": c["case_title"] in text,
                "band_rendered": {"low": "낮음이에요", "mid": "주의이에요", "high": "높음이에요"}[c["risk"]["band"]] in text.replace("\n", ""),
            }
            ok = (per["check_required_notice_rendered"] == n_check and per["check_required_counts_rendered"] == per["check_required_counts_bundle"]
                  and per["matched_notice_rendered"] == n_match and per["policy_names_rendered"] == len(c["policies"])
                  and per["factor_label_in_policy_section"] == 0 and per["link_markers_in_policy_section"] == 0
                  and per["title_rendered"] and per["band_rendered"])
            per["ok"] = ok
            if not ok:
                res["failures"].append(f"{p['name']}: 화면-번들 대응 불일치")
            res["per_case"][p["name"]] = per
    if any(v for v in res["forbidden_hits"].values()):
        res["failures"].append(f"금지 표현: {res['forbidden_hits']}")
    res["ok"] = not res["failures"] and res["pages_ok"] == len(ROUTES)
    return res


def run_checker(checker: Path, targets: list[Path]) -> dict:
    cmd = [sys.executable, str(checker), *map(str, targets), "--fail-on", "warn", "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=checker.parents[1])
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {"summary": {}, "findings": [], "parse_error": True}
    s = out.get("summary", {})
    return {"command": " ".join(["python", str(checker), *map(str, targets), "--fail-on", "warn", "--format", "json"]),
            "exit_code": proc.returncode, "scanned_files": s.get("scanned_files"), "skipped": s.get("skipped"),
            "findings": len(out.get("findings", [])), "by_rule": s.get("by_rule", {}), "by_severity": s.get("by_severity", {}),
            "ok": proc.returncode == 0 and not out.get("findings") and not out.get("parse_error")}


def png_text_chunks(p: Path) -> str:
    data, i, chunks = p.read_bytes(), 8, []
    while i + 8 <= len(data):
        n = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + n]
        if kind in (b"tEXt", b"iTXt"):
            chunks.append(body.decode("latin-1"))
        elif kind == b"zTXt":
            key, _, rest = body.partition(b"\0")
            chunks.append(key.decode("latin-1") + zlib.decompress(rest[1:]).decode("latin-1"))
        i += 12 + n
    return "\n".join(chunks)


def texts_of(p: Path) -> list[str]:
    """바이트를 여러 방식으로 읽은 문자열 — JSON은 escape를 푼 문자열도 더한다"""
    raw = p.read_bytes()
    out = [raw.decode("utf-8", "ignore"), raw.decode("latin-1")]
    if p.suffix == ".json":
        try:
            out.append(json.dumps(json.loads(raw.decode("utf-8-sig")), ensure_ascii=False))
        except ValueError:
            pass
    if p.suffix == ".png":
        out.append(png_text_chunks(p))
    return out


def reverse_search(db: Path, cases_file: Path, scan: dict[str, list[Path]]) -> dict:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ids = [r[0] for r in con.execute("select store_id from stores")]
    full_ids = set(ids)
    raw_ids = {i.split("_", 1)[1] for i in ids if "_" in i}
    digit_ids = {re.sub(r"\D", "", i) for i in raw_ids}
    sel = [c for c in json.loads(cases_file.read_text(encoding="utf-8"))["cases"] if c.get("store_id")]
    cols = ["name", "name_norm", "address_road", "address_jibun", "dong", "license_date"]
    terms: dict[str, set[str]] = {k: set() for k in cols}
    for c in sel:
        row = con.execute(f"select {','.join(cols)} from stores where store_id=?", (c["store_id"],)).fetchone()
        for k, v in zip(cols, row):
            if v and len(str(v)) >= 2:
                terms[k].add(str(v))
    con.close()
    squash = lambda s: re.sub(r"\s+", "", s)
    result = {"store_ids_scanned": len(full_ids), "selected_cases": len(sel), "groups": {}}
    total = 0
    for group, paths in scan.items():
        g = {"files": len(paths), "store_id": 0, "raw_store_id": 0, "digit_store_id": 0, **{k: 0 for k in cols}, "hit_files": []}  # claims-allow: ID-01
        for p in paths:
            hit = False
            for t in texts_of(p):
                ts = squash(t)
                n_full = len(set(re.findall(r"[A-Z]{2}_\d{7}-\d{3}-\d{4}-\d{5}", t)) & full_ids)
                n_raw = len(set(re.findall(r"\d{7}-\d{3}-\d{4}-\d{5}", t)) & raw_ids)
                n_dig = len(set(re.findall(r"(?<!\d)\d{19}(?!\d)", t)) & digit_ids)
                g["store_id"] += n_full
                g["raw_store_id"] += n_raw
                g["digit_store_id"] += n_dig
                hit |= bool(n_full or n_raw or n_dig)
                for k in cols:
                    n = sum(1 for v in terms[k] if v in t or squash(v) in ts)
                    g[k] += n
                    hit |= bool(n)
            if hit:
                g["hit_files"].append(f"{group}:{p.name}")  # 파일 이름만 — 값은 남기지 않는다
        total += sum(v for k, v in g.items() if isinstance(v, int) and k != "files")
        result["groups"][group] = g
    result["total_hits"] = total
    result["ok"] = total == 0
    return result


def schema_errors(submission: Path) -> int | None:
    """main에 병합된 #65 submission_schema.json으로 meta·manifest·사례 파일을 검증한 오류 수 (검사 불가면 None → 실패)"""
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        return None
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    def errors(defname: str, obj) -> int:
        sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], "$ref": f"#/$defs/{defname}"}
        return sum(1 for _ in Draft202012Validator(sub).iter_errors(obj))
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))
    n = errors("submission_meta", load(submission / "meta.json")) + errors("submission_manifest", load(submission / "manifest.json"))
    for p in sorted((submission / "cases").glob("CASE-*.json")):
        n += errors("submission_case", load(p))
    return n


def bundle_integrity(out: Path, submission: Path, private_dir: Path | None) -> dict:
    res: dict = {"failures": []}
    src = {p.relative_to(submission).as_posix(): sha(p) for p in files(submission)}
    exp_dir = out / "submission"
    exp = {p.relative_to(exp_dir).as_posix(): sha(p) for p in files(exp_dir)} if exp_dir.is_dir() else {}
    res["files_compared"] = len(src)
    res["byte_mismatch"] = sorted(k for k in set(src) | set(exp) if src.get(k) != exp.get(k))
    manifest = json.loads((submission / "manifest.json").read_text(encoding="utf-8"))
    res["manifest_mismatch"] = [f["path"] for f in manifest["files"]
                                if src.get(f["path"], "")[:12] != f["sha256_12"] or (submission / f["path"]).stat().st_size != f["bytes"]]
    res["manifest_extra"] = sorted(set(src) - {f["path"] for f in manifest["files"]} - {"manifest.json"})
    keys_hit, linked_nonempty, cards = [], 0, 0

    def walk(o, path):
        nonlocal linked_nonempty, cards
        if isinstance(o, dict):
            for k, v in o.items():
                if k in FORBIDDEN_KEYS:
                    keys_hit.append(f"{path}.{k}")
                if k == "linked_factor_ids":
                    cards += 1
                    linked_nonempty += bool(v)
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    for p in files(exp_dir) if exp_dir.is_dir() else []:
        if p.suffix == ".json":
            walk(json.loads(p.read_text(encoding="utf-8")), p.name)
    res["forbidden_keys"] = len(keys_hit)
    res["policy_cards"] = cards
    res["linked_factor_ids_nonempty"] = linked_nonempty
    meta = json.loads((submission / "meta.json").read_text(encoding="utf-8"))
    res["publication_approved"] = meta.get("publication_approved")
    res["schema_errors"] = schema_errors(submission)
    if res["schema_errors"] != 0:
        res["failures"].append("schema")
    if private_dir and private_dir.is_dir():
        priv_hashes = {sha(p) for p in files(private_dir)}
        priv_names = {p.name for p in files(private_dir)}
        res["private_files_in_out"] = sum(sha(p) in priv_hashes or p.name in priv_names for p in files(out))
    else:
        res["private_files_in_out"] = None
        res["failures"].append("private 디렉터리 비교 미실행")
    out_top = sorted(p.name for p in out.iterdir())
    res["out_top_level"] = out_top
    res["out_scope_extra"] = [n for n in out_top if n in {"bundle", "bundle_no_policy", "report", "dong"}]
    for k in ("byte_mismatch", "manifest_mismatch", "manifest_extra", "out_scope_extra"):
        if res[k]:
            res["failures"].append(k)
    if res["forbidden_keys"] or res["linked_factor_ids_nonempty"] or res["private_files_in_out"]:
        res["failures"].append("forbidden/linked/private")
    if res["publication_approved"] is not False:
        res["failures"].append("publication_approved가 false가 아님")
    res["ok"] = not res["failures"]
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dump-dir", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True, help="비공개 선택 결과(store_id 포함). 읽기만 한다")
    ap.add_argument("--submission", type=Path, required=True)
    ap.add_argument("--checker", type=Path, default=ROOT / "scripts/check_claims.py", help="authoritative check_claims (기본: 이 저장소 main의 scripts/check_claims.py)")
    ap.add_argument("--private-dir", type=Path)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--channel", default="chrome", help="Playwright가 쓸 설치된 브라우저 (chrome/msedge)")
    ap.add_argument("--timeout-ms", type=int, default=20000)
    a = ap.parse_args(argv)
    out, dump = a.out.resolve(), a.dump_dir.resolve()
    if not (out / "index.html").is_file() or dump == out or dump.is_relative_to(out):
        ap.error("out/에 index.html이 있어야 하고 dump-dir은 out 밖이어야 합니다")
    dump.mkdir(parents=True, exist_ok=True)

    cases = {}
    for p in sorted((out / "submission" / "cases").glob("CASE-*.json")):
        cases[p.stem] = json.loads(p.read_text(encoding="utf-8"))

    report: dict = {"out_files": len(files(out))}
    pages = render(out, dump, a.channel, a.timeout_ms) if a.render else []
    report["screen"] = screen_checks(pages, cases) if a.render else {"ok": False, "failures": ["렌더 미실행"]}
    rendered_dirs = [dump / "rendered", dump / "dom", dump / "captures"] if a.render else []

    report["check_claims"] = {
        "out": run_checker(a.checker, [out]),
        "submission_dir": run_checker(a.checker, [a.submission.resolve()]),
        **({"rendered": run_checker(a.checker, rendered_dirs)} if rendered_dirs else {}),
    }
    scan = {"out": files(out), "submission_dir": files(a.submission.resolve())}
    for d in rendered_dirs:
        scan[d.name] = files(d)
    report["reverse_search"] = reverse_search(a.db, a.cases, scan)
    report["integrity"] = bundle_integrity(out, a.submission.resolve(), a.private_dir.resolve() if a.private_dir else None)

    ok = (report["screen"]["ok"] and all(v["ok"] for v in report["check_claims"].values())
          and report["reverse_search"]["ok"] and report["integrity"]["ok"])
    report["judgement"] = "PASS" if ok else "FAIL"
    for p in pages:  # 화면 텍스트 원문은 dump/rendered에 있다
        p.pop("text", None)
        p.pop("attrs", None)
    report["render_pages"] = pages
    (dump / "w3_16_e2e_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("judgement", "out_files")}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
