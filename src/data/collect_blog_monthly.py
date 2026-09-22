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
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone

import pandas as pd
import requests

# collect_online_presence.py와 같은 폴더 -> 스크립트를 `python src/data/collect_blog_monthly.py`로
# 실행하면 그 폴더가 sys.path[0]이 되어 바로 import된다.
import collect_online_presence as cop

DEFAULT_INPUT_PATH = "data/interim/all_targets.csv"
DEFAULT_MONTHLY_OUT = "data/interim/online_mentions_monthly.csv"
DEFAULT_QA_OUT = "data/interim/online_blog_monthly_qa.csv"

PAGE_SIZE = 100
MAX_PAGES = 2  # 시나리오 (c). 시나리오 (b, 최대 10페이지)로 바꾸려면 이 값만 올리면 됨.
TRUNCATION_LIMIT = PAGE_SIZE * MAX_PAGES  # 200

MONTHLY_FIELDNAMES = [
    "store_id",
    "year_month",
    "platform",
    "mention_count",
    "sponsor_filtered_count",
    "collected_at",
]

QA_FIELDNAMES = [
    "store_id",
    "name",
    "dong",
    "query_used",
    "collected_at",
    "blog_matched_total",
    "blog_api_total",
    "first_date_truncated",
    "error",
]


def fetch_blog_pages(ctx: "cop.Context", query: str) -> tuple[list, int]:
    """최신순으로 최대 MAX_PAGES 페이지를 받는다. (matched 여부와 무관한 raw item 목록, API total)"""
    all_items = []
    api_total = 0
    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE + 1
        resp = cop.pooled_get(
            ctx,
            ctx.naver,
            cop.NAVER_BLOG_URL,
            {"query": query, "display": PAGE_SIZE, "start": start, "sort": "date"},
        )
        data = resp.json()
        if page == 0:
            api_total = data.get("total", 0)
        items = data.get("items", [])
        all_items.extend(items)
        if len(items) < PAGE_SIZE:
            break  # 마지막 페이지 (더 없음)
        if page == 0 and api_total <= PAGE_SIZE:
            break  # total이 1페이지 안에 다 있음 -> 2페이지 스킵 (쿼터 절약)
    return all_items, api_total


def collect_one(ctx: "cop.Context", row: pd.Series) -> tuple[dict, list[dict]]:
    dong = cop.extract_dong(row.get("addr_jibun"), row.get("addr_road"))
    query = f"{row['name']} {dong}".strip()
    collected_at = datetime.now(timezone.utc).isoformat()
    qa = {
        "store_id": row["store_id"],
        "name": row["name"],
        "dong": dong,
        "query_used": query,
        "collected_at": collected_at,
        "error": "",
    }
    monthly_rows = []
    try:
        items, api_total = fetch_blog_pages(ctx, query)
        matched = cop.filter_mentions(items, row["name"])

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
    return qa, monthly_rows


def load_done_ids(qa_path: str) -> set:
    if not os.path.exists(qa_path):
        return set()
    df = pd.read_csv(qa_path, dtype=str)
    return set(df.loc[df["error"].fillna("") == "", "store_id"])


def main(
    limit: int | None,
    sleep_sec: float,
    resume: bool,
    input_path: str,
    monthly_out: str,
    qa_out: str,
    workers: int,
    max_priority: int | None,
) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    naver_keys = cop.load_naver_keys()
    kakao_keys = cop.load_kakao_keys()  # 이 스크립트는 안 쓰지만 Context 생성에 필요
    if not naver_keys:
        print("네이버 API 키를 찾지 못했습니다 (.env 확인)", flush=True)
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
            result = collect_one(ctx, row)
        except cop.QuotaExhausted:
            ctx.stop.set()
            return None
        if sleep_sec:
            time.sleep(sleep_sec)
        return result

    with open(qa_out, mode, newline="", encoding="utf-8-sig") as f_qa, open(
        monthly_out, mode, newline="", encoding="utf-8-sig"
    ) as f_monthly:
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
                qa, monthly_rows = result
                qa_writer.writerow(qa)
                for row in monthly_rows:
                    monthly_writer.writerow(row)
                n_done += 1
                if qa["error"]:
                    n_errors += 1
            f_qa.flush()
            f_monthly.flush()
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

    if exit_code == 2:
        print(f"중단: 응답 정체. {n_done}/{total}건 저장됨. --resume 으로 다시 실행하세요.", flush=True)
        pool.shutdown(wait=False, cancel_futures=True)
        os._exit(2)

    pool.shutdown(wait=True)
    if ctx.stop.is_set():
        print(
            f"중단: 사용 가능한 API 키 소진. {n_done}/{total}건 저장됨. 한도 초기화 후 --resume 으로 이어서 실행하세요.",
            flush=True,
        )
        return 3

    print(f"완료: {n_done}/{total}건 (오류 {n_errors}건) -> {monthly_out}, {qa_out}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--input", default=DEFAULT_INPUT_PATH)
    parser.add_argument("--monthly-output", default=DEFAULT_MONTHLY_OUT)
    parser.add_argument("--qa-output", default=DEFAULT_QA_OUT)
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
            args.workers,
            args.max_priority,
        )
    )
