import numpy as np
import pandas as pd
import pytest

from src.data import mdis
from src.data import mdis_schema as schema


def test_filter_industry_keeps_only_target_codes():
    df = pd.DataFrame({"산업중분류코드": ["47", "56", "96", "10", "99"]})
    out = mdis.filter_industry(df, expected_rows=3)
    assert sorted(out["산업중분류코드"]) == ["47", "56", "96"]


def test_filter_industry_raises_on_mismatch():
    df = pd.DataFrame({"산업중분류코드": ["47", "10"]})
    with pytest.raises(ValueError):
        mdis.filter_industry(df, expected_rows=5)


def test_recode_yesno_columns_maps_values_and_keeps_original():
    df = pd.DataFrame({"경영_부채여부": ["1", "2", "1"]})
    out = mdis.recode_yesno_columns(df)
    assert out["경영_부채여부_bin"].tolist() == [1, 0, 1]
    assert out["경영_부채여부"].tolist() == ["1", "2", "1"]  # 원본 보존


def test_recode_yesno_columns_allows_nan_but_raises_on_out_of_domain_value():
    # M2: {1,2} 밖 값이 조용히 NaN으로 매핑되던 문제. 결측 자체는 정상 통과해야 한다.
    ok = pd.DataFrame({"경영_부채여부": ["1", None, "2"]})
    out = mdis.recode_yesno_columns(ok)
    assert out["경영_부채여부_bin"].tolist()[0] == 1
    assert pd.isna(out["경영_부채여부_bin"].iloc[1])

    bad = pd.DataFrame({"경영_부채여부": ["1", "3"]})
    with pytest.raises(ValueError):
        mdis.recode_yesno_columns(bad)


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
    out = mdis.add_derived_features(df, expected_seoul_count=1)
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
    out = mdis.add_derived_features(df, expected_seoul_count=1)
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
    out = mdis.add_derived_features(df, expected_seoul_count=1)
    assert out["is_seoul"].tolist() == [True, False]


def test_add_derived_features_seoul_count_mismatch_raises():
    # M2: 서울 매칭이 >0인지만 확인하고 정확한 기대값은 확인하지 않던 문제.
    df = pd.DataFrame(
        {
            "일반_창업인수승계_연도": ["2020"],
            "일반_창업인수승계_월": ["1"],
            "경영_매출금액": [100],
            "경영_영업이익": [10],
            "행정구역시도코드": ["11"],
        }
    )
    with pytest.raises(ValueError):
        mdis.add_derived_features(df, expected_seoul_count=2)


def test_coerce_numeric_columns_converts_clean_values():
    df = pd.DataFrame({"경영_영업이익": ["10", "-5", None]})
    out = mdis.coerce_numeric_columns(df)
    assert out["경영_영업이익"].tolist()[:2] == [10.0, -5.0]
    assert pd.isna(out["경영_영업이익"].iloc[2])


def test_coerce_numeric_columns_raises_on_parse_failures():
    # M2: 파싱 실패를 print만 하고 계속 진행하던 문제 - 이제는 조용히 넘어가지 않고 즉시 실패한다.
    df = pd.DataFrame({"경영_영업이익": ["10", "1,234", "N/A"]})
    with pytest.raises(ValueError, match="숫자 변환 실패 2건"):
        mdis.coerce_numeric_columns(df)


def test_validate_no_missing_required_columns_passes_when_clean():
    df = pd.DataFrame({"사업체수가중값": [1.5, 2.0, 3.25]})
    mdis.validate_no_missing_required_columns(df)  # 예외 없이 통과해야 한다


def test_validate_no_missing_required_columns_raises_on_missing():
    # M6: 가중치 결측은 가중 평균 등 모든 분석에 영향을 주므로 조용히 넘어가면 안 된다.
    df = pd.DataFrame({"사업체수가중값": [1.5, None, 3.25]})
    with pytest.raises(ValueError, match="사업체수가중값 결측 1건"):
        mdis.validate_no_missing_required_columns(df)


def test_validate_weight_positive_passes_when_clean():
    df = pd.DataFrame({"사업체수가중값": [1.5, 2.0, 3.25]})
    mdis.validate_weight_positive(df)  # 예외 없이 통과해야 한다


def test_validate_weight_positive_raises_on_zero_or_negative():
    # PR #15 리뷰(choihongjun1): 가중치<=0이 결측 검증만으로는 잡히지 않던 문제.
    df = pd.DataFrame({"사업체수가중값": [1.5, 0.0, -3.25]})
    with pytest.raises(ValueError, match="사업체수가중값"):
        mdis.validate_weight_positive(df)


def test_validate_categorical_code_columns_passes_when_clean():
    df = pd.DataFrame({"일반_창업형태코드": ["1", "2", "3"]})
    mdis.validate_categorical_code_columns(df)  # 예외 없이 통과해야 한다


def test_validate_categorical_code_columns_raises_on_missing():
    df = pd.DataFrame({"일반_창업형태코드": ["1", None, "3"]})
    with pytest.raises(ValueError, match="일반_창업형태코드 결측"):
        mdis.validate_categorical_code_columns(df)


