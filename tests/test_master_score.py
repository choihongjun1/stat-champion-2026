# -*- coding: utf-8 -*-
"""예측용 master_score 테스트 (합성 데이터, Issue #35)."""
import pandas as pd
import pytest

from src.data import label_schema, labels, master
from src.data import master_schema as schema
from src.data import master_score as ms
from tests.test_master import _er, _spatial, _trdar, _trdar_biz

ORIGIN = "2025Q2"  # 합성 상권 원천이 2025Q2까지라 T-1 = 2025Q1 (T 값 999는 쓰면 안 된다)
CODE = label_schema.DISTRICT_CODES["마포구"]


def _combined():
    """parse_dates 이후 모양의 인허가 결합 테이블. 경계 사례를 모은다 (origin_end = 2025-06-30)."""
    rows = [
        # store_id, 인허가일, 폐업일, 상태명, 면적, X
        ("GR_1", "2020-01-15", None, "영업/정상", "33.5", "1"),          # 영업 중
        ("GR_2", "2025-06-30", None, "영업/정상", "1,390.75", None),     # 기준일 당일 인허가 → 포함, 좌표 없음
        ("GR_3", "2019-03-01", "2025-06-30", "폐업", "20", "1"),         # 기준일 당일 폐업 → 제외
        ("GR_9", "2018-05-01", "2025-07-01", "폐업", "15", "1"),         # 기준일 후 폐업 → 당시 영업이라 보존
        ("GR_8", "2025-07-01", None, "영업/정상", "10", "1"),            # 기준일 후 개업 → 제외
        ("GR_7", "2021-02-01", None, "폐업", "12", "1"),                 # 상태명 폐업·폐업일 없음 → 날짜 규칙상 포함
        ("GR_6", None, None, "영업/정상", "12", "1"),                    # 인허가일 결측 → 판정 불가, 제외
    ]
    df = pd.DataFrame(rows, columns=["store_id", "인허가일자", "폐업일자", "영업상태명", "소재지면적", "좌표정보(X)"])
    df["좌표정보(Y)"] = df["좌표정보(X)"]
    df["source_type"] = "일반음식점"
    df["개방자치단체코드"] = CODE
    df["인허가일자_dt"] = pd.to_datetime(df["인허가일자"])
    df["폐업일자_dt"] = pd.to_datetime(df["폐업일자"])
    # 원천 최종 관측일(성숙 컷오프 확인용) 2025-09-10 — 그날 개업한 점포 (기준일 후 개업이라 제외)
    extra = df.iloc[[0]].assign(store_id="GR_5", 인허가일자_dt=pd.Timestamp("2025-09-10"),
                                폐업일자_dt=pd.NaT, 영업상태명="영업/정상")
    return pd.concat([df, extra], ignore_index=True)


def _score(combined=None):
    panel = ms.build_score_panel(_combined() if combined is None else combined, ORIGIN)
    return ms.build_master_score(panel, _spatial(), _er(), _trdar(), "synthetic.csv@0000",
                                 _trdar_biz(), "synthetic-biz.csv@0000")


def test_eligibility_boundaries():
    panel = ms.build_score_panel(_combined(), ORIGIN)
    assert sorted(panel["store_id"]) == ["GR_1", "GR_2", "GR_7", "GR_9"]
    assert set(panel["origin"]) == {ORIGIN}
    end = ms.origin_end(ORIGIN)
    c = _combined().set_index("store_id").loc[panel["store_id"]]
    assert (c["인허가일자_dt"] <= end).all()
    assert (c["폐업일자_dt"].isna() | (c["폐업일자_dt"] > end)).all()


