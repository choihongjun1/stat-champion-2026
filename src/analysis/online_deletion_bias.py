# -*- coding: utf-8 -*-
"""이슈 #26 — 오래된 origin의 온라인 feature 사용 조건: 게시물 삭제 편향 진단.

문제: 블로그 글은 2026-09에 수집했다. 오래전에 폐업한 점포일수록 폐업 후 시간이 길어 글이 더 많이
지워졌을 수 있다. 그러면 오래된 origin에서 "온라인 언급 없음"이 실제 차이보다 폐업(event=1) 쪽에
몰리고, 모델은 수집 시점의 결과(삭제)를 과거 신호로 배운다.

지표 (DECISIONS.md 2026-09-24 #26)
- origin별 "직전 12개월 블로그 언급 1건 이상 보유율"을 event=1 / event=0으로 나눠 구한다
  (`online_blog_has_12m`, 결측 점포는 분모에서 제외).
- 격차 = 보유율(event=0) − 보유율(event=1)
- 기준선 = 2022Q4~2025Q2 격차 평균. 폐업 후 경과 시간이 짧아 삭제 영향이 작은 구간이다.
- 판정: 2021Q1~2022Q3 각 origin의 (격차 − 기준선)에 대한 **점포 단위 부트스트랩 95% 구간의 하한이 0보다
  크면 불통과**(삭제 편향으로 격차가 벌어졌다). 불통과 origin은 온라인 feature를 전 점포 일괄 NA.
- 업종별로도 같은 표를 낸다 (판정은 전체 기준).

보조 진단 (판정에는 쓰지 않음)
- strict: 최근 4개 origin을 기준선으로 두고 그 앞 전부를 검정 (`*_strict.csv`). 삭제 영향이 폐업 후 경과
  시간에 따라 연속적으로 줄면 기본 기준선(2022Q4~)에도 영향이 남는다 — 합성 데이터에서 확인됨.
- origin별 격차의 선형 추세 (분기당 %p).
- origin × event별 결측률. 절단 점포 결측은 수집 시점 인기에 의존하므로 라벨과 상관될 수 있다.

한계: 2021년은 코로나 시기라 온라인 행동 자체가 달랐을 수 있다. 이 방법은 삭제 효과와 시기 효과를
분리하지 못한다. 그래서 격차가 커지면 보수적으로 제외하는 쪽으로 판정한다.

실행:
    python -m src.analysis.online_deletion_bias --features outputs/online/online_features.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import config

TEST_ORIGINS = [str(p) for p in pd.period_range("2021Q1", "2022Q3", freq="Q")]
BASELINE_ORIGINS = [str(p) for p in pd.period_range("2022Q4", "2025Q2", freq="Q")]
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "w2" / "online_deletion_bias"


def _rates(origin_idx, event, has, weight, n_origin):
    """origin × event별 가중 보유율. 반환 (n_origin, 2)."""
    num = np.zeros((n_origin, 2))
    den = np.zeros((n_origin, 2))
    np.add.at(num, (origin_idx, event), weight * has)
    np.add.at(den, (origin_idx, event), weight)
    with np.errstate(invalid="ignore", divide="ignore"):
        return num / den


def gap_table(df: pd.DataFrame, origins: list[str], *, n_boot: int = 500, seed: int = 20260925,
              baseline_origins=None, test_origins=None) -> pd.DataFrame:
    """df: store_id, origin, event_12m, online_blog_has_12m (NA 제외 전).
    origin별 격차와, 기준선 대비 차이의 점포 단위 부트스트랩 구간."""
    d = df[df["online_blog_has_12m"].notna()]
    o_idx = pd.Index(origins).get_indexer(d["origin"])
    keep = o_idx >= 0
    d, o_idx = d[keep], o_idx[keep]
    ev = d["event_12m"].to_numpy().astype(int)
    has = d["online_blog_has_12m"].to_numpy().astype(float)
    s_codes, stores = pd.factorize(d["store_id"])
    baseline_origins = list(baseline_origins or BASELINE_ORIGINS)
    test_origins = list(test_origins or TEST_ORIGINS)
    base_mask = np.isin(origins, baseline_origins)

    rates = _rates(o_idx, ev, has, np.ones(len(d)), len(origins))
    gap = rates[:, 0] - rates[:, 1]
    baseline = np.nanmean(gap[base_mask])

    rng = np.random.default_rng(seed)
    boots = np.empty((n_boot, len(origins)))
    for b in range(n_boot):
        w_store = rng.poisson(1.0, len(stores)).astype(float)  # 점포 단위 Poisson 부트스트랩
        r = _rates(o_idx, ev, has, w_store[s_codes], len(origins))
        g = r[:, 0] - r[:, 1]
        boots[b] = g - np.nanmean(g[base_mask])

    n = pd.crosstab(d["origin"], d["event_12m"]).reindex(origins).fillna(0).astype(int)
    out = pd.DataFrame({
        "origin": origins,
        "n_event0": n.get(0, pd.Series(0, index=origins)).to_numpy(),
        "n_event1": n.get(1, pd.Series(0, index=origins)).to_numpy(),
        "has12_rate_event0": rates[:, 0],
        "has12_rate_event1": rates[:, 1],
        "gap": gap,
        "gap_minus_baseline": gap - baseline,
        "diff_ci_low": np.nanpercentile(boots, 2.5, axis=0),
        "diff_ci_high": np.nanpercentile(boots, 97.5, axis=0),
    })
    out["baseline_gap"] = baseline
    out["role"] = np.where(np.isin(origins, test_origins), "test",
                           np.where(base_mask, "baseline", "other"))
    out["verdict"] = np.where(out["role"] != "test", "-",
                              np.where(out["diff_ci_low"] > 0, "FAIL", "pass"))
    return out


def missing_table(df: pd.DataFrame) -> pd.DataFrame:
    """origin × event별 온라인 feature 결측률과, 결측/비결측 점포의 폐업률."""
    na = df["online_blog_has_12m"].isna()
    t = df.assign(na=na).groupby("origin").agg(
        na_rate=("na", "mean"),
        na_rate_event1=("na", lambda s: s[df.loc[s.index, "event_12m"] == 1].mean()),
        na_rate_event0=("na", lambda s: s[df.loc[s.index, "event_12m"] == 0].mean()),
    )
    ev = df.groupby(["origin", na])["event_12m"].mean().unstack()
    t["event_rate_if_na"] = ev.get(True)
    t["event_rate_if_observed"] = ev.get(False)
    return t.reset_index()


def plot(tab: pd.DataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from src.models.calibration import _use_korean_font

    _use_korean_font()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    x = np.arange(len(tab))
    y = tab["gap_minus_baseline"].to_numpy()
    lo, hi = tab["diff_ci_low"].to_numpy(), tab["diff_ci_high"].to_numpy()
    ink, muted = "#2F5D8C", "#8A8F98"
    ax.axhline(0, color=muted, lw=1)
    ax.axvspan(-0.5, len(TEST_ORIGINS) - 0.5, color="#EEF1F5", zorder=0)
    ax.errorbar(x, y, yerr=[y - lo, hi - y], fmt="o", ms=5, color=ink, ecolor=ink, elinewidth=1.4, capsize=0)
    for i, v in enumerate(tab["verdict"]):
        if v == "FAIL":
            ax.annotate("불통과", (x[i], hi[i]), xytext=(0, 6), textcoords="offset points",
                        ha="center", fontsize=8, color="#333333")
    ax.set_xticks(x, tab["origin"], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("보유율 격차 - 기준선 (%p)")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v * 100:.0f}"))
    ax.set_title("온라인 언급 보유율 격차 (비폐업 - 폐업), 기준선 2022Q4~2025Q2 대비 · 95% 부트스트랩",
                 fontsize=10)
    ax.text(0, ax.get_ylim()[1], " 판정 구간", va="top", fontsize=8, color="#555555")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="#E3E6EA", lw=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(features_path: Path, panel_path: Path, out_dir: Path, n_boot: int) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    feat = pd.read_parquet(features_path, columns=["store_id", "origin", "online_blog_has_12m"])
    panel = pd.read_parquet(panel_path, columns=["store_id", "origin", "event_12m", "biz_type"])
    df = panel.merge(feat, on=["store_id", "origin"], how="left", validate="1:1")
    origins = sorted(df["origin"].unique())

    tab = gap_table(df, origins, n_boot=n_boot)
    tab.to_csv(out_dir / "deletion_bias_by_origin.csv", index=False)
    # 보조 판정(strict): 삭제 영향은 폐업 후 경과 시간에 따라 연속적으로 줄어드므로, 기준 구간(2022Q4~)에도
    # 영향이 남아 있을 수 있다. 가장 최근 4개 origin만 기준선으로 두고 나머지 전부를 검정한다.
    strict = gap_table(df, origins, n_boot=n_boot, baseline_origins=origins[-4:], test_origins=origins[:-4])
    strict.to_csv(out_dir / "deletion_bias_by_origin_strict.csv", index=False)
    slope = np.polyfit(np.arange(len(origins)), tab["gap"].to_numpy(), 1)[0]
    by_biz = [gap_table(g, origins, n_boot=n_boot).assign(biz_type=b) for b, g in df.groupby("biz_type")]
    pd.concat(by_biz).to_csv(out_dir / "deletion_bias_by_origin_biz.csv", index=False)
    missing_table(df).to_csv(out_dir / "online_missing_by_origin.csv", index=False)
    plot(tab, out_dir / "deletion_bias_by_origin.png")

    fails = tab.loc[tab["verdict"] == "FAIL", "origin"].tolist()
    (out_dir / "failed_origins.txt").write_text(",".join(fails), encoding="utf-8")
    cols = ["origin", "role", "has12_rate_event0", "has12_rate_event1", "gap", "gap_minus_baseline",
            "diff_ci_low", "diff_ci_high", "verdict"]
    print(tab[cols].round(4).to_string(index=False))
    strict_fails = strict.loc[strict["verdict"] == "FAIL", "origin"].tolist()
    print(f"\n기준선 격차 {tab['baseline_gap'].iloc[0]:.4f} / 불통과 origin: {fails or '없음'}")
    print(f"[보조] 최근 4개 origin 기준선 불통과: {strict_fails or '없음'} / 격차 추세 {slope * 100:+.2f}%p/분기")
    print(f"→ 불통과 origin 마스킹: python -m src.data.online_features --mask-origins {','.join(fails)}")
    return tab


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#26 온라인 삭제 편향 진단")
    ap.add_argument("--features", type=Path,
                    default=config.REPO_ROOT / "outputs" / "online" / "online_features.parquet")
    ap.add_argument("--panel", type=Path, default=config.LABELS_BASE_PATH)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--n-boot", type=int, default=500)
    a = ap.parse_args(argv)
    run(a.features, a.panel, a.out, a.n_boot)


if __name__ == "__main__":
    main()