def test_validate_categorical_code_columns_raises_on_invalid_code():
    # PR #15 리뷰(choihongjun1): 창업형태 이상값(정의 밖 코드)이 조용히 통과하던 문제.
    df = pd.DataFrame({"일반_창업형태코드": ["1", "4", "3"]})
    with pytest.raises(ValueError, match="일반_창업형태코드"):
        mdis.validate_categorical_code_columns(df)


def test_apply_winsorize_preserves_original_and_flags_outliers():
    df = pd.DataFrame({"경영_영업이익": list(range(100))})
    out = mdis.apply_winsorize(df)
    assert out["경영_영업이익"].tolist() == list(range(100))  # 원본 보존
    assert out["profit_outlier"].sum() > 0
    assert out.loc[out["profit_outlier"] == 1, "경영_영업이익_winsorized"].nunique() <= 2


def test_build_treatment_vars_explicit_zero_not_nan():
    # PR #15 리뷰 이전에는 처치군(여부=1)의 비율이 NaN이어도 그대로 통과시켰다.
    # 이제는 treat_binary==1인데 비율이 NaN이면 즉시 실패해야 하므로(아래 신규 테스트
    # 참조), 이 테스트는 "미처치군=명시적 0"만 검증하도록 처치군 비율을 실측값(90)으로 바꿨다.
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": ["1", "2"],
            "경영_전자상거래_매출비율": [90, np.nan],
        }
    )
    out = mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})
    assert out["treat_cont"].iloc[0] == 90  # 처치군 비율은 그대로 유지
    assert out["treat_cont"].iloc[1] == 0  # NaN이 아니라 명시적 0


def test_build_treatment_vars_raises_when_treated_row_has_nan_ratio():
    # PR #15 리뷰(choihongjun1): 처치군(여부=1)인데 비율이 NaN인 행이 예전에는
    # "그대로 NaN 노출(은폐하지 않음)"으로 정상 취급됐다 - 이번 리뷰는 정확히 그 케이스가
    # 원본 모순(처치군인데 비율이 없음)이므로 에러 없이 통과하면 안 된다고 지적한 것이라
    # 기대값을 반대로 뒤집었다.
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": ["1", "2"],
            "경영_전자상거래_매출비율": [np.nan, np.nan],
        }
    )
    with pytest.raises(ValueError, match="NaN이거나"):
        mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})


def test_build_treatment_vars_raises_when_treated_row_ratio_exceeds_100():
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": ["1", "2"],
            "경영_전자상거래_매출비율": [150, np.nan],
        }
    )
    with pytest.raises(ValueError, match="100 초과"):
        mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})


def test_build_treatment_vars_raises_on_type_mismatch():
    # 값이 정수 1/2로 들어와서 문자열 '1'과 매칭되지 않으면 도메인 검증에서 바로 실패해야 한다
    # (과거에는 조용히 전부 treat_binary=0으로 오분류된 뒤 분포 assert에서야 잡혔음).
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": [1, 2],
            "경영_전자상거래_매출비율": [90, np.nan],
        }
    )
    with pytest.raises(ValueError):
        mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})


def test_build_treatment_vars_raises_on_missing_source_value():
    # M6: 원본이 결측이면 (NaN == '1')이 False가 되어 조용히 treat_binary=0(미처치)으로
    # 오분류되던 문제 - 결측이면 즉시 실패해야 한다.
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": ["1", None],
            "경영_전자상거래_매출비율": [90, np.nan],
        }
    )
    with pytest.raises(ValueError):
        mdis.build_treatment_vars(df, expected_counts={1: 1, 0: 1})


def test_build_treatment_vars_raises_when_control_row_has_ratio_value():
    # M2: 여부=2(미처치)인데 매출비율이 실제 값을 가진 행을 조용히 0으로 덮어쓰던 문제.
    df = pd.DataFrame(
        {
            "경영_전자상거래_매출실적여부": ["1", "2"],
            "경영_전자상거래_매출비율": [90, 5],  # 미처치인데 비율 5가 있음 - 원본 모순
        }
    )
    with pytest.raises(ValueError):
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
    out = mdis.add_derived_features(df, expected_seoul_count=2)
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
    with pytest.raises(ValueError):
        mdis.add_derived_features(df, expected_seoul_count=1)


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
    with pytest.raises(ValueError):
        mdis.split_stage_ab(df, expected_stage_b_len=2)


# --- M6 회귀 테스트: provenance ---


def test_build_provenance_records_sha256_and_versions(tmp_path):
    raw = tmp_path / "raw.csv"
    raw.write_text("a,b\n1,2\n", encoding="utf-8")
    import hashlib

    expected_sha256 = hashlib.sha256(raw.read_bytes()).hexdigest()

    provenance = mdis.build_provenance(raw)
    assert provenance["raw_sha256"] == expected_sha256
    assert provenance["raw_file"] == str(raw)
    assert "pandas_version" in provenance
    assert "python_version" in provenance


def test_write_provenance_json_creates_readable_file(tmp_path):
    import json

    path = tmp_path / "out" / "provenance.json"
    mdis.write_provenance_json({"raw_sha256": "abc123"}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"raw_sha256": "abc123"}
