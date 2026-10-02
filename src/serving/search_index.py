"""W2-5 점포 검색 인덱스 (Issue #29 검색 흐름의 상호 → 주소 단계).

SQLite 정본(`build_db`)의 `stores` 테이블만 읽는다. 인덱스에는 인허가 공개 정보(상호·업종·구·법정동·주소)만 담고
위험도·등급·기여·정책은 담지 않는다 (`search_index_entry`는 정의에 없는 키를 거부한다).

- 상호 정규화는 인허가 표준화와 같은 `src.data.names.normalize_name`을 쓴다 (지점명은 name_norm에서 빠진다).
  그래서 같은 상호의 여러 지점·점포가 한 검색어에 모두 걸리며, 임의로 하나를 고르지 않는다.
- 검색 결과 순서는 (일치 단계, 구, 법정동, 정규화 상호, store_id)로 항상 같다.
- 인덱스 store_id 집합 = 정본 stores = risk (상세 리포트와 같은 store_id)인지 검증한다.

실행:
    python -m src.serving.search_index --db outputs/serving/report.sqlite --out outputs/serving/dev/search_index.json
    python -m src.serving.search_index --db outputs/serving/report.sqlite --name "샘플식당"   # 조회만
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from src.data.names import normalize_name
from src.serving import build_db as bd
from src.serving import report_validation as rv

ENTRY_FIELDS = ["store_id", "name", "name_norm", "biz_type", "gu", "dong", "address_road", "address_jibun"]
MIN_QUERY_LEN = 2
_NAME_KEEP_RE = re.compile(r"[^가-힣A-Za-z0-9]")  # names.py 규칙 4와 같은 문자 집합
_ADDR_KEEP_RE = re.compile(r"[^가-힣A-Za-z0-9|-]")
_ADDR_BREAK_RE = re.compile(r"[,()]")  # 번지 뒤 ', 2층'·'(동명)'의 경계를 '|'로 남긴다 ('가상로 12, 2층' ≠ '가상로 122층')
_ADDR_PREFIX_RE = re.compile(r"^(서울특별시|서울시|서울)")


class SearchIndexError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 정규화
def compact_name(text: str | None) -> str | None:
    """지점명까지 포함한 상호 비교 키 (NFKC·대문자·한글/영문/숫자만)."""
    if text is None:
        return None
    out = _NAME_KEEP_RE.sub("", unicodedata.normalize("NFKC", str(text)).upper())
    return out or None


def address_key(text: str | None) -> str | None:
    """주소 비교 키: NFKC·대문자, 쉼표·괄호는 경계 '|'로, 공백·그 밖의 문장부호 제거('-' 유지),
    앞의 '서울특별시/서울시/서울' 제거."""
    if text is None:
        return None
    out = _ADDR_BREAK_RE.sub("|", unicodedata.normalize("NFKC", str(text)).upper())
    out = _ADDR_KEEP_RE.sub("", out).strip("|")
    out = _ADDR_PREFIX_RE.sub("", out)
    return out or None


# ---------------------------------------------------------------------------
# 인덱스 생성·검증
def _sort_key(e: dict):
    return (e["gu"], e["dong"] is None, e["dong"] or "", e["name_norm"] is None, e["name_norm"] or "", e["store_id"])


def check_consistency(conn: sqlite3.Connection, entries: list[dict], run: dict) -> list[str]:
    errs = []
    ids = [e["store_id"] for e in entries]
    if len(ids) != len(set(ids)):
        errs.append(f"store_id 중복 {len(ids) - len(set(ids))}건")
    if len(entries) != run["n_stores"]:
        errs.append(f"인덱스 행 수 {len(entries):,} ≠ runs.n_stores {run['n_stores']:,}")
    stores = {r[0] for r in conn.execute("SELECT store_id FROM stores")}
    risk = {r[0] for r in conn.execute("SELECT store_id FROM risk")}
    if set(ids) != stores or stores != risk:
        errs.append(f"store_id 집합 불일치: 인덱스 {len(set(ids)):,} / stores {len(stores):,} / risk {len(risk):,} "
                    f"(인덱스에만 {len(set(ids) - risk)}, 리포트에만 {len(risk - set(ids))})")
    for e in entries:
        want = normalize_name(e["name"])[0]
        if want != e["name_norm"]:
            errs.append(f"{e['store_id']}: name_norm '{e['name_norm']}' ≠ normalize_name(name) '{want}'")
    return errs


def build_index(conn: sqlite3.Connection, *, purpose: str = "dev") -> dict:
    """정본 → 검색 인덱스 파일 dict (`search_index_file`). purpose='release'면 공개 불가 정본에서 멈춘다."""
    run = bd.read_run(conn)
    blockers = bd.release_blockers(run)
    if purpose == "release" and blockers:
        raise SearchIndexError("공개 배포용 인덱스를 만들 수 없다: " + " / ".join(blockers))
    cur = conn.execute(f"SELECT {', '.join(ENTRY_FIELDS)} FROM stores")
    entries = sorted((dict(zip(ENTRY_FIELDS, r)) for r in cur.fetchall()), key=_sort_key)
    errs = check_consistency(conn, entries, run)
    if errs:
        raise SearchIndexError("검색 인덱스 검증 실패:\n  - " + "\n  - ".join(errs[:10]))
    out = {"_schema_version": rv.SCHEMA_VERSION, "run_id": run["run_id"], "score_origin": run["score_origin"],
           "as_of": run["as_of"], "release_ready": not blockers, "release_blockers": blockers,
           "n_entries": len(entries), "entries": entries}
    errs = rv.validate_def("search_index_file", out)
    if errs:
        raise SearchIndexError("검색 인덱스 스키마 위반:\n  - " + "\n  - ".join(errs[:10]))
    return out


# ---------------------------------------------------------------------------
# 검색 (프론트 구현의 기준 동작)
def search_name(entries: list[dict], query: str, min_len: int = MIN_QUERY_LEN) -> list[dict]:
    """상호 검색. 일치 단계: 0 지점명까지 같음 → 1 정규화 상호 같음 → 2 앞부분 일치 → 3 포함.
    같은 단계 안에서는 구·동·상호·store_id 순. 후보를 모두 돌려준다."""
    q_norm = normalize_name(query)[0]
    q_full = compact_name(query)
    if q_norm is None or len(q_norm) < min_len:
        return []
    hits = []
    for e in entries:
        n = e["name_norm"]
        if n is None:
            continue
        if n == q_norm:
            rank = 0 if compact_name(e["name"]) == q_full else 1
        elif n.startswith(q_norm):
            rank = 2
        elif q_norm in n:
            rank = 3
        else:
            continue
        hits.append((rank, _sort_key(e), e))
    return [dict(e) for _, _, e in sorted(hits, key=lambda h: h[:2])]


def _addr_rank(key: str | None, q: str) -> int | None:
    if key is None:
        return None
    best = None
    start = key.find(q)
    while start != -1:
        nxt = key[start + len(q): start + len(q) + 1]
        rank = 1 if (nxt.isdigit() and q[-1:].isdigit()) else 0  # '샘플로1'이 '샘플로12'에 걸린 경우는 뒤로
        best = rank if best is None else min(best, rank)
        start = key.find(q, start + 1)
    return best


def search_address(entries: list[dict], query: str, min_len: int = MIN_QUERY_LEN) -> list[dict]:
    """도로명·지번 주소 검색. 번지까지 경계가 맞으면 먼저, 숫자가 이어지는 부분 일치는 뒤로."""
    q = address_key(query)
    if q is None or len(q) < min_len:
        return []
    hits = []
    for e in entries:
        ranks = [r for r in (_addr_rank(address_key(e["address_road"]), q),
                             _addr_rank(address_key(e["address_jibun"]), q)) if r is not None]
        if ranks:
            hits.append((min(ranks), _sort_key(e), e))
    return [dict(e) for _, _, e in sorted(hits, key=lambda h: h[:2])]


def write_json(obj: dict, out: Path) -> None:
    out = Path(out)
    bd.guard_output_path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-5 검색 인덱스")
    ap.add_argument("--db", type=Path, default=bd.DEFAULT_OUT)
    ap.add_argument("--out", type=Path, default=None, help="인덱스 JSON 경로 (git 무시 경로만)")
    ap.add_argument("--purpose", choices=bd.PURPOSES, default="dev")
    ap.add_argument("--name", help="상호 검색 결과만 출력")
    ap.add_argument("--address", help="주소 검색 결과만 출력")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{a.db.as_posix()}?mode=ro", uri=True)
    try:
        idx = build_index(conn, purpose=a.purpose)
    finally:
        conn.close()
    print(f"검색 인덱스 {idx['n_entries']:,}점포 · run_id {idx['run_id']} · 공개 가능 {idx['release_ready']}")
    for q, fn in ((a.name, search_name), (a.address, search_address)):
        if q:
            hits = fn(idx["entries"], q)
            print(f"'{q}' → {len(hits)}건")
            for h in hits[:20]:
                print(f"  {h['store_id']} {h['name']} · {h['gu']} {h['dong']} · {h['address_road'] or h['address_jibun']}")
    if a.out:
        write_json(idx, a.out)
        print(f"→ {a.out}")


if __name__ == "__main__":
    main()
