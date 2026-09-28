"""W2-1: 온라인 존재감 feature를 outputs/online/*.parquet으로 내보낸다.

temporal 메타데이터(feature_asof/available_at/source_snapshot)는 수집 스크립트가
아니라 이 내보내기 단계에서 붙인다 — 수집은 항상 "현재 시점"에 이뤄지므로, origin
시점 값 재구성이 필요한 판단은 수집이 아니라 여기서 한다 (이슈 #23). as-of 컷오프
(available_at > origin_end -> NA)는 이 스크립트가 하지 않고, W2-0 master 조인의
기존 규칙을 그대로 쓴다.

source_snapshot은 label_schema.py의 관례(값을 계산한 원천 파일 식별자, 날짜값이
아님)를 따른다 — 날짜는 이미 feature_asof/available_at에 있으므로 중복하지 않는다
(이슈 #23 정정: "수집일" 같은 날짜값은 source_snapshot으로 부적절).

축B source_snapshot = `blog_items.jsonl.gz@sha256:<raw 64자>#run:<collection_run_id>#git:<git SHA 12자>`
- sha256은 export 시점에 존재하는 raw 파일(`blog_items.jsonl.gz`) **바이트 자체**의 해시다 (gzip 해제 아님).
  manifest의 `input_checksum_sha256`은 수집 **입력 대상 목록**(all_targets.csv)의 해시라 raw 식별에 쓰지 않는다.
- `--resume` 수집은 같은 raw 파일에 이어 쓰므로 run별 중간 raw는 남지 않는다. 그래서 run 종료 시점이 아니라
  export가 참조한 최종 raw 전체를 해시한다 — 지금 있는 파일로 언제든 다시 검증할 수 있는 값이다.
  한 행이 어느 run에서 왔는지는 `#run:`/`#git:`(과 별도 컬럼 collection_run_id·git_sha)이 맡는다.

사용법:
    python src/data/export_online_features.py
"""

import hashlib
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
RAW_BLOG_PATH = "data/raw/online/blog_items.jsonl.gz"  # collect_blog_monthly.py DEFAULT_RAW_OUT
RAW_BLOG_FILENAME = os.path.basename(RAW_BLOG_PATH)

OUT_DIR = "outputs/online"
OUT_PRESENCE = f"{OUT_DIR}/online_presence.parquet"
OUT_MONTHLY = f"{OUT_DIR}/online_mentions_monthly.parquet"


