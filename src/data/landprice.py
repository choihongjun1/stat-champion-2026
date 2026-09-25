# -*- coding: utf-8 -*-
"""개별공시지가(2024·2025·2026) 결합.

원칙:
- 토지코드(=PNU)는 문자열로 처리하고 leading zero를 보존하며 19자리를 검증한다.
- 19자리가 아닌 불량 코드는 제외하고 건수를 기록한다 (DATA_CATALOG 실측: 연 ~200건).
- 동일 PNU 중복은 원인을 조사한다: 값까지 동일하면 안전 dedup,
  값이 다르면 임의 선택하지 않고 조인에서 제외 + 건수 보고.
- 인허가 점포와는 left join — 미매칭 점포를 삭제하지 않는다.

시간 누수 주의 (매우 중요):
- `feature_asof`(가격의 기준일, 1월 1일)와 `available_at`(그 값을 예측자가 실제로 알 수
  있게 된 결정·공시일)은 **반드시 별개로 유지한다**. 둘을 혼동하면 leakage가 된다.
- prediction origin이 해당 연도의 `available_at` 이전이면 그 연도 land_price는 사용 금지다.
  예) origin 2026-03-31 → land_price_2026 사용 금지(당시 미공시), 이전 연도 값 사용.
      origin >= 2026-04-30 → land_price_2026 사용 가능.
- origin별 feature selection 로직은 이 모듈(및 이 PR)의 범위가 아니다. 아래 metadata를
  W2가 판단 근거로 사용한다.

0원 필지 (Issue #9, 2026-09-22 조사):
- 2026년에만 0원이 나온다 (2024·2025는 0건). 2026 raw 339행 중 336행이 19자리 PNU다.
- 그 339행 중 311행은 2025년에 0보다 큰 공시지가가 있었다(중앙값 247.6만원/㎡). 즉 "아직 평가되지
  않은 신규 필지"로 설명되지 않는다. 2026 신규 PNU 1,782건 중 0원은 28건(1.6%)뿐이다.
- 따라서 0은 유효한 지가가 아니라 해당 연도 값이 비어 있는 상태로 본다.
- **raw 값은 그대로 보존**하고(`land_price_{year}`), 대신 `land_price_zero_flag`와
  연도별 `land_price_{year}_valid`를 함께 제공한다. feature로는 valid 값만 쓴다.
- 0이 된 행정적 사유(공시 제외·유보 등)는 데이터만으로 확정할 수 없다 — 배포처 확인 대상.
"""
from __future__ import annotations

import pandas as pd

from src.data.config import RAW_DIR

LANDPRICE_DIR = RAW_DIR / "공시지가"
YEARS = (2024, 2025, 2026)
LANDPRICE_FILE = "공시지가_{year}년.csv"  # 연도별 raw 파일명 (source_snapshot으로도 쓴다)

# available_at(결정·공시일)의 원 출처. 서울시 「연도별 개별공시지가 결정·공시」 보도자료.
# 2024년분은 서울시 원 페이지에 직접 접근되지 않아 국회도서관 지방의정포털(CLIK)에
# 보존된 서울특별시청 원 보도자료를 근거로 사용한다. 상세는 docs/DATA_CATALOG.md 참조.
AVAILABLE_AT_SOURCES = {
    2024: {
        "title": "서울시 「2024년도 개별공시지가 결정·공시」",
        "url": ("https://clik.nanet.go.kr/potal/search/searchView.do"
                "?DOCID=CLIKC1085450841277470&collection=policyinfo"),
        "note": "국회도서관 지방의정포털 보존본 (서울시 원 페이지 직접 접근 불가)",
    },
    2025: {
        "title": "서울시 「2025년도 개별공시지가 결정·공시」",
        "url": "https://www.seoul.go.kr/news/news_report.do?nttNo=434593&tr_code=snews",
        "note": "서울시 보도자료 원문",
    },
    2026: {
        "title": "서울시 「2026년도 개별공시지가 결정·공시」",
        "url": "https://www.seoul.go.kr/news/news_report.do?nttNo=457142&srchCtgry=465",
        "note": "서울시 보도자료 원문",
    },
}

# reference_year → 시점 메타데이터.
# feature_asof: 가격의 기준일 (raw 기준년월 실측값)
# available_at: 결정·공시일 = 그 값을 현실에서 이용할 수 있게 된 날짜
LANDPRICE_META = {
    y: {
        "reference_year": y,
        "feature_asof": f"{y}-01-01",
        "available_at": f"{y}-04-30",
        "available_at_source": AVAILABLE_AT_SOURCES[y]["title"],
        "available_at_source_url": AVAILABLE_AT_SOURCES[y]["url"],
        "available_at_source_note": AVAILABLE_AT_SOURCES[y]["note"],
    }
    for y in YEARS
}


