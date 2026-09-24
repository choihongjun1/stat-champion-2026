"""W2-1 시나리오 (c): 블로그 언급의 월별 시계열 수집.

collect_online_presence.py(축A)의 "총 몇 건" 요약값과는 별개의 수집이다.
축A는 정렬 기준을 지정하지 않아 관련도순으로 1페이지(최대 100건)만 받았는데,
월별로 정확히 쪼개려면 "최신순(sort=date)"으로 다시 받아야 한다 — 관련도순 페이지와
최신순 페이지는 순서 체계가 달라 이어붙일 수 없으므로, 축A 결과를 재사용하지 않고
블로그만 처음부터 새로 수집한다 (카페는 postdate가 없어 월별 재구성 자체가 불가능하므로
대상에서 제외 — DATA_CATALOG.md §6 갱신 필요).

시나리오 (c): 점포당 최신순 최대 2페이지(200건)까지만 수집. 1페이지 응답의 API 표기
total이 100 이하면 더 받을 게 없다는 뜻이라 2페이지를 건너뛴다 (쿼터 절약).
total이 200을 넘으면 200건 밖의 과거 글은 못 받은 것이므로 해당 점포에
first_date_truncated=True를 남긴다.

이름 매칭 로직(짧은/흔한 상호에서 오탐 발생 가능)은 collect_online_presence.py와 동일하게
유지한다 — 팀 결정: 이번 범위에서는 수정하지 않고 한계로만 기록 (DATA_CATALOG.md 갱신 필요).
9/27 짧은 상호 매칭 정밀도 재검토 결과 규칙을 바꾸게 되면, 이 스크립트가 저장하는
원본(data/raw/online/blog_items_*.jsonl.gz)으로 오프라인 재적용한다. 이 재적용은
블로그에만 해당하며, 카페는 모델 feature로도 화면 표시로도 쓰이지 않아 원본을 저장하지
않으므로 재적용 대상이 아니다 — 매칭 기준이 바뀌어도 카페는 현행 규칙을 유지한다.

출력 2개:
  - online_mentions_monthly.csv : store_id x year_month 단위. 스키마 설계안의
    online_mentions_monthly 테이블. platform은 항상 'blog'.
  - online_blog_monthly_qa.csv : store_id 단위 1행. query_used, 매칭 총건수,
    truncated 플래그, error → 재개(resume) 판단과 QA에 사용.

사용법:
    python src/data/collect_blog_monthly.py --limit 5                        # 연결 테스트
    python src/data/collect_blog_monthly.py \
        --input data/interim/all_targets.csv --max-priority 1 \
        --workers 20 --resume                                                # 1순위만
    python src/data/collect_blog_monthly.py \
        --input data/interim/all_targets.csv --workers 20 --resume           # 전체
"""

import argparse
import csv
import gzip
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# collect_online_presence.py와 같은 폴더 -> 스크립트를 `python src/data/collect_blog_monthly.py`로
# 실행하면 그 폴더가 sys.path[0]이 되어 바로 import된다.
import collect_online_presence as cop

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INPUT_PATH = "data/interim/all_targets.csv"
DEFAULT_MONTHLY_OUT = "data/interim/online_mentions_monthly.csv"
DEFAULT_QA_OUT = "data/interim/online_blog_monthly_qa.csv"
# 축B는 한 번만 도는 수집이라, 매칭 통과 여부와 무관하게 받은 글 전부를 최소 필드로
# 남긴다 (git 제외, .gitignore의 /data/* 규칙에 걸림). 절단 지점 재계산·매칭 기준
# 변경 재적용을 API 재호출 없이 오프라인으로 할 수 있게 하기 위함 — 이슈 #24/#25/#28.
DEFAULT_RAW_OUT = "data/raw/online/blog_items.jsonl.gz"
# 실행마다 설정(MAX_PAGES 등)이 바뀔 수 있어(예: 절단 비율 보고 3~4로 상향), 어떤
# 설정으로 어떤 입력을 언제 수집했는지 원본과 별개로 남긴다 — 이슈 #4(manifest).
DEFAULT_MANIFEST_OUT = "data/raw/online/collection_manifest.jsonl"

PAGE_SIZE = 100
MAX_PAGES = 2  # 시나리오 (c). 시나리오 (b, 최대 10페이지)로 바꾸려면 이 값만 올리면 됨.
SORT = "date"
TRUNCATION_LIMIT = PAGE_SIZE * MAX_PAGES  # 200

