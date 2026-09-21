import numpy as np
import pandas as pd
import pytest

from src.data import mdis_validation as mv


def _make_balance_df(n_treat=6, n_control=6):
    treat = pd.DataFrame(
        {
            "treat_binary": [1] * n_treat,
            "tenure_months": [100 + i for i in range(n_treat)],  # 대조군과 뚜렷이 다름
            "일반_합계종사자수": [3] * n_treat,  # 대조군과 동일 -> SMD ~ 0
            "경영_영업비용_임차료": [1000] * n_treat,
            "경영_판매처별매출_소비자비율": [80] * n_treat,
            "산업중분류코드": ["47"] * n_treat,
            "행정구역시도코드": ["11"] * n_treat,
            "경영_부채여부_bin": [1] * n_treat,
        }
    )
    control = pd.DataFrame(
        {
            "treat_binary": [0] * n_control,
            "tenure_months": [10 + i for i in range(n_control)],
            "일반_합계종사자수": [3] * n_control,
            "경영_영업비용_임차료": [1000] * n_control,
            "경영_판매처별매출_소비자비율": [80] * n_control,
            "산업중분류코드": ["56"] * n_control,
            "행정구역시도코드": ["11"] * n_control,
            "경영_부채여부_bin": [0] * n_control,
        }
    )
    return pd.concat([treat, control], ignore_index=True)


def test_compute_missing_rates():
    df = pd.DataFrame({"a": [1, None, 3, None], "b": [1, 2, 3, 4]})
    rates = mv.compute_missing_rates(df)
    assert rates["a"] == 50.0
    assert rates["b"] == 0.0


def test_coerce_weight_column_converts_clean_values():
    df = pd.DataFrame({"사업체수가중값": ["1.5", "2.0", "3.25"]})
    out = mv.coerce_weight_column(df)
    assert out.tolist() == [1.5, 2.0, 3.25]


def test_coerce_weight_column_reports_parse_failures(capsys):
    df = pd.DataFrame({"사업체수가중값": ["1.5", "N/A"]})
    out = mv.coerce_weight_column(df)
    assert out.iloc[0] == 1.5
    assert pd.isna(out.iloc[1])
    captured = capsys.readouterr()
    assert "숫자 변환 실패 1건" in captured.out


def test_compute_smd_continuous_known_values():
    treat = pd.Series([2.0, 2.0, 2.0, 2.0])  # var=0
    control = pd.Series([0.0, 0.0, 0.0, 0.0])  # var=0
    # pooled_sd == 0인 퇴화 케이스는 NaN을 반환해야 한다
    assert pd.isna(mv.compute_smd_continuous(treat, control))

    treat = pd.Series([12.0, 10.0, 14.0, 12.0])  # mean=12, var=8/3
    control = pd.Series([8.0, 6.0, 10.0, 8.0])  # mean=8, var=8/3
    expected = (12.0 - 8.0) / np.sqrt((8 / 3 + 8 / 3) / 2)
    assert mv.compute_smd_continuous(treat, control) == pytest.approx(expected)


def test_compute_smd_binary_known_values():
    treat = pd.Series([1, 1, 0, 0])  # p=0.5
    control = pd.Series([0, 0, 0, 0])  # p=0.0
    expected = (0.5 - 0.0) / np.sqrt((0.5 * 0.5 + 0.0) / 2)
    assert mv.compute_smd_binary(treat, control) == pytest.approx(expected)


def test_compute_covariate_balance_flags_imbalanced_continuous_variable():
    df = _make_balance_df()
    balance = mv.compute_covariate_balance(df)

    tenure_row = balance[balance["변수"] == "tenure_months"].iloc[0]
    assert bool(tenure_row["flag"]) is True  # 처치/대조 tenure_months가 뚜렷이 다름

    workers_row = balance[balance["변수"] == "일반_합계종사자수"].iloc[0]
    assert bool(workers_row["flag"]) is False  # 완전히 동일한 값 -> SMD 0

    # 범주형은 범주별로 행이 확장된다
    industry_rows = balance[balance["변수"] == "산업중분류코드"]
    assert set(industry_rows["범주"]) == {"47", "56"}


def test_compute_covariate_balance_binary_covariate():
    df = _make_balance_df()
    balance = mv.compute_covariate_balance(df)
    debt_row = balance[balance["변수"] == "경영_부채여부_bin"].iloc[0]
    assert debt_row["처치군 값"] == 1.0
    assert debt_row["대조군 값"] == 0.0


