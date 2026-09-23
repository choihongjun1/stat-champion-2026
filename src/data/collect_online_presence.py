"""W1-4 / W2-1: 온라인 존재감 수집 스크립트.

점포별로 "상호 + 법정동" 쿼리를 만들어
  - 네이버 지역검색 (등록 여부)
  - 네이버 블로그 검색 (언급 건수 · 최신일 · 협찬 필터)
  - 네이버 카페글 검색 (언급 건수 · 협찬 필터)
  - 카카오 로컬 (등록 여부)
를 조회하고 결과를 CSV로 누적 저장한다.

등록 여부 판정은 상호명 일치 + 후보 주소의 시군구·법정동 일치를 모두 요구한다
(1차 시범에서 상호명+동만으로 판정 시 짧은 상호(예: "이화")가 다른 동네 가게와
오탐 매칭되는 사례가 검수로 확인됨 — audit_online_presence_matches.py 참조.
PR #21 리뷰 지적: 3구 법정동 49개 중 19개가 타 시군구에도 존재해(전체의 45.2%),
동 일치만으로는 동명이동 오매칭을 막지 못한다 — 구 일치를 추가로 요구한다).
후보가 여러 건 일치하면 첫 번째(검색 API가 이미 관련도/근접도순으로 정렬)를 쓰되
`ambiguous=True`로 표시해 2순위 이하 후보가 있었음을 남긴다. 후보 수(`n_candidates`)는
"검색 결과 0건"과 "결과는 있었지만 조건 불일치로 미매칭"을 구분하기 위해 항상 남긴다.
블로그/카페는 검색 결과 중 상호명이 제목·본문에 실제로 들어있는 글만 언급으로 센다.

DATA_CATALOG.md 등록 규칙에 따라 모든 레코드에 collected_at, query_used를 남긴다.
DECISIONS.md 온라인 변수 사용 범위: 등록 여부·순위는 "현재 진단 표시용"으로만 쓴다.
작성일이 있는 블로그 언급만 과거 시점 feature 재구성에 쓸 수 있다.
카페글 검색 API 응답에는 작성일(postdate)이 없어 카페 언급은 시계열로 재구성할 수 없다.

API 키 (.env):
  NAVER_CLIENT_ID / NAVER_CLIENT_SECRET, NAVER_CLIENT_ID_2 / NAVER_CLIENT_SECRET_2, ...
  KAKAO_REST_API_KEY, KAKAO_REST_API_KEY_2, ...
네이버 검색 API는 키(Application)당 일 25,000회 한도가 있다. 키를 번호순으로 쓰다가
한도 초과(429)가 나면 다음 키로 넘어가고, 모든 키가 소진되면 깔끔하게 멈춘다.
다음 날 --resume 으로 이어서 실행하면 된다.

종료 코드: 0 정상 완료 / 2 응답 정체로 중단 / 3 모든 키 한도 소진으로 중단

사용법:
    python src/data/collect_online_presence.py --limit 5                     # 연결 테스트
    python src/data/collect_online_presence.py                               # 광진구 표본(기본 입출력)
    python src/data/collect_online_presence.py \
        --input data/interim/all_targets.csv \
        --output data/interim/online_presence_all.csv \
        --workers 20 --max-priority 1 --resume                               # W2-1: 1순위 대상, 이어하기
"""

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

DEFAULT_INPUT_PATH = "data/interim/gwangjin_sample.csv"
DEFAULT_OUT_PATH = "data/interim/online_presence_gwangjin_sample.csv"

# 2026-07 NAVER API HUB(NCP)로 이관됨. 기존 openapi.naver.com + X-Naver-Client-* 헤더는 더 이상 동작하지 않는다.
NAVER_LOCAL_URL = "https://naverapihub.apigw.ntruss.com/search/v1/local"
NAVER_BLOG_URL = "https://naverapihub.apigw.ntruss.com/search/v1/blog"
NAVER_CAFE_URL = "https://naverapihub.apigw.ntruss.com/search/v1/cafearticle"
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

