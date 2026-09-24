"""W2-1: 온라인 존재감 feature를 outputs/online/*.parquet으로 내보낸다.

temporal 메타데이터(feature_asof/available_at/source_snapshot)는 수집 스크립트가
아니라 이 내보내기 단계에서 붙인다 — 수집은 항상 "현재 시점"에 이뤄지므로, origin
시점 값 재구성이 필요한 판단은 수집이 아니라 여기서 한다 (이슈 #23). as-of 컷오프
(available_at > origin_end -> NA)는 이 스크립트가 하지 않고, W2-0 master 조인의
기존 규칙을 그대로 쓴다.

source_snapshot은 label_schema.py의 관례(값을 계산한 원천 파일 식별자, 날짜값이
아님)를 따른다 — 날짜는 이미 feature_asof/available_at에 있으므로 중복하지 않는다
(이슈 #23 정정: "수집일" 같은 날짜값은 source_snapshot으로 부적절).

사용법:
    python src/data/export_online_features.py
"""

import json
import os

import pandas as pd

PRESENCE_PATHS = [
    "data/interim/online_presence_all.csv",
    "data/interim/online_presence_remaining.csv",
]
MONTHLY_PATH = "data/interim/online_mentions_monthly.csv"
QA_PATH = "data/interim/online_blog_monthly_qa.csv"
MANIFEST_PATH = "data/raw/online/collection_manifest.jsonl"
RAW_BLOG_FILENAME = "blog_items.jsonl.gz"

OUT_DIR = "outputs/online"
OUT_PRESENCE = f"{OUT_DIR}/online_presence.parquet"
OUT_MONTHLY = f"{OUT_DIR}/online_mentions_monthly.parquet"


def load_manifest_git_sha(manifest_path: str) -> dict:
    """collection_run_id -> git_sha 매핑 (완료(status=completed)된 실행만)."""
    sha_by_run = {}
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("status") == "completed":
                sha_by_run[rec["collection_run_id"]] = rec.get("git_sha", "unknown")
    return sha_by_run


def build_presence() -> pd.DataFrame:
    parts = [pd.read_csv(p, dtype=str, keep_default_na=False) for p in PRESENCE_PATHS]
    df = pd.concat(parts, ignore_index=True)
    dup = df["store_id"].duplicated().sum()
    assert dup == 0, f"store_id 중복 {dup}건 - 축A 출력 파일이 겹친다"

    # 등록 여부·순위는 수집 시점의 "현재값" -> feature_asof/available_at을 항상
    # 모든 origin보다 늦게 만들어 과거 예측 feature로 못 쓰게 강제한다
    # (DECISIONS.md 2026-09-13 "현재 진단 표시용" 결정을 조인 규칙으로 강제).
    df["feature_asof"] = df["collected_at"]
    df["available_at"] = df["collected_at"]
    # 축A는 원본 API 응답을 보존하지 않는다(이슈 #24 스코프 밖, 출력 CSV 자체가 원천).
    df["source_snapshot"] = "collect_online_presence.py:online_presence_all+remaining"
    return df


def build_monthly() -> pd.DataFrame:
    monthly = pd.read_csv(MONTHLY_PATH, dtype=str, keep_default_na=False)
    qa = pd.read_csv(QA_PATH, dtype=str, keep_default_na=False)
    sha_by_run = load_manifest_git_sha(MANIFEST_PATH)

    run_by_store = qa.drop_duplicates("store_id").set_index("store_id")["collection_run_id"]
    monthly["collection_run_id"] = monthly["store_id"].map(run_by_store)
    monthly["git_sha"] = monthly["collection_run_id"].map(sha_by_run).fillna("unknown")

    # 게시월 말일을 기준시점으로 삼는다 (그 달이 다 지나야 그 달 언급 총량을
    # 확정적으로 알 수 있음). raw는 수집 시점에 전량 받았으므로 available_at도 동일값.
    period = pd.PeriodIndex(monthly["year_month"], freq="M")
    monthly["feature_asof"] = period.end_time.strftime("%Y-%m-%d")
    monthly["available_at"] = monthly["feature_asof"]
    monthly["source_snapshot"] = (
        RAW_BLOG_FILENAME
        + "@" + monthly["collection_run_id"].fillna("unknown")
        + "#" + monthly["git_sha"].str.slice(0, 12)
    )
    return monthly


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    presence = build_presence()
    presence.to_parquet(OUT_PRESENCE, index=False)
    print(f"{len(presence)}건 -> {OUT_PRESENCE}")

    monthly = build_monthly()
    monthly.to_parquet(OUT_MONTHLY, index=False)
    print(f"{len(monthly)}건 -> {OUT_MONTHLY}")


if __name__ == "__main__":
    main()
