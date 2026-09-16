# -*- coding: utf-8 -*-
"""인허가 3종(일반음식점·휴게음식점·미용업) 주소·PNU·좌표 표준화 파이프라인.

실행:
    python -m src.data.standardize

산출물 (outputs/standardized/, git 미추적):
    - licenses_3gu.parquet        표준화 결과
    - qa_report.md                QA 리포트
    - pnu_validation_sample.csv   임의 표본 PNU 검증표 (소진공 지번코드 대조)

범위 제한: 소진공 entity resolution / 상권 spatial join / 폐업 라벨 정의는
이 단계에서 수행하지 않는다.
"""
from __future__ import annotations

import glob
import sys

import pandas as pd

from src.data import address, coords, io_license
from src.data.config import (
    BUSINESS_TYPES,
    LICENSE_DIR,
    OUTPUT_DIR,
    RAW_ENCODING,
    RAW_ENCODING_ERRORS,
    SEMAS_DIR,
    TARGET_GUS,
)
from src.data.names import normalize_name

PNU_SAMPLE_N = 40
PNU_SAMPLE_SEED = 20260916

# 최종 산출물 컬럼 순서
OUTPUT_COLUMNS = [
    # 식별
    "store_id", "source", "business_type", "mgmt_no", "gov_code", "gu",
    # 상호
    "name_raw", "name_norm", "branch", "uptae_raw",
    # 주소·PNU
    "addr_raw", "road_addr_raw", "addr_gu", "gu_mismatch", "dong", "san",
    "bunji_main", "bunji_sub", "bjd_code", "pnu", "parse_status",
    # 좌표
    "x_raw", "y_raw", "x_5179", "y_5179", "coord_missing", "coord_suspect",
    # 시간·상태 (다음 단계의 라벨·업력 계산에 필요 — 손실 없이 유지)
    "license_date_raw", "license_date", "close_date_raw", "close_date",
    "status_code", "status_name", "detail_status_code", "detail_status_name",
    "update_type", "last_modified_raw", "data_updated_raw",
]


def _raw_schemas() -> dict[str, list[str]]:
    """업종별 raw 파일의 전체 컬럼 목록 (QA의 schema 차이 보고용)."""
    out = {}
    for btype, info in BUSINESS_TYPES.items():
        df = pd.read_csv(
            LICENSE_DIR / info["file"],
            encoding=RAW_ENCODING,
            encoding_errors=RAW_ENCODING_ERRORS,
            nrows=0,
        )
        out[btype] = list(df.columns)
    return out


def _load_semas_pnu_reference() -> pd.DataFrame:
    """소진공 3구 지번코드(PNU)와 대표 지번주소 (PNU 표본 검증용, 전 스냅샷 union)."""
    frames = []
    for f in sorted(glob.glob(str(SEMAS_DIR / "*.csv"))):
        df = pd.read_csv(
            f, dtype=str, usecols=["시군구명", "지번코드", "지번주소"]
        )
        frames.append(df[df["시군구명"].isin(TARGET_GUS)].drop_duplicates("지번코드"))
    ref = pd.concat(frames).drop_duplicates("지번코드")
    return ref.rename(columns={"지번코드": "pnu", "지번주소": "semas_addr"})[
        ["pnu", "semas_addr"]
    ]


def standardize_one(business_type: str, bjd_mapping: dict) -> tuple[pd.DataFrame, dict]:
    """업종 하나를 로딩→3구 필터→표준화하고 (결과, QA 통계)를 반환한다."""
    qa: dict = {"business_type": business_type}

    raw = io_license.load_license_raw(business_type)
    qa["rows_input"] = len(raw)

    df = io_license.filter_target_gu(raw)
    qa["rows_3gu"] = len(df)
    qa["replacement_char_cells"] = io_license.count_replacement_chars(df)

    # store_id
    prefix = BUSINESS_TYPES[business_type]["prefix"]
    df["store_id"] = prefix + "_" + df["mgmt_no"]

    # 주소 파싱 + PNU
    parsed = address.parse_address_series(df["addr_raw"], bjd_mapping)
    df = pd.concat([df, parsed], axis=1)
    # 주소의 구가 관할 구와 다른 경우 (삭제하지 않고 플래그만)
    df["gu_mismatch"] = df["addr_gu"].notna() & (df["addr_gu"] != df["gu"])

    # 상호명
    norm = [normalize_name(v) for v in df["name_raw"]]
    df["name_norm"] = [a for a, _ in norm]
    df["branch"] = [b for _, b in norm]

    # 좌표
    cdf = coords.standardize_coords(df["x_raw"], df["y_raw"])
    df = df.drop(columns=["x_raw", "y_raw"])
    df = pd.concat([df, cdf], axis=1)

    # 날짜 (raw 문자열 보존 + 파싱본 추가)
    for col in ("license_date", "close_date"):
        raw_col = f"{col}_raw"
        df[col] = pd.to_datetime(df[raw_col], format="%Y-%m-%d", errors="coerce")
        qa[f"{col}_unparsed"] = int((df[raw_col].notna() & df[col].isna()).sum())
        bad = df.loc[df[raw_col].notna() & df[col].isna(), raw_col]
        qa[f"{col}_unparsed_examples"] = bad.head(5).tolist()

    qa["parse_status_counts"] = df["parse_status"].value_counts().to_dict()
    qa["pnu_success_rate"] = float(df["pnu"].notna().mean())
    qa["gu_mismatch_rows"] = int(df["gu_mismatch"].sum())
    qa["coord_missing_rate"] = float(df["coord_missing"].mean())
    qa["coord_suspect_rows"] = int(df["coord_suspect"].sum())
    qa["rows_by_gu"] = df["gu"].value_counts().to_dict()

    return df[OUTPUT_COLUMNS], qa