# 협찬/광고성 게시물 판정 키워드 (경계 기준, 문서화 필요 — CLAUDE.md 개발 규칙).
# 네이버 블로그·카페 검색 결과의 title+description에 아래 문구 중 하나라도 포함되면
# "협찬"으로 표시한다 (raw_count에서 제외하지는 않음 — sponsor_filtered_count로 별도 집계만 함).
# 근거: 공정거래위원회 「추천·보증 등에 관한 표시·광고 심사지침」이 요구하는 경제적 대가
# 표기 문구를 기준으로 선정. 이 문구가 없어도 실제로는 협찬일 수 있고(미표기 위반 사례),
# 반대로 "협찬 아님을 밝힙니다"처럼 부정문 안에 키워드가 있어도 매칭되는 한계가 있다
# (단순 키워드 매칭이라 문맥/부정어 처리 안 함) — sponsor_filtered_count는 참고용 신호로만
# 쓰고, 정밀 판정이 필요하면 사람이 직접 확인해야 한다.
SPONSOR_KEYWORDS = [
    "협찬",
    "제공받아",
    "원고료",
    "체험단",
    "무상으로 제공",
    "무료로 제공",
    "지원을 받아",
    "소정의 활동비",
    "소정의 원고료",
]
SPONSOR_PATTERN = re.compile("|".join(re.escape(k) for k in SPONSOR_KEYWORDS))


def is_sponsored(item: dict) -> bool:
    text = strip_tags(item.get("title", "")) + " " + strip_tags(item.get("description", ""))
    return bool(SPONSOR_PATTERN.search(text))

DONG_PATTERN = re.compile(r"[가-힣]+구\s+(\S+?동)")

STALL_TIMEOUT_SEC = 120
STALL_LIMIT = 3
SESSION_RECYCLE_EVERY = 1000

FIELDNAMES = [
    "store_id",
    "name",
    "gu",
    "dong",
    "query_used",
    "collected_at",
    "naver_local_registered",
    "naver_local_rank",
    "naver_local_matched_title",
    "naver_local_matched_address",
    "naver_local_n_candidates",
    "naver_local_ambiguous",
    "naver_blog_total",
    "naver_blog_api_total",
    "naver_blog_sponsor_filtered",
    "naver_blog_last_date",
    "naver_blog_first_date",
    "naver_cafe_total",
    "naver_cafe_api_total",
    "naver_cafe_sponsor_filtered",
    "kakao_registered",
    "kakao_rank",
    "kakao_matched_place_name",
    "kakao_matched_address",
    "kakao_n_candidates",
    "kakao_ambiguous",
    "error_type",
    "error",
]


class QuotaExhausted(Exception):
    """사용 가능한 API 키가 남아있지 않음 (일 한도 소진 또는 키 무효)."""


class KeyQuotaExceeded(Exception):
    """이 요청에 쓴 키가 한도 초과/인증 실패로 쓸 수 없음."""


class KeyPool:
    """번호순으로 키를 쓰다가, 한도 초과가 난 키는 제외하고 다음 키로 넘어간다."""

    def __init__(self, keys: list[dict], label: str):
        self._keys = keys
        self._label = label
        self._dead: dict[int, str] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    def active_index(self) -> int | None:
        with self._lock:
            for i in range(len(self._keys)):
                if i not in self._dead:
                    return i
        return None

    def n_active(self) -> int:
        with self._lock:
            return len(self._keys) - len(self._dead)

    def headers(self, idx: int) -> dict:
        return self._keys[idx]

    def mark_dead(self, idx: int, reason: str) -> None:
        with self._lock:
            if idx in self._dead:
                return
            self._dead[idx] = reason
            n_left = len(self._keys) - len(self._dead)
        print(
            f"[{self._label}] 키 #{idx + 1} 사용 불가({reason}) -> 남은 키 {n_left}/{len(self._keys)}개",
            flush=True,
        )


