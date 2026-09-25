# -*- coding: utf-8 -*-
"""온라인 존재감(축B 블로그 월별) → (store_id, origin) Enriched feature 테이블.

입력 (PR #21 `export_online_features.py` 산출물)
- 월별 언급: `online_mentions_monthly.parquet` — 언급이 있는 달만 행이 있다 (sparse)
- QA: `online_blog_monthly_qa.csv` — 점포당 1행 (`error`, `first_date_truncated`, `oldest_raw_postdate`)

결측 규칙 (DECISIONS.md 2026-09-24 #25)
- QA에 없는 점포, QA `error`가 있는 점포 → 전 origin NA
- 절단 점포(`first_date_truncated=True`, 최신순 200건 상한) → `oldest_raw_postdate`가 속한 달보다 앞선 달은
  관측하지 않은 달이다. 창(window)이 그 달보다 앞에서 시작하면 그 창의 값은 NA
- 그 외 행이 없는 달 → 0건

시점 규칙: origin t의 feature는 origin_end(t)가 속한 달까지의 게시월만 쓴다
(월별 행의 available_at = 게시월 말일 ≤ origin_end).

**쓰지 않는 것**: `first_date_truncated` 자체는 predictor로 넘기지 않는다. 절단 여부는 수집 시점(2026-09)의
누적 게시물 수(api_total > 200)로 정해지므로 origin 이후 인기(=생존)를 반영한 미래 정보다.
결측 처리에만 쓰고, 이 결측이 라벨과 얼마나 상관되는지는 `online_deletion_bias.py`가 따로 잰다.

실행:
    python -m src.data.online_features --monthly <월별 parquet> --qa <QA csv>
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config

FEATURES = [
    "online_blog_cnt_3m",        # origin 직전 3개월 언급 수
    "online_blog_cnt_12m",       # 직전 12개월 언급 수
    "online_blog_has_12m",       # 직전 12개월 언급 1건 이상 (0/1)
    "online_blog_trend_6m",      # 최근 6개월 − 그 앞 6개월
    "online_blog_has_ever",      # origin까지 언급이 한 번이라도 있었는지 (0/1)
    "online_blog_months_since_last",  # 마지막 언급 이후 경과 개월 (언급 없으면 NA)
]
META = ["online_feature_asof", "online_available_at", "online_source_snapshot"]

DEFAULT_MONTHLY = config.REPO_ROOT / "outputs" / "online" / "online_mentions_monthly.parquet"
DEFAULT_QA = config.REPO_ROOT / "data" / "interim" / "online_blog_monthly_qa.csv"
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "online" / "online_features.parquet"


def _sha12(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def load_qa(path: Path) -> pd.DataFrame:
    qa = pd.read_csv(path, dtype=str, keep_default_na=False)
    if qa["store_id"].duplicated().any():
        raise ValueError("QA store_id 중복")
    out = pd.DataFrame({"store_id": qa["store_id"]})
    out["ok"] = qa["error"].str.strip().eq("")
    out["truncated"] = qa["first_date_truncated"].str.strip().str.lower().eq("true")
    trunc_date = pd.to_datetime(qa["oldest_raw_postdate"].str.strip(), format="%Y%m%d", errors="coerce")
    out["trunc_month"] = trunc_date.dt.to_period("M")
    bad = out["ok"] & out["truncated"] & out["trunc_month"].isna()
    if bad.any():
        raise ValueError(f"절단 점포인데 oldest_raw_postdate가 없다: {int(bad.sum())}건")
    return out


def load_monthly(path: Path) -> pd.DataFrame:
    m = pd.read_parquet(path)
    if "platform" in m.columns:
        m = m[m["platform"].fillna("blog") == "blog"]
    m = m[["store_id", "year_month", "mention_count"]].copy()
    m["month"] = pd.PeriodIndex(m["year_month"].astype(str), freq="M")
    m["mention_count"] = pd.to_numeric(m["mention_count"]).astype("int64")
    if m.duplicated(["store_id", "month"]).any():
        raise ValueError("월별 파일에 (store_id, year_month) 중복")
    return m


TRUNCATED_POLICIES = ("na", "lower_bound")


def build_online_features(panel: pd.DataFrame, monthly: pd.DataFrame, qa: pd.DataFrame,
                          source_snapshot: str = "", truncated_policy: str = "na") -> pd.DataFrame:
    """panel(store_id, origin, origin_end)과 같은 행 순서·행 수의 feature 테이블을 만든다.

    truncated_policy — 절단 점포의 미관측 구간 처리
      na          : 미관측 달을 포함하는 창은 NA (DECISIONS.md #25 기본값)
      lower_bound : 관측된 글만 센 값(하한)을 그대로 쓴다 (민감도용)
    두 방식 모두 수집 시점의 누적 게시물 수에 의존하므로 미래 정보가 섞인다. na는 "결측 = 인기 점포
    (생존)" 신호를, lower_bound는 반대 방향(인기 점포의 과거가 과소 집계) 편향을 만든다.
    `online_deletion_bias.missing_table`로 결측과 폐업률의 상관을 보고 고른다.
    """
    if truncated_policy not in TRUNCATED_POLICIES:
        raise ValueError(f"truncated_policy는 {TRUNCATED_POLICIES} 중 하나")
    panel = panel[["store_id", "origin", "origin_end"]].reset_index(drop=True)
    end_m = pd.PeriodIndex(pd.to_datetime(panel["origin_end"]), freq="M")
    m0 = min(end_m.min() - 12, monthly["month"].min() if len(monthly) else end_m.min() - 12)
    m1 = end_m.max()
    n_month = (m1 - m0).n + 1

    stores = pd.Index(panel["store_id"].unique())
    s_idx = stores.get_indexer(monthly["store_id"])
    keep = (s_idx >= 0) & (monthly["month"] <= m1).to_numpy()
    counts = np.zeros((len(stores), n_month), dtype=np.int64)
    mi = np.asarray([(p - m0).n for p in monthly.loc[keep, "month"]], dtype=np.int64)
    np.add.at(counts, (s_idx[keep], mi), monthly.loc[keep, "mention_count"].to_numpy())
    csum = np.concatenate([np.zeros((len(stores), 1), np.int64), counts.cumsum(axis=1)], axis=1)
    # 월 j까지(포함) 마지막 언급 월 인덱스, 없으면 -1
    has = counts > 0
    last_idx = np.where(has, np.arange(n_month)[None, :], -1)
    last_idx = np.maximum.accumulate(last_idx, axis=1)

    r = stores.get_indexer(panel["store_id"])
    e = np.asarray([(p - m0).n for p in end_m], dtype=np.int64)  # origin 마지막 달 인덱스

    def window_sum(k: int) -> np.ndarray:  # e-k+1 .. e
        return (csum[r, e + 1] - csum[r, e + 1 - k]).astype(float)

    out = pd.DataFrame({"store_id": panel["store_id"], "origin": panel["origin"]})
    c3, c12 = window_sum(3), window_sum(12)
    c6_recent = window_sum(6)
    out["online_blog_cnt_3m"] = c3
    out["online_blog_cnt_12m"] = c12
    out["online_blog_has_12m"] = (c12 > 0).astype(float)
    out["online_blog_trend_6m"] = c6_recent - (c12 - c6_recent)
    ever = csum[r, e + 1] > 0
    out["online_blog_has_ever"] = ever.astype(float)
    li = last_idx[r, e]
    out["online_blog_months_since_last"] = np.where(li >= 0, e - li, np.nan).astype(float)

    # --- 결측 규칙
    q = qa.set_index("store_id").reindex(panel["store_id"])
    observed = q["ok"].eq(True).to_numpy()  # QA 미존재·오류 → 전부 NA
    out.loc[~observed, FEATURES] = np.nan
    trunc = observed & q["truncated"].eq(True).to_numpy()
    t_idx = np.full(len(panel), -10**9, dtype=np.int64)
    tm = q["trunc_month"]
    has_tm = trunc & tm.notna().to_numpy()
    t_idx[has_tm] = [(p - m0).n for p in tm[has_tm]]

    if truncated_policy == "lower_bound":
        trunc = np.zeros_like(trunc)

    def mask_window(cols, k):
        bad = trunc & (e - k + 1 < t_idx)
        out.loc[bad, cols] = np.nan

    mask_window(["online_blog_cnt_3m"], 3)
    mask_window(["online_blog_cnt_12m", "online_blog_has_12m", "online_blog_trend_6m"], 12)
    # 누적·마지막 언급: 절단 점포는 관측 시작 이전을 모른다.
    # 관측 구간 안에서 언급을 찾았으면 has_ever=1, 마지막 언급은 유효. 못 찾았으면 NA.
    found = li >= np.maximum(t_idx, 0)
    unknown = trunc & ~(found & (li >= 0))
    out.loc[unknown, ["online_blog_has_ever", "online_blog_months_since_last"]] = np.nan

    out["online_feature_asof"] = pd.to_datetime(panel["origin_end"]).to_numpy()
    out["online_available_at"] = out["online_feature_asof"]
    out["online_source_snapshot"] = source_snapshot
    assert len(out) == len(panel)
    return out


def mask_origins(table: pd.DataFrame, origins) -> pd.DataFrame:
    """#26 진단에서 불통과한 origin의 온라인 feature를 전 점포 일괄 NA로 둔다."""
    out = table.copy()
    out.loc[out["origin"].isin(list(origins)), FEATURES] = np.nan
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="온라인 존재감 → (store_id, origin) feature")
    ap.add_argument("--panel", type=Path, default=config.LABELS_BASE_PATH)
    ap.add_argument("--monthly", type=Path, default=DEFAULT_MONTHLY)
    ap.add_argument("--qa", type=Path, default=DEFAULT_QA)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--mask-origins", default="", help="쉼표 구분 origin (#26 불통과) — 전 점포 NA")
    ap.add_argument("--truncated-policy", choices=TRUNCATED_POLICIES, default="na")
    a = ap.parse_args(argv)

    panel = pd.read_parquet(a.panel, columns=["store_id", "origin", "origin_end"])
    monthly, qa = load_monthly(a.monthly), load_qa(a.qa)
    snap = f"{a.monthly.name}@{_sha12(a.monthly)}+{a.qa.name}@{_sha12(a.qa)}"
    table = build_online_features(panel, monthly, qa, snap + f"#trunc={a.truncated_policy}", a.truncated_policy)
    if a.mask_origins.strip():
        table = mask_origins(table, [o.strip() for o in a.mask_origins.split(",") if o.strip()])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(a.out, index=False)
    print(f"{len(table):,}행 → {a.out}")
    print(table[FEATURES].isna().groupby(table["origin"]).mean().round(3).to_string())


if __name__ == "__main__":
    main()
