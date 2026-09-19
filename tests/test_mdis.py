import numpy as np
import pandas as pd
import pytest

from src.data import mdis
from src.data import mdis_schema as schema


def test_filter_industry_keeps_only_target_codes():
    df = pd.DataFrame({"산업중분류코드": ["47", "56", "96", "10", "99"]})
    out = mdis.filter_industry(df, expected_rows=3)
    assert sorted(out["산업중분류코드"]) == ["47", "56", "96"]


def test_filter_industry_assert_fails_on_mismatch():
    df = pd.DataFrame({"산업중분류코드": ["47", "10"]})
    with pytest.raises(AssertionError):
        mdis.filter_industry(df, expected_rows=5)


def test_recode_yesno_columns_maps_values_and_keeps_original():
    df = pd.DataFrame({"경영_부채여부": ["1", "2", "1"]})
    out = mdis.recode_yesno_columns(df)
    assert out["경영_부채여부_bin"].tolist() == [1, 0, 1]
    assert out["경영_부채여부"].tolist() == ["1", "2", "1"]  # 원본 보존


def test_add_derived_features_tenure_months():
    # 경영_매출금액/경영_영업이익은 coerce_numeric_columns 이후를 가정하므로 숫자형으로 전달한다.
    df = pd.DataFrame(
        {
            "일반_창업인수승계_연도": ["2020"],
            "일반_창업인수승계_월": ["1"],
            "경영_매출금액": [100],
            "경영_영업이익": [10],
            "행정구역시도코드": ["11"],
        }
    )
    out = mdis.add_derived_features(df)
    assert out["tenure_months"].iloc[0] == 2023 * 12 - (2020 * 12 + 1)


def test_add_derived_features_profit_margin_zero_revenue_is_nan_and_flagged():
    df = pd.DataFrame(
        {
            "일반_창업인수승계_연도": ["2020"],
            "일반_창업인수승계_월": ["1"],
            "경영_매출금액": [0],
            "경영_영업이익": [10],
            "행정구역시도코드": ["11"],
        }
    )
    out = mdis.add_derived_features(df)
    assert out["profit_margin"].isna().all()
    assert out["data_flag"].iloc[0] == 1


def test_add_derived_features_is_seoul():
    df = pd.DataFrame(
        {
            "일반_창업인수승계_연도": ["2020", "2020"],
            "일반_창업인수승계_월": ["1", "1"],
            "경영_매출금액": [100, 100],
            "경영_영업이익": [10, 10],
            "행정구역시도코드": ["11", "31"],
        }
    )
    out = mdis.add_derived_features(df)
    assert out["is_seoul"].tolist() == [True, False]


def test_coerce_numeric_columns_converts_clean_values():
    df = pd.DataFrame({"경영_영업이익": ["10", "-5", None]})
    out = mdis.coerce_numeric_columns(df)
    assert out["경영_영업이익"].tolist()[:2] == [10.0, -5.0]
    assert pd.isna(out["경영_영업이익"].iloc[2])


def test_coerce_numeric_columns_reports_parse_failures(capsys):
    df = pd.DataFrame({"경영_영업이익": ["10", "1,234", "N/A"]})
    out = mdis.coerce_numeric_columns(df)
    assert out["경영_영업이익"].iloc[0] == 10.0
    assert pd.isna(out["경영_영업이익"].iloc[1])  # "1,234"는 파싱 실패로 NaN
    assert pd.isna(out["경영_영업이익"].iloc[2])  # "N/A"도 파싱 실패로 NaN
    captured = capsys.readouterr()
    assert "숫자 변환 실패 2건" in captured.out


def test_apply_winsorize_preserves_original_and_flags_outliers():
    df = pd.DataFrame({"경영_영업이익": list(range(100))})
    out = mdis.apply_winsorize(df)
    assert out["경영_영업이익"].tolist() == list(range(100))  # 원본 보존
    assert out["profit_outlier"].sum() > 0
    assert out.loc[out["profit_outlier"] == 1, "경영_영업이익_winsorized"].nunique() <= 2


def test_build_treatment_vars_explicit_zero_not_nan():
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": ["1", "2"],
            "경영_전자상거래_매출비율": [np.nan, np.nan],
        }
    )
    out = mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})
    assert out["treat_cont"].iloc[1] == 0  # NaN이 아니라 명시적 0
    assert pd.isna(out["treat_cont"].iloc[0])  # 실적 있는데 비율이 NaN이면 그대로 NaN 노출 (은폐하지 않음)