def load_landprice_year(year: int) -> tuple[pd.DataFrame, dict]:
    """연도 파일 로드 → (pnu, land_price_{year}) 테이블 + QA dict.

    반환 테이블의 pnu는 19자리 검증·중복 처리 완료 상태 (조인 안전).
    """
    path = LANDPRICE_DIR / LANDPRICE_FILE.format(year=year)
    df = pd.read_csv(path, dtype=str, encoding="cp949",
                     usecols=["토지코드", "공시지가(원/㎡)", "기준년도", "기준년월"])
    qa: dict = {"year": year, "rows": len(df)}

    ref_years = df["기준년도"].dropna().unique().tolist()
    qa["reference_years_in_file"] = ref_years
    qa["reference_months_in_file"] = df["기준년월"].dropna().unique().tolist()[:3]

    pnu = df["토지코드"].fillna("").str.strip()
    valid_pnu = (pnu.str.len() == 19) & pnu.str.isdigit()
    qa["invalid_pnu"] = int((~valid_pnu).sum())
    qa["invalid_pnu_len_dist"] = (
        pnu[~valid_pnu].str.len().value_counts().to_dict()
    )
    df = df[valid_pnu].copy()
    df["pnu"] = pnu[valid_pnu]

    price = pd.to_numeric(df["공시지가(원/㎡)"], errors="coerce")
    df["price"] = price
    qa["price_nan"] = int(price.isna().sum())
    qa["price_zero"] = int((price == 0).sum())
    qa["price_negative"] = int((price < 0).sum())
    qa["price_median"] = float(price.median())
    qa["price_p99"] = float(price.quantile(0.99))
    qa["price_max"] = float(price.max())

    # 중복 PNU 조사
    dup_mask = df["pnu"].duplicated(keep=False)
    qa["dup_pnu_rows"] = int(dup_mask.sum())
    if dup_mask.any():
        dup = df[dup_mask]
        # 같은 PNU에서 가격까지 동일하면 안전 dedup 가능
        conflict = dup.groupby("pnu")["price"].nunique()
        conflict_pnus = set(conflict[conflict > 1].index)
        qa["dup_pnu_unique"] = int(dup["pnu"].nunique())
        qa["dup_pnu_conflicting_values"] = len(conflict_pnus)
        # 값 동일 중복 → dedup / 값 상이 중복 → 조인에서 제외 (임의 선택 금지)
        df = df[~df["pnu"].isin(conflict_pnus)]
        df = df.drop_duplicates("pnu")
        qa["excluded_conflicting_pnus"] = len(conflict_pnus)
    else:
        qa["dup_pnu_unique"] = 0
        qa["dup_pnu_conflicting_values"] = 0
        qa["excluded_conflicting_pnus"] = 0
        df = df.drop_duplicates("pnu")

    qa["unique_pnu"] = int(df["pnu"].nunique())
    out = df[["pnu", "price"]].rename(columns={"price": f"land_price_{year}"})
    return out, qa


def join_landprice(stores: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """인허가 점포(pnu 컬럼)에 3개년 공시지가를 wide로 left join한다."""
    out = stores.copy()
    qas = []
    for y in YEARS:
        tbl, qa = load_landprice_year(y)
        before = len(out)
        out = out.merge(tbl, on="pnu", how="left")
        assert len(out) == before, f"{y} 조인에서 행 수 변화 — PNU 중복 방어 실패"
        matched = out[f"land_price_{y}"].notna()
        qa["store_match_all"] = float(matched.mean())
        has_pnu = out["pnu"].notna()
        qa["store_match_with_pnu"] = float(matched[has_pnu].mean())
        qas.append(qa)
    price_cols = [f"land_price_{y}" for y in YEARS]
    out["land_price_match"] = out[price_cols].notna().any(axis=1)

    # 0원은 유효 지가가 아니다 (Issue #9). raw는 그대로 두고 valid 파생값과 flag를 붙인다.
    for y in YEARS:
        out[f"land_price_{y}_valid"] = out[f"land_price_{y}"].where(
            out[f"land_price_{y}"] > 0)
    out["land_price_zero_flag"] = (out[price_cols] == 0).any(axis=1)
    return out, qas
