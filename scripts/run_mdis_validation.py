"""MDIS 검증 표 작성 실행 스크립트 (W1 A-4).

로직은 담지 않는다 — src/data/mdis_validation.py 함수를 순서대로 호출만 한다.
실행 전 scripts/run_mdis_stage_a.py로 mdis_stage_a/b.parquet이 먼저 생성되어 있어야 한다.
실행: python scripts/run_mdis_validation.py (프로젝트 루트에서)
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from src.data import config, mdis_validation  # noqa: E402


def main() -> None:
    if not config.MDIS_STAGE_A_PATH.exists() or not config.MDIS_STAGE_B_PATH.exists():
        raise RuntimeError(
            f"{config.MDIS_STAGE_A_PATH} / {config.MDIS_STAGE_B_PATH}가 없습니다. "
            "먼저 python scripts/run_mdis_stage_a.py를 실행하세요."
        )

    stage_a = pd.read_parquet(config.MDIS_STAGE_A_PATH)
    stage_b = pd.read_parquet(config.MDIS_STAGE_B_PATH)
    print(f"mdis_stage_a 로드: {len(stage_a)}행 / mdis_stage_b 로드: {len(stage_b)}행")

    missing_rates = mdis_validation.compute_missing_rates(stage_a)
    high_missing = missing_rates[missing_rates > 30]
    if len(high_missing) > 0:
        print(f"\n결측률 30% 초과 변수 {len(high_missing)}개 (A-4 팀 보고 대상):")
        print(high_missing.to_string())
    else:
        print("\n결측률 30% 초과 변수 없음.")

    weight = mdis_validation.coerce_weight_column(stage_a)

    balance_df = mdis_validation.compute_covariate_balance(stage_a)
    n_flagged = int(balance_df["flag"].sum())
    print(f"\n공변량 균형(SMD) 계산 완료: {len(balance_df)}행, |SMD|>0.1 플래그 {n_flagged}건")
    print(balance_df.to_string(index=False))

    propensity = mdis_validation.fit_propensity_scores(stage_a)
    n_excluded_ps = int(propensity.isna().sum())
    mdis_validation.plot_propensity_overlap(
        propensity, stage_a["treat_binary"], config.MDIS_PROPENSITY_FIGURE_PATH
    )
    print(f"\n성향점수 모델 적합 완료 (결측 제외 {n_excluded_ps}건)")
    print(f"저장 완료: {config.MDIS_PROPENSITY_FIGURE_PATH}")

    profit_df = mdis_validation.compute_weighted_unweighted_profit(stage_a, weight)
    print("\n가중치 적용 전후 영업이익률(profit_margin) 비교:")
    print(profit_df.to_string(index=False))

    assoc_df = mdis_validation.compute_stage_b_treat_cont_association(stage_b)
    n_concentration_flagged = int((assoc_df["flag"] == True).sum())  # noqa: E712
    print(f"\nmdis_stage_b treat_cont 연관성/쏠림 계산 완료 (쏠림 플래그 {n_concentration_flagged}건):")
    print(assoc_df.to_string(index=False))

    section_md = mdis_validation.render_validation_report_md(
        missing_rates=missing_rates,
        balance_df=balance_df,
        propensity_fig_rel_path="../outputs/figures/mdis_propensity_overlap.png",
        n_excluded_ps=n_excluded_ps,
        profit_df=profit_df,
        assoc_df=assoc_df,
    )
    mdis_validation.append_validation_section_md(section_md, config.MDIS_CODEBOOK_PATH)
    print(f"\n저장 완료: {config.MDIS_CODEBOOK_PATH} (## A-4 검증 결과 절 갱신)")
    print(
        "참고: A-4 절과 수기 메모 블록은 run_mdis_stage_a.py 재실행에도 보존된다 "
        "(mdis.write_codebook_md가 마커 블록을 유지)."
    )


if __name__ == "__main__":
    main()
