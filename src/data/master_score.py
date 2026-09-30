# -*- coding: utf-8 -*-
"""W2-0 확장 — 예측용 master_score (Issue #35).

라벨이 없는 **단일 예측 시점(score origin)** 의 점포 패널을 만든다. PR #36 serve의 `--score` 입력이다.

- 모집단·적격 판정은 labels_base와 **같은 함수**를 쓴다: 원본 인허가 CSV 로더 → 3구 필터(`개방자치단체코드`) →
  store_id → 날짜 파싱 → `labels.build_long_panel`(인허가일 ≤ origin_end, 폐업일 없음 또는 > origin_end) →
  `labels.add_panel_features`. `build_long_panel`이 만드는 `event_12m`(origin 이후 폐업 = 미래 정보)은 즉시 버리고,
  성숙 컬럼도 뺀다 (`master_schema.SCORE_EXCLUDED_COLUMNS`).
- feature 결합은 master_base와 **같은 함수**(`master.attach_*`)를 같은 순서로 쓴다 — 이름·dtype·결측 규칙이 같다.
  상권은 T-1, 공시지가는 strict as-of. 온라인 feature는 여기서 붙이지 않는다 (PR #33 `online_features --panel`).
- master.py의 학습용 생성 경로와 산출물은 건드리지 않는다.

실행:
    python -m src.data.master_score --origin 2026Q2
    python -m src.data.master_score --origin 2025Q2 --out-dir <임시 폴더> --compare-master   # 역검증
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config, label_schema, labels, landprice, master, trdar_features
from src.data import master_schema as schema

SCORE_VERSION = "w2-0-score-0.1"
STATUS_CLOSED = "폐업"
PANEL_COLUMNS = [c for c in label_schema.PANEL_OUTPUT_COLUMNS if c not in schema.SCORE_EXCLUDED_COLUMNS]


class ScoreValidationError(RuntimeError):
    """master_score 불변식 위반."""


def origin_end(origin: str) -> pd.Timestamp:
    return pd.Period(origin, freq="Q").end_time.normalize()


# ---------------------------------------------------------------------------
# 모집단
# ---------------------------------------------------------------------------
def load_license_combined() -> pd.DataFrame:
    """labels_base와 같은 경로로 원본 인허가 3종을 읽어 결합한다 (scripts/run_labels_stage1.py와 같은 단계)."""
    frames = []
    for st in label_schema.RAW_SOURCE_TYPES:
        df = labels.load_licensing_raw(st, config.get_licensing_raw_path(st))
        df = labels.standardize_columns(df, st)
        df = labels.filter_target_districts(df, expected_rows=label_schema.EXPECTED_DISTRICT_FILTERED_ROWS[st])
        frames.append(df)
    combined = labels.build_store_id(labels.combine_sources(frames))
    return labels.parse_dates(combined)


def last_observed_date(combined: pd.DataFrame) -> pd.Timestamp:
    return max(combined["인허가일자_dt"].max(), combined["폐업일자_dt"].max())


def check_observation_window(combined: pd.DataFrame, origin: str) -> pd.Timestamp:
    """as_of 당시 영업 여부를 판정하려면 as_of 이후 성숙 컷오프(1개월)만큼 폐업 신고가 관측돼 있어야 한다
    (DECISIONS.md 2026-09-18). 부족하면 as_of 전에 폐업한 점포가 영업 중으로 남을 수 있다."""
    last = last_observed_date(combined)
    need = origin_end(origin) + pd.DateOffset(months=label_schema.MATURITY_CUTOFF_MONTHS)
    if last < need:
        raise ScoreValidationError(f"원천 최종 관측일 {last.date()}이 {need.date()}보다 이르다 — "
                                   f"{origin} 당시 영업 여부를 확정할 수 없다")
    return last


def build_score_panel(combined: pd.DataFrame, origin: str) -> pd.DataFrame:
    """origin 당시 영업 점포 1행씩. labels_base와 같은 적격 판정·인허가 feature, 라벨·성숙 컬럼 없음."""
    panel = labels.build_long_panel(combined, [pd.Period(origin, freq="Q")])
    panel = panel.drop(columns="event_12m")  # origin 이후 폐업 = 미래 정보. 계산 즉시 버린다
    panel = labels.add_panel_features(panel, label_schema.MATURITY_CUTOFF_MONTHS)
    panel = panel[PANEL_COLUMNS].sort_values("store_id").reset_index(drop=True)
    return panel


def population_qa(combined: pd.DataFrame, panel: pd.DataFrame, origin: str) -> dict:
    """모집단 판정 근거와 경계 사례. 값은 원천에서 매번 센다."""
    end = origin_end(origin)
    lic, clo = combined["인허가일자_dt"], combined["폐업일자_dt"]
    status = combined["영업상태명"].astype("string").str.strip()
    eligible = (lic <= end) & (clo.isna() | (clo > end))
    inv_gu = {v: k for k, v in label_schema.DISTRICT_CODES.items()}
    gu = combined["개방자치단체코드"].astype("string").str.strip().map(inv_gu)
    in_panel = combined["store_id"].isin(panel["store_id"])
    by = (combined[eligible].assign(gu=gu[eligible])
          .groupby(["gu", "source_type"]).size().unstack(fill_value=0))
    return {
        "license_rows_3gu": int(len(combined)),
        "eligible_at_as_of": int(eligible.sum()),
        "panel_rows": int(len(panel)),
        "panel_equals_eligible_set": bool(set(panel["store_id"]) == set(combined.loc[eligible, "store_id"])),
        "opened_after_as_of": int((lic > end).sum()),
        "opened_after_as_of_in_panel": int((in_panel & (lic > end)).sum()),
        "closed_on_or_before_as_of_in_panel": int((in_panel & clo.notna() & (clo <= end)).sum()),
        "open_at_as_of_closed_since": int((eligible & clo.notna()).sum()),
        "current_snapshot_open_by_close_date": int(clo.isna().sum()),
        "current_snapshot_open_by_status": int((status != STATUS_CLOSED).sum()),
        "license_date_missing": int(lic.isna().sum()),
        "close_before_license": int((clo.notna() & lic.notna() & (clo < lic)).sum()),
        "license_after_last_observed": int((lic > last_observed_date(combined)).sum()),
        # 상태명 충돌: 폐업 상태인데 폐업일이 없다 → 날짜 규칙으로는 영업 중이라 포함된다(학습 패널과 같다).
        # 실제 영업이 확인된 것이 아니다.
        "status_closed_without_close_date_in_panel": int((eligible & (status == STATUS_CLOSED) & clo.isna()).sum()),
        "status_open_with_close_date": int(((status != STATUS_CLOSED) & clo.notna()).sum()),
        "store_id_unique": bool(combined["store_id"].is_unique),
        "gu_outside_target": int(gu.isna().sum()),
        "source_type_outside_target": int((~combined["source_type"].isin(label_schema.RAW_SOURCE_TYPES)).sum()),
        "by_gu_biz": {g: {b: int(v) for b, v in row.items()} for g, row in by.iterrows()},
    }


# ---------------------------------------------------------------------------
# 결합 (master.py 함수 재사용)
# ---------------------------------------------------------------------------
def verify_score_step(step: str, df: pd.DataFrame, panel: pd.DataFrame, origin: str, log: list[dict]) -> None:
    """결합 단계 불변식: 행 수·점포 집합·origin 하나·인허가 패널 컬럼 불변·시점 누수 0·업종 상권 불변식."""
    leak = master.temporal_leakage_counts(df)
    biz = master.biz_integrity_counts(df)
    same_panel = df[PANEL_COLUMNS].sort_values("store_id").reset_index(drop=True).equals(panel)
    row = {"step": step, "rows": len(df), "store_id_unique": bool(df["store_id"].is_unique),
           "origins": sorted(df["origin"].astype(str).unique()), "panel_unchanged": same_panel,
           "leakage_violations": sum(leak.values()), "biz_violations": sum(biz.values())}
    log.append(row)
    problems = []
    if len(df) != len(panel):
        problems.append(f"rows {len(panel)} → {len(df)}")
    if not row["store_id_unique"]:
        problems.append("store_id 중복")
    if row["origins"] != [origin]:
        problems.append(f"origin {row['origins']}")
    if not same_panel:
        problems.append("인허가 패널 컬럼 값이 결합 중 바뀌었다")
    if row["leakage_violations"]:
        problems.append(f"시점 누수 {leak}")
    if row["biz_violations"]:
        problems.append(f"업종 상권 불변식 {biz}")
    if problems:
        raise ScoreValidationError(f"[{step}] " + " / ".join(problems))


def build_master_score(panel: pd.DataFrame, spatial: pd.DataFrame, er: pd.DataFrame,
                       trdar: pd.DataFrame, trdar_source_snapshot: str,
                       trdar_biz: pd.DataFrame, trdar_biz_source_snapshot: str) -> tuple[pd.DataFrame, list[dict]]:
    """build_master_base와 같은 순서·같은 함수로 결합한다. (master_score, step log)."""
    origins = panel["origin"].astype(str).unique()
    if len(origins) != 1:
        raise ScoreValidationError(f"예측용 패널에는 origin이 하나여야 한다: {list(origins)}")
    origin = str(origins[0])
    bad = [c for c in schema.SCORE_EXCLUDED_COLUMNS if c in panel.columns]
    if bad:
        raise ScoreValidationError(f"예측용 패널에 라벨·성숙 컬럼이 있다: {bad}")
    log: list[dict] = []
    df = panel.copy()
    verify_score_step("0 score panel", df, panel, origin, log)
    df = master.attach_store_attributes(df, spatial)
    verify_score_step("1 +store attributes / trdar assignment", df, panel, origin, log)
    df = master.attach_er_provenance(df, er)
    verify_score_step("2 +ER provenance", df, panel, origin, log)
    df = master.attach_land_price(df, spatial)
    verify_score_step("3 +land price (strict as-of)", df, panel, origin, log)
    df = master.attach_trdar_features(df, trdar, trdar_source_snapshot)
    verify_score_step("4 +trdar area features (T-1)", df, panel, origin, log)
    df = master.attach_trdar_biz_features(df, trdar_biz, trdar_biz_source_snapshot)
    verify_score_step("5 +trdar biz features (T-1, observed)", df, panel, origin, log)
    cols = schema.score_columns()
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ScoreValidationError(f"master_score 컬럼 누락: {missing}")
    df = df[cols].sort_values("store_id").reset_index(drop=True)
    return df, log


# ---------------------------------------------------------------------------
# 검증·메타
# ---------------------------------------------------------------------------
def contract_errors(df: pd.DataFrame) -> list[str]:
    """PR #36 serve 입력 계약 + master_score 스키마."""
    errs = []
    if list(df.columns) != schema.score_columns():
        errs.append("컬럼 순서·집합이 score_columns()와 다르다")
    for c in schema.SCORE_EXCLUDED_COLUMNS:
        if c in df.columns:
            errs.append(f"라벨·성숙 컬럼 {c}")
    missing = [c for c in [*schema.SCORE_REQUIRED_META, *schema.predictor_columns()] if c not in df.columns]
    if missing:
        errs.append(f"serve 입력 필수 컬럼 누락 {missing}")
    if df["origin"].astype(str).nunique() != 1:
        errs.append("origin이 하나가 아니다")
    if not df["store_id"].is_unique:
        errs.append("store_id 중복")
    return errs


