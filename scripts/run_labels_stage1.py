"""인허가 단독 패널 실행 스크립트 (W1 §B-2 단계 1).

로직은 담지 않는다 - src/data/labels.py 함수를 순서대로 호출만 한다.
실행: python scripts/run_labels_stage1.py (프로젝트 루트에서)
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.data import config, labels
from src.data import label_schema as schema


def main() -> None:
    exclusion_rows = []
    standardized = {}

    for source_type in schema.RAW_SOURCE_TYPES:
        raw_path = config.get_licensing_raw_path(source_type)
        df = labels.load_licensing_raw(source_type, raw_path)
        print(f"[{source_type}] 원본 로드: {len(df)}행")

        df = labels.standardize_columns(df, source_type)

        mismatch = labels.diagnose_district_filter(df)
        if len(mismatch) > 0:
            print(
                f"[{source_type}] 개방자치단체코드/주소텍스트 불일치 {len(mismatch)}건 "
                "(QA 기록 - 모집단 정본은 개방자치단체코드, DECISIONS.md 2026-09-21):"
            )
            print(
                mismatch[["개방자치단체코드", "지번주소", "도로명주소", "_code_match", "_text_match"]]
                .head(20)
                .to_string()
            )

        before = len(df)
        df = labels.filter_target_districts(
            df, expected_rows=schema.EXPECTED_DISTRICT_FILTERED_ROWS[source_type]
        )
        print(f"[{source_type}] 3구 필터 후: {len(df)}행 (assert 통과)")
        exclusion_rows.append({"사유": f"{source_type} 3구 외 제외", "건수": before - len(df)})

        standardized[source_type] = df

    combined = labels.combine_sources(list(standardized.values()))
    print(f"3개 파일 결합: {len(combined)}행")

    combined = labels.build_store_id(combined)
    print(f"store_id 생성: 고유 {combined['store_id'].nunique()}건 (assert 통과, 중복 0건)")

    combined = labels.parse_dates(combined)

    last_available_data_date = max(
        combined["인허가일자_dt"].max(), combined["폐업일자_dt"].max()
    )
    print(f"최신 관측 시점(last_available_data_date): {last_available_data_date.date()}")

    maturity_report = labels.recommend_maturity_cutoff(combined["폐업일자_dt"])
    print(
        f"성숙 컷오프 권고: {maturity_report['recommended_cutoff_months']}개월 "
        f"(불안정 tail 월: {[str(m) for m in maturity_report['flagged_months']]})"
    )

    diagnostic_table = labels.build_maturity_diagnostic_table(
        maturity_report, last_available_data_date, recent_months=6
    )
    print("최근 6개월 폐업 신고 추이 (months_ago=0은 아직 안 끝난 부분월):")
    print(diagnostic_table.to_string(index=False))

    non_partial_flagged = [
        m
        for m in maturity_report["flagged_months"]
        if (last_available_data_date.to_period("M") - m).n > 0
    ]
    if not non_partial_flagged:
        print(
            "[참고] flag된 달이 당월(부분월)뿐입니다 - 신고 지연보다는 "
            "관측 기간 부족(당월이 아직 끝나지 않음) 가능성이 높습니다."
        )
    else:
        print(
            f"[참고] 이미 끝난 달({len(non_partial_flagged)}개월)도 flag되어 있어 "
            f"신고 지연 가능성을 배제할 수 없습니다: {[str(m) for m in non_partial_flagged]}"
        )

    if maturity_report["recommended_cutoff_months"] != schema.MATURITY_CUTOFF_MONTHS:
        print(
            f"[경고] 실측 권고 컷오프({maturity_report['recommended_cutoff_months']}개월)가 "
            f"확정값({schema.MATURITY_CUTOFF_MONTHS}개월, DECISIONS.md 2026-09-18)과 다릅니다 - "
            "원본 갱신으로 tail 안정성이 달라졌을 수 있으니 재검토하세요."
        )
    config.FIGURES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    labels.plot_maturity_tail(maturity_report, config.MATURITY_TAIL_FIGURE_PATH)
    print(f"저장 완료: {config.MATURITY_TAIL_FIGURE_PATH}")

    maturity_cutoff_months = schema.MATURITY_CUTOFF_MONTHS
    print(f"성숙 컷오프: {maturity_cutoff_months}개월 (DECISIONS.md 2026-09-18 확정)")

    # 확정 전 검토 대상이던 4개월과 확정값을 비교 출력한다 - 컷오프가 origin 후보
    # 범위에 주는 영향을 매 실행에서 눈으로 확인하기 위한 민감도 리포트다.
    PREVIOUS_CUTOFF_FOR_COMPARISON = 4
    print(f"컷오프 변경 비교 ({PREVIOUS_CUTOFF_FOR_COMPARISON}개월 -> {maturity_cutoff_months}개월):")
    for label, cutoff in (
        ("이전", PREVIOUS_CUTOFF_FOR_COMPARISON),
        ("현재", maturity_cutoff_months),
    ):
        candidate_origins = labels.generate_candidate_origins(
            schema.MIN_ORIGIN_QUARTER, last_available_data_date, cutoff
        )
        print(
            f"  [{label}] cutoff={cutoff}개월 -> origin 후보 {len(candidate_origins)}개 분기, "
            f"최신 origin={candidate_origins[-1]}"
        )

    origins = labels.generate_candidate_origins(
        schema.MIN_ORIGIN_QUARTER, last_available_data_date, maturity_cutoff_months
    )
    print(f"origin 후보: {len(origins)}개 분기 ({origins[0]} ~ {origins[-1]})")

    panel = labels.build_long_panel(combined, origins)
    print(f"Long Panel 생성: {len(panel)}행 / 고유 store_id {panel['store_id'].nunique()}건")

    panel = labels.add_panel_features(panel, maturity_cutoff_months)
    print(f"has_coord 비율: {panel['has_coord'].mean():.3f}")
    print(f"area 결측률: {panel['area'].isna().mean() * 100:.2f}%")

    print("origin별 행수:")
    print(panel.groupby("origin").size().to_string())
    print("origin별 event_12m 비율:")
    print(panel.groupby("origin")["event_12m"].mean().to_string())

    km_input = labels.build_km_input(
        combined, "일반음식점", schema.DISTRICT_CODES["광진구"], last_available_data_date
    )
    labels.plot_km_curve(km_input, config.KM_GWANGJIN_FIGURE_PATH, "광진구 일반음식점 Kaplan-Meier")
    print(f"저장 완료: {config.KM_GWANGJIN_FIGURE_PATH}")

    license_years = combined.set_index("store_id")["인허가일자_dt"].dt.year
    cohort_paths = labels.plot_km_curve_by_license_year_cohort(
        km_input, license_years, config.KM_GWANGJIN_FIGURE_PATH, "광진구 일반음식점 Kaplan-Meier"
    )
    for cohort_path in cohort_paths:
        print(f"저장 완료: {cohort_path}")

    output_panel = labels.select_panel_output_columns(panel)
    config.LABELS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_panel.to_parquet(config.LABELS_BASE_PATH, index=False)
    print(f"저장 완료: {config.LABELS_BASE_PATH}")

    codebook_rows = labels.build_label_codebook_rows(output_panel)
    exclusion_summary = labels.build_exclusion_summary(exclusion_rows)
    constants_table = labels.build_constants_table()
    labels.write_label_spec_md(
        codebook_rows, exclusion_summary, maturity_report, constants_table, config.LABEL_SPEC_PATH
    )
    print(f"저장 완료: {config.LABEL_SPEC_PATH}")

    missing_rates = output_panel.isna().mean() * 100
    high_missing = missing_rates[missing_rates > 30]
    if len(high_missing) > 0:
        print("\n결측률 30% 초과 변수 (팀 보고 대상):")
        print(high_missing.round(2).to_string())
    else:
        print("\n결측률 30% 초과 변수 없음.")


if __name__ == "__main__":
    main()
