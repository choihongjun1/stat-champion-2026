"""MDIS 전처리 실행 스크립트 (W1 A-3 2~7번).

로직은 담지 않는다 — src/data/mdis.py 함수를 순서대로 호출만 한다.
실행: python scripts/run_mdis_stage_a.py (프로젝트 루트에서)
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.data import config, mdis  # noqa: E402


def main() -> None:
    raw_path = config.get_mdis_raw_path()

    df = mdis.load_mdis_raw(raw_path)
    print(f"원본 로드: {len(df)}행")

    df = mdis.assign_row_id(df)
    df = mdis.filter_industry(df)
    print(f"산업중분류 필터 후: {len(df)}행 (검증 통과)")

    df = mdis.extract_role_columns(df)
    df = mdis.coerce_numeric_columns(df)
    mdis.validate_no_missing_required_columns(df)
    df = mdis.recode_yesno_columns(df)
    df = mdis.add_derived_features(df)
    print(f"is_seoul 매칭: {int(df['is_seoul'].sum())}건 (검증 통과)")

    df = mdis.apply_winsorize(df)
    df = mdis.build_treatment_vars(df)
    print(f"treat_binary 분포: {df['treat_binary'].value_counts().to_dict()} (검증 통과)")

    stage_a, stage_b = mdis.split_stage_ab(df)
    print(f"stage_a: {len(stage_a)}행 / stage_b: {len(stage_b)}행 (검증 통과)")

    config.MDIS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stage_a.to_parquet(config.MDIS_STAGE_A_PATH, index=False)
    stage_b.to_parquet(config.MDIS_STAGE_B_PATH, index=False)
    print(f"저장 완료: {config.MDIS_STAGE_A_PATH}")
    print(f"저장 완료: {config.MDIS_STAGE_B_PATH}")

    provenance = mdis.build_provenance(raw_path)
    mdis.write_provenance_json(provenance, config.MDIS_PROVENANCE_PATH)
    print(f"저장 완료: {config.MDIS_PROVENANCE_PATH}")

    rows = mdis.build_codebook_rows(stage_a)
    encoding_table = mdis.build_encoding_table()
    mdis.write_codebook_md(rows, encoding_table, config.MDIS_CODEBOOK_PATH)
    print(f"저장 완료: {config.MDIS_CODEBOOK_PATH}")

    missing_rates = stage_a.isna().mean() * 100
    high_missing = missing_rates[missing_rates > 30]
    if len(high_missing) > 0:
        print("\n결측률 30% 초과 변수 (A-4 팀 보고 대상):")
        print(high_missing.round(2).to_string())
    else:
        print("\n결측률 30% 초과 변수 없음.")


if __name__ == "__main__":
    main()