MONTHLY_FIELDNAMES = [
    "store_id",
    "year_month",
    "platform",
    "mention_count",
    "sponsor_filtered_count",
    "collected_at",
]

# 수집 시작 전에 고정 — 수집 도중에는 컬럼을 바꾸지 않는다 (한 파일에 스키마 두 개가
# 섞이는 걸 막기 위함, PR #21 리뷰 §8-3 재발 방지).
QA_FIELDNAMES = [
    "store_id",
    "name",
    "dong",
    "query_used",
    "collected_at",
    "collection_run_id",
    "blog_matched_total",
    "blog_api_total",
    "first_date_truncated",
    "oldest_raw_postdate",
    "error",
]


def fetch_blog_pages(ctx: "cop.Context", query: str) -> tuple[list, int]:
    """최신순으로 최대 MAX_PAGES 페이지를 받는다. (matched 여부와 무관한 raw item 목록, API total)

    페이지를 나눠 받는 사이 새 글이 올라오면 정렬 기준(최신순)이 밀리면서 앞 페이지의
    마지막 글이 다음 페이지 앞부분에 다시 나올 수 있다 -> link 기준으로 중복 제거한다.
    """
    all_items = []
    seen_links = set()
    api_total = 0
    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE + 1
        resp = cop.pooled_get(
            ctx,
            ctx.naver,
            cop.NAVER_BLOG_URL,
            {"query": query, "display": PAGE_SIZE, "start": start, "sort": SORT},
        )
        data = resp.json()
        if page == 0:
            api_total = data.get("total", 0)
        page_items = data.get("items", [])
        for it in page_items:
            link = it.get("link", "")
            if link and link in seen_links:
                continue
            seen_links.add(link)
            all_items.append(it)
        if len(page_items) < PAGE_SIZE:
            break  # 마지막 페이지 (더 없음)
        if page == 0 and api_total <= PAGE_SIZE:
            break  # total이 1페이지 안에 다 있음 -> 2페이지 스킵 (쿼터 절약)
    return all_items, api_total


def collect_one(ctx: "cop.Context", row: pd.Series, run_id: str) -> tuple[dict, list[dict], list[dict]]:
    dong = cop.extract_dong(row.get("addr_jibun"), row.get("addr_road"))
    query = f"{row['name']} {dong}".strip()
    collected_at = datetime.now(timezone.utc).isoformat()
    qa = {
        "store_id": row["store_id"],
        "name": row["name"],
        "dong": dong,
        "query_used": query,
        "collected_at": collected_at,
        "collection_run_id": run_id,
        "error": "",
    }
    monthly_rows = []
    raw_records = []
    try:
        items, api_total = fetch_blog_pages(ctx, query)  # 이미 link 기준 중복 제거됨
        matched = cop.filter_mentions(items, row["name"])
        matched_links = {it.get("link") for it in matched}

        raw_dates = sorted(it.get("postdate", "") for it in items if len(it.get("postdate", "")) == 8)
        qa["oldest_raw_postdate"] = raw_dates[0] if raw_dates else ""

        # 받은 글 전부(매칭 통과 여부 무관)를 최소 필드로 남긴다 -> 나중에 매칭 기준이
        # 바뀌어도 API 재호출 없이 오프라인으로 재적용할 수 있다.
        for it in items:
            raw_records.append(
                {
                    "store_id": row["store_id"],
                    "link": it.get("link", ""),
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "postdate": it.get("postdate", ""),
                    "matched": it.get("link") in matched_links,
                }
            )

        buckets: dict[str, dict[str, int]] = {}
        for it in matched:
            postdate = it.get("postdate", "")
            if len(postdate) != 8:
                continue
            ym = f"{postdate[:4]}-{postdate[4:6]}"
            b = buckets.setdefault(ym, {"count": 0, "sponsor": 0})
            b["count"] += 1
            if cop.is_sponsored(it):
                b["sponsor"] += 1

        for ym, b in sorted(buckets.items()):
            monthly_rows.append(
                {
                    "store_id": row["store_id"],
                    "year_month": ym,
                    "platform": "blog",
                    "mention_count": b["count"],
                    "sponsor_filtered_count": b["sponsor"],
                    "collected_at": collected_at,
                }
            )

        qa["blog_matched_total"] = len(matched)
        qa["blog_api_total"] = api_total
        qa["first_date_truncated"] = api_total > TRUNCATION_LIMIT
    except cop.QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001
        qa["error"] = str(exc)
        qa["blog_matched_total"] = ""
        qa["blog_api_total"] = ""
        qa["first_date_truncated"] = ""
        qa["oldest_raw_postdate"] = ""
    return qa, monthly_rows, raw_records


