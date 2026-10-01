"""A4 numeric contracts. Dependencies: standard library, pandas and pyarrow.

JSON paths accept object keys, nonnegative indices and unique object selectors. CI uses
{"low": <extract>, "high": <extract>} against the same artifact.
Parquet statistics read only the needed column; rows uses footer metadata.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import numbers
from pathlib import Path
import string
import subprocess
import sys

import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
HEADER = "본문·화면 수치는 이 표의 값만 사용합니다. 잠정 값은 본문에 쓰지 않습니다."
STATUSES = ("확정", "잠정(재생성 전)", "재생성 후 생성")
COLUMNS = ("id", "label", "표시값", "CI", "metric", "window", "sample", "status", "rule_ref",
           "artifact", "artifact sha256(앞 12자)", "artifact 수정 시각", "생성 commit")
REQUIRED = ("id", "label", "metric", "window", "sample", "stage", "artifact", "extract", "format", "status", "rule_ref")


class ExtractionError(ValueError):
    """Safe diagnostic: never echo input values or records."""


def _numeric(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, numbers.Real) and math.isfinite(value)
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        raise ExtractionError("유한 숫자 스칼라가 아님")
    return value


def extract_value(content: bytes, spec: dict):
    if not isinstance(spec, dict):
        raise ExtractionError("추출 정의 형식 오류")
    kind = spec.get("type")
    if kind == "json":
        path = spec.get("path")
        if not isinstance(path, list) or not path or "TBD" in path:
            raise ExtractionError("JSON 경로 미확정 또는 형식 오류")
        try:
            value = json.loads(content.decode("utf-8-sig"))
            for key in path:
                if isinstance(value, list):
                    if isinstance(key, dict):
                        if not key or any(not isinstance(k, str) for k in key):
                            raise ExtractionError("JSON 객체 선택자 형식 오류")
                        selected = [item for item in value if isinstance(item, dict)
                                    and all(k in item and item[k] == v for k, v in key.items())]
                        if len(selected) != 1:
                            raise ExtractionError(f"JSON 객체 선택자 {len(selected)}개 매칭; 정확히 1개 필요")
                        value = selected[0]
                        continue
                    if type(key) is not int or key < 0:
                        raise ExtractionError("JSON 배열 인덱스 오류")
                elif isinstance(value, dict):
                    if not isinstance(key, str):
                        raise ExtractionError("JSON 객체 키 오류")
                else:
                    raise ExtractionError("JSON 경로 중간 값이 객체/배열이 아님")
                value = value[key]
        except (KeyError, IndexError, json.JSONDecodeError, UnicodeError) as exc:
            raise ExtractionError("JSON 경로 없음 또는 읽기 오류") from exc
        return _numeric(value)
    if kind not in ("csv_cell", "csv_agg"):
        raise ExtractionError("지원하지 않는 추출 유형")
    try:
        # Preserve textual filter values (e.g. 01); numeric conversion happens
        # only for the selected metric, with no silent dropping of missing rows.
        frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (ValueError, UnicodeError, pd.errors.ParserError) as exc:
        raise ExtractionError("CSV 읽기 오류") from exc
    filters = spec.get("filter", {})
    if not isinstance(filters, dict):
        raise ExtractionError("CSV 필터 형식 오류")
    for column, value in filters.items():
        if value == "TBD" or column == "TBD":
            raise ExtractionError("CSV 필터 미확정")
        if column not in frame or not isinstance(value, (str, int, float, bool)):
            raise ExtractionError("CSV 필터 열 없음 또는 형식 오류")
        frame = frame.loc[frame[column].eq(str(value))]
    column = spec.get("column")
    if column == "TBD" or column not in frame:
        raise ExtractionError("CSV 값 열 미확정 또는 없음")
    if "expected_rows" in spec:
        if type(spec["expected_rows"]) is not int or spec["expected_rows"] < 0:
            raise ExtractionError("CSV 예상 행 수 정의 오류")
        if len(frame) != spec["expected_rows"]:
            raise ExtractionError("CSV 행 수가 사전 정의와 다름")
    expected_values = spec.get("expected_values", {})
    if not isinstance(expected_values, dict) or any(not isinstance(values, list) or any(not isinstance(x, str) for x in values) for values in expected_values.values()):
        raise ExtractionError("CSV 분석 창 정의 오류")
    for key, expected in expected_values.items():
        if key not in frame or sorted(frame[key].tolist()) != sorted(expected):
            raise ExtractionError("CSV 분석 창이 사전 정의와 다름")
    if kind == "csv_cell":
        if len(frame) != 1:
            raise ExtractionError(f"CSV 필터 결과 {len(frame)}행; 정확히 1행 필요")
    else:
        agg = spec.get("agg")
        if agg not in ("mean", "min", "max", "count"):
            raise ExtractionError("집계는 mean/min/max/count만 허용")
        if not len(frame) and agg != "count":
            raise ExtractionError("CSV 집계 대상 0행")
        if agg == "count":
            # Count nonblank cells, including textual columns; no values emitted.
            return int(frame[column].replace("", pd.NA).count())
    try:
        values = pd.to_numeric(frame[column], errors="raise")
        for value in values:
            _numeric(value)
    except (ValueError, TypeError) as exc:
        raise ExtractionError("CSV 값에 결측 또는 비숫자 포함") from exc
    if kind == "csv_cell":
        return _numeric(values.iloc[0])
    return _numeric(getattr(values, agg)())


def extract_parquet(path: Path, spec: dict):
    """Aggregate only; input rows and identifiers never enter output diagnostics."""
    op = spec.get("op")
    if op not in ("rows", "nunique", "sum", "mean"):
        raise ExtractionError("Parquet 연산은 rows/nunique/sum/mean만 허용")
    try:
        if op == "rows":
            # No data columns are needed to obtain the row count.
            return int(pq.ParquetFile(path).metadata.num_rows)
        column = spec.get("column")
        if not isinstance(column, str) or not column:
            raise ExtractionError("Parquet 열 이름 필요")
        series = pd.read_parquet(path, columns=[column], engine="pyarrow")[column]
        if op == "nunique":
            return int(series.nunique(dropna=True))
        values = pd.to_numeric(series, errors="raise")
        for value in values:
            _numeric(value)
        return _numeric(getattr(values, op)())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExtractionError("Parquet 열 없음 또는 읽기·숫자 집계 오류") from exc


def _format(value, pattern):
    if not isinstance(pattern, str):
        raise ExtractionError("표시 형식 오류")
    try:
        parts = list(string.Formatter().parse(pattern))
        fields = [field for _, field, _, _ in parts if field is not None]
        if fields not in ([""], ["0"]) or any(conversion or "{" in fmt for _, _, fmt, conversion in parts):
            raise ExtractionError("숫자 필드 하나만 표시할 수 있음")
        # Literal prose is disallowed so a format cannot attach an assessment.
        if any(literal.strip() for literal, _, _, _ in parts):
            raise ExtractionError("표시 형식에 문구 추가 불가")
        return pattern.format(_numeric(value))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ExtractionError("숫자 표시 형식 적용 실패") from exc


def load_definitions(path: Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(entries, list):
        raise ValueError("정의 파일은 항목 배열이어야 합니다")
    ids = []
    for entry in entries:
        if not isinstance(entry, dict) or any(key not in entry for key in REQUIRED):
            raise ValueError("정의 필수 필드 누락")
        if any(not isinstance(entry[key], str) or not entry[key] for key in REQUIRED if key != "extract"):
            raise ValueError("정의 문자열 필드 형식 오류")
        if entry["status"] not in STATUSES or entry["stage"] not in ("detect", "diagnose", "prescribe", "policy"):
            raise ValueError("정의 status/stage 오류")
        if any(word in json.dumps(entry, ensure_ascii=False) for word in ("효과", "유의")):
            raise ValueError("정의에 출력 금지 문구 포함")
        aliases = entry.get("forbidden_alias", [])
        if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
            raise ValueError("forbidden_alias는 id 배열이어야 합니다")
        ids.append(entry["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("수치 id 중복")
    if any(alias not in ids or alias == entry["id"] for entry in entries for alias in entry.get("forbidden_alias", [])):
        raise ValueError("forbidden_alias 대상 id 오류")
    return entries


def generation_commit(repo_root: Path = REPO_ROOT) -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip())
        return sha + (" +dirty" if dirty else " +clean")
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN (git 조회 실패)"


def build_registry(entries: list[dict], outputs_root: Path, *, allow_missing_status: str | None = None,
                   commit: str | None = None) -> tuple[pd.DataFrame, int, list[str]]:
    root = outputs_root.resolve()
    commit = generation_commit() if commit is None else commit
    cache = {}
    rows, failures, warnings = [], 0, []
    for entry in entries:
        row = {key: entry[key] for key in ("id", "label", "metric", "window", "sample", "status", "rule_ref", "artifact")}
        row.update({"표시값": "", "CI": "", "artifact sha256(앞 12자)": "", "artifact 수정 시각": "", "생성 commit": commit})
        try:
            if not isinstance(entry["extract"], dict):
                raise ExtractionError("추출 정의 형식 오류")
            if entry["artifact"] == "TBD":
                raise ExtractionError("artifact 미확정")
            relative = Path(entry["artifact"])
            path = (root / relative).resolve()
            if relative.is_absolute() or relative.drive or not path.is_relative_to(root) or path == root:
                raise ExtractionError("artifact는 outputs-root 내부 상대 파일 경로여야 함")
            if path not in cache:
                try:
                    before = path.stat()
                    if entry["extract"].get("type") == "parquet_stat":
                        content = None
                        digest = hashlib.sha256()
                        with path.open("rb") as stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(block)
                        digest = digest.hexdigest()[:12]
                    else:
                        content = path.read_bytes()
                        digest = hashlib.sha256(content).hexdigest()[:12]
                    after = path.stat()
                    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                        raise ExtractionError("읽는 중 artifact 변경")
                    cache[path] = (content, digest,
                                   datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat(),
                                   (after.st_mtime_ns, after.st_size))
                except OSError as exc:
                    raise ExtractionError("artifact 없음 또는 읽기 불가") from exc
            content, digest, modified, fingerprint = cache[path]
            row["artifact sha256(앞 12자)"], row["artifact 수정 시각"] = digest, modified
            def extract_current(spec):
                if not isinstance(spec, dict):
                    raise ExtractionError("추출 정의 형식 오류")
                if spec.get("type") != "parquet_stat":
                    if content is None:
                        raise ExtractionError("동일 artifact에 서로 다른 파일 형식 지정")
                    return extract_value(content, spec)
                try:
                    before = path.stat()
                    value = extract_parquet(path, spec)
                    after = path.stat()
                except OSError as exc:
                    raise ExtractionError("Parquet 파일 없음 또는 읽기 불가") from exc
                if any((stat.st_mtime_ns, stat.st_size) != fingerprint for stat in (before, after)):
                    raise ExtractionError("집계 중 artifact 변경")
                return value
            row["표시값"] = _format(extract_current(entry["extract"]), entry["format"])
            if "ci" in entry:
                ci = entry["ci"]
                if not isinstance(ci, dict) or not all(key in ci for key in ("low", "high")):
                    raise ExtractionError("CI 정의 형식 오류")
                lo, hi = extract_current(ci["low"]), extract_current(ci["high"])
                if lo > hi:
                    raise ExtractionError("CI 하한이 상한보다 큼")
                row["CI"] = f"[{_format(lo, entry['format'])}, {_format(hi, entry['format'])}]"
        except (ExtractionError, TypeError) as exc:
            planned = entry["status"] == allow_missing_status
            row["표시값"] = ("MISSING(예정)" if planned else "MISSING") + f": {exc}"
            row["CI"] = ""
            failures += not planned
        rows.append(row)
    by_id = {row["id"]: row for row in rows}
    seen = set()
    for entry in entries:
        for alias in entry.get("forbidden_alias", []):
            pair = tuple(sorted((entry["id"], alias)))
            if pair in seen:
                continue
            seen.add(pair)
            left, right = (by_id[key]["표시값"] for key in pair)
            if not left.startswith("MISSING") and left == right:
                warnings.append(f"WARN forbidden_alias: {pair[0]} / {pair[1]} 표시값 동일")
    return pd.DataFrame(rows, columns=COLUMNS), int(failures), warnings


def write_registry(table: pd.DataFrame, out_dir: Path, warnings: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / "numbers_registry.csv", index=False, encoding="utf-8-sig")
    def escape(value):
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("<", "&lt;")
    lines = [HEADER, "", "생성 commit은 이 표 생성 코드의 HEAD입니다. artifact 자체를 만든 commit을 뜻하지 않습니다.", "",
             "| " + " | ".join(COLUMNS) + " |", "| " + " | ".join(["---"] * len(COLUMNS)) + " |"]
    lines += ["| " + " | ".join(escape(x) for x in row) + " |" for row in table.itertuples(index=False, name=None)]
    lines += ["", *(escape(warning) for warning in warnings)]
    (out_dir / "numbers_registry.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A4 수치 계약표 생성")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs/numbers_w3.json")
    parser.add_argument("--outputs-root", type=Path, default=Path("outputs"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/numbers"))
    parser.add_argument("--allow-missing-status", choices=STATUSES)
    args = parser.parse_args(argv)
    try:
        entries = load_definitions(args.config)
        table, failures, warnings = build_registry(entries, args.outputs_root,
                                                   allow_missing_status=args.allow_missing_status)
        write_registry(table, args.out_dir, warnings)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"설정 또는 출력 오류 ({type(exc).__name__})\n")
    for warning in warnings:
        print(warning, file=sys.stderr)
    print(f"수치 계약표 생성: {len(table)}항목, 누락 실패 {failures}건")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
