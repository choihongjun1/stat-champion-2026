"""W2-5 동 단위 요약 (Issue #29: 상호·주소 검색에 실패했을 때 동을 골라 보는 주변 현황).

SQLite 정본의 `stores`·`risk`만 읽는다. 한 정본 = 한 serve 실행(run_id·score_origin)이므로 집계 기준이 섞이지 않는다.

- 동은 인허가 원천의 **법정동**이다. 식별자는 `(gu, dong)`, 표시명은 "망원동 (마포구)". 행정동 표기(예: '망원1동')가
  보이면 멈춘다. 법정동이 없는 점포는 동 요약에서 빠지며 그 수를 파일에 남긴다.
- 행: 동 전체(biz_type=null) + 동×업종. 값은 모형 예측 등급(band)의 분포이며 실제 폐업률이 아니고 개별 점포 진단도 아니다.
- 점포별 위험도·store_id는 담지 않는다 (`dong_summary_row`·`dong_summary_file`은 정의에 없는 키를 거부한다).
- 소표본 숨김: 점포 수 < min_cell_n인 칸(small_cell). 동 전체가 공개되는데 숨긴 업종 칸이 하나뿐이면 전체 − 공개 칸으로
  역산되므로 가장 작은 공개 칸을 추가로 숨긴다(complementary). 결과에 역산 가능한 칸이 남으면 멈춘다.
  **min_cell_n은 아직 확정되지 않았다** — 값은 호출자가 주고, `min_cell_n_status=provisional`이면 공개 배포용으로 만들 수 없다.

실행:
    python -m src.serving.dong_summary build --db outputs/serving/report.sqlite --min-cell-n <K> \\
        --out outputs/serving/dev/dong_summary.json
    python -m src.serving.dong_summary profile --licenses outputs/standardized/licenses_3gu.parquet --as-of 2026-06-30
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from src.serving import build_db as bd
from src.serving import report_validation as rv
from src.serving.search_index import write_json

BANDS = ("low", "mid", "high")
BIZ_ORDER = ("일반음식점", "휴게음식점", "미용업")  # 스키마 enum 순서
ADMIN_DONG_RE = re.compile(r"\d동$")  # 행정동 표기(망원1동 등). 서울 법정동 이름은 '숫자+동'으로 끝나지 않는다
RISK_RANKING_RULE = (
    "동 전체 행의 top_risk_biz_types = 숨기지 않은 업종 칸 중 high 등급 점포가 1곳 이상인 업종을 "
    "high 비율(high 등급 점포 수 / 점포 수) 내림차순 → mid+high 비율 내림차순 → 점포 수 내림차순 → 업종 순으로 나열. "
    "평균 예측 확률이 아니라 등급 비율이며, 숨긴 업종 칸은 순위에 넣지 않는다.")
NOTE_ROW = "{label} {what}의 예측 위험 등급 분포입니다. 모형 예측을 모은 값이며 개별 가게 진단이 아닙니다."
NOTE_HIDDEN = "{label} {what}는 점포 수가 적어(또는 다른 칸의 역산을 막기 위해) 수치를 표시하지 않습니다. 개별 가게 진단이 아닙니다."


class DongSummaryError(RuntimeError):
    pass


def dong_label(gu: str, dong: str) -> str:
    return f"{dong} ({gu})"


# ---------------------------------------------------------------------------
# 집계
def load_store_bands(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT s.gu, s.dong, s.biz_type, r.band FROM stores s JOIN risk r USING (store_id)", conn)
    n_stores = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    n_risk = conn.execute("SELECT COUNT(*) FROM risk").fetchone()[0]
    if not len(df) == n_stores == n_risk:
        raise DongSummaryError(f"stores {n_stores:,} / risk {n_risk:,} / 결합 {len(df):,} 행 수 불일치")
    return df


def cell_counts(df: pd.DataFrame) -> pd.DataFrame:
    """동×업종 칸의 점포 수와 등급별 점포 수 (법정동 결측 제외)."""
    d = df.dropna(subset=["dong"])
    tab = (d.groupby(["gu", "dong", "biz_type", "band"]).size().unstack("band", fill_value=0)
           .reindex(columns=list(BANDS), fill_value=0).reset_index())
    tab["n"] = tab[list(BANDS)].sum(axis=1)
    return tab[["gu", "dong", "biz_type", "n", *BANDS]]


def apply_suppression(cells: pd.DataFrame, min_cell_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (업종 칸 + suppressed/reason, 동 전체 + suppressed/reason)."""
    cells = cells.copy()
    cells["reason"] = np.where(cells["n"] < min_cell_n, "small_cell", None)
    totals = cells.groupby(["gu", "dong"], as_index=False)[["n", *BANDS]].sum()
    totals["reason"] = np.where(totals["n"] < min_cell_n, "small_cell", None)
    for t in totals.itertuples():
        m = (cells["gu"] == t.gu) & (cells["dong"] == t.dong)
        if t.reason is not None:
            cells.loc[m & cells["reason"].isna(), "reason"] = "complementary"
            continue
        hidden = m & cells["reason"].notna()
        if hidden.sum() == 1:
            shown = cells[m & cells["reason"].isna()]
            if len(shown):
                pick = shown.assign(o=shown["biz_type"].map(BIZ_ORDER.index)).sort_values(["n", "o"]).index[0]
                cells.loc[pick, "reason"] = "complementary"
    cells["suppressed"] = cells["reason"].notna()
    totals["suppressed"] = totals["reason"].notna()
    return cells, totals


