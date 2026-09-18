# -*- coding: utf-8 -*-
"""공간조인 QA figure 생성 (generated artifact — git 미추적).

- Figure A: d1 vs (d2-d1) 산점도. "가까운 nearest라도 2순위와 충분히 분리되지 않을 수
  있다"는 점을 보여주는 QA figure. provisional QA 기준 영역을 참고선으로만 표시하며
  '정답 영역'이 아니다.
- Figure B: 대표 사례의 local map (store point + 1·2순위 polygon + 경계 + d1/d2).
  basemap/외부 API 없이 현재 SHP만으로 재현한다.

이 모듈은 파이프라인의 선택적 부가 산출물이며, matplotlib이 없으면 건너뛴다.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from src.data.spatial import (  # noqa: E402
    PROVISIONAL_QA_D1_MAX_M,
    PROVISIONAL_QA_GAP_MIN_M,
)

BAND_COLORS = {
    "0–20m": "#1f77b4",
    "20–50m": "#2ca02c",
    "50–100m": "#ff7f0e",
    ">100m": "#d62728",
}


def _use_korean_font() -> None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Malgun Gothic", "NanumGothic", "Gulim"):
        if cand in names:
            plt.rcParams["font.family"] = cand
            break
    plt.rcParams["axes.unicode_minus"] = False


def _band(d: float) -> str:
    if d <= 20:
        return "0–20m"
    if d <= 50:
        return "20–50m"
    if d <= 100:
        return "50–100m"
    return ">100m"


def figure_a_gap_scatter(out: pd.DataFrame, review: pd.DataFrame,
                         out_dir: Path) -> Path:
    """d1 vs gap 산점도 (전체 within 미매칭 배경 + 검증 표본 강조)."""
    _use_korean_font()
    un = out[out["nearest_distance_m"].notna() & out["nearest_gap_m"].notna()]
    rv = review[review["nearest_distance_m_recomputed"].notna()
                & review["nearest_gap_m"].notna()].copy()

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for ax, xmax, title in (
        (axes[0], 300, "전체 within 미매칭 (d1 ≤ 300m 구간)"),
        (axes[1], 120, "검증 표본 42건 (d1 ≤ 120m 구간)"),
    ):
        # provisional QA 후보 영역 (정답 영역 아님)
        ax.add_patch(mpatches.Rectangle(
            (0, PROVISIONAL_QA_GAP_MIN_M), PROVISIONAL_QA_D1_MAX_M, 1e6,
            facecolor="#cccccc", alpha=0.35, zorder=0,
            label=f"provisional QA candidate region\n"
                  f"(d1≤{PROVISIONAL_QA_D1_MAX_M:.0f}m & gap≥{PROVISIONAL_QA_GAP_MIN_M:.0f}m) "
                  f"— 정답 영역 아님"))
        ax.axvline(PROVISIONAL_QA_D1_MAX_M, color="#888888", ls="--", lw=0.9, zorder=1)
        ax.axhline(PROVISIONAL_QA_GAP_MIN_M, color="#888888", ls="--", lw=0.9, zorder=1)
        ax.set_xlim(0, xmax)
        ax.set_xlabel("d1 = nearest_distance_m (m)")
        ax.set_ylabel("gap = d2 - d1 (m)")
        ax.set_title(title, fontsize=11)
        ax.grid(alpha=0.25, zorder=0)

    sub = un[un["nearest_distance_m"] <= 300]
    axes[0].scatter(sub["nearest_distance_m"], sub["nearest_gap_m"],
                    s=4, alpha=0.18, color="#4c72b0", zorder=2,
                    label=f"within 미매칭 {len(sub):,}건")
    axes[0].set_ylim(0, min(400, float(sub["nearest_gap_m"].quantile(0.995)) + 20))
    axes[0].legend(loc="upper right", fontsize=8)

    rv["band"] = rv["nearest_distance_m_recomputed"].map(_band)
    for band, color in BAND_COLORS.items():
        g = rv[rv["band"] == band]
        if len(g) == 0:
            continue
        axes[1].scatter(g["nearest_distance_m_recomputed"], g["nearest_gap_m"],
                        s=55, color=color, edgecolor="black", linewidth=0.6,
                        zorder=3, label=f"{band} (n={len(g)})")
    axes[1].set_ylim(0, float(rv["nearest_gap_m"].max()) * 1.15 + 5)
    axes[1].legend(loc="upper right", fontsize=8)

    fig.suptitle(
        "Figure A — nearest 거리(d1)와 2순위 분리도(gap): 짧은 d1도 분리도가 낮을 수 있다\n"
        "(QA/sensitivity 전용 — base assignment는 within-only)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    path = out_dir / "nearest_gap_validation.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _pick_cases(review: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    """대표 사례 선택: boundary / 분리 나쁜 단거리 / 분리 좋은 단거리 / 중거리."""
    rv = review[review["nearest_distance_m_recomputed"].notna()
                & review["second_nearest_distance_m"].notna()].copy()
    cases: list[tuple[str, pd.Series]] = []
    if rv.empty:
        return cases
    short = rv[rv["nearest_distance_m_recomputed"] <= PROVISIONAL_QA_D1_MAX_M]

    cases.append(("boundary case (최단 d1)",
                  rv.loc[rv["nearest_distance_m_recomputed"].idxmin()]))
    if len(short):
        worst = short.loc[short["nearest_gap_m"].idxmin()]
        cases.append(("ambiguous short-distance (gap 최소)", worst))
        best = short.loc[short["nearest_gap_m"].idxmax()]
        if best["store_id"] != worst["store_id"]:
            cases.append(("clear short-distance (gap 최대)", best))
    mid = rv[(rv["nearest_distance_m_recomputed"] > 50)
             & (rv["nearest_distance_m_recomputed"] <= 100)]
    if len(mid):
        cases.append(("50–100m case", mid.loc[mid["nearest_gap_m"].idxmin()]))
    seen, uniq = set(), []
    for label, row in cases:
        if row["store_id"] in seen:
            continue
        seen.add(row["store_id"])
        uniq.append((label, row))
    return uniq[:4]


def figure_b_case_maps(review: pd.DataFrame, areas, out_dir: Path) -> Path | None:
    """대표 사례 local map: store point + 1·2순위 polygon + d1/d2."""
    _use_korean_font()
    cases = _pick_cases(review)
    if not cases:
        return None

    ncol = min(2, len(cases))
    nrow = (len(cases) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(7.0 * ncol, 6.2 * nrow),
                             squeeze=False)
    area_idx = areas.set_index("TRDAR_CD")

    for k, (label, row) in enumerate(cases):
        ax = axes[k // ncol][k % ncol]
        x, y = float(row["x_5179"]), float(row["y_5179"])
        d1 = float(row["nearest_distance_m_recomputed"])
        d2 = float(row["second_nearest_distance_m"])
        c1, c2 = row["nearest_trdar_cd_recomputed"], row["second_nearest_trdar_cd"]

        pad = max(40.0, d2 * 1.6)
        for cd, color, tag in ((c1, "#1f77b4", "1순위"), (c2, "#d62728", "2순위")):
            if cd is None or pd.isna(cd) or cd not in area_idx.index:
                continue
            geom = area_idx.loc[cd, "geometry"]
            nm = area_idx.loc[cd, "TRDAR_CD_N"]
            gpd_geom = areas[areas["TRDAR_CD"] == cd]
            gpd_geom.plot(ax=ax, facecolor=color, alpha=0.18, edgecolor=color,
                          linewidth=1.8, zorder=2)
            gpd_geom.boundary.plot(ax=ax, color=color, linewidth=1.8, zorder=3)
            ax.plot([], [], color=color, linewidth=3,
                    label=f"{tag} {cd} {nm}")
            _ = geom

        # 주변 맥락: 화면 범위에 걸치는 다른 상권 경계는 옅게
        window = areas.cx[x - pad:x + pad, y - pad:y + pad]
        others = window[~window["TRDAR_CD"].isin([c1, c2])]
        if len(others):
            others.boundary.plot(ax=ax, color="#999999", linewidth=0.7,
                                 linestyle=":", zorder=1)

        ax.scatter([x], [y], s=110, color="black", marker="*", zorder=5,
                   label="store point")
        ax.set_xlim(x - pad, x + pad)
        ax.set_ylim(y - pad, y + pad)
        ax.set_aspect("equal")
        ax.set_xlabel("x (EPSG:5179, m)")
        ax.set_ylabel("y (EPSG:5179, m)")
        name = str(row.get("name_raw", ""))[:18]
        ax.set_title(
            f"{label}\n{row['store_id']} · {name}\n"
            f"d1={d1:.2f}m ({c1}) / d2={d2:.2f}m ({c2}) / gap={d2 - d1:.2f}m",
            fontsize=10)
        ax.legend(loc="upper right", fontsize=7.5)
        ax.grid(alpha=0.2)

    for k in range(len(cases), nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")

    fig.suptitle(
        "Figure B — 대표 사례 local map (현재 SHP만으로 재현, basemap 미사용)\n"
        "QA 확인용이며 nearest 배정을 정당화하지 않는다", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = out_dir / "nearest_case_maps.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def make_all(out: pd.DataFrame, review: pd.DataFrame, areas,
             out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [figure_a_gap_scatter(out, review, out_dir)]
    b = figure_b_case_maps(review, areas, out_dir)
    if b is not None:
        paths.append(b)
    return paths
