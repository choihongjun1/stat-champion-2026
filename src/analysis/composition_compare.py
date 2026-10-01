"""S7: unweighted descriptive composition tables; no fitted models.

Inputs are stage_a and the eligible master_base panel, not raw licensing data.
Only the requested input parquet files are read by the CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pandas as pd

from src.data import config

HEADER = "두 자료의 구성 차이를 보여 주는 기술 통계이며, 재가중이나 검정을 하지 않았습니다."
RULE_VERSION = "S7-20261001-v1"
SAMPLES = ("A", "B", "C(전체)", "C(56)", "D")
COLUMNS = ("차원", "범주", *SAMPLES, "대응 수준", "정의 차이 주석")
TENURE_BINS = ("<1년", "1–3년", "3–5년", "5–10년", "10년 이상")
SIZE_BINS = ("1명", "2명", "3–4명", "5명 이상")
GUS = ("광진구", "마포구", "영등포구")
MAPPING = {
    "47": ((), "대응 없음", "서비스 패널에 소매업 없음"),
    "56": (("일반음식점", "휴게음식점"), "부분 대응", "인허가 업종과 산업분류의 정의 차이"),
    "96": (("미용업",), "부분 대응", "96에는 미용 외 업종도 포함; 소분류가 없어 미용 식별 불가"),
}
TENURE_NOTE = (
    "MDIS: 현재 사업자 운영 기간(인수·승계 시 재시작); "
    "패널: 인허가일 기준(승계·양도양수 시 왜곡 가능). "
    "출처: MDIS_CODEBOOK.md:17–18,35; MASTER_SPEC.md:37; labels.py:244–246"
)
DENOM_NOTE = "무가중; % 분모는 해당 열·차원의 유효 N; 결측·무효는 분모에서 제외(별도 행도 같은 유효 N 대비 %); 유효 N=0이면 %는 —"


def quarter_end(asof: str) -> pd.Timestamp:
    if not re.fullmatch(r"\d{4}Q[1-4]", asof):
        raise ValueError("panel-asof는 YYYYQ1–YYYYQ4 형식이어야 합니다")
    return pd.Period(asof, freq="Q").end_time.normalize()


def _require(df: pd.DataFrame, columns: list[str]) -> None:
    if any(c not in df.columns for c in columns):
        # Do not echo user-supplied column values or row identifiers.
        raise ValueError("필수 입력 열이 없습니다; 코드북과 CLI 열 이름을 확인하세요")


def _binary(series: pd.Series) -> pd.Series:
    """Reject unexpected encodings rather than interpreting strings as truthy."""
    out = pd.to_numeric(series, errors="coerce")
    if (series.notna() & (~out.isin([0, 1]))).any():
        raise ValueError("이진 열은 0/1 또는 bool이어야 합니다")
    return out


def tenure_categories(values: pd.Series, invalid: pd.Series | None = None) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("무효", index=values.index, dtype="object")
    result.loc[values.isna()] = "결측"
    valid = numeric.notna() & np.isfinite(numeric) & (numeric >= 0) & (numeric % 1 == 0)
    result.loc[valid] = pd.cut(
        numeric.loc[valid], [0, 12, 36, 60, 120, np.inf], right=False,
        labels=TENURE_BINS,
    ).astype("object")
    if invalid is not None:
        flag = _binary(invalid)
        result.loc[flag.isna()] = "결측"
        result.loc[flag.eq(1)] = "무효"
    return result


def size_categories(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("무효", index=values.index, dtype="object")
    result.loc[values.isna()] = "결측"
    valid = numeric.notna() & np.isfinite(numeric) & (numeric >= 1) & (numeric % 1 == 0)
    result.loc[valid] = pd.cut(
        numeric.loc[valid], [1, 2, 3, 5, np.inf], right=False, labels=SIZE_BINS,
    ).astype("object")
    return result


def _cell(categories: pd.Series, category: str, valid_labels: tuple[str, ...]) -> str:
    n = int(categories.eq(category).sum())
    if n < 5:
        return "<5 (<5)"
    denominator = int(categories.isin(valid_labels).sum())
    pct = f"{100 * n / denominator:.2f}%" if denominator else "—"
    return f"{n} ({pct})"


def select_panel(
    panel: pd.DataFrame, asof: str, *, biz_col: str = "biz_type",
    tenure_col: str = "age_months", license_col: str | None = None,
    close_col: str | None = None,
) -> pd.DataFrame:
    end = quarter_end(asof)
    _require(panel, ["store_id", "origin", "gu", biz_col])
    selected = panel.loc[panel["origin"].astype(str).eq(asof)].copy()
    if selected["store_id"].isna().any() or selected["store_id"].duplicated().any():
        raise ValueError("기준 분기의 store_id는 결측 없이 유일해야 합니다")
    if "origin_end" in selected:
        ends = pd.to_datetime(selected["origin_end"], errors="coerce")
        if not ends.eq(end).all():
            raise ValueError("origin_end가 기준 분기 말일과 다릅니다")
    # MASTER_SPEC.md:21,23 and labels.py:216–218: master_base rows already
    # satisfy license_date <= origin_end and (close_date is NA or > origin_end).
    # event_12m describes FUTURE closures and must not be used to filter here.
    if close_col is not None and license_col is None:
        raise ValueError("폐업일 열을 사용하려면 인허가일 열도 지정하세요")
    if license_col is not None:
        _require(selected, [license_col] + ([close_col] if close_col else []))
        licenses = pd.to_datetime(selected[license_col], errors="coerce")
        if licenses.isna().any():
            raise ValueError("인허가일 결측 또는 변환 실패: 영업 적격 판단 불가")
        eligible = licenses.le(end)
        if close_col:
            closes = pd.to_datetime(selected[close_col], errors="coerce")
            if (selected[close_col].notna() & closes.isna()).any():
                raise ValueError("폐업일 변환 실패: 영업 적격 판단 불가")
            eligible &= closes.isna() | closes.gt(end)
        # Same calendar-month difference as labels.py:244–246 (no day fraction).
        ages = (end.year - licenses.dt.year) * 12 + end.month - licenses.dt.month
        selected = selected.loc[eligible].copy()
        selected["_age"] = ages.loc[eligible]
    else:
        _require(selected, [tenure_col])
        selected["_age"] = selected[tenure_col]
    gu = selected["gu"].replace({"광진": "광진구", "마포": "마포구", "영등포": "영등포구"})
    if (gu.notna() & ~gu.isin(GUS)).any():
        raise ValueError("패널 지역은 문서의 서울 3구여야 합니다")
    selected["_gu"] = gu.fillna("결측")
    selected["_biz"] = selected[biz_col].fillna("결측")
    if (~selected["_biz"].isin(["일반음식점", "휴게음식점", "미용업", "결측"])).any():
        raise ValueError("패널 업종은 문서의 3개 인허가 업종이어야 합니다")
    return selected


def build_composition_table(
    mdis: pd.DataFrame, panel: pd.DataFrame, *, panel_asof: str = "2023Q4",
    panel_biz_col: str = "biz_type", panel_tenure_col: str = "age_months",
    panel_license_col: str | None = None, panel_close_col: str | None = None,
    panel_size_col: str | None = None, panel_size_definition: str = "unknown",
) -> pd.DataFrame:
    """Return only protected presentation cells; never include input identifiers."""
    _require(mdis, ["산업중분류코드", "is_seoul", "tenure_months", "tenure_invalid_flag",
                    "일반_합계종사자수", "treat_binary"])
    if panel_size_definition not in ("employees", "area", "unknown"):
        raise ValueError("규모 정의는 employees/area/unknown 중 하나여야 합니다")
    industry = mdis["산업중분류코드"].astype("string")
    a = mdis.loc[industry.isin(MAPPING)].copy()
    a["_industry"] = industry.loc[a.index]
    a["_seoul"] = _binary(a["is_seoul"])
    b = a.loc[a["_industry"].eq("56")]
    d = select_panel(panel, panel_asof, biz_col=panel_biz_col,
                     tenure_col=panel_tenure_col, license_col=panel_license_col,
                     close_col=panel_close_col)
    groups = dict(zip(SAMPLES, (a, b, a.loc[a["_seoul"].eq(1)],
                               b.loc[b["_seoul"].eq(1)], d)))
    rows: list[dict[str, str]] = []

    def add(dimension, category, cats, labels, level="대응", note=""):
        row = {"차원": dimension, "범주": category, "대응 수준": level,
               "정의 차이 주석": note + "; " + DENOM_NOTE}
        for key in SAMPLES:
            row[key] = _cell(cats[key], category, labels) if key in cats else "해당 없음"
        rows.append(row)

    # Cohort totals are counted from input, never hard-coded to the published 5042.
    totals = {key: pd.Series("전체 N", index=df.index) for key, df in groups.items()}
    add("표본", "전체 N", totals, ("전체 N",), note="A=47·56·96; B=56; C=서울 부분집합; D=기준 분기 적격 점포")
    industry_cats = {key: df["_industry"] for key, df in groups.items() if key != "D"}
    industry_cats["D"] = d["_biz"].map({"일반음식점": "56", "휴게음식점": "56", "미용업": "96", "결측": "결측"})
    for code, name in (("47", "소매업"), ("56", "음식점·주점업"), ("96", "개인서비스업")):
        types, level, reason = MAPPING[code]
        add("업종", code, industry_cats, tuple(MAPPING), level,
            f"MDIS {code} {name} ↔ 패널 {' + '.join(types) if types else '없음'}; {reason}")
    add("업종", "결측", industry_cats, tuple(MAPPING), "부분 대응")
    for dim, cats, labels, note in (
        ("업력", {key: tenure_categories(df["_age"]) if key == "D" else tenure_categories(df["tenure_months"], df["tenure_invalid_flag"]) for key, df in groups.items()}, TENURE_BINS, TENURE_NOTE),
        ("규모", {key: size_categories(df["일반_합계종사자수"]) for key, df in groups.items() if key != "D"}, SIZE_BINS, "MDIS: 대표자 포함 총 종사자수(MDIS_CODEBOOK.md:20)"),
    ):
        compare_size = panel_size_col is not None and panel_size_definition == "employees"
        if dim == "규모" and compare_size:
            _require(d, [panel_size_col])
            cats["D"] = size_categories(d[panel_size_col])
            note += "; 패널: 이용자가 대표자 포함 총 종사자수와 동일 정의임을 명시"
        for category in (*labels, "결측", "무효"):
            level = ("대응" if compare_size else "대응 없음") if dim == "규모" else "부분 대응"
            add(dim, category, cats, labels, level, note)
            if dim == "규모" and not compare_size:
                rows[-1]["D"] = ""
        if dim == "규모" and not compare_size:
            description = {"area": "면적", "unknown": "열 미지정 또는 정의 미확인", "employees": "열 미지정"}[panel_size_definition]
            rows.append(dict(zip(COLUMNS, ("규모", f"비교 불가 — 정의 다름(MDIS 종사자 수 / 패널 {description})", "", "", "", "", "", "대응 없음", "패널 값은 채우지 않음; MASTER_SPEC.md:39의 area는 면적"))))
    region = {key: df["_seoul"].map({1: "서울", 0: "서울 외"}).fillna("결측") for key, df in groups.items() if key != "D"}
    for category in ("서울", "서울 외", "결측"):
        add("지역(시도)", category, region, ("서울", "서울 외"), "대응 없음", "MDIS는 서울/서울 외만 표시; 구별 분포와 직접 대응하지 않음")
    for category in (*GUS, "결측"):
        add("지역(구)", category, {"D": d["_gu"]}, GUS, "대응 없음", "패널 3구 행정구역; MASTER_SPEC.md:44")
    treatment = {key: _binary(df["treat_binary"]).map({1: "있음", 0: "없음"}).fillna("결측") for key, df in groups.items() if key != "D"}
    for category in ("있음", "없음", "결측"):
        add("전자상거래 매출실적", category, treatment, ("있음", "없음"), "대응 없음", "MDIS만 제공; 패널 해당 정보 없음")
    return pd.DataFrame(rows, columns=COLUMNS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_outputs(table: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / "composition_table.csv", index=False, encoding="utf-8-sig")
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")
    lines = [HEADER, "", "각 셀: n (%). n<5인 셀은 두 값 모두 <5로 표시합니다.", "",
             "| " + " | ".join(COLUMNS) + " |", "| " + " | ".join(["---"] * len(COLUMNS)) + " |"]
    lines += ["| " + " | ".join(escape(x) for x in row) + " |" for row in table.itertuples(index=False, name=None)]
    (out_dir / "composition_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=HEADER)
    # Source: src/data/config.py MDIS_STAGE_A_PATH / MASTER_BASE_PATH;
    # docs/MASTER_SPEC.md:5,37–39,44 document eligible master_base and its columns.
    parser.add_argument("--mdis", type=Path, default=config.MDIS_STAGE_A_PATH)
    parser.add_argument("--panel", type=Path, default=config.MASTER_BASE_PATH)
    parser.add_argument("--panel-asof", default="2023Q4")
    parser.add_argument("--panel-biz-col", default="biz_type")
    parser.add_argument("--panel-tenure-col", default="age_months")
    parser.add_argument("--panel-license-col", help="선택: 인허가일 열; age_months 대신 분기 말까지 달력 개월 차 계산")
    parser.add_argument("--panel-close-col", help="인허가일 열과 함께 제공하면 분기 말 폐업 적격도 재확인")
    parser.add_argument("--panel-size-col", default=None)
    parser.add_argument("--panel-size-definition", choices=("employees", "area", "unknown"), default="unknown",
                        help="employees: 대표자 포함 총 종사자수와 같은 정의임을 이용자가 확인한 경우만")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/prescribe/composition/"))
    args = parser.parse_args(argv)
    try:
        quarter_end(args.panel_asof)
        # Hash before/after reading to reject concurrently replaced inputs.
        hashes = {name: _sha256(getattr(args, name)) for name in ("mdis", "panel")}
        mdis, panel = pd.read_parquet(args.mdis), pd.read_parquet(args.panel)
        table = build_composition_table(mdis, panel, **{key: getattr(args, key) for key in (
            "panel_asof", "panel_biz_col", "panel_tenure_col", "panel_license_col", "panel_close_col",
            "panel_size_col", "panel_size_definition")})
        if any(hashes[name] != _sha256(getattr(args, name)) for name in hashes):
            raise ValueError("읽는 동안 입력 파일이 변경되었습니다")
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.REPO_ROOT,
                                             text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = None
        meta = {
            "inputs": {name: {"path": str(getattr(args, name).resolve()), "sha256": hashes[name],
                              "rows": len(df)} for name, df in (("mdis", mdis), ("panel", panel))},
            "panel_asof": args.panel_asof, "panel_asof_end": str(quarter_end(args.panel_asof).date()),
            "mapping_rule_version": RULE_VERSION, "mapping_rules": MAPPING,
            "generated_commit_sha": commit,
            "package_versions": {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "pyarrow")},
            "column_arguments": {key: getattr(args, key) for key in ("panel_biz_col", "panel_tenure_col", "panel_license_col", "panel_close_col", "panel_size_col", "panel_size_definition")},
            "panel_eligibility": "master_base 계약: 인허가일≤분기 말, 폐업일 없음 또는 분기 말 이후; MASTER_SPEC.md:21,23",
            "denominator_policy": DENOM_NOTE, "small_cell_threshold": 5,
        }
        write_outputs(table, args.out_dir)
        (args.out_dir / "composition_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"입력 또는 출력 처리 실패 ({type(exc).__name__}); 경로·스키마·CLI 인자를 확인하세요\n")
    print("구성 비교표 CSV·MD·메타데이터 생성 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