def recoverable_cells(rows: list[dict]) -> list[tuple[str, str, str]]:
    """공개 행만 보고 역산할 수 있는 숨김 칸. 동 전체가 공개됐는데 그 동의 숨긴 업종 칸이 1개면 전체 − 공개 칸 = 숨긴 칸.
    (업종 행이 없는 업종은 점포 0으로 본다.)"""
    out = []
    by_dong: dict[tuple, list[dict]] = {}
    for r in rows:
        by_dong.setdefault((r["gu"], r["dong"]), []).append(r)
    for (gu, dong), rs in by_dong.items():
        total = [r for r in rs if r["biz_type"] is None]
        hidden = [r for r in rs if r["biz_type"] is not None and r["suppressed"]]
        if total and not total[0]["suppressed"] and len(hidden) == 1:
            out.append((gu, dong, hidden[0]["biz_type"]))
    return out


def rank_risk_biz_types(shown_cells: pd.DataFrame) -> list[str]:
    c = shown_cells[shown_cells["high"] > 0].copy()
    if c.empty:
        return []
    c["high_share"] = c["high"] / c["n"]
    c["mid_high_share"] = (c["mid"] + c["high"]) / c["n"]
    c["o"] = c["biz_type"].map(BIZ_ORDER.index)
    c = c.sort_values(["high_share", "mid_high_share", "n", "o"], ascending=[False, False, False, True])
    return c["biz_type"].tolist()


def _share(r) -> dict:
    return {b: round(int(r[b]) / int(r["n"]), 4) for b in BANDS}