def time_checks(df: pd.DataFrame, origin: str) -> dict:
    """원천별 시점 확인. *_violations는 0이어야 한다."""
    end = origin_end(origin)
    t1 = str(pd.Period(origin, freq="Q") - schema.TRDAR_LAG_QUARTERS)
    years_ok = [y for y in landprice.YEARS if pd.Timestamp(landprice.LANDPRICE_META[y]["available_at"]) <= end]
    expect_year = max(years_ok) if years_ok else None
    basis = df["trdar_available_at_basis"].value_counts(dropna=False)
    return {
        "leakage": master.temporal_leakage_counts(df),
        "biz_integrity": master.biz_integrity_counts(df),
        "trdar_quarter_expected": t1,
        "trdar_quarter_used_violations": int((df["trdar_quarter_used"] != t1).sum()),
        "trdar_available_at": (None if df["trdar_available_at"].isna().all()
                               else str(df["trdar_available_at"].dropna().max().date())),
        "trdar_available_at_basis": {str(k): int(v) for k, v in basis.items()},
        "trdar_geometry_backcast_rows": int(df["trdar_geometry_backcast_flag"].sum()),
        "land_price_year_expected": expect_year,
        "land_price_year_used_violations": int(
            (df["land_price_year_used"].astype("Float64") != expect_year).fillna(True).sum()
            if expect_year is not None else df["land_price_year_used"].notna().sum()),
        "land_price_available_at": (None if expect_year is None
                                    else landprice.LANDPRICE_META[expect_year]["available_at"]),
        "license_feature_asof": str(end.date()),
    }


