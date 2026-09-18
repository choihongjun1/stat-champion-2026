"""프로젝트 데이터 경로 설정.

`PROJECT_DATA_ROOT` 환경변수(.env)를 기준으로 원본/산출물 경로를 계산한다.
미설정 시 값을 임의로 추정하지 않고 즉시 에러를 발생시킨다.
"""

from pathlib import Path

from dotenv import load_dotenv
import os

# 프로젝트 루트: 이 파일 기준 src/data/config.py -> src/data -> src -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


def get_project_data_root() -> Path:
    raw = os.environ.get("PROJECT_DATA_ROOT")
    if not raw:
        raise RuntimeError(
            "PROJECT_DATA_ROOT 환경변수가 설정되어 있지 않습니다. "
            f"{REPO_ROOT / '.env'} 파일에 PROJECT_DATA_ROOT=<raw 데이터 루트 절대경로>를 설정하세요."
        )
    root = Path(raw)
    if not root.exists():
        raise RuntimeError(f"PROJECT_DATA_ROOT 경로가 존재하지 않습니다: {root}")
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


LICENSING_SOURCE_FILENAMES = {
    "미용업": "생활_미용업.csv",
    # 실제 파일명. docs/W1_MDIS_AND_LABEL.md §B-2는 "식품_일반음식점.csv"라고 되어 있으나
    # 실측 파일명과 다르다 (실측 확인: 2026-09-18) - 문서 정정은 별도 후속 작업.
    "일반음식점": "서울시 일반음식점 인허가 정보.csv",
    "휴게음식점": "식품_휴게음식점.csv",
}


def get_licensing_raw_path(source_type: str) -> Path:
    if source_type not in LICENSING_SOURCE_FILENAMES:
        raise RuntimeError(f"알 수 없는 인허가 source_type: {source_type}")
    path = (
        get_project_data_root()
        / "00_raw"
        / "인허가"
        / LICENSING_SOURCE_FILENAMES[source_type]
    )
    if not path.exists():
        raise RuntimeError(f"인허가 원본 파일을 찾을 수 없습니다: {path}")
    return path


LABELS_OUTPUT_DIR = REPO_ROOT / "outputs" / "labels"
LABELS_BASE_PATH = LABELS_OUTPUT_DIR / "labels_base.parquet"
FIGURES_OUTPUT_DIR = REPO_ROOT / "outputs" / "figures"
MATURITY_TAIL_FIGURE_PATH = FIGURES_OUTPUT_DIR / "closure_maturity_tail.png"
KM_GWANGJIN_FIGURE_PATH = FIGURES_OUTPUT_DIR / "km_gwangjin.png"
LABEL_SPEC_PATH = REPO_ROOT / "docs" / "LABEL_SPEC.md"
