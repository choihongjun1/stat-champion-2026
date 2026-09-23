# -*- coding: utf-8 -*-
"""W2-0 master_base 조립.

실행:
    python -m src.data.master

`outputs/labels/labels_base.parquet`의 `(store_id, origin)` 패널을 축으로 feature 원천을
LEFT JOIN해 모델 입력용 한 장의 테이블을 만든다. 산출물(outputs/master/, git 미추적):
    - master_base.parquet
    - qa_report.md

원칙:
- 행을 절대 늘리거나 지우지 않는다. 모든 조인은 m:1이며 매칭 실패는 NA로 둔다
  (complete-case 금지, DECISIONS.md 2026-09-13).
- 조인 단계마다 행수 / store_id 수 / (store_id, origin) 중복 / origin별 event 비율 /
  label 값 / temporal leakage를 검사하고 결과를 step log로 남긴다. 하나라도 깨지면 멈춘다.
- 컬럼 역할은 `master_schema.COLUMN_ROLES`가 단일 출처다. ER 결과는 provenance 전용이다.
- 온라인 존재감은 Base에 넣지 않는다. Enriched는 이 테이블에 `(store_id, origin)` 단위
  m:1 테이블을 LEFT JOIN하는 방식으로 확장한다 (`attach_enriched_table`).
"""
from __future__ import annotations

import pandas as pd

from src.data import config
from src.data import master_schema as schema

SPATIAL_PATH = config.REPO_ROOT / "outputs" / "spatial" / "spatial_joined.parquet"
ER_PATH = config.REPO_ROOT / "outputs" / "matching" / "license_semas_matches.parquet"

SPATIAL_COLS = ["store_id", "gu", "pnu", "coord_missing", "coord_suspect", "trdar_cd",
                "trdar_type", "in_polygon", "spatial_ambiguous", "spatial_match_method"]
ER_COLS = {
    "store_id": "store_id",
    "gu_mismatch": "gu_mismatch",
    "parse_status": "parse_status",
    "matched": "er_matched",
    "ambiguous": "er_ambiguous",
    "match_tier": "match_tier",
    "match_confidence": "match_confidence",
    "crowded_pnu": "crowded_pnu",
    "sj_entity_id": "sj_entity_id",
    "unmatched_reason": "unmatched_reason",
}


class MasterValidationError(AssertionError):
    """master 조립 불변식 위반."""


# ---------------------------------------------------------------------------
# 단계별 검증
# ---------------------------------------------------------------------------
def label_baseline(panel: pd.DataFrame) -> dict:
    """조인 전 기준값. 이후 모든 단계는 이 값과 비교한다."""
    return {
        "rows": len(panel),
        "stores": panel["store_id"].nunique(),
        "event_by_origin": panel.groupby("origin")["event_12m"].agg(["size", "mean"]),
        "labels": panel[schema.LABEL_COLUMNS].sort_values(schema.KEY).reset_index(drop=True),
    }


def temporal_leakage_counts(df: pd.DataFrame) -> dict[str, int]:
    """시점 정합성 위반 건수. 전부 0이어야 한다.

    `available_at > origin_end`인 non-null 값과, 금지 목록에 걸리는 predictor 등록을 센다.
    feature group이 아직 붙지 않았으면 그 group의 검사는 건너뛴다.
    """
    end = df["origin_end"]
    out = {
        "license available_at > origin_end": int((df["available_at"] > end).sum()),
        "forbidden predictor registered": len(schema.forbidden_predictors_in_registry()),
    }
    return out


def verify_step(step: str, df: pd.DataFrame, baseline: dict, log: list[dict]) -> None:
    """조인 단계 불변식 검사 → log에 한 줄 추가. 하나라도 깨지면 MasterValidationError."""
    dup = int(df.duplicated(schema.KEY).sum())
    ev = df.groupby("origin")["event_12m"].agg(["size", "mean"])
    ev_ok = ev.equals(baseline["event_by_origin"])
    ev_diff = float((ev["mean"] - baseline["event_by_origin"]["mean"]).abs().max())
    labels_now = df[schema.LABEL_COLUMNS].sort_values(schema.KEY).reset_index(drop=True)
    labels_ok = labels_now.equals(baseline["labels"])
    leakage = temporal_leakage_counts(df)
    n_leak = sum(leakage.values())

    row = {
        "step": step,
        "rows": len(df),
        "stores": df["store_id"].nunique(),
        "dup_key": dup,
        "event_rate_max_abs_diff": ev_diff,
        "event_by_origin_equal": ev_ok,
        "labels_unchanged": labels_ok,
        "leakage_violations": n_leak,
        "n_columns": df.shape[1],
    }
    log.append(row)
    problems = []
    if row["rows"] != baseline["rows"]:
        problems.append(f"rows {baseline['rows']} -> {row['rows']}")
    if row["stores"] != baseline["stores"]:
        problems.append(f"stores {baseline['stores']} -> {row['stores']}")
    if dup:
        problems.append(f"(store_id, origin) dup={dup}")
    if not ev_ok:
        problems.append(f"origin별 event 비율 변화 (max diff {ev_diff})")
    if not labels_ok:
        problems.append("label/panel 컬럼 값 변화")
    if n_leak:
        problems.append(f"temporal leakage {leakage}")
    if problems:
        raise MasterValidationError(f"[{step}] " + "; ".join(problems))