def build_summary(conn: sqlite3.Connection, *, min_cell_n: int, min_cell_n_status: str = "provisional",
                  purpose: str = "dev") -> dict:
    """정본 → 동 요약 파일 dict (`dong_summary_file`)."""
    if min_cell_n_status not in ("provisional", "decided"):
        raise DongSummaryError(f"min_cell_n_status: {min_cell_n_status}")
    if not isinstance(min_cell_n, int) or min_cell_n < 1:
        raise DongSummaryError(f"min_cell_n은 1 이상의 정수: {min_cell_n}")
    run = bd.read_run(conn)
    blockers = bd.release_blockers(run)
    if min_cell_n_status != "decided":
        blockers = blockers + [f"소표본 하한값 미확정 (min_cell_n={min_cell_n}, provisional)"]
    if purpose == "release" and blockers:
        raise DongSummaryError("공개 배포용 동 요약을 만들 수 없다: " + " / ".join(blockers))

    df = load_store_bands(conn)
    admin_like = sorted({d for d in df["dong"].dropna().unique() if ADMIN_DONG_RE.search(d)})
    if admin_like:
        raise DongSummaryError(f"행정동으로 보이는 dong 값 — 법정동과 섞으면 안 된다: {admin_like[:10]}")
    cells = cell_counts(df)
    cells, totals = apply_suppression(cells, min_cell_n)

    # 정합성: 칸 합계 = 동 전체, 등급 합 = 점포 수, 동 합계 + 동 없음 = 전체, 등급 분포 = risk 테이블
    errs = []
    if not (cells[list(BANDS)].sum(axis=1) == cells["n"]).all():
        errs.append("업종 칸의 등급별 점포 수 합 ≠ 점포 수")
    chk = cells.groupby(["gu", "dong"])[["n", *BANDS]].sum().reset_index().merge(
        totals[["gu", "dong", "n", *BANDS]], on=["gu", "dong"], suffixes=("_c", "_t"))
    if len(chk) != len(totals) or any((chk[f"{c}_c"] != chk[f"{c}_t"]).any() for c in ("n", *BANDS)):
        errs.append("업종 칸 합계 ≠ 동 전체")
    n_without = int(df["dong"].isna().sum())
    if int(totals["n"].sum()) + n_without != run["n_stores"]:
        errs.append(f"동 합계 {int(totals['n'].sum()):,} + 동 없음 {n_without:,} ≠ runs.n_stores {run['n_stores']:,}")
    band_db = df.dropna(subset=["dong"])["band"].value_counts().reindex(list(BANDS), fill_value=0)
    if not (totals[list(BANDS)].sum().to_numpy() == band_db.to_numpy()).all():
        errs.append("동 전체 등급 합계 ≠ risk 테이블 등급 분포")
    if errs:
        raise DongSummaryError("동 요약 정합성 실패:\n  - " + "\n  - ".join(errs))

    rows, dongs = [], []
    base = {"score_origin": run["score_origin"], "as_of": run["as_of"]}
    for t in totals.sort_values(["gu", "dong"]).itertuples(index=False):
        label = dong_label(t.gu, t.dong)
        dongs.append({"gu": t.gu, "dong": t.dong, "label": label})
        m = (cells["gu"] == t.gu) & (cells["dong"] == t.dong)
        dc = cells[m].assign(o=cells.loc[m, "biz_type"].map(BIZ_ORDER.index)).sort_values("o")
        tr = t._asdict()
        rows.append({"gu": t.gu, "dong": t.dong, "biz_type": None, **base,
                     "n_stores": None if t.suppressed else int(t.n), "suppressed": bool(t.suppressed),
                     "suppression_reason": t.reason,
                     "band_share": None if t.suppressed else _share(tr),
                     "top_risk_biz_types": None if t.suppressed else rank_risk_biz_types(dc[~dc["suppressed"]]),
                     "note": (NOTE_HIDDEN if t.suppressed else NOTE_ROW).format(label=label, what="전체 점포")})
        for c in dc.itertuples(index=False):
            cr = c._asdict()
            what = f"{c.biz_type} 점포"
            rows.append({"gu": c.gu, "dong": c.dong, "biz_type": c.biz_type, **base,
                         "n_stores": None if c.suppressed else int(c.n), "suppressed": bool(c.suppressed),
                         "suppression_reason": c.reason, "band_share": None if c.suppressed else _share(cr),
                         "top_risk_biz_types": None,
                         "note": (NOTE_HIDDEN if c.suppressed else NOTE_ROW).format(label=label, what=what)})

    leak = recoverable_cells(rows)
    if leak:
        raise DongSummaryError(f"숨긴 칸이 역산된다: {leak[:5]}")
    names = pd.Series([d["dong"] for d in dongs])
    dup = sorted(names[names.duplicated()].unique().tolist())
    out = {"_schema_version": rv.SCHEMA_VERSION, "run_id": run["run_id"], **base,
           "release_ready": not blockers, "release_blockers": blockers,
           "min_cell_n": min_cell_n, "min_cell_n_status": min_cell_n_status, "risk_ranking_rule": RISK_RANKING_RULE,
           "n_stores_total": int(run["n_stores"]), "n_stores_without_dong": n_without,
           "duplicate_dong_names": dup, "dongs": dongs, "rows": rows}
    errs = rv.validate_def("dong_summary_file", out)
    if errs:
        raise DongSummaryError("동 요약 스키마 위반:\n  - " + "\n  - ".join(errs[:10]))
    return out


