"""W2-1 사전 작업: 3개 구(광진·마포·영등포) 인허가 점포 전체 목록 추출.

W1-4 build_gwangjin_targets.py를 3개 구로 일반화한 버전.
인허가 3종(일반음식점·휴게음식점·미용업) raw CSV에서 대상 구 점포를 모두 추려
온라인 존재감 수집 스크립트(collect_online_presence.py)의 입력으로 쓴다.
(광진구 1,000건 표본은 W1-4 시범용이었고, 이번엔 3개 구 전체 모집단을 뽑는다.)

사용법:
    python src/data/build_targets.py
"""

import pandas as pd

RAW_FILES = {
    "일반음식점": "data/raw/인허가/서울시 일반음식점 인허가 정보.csv",
    "휴게음식점": "data/raw/인허가/식품_휴게음식점.csv",
    "미용업": "data/raw/인허가/생활_미용업.csv",
}

TARGET_GU = ["광진구", "마포구", "영등포구"]

COLUMNS = ["관리번호", "사업장명", "도로명주소", "지번주소", "영업상태명", "폐업일자"]

OUT_PATH = "data/interim/all_targets.csv"

# 네이버 검색 API는 키당 일 25,000회 한도라 전체(약 11만 곳)를 한 번에 못 돈다.
# 영업중 + 이 연도 이후 폐업 점포를 1순위로 먼저 수집한다 (오래전 폐업 점포는
# 광진구 시범에서 온라인 흔적이 거의 없는 것으로 확인됨).
PRIORITY1_CLOSED_FROM_YEAR = 2023


def extract_gu(addr: str) -> str:
    for gu in TARGET_GU:
        if gu in addr:
            return gu
    return ""


def load_targets(biz_type: str, path: str) -> pd.DataFrame:
    # DATA_CATALOG.md: 일반음식점 파일은 CP949로 완전 디코딩되지 않는 바이트가 존재.
    df = pd.read_csv(
        path, encoding="cp949", encoding_errors="replace", usecols=COLUMNS, dtype=str
    )
    addr = df["도로명주소"].fillna("") + " " + df["지번주소"].fillna("")
    gu = addr.apply(extract_gu)
    df = df[gu != ""].copy()
    df["구"] = gu[gu != ""]
    df["업종"] = biz_type
    return df


def main() -> None:
    parts = [load_targets(name, path) for name, path in RAW_FILES.items()]
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.rename(
        columns={
            "관리번호": "store_id",
            "사업장명": "name",
            "도로명주소": "addr_road",
            "지번주소": "addr_jibun",
            "영업상태명": "status",
            "폐업일자": "closed_date",
        }
    )

    closed_year = pd.to_datetime(combined["closed_date"].str.strip(), errors="coerce").dt.year
    is_active = combined["status"] == "영업/정상"
    combined["priority"] = ((is_active) | (closed_year >= PRIORITY1_CLOSED_FROM_YEAR)).map({True: 1, False: 2})
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"총 {len(combined)}건 -> {OUT_PATH}")
    print(combined.groupby(["구", "업종", "status"]).size())
    print(combined["priority"].value_counts().sort_index())


if __name__ == "__main__":
    main()