def run() -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("법정동 매핑 구축 (소진공)...")
    bjd_mapping = address.build_bjd_mapping()
    n_target_dong = sum(1 for gu, _ in bjd_mapping if gu in TARGET_GUS)
    print(f"  서울 전체 {len(bjd_mapping)}개 동, 3구 {n_target_dong}개 동")

    frames, qas = [], []
    for btype in BUSINESS_TYPES:
        print(f"표준화: {btype} ...")
        df, qa = standardize_one(btype, bjd_mapping)
        frames.append(df)
        qas.append(qa)
        print(f"  입력 {qa['rows_input']:,} → 3구 {qa['rows_3gu']:,}, "
              f"PNU 성공률 {qa['pnu_success_rate']:.4f}, "
              f"좌표 결측률 {qa['coord_missing_rate']:.4f}")

    out = pd.concat(frames, ignore_index=True)

    # store_id 유일성은 하드 체크 (실패 시 산출 중단)
    dup = out["store_id"].duplicated().sum()
    if dup:
        raise AssertionError(f"store_id 중복 {dup}건 — 규칙 재검토 필요")

    parquet_path = OUTPUT_DIR / "licenses_3gu.parquet"
    out.to_parquet(parquet_path, index=False)
    print(f"저장: {parquet_path} ({len(out):,} rows)")

    # PNU 표본 검증표 + 소진공 대조
    semas_ref = _load_semas_pnu_reference()
    write_qa_report(out, qas, semas_ref, bjd_mapping)
    return out


