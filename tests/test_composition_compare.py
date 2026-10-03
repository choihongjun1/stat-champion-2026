"""S7 checks use synthetic inputs only; no project input files are opened."""
import hashlib
import json
import re

import numpy as np
import pandas as pd
import pytest

from src.analysis.composition_compare import (
    HEADER, SIZE_BINS, TENURE_BINS, build_composition_table, main,
    quarter_end, select_panel, size_categories, tenure_categories,
)


@pytest.fixture
def frames():
    # Five copies ensure every regular category can be inspected above threshold.
    mdis = pd.DataFrame([
        {"산업중분류코드": code, "행정구역시도코드": "11" if seoul else "26",
         "is_seoul": seoul, "tenure_months": age, "tenure_invalid_flag": 0,
         "일반_합계종사자수": size, "treat_binary": treat}
        for code in ("47", "56", "96") for seoul in (True, False)
        for age in (0, 12, 36, 60, 120) for size in (1, 2, 3, 5)
        for treat in (0, 1) for _ in range(5)
    ])
    panel = pd.DataFrame([
        {"store_id": f"SYN-{i:06d}", "origin": origin, "origin_end": quarter_end(origin),
         "biz_type": biz, "gu": gu, "age_months": age, "workers": size,
         "area": 35.0, "event_12m": 1}
        for i, (origin, biz, gu, age, size) in enumerate(
            (o, b, g, a, s) for o in ("2023Q3", "2023Q4", "2024Q1")
            for b in ("일반음식점", "휴게음식점", "미용업")
            for g in ("광진구", "마포구", "영등포구")
            for a in (0, 12, 36, 60, 120) for s in (1, 2, 3, 5) for _ in range(5)
        )
    ])
    return mdis, panel


def cell(table, dim, category, column):
    return table.loc[table["차원"].eq(dim) & table["범주"].eq(category), column].item()


def unpack(value):
    match = re.fullmatch(r"(\d+) \(([\d.]+)%\)", value)
    assert match, value
    return int(match[1]), float(match[2])


def test_sample_counts_and_valid_percentages(frames):
    mdis, panel = frames
    table = build_composition_table(mdis, panel, panel_size_col="workers", panel_size_definition="employees")
    expected = {"A": 1200, "B": 400, "C(전체)": 600, "C(56)": 200, "D": 900}
    for column, n in expected.items():
        assert unpack(cell(table, "표본", "전체 N", column)) == (n, 100)
        for dim, categories in (("업력", TENURE_BINS), ("규모", SIZE_BINS)):
            values = [unpack(cell(table, dim, category, column)) for category in categories]
            assert sum(x[0] for x in values) == n
            assert sum(x[1] for x in values) == pytest.approx(100, abs=.03)
    assert sum(unpack(cell(table, "업종", code, "A"))[1] for code in ("47", "56", "96")) == pytest.approx(100, abs=.02)
    assert sum(unpack(cell(table, "지역(구)", gu, "D"))[1] for gu in ("광진구", "마포구", "영등포구")) == pytest.approx(100, abs=.02)
    assert unpack(cell(table, "업종", "56", "D"))[0] == 600
    assert cell(table, "전자상거래 매출실적", "있음", "D") == "해당 없음"
    assert unpack(cell(table, "전자상거래 매출실적", "있음", "A"))[1] == 50


@pytest.mark.parametrize("months,expected", [(0, "<1년"), (11, "<1년"), (12, "1–3년"), (35, "1–3년"), (36, "3–5년"), (59, "3–5년"), (60, "5–10년"), (119, "5–10년"), (120, "10년 이상")])
def test_tenure_boundaries(months, expected):
    assert tenure_categories(pd.Series([months])).item() == expected


def test_invalid_missing_and_denominators(frames):
    mdis, panel = frames
    bad = mdis.iloc[:10].copy()
    bad.loc[bad.index[:5], "tenure_months"] = np.nan
    bad.loc[bad.index[:5], "tenure_invalid_flag"] = 1
    bad.loc[bad.index[5:], "tenure_months"] = np.nan
    mdis = pd.concat([mdis, bad], ignore_index=True)
    table = build_composition_table(mdis, panel)
    assert cell(table, "업력", "무효", "A").startswith("5 (")
    assert cell(table, "업력", "결측", "A").startswith("5 (")
    assert sum(unpack(cell(table, "업력", x, "A"))[0] for x in TENURE_BINS) == 1200
    assert sum(unpack(cell(table, "업력", x, "A"))[1] for x in TENURE_BINS) == 100
    assert table.loc[table["차원"].eq("업력"), "정의 차이 주석"].str.contains("결측·무효는 분모에서 제외").all()
    assert tenure_categories(pd.Series([-1, 1.5, np.inf, None, "bad"])).tolist() == ["무효", "무효", "무효", "결측", "무효"]