# ---------------------------------------------------------------------------
# 소표본 하한값 실측 (위험도 없이 점포 수만 본다)
def cell_size_report(counts: pd.DataFrame, candidates=(3, 5, 10, 20, 30)) -> dict:
    """counts: gu, dong, biz_type, n (n > 0 칸만). 후보 하한값별 숨김 영향."""
    n = counts["n"].to_numpy()
    stats = {"n_cells": int(len(n)), "n_dongs": int(counts[["gu", "dong"]].drop_duplicates().shape[0]),
             "n_stores": int(n.sum()), "min": int(n.min()), "p5": float(np.percentile(n, 5)),
             "p10": float(np.percentile(n, 10)), "median": float(np.median(n)), "max": int(n.max())}
    cand = []
    base = counts.assign(**{b: 0 for b in BANDS})
    for k in candidates:
        cells, totals = apply_suppression(base, k)
        hid = cells[cells["suppressed"]]
        cand.append({"min_cell_n": k,
                     "cells_small": int((cells["reason"] == "small_cell").sum()),
                     "cells_complementary": int((cells["reason"] == "complementary").sum()),
                     "dong_totals_hidden": int(totals["suppressed"].sum()),
                     "dongs_affected": int(hid[["gu", "dong"]].drop_duplicates().shape[0]),
                     "stores_in_hidden_cells": int(hid["n"].sum())})
    return {"stats": stats, "candidates": cand}


def open_population_counts(licenses: pd.DataFrame, as_of: str) -> tuple[pd.DataFrame, int]:
    """as_of 당시 영업 점포(인허가일 ≤ as_of, 폐업일 없음 또는 > as_of)의 동×업종 점포 수 — score 패널(#35)의 근사.
    → (counts, 법정동 결측 점포 수)."""
    t = pd.Timestamp(as_of)
    lic = licenses
    open_ = lic[(lic["license_date"] <= t) & (lic["close_date"].isna() | (lic["close_date"] > t))]
    counts = (open_.dropna(subset=["dong"]).groupby(["gu", "dong", "business_type"]).size()
              .rename("n").reset_index().rename(columns={"business_type": "biz_type"}))
    return counts, int(open_["dong"].isna().sum())


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-5 동 요약")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--db", type=Path, default=bd.DEFAULT_OUT)
    b.add_argument("--min-cell-n", type=int, required=True, help="소표본 하한값 (미확정 — 반드시 명시)")
    b.add_argument("--min-cell-n-status", choices=("provisional", "decided"), default="provisional")
    b.add_argument("--purpose", choices=bd.PURPOSES, default="dev")
    b.add_argument("--out", type=Path, default=None)
    p = sub.add_parser("profile", help="동×업종 점포 수 분포와 후보 하한값별 숨김 영향 (위험도 불필요)")
    p.add_argument("--licenses", type=Path, default=bd.DEFAULT_LICENSES)
    p.add_argument("--as-of", required=True)
    p.add_argument("--candidates", type=int, nargs="+", default=[3, 5, 10, 20, 30])
    a = ap.parse_args(argv)

    if a.cmd == "profile":
        lic = pd.read_parquet(a.licenses, columns=["gu", "dong", "business_type", "license_date", "close_date"])
        counts, n_no_dong = open_population_counts(lic, a.as_of)
        rep = cell_size_report(counts, a.candidates)
        print(f"as_of {a.as_of} 영업 점포 근사 모집단 (법정동 결측 {n_no_dong}곳 제외)")
        print(rep["stats"])
        print(pd.DataFrame(rep["candidates"]).to_string(index=False))
        return
    conn = sqlite3.connect(f"file:{a.db.as_posix()}?mode=ro", uri=True)
    try:
        out = build_summary(conn, min_cell_n=a.min_cell_n, min_cell_n_status=a.min_cell_n_status, purpose=a.purpose)
    finally:
        conn.close()
    hidden = sum(r["suppressed"] for r in out["rows"])
    print(f"동 {len(out['dongs'])}개 · 행 {len(out['rows'])} (숨김 {hidden}) · run_id {out['run_id']} · "
          f"공개 가능 {out['release_ready']}")
    if a.out:
        write_json(out, a.out)
        print(f"→ {a.out}")


if __name__ == "__main__":
    main()
