"""W3-16 static/rendered text extraction; C1 is invoked as a subprocess.

#48 sources: bundle.ts:39 report JSON template; report/[storeId]/page.tsx:32
renders ReportView, whose main/h1 are at ReportView.tsx:35,52. SearchScreen.tsx
renders main/SearchField at :56,73 on the root page. Selectors remain CLI overrides.
Exit codes: C1 0/1/2/3; 4 = incomplete extraction or unchecked captures.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ATTRS = ("alt", "title", "aria-label", "placeholder")
HEADER = "화면에 실제로 보이는 텍스트를 추출해 검사한 결과입니다. 추출 실패·미검사 항목은 통과로 보지 않습니다."


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.values = []
    def handle_starttag(self, tag, attrs):
        hidden = tag in {"script", "style", "template"} or any(k == "hidden" or (k == "style" and re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", v or "")) for k, v in attrs)
        if not any(self.stack) and not hidden:
            self.values.extend((f"{tag}@{k}", v) for k, v in attrs if k in ATTRS and v)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(hidden or any(self.stack))
    def handle_startendtag(self, tag, attrs):
        before = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[before:]
    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()
    def handle_data(self, value):
        if not any(self.stack) and value.strip():
            self.values.append((f"text@{self.getpos()[0]}", value.strip()))


def json_strings(value, path="$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from json_strings(item, path + "[" + json.dumps(key, ensure_ascii=False) + "]")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from json_strings(item, f"{path}[{index}]")


def folder_hash(folder):
    items = [(p.relative_to(folder).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(folder.rglob("*")) if p.is_file()]
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def commit():
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@contextmanager
def server(folder):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(folder), **kwargs)
        def log_message(self, *args):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def report_routes(folder, url_template):
    ids = set()
    for meta_path in folder.rglob("meta.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        template = meta["report_path_template"]
        if template.count("{store_id}") != 1 or template.startswith("/") or ".." in Path(template).parts:
            raise ValueError("invalid report template")
        prefix, suffix = template.split("{store_id}")
        index = meta_path.parent / meta.get("files", {}).get("search_index", "search_index.json")
        if not index.resolve().is_relative_to(folder.resolve()):
            raise ValueError("index outside root")
        if index.exists():
            content = json.loads(index.read_text(encoding="utf-8-sig"))
            entries = content["entries"] if isinstance(content, dict) else content
            ids.update(entry["store_id"] for entry in entries)
        for path in meta_path.parent.rglob("*.json"):
            relative = path.relative_to(meta_path.parent).as_posix()
            if relative.startswith(prefix) and relative.endswith(suffix):
                ids.add(relative[len(prefix):len(relative) - len(suffix) if suffix else None])
    if not ids:
        raise ValueError("no report routes")
    if not url_template.startswith("/") or url_template.startswith("//") or url_template.count("{store_id}") != 1:
        raise ValueError("invalid page template")
    return [url_template.replace("{store_id}", quote(str(key), safe="")) for key in sorted(ids)]


def rendered(folder, args):
    api = importlib.import_module("playwright.sync_api")
    pages = [("/", "body", "첫 화면"), ("/", args.search_selector, "검색 화면")]
    pages += [(route, args.report_selector, "리포트") for route in report_routes(folder, args.report_url_template)]
    records = []
    with server(folder) as base, api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(**({"executable_path": str(args.chromium)} if args.chromium else {}))
        try:
            for route, selector, kind in pages:
                page = browser.new_page()
                if kind != "첫 화면":
                    page.add_init_script("sessionStorage.setItem('dash_splash_seen', '1')")
                try:
                    response = page.goto(base + route, wait_until="networkidle", timeout=args.timeout_ms)
                    if response is None or response.status >= 400:
                        raise ValueError("page response failed")
                    page.locator(selector).first.wait_for(state="visible", timeout=args.timeout_ms)
                    values = [("body.innerText", page.locator("body").inner_text())]
                    values += page.evaluate("""() => Array.from(document.querySelectorAll('[alt],[title],[aria-label],[placeholder]')).filter(e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden').flatMap((e,i) => ['alt','title','aria-label','placeholder'].filter(k => e.hasAttribute(k)).map(k => [e.tagName.toLowerCase()+'['+i+']@'+k,e.getAttribute(k)]))""")
                    records.append((kind + " " + base + route, values, None))
                except Exception as exc:
                    records.append((kind + " " + base + route, [], type(exc).__name__))
                finally:
                    page.close()
        finally:
            browser.close()
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dump-dir", type=Path, required=True)
    parser.add_argument("--captures", type=Path)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--chromium", type=Path)
    parser.add_argument("--report-selector", default="main:has(h1)")
    parser.add_argument("--search-selector", default="main input")
    parser.add_argument("--report-url-template", default="/report/{store_id}/")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--fail-on", choices=("error", "warn"), default="error")
    args = parser.parse_args(argv)
    folder, dump = args.out_dir.resolve(), args.dump_dir.resolve()
    if not folder.is_dir() or dump == folder or dump.is_relative_to(folder) or (args.captures and not args.captures.is_dir()):
        parser.error("입력 폴더와 분리된 dump 폴더가 필요합니다")
    dump.mkdir(parents=True, exist_ok=True)
    text_dir = dump / "extracted"
    text_dir.mkdir(exist_ok=True)
    for old in text_dir.glob("page-*.txt"):
        old.unlink()
    rules = json.loads((ROOT / "configs/claims_rules.json").read_text(encoding="utf-8-sig"))
    patterns = [re.compile(p) for rule in rules if rule["mask"] for p in rule["patterns"]]
    def safe(value):
        for pattern in patterns:
            value = pattern.sub("[MASKED]", value)
        return value
    records, unchecked, mode_code = [], [], 0
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in {".html", ".json"} or not path.is_file():
            continue
        source = path.relative_to(folder).as_posix()
        try:
            content = path.read_text(encoding="utf-8-sig")
            if path.suffix.lower() == ".json":
                values = list(json_strings(json.loads(content)))
            else:
                html = TextParser()
                html.feed(content)
                values = html.values
            records.append((source, values, None))
        except (OSError, ValueError) as exc:
            records.append((source, [], type(exc).__name__))
    if args.render:
        try:
            records += rendered(folder, args)
        except ModuleNotFoundError:
            print("렌더 모드 사용 불가: playwright 미설치", file=sys.stderr)
            mode_code = 3
        except Exception as exc:
            records.append(("렌더 초기화", [], type(exc).__name__))
    if args.captures:
        for png in sorted(args.captures.rglob("*.png")):
            txt = png.with_suffix(".txt")
            source = "capture:" + png.relative_to(args.captures).as_posix()
            if not txt.is_file():
                unchecked.append(safe(source))
            else:
                try:
                    records.append((source, [("sidecar", txt.read_text(encoding="utf-8-sig"))], None))
                except (OSError, UnicodeError) as exc:
                    records.append((source, [], type(exc).__name__))
    mapping, failures = {}, []
    success = 0
    for index, (source, values, error) in enumerate(records):
        if error:
            failures.append({"source": safe(source), "reason": error})
            continue
        success += 1
        lines = []
        for element, value in values:
            for line in value.splitlines():
                if not line.strip():
                    continue
                origin = safe(source + ":" + element)
                lines.append(origin.replace("\n", " ") + "\t" + line)
                mapping[f"page-{index:06d}.txt:{len(lines)}"] = origin
        if lines:
            (text_dir / f"page-{index:06d}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/check_claims.py"), str(text_dir), "--format", "json", "--fail-on", args.fail_on], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    try:
        checked = json.loads(proc.stdout)
    except ValueError:
        checked = {"findings": [], "summary": {}, "message": "검사기 출력 읽기 실패"}
    findings = checked.get("findings", [])
    for item in findings:
        item["source"] = mapping.get(Path(item["file"]).name + ":" + str(item["line"]), "출처 매핑 실패")
    partial = bool(failures or unchecked or mode_code)
    code = mode_code or proc.returncode
    if code == 0 and partial:
        code = 4
    report = {"mode": "render+static" if args.render else "static", "pages": len(records), "extraction_success": success,
              "extraction_failures": failures, "unchecked_captures": unchecked,
              "checker_summary": checked.get("summary", {}), "checker_exit_code": proc.returncode,
              "findings": findings, "source_mapping": mapping, "exit_code": code,
              "judgement": "부분 검사" if partial else ("통과" if code == 0 else "위반 또는 검사 오류"),
              "input_folder_sha256": folder_hash(folder), "captures_sha256": folder_hash(args.captures) if args.captures else None,
              "generating_commit": commit()}
    (dump / "rendered_text_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [HEADER, "", f"판정: {report['judgement']} / 종료 코드: {code}",
             f"페이지·자료: {len(records)}, 추출 성공: {success}, 추출 실패: {len(failures)}, 미검사 캡처: {len(unchecked)}", "",
             "정적 모드는 HTML·JSON 문자열의 보수적인 전수 추출입니다. CSS·클라이언트 조합 문구는 렌더 모드로 확인합니다.",
             "종료 코드: 0 위반 없음 / 1 기준 이상 위반 / 2 사용 오류 / 3 검사 파일 없음 또는 렌더 사용 불가 / 4 추출 실패·미검사 항목 존재", "",
             f"입력 폴더 sha256: {report['input_folder_sha256']}", f"생성 commit: {report['generating_commit']}", ""]
    lines += [f"- {x['rule_id']} [{x['severity']}] {x['source']}: {x['match']}" for x in findings]
    lines += [f"- 추출 실패: {x['source']} ({x['reason']})" for x in failures]
    lines += [f"- 미검사: {x}" for x in unchecked]
    (dump / "rendered_text_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