def missing_counts(df: pd.DataFrame) -> dict:
    preds = schema.predictor_columns(df.columns)
    return {
        "trdar_cd_missing (상권 미배정)": int(df["trdar_cd"].isna().sum()),
        "coord_missing": int(df["coord_missing"].fillna(False).sum()),
        "has_coord_false": int((~df["has_coord"].astype(bool)).sum()),
        "area_missing": int(df["area"].isna().sum()),
        "land_price_missing": int(df["land_price"].isna().sum()),
        "trdar_area_feature_all_missing": int(df[master.TRDAR_FEATURE_COLS].isna().all(axis=1).sum()),
        "trdar_biz_feature_all_missing": int(df[trdar_features.BIZ_FEATURES].isna().all(axis=1).sum()),
        "predictor_missing_rate": {c: round(float(df[c].isna().mean()), 4) for c in preds},
    }


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_version() -> dict:
    def git(*args):
        r = subprocess.run(["git", *args], cwd=config.REPO_ROOT, capture_output=True, encoding="utf-8")
        return r.stdout.strip() if r.returncode == 0 else None
    dirty = git("status", "--porcelain", "--", "src")
    return {"score_version": SCORE_VERSION, "git_commit": git("rev-parse", "HEAD"),
            "src_dirty": bool(dirty) if dirty is not None else None}