def _merge_m1(left: pd.DataFrame, right: pd.DataFrame, on, name: str) -> pd.DataFrame:
    """m:1 LEFT JOIN. 오른쪽 키가 유일하지 않으면 pandas가 MergeError로 멈춘다."""
    overlap = (set(left.columns) & set(right.columns)) - set([on] if isinstance(on, str) else on)
    if overlap:
        raise MasterValidationError(f"[{name}] 컬럼 충돌: {sorted(overlap)}")
    return left.merge(right, on=on, how="left", validate="m:1")


# ---------------------------------------------------------------------------
# 원천별 결합
# ---------------------------------------------------------------------------
def attach_store_attributes(panel: pd.DataFrame, spatial: pd.DataFrame) -> pd.DataFrame:
    """점포 정적 속성 + 상권 배정(within-only). spatial_joined는 store_id 유일."""
    return _merge_m1(panel, spatial[SPATIAL_COLS], "store_id", "spatial")


def attach_er_provenance(panel: pd.DataFrame, er: pd.DataFrame) -> pd.DataFrame:
    """ER 결과를 provenance로만 붙인다. 매칭 실패 점포도 그대로 남는다."""
    right = er[list(ER_COLS)].rename(columns=ER_COLS)
    return _merge_m1(panel, right, "store_id", "er")


def attach_enriched_table(master: pd.DataFrame, table: pd.DataFrame,
                          name: str) -> pd.DataFrame:
    """Enriched 확장 인터페이스 (Base에서는 호출하지 않는다).

    table은 `(store_id, origin)` 유일이어야 하고, 값은 origin_end 이전 정보만 집계한 것이어야
    한다 (예: 온라인 블로그 월별 건수 중 month_end <= origin_end인 달만). 시점 메타는
    `{group}_feature_asof` / `{group}_available_at` / `{group}_source_snapshot`로 함께 넣는다.
    """
    return _merge_m1(master, table, schema.KEY, name)


# ---------------------------------------------------------------------------
# 조립 / 검증 / 저장
# ---------------------------------------------------------------------------
def validate_schema(df: pd.DataFrame) -> None:
    registered = list(schema.COLUMN_ROLES)
    missing = [c for c in registered if c not in df.columns]
    extra = [c for c in df.columns if c not in schema.COLUMN_ROLES]
    if missing or extra:
        raise MasterValidationError(f"schema 불일치: missing={missing} extra={extra}")


def build_master_base(labels: pd.DataFrame, spatial: pd.DataFrame,
                      er: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """labels_base 축 LEFT JOIN 조립. (master, step log) 반환."""
    log: list[dict] = []
    baseline = label_baseline(labels)
    df = labels.copy()
    verify_step("0 labels_base", df, baseline, log)

    df = attach_store_attributes(df, spatial)
    verify_step("1 +store attributes / trdar assignment", df, baseline, log)

    df = attach_er_provenance(df, er)
    verify_step("2 +ER provenance", df, baseline, log)

    df = df[list(schema.COLUMN_ROLES)]
    validate_schema(df)
    return df, log


def missing_rate_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """predictor 결측률: 전체 / origin별."""
    cols = schema.predictor_columns(df.columns)
    overall = df[cols].isna().mean().rename("missing_rate").to_frame()
    by_origin = df.groupby("origin")[cols].apply(lambda g: g.isna().mean())
    return overall, by_origin


def _md(df: pd.DataFrame, index: bool = True, floatfmt: str = ".4f") -> str:
    return df.to_markdown(index=index, floatfmt=floatfmt)


def write_qa_report(df: pd.DataFrame, log: list[dict], path=config.MASTER_QA_REPORT_PATH) -> None:
    overall, by_origin = missing_rate_tables(df)
    leak = pd.Series(temporal_leakage_counts(df), name="violations").to_frame()
    roles = pd.DataFrame(
        [(c, r, g) for c, (r, g) in schema.COLUMN_ROLES.items()],
        columns=["column", "role", "group"],
    )
    er_rate = df.groupby("er_matched")["event_12m"].agg(["size", "mean"])
    lines = [
        "# master_base QA report",
        "",
        "`python -m src.data.master`가 생성한다. 모든 단계는 labels_base 기준값과 비교했다.",
        "",
        "## 단계별 검증",
        "",
        _md(pd.DataFrame(log), index=False, floatfmt=".6f"),
        "",
        "## Temporal leakage (전부 0이어야 한다)",
        "",
        _md(leak),
        "",
        "## Predictor 결측률 (전체)",
        "",
        _md(overall),
        "",
        "## Predictor 결측률 (origin별)",
        "",
        _md(by_origin),
        "",
        "## ER 누수 경고 지표 (provenance 전용 근거)",
        "",
        "`er_matched`는 2024-12~2026-06 스냅샷 union으로 계산된다. 아래 event 비율 격차가",
        "predictor로 쓰면 안 되는 이유다.",
        "",
        _md(er_rate),
        "",
        "## 컬럼 역할",
        "",
        _md(roles, index=False),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (pd.read_parquet(config.LABELS_BASE_PATH),
            pd.read_parquet(SPATIAL_PATH, columns=SPATIAL_COLS),
            pd.read_parquet(ER_PATH, columns=list(ER_COLS)))


def run() -> pd.DataFrame:
    from src.data import w1_invariants

    w1_invariants.run()  # W2 진입 게이트: freeze 상태와 다르면 여기서 멈춘다
    labels, spatial, er = load_inputs()
    master, log = build_master_base(labels, spatial, er)
    print(pd.DataFrame(log).to_string(index=False))
    config.MASTER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    master.to_parquet(config.MASTER_BASE_PATH, index=False)
    write_qa_report(master, log)
    print(f"\n저장: {config.MASTER_BASE_PATH} {master.shape}")
    return master


if __name__ == "__main__":
    run()