def load_done_ids(qa_path: str) -> set:
    if not os.path.exists(qa_path):
        return set()
    df = pd.read_csv(qa_path, dtype=str)
    return set(df.loc[df["error"].fillna("") == "", "store_id"])


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:  # noqa: BLE001 - manifest 부가정보라 실패해도 수집은 계속되어야 함
        return "unknown"


def file_checksum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def append_manifest(manifest_out: str, record: dict) -> None:
    os.makedirs(os.path.dirname(manifest_out), exist_ok=True)
    with open(manifest_out, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main(
    limit: int | None,
    sleep_sec: float,
    resume: bool,
    input_path: str,
    monthly_out: str,
    qa_out: str,
    raw_out: str,
    manifest_out: str,
    workers: int,
    max_priority: int | None,
) -> int:
    from dotenv import load_dotenv

    load_dotenv()

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    start_time = datetime.now(timezone.utc).isoformat()
    manifest_base = {
        "collection_run_id": run_id,
        "max_pages": MAX_PAGES,
        "sort": SORT,
        "page_size": PAGE_SIZE,
        "endpoint": cop.NAVER_BLOG_URL,
        "git_sha": git_sha(),
        "input_path": input_path,
        "input_checksum_sha256": file_checksum(input_path),
        "start_time": start_time,
    }
    append_manifest(manifest_out, {**manifest_base, "status": "started"})
    print(f"collection_run_id={run_id}", flush=True)

    naver_keys = cop.load_naver_keys()
    kakao_keys = cop.load_kakao_keys()  # 이 스크립트는 안 쓰지만 Context 생성에 필요
    if not naver_keys:
        print("네이버 API 키를 찾지 못했습니다 (.env 확인)", flush=True)
        append_manifest(manifest_out, {**manifest_base, "status": "aborted_no_keys", "end_time": datetime.now(timezone.utc).isoformat()})
        return 1
    print(f"네이버 키 {len(naver_keys)}개 로드", flush=True)

    ctx = cop.Context(
        cop.SessionHolder(pool_size=max(workers, 10)),
        cop.KeyPool(naver_keys, "naver"),
        cop.KeyPool(kakao_keys or [{"Authorization": "unused"}], "kakao"),
    )

    df = pd.read_csv(input_path, dtype=str)
    df = df[df["name"].notna()]
    if "priority" in df.columns:
        df["priority"] = df["priority"].astype(int)
        if max_priority:
            df = df[df["priority"] <= max_priority]
        df = df.sort_values("priority", kind="stable")
    if limit:
        df = df.head(limit)

    done_ids = load_done_ids(qa_out) if resume else set()
    if done_ids:
        df = df[~df["store_id"].isin(done_ids)]
        print(f"재개: {len(done_ids)}건 완료됨, {len(df)}건 남음", flush=True)

    # QA 파일에서 실패한(error 있는) 행은 재시도 대상에 포함시키기 위해 제거하고 다시 쓴다.
    if resume and os.path.exists(qa_out):
        qa_existing = pd.read_csv(qa_out, dtype=str)
        qa_existing = qa_existing[qa_existing["error"].fillna("") == ""]
        qa_existing.to_csv(qa_out, index=False, encoding="utf-8-sig")
        if os.path.exists(monthly_out):
            monthly_existing = pd.read_csv(monthly_out, dtype=str)
            monthly_existing = monthly_existing[monthly_existing["store_id"].isin(done_ids)]
            monthly_existing.to_csv(monthly_out, index=False, encoding="utf-8-sig")

    write_header_qa = not (resume and os.path.exists(qa_out))
    write_header_monthly = not (resume and os.path.exists(monthly_out))
    mode = "a" if (resume and os.path.exists(qa_out)) else "w"
    raw_mode = "at" if (resume and os.path.exists(raw_out)) else "wt"
    os.makedirs(os.path.dirname(raw_out), exist_ok=True)

    total = len(df)
    n_done = 0
    n_errors = 0
    stalls = 0
    next_recycle = cop.SESSION_RECYCLE_EVERY
    exit_code = 0

    def worker(row: pd.Series):
        if ctx.stop.is_set():
            return None
        try:
            result = collect_one(ctx, row, run_id)
        except cop.QuotaExhausted:
            ctx.stop.set()
            return None
        if sleep_sec:
            time.sleep(sleep_sec)
        return result

    with open(qa_out, mode, newline="", encoding="utf-8-sig") as f_qa, open(
        monthly_out, mode, newline="", encoding="utf-8-sig"
    ) as f_monthly, gzip.open(raw_out, raw_mode, encoding="utf-8") as f_raw:
        qa_writer = csv.DictWriter(f_qa, fieldnames=QA_FIELDNAMES)
        monthly_writer = csv.DictWriter(f_monthly, fieldnames=MONTHLY_FIELDNAMES)
        if write_header_qa:
            qa_writer.writeheader()
        if write_header_monthly:
            monthly_writer.writeheader()

        pool = ThreadPoolExecutor(max_workers=workers)
        pending = {pool.submit(worker, row) for _, row in df.iterrows()}

        while pending:
            done, pending = wait(pending, timeout=cop.STALL_TIMEOUT_SEC, return_when=FIRST_COMPLETED)
            if not done:
                stalls += 1
                print(f"응답 정체 {stalls}/{cop.STALL_LIMIT} -> 세션 재생성", flush=True)
                ctx.sessions.recycle()
                if stalls >= cop.STALL_LIMIT:
                    ctx.stop.set()
                    exit_code = 2
                    break
                continue
            stalls = 0
            for fut in done:
                result = fut.result()
                if result is None:
                    continue
                qa, monthly_rows, raw_records = result
                qa_writer.writerow(qa)
                for row in monthly_rows:
                    monthly_writer.writerow(row)
                for rec in raw_records:
                    f_raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_done += 1
                if qa["error"]:
                    n_errors += 1
            f_qa.flush()
            f_monthly.flush()
            f_raw.flush()
            if n_done >= next_recycle:
                ctx.sessions.recycle()
                next_recycle += cop.SESSION_RECYCLE_EVERY
            if n_done and n_done % 200 < len(done):
                print(
                    f"{n_done}/{total} 처리 (오류 {n_errors}건, 네이버 키 {ctx.naver.n_active()}/{len(ctx.naver)}개 사용 가능)",
                    flush=True,
                )

        f_qa.flush()
        f_monthly.flush()
        f_raw.flush()

    if exit_code == 2:
        print(f"중단: 응답 정체. {n_done}/{total}건 저장됨. --resume 으로 다시 실행하세요.", flush=True)
        append_manifest(
            manifest_out,
            {**manifest_base, "status": "interrupted_stall", "end_time": datetime.now(timezone.utc).isoformat(), "n_done": n_done, "n_errors": n_errors},
        )
        pool.shutdown(wait=False, cancel_futures=True)
        os._exit(2)

    pool.shutdown(wait=True)
    if ctx.stop.is_set():
        print(
            f"중단: 사용 가능한 API 키 소진. {n_done}/{total}건 저장됨. 한도 초기화 후 --resume 으로 이어서 실행하세요.",
            flush=True,
        )
        append_manifest(
            manifest_out,
            {**manifest_base, "status": "interrupted_quota", "end_time": datetime.now(timezone.utc).isoformat(), "n_done": n_done, "n_errors": n_errors},
        )
        return 3

    print(f"완료: {n_done}/{total}건 (오류 {n_errors}건) -> {monthly_out}, {qa_out}, {raw_out}", flush=True)
    append_manifest(
        manifest_out,
        {**manifest_base, "status": "completed", "end_time": datetime.now(timezone.utc).isoformat(), "n_done": n_done, "n_errors": n_errors},
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--input", default=DEFAULT_INPUT_PATH)
    parser.add_argument("--monthly-output", default=DEFAULT_MONTHLY_OUT)
    parser.add_argument("--qa-output", default=DEFAULT_QA_OUT)
    parser.add_argument("--raw-output", default=DEFAULT_RAW_OUT, help="원본(jsonl.gz) 저장 경로")
    parser.add_argument("--manifest-output", default=DEFAULT_MANIFEST_OUT, help="실행 manifest(jsonl) 경로")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-priority", type=int, default=None)
    args = parser.parse_args()
    sys.exit(
        main(
            args.limit,
            args.sleep,
            args.resume,
            args.input,
            args.monthly_output,
            args.qa_output,
            args.raw_output,
            args.manifest_output,
            args.workers,
            args.max_priority,
        )
    )
