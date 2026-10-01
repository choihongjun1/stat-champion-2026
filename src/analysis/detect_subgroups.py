# -*- coding: utf-8 -*-
"""탐지 모형 집단별 성능·시간 안정성 점검 (보고서용, 집단 단위 집계만).

산출물 파일만 읽는다 (탐지 모형 코드를 import하지 않는다).
- risk_scores.parquet : 검증 구간(rolling OOF 마지막 origin들) 점포별 예측·등급·event_12m
- master_base.parquet : age_months, 상권 feature (전부 결측이면 상권 밖)
- 온라인 feature parquet : online_blog_cnt_12m (결측 아니면 온라인 관측)
- detect-dir : run_meta.json(모형 이름), oof_metrics_by_origin.csv, band_share_by_origin.csv (시간 안정성)
- (선택) oof_calibration_by_origin.csv : origin별 OOF 예측 평균·실측률 — 탐지 산출물에 없어 별도로 만든 파일
  (`--oof-calibration`, 기본은 --out 폴더에 있으면 사용)

집단: 전체 / 자치구 / 업종 / 업력대 / 상권 안·밖 / 온라인 관측·미관측 / 자치구×업종.
사건 수 < 30인 집단은 지표를 NaN으로 두고 "표본 부족"으로 표시한다. AUC·AP 95% 구간은 점포 단위 부트스트랩.
출력에는 점포 식별 열(store_id 등)을 넣지 않는다.

실행:
    python -m src.analysis.detect_subgroups --risk outputs/models/detect_v0_enriched/risk_scores.parquet \\
        --master outputs/master/master_base.parquet --online outputs/online/online_features.parquet \\
        --detect-dir outputs/models/detect_v0_enriched --n-boot 200 --out outputs/w2/subgroups
"""
from __future__ import annotations

import argparse
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score, roc_auc_score

from src.data import master_schema as ms

MIN_EVENTS = 30
SEED = 20260926
BANDS = ("low", "mid", "high")
# 업력대 — 진단(W2-3)과 같은 경계: 12·36·60·120개월 이하가 아래 구간
AGE_EDGES = [(12, "1년 미만"), (36, "1–3년"), (60, "3–5년"), (120, "5–10년"), (np.inf, "10년 이상")]
AGE_ORDER = [name for _, name in AGE_EDGES]
ONLINE_OBS_COL = "online_blog_cnt_12m"
FORBIDDEN_OUT = ("store_id", "name", "address", "sj_entity_id", "pnu")


def age_band(age_months: pd.Series) -> pd.Series:
    out = pd.Series(pd.NA, index=age_months.index, dtype="object")
    lo = -np.inf
    for hi, name in AGE_EDGES:
        out[(age_months > lo) & (age_months <= hi)] = name
        lo = hi
    return out


def trdar_columns(columns) -> list[str]:
    """상권 feature = master_schema predictor 중 trdar_ 계열 (상권 단위 + 업종 단위)."""
    return [c for c in ms.predictor_columns(columns) if c.startswith("trdar_")]


def build_panel(risk: pd.DataFrame, master: pd.DataFrame, online: pd.DataFrame) -> pd.DataFrame:
    """risk_scores에 업력·상권 소속·온라인 관측 여부를 (store_id, origin) 1:1로 붙인다."""
    key = ["store_id", "origin"]
    for name, df in (("risk", risk), ("master", master), ("online", online)):
        if df.duplicated(key).any():
            raise ValueError(f"{name}: (store_id, origin) 중복")
    tr = trdar_columns(master.columns)
    if not tr:
        raise ValueError("master에 상권 feature가 없다")
    m = master[key + ["age_months"] + tr].copy()
    m["in_trdar"] = m[tr].notna().any(axis=1)
    o = online[key + [ONLINE_OBS_COL]].copy()
    o["online_observed"] = o[ONLINE_OBS_COL].notna()
    p = risk.merge(m[key + ["age_months", "in_trdar"]], on=key, how="left", validate="one_to_one")
    p = p.merge(o[key + ["online_observed"]], on=key, how="left", validate="one_to_one")
    if len(p) != len(risk) or p["age_months"].isna().any() or p["in_trdar"].isna().any():
        raise ValueError("risk_scores 행이 master와 1:1로 붙지 않는다")
    p["online_observed"] = p["online_observed"].eq(True)  # 온라인 테이블에 없는 행 = 미관측
    p["age_band"] = age_band(p["age_months"])
    if p["age_band"].isna().any():
        raise ValueError("업력대를 정할 수 없는 행 (age_months 음수?)")
    p["trdar_in"] = np.where(p["in_trdar"], "상권 안", "상권 밖")
    p["online_obs"] = np.where(p["online_observed"], "온라인 관측", "온라인 미관측")
    p["gu_biz"] = p["gu"].astype(str) + "×" + p["biz_type"].astype(str)
    return p