def test_quarter_filter_keeps_future_closures(frames):
    _, panel = frames
    selected = select_panel(panel, "2023Q4")
    assert len(selected) == 900
    assert selected["origin"].unique().tolist() == ["2023Q4"]
    assert selected["event_12m"].eq(1).all()
    assert len(select_panel(panel, "2023Q3")) == 900


def test_license_month_difference_and_operating_cutoff():
    panel = pd.DataFrame({"store_id": [f"SYN-{i:06d}" for i in range(6)],
        "origin": ["2023Q4"] * 6, "biz_type": ["미용업"] * 6, "gu": ["마포"] * 6,
        "licensed": ["2022-12-31", "2023-12-31", "2024-01-01", "2020-01-01", "2020-01-01", "2020-01-01"],
        "closed": [None, None, None, "2023-12-31", "2024-01-01", "2023-12-30"]})
    selected = select_panel(panel, "2023Q4", license_col="licensed", close_col="closed")
    assert selected.index.tolist() == [0, 1, 4]
    assert selected["_age"].tolist() == [12, 0, 47]


@pytest.mark.parametrize("kwargs", [{}, {"panel_size_col": "area", "panel_size_definition": "area"}, {"panel_size_col": "workers"}])
def test_size_requires_same_definition(frames, kwargs):
    table = build_composition_table(*frames, **kwargs)
    size = table.loc[table["차원"].eq("규모")]
    assert size["범주"].str.contains("비교 불가 — 정의 다름").any()
    assert size["D"].eq("").all()
    assert size["대응 수준"].eq("대응 없음").all()


def test_size_bins():
    assert size_categories(pd.Series([1, 2, 3, 4, 5, 200, 0, 1.5, np.nan])).tolist() == ["1명", "2명", "3–4명", "3–4명", "5명 이상", "5명 이상", "무효", "무효", "결측"]


def test_small_cells_and_mapping(frames):
    mdis, panel = frames
    table = build_composition_table(mdis.iloc[:4], panel.iloc[:4], panel_asof="2023Q3")
    assert cell(table, "표본", "전체 N", "A") == "<5 (<5)"
    assert cell(table, "표본", "전체 N", "D") == "<5 (<5)"
    assert cell(table, "표본", "전체 N", "B") == "0 (—)"
    assert cell(table, "업종", "56", "대응 수준") == "부분 대응"
    assert cell(table, "업종", "96", "대응 수준") == "부분 대응"
    assert cell(table, "업종", "47", "대응 수준") == "대응 없음"
    assert cell(table, "업종", "96", "정의 차이 주석").startswith("MDIS 96 개인서비스업")