def write_qa_report(
    out: pd.DataFrame, qas: list[dict], semas_ref: pd.DataFrame, bjd_mapping: dict
) -> None:
    bbox = coords.seoul_bbox_5179()
    semas_pnu = set(semas_ref["pnu"])

    ok = out[out["pnu"].notna()]
    pnu_in_semas = ok["pnu"].isin(semas_pnu)

    # 임의 표본: 조립 PNU를 소진공 지번코드·지번주소와 눈으로 대조할 수 있는 구조
    sample = (
        ok.sample(n=min(PNU_SAMPLE_N, len(ok)), random_state=PNU_SAMPLE_SEED)[
            ["store_id", "business_type", "gu", "addr_raw", "dong", "san",
             "bunji_main", "bunji_sub", "pnu"]
        ]
        .merge(semas_ref, on="pnu", how="left")
    )
    sample["pnu_in_semas"] = sample["semas_addr"].notna()
    sample_path = OUTPUT_DIR / "pnu_validation_sample.csv"
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")

    valid_xy = out[~out["coord_missing"]]
    lines = ["# 인허가 3종 표준화 QA 리포트", ""]
    lines.append(f"- 생성 스크립트: `python -m src.data.standardize`")
    lines.append(f"- 총 출력 행수: {len(out):,} / store_id 중복: 0 (하드 체크 통과)")
    lines.append("")

    lines.append("## 업종별 입력/출력")
    lines.append("")
    lines.append("| 업종 | 입력(전체) | 3구 필터 후 | PNU 성공률 | 좌표 결측률 |")
    lines.append("|---|---|---|---|---|")
    for qa in qas:
        lines.append(
            f"| {qa['business_type']} | {qa['rows_input']:,} | {qa['rows_3gu']:,} "
            f"| {qa['pnu_success_rate']:.4f} | {qa['coord_missing_rate']:.4f} |"
        )
    lines.append("")
    lines.append("주의: 3구 필터는 개방자치단체코드(관할) 기준. audit(주소 문자열 기준 추정)")
    lines.append("실측치와 수 건 수준의 차이가 있을 수 있다. 관할 코드와 주소 구가 다른 행은")
    lines.append("삭제하지 않고 `gu_mismatch=True`로 플래그했다.")
    lines.append("")

    lines.append("## 3구별 행수 (업종별)")
    lines.append("")
    lines.append("| 업종 | " + " | ".join(TARGET_GUS) + " |")
    lines.append("|---|" + "---|" * len(TARGET_GUS))
    for qa in qas:
        cells = " | ".join(f"{qa['rows_by_gu'].get(g, 0):,}" for g in TARGET_GUS)
        lines.append(f"| {qa['business_type']} | {cells} |")
    lines.append("")

    lines.append("## 주소 파싱 / PNU")
    lines.append("")
    lines.append(f"- 법정동 매핑: 소진공 7개 스냅샷 union, 서울 {len(bjd_mapping)}개 (구,동) 키,")
    lines.append(f"  3구 {sum(1 for gu, _ in bjd_mapping if gu in TARGET_GUS)}개 동. (구,동)→법정동코드 1:1 검증 통과.")
    for qa in qas:
        lines.append(f"- {qa['business_type']} parse_status: {qa['parse_status_counts']}")
        lines.append(f"  - gu_mismatch(주소 구 ≠ 관할 구): {qa['gu_mismatch_rows']}건")
    lines.append("")
    lines.append(f"- 조립 PNU의 소진공 지번코드 존재율: {pnu_in_semas.mean():.4f}")
    lines.append("  (소진공에 상가가 없는 필지는 원래 미존재하므로 100%가 목표치는 아님 — 참고 지표)")
    lines.append(f"- 임의 표본 {len(sample)}건 검증표: `pnu_validation_sample.csv`")
    lines.append(f"  (표본 중 소진공 일치 {int(sample['pnu_in_semas'].sum())}건 — 지번주소 문자열 눈 대조 가능)")
    lines.append("")

    lines.append("## 좌표")
    lines.append("")
    lines.append(f"- 변환: EPSG:5174 → EPSG:5179 (raw 좌표 보존, x/y 명명 유지)")
    lines.append(
        f"- 서울 bbox(5179): x [{bbox['x_min']:.0f}, {bbox['x_max']:.0f}], "
        f"y [{bbox['y_min']:.0f}, {bbox['y_max']:.0f}]"
    )
    if len(valid_xy):
        lines.append(
            f"- 변환 좌표 실측 범위: x [{valid_xy['x_5179'].min():.0f}, {valid_xy['x_5179'].max():.0f}], "
            f"y [{valid_xy['y_5179'].min():.0f}, {valid_xy['y_5179'].max():.0f}]"
        )
    n_suspect = int(out["coord_suspect"].sum())
    lines.append(f"- 서울 bbox 밖(coord_suspect): {n_suspect}건")
    if n_suspect:
        sus = out[out["coord_suspect"]][["store_id", "addr_raw", "x_raw", "y_raw"]]
        lines.append(f"  예시: {sus.head(5).to_dict('records')}")
    lines.append("")

    lines.append("## 날짜 파싱")
    lines.append("")
    for qa in qas:
        lines.append(
            f"- {qa['business_type']}: 인허가일자 파싱 실패 {qa['license_date_unparsed']}건 "
            f"{qa['license_date_unparsed_examples']}, "
            f"폐업일자 파싱 실패 {qa['close_date_unparsed']}건 "
            f"{qa['close_date_unparsed_examples']}"
        )
    lines.append("")

    lines.append("## 인코딩 (CP949 치환문자 포함 셀)")
    lines.append("")
    for qa in qas:
        lines.append(f"- {qa['business_type']}: {qa['replacement_char_cells'] or '없음'}")
    lines.append("")

    lines.append("## 업종별 raw schema 차이")
    lines.append("")
    schemas = _raw_schemas()
    common = set.intersection(*[set(v) for v in schemas.values()])
    lines.append(f"- 공통 컬럼 {len(common)}개")
    for btype, cols_ in schemas.items():
        extra = [c for c in cols_ if c not in common]
        lines.append(f"- {btype}: 전체 {len(cols_)}열, 고유 {len(extra)}개 — {extra}")
    lines.append("")

    report_path = OUTPUT_DIR / "qa_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"QA 리포트: {report_path}")


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