GROUPINGS = [("전체", None), ("자치구", "gu"), ("업종", "biz_type"), ("업력대", "age_band"),
             ("상권", "trdar_in"), ("온라인", "online_obs"), ("자치구×업종", "gu_biz")]


def _bootstrap(y: np.ndarray, p: np.ndarray, store: np.ndarray, n_boot: int, seed: int) -> tuple:
    """점포 단위 재표본 (한 점포의 여러 origin 행을 함께 뽑는다). 95% 백분위."""
    if n_boot <= 0:
        return (np.nan,) * 4
    codes, uniq = pd.factorize(store)
    rows_of = pd.Series(np.arange(len(codes))).groupby(codes).apply(np.asarray).to_numpy()
    rng = np.random.default_rng(seed)
    aucs, aps = [], []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate(rows_of[pick])
        yb = y[idx]
        if yb.min() == yb.max():
            continue
        aucs.append(roc_auc_score(yb, p[idx]))
        aps.append(average_precision_score(yb, p[idx]))
    if not aucs:
        return (np.nan,) * 4
    return (*np.percentile(aucs, [2.5, 97.5]), *np.percentile(aps, [2.5, 97.5]))


def group_metrics(df: pd.DataFrame, overall_rate: float, n_boot: int, seed: int, label: str) -> dict:
    y = df["event_12m"].to_numpy().astype(int)
    p = df["probability_12m"].to_numpy().astype(float)
    n, k = len(y), int(y.sum())
    row = {"n": n, "events": k, "base_rate": k / n if n else np.nan, "pred_mean": p.mean() if n else np.nan}
    row["calib_gap"] = row["pred_mean"] - row["base_rate"]
    for b in BANDS:
        m = (df["band"] == b).to_numpy()
        row[f"share_{b}"] = m.mean() if n else np.nan
        row[f"obs_rate_{b}"] = y[m].mean() if m.any() else np.nan
    enough = k >= MIN_EVENTS and (n - k) >= MIN_EVENTS
    row["note"] = "" if enough else "표본 부족"
    if enough:
        row["auc"] = roc_auc_score(y, p)
        row["ap"] = average_precision_score(y, p)
        row["ap_lift"] = row["ap"] / row["base_rate"]
        row["high_lift"] = row["obs_rate_high"] / overall_rate if pd.notna(row["obs_rate_high"]) else np.nan
        s = seed ^ zlib.crc32(label.encode("utf-8"))  # 집단마다 고정된 난수열 (순서와 무관하게 재현)
        row["auc_lo"], row["auc_hi"], row["ap_lo"], row["ap_hi"] = _bootstrap(
            y, p, df["store_id"].to_numpy(), n_boot, s)
    else:
        for c in ("auc", "ap", "ap_lift", "high_lift", "auc_lo", "auc_hi", "ap_lo", "ap_hi"):
            row[c] = np.nan
    return row


