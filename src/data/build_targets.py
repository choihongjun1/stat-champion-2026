"""W2-1 사전 작업: 3개 구(광진·마포·영등포) 인허가 점포 전체 목록 추출.

`main`의 `io_license.py`/`config.py`(개방자치단체코드 기준, 정본 모집단)를 그대로 써서
온라인 존재감 수집 스크립트(collect_online_presence.py)의 입력을 만든다.
store_id는 `standardize.py`와 동일한 규칙(`{prefix}_{관리번호}`, GR/SR/BT)을 따른다 —
PR #21 리뷰에서 지적된 두 문제(주소텍스트 기반 모집단, store_id 형식 불일치)를 반영한 버전.

사용법:
    python src/data/build_targets.py  (프로젝트 루트에서)
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.data import io_license  # noqa: E402
from src.data.config import BUSINESS_TYPES  # noqa: E402

OUT_PATH = "data/interim/all_targets.csv"


def build_store_id(biz_type: str, mgmt_no: str) -> str:
    # standardize.py와 동일한 규칙: store_id = "{prefix}_{관리번호}".
    return f"{BUSINESS_TYPES[biz_type]['prefix']}_{mgmt_no}"

# 네이버 검색 API는 키당 일 25,000회 한도라 전체(약 11만 곳)를 한 번에 못 돈다.
# 영업중 + 이 연도 이후 폐업 점포를 1순위로 먼저 수집한다 (오래전 폐업 점포는
# 광진구 시범에서 온라인 흔적이 거의 없는 것으로 확인됨).
PRIORITY1_CLOSED_FROM_YEAR = 2023


def load_targets(biz_type: str) -> pd.DataFrame:
    raw = io_license.load_license_raw(biz_type)
    df = io_license.filter_target_gu(raw)
    df["store_id"] = df["mgmt_no"].apply(lambda m: build_store_id(biz_type, m))
    return df


def main() -> None:
    parts = [load_targets(biz_type) for biz_type in BUSINESS_TYPES]
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.rename(
        columns={
            "name_raw": "name",
            "road_addr_raw": "addr_road",
            "addr_raw": "addr_jibun",
            "status_name": "status",
            "close_date_raw": "closed_date",
            "business_type": "업종",
            "gu": "구",
        }
    )

    closed_year = pd.to_datetime(combined["closed_date"], errors="coerce").dt.year
    is_active = combined["status"] == "영업/정상"
    combined["priority"] = ((is_active) | (closed_year >= PRIORITY1_CLOSED_FROM_YEAR)).map({True: 1, False: 2})

    out_cols = ["store_id", "name", "addr_road", "addr_jibun", "status", "closed_date", "구", "업종", "priority"]
    combined[out_cols].to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"총 {len(combined)}건 -> {OUT_PATH}")
    print(combined.groupby(["구", "업종", "status"]).size())
    print(combined["priority"].value_counts().sort_index())


if __name__ == "__main__":
    main()