def test_fit_propensity_scores_excludes_missing_rows_and_reports(capsys):
    df = _make_balance_df(n_treat=8, n_control=8)
    df.loc[0, "일반_합계종사자수"] = np.nan

    scores = mv.fit_propensity_scores(df)

    assert len(scores) == len(df)
    assert pd.isna(scores.loc[0])
    remaining = scores.drop(index=0)
    assert remaining.notna().all()
    assert remaining.between(0, 1).all()

    captured = capsys.readouterr()
    assert "제외" in captured.out and "1건" in captured.out


def test_plot_propensity_overlap_creates_file(tmp_path):
    propensity = pd.Series([0.2, 0.3, 0.8, 0.9])
    treat = pd.Series([0, 0, 1, 1])
    out_path = tmp_path / "fig" / "propensity.png"
    mv.plot_propensity_overlap(propensity, treat, out_path)
    assert out_path.exists()


def test_compute_weighted_unweighted_profit_matches_manual_average():
    df = pd.DataFrame(
        {
            "treat_binary": [1, 1, 0, 0],
            "profit_margin": [0.1, 0.3, 0.2, 0.4],
        }
    )
    weight = pd.Series([1.0, 3.0, 1.0, 1.0])

    result = mv.compute_weighted_unweighted_profit(df, weight)

    treat_row = result[result["집단"] == "처치군"].iloc[0]
    assert treat_row["무가중 평균"] == pytest.approx((0.1 + 0.3) / 2)
    assert treat_row["가중 평균"] == pytest.approx((0.1 * 1.0 + 0.3 * 3.0) / (1.0 + 3.0))

    control_row = result[result["집단"] == "대조군"].iloc[0]
    assert control_row["무가중 평균"] == pytest.approx((0.2 + 0.4) / 2)

    diff_row = result[result["집단"] == "처치효과(처치-대조)"].iloc[0]
    assert diff_row["무가중 평균"] == pytest.approx(treat_row["무가중 평균"] - control_row["무가중 평균"])


def test_compute_stage_b_treat_cont_association_matches_pandas_corr():
    stage_b_df = pd.DataFrame(
        {
            "tenure_months": [10, 20, 30, 40, 50],
            "일반_합계종사자수": [1, 2, 3, 4, 5],
            "경영_영업비용_임차료": [100, 200, 300, 400, 500],
            "경영_판매처별매출_소비자비율": [10, 20, 30, 40, 50],
            "경영_부채여부_bin": [0, 1, 0, 1, 0],
            "산업중분류코드": ["47", "47", "56", "56", "56"],
            "행정구역시도코드": ["11", "11", "11", "11", "11"],
            "treat_cont": [5, 10, 15, 20, 100],
        }
    )
    assoc = mv.compute_stage_b_treat_cont_association(stage_b_df)

    tenure_row = assoc[assoc["변수"] == "tenure_months"].iloc[0]
    expected_corr = stage_b_df["tenure_months"].corr(stage_b_df["treat_cont"])
    assert tenure_row["값"] == pytest.approx(round(expected_corr, 3))

    industry_rows = assoc[assoc["변수"] == "산업중분류코드"]
    assert set(industry_rows["범주"]) == {"47", "56"}
    row_56 = industry_rows[industry_rows["범주"] == "56"].iloc[0]
    assert row_56["표본비율"] == pytest.approx(0.6)
    assert bool(row_56["flag"]) is True  # 0.6 > CATEGORY_CONCENTRATION_THRESHOLD(0.5)


def test_append_validation_section_md_is_idempotent(tmp_path):
    path = tmp_path / "MDIS_CODEBOOK.md"
    path.write_text("# MDIS 코드북\n\n## 변수 사전\n\n(내용)\n", encoding="utf-8")

    mv.append_validation_section_md("## A-4 검증 결과\n\n첫 번째 실행", path)
    mv.append_validation_section_md("## A-4 검증 결과\n\n두 번째 실행", path)

    content = path.read_text(encoding="utf-8")
    assert content.count("<!-- A4-VALIDATION-START -->") == 1
    assert content.count("<!-- A4-VALIDATION-END -->") == 1
    assert "두 번째 실행" in content
    assert "첫 번째 실행" not in content
    assert "## 변수 사전" in content  # 기존 내용 보존
