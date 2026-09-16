# -*- coding: utf-8 -*-
"""인허가 3종 표준화 파이프라인 상수 정의.

경로·컬럼 매핑·좌표계 등 파이프라인 전반에서 공유하는 설정.
raw 파일의 컬럼 구성이 업종별로 다르므로(39/39/37열) 위치 기반 접근을 금지하고
여기 정의한 컬럼명 매핑만 사용한다.
"""
from __future__ import annotations

from pathlib import Path

# 이 파일 기준 repo root (src/data/config.py -> repo)
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
LICENSE_DIR = RAW_DIR / "인허가"
SEMAS_DIR = RAW_DIR / "소진공_상가정보"  # 소상공인시장진흥공단 상가(상권)정보
OUTPUT_DIR = REPO_ROOT / "outputs" / "standardized"

# ---------------------------------------------------------------------------
# 대상 지역: 서울 광진구·마포구·영등포구
# 필터 기준은 개방자치단체코드(관할 자치단체). 지번주소의 구 표기와 99.99% 일치함을
# 사전 검증했고, 불일치 행은 삭제하지 않고 parse_status로 플래그한다.
# ---------------------------------------------------------------------------
TARGET_GU_CODES: dict[str, str] = {
    "3040000": "광진구",
    "3130000": "마포구",
    "3180000": "영등포구",
}
TARGET_GUS: tuple[str, ...] = ("광진구", "마포구", "영등포구")

# 소진공 법정동코드(10자리) 앞 5자리 = 시군구코드 (사전 검증: 3구 1:1 대응)
TARGET_SGG_CODES: dict[str, str] = {
    "광진구": "11215",
    "마포구": "11440",
    "영등포구": "11560",
}

# ---------------------------------------------------------------------------
# 업종별 raw 파일과 store_id prefix
# store_id = "{prefix}_{관리번호}" — 관리번호는 파일 내 유일(사전 검증 중복 0건)이며
# prefix로 업종 간 충돌을 차단한다. raw 재다운로드에도 불변인 결정적 규칙.
# ---------------------------------------------------------------------------
BUSINESS_TYPES: dict[str, dict[str, str]] = {
    "일반음식점": {
        "file": "서울시 일반음식점 인허가 정보.csv",
        "prefix": "GR",  # general restaurant
    },
    "휴게음식점": {
        "file": "식품_휴게음식점.csv",
        "prefix": "SR",  # snack/rest restaurant
    },
    "미용업": {
        "file": "생활_미용업.csv",
        "prefix": "BT",  # beauty
    },
}

RAW_ENCODING = "cp949"
# 일반음식점 파일에 CP949로 디코딩되지 않는 바이트 존재(audit 확인) → U+FFFD로 치환하고
# QA에서 치환 발생 건수를 집계한다. raw 파일 자체는 수정하지 않는다.
RAW_ENCODING_ERRORS = "replace"

# ---------------------------------------------------------------------------
# 컬럼 매핑: 표준 컬럼명 -> 업종별 raw 컬럼명
# 최종수정/데이터갱신 컬럼명이 일반음식점(…일자)과 나머지(…시점)에서 다르다.
# ---------------------------------------------------------------------------
COMMON_COLUMNS: dict[str, str] = {
    "mgmt_no": "관리번호",
    "gov_code": "개방자치단체코드",
    "license_date_raw": "인허가일자",
    "close_date_raw": "폐업일자",
    "status_code": "영업상태코드",
    "status_name": "영업상태명",
    "detail_status_code": "상세영업상태코드",
    "detail_status_name": "상세영업상태명",
    "name_raw": "사업장명",
    "uptae_raw": "업태구분명",
    "addr_raw": "지번주소",
    "road_addr_raw": "도로명주소",
    "x_raw": "좌표정보(X)",
    "y_raw": "좌표정보(Y)",
    "update_type": "데이터갱신구분",
}

# 업종별로 이름이 다른 컬럼
PER_TYPE_COLUMNS: dict[str, dict[str, str]] = {
    "일반음식점": {
        "last_modified_raw": "최종수정일자",
        "data_updated_raw": "데이터갱신일자",
    },
    "휴게음식점": {
        "last_modified_raw": "최종수정시점",
        "data_updated_raw": "데이터갱신시점",
    },
    "미용업": {
        "last_modified_raw": "최종수정시점",
        "data_updated_raw": "데이터갱신시점",
    },
}

# ---------------------------------------------------------------------------
# 좌표계
# raw 좌표는 EPSG:5174 (audit 확인). 표준화 좌표는 EPSG:5179 (Korea 2000 / Unified CS).
# 컬럼명은 x/y로 유지한다 (lon/lat 아님 — 투영좌표계이므로).
# ---------------------------------------------------------------------------
CRS_RAW = "EPSG:5174"
CRS_STD = "EPSG:5179"

# 서울 대략 경계 (EPSG:4326): sanity check 용. 변환 후 5179 bbox는 coords.py에서
# 이 경계를 pyproj로 변환해 계산한다. 여유를 두고 약간 넓게 잡는다.
SEOUL_BBOX_4326 = {
    "lon_min": 126.70,
    "lon_max": 127.30,
    "lat_min": 37.38,
    "lat_max": 37.75,
}