def test_cli_outputs_provenance_and_language(frames, tmp_path):
    mdis, panel = frames
    mpath, ppath = tmp_path / "mdis.parquet", tmp_path / "panel.parquet"
    mdis.to_parquet(mpath, index=False)
    panel.to_parquet(ppath, index=False)
    out = tmp_path / "out"
    assert main(["--mdis", str(mpath), "--panel", str(ppath), "--out-dir", str(out)]) == 0
    md = (out / "composition_table.md").read_text(encoding="utf-8")
    assert md.startswith(HEADER)
    assert "0 (0.00%)" in md
    assert "D56=그중 일반음식점+휴게음식점" in md
    csv = pd.read_csv(out / "composition_table.csv", keep_default_na=False)
    assert csv.equals(build_composition_table(mdis, panel))
    meta = json.loads((out / "composition_meta.json").read_text(encoding="utf-8"))
    for name, path, n in (("mdis", mpath, len(mdis)), ("panel", ppath, len(panel))):
        assert meta["inputs"][name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert meta["inputs"][name]["rows"] == n
        assert meta["inputs"][name]["path"] == str(path.resolve())
    assert meta["panel_asof"] == "2023Q4"
    assert meta["panel_asof_end"] == "2023-12-31"
    assert meta["mapping_rule_version"] == "S7-20261001-v2"
    assert meta["masking_change_reason"] == "0은 공표 위험이 없어 표시"
    assert re.fullmatch(r"[0-9a-f]{40}", meta["generated_commit_sha"])
    assert set(meta["package_versions"]) == {"pandas", "numpy", "pyarrow"}
    # C1 is absent from origin/main at the task baseline: direct requested list.
    for artifact in out.iterdir():
        text = artifact.read_text(encoding="utf-8-sig")
        assert not any(word in text for word in ("대표성 확인", "일반화 가능", "독립 검증", "유의"))
        assert "SYN-" not in text


@pytest.mark.parametrize("problem", ["quarter", "duplicate", "end", "binary", "schema", "region", "license"])
def test_invalid_input_rejected(frames, problem):
    mdis, panel = (frame.copy() for frame in frames)
    kwargs = {}
    if problem == "quarter":
        kwargs["panel_asof"] = "2023Q5"
    elif problem == "duplicate":
        panel = pd.concat([panel, panel.loc[panel["origin"].eq("2023Q4")].iloc[:1]], ignore_index=True)
    elif problem == "end":
        panel.loc[panel["origin"].eq("2023Q4"), "origin_end"] = pd.Timestamp("2023-10-01")
    elif problem == "binary":
        mdis.loc[0, "treat_binary"] = 2
    elif problem == "schema":
        panel = panel.drop(columns="gu")
    elif problem == "region":
        panel.loc[panel["origin"].eq("2023Q4"), "gu"] = "다른구"
    elif problem == "license":
        panel["licensed"] = "bad"
        kwargs["panel_license_col"] = "licensed"
    with pytest.raises(ValueError):
        build_composition_table(mdis, panel, **kwargs)


def test_cli_error_no_outputs(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        main(["--mdis", str(tmp_path / "absent.parquet"), "--out-dir", str(out)])
    assert exc.value.code == 2
    assert not out.exists()


@pytest.mark.parametrize("dtype", ["bool", "int64"])
def test_region_binary_mapping(frames, dtype):
    mdis, panel = frames
    mdis = mdis.copy()
    mdis["is_seoul"] = mdis["is_seoul"].astype(dtype)
    table = build_composition_table(mdis, panel)
    assert unpack(cell(table, "지역(시도)", "서울", "A")) == (600, 50)
    assert unpack(cell(table, "지역(시도)", "서울 외", "A")) == (600, 50)
    assert cell(table, "지역(시도)", "결측", "A") == "0 (0.00%)"
    assert unpack(cell(table, "지역(시도)", "서울", "C(전체)")) == (600, 100)


def test_binary_nullable_integer():
    from src.analysis.composition_compare import _binary
    result = _binary(pd.Series([True, False, None], dtype="boolean"))
    assert str(result.dtype) == "Int64"
    assert result.iloc[:2].tolist() == [1, 0]
    assert pd.isna(result.iloc[2])


@pytest.mark.parametrize("n,expected", [(0, "0 (0.00%)"), (1, "<5 (<5)"), (4, "<5 (<5)"), (5, "5 (100.00%)")])
def test_masking_boundaries(n, expected):
    from src.analysis.composition_compare import _cell
    series = pd.Series(["hit"] * n + (["other"] * 5 if n == 0 else []), dtype="object")
    assert _cell(series, "hit", ("hit", "other")) == expected


def test_zero_denominator():
    from src.analysis.composition_compare import _cell
    assert _cell(pd.Series([], dtype="object"), "hit", ("hit",)) == "0 (—)"


def test_d56_counts_and_distribution(frames):
    mdis, panel = frames
    panel = panel.copy()
    panel.loc[panel["biz_type"].eq("미용업"), "age_months"] = 200
    table = build_composition_table(mdis, panel)
    assert unpack(cell(table, "표본", "전체 N", "D56")) == (600, 100)
    assert unpack(cell(table, "업종", "56", "D56")) == (600, 100)
    assert cell(table, "업종", "96", "D56") == "0 (0.00%)"
    assert sum(unpack(cell(table, "업력", x, "D56"))[0] for x in TENURE_BINS) == 600
    assert all(unpack(cell(table, "업력", x, "D56")) == (120, 20) for x in TENURE_BINS)
    assert unpack(cell(table, "업력", "10년 이상", "D"))[0] == 420
    assert sum(unpack(cell(table, "지역(구)", x, "D56"))[0] for x in ("광진구", "마포구", "영등포구")) == 600