def test_build_treatment_vars_assert_fails_on_type_mismatch():
    # 값이 정수 1/2로 들어와서 문자열 '1'과 매칭되지 않는 경우 assert가 실패해야 한다
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": [1, 2],
            "경영_전자상거래_매출비율": [90, np.nan],
        }
    )
    with pytest.raises(AssertionError):
        mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})


def test_split_stage_ab():
    df = pd.DataFrame({"treat_binary": [1, 0, 1, 0, 0]})
    stage_a, stage_b = mdis.split_stage_ab(df, expected_stage_b_len=2)
    assert len(stage_a) == 5
    assert len(stage_b) == 2
    assert set(stage_b["treat_binary"]) == {1}


# --- Issue #13 Part B 회귀 테스트 ---


def test_coerce_numeric_columns_converts_weight_column():
    # B1: 사업체수가중값이 NUMERIC_COLUMNS 누락으로 문자열로 남던 버그.
    df = pd.DataFrame({"사업체수가중값": ["1301.96341463415", "59.9082568807339"]})
    out = mdis.coerce_numeric_columns(df)
    assert out["사업체수가중값"].dtype == float
    assert out["사업체수가중값"].tolist() == pytest.approx([1301.96341463415, 59.9082568807339])


def test_add_derived_features_sentinel_year_is_nan_and_flagged():
    # B2: 창업연도 1900(sentinel)이 tenure_months=1475처럼 왜곡되던 버그.
    df = pd.DataFrame(
        {
            "일반_창업인수승계_연도": ["1900", "2020"],
            "일반_창업인수승계_월": ["1", "1"],
            "경영_매출금액": [100, 100],
            "경영_영업이익": [10, 10],
            "행정구역시도코드": ["11", "11"],
        }
    )
    out = mdis.add_derived_features(df)
    assert pd.isna(out["tenure_months"].iloc[0])
    assert out["tenure_invalid_flag"].tolist() == [1, 0]
    assert out["tenure_months"].iloc[1] == 2023 * 12 - (2020 * 12 + 1)


def test_add_derived_features_implausible_non_sentinel_year_raises():
    # B2: sentinel(1900)도 아니고 그럴듯한 범위도 아닌 값은 조용히 넘기지 않고 즉시 실패해야 한다.
    df = pd.DataFrame(
        {
            "일반_창업인수승계_연도": ["1800"],
            "일반_창업인수승계_월": ["1"],
            "경영_매출금액": [100],
            "경영_영업이익": [10],
            "행정구역시도코드": ["11"],
        }
    )
    with pytest.raises(AssertionError):
        mdis.add_derived_features(df)


def test_assign_row_id_is_sequential_and_predates_filter():
    # B3: 원본에 ID 컬럼이 없어 행 추적이 불가능하던 문제.
    df = pd.DataFrame({"산업중분류코드": ["47", "10", "56"]})
    out = mdis.assign_row_id(df)
    assert out["mdis_row_id"].tolist() == [0, 1, 2]


def test_extract_role_columns_preserves_row_id_and_startup_type_code():
    # B3 + B5: mdis_row_id와 일반_창업형태코드가 extract_role_columns에서 누락되던 문제.
    all_cols = [c for role_cols in schema.ROLE_COLUMNS.values() for c in role_cols]
    df = pd.DataFrame({col: ["x"] for col in all_cols + ["mdis_row_id"]})
    out = mdis.extract_role_columns(df)
    assert "mdis_row_id" in out.columns
    assert "일반_창업형태코드" in out.columns


def test_split_stage_ab_row_id_uniqueness_and_subset():
    # B3: stage_a 내 mdis_row_id 유일성과 stage_b ⊆ stage_a 관계 검증.
    df = pd.DataFrame(
        {"mdis_row_id": [10, 11, 12, 13, 14], "treat_binary": [1, 0, 1, 0, 0]}
    )
    stage_a, stage_b = mdis.split_stage_ab(df, expected_stage_b_len=2)
    assert stage_a["mdis_row_id"].is_unique
    assert set(stage_b["mdis_row_id"]) <= set(stage_a["mdis_row_id"])


def test_split_stage_ab_row_id_duplicate_raises():
    df = pd.DataFrame(
        {"mdis_row_id": [10, 10, 12, 13, 14], "treat_binary": [1, 0, 1, 0, 0]}
    )
    with pytest.raises(AssertionError):
        mdis.split_stage_ab(df, expected_stage_b_len=2)
