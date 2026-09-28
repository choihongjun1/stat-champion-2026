"""W1-4 매칭 정확도 검수.

collect_online_presence.py는 "상호명이 검색결과 제목에 포함되는지"만 보고
등록 여부를 True/False로 판단한다. 이 방식은 상호명이 짧거나 흔하면
(예: "이화식당" -> "이화") 다른 가게를 잘못 매칭할 수 있다.

이 스크립트는 registered=True로 나온 건에 대해 후보의 주소를 다시 조회해서,
점포의 실제 동(법정동)과 후보 주소의 동이 일치하는지로 매칭 정확도를 추정한다.
동이 다르면 이름만 겹치고 실제로는 다른 가게일 가능성이 높다 (오탐 후보).

사용법:
    python src/data/audit_online_presence_matches.py
"""

import os
import re
import time

import pandas as pd
import requests
from dotenv import load_dotenv

PRESENCE_PATH = "data/interim/online_presence_gwangjin_sample.csv"
OUT_PATH = "data/interim/online_presence_match_audit.csv"

NAVER_LOCAL_URL = "https://naverapihub.apigw.ntruss.com/search/v1/local"
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def strip_tags(text: str) -> str:
    return re.sub(r"</?b>", "", text or "")


def normalize_name(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", name or "").lower()


def name_matches(store_name: str, candidate: str) -> bool:
    a, b = normalize_name(store_name), normalize_name(candidate)
    if not a or not b:
        return False
    return a in b or b in a


def fetch_naver_candidate(query: str, store_name: str, headers: dict) -> dict:
    resp = requests.get(
        NAVER_LOCAL_URL, headers=headers, params={"query": query, "display": 5}, timeout=10
    )
    resp.raise_for_status()
    for item in resp.json().get("items", []):
        title = strip_tags(item.get("title", ""))
        if name_matches(store_name, title):
            return {"title": title, "address": item.get("address", "") or item.get("roadAddress", "")}
    return {}


def fetch_kakao_candidate(query: str, store_name: str, headers: dict) -> dict:
    resp = requests.get(
        KAKAO_LOCAL_URL, headers=headers, params={"query": query, "size": 15}, timeout=10
    )
    resp.raise_for_status()
    for doc in resp.json().get("documents", []):
        place_name = doc.get("place_name", "")
        if name_matches(store_name, place_name):
            # address_name(지번주소)은 법정동명을 포함하지만 road_address_name(도로명주소)은
            # 보통 포함하지 않는다 (예: "자양로13길 98"에는 "자양동"이 없음) -> 지번주소를 우선.
            return {
                "title": place_name,
                "address": doc.get("address_name", "") or doc.get("road_address_name", ""),
            }
    return {}


def dong_consistent(store_dong: str, candidate_address: str) -> bool:
    if not store_dong or not candidate_address:
        return False
    return store_dong in candidate_address


def main() -> None:
    load_dotenv()
    naver_headers = {
        "X-NCP-APIGW-API-KEY-ID": os.environ["NAVER_CLIENT_ID"],
        "X-NCP-APIGW-API-KEY": os.environ["NAVER_CLIENT_SECRET"],
    }
    kakao_headers = {"Authorization": f"KakaoAK {os.environ['KAKAO_REST_API_KEY']}"}

    presence = pd.read_csv(PRESENCE_PATH, dtype=str)
    presence["naver_local_registered"] = presence["naver_local_registered"] == "True"
    presence["kakao_registered"] = presence["kakao_registered"] == "True"

    # presence CSV에 이미 dong 컬럼이 있으므로 sample과는 store_id만 대조 확인 용도로 남겨둔다.
    matched = presence[presence["naver_local_registered"] | presence["kakao_registered"]].copy()

    rows = []
    for i, (_, row) in enumerate(matched.iterrows(), start=1):
        query = row["query_used"]
        result = {"store_id": row["store_id"], "name": row["name"], "dong": row["dong"], "query_used": query}

        if row["naver_local_registered"]:
            cand = fetch_naver_candidate(query, row["name"], naver_headers)
            result["naver_candidate_title"] = cand.get("title", "")
            result["naver_candidate_address"] = cand.get("address", "")
            result["naver_dong_consistent"] = dong_consistent(row["dong"], cand.get("address", ""))
        else:
            result["naver_candidate_title"] = ""
            result["naver_candidate_address"] = ""
            result["naver_dong_consistent"] = ""

        if row["kakao_registered"]:
            cand = fetch_kakao_candidate(query, row["name"], kakao_headers)
            result["kakao_candidate_title"] = cand.get("title", "")
            result["kakao_candidate_address"] = cand.get("address", "")
            result["kakao_dong_consistent"] = dong_consistent(row["dong"], cand.get("address", ""))
        else:
            result["kakao_candidate_title"] = ""
            result["kakao_candidate_address"] = ""
            result["kakao_dong_consistent"] = ""

        rows.append(result)
        time.sleep(0.15)
        if i % 100 == 0:
            print(f"{i}/{len(matched)}")

    out = pd.DataFrame(rows)
    out.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    n_naver = (out["naver_candidate_title"] != "").sum()
    n_kakao = (out["kakao_candidate_title"] != "").sum()
    naver_precision = out.loc[out["naver_candidate_title"] != "", "naver_dong_consistent"].eq(True).mean()
    kakao_precision = out.loc[out["kakao_candidate_title"] != "", "kakao_dong_consistent"].eq(True).mean()

    print(f"-> {OUT_PATH}")
    print(f"naver: {n_naver}건 중 동 일치 {naver_precision:.1%}")
    print(f"kakao: {n_kakao}건 중 동 일치 {kakao_precision:.1%}")


if __name__ == "__main__":
    main()