def compare_with_master_base(score: pd.DataFrame, master_base: pd.DataFrame, origin: str) -> dict:
    """같은 origin의 master_base(라벨·성숙 컬럼 제외)와 비교. 역검증용."""
    base = (master_base[master_base["origin"].astype(str) == origin]
            .drop(columns=list(schema.SCORE_EXCLUDED_COLUMNS))
            .sort_values("store_id").reset_index(drop=True))
    s = score.sort_values("store_id").reset_index(drop=True)
    out = {"rows_score": len(s), "rows_master_base": len(base),
           "store_id_set_equal": bool(set(s["store_id"]) == set(base["store_id"])),
           "columns_equal": list(s.columns) == list(base.columns)}
    diffs = {}
    if out["store_id_set_equal"] and out["columns_equal"]:
        for c in s.columns:
            a, b = s[c], base[c]
            if str(a.dtype) != str(b.dtype):
                diffs[c] = f"dtype {a.dtype} ≠ {b.dtype}"
            elif not a.isna().equals(b.isna()):
                diffs[c] = f"결측 마스크 다름 ({int((a.isna() != b.isna()).sum())}행)"
            elif not a.equals(b):
                diffs[c] = f"값 다름 ({int((a != b).fillna(True).sum())}행)"
    out["column_differences"] = diffs
    out["identical"] = (out["store_id_set_equal"] and out["columns_equal"] and not diffs
                        and len(s) == len(base))
    return out


