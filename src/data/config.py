"""프로젝트 데이터 경로 설정.

원본 데이터 루트는 `PROJECT_DATA_ROOT` 환경변수(.env)가 있으면 그 값을, 없으면
`<repo>/data`를 사용한다. 두 경우 모두 하위 원본 디렉터리명은 `00_raw`로 고정한다.
경로 존재 검사는 import 시점이 아니라 실제 파일이 필요한 `get_*_path()`에서만 한다
(원본 데이터가 없는 환경에서도 import·테스트가 가능해야 하므로).
"""

from pathlib import Path

from dotenv import load_dotenv
import os

# 프로젝트 루트: 이 파일 기준 src/data/config.py -> src/data -> src -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


def _resolve_data_root() -> Path:
    """원본 데이터 루트. MDIS·인허가·소진공 경로가 공유하는 단일 출처다."""
    raw = os.environ.get("PROJECT_DATA_ROOT")
    if raw:
        return Path(raw)
    return REPO_ROOT / "data"


def get_project_data_root() -> Path:
    """`_resolve_data_root()`와 같은 규칙으로 해석하되 존재 여부까지 검사한다."""
    root = _resolve_data_root()
    if not root.exists():
        raise RuntimeError(
            f"원본 데이터 루트를 찾을 수 없습니다: {root} — "
            f"{REPO_ROOT / '.env'}에 PROJECT_DATA_ROOT=<raw 데이터 루트 절대경로>를 "
            "설정하거나 <repo>/data 아래에 원본을 두세요."
        )
    return root


def get_mdis_raw_path() -> Path:
    path = get_project_data_root() / "00_raw" / "MDIS" / "2023_연간자료_등록기반_20260910_85675.csv"
    if not path.exists():
        raise RuntimeError(f"MDIS 원본 파일을 찾을 수 없습니다: {path}")
    return path


MDIS_OUTPUT_DIR = REPO_ROOT / "outputs" / "mdis"
MDIS_STAGE_A_PATH = MDIS_OUTPUT_DIR / "mdis_stage_a.parquet"
MDIS_STAGE_B_PATH = MDIS_OUTPUT_DIR / "mdis_stage_b.parquet"
MDIS_CODEBOOK_PATH = REPO_ROOT / "docs" / "MDIS_CODEBOOK.md"
MDIS_PROVENANCE_PATH = MDIS_OUTPUT_DIR / "mdis_provenance.json"

FIGURES_OUTPUT_DIR = REPO_ROOT / "outputs" / "figures"
MDIS_PROPENSITY_FIGURE_PATH = FIGURES_OUTPUT_DIR / "mdis_propensity_overlap.png"


# ---------------------------------------------------------------------------
# 원본 데이터 경로 (인허가 표준화/ER/공간조인 파이프라인 공용).
# 경로 해석 규칙은 위 `_resolve_data_root()` 하나만 쓴다 — MDIS 경로와 동일 출처.
# 여기서는 경로 존재를 검사하지 않는다(원본 없는 환경에서도 import 가능해야 함).
# ---------------------------------------------------------------------------
# RAW_DIR/LICENSE_DIR/SEMAS_DIR을 함수가 아니라 상수로 유지하는 이유:
# feature/address-normalization의 6개 파일(address.py, io_license.py,
# standardize.py, semas.py, landprice.py, spatial.py 등)이 이 상수들을
# 함수 기본 인자(예: def build_bjd_mapping(semas_dir=SEMAS_DIR))로 쓰고
# 있어서, 함수로 바꾸면 그 파일들을 전부 고쳐야 한다.
RAW_DIR = _resolve_data_root() / "00_raw"
LICENSE_DIR = RAW_DIR / "인허가"
SEMAS_DIR = RAW_DIR / "소진공_상가정보"
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


# ---------------------------------------------------------------------------
# 라벨 파이프라인(W1 B-2) 산출물 경로.
# 인허가 원본 파일명·prefix는 위 BUSINESS_TYPES가 단일 출처다 — 여기서 따로 정의하지 않는다.
# ---------------------------------------------------------------------------
def get_licensing_raw_path(business_type: str) -> Path:
    """인허가 원본 CSV 경로. 파일명은 `BUSINESS_TYPES`에서만 가져온다."""
    if business_type not in BUSINESS_TYPES:
        raise RuntimeError(f"알 수 없는 인허가 business_type: {business_type}")
    path = LICENSE_DIR / BUSINESS_TYPES[business_type]["file"]
    if not path.exists():
        raise RuntimeError(f"인허가 원본 파일을 찾을 수 없습니다: {path}")
    return path


LABELS_OUTPUT_DIR = REPO_ROOT / "outputs" / "labels"
LABELS_BASE_PATH = LABELS_OUTPUT_DIR / "labels_base.parquet"
MATURITY_TAIL_FIGURE_PATH = FIGURES_OUTPUT_DIR / "closure_maturity_tail.png"
KM_GWANGJIN_FIGURE_PATH = FIGURES_OUTPUT_DIR / "km_gwangjin.png"
LABEL_SPEC_PATH = REPO_ROOT / "docs" / "LABEL_SPEC.md"

# W2-0 master dataset 산출물 경로.
MASTER_OUTPUT_DIR = REPO_ROOT / "outputs" / "master"
MASTER_BASE_PATH = MASTER_OUTPUT_DIR / "master_base.parquet"
MASTER_QA_REPORT_PATH = MASTER_OUTPUT_DIR / "qa_report.md"
MASTER_SPEC_PATH = REPO_ROOT / "docs" / "MASTER_SPEC.md"
# 예측용 master (Issue #35, `python -m src.data.master_score`): 라벨 없는 단일 origin 패널.
MASTER_SCORE_PATH = MASTER_OUTPUT_DIR / "master_score.parquet"
MASTER_SCORE_META_PATH = MASTER_OUTPUT_DIR / "score_meta.json"
MASTER_SCORE_QA_REPORT_PATH = MASTER_OUTPUT_DIR / "qa_report_score.md"
