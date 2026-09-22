"""W1-4 사전 작업: 광진구 인허가 점포 목록 추출.

인허가 3종(일반음식점·휴게음식점·미용업) raw CSV에서 광진구 점포만 추려
온라인 존재감 수집 스크립트(collect_online_presence.py)의 입력으로 쓴다.

사용법:
    python src/data/build_gwangjin_targets.py
"""

import pandas as pd

RAW_FILES = {
    "일반음식점": "data/raw/인허가/서울시 일반음식점 인허가 정보.csv",
    "휴게음식점": "data/raw/인허가/식품_휴게음식점.csv",
    "미용업": "data/raw/인허가/생활_미용업.csv",
}

COLUMNS = ["관리번호", "사업장명", "도로명주소", "지번주소", "영업상태명", "폐업일자"]

OUT_PATH = "data/interim/gwangjin_targets.csv"


def load_gwangjin(biz_type: str, path: str) -> pd.DataFrame:
    # DATA_CATALOG.md: 일반음식점 파일은 CP949로 완전 디코딩되지 않는 바이트가 존재.
    df = pd.read_csv(
        path, encoding="cp949", encoding_errors="replace", usecols=COLUMNS, dtype=str
    )
    addr = df["도로명주소"].fillna("") + " " + df["지번주소"].fillna("")
    df = df[addr.str.contains("광진구")].copy()
    df["업종"] = biz_type
    return df


def main() -> None:
    parts = [load_gwangjin(name, path) for name, path in RAW_FILES.items()]
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
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"총 {len(combined)}건 -> {OUT_PATH}")
    print(combined.groupby(["업종", "status"]).size())


if __name__ == "__main__":
    main()