def write_qa_report(path: Path, meta: dict, log: list[dict]) -> None:
    pop, tc, miss = meta["population"], meta["time_checks"], meta["missing"]
    by = pd.DataFrame(pop["by_gu_biz"]).T
    by["합계"] = by.sum(axis=1)
    by.loc["합계"] = by.sum()
    lines = [
        f"# master_score QA ({meta['score_origin']}, as_of {meta['as_of']})", "",
        "`python -m src.data.master_score`가 생성한다. 점포별 원자료가 없는 집계만 담는다.", "",
        "## 모집단 (원천에서 재계산)", "",
        f"- 3구 인허가 {pop['license_rows_3gu']:,}행 → as_of 당시 영업 **{pop['eligible_at_as_of']:,}** "
        f"(패널 {pop['panel_rows']:,}, 판정 집합 일치 {pop['panel_equals_eligible_set']})",
        f"- as_of 이후 개업 {pop['opened_after_as_of']:,} (패널 포함 {pop['opened_after_as_of_in_panel']}) · "
        f"as_of 이전 폐업이 패널에 포함 {pop['closed_on_or_before_as_of_in_panel']} · "
        f"as_of 당시 영업이었고 이후 폐업 {pop['open_at_as_of_closed_since']:,} (보존)",
        f"- 현재 스냅샷 영업: 폐업일 결측 기준 {pop['current_snapshot_open_by_close_date']:,} / 상태명 기준 "
        f"{pop['current_snapshot_open_by_status']:,} — as_of 모집단이 아니다",
        f"- **상태명 충돌**: 영업상태명 '폐업'인데 폐업일자 없음 {pop['status_closed_without_close_date_in_panel']}곳 — "
        "날짜 규칙상 영업 중이라 학습 패널과 같이 포함했다. 실제 영업이 확인된 것이 아니다. "
        f"(상태명 영업인데 폐업일 있음 {pop['status_open_with_close_date']})",
        f"- 이상값: 인허가일 결측 {pop['license_date_missing']} · 폐업일 < 인허가일 {pop['close_before_license']} · "
        f"store_id 유일 {pop['store_id_unique']} · 대상 밖 구 {pop['gu_outside_target']} · "
        f"대상 밖 업종 {pop['source_type_outside_target']}",
        f"- 원천 최종 관측일 {meta['last_observed_date']} (as_of + 성숙 컷오프 이후여야 한다)", "",
        by.to_markdown(), "",
        "## 시간 정합성", "",
        f"- 인허가 feature 기준 시점 {tc['license_feature_asof']} (면적·좌표 유무는 인허가 **현재 스냅샷** 값 — "
        "as_of 당시 값이라고 단정할 수 없다, master_base와 같은 한계)",
        f"- 상권 T-1 = {tc['trdar_quarter_expected']} (위반 {tc['trdar_quarter_used_violations']}) · 공표일 "
        f"{tc['trdar_available_at'] or '미확인(NaT)'} · basis {tc['trdar_available_at_basis']} · "
        f"경계 소급 행 {tc['trdar_geometry_backcast_rows']}",
        "  - `archive_inferred`는 공식 일정 기반 추정이며 검증된 공표일이 아니다. origin 분기 자체 값은 쓰지 않는다.",
        f"- 공시지가 strict as-of: {tc['land_price_year_expected']}년 값 (공시 {tc['land_price_available_at']}, "
        f"위반 {tc['land_price_year_used_violations']}) — 탐지 모형은 검증되지 않은 feature라 쓰지 않는다 (서빙이 제외)",
        f"- 누수 검사 {tc['leakage']}",
        f"- 업종 상권 불변식 {tc['biz_integrity']}",
        "- 온라인 feature는 결합하지 않았다 (PR #33 `online_features --panel`로 별도 생성). 현재 등록 스냅샷(PR #21)은 "
        "예측 feature로 쓰지 않는다.", "",
        "## 결측·공간 미매칭 (행은 모두 보존)", "",
        *[f"- {k}: {v:,}" for k, v in miss.items() if k != "predictor_missing_rate"], "",
        "| predictor | 결측률 |", "|---|---|",
        *[f"| {k} | {v:.4f} |" for k, v in miss["predictor_missing_rate"].items()], "",
        "## 결합 단계 검사", "",
        pd.DataFrame(log).to_markdown(index=False), "",
        "## 계약", "",
        f"- 행 {meta['n_rows']:,} · 컬럼 {meta['n_columns']} · predictor {len(meta['predictors'])} · "
        f"계약 위반 {meta['contract_errors'] or '없음'}", "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def load_inputs() -> dict:
    return master.load_inputs()


def run(origin: str, out_dir: Path | None = None, compare_master: bool = False) -> tuple[pd.DataFrame, dict]:
    from src.data import w1_invariants

    w1_invariants.run()  # W2 진입 게이트 (master.run과 같다)
    out_dir = Path(out_dir) if out_dir is not None else config.MASTER_OUTPUT_DIR
    combined = load_license_combined()
    last = check_observation_window(combined, origin)
    panel = build_score_panel(combined, origin)
    pop = population_qa(combined, panel, origin)
    if not pop["panel_equals_eligible_set"] or pop["opened_after_as_of_in_panel"] \
            or pop["closed_on_or_before_as_of_in_panel"] or not pop["store_id_unique"] \
            or pop["gu_outside_target"] or pop["source_type_outside_target"]:
        raise ScoreValidationError(f"모집단 불변식 위반: {pop}")

    inputs = load_inputs()
    score, log = build_master_score(panel, inputs["spatial"], inputs["er"], inputs["trdar"],
                                    inputs["trdar_source_snapshot"], inputs["trdar_biz"],
                                    inputs["trdar_biz_source_snapshot"])
    errs = contract_errors(score)
    tc = time_checks(score, origin)
    if errs or sum(tc["leakage"].values()) or sum(tc["biz_integrity"].values()) \
            or tc["trdar_quarter_used_violations"] or tc["land_price_year_used_violations"]:
        raise ScoreValidationError(f"계약·시점 검사 실패: {errs} {tc}")

    raw_paths = {st: config.get_licensing_raw_path(st) for st in label_schema.RAW_SOURCE_TYPES}
    meta = {
        "score_origin": origin, "as_of": str(origin_end(origin).date()),
        "n_rows": int(len(score)), "n_columns": int(score.shape[1]),
        "predictors": schema.predictor_columns(score.columns),
        "excluded_columns": list(schema.SCORE_EXCLUDED_COLUMNS),
        "last_observed_date": str(last.date()),
        "inputs": {
            **{f"license_raw[{st}]": {"file": p.name, "sha256": sha256(p)} for st, p in raw_paths.items()},
            "spatial_joined": {"file": master.SPATIAL_PATH.name, "sha256": sha256(master.SPATIAL_PATH)},
            "er_matches": {"file": master.ER_PATH.name, "sha256": sha256(master.ER_PATH)},
            "trdar_area": inputs["trdar_source_snapshot"],
            "trdar_biz": inputs["trdar_biz_source_snapshot"],
        },
        "feature_timing": {
            "license": "origin_end 기준 (면적·좌표 유무는 인허가 현재 스냅샷 값)",
            "trdar": f"T-{schema.TRDAR_LAG_QUARTERS} = {tc['trdar_quarter_expected']}",
            "land_price": f"strict as-of → {tc['land_price_year_expected']}년 (available_at {tc['land_price_available_at']})",
            "online": "결합하지 않음 (PR #33 online_features --panel로 별도 생성)",
            "er": "provenance 전용 (predictor 아님)",
        },
        "time_checks": tc,
        "population": pop,
        "missing": missing_counts(score),
        "contract_errors": errs,
        "code": code_version(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    score.to_parquet(out_dir / config.MASTER_SCORE_PATH.name, index=False)
    if compare_master:
        # 저장된 파일끼리 비교한다 (parquet은 datetime64[s]를 [ms]로 저장하므로 메모리 값과 dtype 단위가 다르다)
        saved = pd.read_parquet(out_dir / config.MASTER_SCORE_PATH.name)
        meta["compare_master_base"] = compare_with_master_base(saved, pd.read_parquet(config.MASTER_BASE_PATH), origin)
    (out_dir / config.MASTER_SCORE_META_PATH.name).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    write_qa_report(out_dir / config.MASTER_SCORE_QA_REPORT_PATH.name, meta, log)
    print(pd.DataFrame(log).to_string(index=False))
    print(f"\n저장: {out_dir / config.MASTER_SCORE_PATH.name} {score.shape} · 모집단 {pop['eligible_at_as_of']:,}")
    if compare_master:
        print("master_base 역검증:", json.dumps(meta["compare_master_base"], ensure_ascii=False))
    return score, meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="예측용 master_score (라벨 없는 단일 origin)")
    ap.add_argument("--origin", required=True, help="예측 기준 분기 (예: 2026Q2)")
    ap.add_argument("--out-dir", type=Path, default=None, help=f"기본 {config.MASTER_OUTPUT_DIR}")
    ap.add_argument("--compare-master", action="store_true",
                    help="같은 origin의 master_base(라벨 제외)와 비교 — 라벨이 있는 origin의 역검증용")
    a = ap.parse_args(argv)
    run(a.origin, a.out_dir, a.compare_master)


if __name__ == "__main__":
    main()
