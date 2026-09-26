# -*- coding: utf-8 -*-
"""W2 경쟁지표 6종 — 인허가 기반 `(store_id, origin)` 테이블 (DECISIONS.md 2026-09-26 "W2 경쟁지표 6종 정의").

master_base·master_score에는 넣지 않는다(DECISIONS.md 2026-09-25). 이 모듈은 패널의 키를 받아
별도 테이블을 만들고, 모델 단계가 `master.attach_enriched_table`처럼 m:1로 붙인다.

정의 (t = origin_end, 모든 점포 수는 자기 점포 제외)
- comp_pnu_cnt            같은 PNU, t 당시 영업 중인 3개 업종 인허가 점포 수
- comp_pnu_same_type_cnt  같은 PNU·같은 biz_type
- comp_dong_same_type_cnt 같은 법정동(`bjd_code` 10자리)·같은 biz_type
- comp_dong_open_4q       같은 법정동·업종에서 인허가일 ∈ (t−12개월, t]인 점포 수 (자기 개업 제외)
- comp_dong_close_4q      같은 법정동·업종에서 폐업일 ∈ (t−12개월, t]인 점포 수
- comp_dong_density_yoy   같은 법정동·업종 전체 영업 점포 수의 전년 대비 증감률 (N_t − N_{t−12개월}) / N_{t−12개월}.
                          **면적당 밀도가 아니라 점포 수 증감률이다.** 분모 0이면 NA.
                          N_t − N_{t−12개월} = (창 안 개업 전체) − (창 안 폐업 전체)가 항등식으로 성립하므로
                          open_4q·close_4q와 정보가 겹친다 (규모로 나눈 값만 새로 더해진다).

영업 판정: labels.build_long_panel과 같은 규칙 — 인허가일 ≤ t 이고 (폐업일 없음 또는 폐업일 > t).
현재 영업상태명은 쓰지 않는다. 기준일 당일 개업은 영업 중, 당일 폐업은 영업 아님(폐업 집계에는 포함).
`event_12m`은 읽지 않는다.

위치 키
- PNU가 없으면 PNU 지표 NA, `bjd_code`가 없으면 동 지표 NA (경쟁 점포 0개로 두지 않는다).
- `bjd_code` 앞 5자리가 점포의 자치구(`gu`, 개방자치단체코드 기준 모집단)와 다르면 위치 키를 믿을 수 없어
  PNU·동 지표를 모두 NA로 두고 경쟁 점포 집계에서도 뺀다 (`comp_location_status="gu_bjd_mismatch"`).
  다른 자치구로 옮겨 집계하지 않는다.

시점 한계 (provenance)
- 위치(PNU·법정동)는 인허가 파일의 **현재 스냅샷 주소**를 과거 origin에 소급한 것이다 (이전 식별 불가).
- `comp_feature_asof = origin_end`는 논리적 관측 기준일이다. 인허가 기록이 그날 실제로 공개돼 있었는지는
  확인되지 않았다 → `comp_available_at`은 NA, `comp_available_at_basis="unverified"`.

실행:
    python -m src.data.competition_features                                   # master_base → competition_features.parquet
    python -m src.data.competition_features --panel outputs/master/master_score.parquet
        # → competition_features_score_2026Q2.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from src.data import config

KEY = ["store_id", "origin"]
FEATURES = [
    "comp_pnu_cnt",
    "comp_pnu_same_type_cnt",
    "comp_dong_same_type_cnt",
    "comp_dong_open_4q",
    "comp_dong_close_4q",
    "comp_dong_density_yoy",
]
COUNT_FEATURES = FEATURES[:5]
META = [
    "comp_feature_asof",        # 논리적 관측 기준일 = origin_end
    "comp_available_at",        # 실제 공개 시점 — 확인되지 않아 NA
    "comp_available_at_basis",  # "unverified"
    "comp_source_snapshot",     # licenses_3gu.parquet@sha256[:12]
    "comp_raw_last_observed",   # 원천의 최종 인허가·폐업일 (raw 추출 시점의 하한)
    "comp_location_basis",      # "license_current_address" — 현재 주소를 과거 origin에 소급
    "comp_location_status",     # ok / pnu_missing / no_location / gu_bjd_mismatch
]
PNU_FEATURES = ["comp_pnu_cnt", "comp_pnu_same_type_cnt"]
DONG_FEATURES = ["comp_dong_same_type_cnt", "comp_dong_open_4q", "comp_dong_close_4q", "comp_dong_density_yoy"]

WINDOW_MONTHS = 12
# 법정동 코드 앞 5자리(시군구) — 모집단 자치구와 대조하는 QA 기준
GU_SIGUNGU_CODE = {"광진구": "11215", "마포구": "11440", "영등포구": "11560"}
AVAILABLE_AT_BASIS = "unverified"
LOCATION_BASIS = "license_current_address"

LICENSES_PATH = config.OUTPUT_DIR / "licenses_3gu.parquet"
COMPETITION_OUTPUT_DIR = config.REPO_ROOT / "outputs" / "competition"
LICENSE_COLS = ["store_id", "business_type", "gu", "pnu", "bjd_code", "license_date", "close_date"]


class CompetitionValidationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
def _blank_to_na(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    return s.mask(s == "")


def prepare_licenses(lic: pd.DataFrame) -> pd.DataFrame:
    """licenses_3gu 형식 → 집계용 표. 위치 상태를 붙이고, 믿을 수 없는 위치 키는 비운다."""
    missing = [c for c in LICENSE_COLS if c not in lic.columns]
    if missing:
        raise CompetitionValidationError(f"인허가 테이블에 컬럼이 없다: {missing}")
    out = lic[LICENSE_COLS].rename(columns={"business_type": "biz_type"}).copy()
    if out["store_id"].duplicated().any():
        raise CompetitionValidationError("인허가 테이블 store_id 중복")
    unknown_gu = sorted(set(out["gu"].dropna()) - set(GU_SIGUNGU_CODE))
    if unknown_gu or out["gu"].isna().any():
        raise CompetitionValidationError(f"3개 구 밖 또는 결측 자치구: {unknown_gu}")
    out["pnu"] = _blank_to_na(out["pnu"])
    out["bjd_code"] = _blank_to_na(out["bjd_code"])
    out["license_date"] = pd.to_datetime(out["license_date"])
    out["close_date"] = pd.to_datetime(out["close_date"])
    if out["license_date"].isna().any():
        raise CompetitionValidationError("인허가일 결측 — 영업 판정 불가")

    expected = out["gu"].map(GU_SIGUNGU_CODE)
    mismatch = out["bjd_code"].notna() & (out["bjd_code"].str[:5] != expected)
    status = np.select(
        [mismatch, out["pnu"].isna() & out["bjd_code"].isna(), out["pnu"].isna()],
        ["gu_bjd_mismatch", "no_location", "pnu_missing"], default="ok")
    out["location_status"] = status
    out.loc[mismatch, ["pnu", "bjd_code"]] = pd.NA
    return out


def load_licenses(path: Path = LICENSES_PATH) -> pd.DataFrame:
    return prepare_licenses(pd.read_parquet(path, columns=LICENSE_COLS))


# ---------------------------------------------------------------------------
# 시점 판정
# ---------------------------------------------------------------------------
def is_open_at(lic: pd.DataFrame, t: pd.Timestamp) -> pd.Series:
    """labels.build_long_panel의 적격 조건과 같다."""
    return (lic["license_date"] <= t) & (lic["close_date"].isna() | (lic["close_date"] > t))


def in_window(dates: pd.Series, t: pd.Timestamp) -> pd.Series:
    """(t − 12개월, t]"""
    return (dates > t - pd.DateOffset(months=WINDOW_MONTHS)) & (dates <= t)


def _origin_end(panel: pd.DataFrame) -> dict[str, pd.Timestamp]:
    ends = panel.groupby("origin")["origin_end"].agg(["min", "max"])
    if (ends["min"] != ends["max"]).any():
        raise CompetitionValidationError("같은 origin 안에서 origin_end가 다르다")
    out = {}
    for o, t in ends["min"].items():
        t = pd.Timestamp(t).normalize()
        if t != pd.Period(o, freq="Q").end_time.normalize():
            raise CompetitionValidationError(f"{o}의 origin_end {t.date()}가 분기 말이 아니다")
        out[o] = t
    return out


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------
def _lookup(counts: pd.Series, keys: pd.MultiIndex | pd.Index, fill_zero: bool) -> np.ndarray:
    v = counts.reindex(keys).to_numpy(dtype=float)
    return np.where(np.isnan(v), 0.0, v) if fill_zero else v


def compute(panel: pd.DataFrame, lic: pd.DataFrame) -> pd.DataFrame:
    """패널과 같은 행 순서의 계산 표. 검증용 중간값(N_t, N_prev, 창 안 개업·폐업 전체)을 함께 낸다.

    lic는 `prepare_licenses`를 거친 표. panel은 store_id·origin·origin_end만 쓴다 (event_12m은 읽지 않는다).
    """
    p = panel[["store_id", "origin", "origin_end"]].copy()
    p["origin_end"] = pd.to_datetime(p["origin_end"])
    if p.duplicated(KEY).any():
        raise CompetitionValidationError("패널 (store_id, origin) 중복")
    ends = _origin_end(p)
    x = p.reset_index(drop=True).merge(lic, on="store_id", how="left", validate="m:1")
    if x["biz_type"].isna().any():
        raise CompetitionValidationError(f"인허가 테이블에 없는 패널 점포 {int(x['biz_type'].isna().sum())}행")

    cols = ["n_pnu_all", "n_pnu_type", "n_t", "n_prev", "opens_all", "closes_all", "self_open", "self_opened_4q"]
    res = pd.DataFrame(np.nan, index=x.index, columns=cols)
    for o, t in ends.items():
        rows = np.flatnonzero((x["origin"] == o).to_numpy())
        g = x.iloc[rows]
        open_t = lic[is_open_at(lic, t)]
        open_prev = lic[is_open_at(lic, t - pd.DateOffset(months=WINDOW_MONTHS))]
        opened = lic[in_window(lic["license_date"], t)]
        closed = lic[in_window(lic["close_date"], t)]

        k_pnu = pd.Index(g["pnu"])
        k_pt = pd.MultiIndex.from_arrays([g["pnu"], g["biz_type"]])
        k_dt = pd.MultiIndex.from_arrays([g["bjd_code"], g["biz_type"]])
        by = lambda d, keys: d.dropna(subset=[keys[0]]).groupby(keys).size()  # noqa: E731
        res.iloc[rows, 0] = _lookup(by(open_t, ["pnu"]), k_pnu, False)
        res.iloc[rows, 1] = _lookup(by(open_t, ["pnu", "biz_type"]), k_pt, False)
        res.iloc[rows, 2] = _lookup(by(open_t, ["bjd_code", "biz_type"]), k_dt, False)
        res.iloc[rows, 3] = _lookup(by(open_prev, ["bjd_code", "biz_type"]), k_dt, True)
        res.iloc[rows, 4] = _lookup(by(opened, ["bjd_code", "biz_type"]), k_dt, True)
        res.iloc[rows, 5] = _lookup(by(closed, ["bjd_code", "biz_type"]), k_dt, True)
        res.iloc[rows, 6] = is_open_at(g, t).to_numpy(dtype=float)
        res.iloc[rows, 7] = in_window(g["license_date"], t).to_numpy(dtype=float)

    if (res["self_open"] != 1).any():
        bad = x.loc[res["self_open"] != 1, "origin"].value_counts().to_dict()
        raise CompetitionValidationError(f"패널 행이 origin_end에 영업 중이 아니다 (인허가 원천과 불일치): {bad}")

    out = x[["store_id", "origin", "origin_end", "biz_type", "gu", "pnu", "bjd_code", "location_status"]].copy()
    out = pd.concat([out, res], axis=1)
    no_pnu = out["pnu"].isna()
    no_dong = out["bjd_code"].isna()
    out.loc[no_dong, ["n_t", "n_prev", "opens_all", "closes_all"]] = np.nan

    out["comp_pnu_cnt"] = out["n_pnu_all"] - 1
    out["comp_pnu_same_type_cnt"] = out["n_pnu_type"] - 1
    out["comp_dong_same_type_cnt"] = out["n_t"] - 1
    out["comp_dong_open_4q"] = out["opens_all"] - out["self_opened_4q"]
    out["comp_dong_close_4q"] = out["closes_all"]  # t에 영업 중인 자기 점포는 창 안에서 폐업했을 수 없다
    out["comp_dong_density_yoy"] = np.where(out["n_prev"] > 0, (out["n_t"] - out["n_prev"]) / out["n_prev"], np.nan)
    out.loc[no_pnu, PNU_FEATURES] = np.nan
    out.loc[no_dong, DONG_FEATURES] = np.nan
    return out


def build_competition_features(panel: pd.DataFrame, lic: pd.DataFrame, *, source_snapshot: str = "",
                               raw_last_observed: pd.Timestamp | None = None) -> pd.DataFrame:
    """패널과 같은 행 수·순서의 경쟁지표 테이블 (store_id, origin, 지표 6개, provenance)."""
    c = compute(panel, lic)
    out = c[KEY].copy()
    for f in COUNT_FEATURES:
        out[f] = pd.array(c[f].to_numpy(), dtype="Float64").astype("Int64")
    out["comp_dong_density_yoy"] = pd.array(c["comp_dong_density_yoy"].to_numpy(), dtype="Float64")
    out["comp_feature_asof"] = c["origin_end"].to_numpy()
    out["comp_available_at"] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    out["comp_available_at_basis"] = AVAILABLE_AT_BASIS
    out["comp_source_snapshot"] = source_snapshot
    out["comp_raw_last_observed"] = pd.Timestamp(raw_last_observed) if raw_last_observed is not None else pd.NaT
    out["comp_location_basis"] = LOCATION_BASIS
    out["comp_location_status"] = c["location_status"].to_numpy()
    return out


def raw_last_observed(lic: pd.DataFrame) -> pd.Timestamp:
    return max(lic["license_date"].max(), lic["close_date"].max())


def truncate_to(lic: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """t 시점에 존재할 수 있었던 원천: t 이후 인허가 행을 지우고 t 이후 폐업일을 되돌린다."""
    out = lic[lic["license_date"] <= t].copy()
    out.loc[out["close_date"] > t, "close_date"] = pd.NaT
    return out


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------
def qa_checks(panel: pd.DataFrame, lic: pd.DataFrame, table: pd.DataFrame, calc: pd.DataFrame,
              invariance_origins: list[str] | None = None) -> dict:
    """출력 검증. 실패 항목은 `failures`에 모은다."""
    failures = []
    pk = panel[KEY].reset_index(drop=True)
    keys_equal = bool(pk.equals(table[KEY].reset_index(drop=True)))
    dup = int(table.duplicated(KEY).sum())
    if not keys_equal:
        failures.append("출력 키·순서가 패널과 다르다")
    if dup:
        failures.append(f"(store_id, origin) 중복 {dup}")
    neg = {f: int((table[f] < 0).sum()) for f in COUNT_FEATURES}
    if any(neg.values()):
        failures.append(f"음수 점포 수 {neg}")

    # 패널 키 = origin_end 영업 점포 전체인지 (labels.build_long_panel과 같은 판정인지)
    same_population = {}
    for o, t in _origin_end(panel.assign(origin_end=pd.to_datetime(panel["origin_end"]))).items():
        expect = set(lic.loc[is_open_at(lic, t), "store_id"])
        got = set(panel.loc[panel["origin"] == o, "store_id"])
        same_population[o] = expect == got
    # 항등식: N_t − N_prev = 창 안 개업 전체 − 창 안 폐업 전체 (동 키가 있는 행)
    v = calc["bjd_code"].notna()
    identity_bad = int(((calc.loc[v, "n_t"] - calc.loc[v, "n_prev"])
                        != (calc.loc[v, "opens_all"] - calc.loc[v, "closes_all"])).sum())
    if identity_bad:
        failures.append(f"점포 수 변화 ≠ 개업 − 폐업 {identity_bad}행")
    # 자기 제외: 포함 집계 − 1
    self_excl_ok = bool(((calc["n_t"] - 1).fillna(-9) == calc["comp_dong_same_type_cnt"].fillna(-9)).all())
    if not self_excl_ok:
        failures.append("자기 점포 제외 규칙 위반")
    # 미래 원천 행을 지운 원천으로 다시 계산해도 같은지
    inv = {}
    for o in invariance_origins or []:
        sub = panel[panel["origin"] == o]
        t = pd.Timestamp(sub["origin_end"].iloc[0]).normalize()
        a = build_competition_features(sub, lic)[FEATURES]
        b = build_competition_features(sub, truncate_to(lic, t))[FEATURES]
        inv[o] = bool(np.allclose(a.to_numpy(float, na_value=np.nan), b.to_numpy(float, na_value=np.nan), equal_nan=True))
        if not inv[o]:
            failures.append(f"{o}: 미래 원천 행에 따라 지표가 바뀐다")

    t = table.assign(biz_type=calc["biz_type"].to_numpy(), gu=calc["gu"].to_numpy())
    t[FEATURES] = t[FEATURES].astype("float64")
    med = lambda by: t.groupby(by)[FEATURES].median().round(4).to_dict("index")  # noqa: E731
    out = {
        "n_rows": int(len(table)), "n_panel_rows": int(len(panel)), "keys_equal": keys_equal, "duplicates": dup,
        "negative_counts": neg,
        "panel_equals_open_population": same_population,
        "identity_violations": identity_bad, "self_exclusion_ok": self_excl_ok,
        "future_invariance": inv,
        "density_zero_denominator_rows": int((v & (calc["n_prev"] == 0)).sum()),
        "location_status": table["comp_location_status"].value_counts().to_dict(),
        "missing_rate": {f: round(float(table[f].isna().mean()), 6) for f in FEATURES},
        "missing_rate_by_origin": table[FEATURES].isna().groupby(table["origin"]).mean().round(6).to_dict("index"),
        "median_by_origin": med("origin"),
        "median_by_biz_type": med("biz_type"),
        "median_by_gu": med("gu"),
        "summary": table[FEATURES].astype("float64").describe(percentiles=[.1, .5, .9, .99]).round(4).to_dict(),
        "failures": failures,
    }
    if "trdar_cd" in panel.columns:
        na = panel["trdar_cd"].isna().to_numpy()
        out["trdar_unassigned_rows"] = int(na.sum())
        out["trdar_unassigned_with_dong_features"] = int(table.loc[na, "comp_dong_same_type_cnt"].notna().sum())
    return out


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_panel(path: Path) -> pd.DataFrame:
    names = pq.read_schema(path).names
    cols = [c for c in ["store_id", "origin", "origin_end", "trdar_cd"] if c in names]
    return pd.read_parquet(path, columns=cols)


def default_out(panel: pd.DataFrame, panel_path: Path) -> Path:
    names = pq.read_schema(panel_path).names
    origins = sorted(panel["origin"].unique())
    if "event_12m" not in names and len(origins) == 1:
        return COMPETITION_OUTPUT_DIR / f"competition_features_score_{origins[0]}.parquet"
    return COMPETITION_OUTPUT_DIR / "competition_features.parquet"


def _md_table(d: dict) -> str:
    df = pd.DataFrame(d).T
    return df.to_markdown(floatfmt=".4f")


def write_qa_report(path: Path, meta: dict) -> None:
    q = meta["qa"]
    lines = [
        "# 경쟁지표 QA", "",
        f"- 패널: `{meta['panel']}` (sha256 `{meta['panel_sha256'][:12]}`)",
        f"- 인허가 원천: `{meta['licenses']}` (sha256 `{meta['licenses_sha256'][:12]}`), 최종 관측일 {meta['raw_last_observed']}",
        f"- 출력: `{meta['out']}` (sha256 `{meta['out_sha256'][:12]}`)",
        f"- 행 {q['n_rows']:,} / 패널 {q['n_panel_rows']:,} · 키 일치 {q['keys_equal']} · 중복 {q['duplicates']} · "
        f"패널 해시 불변 {meta['panel_unchanged']}",
        f"- 음수 점포 수 {q['negative_counts']} · 항등식 위반 {q['identity_violations']} · 자기 제외 {q['self_exclusion_ok']}",
        f"- 패널 = origin_end 영업 모집단: {all(q['panel_equals_open_population'].values())}",
        f"- 미래 원천 행 불변성: {q['future_invariance']}",
        f"- density_yoy 분모 0: {q['density_zero_denominator_rows']}행 (NA)",
        f"- 위치 상태: {q['location_status']}",
    ]
    if "trdar_unassigned_rows" in q:
        lines.append(f"- 상권 미배정 {q['trdar_unassigned_rows']:,}행 중 동 지표 산출 {q['trdar_unassigned_with_dong_features']:,}행")
    lines += ["", f"실패: {q['failures'] or '없음'}", "", "## 결측률", "", _md_table({"all": q["missing_rate"]}), "",
              "## origin별 결측률", "", _md_table(q["missing_rate_by_origin"]), "",
              "## origin별 중앙값", "", _md_table(q["median_by_origin"]), "",
              "## 업종별 중앙값", "", _md_table(q["median_by_biz_type"]), "",
              "## 자치구별 중앙값", "", _md_table(q["median_by_gu"]), "",
              "## 분포", "", pd.DataFrame(q["summary"]).to_markdown(floatfmt=".4f"), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def run(panel_path: Path, out_path: Path | None = None, licenses_path: Path = LICENSES_PATH) -> tuple[pd.DataFrame, dict]:
    panel_sha = sha256(panel_path)
    panel = read_panel(panel_path)
    lic = load_licenses(licenses_path)
    lic_sha = sha256(licenses_path)
    last = raw_last_observed(lic)
    snap = f"{licenses_path.name}@{lic_sha[:12]}"

    calc = compute(panel, lic)
    table = build_competition_features(panel, lic, source_snapshot=snap, raw_last_observed=last)
    origins = sorted(panel["origin"].unique())
    qa = qa_checks(panel, lic, table, calc, invariance_origins=sorted({origins[0], origins[-1]}))
    if qa["failures"]:
        raise CompetitionValidationError(f"QA 실패: {qa['failures']}")

    out_path = out_path or default_out(panel, panel_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out_path, index=False)
    meta = {
        "panel": str(panel_path), "panel_sha256": panel_sha, "panel_unchanged": sha256(panel_path) == panel_sha,
        "licenses": str(licenses_path), "licenses_sha256": lic_sha, "raw_last_observed": str(last.date()),
        "out": str(out_path), "out_sha256": sha256(out_path), "window_months": WINDOW_MONTHS,
        "available_at_basis": AVAILABLE_AT_BASIS, "location_basis": LOCATION_BASIS, "qa": qa,
    }
    stem = out_path.with_suffix("")
    Path(f"{stem}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_qa_report(Path(f"{stem}_qa.md"), meta)
    if not meta["panel_unchanged"]:
        raise CompetitionValidationError("실행 중 패널 파일이 바뀌었다")
    return table, meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2 경쟁지표 6종 (인허가 기반)")
    ap.add_argument("--panel", type=Path, default=config.MASTER_BASE_PATH,
                    help="store_id·origin·origin_end가 있는 패널 (master_base 또는 master_score)")
    ap.add_argument("--licenses", type=Path, default=LICENSES_PATH)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    table, meta = run(a.panel, a.out, a.licenses)
    q = meta["qa"]
    print(f"{len(table):,}행 → {meta['out']}")
    print(f"결측률 {q['missing_rate']}")
    print(f"위치 상태 {q['location_status']} · 미래 행 불변성 {q['future_invariance']}")


if __name__ == "__main__":
    main()