def subgroup_table(panel: pd.DataFrame, n_boot: int, seed: int = SEED) -> pd.DataFrame:
    overall_rate = panel["event_12m"].mean()
    rows = []
    for dim, col in GROUPINGS:
        if col is None:
            parts = [("전체", panel)]
        else:
            keys = AGE_ORDER if col == "age_band" else sorted(panel[col].dropna().unique())
            parts = [(k, panel[panel[col] == k]) for k in keys if (panel[col] == k).any()]
        for name, sub in parts:
            rows.append({"dimension": dim, "group": name,
                         **group_metrics(sub, overall_rate, n_boot, seed, f"{dim}|{name}")})
    cols = ["dimension", "group", "n", "events", "base_rate", "auc", "auc_lo", "auc_hi", "ap", "ap_lo", "ap_hi",
            "ap_lift", "pred_mean", "calib_gap", *[f"share_{b}" for b in BANDS], *[f"obs_rate_{b}" for b in BANDS],
            "high_lift", "note"]
    out = pd.DataFrame(rows)[cols]
    assert not any(any(f in c for f in FORBIDDEN_OUT) for c in out.columns)
    return out


MODEL_BASE = "detect_v0"


def detect_model_name(detect_dir: Path, risk: pd.DataFrame) -> tuple[str, str]:
    """모형 이름과 주 feature set. run_meta.json(primary_feature_set)이 기준이고, 없으면 risk_scores의 model 열."""
    meta = Path(detect_dir) / "run_meta.json"
    if meta.exists():
        fs = pd.read_json(meta, typ="series").get("primary_feature_set", "base")
        return (MODEL_BASE if fs == "base" else f"{MODEL_BASE}_{fs}"), fs
    print(f"경고: {meta}가 없어 risk_scores의 model 열로 모형 이름을 쓴다 (예전 실행은 이 열이 부정확할 수 있다)")
    name = ",".join(sorted(risk["model"].astype(str).unique())) if "model" in risk else "?"
    fs = name.split("_", 2)[2] if name.startswith(MODEL_BASE + "_") else "base"
    # model 열이 가리키는 feature set이 OOF 표에 없고 OOF 표에 feature set이 하나뿐이면 그것을 쓴다
    oof_path = Path(detect_dir) / "oof_metrics_by_origin.csv"
    if oof_path.exists():
        sets = set(pd.read_csv(oof_path, usecols=lambda c: c == "feature_set").get("feature_set", pd.Series(dtype=str)))
        if sets and fs not in sets and len(sets) == 1:
            fs = sets.pop()
            print(f"경고: OOF 표의 feature set({fs})으로 시간 안정성을 만든다")
    return name, fs


def time_stability(detect_dir: Path, feature_set: str = "enriched",
                   oof_calibration: Path | None = None) -> pd.DataFrame:
    """origin별 AUC·AP·등급 비율 (+ 있으면 OOF 예측 평균·예측−실측·high 실측률)."""
    oof = pd.read_csv(detect_dir / "oof_metrics_by_origin.csv")
    if "feature_set" in oof.columns:
        if feature_set not in set(oof["feature_set"]):
            raise ValueError(f"oof_metrics_by_origin.csv에 {feature_set} 결과가 없다")
        oof = oof[oof["feature_set"] == feature_set]
    band = pd.read_csv(detect_dir / "band_share_by_origin.csv")
    t = oof[["origin", "n", "base_rate", "auc", "ap"]].merge(band, on="origin", how="left")
    if oof_calibration is not None and Path(oof_calibration).exists():
        c = pd.read_csv(oof_calibration, encoding="utf-8-sig")
        t = t.merge(c[["origin", "pred_mean", "calib_gap", "obs_rate_high"]], on="origin", how="left")
        if t["pred_mean"].isna().any():
            raise ValueError(f"{oof_calibration}에 없는 origin이 있다")
    return t.sort_values("origin").reset_index(drop=True)