def file_sha256(path: str) -> str:
    """파일 바이트의 sha256 (collect_blog_monthly.file_checksum과 같은 방식, 스트리밍)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest_git_sha(manifest_path: str) -> dict:
    """collection_run_id -> git_sha 매핑.

    git_sha는 한 run의 모든 기록(started/interrupted_*/completed)에 같은 값으로 남는다
    (collect_blog_monthly.py의 manifest_base). --resume으로 이어 받은 수집에서는 중단된 run의
    QA·월별 행이 그대로 남으므로 completed 기록만 보면 그 행의 SHA를 잃는다 -> 모든 기록에서 읽고,
    같은 run에 서로 다른 SHA가 있으면 멈춘다.
    """
    sha_by_run = {}
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            run, sha = rec["collection_run_id"], rec.get("git_sha", "")
            if run in sha_by_run and sha_by_run[run] != sha:
                raise ValueError(f"manifest에서 run {run}의 git_sha가 서로 다르다: {sha_by_run[run]} / {sha}")
            sha_by_run[run] = sha
    return sha_by_run


def _to_naive_kst(s: pd.Series) -> pd.Series:
    """UTC ISO8601 문자열을 KST 기준 tz-naive datetime으로 바꾼다.

    master의 시점 검사(`available_at > origin_end`)는 tz-naive datetime끼리 비교한다
    (`src/data/master.py`). tz-aware 값을 그대로 넘기면 TypeError로 검사 자체가 죽으므로
    여기서 맞춰준다. tz 표기가 없는 값은 UTC로 간주한다(수집 스크립트가 UTC로만 기록).

    값이 비면 available_at 없이 통과해버리는 fail-open이 되므로 그대로 실패시킨다
    (master는 available_at 없는 값을 시점 위반으로 센다).
    """
    out = pd.to_datetime(s, utc=True, errors="coerce")
    if out.isna().any():
        bad = int(out.isna().sum())
        raise ValueError(f"collected_at 파싱 실패 {bad}건 - available_at을 만들 수 없다")
    return out.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)


def build_presence() -> pd.DataFrame:
    parts = [pd.read_csv(p, dtype=str, keep_default_na=False) for p in PRESENCE_PATHS]
    df = pd.concat(parts, ignore_index=True)
    dup = df["store_id"].duplicated().sum()
    assert dup == 0, f"store_id 중복 {dup}건 - 축A 출력 파일이 겹친다"

    # 등록 여부·순위는 수집 시점의 "현재값" -> feature_asof/available_at을 항상
    # 모든 origin보다 늦게 만들어 과거 예측 feature로 못 쓰게 강제한다
    # (DECISIONS.md 2026-09-13 "현재 진단 표시용" 결정을 조인 규칙으로 강제).
    #
    # collected_at은 수집 스크립트가 UTC ISO8601(tz 포함)로 남긴다. 그대로 두면
    # tz-aware가 되어 master의 `available_at > origin_end` 비교에서
    # "Cannot compare tz-naive and tz-aware timestamps" TypeError가 난다
    # (labels/landprice/trdar의 available_at은 모두 tz-naive). 한국 기준 달력일로
    # 맞춰야 분기말(origin_end)과 같은 기준이 되므로 KST로 변환한 뒤 tz를 떼어낸다.
    # 시각 정보는 버리지 않는다 - 날짜 의미를 바꾸지 않기 위해서다.
    df["feature_asof"] = _to_naive_kst(df["collected_at"])
    df["available_at"] = df["feature_asof"]
    # 축A는 원본 API 응답을 보존하지 않는다(이슈 #24 스코프 밖, 출력 CSV 자체가 원천).
    df["source_snapshot"] = "collect_online_presence.py:online_presence_all+remaining"
    return df


def build_monthly(monthly_path: str = MONTHLY_PATH, qa_path: str = QA_PATH,
                  manifest_path: str = MANIFEST_PATH, raw_path: str = RAW_BLOG_PATH) -> pd.DataFrame:
    monthly = pd.read_csv(monthly_path, dtype=str, keep_default_na=False)
    qa = pd.read_csv(qa_path, dtype=str, keep_default_na=False)
    sha_by_run = load_manifest_git_sha(manifest_path)

    run_by_store = qa.drop_duplicates("store_id").set_index("store_id")["collection_run_id"]
    monthly["collection_run_id"] = monthly["store_id"].map(run_by_store)
    monthly["git_sha"] = monthly["collection_run_id"].map(sha_by_run)
    # provenance가 빠진 행을 "unknown"으로 내보내면 source_snapshot이 원천을 식별하지 못한다 -> 멈춘다 (fail-closed)
    no_run = monthly["collection_run_id"].fillna("").eq("")
    if no_run.any():
        raise ValueError(f"QA에 collection_run_id가 없는 월별 행 {int(no_run.sum())}건 - source_snapshot을 만들 수 없다")
    no_sha = monthly["git_sha"].fillna("").isin(["", "unknown"])
    if no_sha.any():
        runs = sorted(monthly.loc[no_sha, "collection_run_id"].unique())
        raise ValueError(f"manifest에 git SHA가 없는 run {runs} ({int(no_sha.sum())}행) - source_snapshot을 만들 수 없다")
    raw_sha = file_sha256(raw_path)

    # 게시월 말일을 기준시점으로 삼는다 (그 달이 다 지나야 그 달 언급 총량을
    # 확정적으로 알 수 있음). raw는 수집 시점에 전량 받았으므로 available_at도 동일값.
    period = pd.PeriodIndex(monthly["year_month"], freq="M")
    monthly["feature_asof"] = period.end_time.strftime("%Y-%m-%d")
    monthly["available_at"] = monthly["feature_asof"]
    monthly["source_snapshot"] = (
        os.path.basename(raw_path) + "@sha256:" + raw_sha
        + "#run:" + monthly["collection_run_id"]
        + "#git:" + monthly["git_sha"].str.slice(0, 12)
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