def test_same_panel_as_labels_pipeline_without_labels():
    """labels_base와 같은 함수로 만든 패널에서 라벨·성숙 컬럼만 빠진 것과 같다."""
    combined = _combined()
    lab = labels.add_panel_features(labels.build_long_panel(combined, [pd.Period(ORIGIN, freq="Q")]),
                                    label_schema.MATURITY_CUTOFF_MONTHS)
    lab = lab[label_schema.PANEL_OUTPUT_COLUMNS].drop(columns=list(schema.SCORE_EXCLUDED_COLUMNS))
    lab = lab.sort_values("store_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(ms.build_score_panel(combined, ORIGIN), lab)
    assert not set(schema.SCORE_EXCLUDED_COLUMNS) & set(ms.build_score_panel(combined, ORIGIN).columns)


def test_population_qa_counts():
    combined = _combined()
    pop = ms.population_qa(combined, ms.build_score_panel(combined, ORIGIN), ORIGIN)
    assert pop["eligible_at_as_of"] == pop["panel_rows"] == 4 and pop["panel_equals_eligible_set"]
    assert pop["opened_after_as_of"] == 2 and pop["opened_after_as_of_in_panel"] == 0   # GR_8, GR_5
    assert pop["closed_on_or_before_as_of_in_panel"] == 0
    assert pop["open_at_as_of_closed_since"] == 1                     # GR_9
    assert pop["status_closed_without_close_date_in_panel"] == 1      # GR_7
    assert pop["license_date_missing"] == 1                           # GR_6
    assert pop["by_gu_biz"] == {"마포구": {"일반음식점": 4}}


def test_observation_window_required():
    combined = _combined()
    assert ms.check_observation_window(combined, ORIGIN) == pd.Timestamp("2025-09-10")
    with pytest.raises(ms.ScoreValidationError, match="관측일"):
        ms.check_observation_window(combined, "2025Q3")  # 2025-09-30 + 1개월 > 최종 관측일


def test_column_contract_and_no_labels():
    score, log = _score()
    assert list(score.columns) == schema.score_columns()
    assert ms.contract_errors(score) == []
    for c in schema.SCORE_EXCLUDED_COLUMNS:
        assert c not in score.columns
    for c in (*schema.SCORE_REQUIRED_META, *schema.predictor_columns()):
        assert c in score.columns
    assert all(r["leakage_violations"] == 0 and r["panel_unchanged"] for r in log)
    assert score["store_id"].is_unique and set(score["origin"]) == {ORIGIN}


def test_trdar_t_minus_1_and_land_price_asof():
    score, _ = _score()
    s = score.set_index("store_id")
    assert set(score["trdar_quarter_used"]) == {"2025Q1"}
    assert s.loc["GR_1", "trdar_flow_pop"] == 120                     # 2025Q1 값 (2025Q2의 999가 아님)
    assert s.loc["GR_1", "land_price_year_used"] == 2025               # 2025-04-30 공시 ≤ 2025-06-30
    assert s.loc["GR_1", "land_price"] == 110.0
    tc = ms.time_checks(score, ORIGIN)
    assert tc["trdar_quarter_used_violations"] == 0 and tc["land_price_year_used_violations"] == 0
    assert tc["land_price_year_expected"] == 2025 and sum(tc["leakage"].values()) == 0


def test_unmatched_and_missing_stores_are_kept():
    score, _ = _score()
    s = score.set_index("store_id")
    assert pd.isna(s.loc["GR_2", "trdar_cd"]) and not s.loc["GR_2", "has_coord"]   # 좌표 없음·상권 밖
    assert pd.isna(s.loc["GR_7", "gu"])                                          # 공간 결합 원천에 없음
    assert len(score) == 4


def test_label_or_multi_origin_panel_is_rejected():
    panel = ms.build_score_panel(_combined(), ORIGIN)
    with pytest.raises(ms.ScoreValidationError, match="라벨"):
        ms.build_master_score(panel.assign(event_12m=0), _spatial(), _er(), _trdar(), "s", _trdar_biz(), "b")
    two = pd.concat([panel, panel.assign(origin="2025Q1")], ignore_index=True)
    with pytest.raises(ms.ScoreValidationError, match="origin"):
        ms.build_master_score(two, _spatial(), _er(), _trdar(), "s", _trdar_biz(), "b")


def test_matches_master_base_for_labeled_origin():
    """역검증 축소판: 같은 원천으로 만든 master_base의 같은 origin(라벨 제외)과 같다."""
    combined = _combined()
    lab = labels.add_panel_features(labels.build_long_panel(combined, [pd.Period(ORIGIN, freq="Q")]),
                                    label_schema.MATURITY_CUTOFF_MONTHS)[label_schema.PANEL_OUTPUT_COLUMNS]
    base, _ = master.build_master_base(lab, _spatial(), _er(), _trdar(), "synthetic.csv@0000",
                                       _trdar_biz(), "synthetic-biz.csv@0000")
    score, _ = _score(combined)
    cmp = ms.compare_with_master_base(score, base, ORIGIN)
    assert cmp["identical"], cmp


def test_deterministic():
    a, _ = _score()
    b, _ = _score(_combined().sample(frac=1, random_state=3))  # 입력 순서가 달라도
    pd.testing.assert_frame_equal(a, b)
