import pandas as pd
import pytest

from src.data import labels
from src.data import label_schema as schema


# ---------------------------------------------------------------------------
# load_licensing_raw
# ---------------------------------------------------------------------------


def test_load_licensing_raw_assert_fails_on_wrong_column_count(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="cp949")
    with pytest.raises(AssertionError):
        labels.load_licensing_raw("미용업", path, expected_columns=5)


def test_load_licensing_raw_reads_all_as_str(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("코드,값\n01,001\n", encoding="cp949")
    df = labels.load_licensing_raw("미용업", path, expected_columns=2)
    assert df["코드"].dtype == object
    assert df["코드"].iloc[0] == "01"  # zero-padding 보존


def test_load_licensing_raw_reports_replaced_chars_for_replace_policy(tmp_path, capsys):
    path = tmp_path / "raw.csv"
    with open(path, "wb") as f:
        f.write("컬럼1,컬럼2\n".encode("cp949"))
        f.write(b"\x82\xff,ok\n")  # cp949로 완전 디코딩 불가능한 바이트
    labels.load_licensing_raw("일반음식점", path, expected_columns=2)
    captured = capsys.readouterr()
    assert "[경고]" in captured.out


# ---------------------------------------------------------------------------
# standardize_columns
# ---------------------------------------------------------------------------


def test_standardize_columns_keeps_common_columns_and_tags_source_type():
    df = pd.DataFrame({col: ["x"] for col in schema.CORE_COLUMNS})
    df["기타컬럼"] = ["y"]
    out = labels.standardize_columns(df, "미용업")
    assert list(out.columns) == schema.CORE_COLUMNS + ["source_type"]
    assert out["source_type"].iloc[0] == "미용업"


# ---------------------------------------------------------------------------
# diagnose_district_filter / filter_target_districts
# ---------------------------------------------------------------------------


def test_diagnose_district_filter_finds_mismatch_rows():
    df = pd.DataFrame(
        {
            "개방자치단체코드": ["3040000", "9999999", "3130000"],
            "지번주소": ["서울 광진구 xx", "서울 광진구 yy", "서울 강남구 zz"],
            "도로명주소": ["", "", ""],
        }
    )
    mismatch = labels.diagnose_district_filter(df)
    assert len(mismatch) == 2


def test_filter_target_districts_keeps_only_target_gov_codes():
    # DECISIONS.md 2026-09-21: 모집단 정본은 개방자치단체코드다.
    df = pd.DataFrame(
        {
            "개방자치단체코드": ["3040000", "1111111", "3130000"],
            "지번주소": ["서울 광진구 자양동", "서울 강남구 역삼동", "서울 마포구 서교동"],
            "도로명주소": ["", "", ""],
        }
    )
    out = labels.filter_target_districts(df, expected_rows=2)
    assert out["개방자치단체코드"].tolist() == ["3040000", "3130000"]


def test_filter_target_districts_ignores_address_text():
    # 주소는 3구인데 코드가 3구가 아니면 제외되고(=코드가 정본),
    # 코드가 3구면 주소가 타 지역이어도 남는다(=임의 삭제 금지).
    df = pd.DataFrame(
        {
            "개방자치단체코드": ["9999999", "3180000"],
            "지번주소": ["서울 광진구 자양동", "인천 부평구 어딘가"],
            "도로명주소": ["", ""],
        }
    )
    out = labels.filter_target_districts(df)
    assert out["개방자치단체코드"].tolist() == ["3180000"]


def test_filter_target_districts_assert_fails_on_mismatch():
    df = pd.DataFrame(
        {
            "개방자치단체코드": ["3040000", "9999999"],
            "지번주소": ["서울 광진구 자양동", "서울 강남구 역삼동"],
            "도로명주소": ["", ""],
        }
    )
    with pytest.raises(AssertionError):
        labels.filter_target_districts(df, expected_rows=5)


def test_filter_target_districts_no_assert_when_expected_rows_none():
    df = pd.DataFrame(
        {
            "개방자치단체코드": ["3040000", "9999999"],
            "지번주소": ["서울 광진구 자양동", "서울 강남구 역삼동"],
            "도로명주소": ["", ""],
        }
    )
    out = labels.filter_target_districts(df)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# build_store_id
# ---------------------------------------------------------------------------


def test_build_store_id_uses_business_type_prefix_from_config():
    # 표준화 파이프라인(licenses_3gu.parquet)과 같은 규칙이어야 store_id join이 성립한다.
    df = pd.DataFrame(
        {
            "source_type": ["일반음식점", "휴게음식점", "미용업"],
            "관리번호": ["1", "2", "3"],
        }
    )
    out = labels.build_store_id(df)
    assert out["store_id"].tolist() == ["GR_1", "SR_2", "BT_3"]


def test_build_store_id_raises_on_unknown_source_type():
    df = pd.DataFrame({"source_type": ["없는업종"], "관리번호": ["1"]})
    with pytest.raises(ValueError):
        labels.build_store_id(df)


def test_build_store_id_assert_fails_on_duplicate():
    df = pd.DataFrame({"source_type": ["일반음식점", "일반음식점"], "관리번호": ["1", "1"]})
    with pytest.raises(AssertionError):
        labels.build_store_id(df)


# ---------------------------------------------------------------------------
# parse_dates
# ---------------------------------------------------------------------------


def test_parse_dates_strips_blank_closure_string_to_missing():
    df = pd.DataFrame(
        {
            "인허가일자": ["2020-01-01", "2020-01-01"],
            "폐업일자": ["2021-06-01", "  "],
        }
    )
    out = labels.parse_dates(df)
    assert out["폐업일자_dt"].iloc[0] == pd.Timestamp("2021-06-01")
    assert pd.isna(out["폐업일자_dt"].iloc[1])
    assert out["폐업일자"].tolist() == ["2021-06-01", "  "]  # 원본 보존


def test_parse_dates_reports_missing_rate_split(capsys):
    df = pd.DataFrame(
        {
            "인허가일자": ["2020-01-01", "2020-01-01", "2020-01-01"],
            "폐업일자": ["2021-06-01", "  ", ""],
        }
    )
    labels.parse_dates(df)
    captured = capsys.readouterr()
    assert "폐업표기 1건" in captured.out
    assert "영업중표기 2건" in captured.out


# ---------------------------------------------------------------------------
# generate_candidate_origins
# ---------------------------------------------------------------------------


def test_generate_candidate_origins_requires_maturity_cutoff_arg():
    with pytest.raises(TypeError):
        labels.generate_candidate_origins(
            min_origin="2021Q1", last_data_date=pd.Timestamp("2022-06-30")
        )


def test_generate_candidate_origins_excludes_origins_without_full_window():
    origins = labels.generate_candidate_origins(
        min_origin="2021Q1",
        last_data_date=pd.Timestamp("2022-06-30"),
        maturity_cutoff_months=0,
    )
    assert [str(o) for o in origins] == ["2021Q1", "2021Q2"]


def test_generate_candidate_origins_assert_fails_when_no_valid_origin():
    with pytest.raises(AssertionError):
        labels.generate_candidate_origins(
            min_origin="2021Q1",
            last_data_date=pd.Timestamp("2021-06-30"),
            maturity_cutoff_months=0,
        )


# ---------------------------------------------------------------------------
# build_long_panel
# ---------------------------------------------------------------------------


def test_build_long_panel_includes_only_open_at_origin_start():
    df = pd.DataFrame(
        {
            "store_id": ["A", "B"],
            "인허가일자_dt": pd.to_datetime(["2020-01-01", "2021-06-01"]),
            "폐업일자_dt": pd.to_datetime([pd.NaT, pd.NaT]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    assert panel["store_id"].tolist() == ["A"]


def test_build_long_panel_event_12m_true_within_window():
    df = pd.DataFrame(
        {
            "store_id": ["A"],
            "인허가일자_dt": pd.to_datetime(["2020-01-01"]),
            "폐업일자_dt": pd.to_datetime(["2021-06-01"]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    assert panel["event_12m"].tolist() == [1]


def test_build_long_panel_event_12m_false_after_window():
    df = pd.DataFrame(
        {
            "store_id": ["A"],
            "인허가일자_dt": pd.to_datetime(["2020-01-01"]),
            "폐업일자_dt": pd.to_datetime(["2022-06-01"]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    assert panel["event_12m"].tolist() == [0]


def test_build_long_panel_excludes_store_closed_before_origin():
    df = pd.DataFrame(
        {
            "store_id": ["A"],
            "인허가일자_dt": pd.to_datetime(["2019-01-01"]),
            "폐업일자_dt": pd.to_datetime(["2020-06-01"]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    assert len(panel) == 0


def test_build_long_panel_includes_store_opened_mid_quarter():
    # 2021Q1 = start 2021-01-01 / end 2021-03-31. 분기 중간(02-15)에 개업한 점포는
    # origin_end 기준이면 포함되어야 한다 (origin_start 기준이면 잘못 제외됨 - 회귀 방지).
    df = pd.DataFrame(
        {
            "store_id": ["A"],
            "인허가일자_dt": pd.to_datetime(["2021-02-15"]),
            "폐업일자_dt": pd.to_datetime([pd.NaT]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    assert panel["store_id"].tolist() == ["A"]


def test_build_long_panel_excludes_store_closed_mid_quarter_no_leakage():
    # 핵심 회귀 테스트(시간 누수). 2020-01-01 개업, origin_start(2021-01-01)와
    # origin_end(2021-03-31) 사이인 2021-02-15에 폐업한 점포는 feature_asof(=origin_end)
    # 시점에 이미 폐업한 상태이므로 패널에 행 자체가 남으면 안 된다.
    df = pd.DataFrame(
        {
            "store_id": ["A"],
            "인허가일자_dt": pd.to_datetime(["2020-01-01"]),
            "폐업일자_dt": pd.to_datetime(["2021-02-15"]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    assert len(panel) == 0


def test_build_long_panel_event_12m_measured_from_origin_end():
    # origin_end(2021-03-31) + 12개월 = 2022-03-31 경계를 직접 검증한다.
    df = pd.DataFrame(
        {
            "store_id": ["A", "B"],
            "인허가일자_dt": pd.to_datetime(["2020-01-01", "2020-01-01"]),
            "폐업일자_dt": pd.to_datetime(["2022-03-31", "2022-04-01"]),
        }
    )
    origins = [pd.Period("2021Q1", freq="Q")]
    panel = labels.build_long_panel(df, origins)
    result = dict(zip(panel["store_id"], panel["event_12m"]))
    assert result == {"A": 1, "B": 0}


# ---------------------------------------------------------------------------
# add_panel_features
# ---------------------------------------------------------------------------


def _panel_features_input(**overrides):
    base = {
        "origin_end": pd.to_datetime(["2021-03-31"]),
        "인허가일자_dt": pd.to_datetime(["2020-01-15"]),
        "source_type": ["일반음식점"],
        "소재지면적": ["33.5"],
        "좌표정보(X)": ["127.1"],
        "좌표정보(Y)": ["37.5"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_add_panel_features_age_months_uses_origin_end():
    df = _panel_features_input()
    out = labels.add_panel_features(df, maturity_cutoff_months=4)
    assert out["age_months"].iloc[0] == 14  # 2020-01 -> 2021-03


def test_add_panel_features_age_months_negative_raises():
    df = _panel_features_input(
        origin_end=pd.to_datetime(["2020-01-31"]),
        인허가일자_dt=pd.to_datetime(["2021-01-01"]),
    )
    with pytest.raises(AssertionError):
        labels.add_panel_features(df, maturity_cutoff_months=4)


def test_add_panel_features_has_coord_true_false():
    df = _panel_features_input(
        origin_end=pd.to_datetime(["2021-03-31", "2021-03-31"]),
        인허가일자_dt=pd.to_datetime(["2020-01-01", "2020-01-01"]),
        source_type=["일반음식점", "일반음식점"],
        소재지면적=["10", "10"],
        **{"좌표정보(X)": ["127.1", None], "좌표정보(Y)": ["37.5", None]},
    )
    out = labels.add_panel_features(df, maturity_cutoff_months=4)
    assert out["has_coord"].tolist() == [True, False]


def test_add_panel_features_feature_asof_available_at_and_cutoff_column():
    df = _panel_features_input(source_type=["미용업"])
    out = labels.add_panel_features(df, maturity_cutoff_months=4)
    assert out["feature_asof"].iloc[0] == out["origin_end"].iloc[0]
    assert out["available_at"].iloc[0] == out["feature_asof"].iloc[0]
    assert out["maturity_cutoff_used_months"].iloc[0] == 4
    assert out["source_snapshot"].iloc[0] == schema.SOURCE_SNAPSHOT["미용업"]


def test_add_panel_features_area_reports_parse_failures(capsys):
    df = _panel_features_input(소재지면적=["abc"])
    out = labels.add_panel_features(df, maturity_cutoff_months=4)
    assert pd.isna(out["area"].iloc[0])
    captured = capsys.readouterr()
    assert "숫자 변환 실패 1건" in captured.out


def test_add_panel_features_area_parses_thousands_comma():
    df = _panel_features_input(소재지면적=["1,390.75"])
    out = labels.add_panel_features(df, maturity_cutoff_months=4)
    assert out["area"].iloc[0] == pytest.approx(1390.75)


# ---------------------------------------------------------------------------
# select_panel_output_columns
# ---------------------------------------------------------------------------


def test_select_panel_output_columns_keeps_only_defined_columns():
    df = pd.DataFrame({col: [1] for col in schema.PANEL_OUTPUT_COLUMNS})
    df["extra"] = [1]
    out = labels.select_panel_output_columns(df)
    assert list(out.columns) == schema.PANEL_OUTPUT_COLUMNS


# ---------------------------------------------------------------------------
# recommend_maturity_cutoff
# ---------------------------------------------------------------------------


def _monthly_closure_dates(values, start="2025-01"):
    months = pd.period_range(start, periods=len(values), freq="M")
    dates = []
    for period, count in zip(months, values):
        dates += [period.to_timestamp()] * count
    return pd.Series(pd.to_datetime(dates))


def test_recommend_maturity_cutoff_flags_recent_drop():
    closure_dates = _monthly_closure_dates([100, 100, 100, 100, 100, 100, 40])
    report = labels.recommend_maturity_cutoff(
        closure_dates, baseline_window_months=6, drop_threshold=0.75
    )
    assert report["recommended_cutoff_months"] == 1
    assert len(report["flagged_months"]) == 1


def test_recommend_maturity_cutoff_no_flag_when_stable():
    closure_dates = _monthly_closure_dates([100] * 7)
    report = labels.recommend_maturity_cutoff(closure_dates)
    assert report["flagged_months"] == []
    assert report["recommended_cutoff_months"] == 0


# ---------------------------------------------------------------------------
# build_maturity_diagnostic_table
# ---------------------------------------------------------------------------


def test_build_maturity_diagnostic_table_marks_partial_month_and_months_ago():
    closure_dates = _monthly_closure_dates([100, 100, 100, 100, 100, 100, 40])
    report = labels.recommend_maturity_cutoff(closure_dates)
    last_month = pd.period_range("2025-01", periods=7, freq="M")[-1]
    last_data_date = pd.Timestamp(f"{last_month}-10")  # 당월 10일까지만 관측된 상황 가정

    table = labels.build_maturity_diagnostic_table(report, last_data_date, recent_months=6)

    assert len(table) == 6
    assert table["months_ago"].tolist() == [5, 4, 3, 2, 1, 0]  # 오래된 달 -> 최근 달 순
    latest_row = table.iloc[-1]
    assert latest_row["months_ago"] == 0
    assert "부분월" in latest_row["비고"]
    assert latest_row["flagged"] == True  # noqa: E712
    assert table.iloc[0]["flagged"] == False  # noqa: E712


def test_build_maturity_diagnostic_table_limits_to_recent_months():
    closure_dates = _monthly_closure_dates([100] * 12)
    report = labels.recommend_maturity_cutoff(closure_dates)
    last_month = pd.period_range("2025-01", periods=12, freq="M")[-1]
    last_data_date = pd.Timestamp(f"{last_month}-15")

    table = labels.build_maturity_diagnostic_table(report, last_data_date, recent_months=3)
    assert table["months_ago"].tolist() == [2, 1, 0]


# ---------------------------------------------------------------------------
# build_km_input
# ---------------------------------------------------------------------------


def test_build_km_input_duration_and_event_flag():
    df = pd.DataFrame(
        {
            "store_id": ["A", "B"],
            "source_type": ["일반음식점", "일반음식점"],
            "개방자치단체코드": ["3040000", "3040000"],
            "인허가일자_dt": pd.to_datetime(["2020-01-01", "2020-01-01"]),
            "폐업일자_dt": pd.to_datetime(["2021-01-01", pd.NaT]),
        }
    )
    as_of = pd.Timestamp("2022-01-01")
    out = labels.build_km_input(df, "일반음식점", "3040000", as_of)
    a = out.loc[out["store_id"] == "A"].iloc[0]
    b = out.loc[out["store_id"] == "B"].iloc[0]
    assert a["event_observed"] == 1
    assert b["event_observed"] == 0
    assert a["duration_months"] < b["duration_months"]


def test_build_km_input_drops_negative_duration_with_warning(capsys):
    df = pd.DataFrame(
        {
            "store_id": ["A"],
            "source_type": ["일반음식점"],
            "개방자치단체코드": ["3040000"],
            "인허가일자_dt": pd.to_datetime(["2023-01-01"]),
            "폐업일자_dt": pd.to_datetime([pd.NaT]),
        }
    )
    as_of = pd.Timestamp("2022-01-01")  # as_of가 인허가일자보다 이전인 이상 데이터
    out = labels.build_km_input(df, "일반음식점", "3040000", as_of)
    assert len(out) == 0
    captured = capsys.readouterr()
    assert "제외" in captured.out


# ---------------------------------------------------------------------------
# plot_maturity_tail / plot_km_curve (smoke tests)
# ---------------------------------------------------------------------------


def test_plot_maturity_tail_creates_file(tmp_path):
    closure_dates = _monthly_closure_dates([10, 12, 8])
    report = labels.recommend_maturity_cutoff(closure_dates)
    path = tmp_path / "fig.png"
    labels.plot_maturity_tail(report, path)
    assert path.exists()


def test_plot_km_curve_creates_file(tmp_path):
    km_input = pd.DataFrame(
        {
            "duration_months": [1, 2, 3, 4, 5],
            "event_observed": [1, 1, 1, 1, 1],
        }
    )
    path = tmp_path / "km.png"
    labels.plot_km_curve(km_input, path, "테스트")
    assert path.exists()


def test_plot_km_curve_by_license_year_cohort_creates_one_file_per_nonempty_cohort(
    tmp_path, capsys
):
    km_input = pd.DataFrame(
        {
            "store_id": ["A", "B", "C", "D", "E"],
            "duration_months": [40.0, 20.0, 10.0, 3.0, 2.0],
            "event_observed": [1, 0, 1, 0, 1],
        }
    )
    license_years = pd.Series({"A": 2021, "B": 2023, "C": 2024, "D": 2025, "E": 2025})
    prefix = tmp_path / "km_gwangjin.png"
    paths = labels.plot_km_curve_by_license_year_cohort(km_input, license_years, prefix, "테스트")

    assert len(paths) == 4  # 2021~2022, 2023, 2024, 2025 코호트 전부 표본 존재
    for p in paths:
        assert p.exists()
    assert {p.name for p in paths} == {
        f"km_gwangjin_{suffix}.png" for _, _, _, suffix in schema.LICENSE_YEAR_COHORTS
    }
    captured = capsys.readouterr()
    assert "표본 1개 가게" in captured.out  # 2021~2022/2023/2024 각각 가게 1개
    assert "표본 2개 가게" in captured.out  # 2025 코호트에 D, E 2개


def test_plot_km_curve_by_license_year_cohort_groups_2021_and_2022_together(tmp_path):
    km_input = pd.DataFrame(
        {
            "store_id": ["A", "B"],
            "duration_months": [30.0, 20.0],
            "event_observed": [1, 0],
        }
    )
    license_years = pd.Series({"A": 2021, "B": 2022})
    prefix = tmp_path / "km_gwangjin.png"
    paths = labels.plot_km_curve_by_license_year_cohort(km_input, license_years, prefix, "테스트")
    assert len(paths) == 1
    assert paths[0].name == "km_gwangjin_2021_2022.png"


def test_plot_km_curve_by_license_year_cohort_skips_empty_cohort_with_warning(
    tmp_path, capsys
):
    km_input = pd.DataFrame(
        {
            "store_id": ["A", "B"],
            "duration_months": [40.0, 2.0],
            "event_observed": [1, 0],
        }
    )
    license_years = pd.Series({"A": 2021, "B": 2025})  # 2023, 2024 코호트는 표본 0건
    prefix = tmp_path / "km_gwangjin.png"
    paths = labels.plot_km_curve_by_license_year_cohort(km_input, license_years, prefix, "테스트")

    assert len(paths) == 2
    captured = capsys.readouterr()
    assert captured.out.count("[경고]") == 2


# ---------------------------------------------------------------------------
# build_label_codebook_rows / build_exclusion_summary / write_label_spec_md
# ---------------------------------------------------------------------------


def test_build_label_codebook_rows_raises_keyerror_on_undocumented_column():
    df = pd.DataFrame({"미정의컬럼": [1, 2, 3]})
    with pytest.raises(KeyError):
        labels.build_label_codebook_rows(df)


def test_build_label_codebook_rows_uses_variable_definitions():
    df = pd.DataFrame({"store_id": ["GR_1", "GR_2"]})
    rows = labels.build_label_codebook_rows(df)
    assert (
        rows.loc[rows["변수명"] == "store_id", "정의"].iloc[0]
        == schema.VARIABLE_DEFINITIONS["store_id"]
    )


def test_write_label_spec_md_creates_file(tmp_path):
    codebook_rows = labels.build_label_codebook_rows(pd.DataFrame({"store_id": ["GR_1"]}))
    exclusion_rows = labels.build_exclusion_summary([{"사유": "테스트 제외", "건수": 3}])
    maturity_report = {
        "recommended_cutoff_months": 1,
        "flagged_months": [pd.Period("2026-08", freq="M")],
    }
    constants_table = labels.build_constants_table()
    path = tmp_path / "LABEL_SPEC.md"
    labels.write_label_spec_md(codebook_rows, exclusion_rows, maturity_report, constants_table, path)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "라벨 정의" in content