def load_naver_keys() -> list[dict]:
    keys = []
    for i in range(1, 21):
        suffix = "" if i == 1 else f"_{i}"
        cid = (os.environ.get(f"NAVER_CLIENT_ID{suffix}") or "").strip()
        secret = (os.environ.get(f"NAVER_CLIENT_SECRET{suffix}") or "").strip()
        if cid and secret:
            keys.append({"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret})
    return keys


def load_kakao_keys() -> list[dict]:
    keys = []
    for i in range(1, 21):
        suffix = "" if i == 1 else f"_{i}"
        key = (os.environ.get(f"KAKAO_REST_API_KEY{suffix}") or "").strip()
        if key:
            keys.append({"Authorization": f"KakaoAK {key}"})
    return keys


def make_session(pool_size: int) -> requests.Session:
    # requests의 기본 커넥션 풀(호스트당 10)이 동시 워커 수보다 작으면 스레드가
    # 풀에서 유휴 커넥션을 기다리며 대기하게 돼 병렬화 효과가 거의 안 남 -> 워커 수만큼 키운다.
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SessionHolder:
    """오래 유지된 keep-alive 커넥션이 절전/네트워크 변경 뒤에 죽은 채 남아 응답이 멈추는
    문제를 막기 위해, 주기적으로 세션(커넥션 풀)을 새로 만든다."""

    def __init__(self, pool_size: int):
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._session = make_session(pool_size)

    def get(self) -> requests.Session:
        with self._lock:
            return self._session

    def recycle(self) -> None:
        with self._lock:
            self._session = make_session(self._pool_size)


class Context:
    def __init__(self, sessions: SessionHolder, naver: KeyPool, kakao: KeyPool):
        self.sessions = sessions
        self.naver = naver
        self.kakao = kakao
        self.stop = threading.Event()


def strip_tags(text: str) -> str:
    return re.sub(r"</?b>", "", text or "")


def normalize_name(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", name or "").lower()


def name_matches(store_name: str, candidate: str) -> bool:
    a, b = normalize_name(store_name), normalize_name(candidate)
    if not a or not b:
        return False
    return a in b or b in a


def dong_consistent(dong: str, candidate_address: str) -> bool:
    if not dong or not candidate_address:
        return False
    return dong in candidate_address


def gu_consistent(gu: str, candidate_address: str) -> bool:
    # gu가 비어있으면(예: 광진구 단일구 시범 입력처럼 구 컬럼이 없는 경우) 제약을 걸지 않는다.
    # 3구 정식 입력(all_targets.csv)은 구가 항상 채워져 있어 이 분기를 타지 않는다.
    if not gu:
        return True
    if not candidate_address:
        return False
    return gu in candidate_address


ROAD_DONG_PATTERN = re.compile(r"\(([가-힣]+동\d*가?)")


def extract_dong(addr_jibun, addr_road=None) -> str:
    # 일부 점포는 지번주소가 비어있다 -> 도로명주소 끝의 "(구의동)" 표기로 대체한다.
    if isinstance(addr_jibun, str):
        m = DONG_PATTERN.search(addr_jibun)
        if m:
            return m.group(1)
    if isinstance(addr_road, str):
        m = ROAD_DONG_PATTERN.search(addr_road)
        if m:
            return m.group(1)
    return ""


def is_quota_response(resp: requests.Response) -> bool:
    """429 응답이 "이 키의 일 한도 소진"인지 판정한다.

    기존에는 응답 본문에 한글 문구("한도")가 있는지로 판정했는데, NAVER API HUB가
    영문 메시지를 반환하면(로케일/응답 포맷 변경 등) 감지가 안 돼 키가 소진된 뒤에도
    계속 요청을 낭비하는 문제가 있었다(PR #21 리뷰 지적). 메시지 문구가 아니라
    `error.errorCode == 429` 구조로 판정하면 언어와 무관하게 동작한다
    (실측: 실제 한도 소진 응답은 `{"error":{"errorCode":429,...}}` 형태).
    구조가 다르면(예: 일시적 429) 한도 소진이 아닌 것으로 보고 재시도 대상에 남긴다.
    """
    try:
        body = json.loads(resp.content)
    except (ValueError, TypeError):
        return False
    return isinstance(body, dict) and body.get("error", {}).get("errorCode") == 429


def request_with_retry(
    ctx: Context, url: str, headers: dict, params: dict, retries: int = 3
) -> requests.Response:
    last_exc = None
    for attempt in range(retries):
        try:
            resp = ctx.sessions.get().get(url, headers=headers, params=params, timeout=(5, 10))
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429 and is_quota_response(resp):
                raise KeyQuotaExceeded("quota")
            if resp.status_code in (401, 403):
                raise KeyQuotaExceeded(f"auth {resp.status_code}")
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"request failed after {retries} retries: {url}")


def pooled_get(ctx: Context, pool: KeyPool, url: str, params: dict) -> requests.Response:
    """풀의 현재 키로 요청하고, 그 키가 한도 초과면 다음 키로 넘어가 재시도한다."""
    while True:
        idx = pool.active_index()
        if idx is None:
            raise QuotaExhausted()
        try:
            return request_with_retry(ctx, url, pool.headers(idx), params)
        except KeyQuotaExceeded as exc:
            pool.mark_dead(idx, str(exc))


def naver_local(ctx: Context, query: str, store_name: str, dong: str, gu: str) -> dict:
    resp = pooled_get(ctx, ctx.naver, NAVER_LOCAL_URL, {"query": query, "display": 5})
    items = resp.json().get("items", [])
    matches = []
    for rank, item in enumerate(items, start=1):
        title = strip_tags(item.get("title", ""))
        address = item.get("address", "") or item.get("roadAddress", "")
        # 이름만 겹치는 경우 흔한 짧은 상호(예: "이화")가 다른 동네 가게와, 동만 같은 경우
        # 타 시군구의 동명이동 가게와 오탐 매칭되는 사례가 확인됨 -> 구+동 모두 일치 요구.
        if name_matches(store_name, title) and dong_consistent(dong, address) and gu_consistent(gu, address):
            matches.append({"rank": rank, "matched_title": title, "matched_address": address})
    n_candidates = len(items)
    if not matches:
        return {
            "registered": False, "rank": None, "matched_title": "", "matched_address": "",
            "n_candidates": n_candidates, "ambiguous": False,
        }
    # 검색 API가 이미 관련도/근접도순으로 정렬해 주므로 첫 매칭을 채택하되,
    # 2순위 이하 후보가 더 있었다는 사실은 ambiguous로 남긴다(임의로 확정하지 않음).
    best = matches[0]
    return {
        "registered": True, "rank": best["rank"], "matched_title": best["matched_title"],
        "matched_address": best["matched_address"], "n_candidates": n_candidates,
        "ambiguous": len(matches) > 1,
    }


def naver_text_search(ctx: Context, url: str, query: str) -> tuple[list, int]:
    # sort 생략 -> 기본값 sim(관련도). "선화분식 군자동"처럼 상호+동을 붙여 검색해도
    # 네이버 블로그/카페 검색은 단어 단위로 느슨하게 매칭돼서, 상호와 무관한 글이
    # (심지어 스팸 블로그까지) 대량으로 잡히는 것이 확인됨 -> 아래에서 상호명 포함 여부로
    # 다시 걸러야 한다 (raw item 개수를 그대로 "언급 건수"로 쓰면 안 됨).
    # PR #21 리뷰 지적: 응답의 total은 매칭 필터 이전 API 원본 총건수라 len(items)와 다르다.
    # len(items)는 1페이지(최대 100건) 한도에 걸려 절단되므로, "언급 건수"가 실제로 100을
    # 초과하는지 사후 진단하려면 이 total을 같이 남겨야 한다.
    resp = pooled_get(ctx, ctx.naver, url, {"query": query, "display": 100})
    data = resp.json()
    return data.get("items", []), data.get("total", 0)


def filter_mentions(items: list, store_name: str) -> list:
    mentions = []
    for it in items:
        text = strip_tags(it.get("title", "")) + " " + strip_tags(it.get("description", ""))
        if name_matches(store_name, text):
            mentions.append(it)
    return mentions


def kakao_local(ctx: Context, query: str, store_name: str, dong: str, gu: str) -> dict:
    resp = pooled_get(ctx, ctx.kakao, KAKAO_LOCAL_URL, {"query": query, "size": 15})
    docs = resp.json().get("documents", [])
    matches = []
    for rank, doc in enumerate(docs, start=1):
        place_name = doc.get("place_name", "")
        # address_name(지번주소)은 법정동명을 포함하지만 road_address_name(도로명주소)은
        # 보통 포함하지 않는다 (예: "자양로13길 98"에는 "자양동"이 없음) -> 지번주소를 우선.
        address = doc.get("address_name", "") or doc.get("road_address_name", "")
        if name_matches(store_name, place_name) and dong_consistent(dong, address) and gu_consistent(gu, address):
            matches.append({"rank": rank, "matched_place_name": place_name, "matched_address": address})
    n_candidates = len(docs)
    if not matches:
        return {
            "registered": False, "rank": None, "matched_place_name": "", "matched_address": "",
            "n_candidates": n_candidates, "ambiguous": False,
        }
    best = matches[0]
    return {
        "registered": True, "rank": best["rank"], "matched_place_name": best["matched_place_name"],
        "matched_address": best["matched_address"], "n_candidates": n_candidates,
        "ambiguous": len(matches) > 1,
    }


def classify_error(exc: Exception) -> str:
    """error 문자열은 자유형식이라 집계로 실패 유형을 못 나눈다(PR #21 리뷰 지적) ->
    실패율(API 문제)과 실제 미등록률을 분리 집계할 수 있게 유형을 따로 남긴다."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        return "http_error"
    if isinstance(exc, (ValueError, KeyError)):
        return "parse_error"
    return "other"


def collect_one(ctx: Context, row: pd.Series) -> dict:
    dong = extract_dong(row.get("addr_jibun"), row.get("addr_road"))
    gu = (row.get("구") or "").strip() if isinstance(row.get("구"), str) else ""
    query = f"{row['name']} {dong}".strip()
    result = {
        "store_id": row["store_id"],
        "name": row["name"],
        "gu": gu,
        "dong": dong,
        "query_used": query,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "error_type": "",
        "error": "",
    }
    try:
        local = naver_local(ctx, query, row["name"], dong, gu)
        result["naver_local_registered"] = local["registered"]
        result["naver_local_rank"] = local["rank"]
        result["naver_local_matched_title"] = local["matched_title"]
        result["naver_local_matched_address"] = local["matched_address"]
        result["naver_local_n_candidates"] = local["n_candidates"]
        result["naver_local_ambiguous"] = local["ambiguous"]

        blog_raw, blog_api_total = naver_text_search(ctx, NAVER_BLOG_URL, query)
        blog_items = filter_mentions(blog_raw, row["name"])
        blog_sponsored = [it for it in blog_items if is_sponsored(it)]
        blog_dates = sorted(it.get("postdate", "") for it in blog_items if it.get("postdate"))
        result["naver_blog_total"] = len(blog_items)
        result["naver_blog_api_total"] = blog_api_total
        result["naver_blog_sponsor_filtered"] = len(blog_sponsored)
        result["naver_blog_last_date"] = blog_dates[-1] if blog_dates else ""
        result["naver_blog_first_date"] = blog_dates[0] if blog_dates else ""

        cafe_raw, cafe_api_total = naver_text_search(ctx, NAVER_CAFE_URL, query)
        cafe_items = filter_mentions(cafe_raw, row["name"])
        cafe_sponsored = [it for it in cafe_items if is_sponsored(it)]
        result["naver_cafe_total"] = len(cafe_items)
        result["naver_cafe_api_total"] = cafe_api_total
        result["naver_cafe_sponsor_filtered"] = len(cafe_sponsored)

        kakao = kakao_local(ctx, query, row["name"], dong, gu)
        result["kakao_registered"] = kakao["registered"]
        result["kakao_rank"] = kakao["rank"]
        result["kakao_matched_place_name"] = kakao["matched_place_name"]
        result["kakao_matched_address"] = kakao["matched_address"]
        result["kakao_n_candidates"] = kakao["n_candidates"]
        result["kakao_ambiguous"] = kakao["ambiguous"]
    except QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001 - 수집 실패는 기록하고 다음 건으로 진행
        result["error"] = str(exc)
        result["error_type"] = classify_error(exc)
    return result


def load_success_rows(out_path: str) -> pd.DataFrame | None:
    """error가 비어있는(=성공한) 행만 반환한다. 네트워크 오류 등으로 실패한 행은
    store_id는 남아있어도 "완료"로 치지 않고 재시도 대상에 포함시켜야 한다."""
    if not os.path.exists(out_path):
        return None
    df = pd.read_csv(out_path, dtype=str)
    return df[df["error"].fillna("") == ""]


def main(
    limit: int | None,
    sleep_sec: float,
    resume: bool,
    input_path: str,
    out_path: str,
    workers: int,
    max_priority: int | None,
) -> int:
    load_dotenv()
    naver_keys = load_naver_keys()
    kakao_keys = load_kakao_keys()
    if not naver_keys or not kakao_keys:
        print("API 키를 찾지 못했습니다 (.env 확인)", flush=True)
        return 1
    print(f"네이버 키 {len(naver_keys)}개, 카카오 키 {len(kakao_keys)}개 로드", flush=True)

    ctx = Context(
        SessionHolder(pool_size=max(workers, 10)),
        KeyPool(naver_keys, "naver"),
        KeyPool(kakao_keys, "kakao"),
    )

    df = pd.read_csv(input_path, dtype=str)
    n_noname = df["name"].isna().sum()
    if n_noname:
        print(f"상호명이 비어있는 {n_noname}건은 수집 대상에서 제외", flush=True)
        df = df[df["name"].notna()]
    if "priority" in df.columns:
        df["priority"] = df["priority"].astype(int)
        if max_priority:
            df = df[df["priority"] <= max_priority]
        df = df.sort_values("priority", kind="stable")
    if limit:
        df = df.head(limit)

    success = load_success_rows(out_path) if resume else None
    if success is not None:
        done_ids = set(success["store_id"])
        # 실패했던 행이 파일에 남아 중복되지 않도록, 성공한 행만 남기고 다시 쓴다.
        success.to_csv(out_path, index=False, encoding="utf-8-sig")
        df = df[~df["store_id"].isin(done_ids)]
        print(f"재개: {len(done_ids)}건 성공 완료, {len(df)}건 남음", flush=True)

    write_header = success is None
    mode = "a" if success is not None else "w"
    total = len(df)
    n_done = 0
    n_errors = 0
    stalls = 0
    next_recycle = SESSION_RECYCLE_EVERY
    exit_code = 0

    def worker(row: pd.Series) -> dict | None:
        if ctx.stop.is_set():
            return None
        try:
            result = collect_one(ctx, row)
        except QuotaExhausted:
            ctx.stop.set()
            return None
        if sleep_sec:
            time.sleep(sleep_sec)
        return result

    with open(out_path, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()

        pool = ThreadPoolExecutor(max_workers=workers)
        pending = {pool.submit(worker, row) for _, row in df.iterrows()}

        while pending:
            done, pending = wait(pending, timeout=STALL_TIMEOUT_SEC, return_when=FIRST_COMPLETED)
            if not done:
                stalls += 1
                print(f"응답 정체 {stalls}/{STALL_LIMIT} ({STALL_TIMEOUT_SEC}초간 완료 0건) -> 세션 재생성", flush=True)
                ctx.sessions.recycle()
                if stalls >= STALL_LIMIT:
                    ctx.stop.set()
                    exit_code = 2
                    break
                continue
            stalls = 0
            for fut in done:
                result = fut.result()
                if result is None:
                    continue
                writer.writerow(result)
                n_done += 1
                if result["error"]:
                    n_errors += 1
            f.flush()
            if n_done >= next_recycle:
                ctx.sessions.recycle()
                next_recycle += SESSION_RECYCLE_EVERY
            if n_done and n_done % 200 < len(done):
                print(
                    f"{n_done}/{total} 처리 (오류 {n_errors}건, 네이버 키 {ctx.naver.n_active()}/{len(ctx.naver)}개 사용 가능)",
                    flush=True,
                )

        f.flush()

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

    print(f"완료: {n_done}/{total}건 (오류 {n_errors}건) -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="테스트용 상위 N건만 실행")
    parser.add_argument("--sleep", type=float, default=0.05, help="요청 후 대기 시간(초, 워커별)")
    parser.add_argument("--resume", action="store_true", help="기존 출력 파일 이어서 진행")
    parser.add_argument("--input", default=DEFAULT_INPUT_PATH, help="대상 점포 목록 CSV 경로")
    parser.add_argument("--output", default=DEFAULT_OUT_PATH, help="결과 저장 CSV 경로")
    parser.add_argument("--workers", type=int, default=1, help="동시 처리 워커 수")
    parser.add_argument("--max-priority", type=int, default=None, help="이 우선순위 이하만 수집 (입력에 priority 컬럼 필요)")
    args = parser.parse_args()
    sys.exit(
        main(args.limit, args.sleep, args.resume, args.input, args.output, args.workers, args.max_priority)
    )