# ---------------------------------------------------------------------------- 그림
def _font():
    import matplotlib
    from matplotlib import font_manager

    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Malgun Gothic", "NanumGothic", "AppleGothic", "Noto Sans CJK KR"):
        if cand in names:
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def plot_auc(tab: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _font()
    t = tab[tab["auc"].notna()].reset_index(drop=True)
    labels = [f"{d}: {g}" if d != "전체" else "전체" for d, g in zip(t["dimension"], t["group"])]
    y = np.arange(len(t))[::-1]
    fig, ax = plt.subplots(figsize=(7.5, 0.32 * len(t) + 1.2))
    err = np.vstack([t["auc"] - t["auc_lo"], t["auc_hi"] - t["auc"]])
    ax.errorbar(t["auc"], y, xerr=np.nan_to_num(err), fmt="o", color="#2b6cb0", ecolor="#90b4d8", capsize=2, ms=4)
    overall = tab.loc[tab["dimension"] == "전체", "auc"].iloc[0]
    ax.axvline(overall, color="#c05621", ls="--", lw=1, label=f"전체 AUC {overall:.3f}")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("AUC (95% 점포 단위 부트스트랩)")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _label_points(ax, g: pd.DataFrame) -> None:
    """라벨끼리 겹치지 않게 후보 위치(오른쪽·왼쪽·위·아래) 중 이미 놓인 라벨과 먼 곳을 고른다 (화면 픽셀 기준)."""
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    cands = [(5, -3, "left"), (-5, -3, "right"), (5, 7, "left"), (5, -13, "left"), (-5, 7, "right"), (-5, -13, "right")]
    placed = []
    for r in g.sort_values("base_rate", ascending=False).itertuples(index=False):
        best = None
        for dx, dy, ha in cands:
            ann = ax.annotate(r.group, (r.pred_mean, r.base_rate), fontsize=7, xytext=(dx, dy),
                              textcoords="offset points", ha=ha)
            bb = ann.get_window_extent(renderer).expanded(1.05, 1.15)
            if not any(bb.overlaps(p) for p in placed):
                best = (ann, bb)
                break
            ann.remove()
        if best is None:  # 모두 겹치면 첫 후보에 둔다
            ann = ax.annotate(r.group, (r.pred_mean, r.base_rate), fontsize=7, xytext=cands[0][:2],
                              textcoords="offset points", ha=cands[0][2])
            best = (ann, ann.get_window_extent(renderer))
        placed.append(best[1])


def plot_calibration(tab: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _font()
    t = tab
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    lim = [0, max(t["pred_mean"].max(), t["base_rate"].max()) * 1.15]
    ax.plot(lim, lim, color="#999", lw=1, ls="--", label="예측 = 실측")
    # 라벨은 자치구×업종 9개에만 붙인다 (나머지는 점 + 범례로 차원 구분 — 가운데 겹침 방지)
    markers = {"전체": "*", "자치구×업종": "D"}
    for dim, g in t.groupby("dimension", sort=False):
        ax.scatter(g["pred_mean"], g["base_rate"], s=70 if dim == "전체" else 30, marker=markers.get(dim, "o"),
                   label=dim, alpha=0.85, zorder=3)
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    _label_points(ax, t[t["dimension"] == "자치구×업종"])
    ax.set_xlabel("예측 평균 (probability_12m)")
    ax.set_ylabel("실측 폐업률 (event_12m)")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------- 요약
def _f(x, pct=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:.1%}" if pct else f"{x:.3f}"


def key_points(tab: pd.DataFrame, ts: pd.DataFrame) -> list[str]:
    ov = tab[tab["dimension"] == "전체"].iloc[0]
    sub = tab[(tab["dimension"] != "전체") & tab["auc"].notna()]
    pts = []
    if len(sub):
        lo, hi = sub.loc[sub["auc"].idxmin()], sub.loc[sub["auc"].idxmax()]
        pts.append(f"집단 간 AUC 범위: {lo['auc']:.3f}({lo['dimension']} {lo['group']}) – "
                   f"{hi['auc']:.3f}({hi['dimension']} {hi['group']}), 전체 {ov['auc']:.3f} "
                   f"[{ov['auc_lo']:.3f}, {ov['auc_hi']:.3f}]")
        apart = sub[(sub["auc_hi"] < ov["auc_lo"]) | (sub["auc_lo"] > ov["auc_hi"])]
        pts.append("AUC 신뢰구간이 전체와 겹치지 않는 집단: " + (
            ", ".join(f"{r.dimension} {r.group} {r.auc:.3f} [{r.auc_lo:.3f}, {r.auc_hi:.3f}]"
                      for r in apart.itertuples(index=False)) or "없음"))
    cg = tab[tab["dimension"] != "전체"]
    over, under = cg.loc[cg["calib_gap"].idxmax()], cg.loc[cg["calib_gap"].idxmin()]
    pts.append(f"과대 예측 최대: {over['dimension']} {over['group']} ({over['calib_gap'] * 100:+.1f}%p) · "
               f"과소 예측 최대: {under['dimension']} {under['group']} ({under['calib_gap'] * 100:+.1f}%p)")
    weak = sub[sub["high_lift"] < 1.5]
    pts.append("high 등급 실측률÷전체 기준률 < 1.5인 집단: " + (
        ", ".join(f"{r.dimension} {r.group} {r.high_lift:.2f}" for r in weak.itertuples(index=False)) or "없음"))
    if len(ts):
        pts.append(f"origin별 변동 폭({ts['origin'].iloc[0]}–{ts['origin'].iloc[-1]}, {len(ts)}개): "
                   f"AUC {ts['auc'].min():.3f}–{ts['auc'].max():.3f}, AP {ts['ap'].min():.3f}–{ts['ap'].max():.3f}, "
                   f"high 비율 {_f(ts['high'].min(), True)}–{_f(ts['high'].max(), True)}"
                   + (f", 예측 평균 {_f(ts['pred_mean'].iloc[0], True)}→{_f(ts['pred_mean'].iloc[-1], True)} "
                      f"(예측−실측 {ts['calib_gap'].iloc[0] * 100:+.1f}%p→{ts['calib_gap'].iloc[-1] * 100:+.1f}%p)"
                      if "pred_mean" in ts else ""))
    return pts


def summary_md(tab: pd.DataFrame, ts: pd.DataFrame, meta: dict) -> str:
    lines = ["# 탐지 모형 집단별 성능·시간 안정성 점검", "",
             f"- 입력: `{meta['risk']}` (origin {meta['origins']}, {meta['n']:,}행, 사건 {meta['events']:,}) · 모형 {meta['model']}",
             f"- AUC·AP 95% 구간: 점포 단위 부트스트랩 {meta['n_boot']}회 (seed {SEED}). 사건 수 < {MIN_EVENTS}인 집단은 지표 없음(표본 부족).",
             "- high 배수 = 집단의 high 등급 실측 폐업률 ÷ 전체 기준 폐업률. 예측−실측 = 예측 평균 − 실측 폐업률.", "",
             "## 요점", ""] + [f"- {p}" for p in key_points(tab, ts)] + ["", "## 집단별 지표", "",
             "| 차원 | 집단 | n | 사건 | 폐업률 | AUC [95%] | AP [95%] | AP÷기준 | 예측−실측 | low/mid/high 비율 | high 실측 | high 배수 | 비고 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in tab.itertuples(index=False):
        auc = "—" if np.isnan(r.auc) else f"{r.auc:.3f} [{_f(r.auc_lo)}, {_f(r.auc_hi)}]"
        ap = "—" if np.isnan(r.ap) else f"{r.ap:.3f} [{_f(r.ap_lo)}, {_f(r.ap_hi)}]"
        shares = f"{r.share_low:.0%}/{r.share_mid:.0%}/{r.share_high:.0%}"
        lines.append(f"| {r.dimension} | {r.group} | {r.n:,} | {r.events:,} | {_f(r.base_rate, True)} | {auc} | {ap} | "
                     f"{_f(r.ap_lift)} | {r.calib_gap * 100:+.1f}%p | {shares} | {_f(r.obs_rate_high, True)} | "
                     f"{_f(r.high_lift)} | {r.note} |")
    cal = "pred_mean" in ts.columns
    lines += ["", f"## origin별 안정성 (rolling OOF, {meta['feature_set']})", ""]
    if cal:
        lines += ["| origin | n | 폐업률 | 예측 평균 | 예측−실측 | AUC | AP | low | mid | high | high 실측 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
    else:
        lines += ["| origin | n | 폐업률 | AUC | AP | low | mid | high |", "|---|---|---|---|---|---|---|---|"]
    for r in ts.itertuples(index=False):
        mid = f" {_f(r.pred_mean, True)} | {r.calib_gap * 100:+.1f}%p |" if cal else ""
        tail = f" {_f(r.obs_rate_high, True)} |" if cal else ""
        lines.append(f"| {r.origin} | {int(r.n):,} | {_f(r.base_rate, True)} |{mid} {_f(r.auc)} | {_f(r.ap)} | "
                     f"{_f(r.low, True)} | {_f(r.mid, True)} | {_f(r.high, True)} |{tail}")
    if cal:
        lines += ["", f"- 예측 평균·high 실측률: `{meta['oof_calibration']}` (rolling OOF를 같은 규칙으로 다시 계산해 "
                  "AUC·등급 비율이 탐지 산출물과 일치함을 확인한 파일)"]
    return "\n".join(lines) + "\n"


def run(risk_path: Path, master_path: Path, online_path: Path, detect_dir: Path, out: Path, n_boot: int,
        oof_calibration: Path | None = None) -> pd.DataFrame:
    t0 = time.time()
    risk = pd.read_parquet(risk_path)
    need = {"store_id", "origin", "gu", "biz_type", "probability_12m", "band", "event_12m"}
    if need - set(risk.columns):
        raise ValueError(f"risk_scores에 없는 열: {sorted(need - set(risk.columns))}")
    mcols = pq.read_schema(master_path).names
    master = pd.read_parquet(master_path, columns=["store_id", "origin", "age_months"] + trdar_columns(mcols))
    master = master[master.set_index(["store_id", "origin"]).index.isin(risk.set_index(["store_id", "origin"]).index)]
    online = pd.read_parquet(online_path, columns=["store_id", "origin", ONLINE_OBS_COL])
    panel = build_panel(risk, master, online)
    tab = subgroup_table(panel, n_boot)
    model, fs = detect_model_name(detect_dir, risk)
    oof_calibration = oof_calibration or (out / "oof_calibration_by_origin.csv")
    ts = time_stability(detect_dir, fs, oof_calibration)
    out.mkdir(parents=True, exist_ok=True)
    tab.to_csv(out / "subgroup_metrics.csv", index=False, encoding="utf-8-sig")
    ts.to_csv(out / "time_stability.csv", index=False, encoding="utf-8-sig")
    plot_auc(tab, out / "fig_subgroup_auc.png")
    plot_calibration(tab, out / "fig_subgroup_calibration.png")
    meta = {"risk": risk_path, "origins": "·".join(sorted(risk["origin"].astype(str).unique())), "n": len(risk),
            "events": int(risk["event_12m"].sum()), "model": model, "feature_set": fs, "n_boot": n_boot,
            "oof_calibration": oof_calibration}
    (out / "SUMMARY.md").write_text(summary_md(tab, ts, meta), encoding="utf-8")
    print(f"집단 {len(tab)}개 · 부트스트랩 {n_boot}회 · {time.time() - t0:.1f}초 → {out}")
    return tab


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="탐지 모형 집단별 성능·시간 안정성 점검")
    ap.add_argument("--risk", type=Path, required=True)
    ap.add_argument("--master", type=Path, required=True)
    ap.add_argument("--online", type=Path, required=True)
    ap.add_argument("--detect-dir", type=Path, required=True)
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--oof-calibration", type=Path, default=None,
                    help="origin별 OOF 예측 평균 csv (기본: --out 폴더의 oof_calibration_by_origin.csv가 있으면 사용)")
    a = ap.parse_args(argv)
    run(a.risk, a.master, a.online, a.detect_dir, a.out, a.n_boot, a.oof_calibration)


if __name__ == "__main__":
    main()
