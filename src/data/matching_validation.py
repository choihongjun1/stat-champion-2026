# -*- coding: utf-8 -*-
"""ER 매칭 규칙 validation: 수작업 판정 표본으로 후보 규칙의 precision/coverage를 비교한다.

실행:
    python -m src.data.matching_validation

입력 (outputs/matching/validation/, git 미추적):
    - match_precision_sample_v1.csv      판정에 쓴 고정 표본 (140건). 파이프라인을 다시
                                          돌리면 표본이 바뀌므로 판정 당시 파일을 고정해 쓴다.
    - precision_labels_*.csv              행 단위 판정 (store_id, band, firstpass_label ...)
    - match_validation_sample_v1.csv      Tier1/2 sanity 표본 (56건)
    - license_semas_matches_baseline.parquet  규칙 변경 전 전체 매칭 (coverage 기준선)
입력 (outputs/): matching/license_semas_matches.parquet (현재 규칙), standardized/semas_entities.parquet

출력: 규칙 비교표·검토표 CSV, figure PNG, validation_report.md (모두 git 미추적)

precision은 yes/(yes+no)로 계산하고 uncertain은 억지로 나누지 않는다. 대신 uncertain을
모두 no/모두 yes로 둔 하한/상한을 함께 낸다. 표본은 점수·거리 구간별 20건 층화추출이라,
모집단 추정치는 (구간×혼잡도) 셀별 역확률 가중으로 따로 계산한다 (셀당 n이 작아 불확실성 큼).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.config import OUTPUT_DIR, REPO_ROOT
from src.data.matching import (
    COORD_RADIUS_M,
    FUZZY_THRESHOLD,
    NAME_CONTAINMENT,
    NAME_EXACT,
    NAME_OTHER,
    TIER3_CROWDED_CC,
    name_structure,
    seq_ratio,
)
from src.data.names import normalize_name

MATCH_DIR = REPO_ROOT / "outputs" / "matching"
VAL_DIR = MATCH_DIR / "validation"
LABEL_GLOB = "precision_labels_*.csv"

CC_BINS = [0, 1, 5, 20, 50, np.inf]
CC_LABELS = ["1", "2-5", "6-20", "21-50", "51+"]
T3_BANDS = [0.75, 0.8, 0.9, 1.0 + 1e-9]
T3_BAND_LABELS = ["0.75-0.8", "0.8-0.9", "0.9-1.0"]
T4_BANDS = [0.0, 10.0, 20.0, 30.0 + 1e-9]
T4_BAND_LABELS = ["0-10", "10-20", "20-30"]


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
def load_sample_with_labels(sample_path: Path, label_path: Path | None) -> pd.DataFrame:
    s = pd.read_csv(sample_path, dtype={"lic_pnu": str, "sj_pnu": str})
    s["sample_row"] = range(len(s))
    for c in ("name_score", "distance_m", "candidate_count"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    s["matched"] = s["matched"].astype(str).str.lower() == "true"
    s["tier"] = np.where(s["band"].str.startswith("tier3"), 3, 4)
    if label_path is not None and label_path.exists():
        lab = pd.read_csv(label_path, dtype=str)
        keep = ["sample_row", "firstpass_label", "error_type", "reason",
                "suggested_final_label", "evidence_needed", "labeler"]
        lab["sample_row"] = lab["sample_row"].astype(int)
        s = s.merge(lab[keep], on="sample_row", how="left")
        # 표본 행과 판정의 store_id가 어긋나면 판정을 쓰지 않는다
        chk = pd.read_csv(label_path, dtype=str)[["sample_row", "store_id"]]
        chk["sample_row"] = chk["sample_row"].astype(int)
        mism = s.merge(chk, on="sample_row", suffixes=("", "_lab"))
        if (mism["store_id"] != mism["store_id_lab"]).any():
            raise ValueError(f"판정 파일과 표본의 store_id가 어긋남: {label_path}")
    else:
        s["firstpass_label"] = pd.NA
    return s


def add_name_features(s: pd.DataFrame) -> pd.DataFrame:
    """정규화 상호, 이름 구조, 길이 차, 지점명 일치 여부."""
    def norm(v):
        return normalize_name(v)[0] if isinstance(v, str) else None

    def branch_norm(v):
        return normalize_name(v)[0] if isinstance(v, str) and v.strip() else None

    s = s.copy()
    s["lic_norm"] = s["lic_name_raw"].map(norm)
    s["sj_norm"] = s["sj_name_raw"].map(norm)
    s["sj_nb_norm"] = [
        (a + b if isinstance(b, str) and b not in a else a) if isinstance(a, str) else None
        for a, b in zip(s["sj_norm"], s["sj_branch_raw"].map(branch_norm))
    ]
    structs, lens = [], []
    for a, x, y in zip(s["lic_norm"], s["sj_norm"], s["sj_nb_norm"]):
        if not isinstance(a, str) or not isinstance(x, str):
            structs.append(None)
            lens.append(np.nan)
            continue
        best = max([c for c in (x, y) if isinstance(c, str)], key=lambda c: seq_ratio(a, c))
        structs.append(name_structure(a, best))
        lens.append(abs(len(a) - len(best)))
    s["name_structure"] = structs
    s["name_len_diff"] = lens
    lb = s["lic_branch"].map(branch_norm)
    sb = s["sj_branch_raw"].map(branch_norm)
    s["branch_exact"] = lb.notna() & sb.notna() & (lb == sb)
    s["cc_bin"] = pd.cut(s["candidate_count"], CC_BINS, labels=CC_LABELS)
    return s


# ---------------------------------------------------------------------------
# 규칙 비교
# ---------------------------------------------------------------------------
def _counts(labels: pd.Series) -> dict:
    y = int((labels == "yes").sum())
    n = int((labels == "no").sum())
    u = int((labels == "uncertain").sum())
    tot = y + n + u
    return {
        "yes": y, "no": n, "uncertain": u,
        "precision_excl_uncertain": round(y / (y + n), 3) if (y + n) else np.nan,
        "precision_lower": round(y / tot, 3) if tot else np.nan,
        "precision_upper": round((y + u) / tot, 3) if tot else np.nan,
    }


def _weighted_precision(sample: pd.DataFrame, keep: pd.Series,
                        weights: pd.Series) -> float:
    d = sample[keep & sample["firstpass_label"].isin(["yes", "no"])]
    if d.empty:
        return np.nan
    w = weights.loc[d.index]
    return round(float((w * (d["firstpass_label"] == "yes")).sum() / w.sum()), 3)


def tier3_rules() -> dict:
    """이름 → (sample/population 공용 조건 함수). 입력 df는 score/cc/branch_exact 컬럼."""
    return {
        "current: s>=0.75": lambda d: d["score"] >= 0.75,
        "s>=0.80": lambda d: d["score"] >= 0.80,
        "s>=0.85": lambda d: d["score"] >= 0.85,
        "s>=0.90": lambda d: d["score"] >= 0.90,
        "s>=0.75 & cc<=20": lambda d: (d["score"] >= 0.75) & (d["cc"] <= 20),
        "s>=0.75 & cc<=50": lambda d: (d["score"] >= 0.75) & (d["cc"] <= 50),
        "cc<=50: s>=0.75 / cc>50: s>=0.85": lambda d: (
            (d["score"] >= 0.75) & ((d["cc"] <= 50) | (d["score"] >= 0.85))),
        "cc<=50: s>=0.75 / cc>50: s>=0.90": lambda d: (
            (d["score"] >= 0.75) & ((d["cc"] <= 50) | (d["score"] >= 0.90))),
        "위 + branch exact면 cc 무관 0.75": lambda d: (
            (d["score"] >= 0.75)
            & ((d["cc"] <= 50) | (d["score"] >= 0.90) | d["branch_exact"])),
    }


def compare_tier3(sample: pd.DataFrame, pop: pd.DataFrame) -> pd.DataFrame:
    """sample: 라벨된 Tier3 표본(0.7 이상 전체), pop: 기준선 Tier3 matched."""
    s = sample[sample["tier"] == 3].copy()
    s = s.assign(score=s["name_score"], cc=s["candidate_count"])
    p = pop.assign(score=pop["match_score"], cc=pop["candidate_count"])
    # 모집단 가중치: (점수구간 × 혼잡도≤/>50) 셀별 모집단수/표본수
    def cell(df):
        band = pd.cut(df["score"], T3_BANDS, right=False, labels=T3_BAND_LABELS)
        crowd = np.where(df["cc"] > TIER3_CROWDED_CC, "cc>50", "cc<=50")
        return band.astype(str) + "|" + crowd
    s["cell"] = cell(s)
    p["cell"] = cell(p)
    pop_n = p["cell"].value_counts()
    smp_n = s.loc[s["score"] >= 0.75, "cell"].value_counts()
    w = s["cell"].map(pop_n / smp_n)

    base_keep_s = s["score"] >= 0.75
    base_keep_p = p["score"] >= 0.75
    rows = []
    for name, rule in tier3_rules().items():
        ks, kp = rule(s) & base_keep_s, rule(p) & base_keep_p
        c = _counts(s.loc[ks, "firstpass_label"])
        lost = s.loc[base_keep_s & ~ks, "firstpass_label"]
        rows.append({
            "rule": name, **c,
            "sample_retained": int(ks.sum()),
            "yes_lost_vs_current": int((lost == "yes").sum()),
            "no_removed_vs_current": int((lost == "no").sum()),
            "weighted_precision_est": _weighted_precision(s, ks, w),
            "population_tier3_retained": int(kp.sum()),
            "population_delta_vs_current": int(kp.sum() - base_keep_p.sum()),
        })
    # 참고: 0.70~0.75 구간(현재 미매칭)을 cc<=50에 한해 열면
    low = s[(s["score"] >= 0.70) & (s["score"] < 0.75)]
    lowc = low[low["cc"] <= 50]
    rows.append({
        "rule": "(참고) 0.70<=s<0.75 & cc<=50 추가 시 신규분", **_counts(lowc["firstpass_label"]),
        "sample_retained": len(lowc), "yes_lost_vs_current": 0,
        "no_removed_vs_current": 0, "weighted_precision_est": np.nan,
        "population_tier3_retained": np.nan, "population_delta_vs_current": np.nan,
    })
    return pd.DataFrame(rows)


def tier4_rules() -> dict:
    ok_struct = lambda d: d["struct"].isin([NAME_EXACT, NAME_CONTAINMENT])  # noqa: E731
    return {
        "current: d<=30 & s>=0.75": lambda d: d["dist"] <= 30,
        "d<=10": lambda d: d["dist"] <= 10,
        "d<=15": lambda d: d["dist"] <= 15,
        "d<=20": lambda d: d["dist"] <= 20,
        "d<=20 & s>=0.80": lambda d: (d["dist"] <= 20) & (d["score"] >= 0.80),
        "d<=10 any / 10-30m는 exact만": lambda d: (d["dist"] <= 10) | (
            (d["dist"] <= 30) & (d["struct"] == NAME_EXACT)),
        "d<=20 & (exact|containment)": lambda d: (d["dist"] <= 20) & ok_struct(d),
        "d<=30 & (exact|containment)  [적용]": lambda d: (d["dist"] <= 30) & ok_struct(d),
    }


def compare_tier4(sample: pd.DataFrame, pop: pd.DataFrame) -> pd.DataFrame:
    s = sample[sample["tier"] == 4].copy()
    s = s.assign(dist=s["distance_m"], score=s["name_score"], struct=s["name_structure"])
    p = pop.assign(dist=pop["distance_m"], score=pop["name_score"], struct=pop["name_structure"])
    band_s = pd.cut(s["dist"], T4_BANDS, right=False, labels=T4_BAND_LABELS)
    band_p = pd.cut(p["dist"], T4_BANDS, right=False, labels=T4_BAND_LABELS)
    w = band_s.map(band_p.value_counts() / band_s.value_counts()).astype(float)
    base_s = s["dist"] <= COORD_RADIUS_M
    base_p = p["dist"] <= COORD_RADIUS_M
    rows = []
    for name, rule in tier4_rules().items():
        ks, kp = rule(s) & base_s, rule(p) & base_p
        c = _counts(s.loc[ks, "firstpass_label"])
        lost = s.loc[base_s & ~ks, "firstpass_label"]
        rows.append({
            "rule": name, **c, "sample_retained": int(ks.sum()),
            "yes_lost_vs_current": int((lost == "yes").sum()),
            "no_removed_vs_current": int((lost == "no").sum()),
            "weighted_precision_est": _weighted_precision(s, ks, w),
            "population_tier4_retained": int(kp.sum()),
            "population_delta_vs_current": int(kp.sum() - base_p.sum()),
        })
    return pd.DataFrame(rows)


def population_tier3(base: pd.DataFrame, entities: pd.DataFrame) -> pd.DataFrame:
    """기준선 Tier3 matched + 지점명 일치 여부 (인허가 branch vs SEMAS branch)."""
    t3 = base[(base["match_tier"] == 3) & base["matched"]].merge(
        entities[["sj_store_id", "branch_norm_last"]], on="sj_store_id", how="left")
    lb = t3["branch"].map(lambda v: normalize_name(v)[0] if isinstance(v, str) else None)
    t3["branch_exact"] = [
        isinstance(a, str) and isinstance(b, str) and a == b
        for a, b in zip(lb, t3["branch_norm_last"])
    ]
    return t3


def population_tier4_structure(base: pd.DataFrame, entities: pd.DataFrame) -> pd.DataFrame:
    """기준선 Tier4 matched에 이름 구조를 붙인다 (구 결과에는 구조 컬럼이 없다)."""
    t4 = base[(base["match_tier"] == 4) & base["matched"]].merge(
        entities[["sj_store_id", "name_norm_last", "name_branch_norm_last"]],
        on="sj_store_id", how="left")
    structs = []
    for a, x, y in zip(t4["name_norm"], t4["name_norm_last"], t4["name_branch_norm_last"]):
        cands = [c for c in (x, y) if isinstance(c, str)]
        best = max(cands, key=lambda c: seq_ratio(a, c))
        structs.append(name_structure(a, best))
    t4["name_structure"] = structs
    return t4


# ---------------------------------------------------------------------------
# 검토표
# ---------------------------------------------------------------------------
def no_uncertain_review(s: pd.DataFrame, new_matches: pd.DataFrame) -> pd.DataFrame:
    r = s[s["firstpass_label"].isin(["no", "uncertain"])].copy()
    nm = new_matches.set_index("store_id")
    r["new_matched"] = r["store_id"].map(nm["matched"])
    r["new_unmatched_reason"] = r["store_id"].map(nm["unmatched_reason"])
    r["new_sj_store_id"] = r["store_id"].map(nm["sj_store_id"])
    return r.rename(columns={
        "lic_name_raw": "permit_name", "sj_name_raw": "semas_name",
        "lic_addr": "address", "lic_branch": "branch", "matched": "current_match_status",
    })[[
        "sample_row", "band", "store_id", "permit_name", "semas_name", "branch",
        "sj_branch_raw", "lic_pnu", "sj_pnu", "address", "tier", "name_score",
        "distance_m", "candidate_count", "name_structure", "current_match_status",
        "new_matched", "new_unmatched_reason", "firstpass_label", "error_type",
        "reason", "suggested_final_label", "evidence_needed",
    ]].sort_values(["error_type", "band", "sample_row"])


def sanity_review(v: pd.DataFrame) -> pd.DataFrame:
    """Tier1/2 표본의 기계적 점검: PNU 일치, 상호(+지점) 정확 일치, 후보 유일성."""
    t = v[v["group"].isin(["tier1_exact", "tier2_branch"])].copy()
    t["pnu_equal"] = t["lic_pnu"] == t["sj_pnu"]
    t["cc_unique"] = pd.to_numeric(t["candidate_count"], errors="coerce") == 1
    return t[["group", "store_id", "lic_name_raw", "lic_branch", "sj_name_raw",
              "sj_branch_raw", "lic_pnu", "sj_pnu", "match_method",
              "pnu_equal", "cc_unique"]]


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
LABEL_STYLE = {
    "yes": ("#2ca02c", "o"), "no": ("#d62728", "X"), "uncertain": ("#ff7f0e", "s"),
}


def _font():
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Malgun Gothic", "NanumGothic", "Gulim"):
        if cand in names:
            plt.rcParams["font.family"] = cand
            break
    plt.rcParams["axes.unicode_minus"] = False


def make_figures(s: pd.DataFrame, out_dir: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _font()
    paths = []

    # Figure A — Tier3: score × log(candidate_count)
    t3 = s[s["tier"] == 3]
    fig, ax = plt.subplots(figsize=(11.5, 6))
    for lab, (color, mk) in LABEL_STYLE.items():
        g = t3[t3["firstpass_label"] == lab]
        ax.scatter(g["name_score"], g["candidate_count"], c=color, marker=mk, s=70,
                   edgecolor="black", linewidth=0.5, label=f"{lab} (n={len(g)})", zorder=3)
    ax.set_yscale("log")
    ax.axvline(FUZZY_THRESHOLD, color="black", ls="--", lw=1,
               label=f"현재 threshold {FUZZY_THRESHOLD}")
    ax.axhline(TIER3_CROWDED_CC, color="gray", ls=":", lw=1.2,
               label=f"candidate_count {TIER3_CROWDED_CC} (혼잡 PNU 경계)")
    ax.set_xlabel("name_score (PNU 내 상호 유사도)")
    ax.set_ylabel("candidate_count (PNU 내 후보 entity 수, log)")
    ax.set_title("Figure A — Tier3 판정: 오매칭은 점수보다 혼잡 PNU에 몰린다\n"
                 "(0.70~0.75는 현재 미매칭 구간, 1차 판정 기준 — ground truth 아님)")
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    ax.grid(alpha=0.25)
    fig.tight_layout()
    p = out_dir / "fig_a_tier3_score_vs_candidates.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # Figure B — Tier4: distance × score, 마커 = 이름 구조
    t4 = s[s["tier"] == 4]
    fig, ax = plt.subplots(figsize=(9, 6))
    rng = np.random.default_rng(0)
    for lab, (color, _) in LABEL_STYLE.items():
        for struct, mk in ((NAME_EXACT, "o"), (NAME_CONTAINMENT, "^"), (NAME_OTHER, "X")):
            g = t4[(t4["firstpass_label"] == lab) & (t4["name_structure"] == struct)]
            if g.empty:
                continue
            jitter = rng.uniform(-0.006, 0.006, len(g))
            ax.scatter(g["distance_m"], g["name_score"] + jitter, c=color, marker=mk,
                       s=75, edgecolor="black", linewidth=0.5, zorder=3,
                       label=f"{lab} / {struct} (n={len(g)})")
    for r in (10, 20, 30):
        ax.axvline(r, color="gray", ls=":", lw=1)
    ax.axhline(FUZZY_THRESHOLD, color="black", ls="--", lw=1,
               label=f"threshold {FUZZY_THRESHOLD} (= 4자 상호 1자 치환 점수)")
    ax.set_xlabel("distance_m (인허가 좌표 ↔ SEMAS entity 좌표)")
    ax.set_ylabel("name_score")
    ax.set_title("Figure B — Tier4 판정: 오매칭은 거리가 아니라 '치환형' 이름에서 발생\n"
                 "(점선 = 반경 10/20/30m 참고선, 1차 판정 기준)")
    ax.legend(fontsize=7.5, loc="center left", bbox_to_anchor=(0.01, 0.62))
    ax.set_ylim(0.70, 1.03)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    p = out_dir / "fig_b_tier4_distance_vs_score.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)

    # Figure C — Tier3 matched: candidate_count 구간별 판정 구성
    m3 = t3[t3["name_score"] >= FUZZY_THRESHOLD]
    tab = (m3.groupby(["cc_bin", "firstpass_label"], observed=False).size()
           .unstack(fill_value=0).reindex(columns=["yes", "uncertain", "no"], fill_value=0))
    fig, ax = plt.subplots(figsize=(8, 5))
    bottom = np.zeros(len(tab))
    for lab in ("yes", "uncertain", "no"):
        ax.bar(tab.index.astype(str), tab[lab], bottom=bottom, color=LABEL_STYLE[lab][0],
               edgecolor="black", linewidth=0.5, label=lab)
        bottom += tab[lab].to_numpy()
    for i, (idx, row) in enumerate(tab.iterrows()):
        dec = row["yes"] + row["no"]
        txt = f"{row['yes'] / dec:.0%}" if dec else "-"
        ax.text(i, bottom[i] + 0.3, f"P={txt}\n(n={int(row.sum())})", ha="center", fontsize=9)
    ax.set_xlabel("candidate_count 구간")
    ax.set_ylabel("표본 수 (Tier3 matched, score ≥ 0.75)")
    ax.set_title("Figure C — Tier3 혼잡도별 precision (yes/(yes+no), uncertain 제외)")
    ax.set_ylim(0, bottom.max() + 4)
    ax.legend(fontsize=9)
    fig.tight_layout()
    p = out_dir / "fig_c_tier3_precision_by_cc.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 규칙 변경으로 바뀐 행 — 사람이 직접 검증할 수 있는 형태
# ---------------------------------------------------------------------------
def _to_wgs84(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from pyproj import Transformer

    from src.data.config import CRS_STD
    tf = Transformer.from_crs(CRS_STD, "EPSG:4326", always_xy=True)
    lon, lat = tf.transform(x, y)
    return np.asarray(lon), np.asarray(lat)


def _kakao_link(name: str, lat: float, lon: float) -> str:
    if not (np.isfinite(lat) and np.isfinite(lon)):
        return ""
    return f"https://map.kakao.com/link/map/{name},{lat:.6f},{lon:.6f}"


def changed_rows_review(
    base: pd.DataFrame, new: pd.DataFrame, ents: pd.DataFrame,
    radius: float = COORD_RADIUS_M, threshold: float = FUZZY_THRESHOLD,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """기준선 대비 매칭 결과가 바뀐 인허가 행과, 그 행의 반경 내 SEMAS 후보 전체.

    반환: (summary 1행/점포, candidates 1행/(점포×후보)). 후보 표는 Tier4가 실제로 본
    값(점수·비교 문자열·이름 구조·통과 여부)을 재계산해 보여준다. 위경도(EPSG:4326)는
    지도 확인용으로만 붙인다.
    """
    key = ["matched", "ambiguous", "match_tier", "sj_entity_id", "sj_store_id",
           "name_score", "distance_m", "best_name_norm_sj", "unmatched_reason"]
    o = base.set_index("store_id")[key]
    n = new.set_index("store_id")[key + ["name_structure"]]
    changed = (
        (o["matched"] != n["matched"]) | (o["ambiguous"] != n["ambiguous"])
        | (o["sj_entity_id"].fillna("") != n["sj_entity_id"].fillna(""))
    )
    ids = o.index[changed.reindex(o.index, fill_value=False)]
    lic = new.set_index("store_id").loc[ids]
    ent_by_id = ents.set_index("sj_store_id")

    def ent_name(sid):
        return ent_by_id.at[sid, "name_raw_last"] if isinstance(sid, str) and sid in ent_by_id.index else None

    def status(r):
        if r["matched"]:
            return "matched"
        return "ambiguous" if r["ambiguous"] else "unmatched"

    plon, plat = _to_wgs84(lic["x_5179"].to_numpy(float), lic["y_5179"].to_numpy(float))
    summary = pd.DataFrame({
        "store_id": ids,
        "business_type": lic["business_type"].to_numpy(),
        "status_name": lic["status_name"].to_numpy(),
        "permit_name": lic["name_raw"].to_numpy(),
        "permit_name_norm": lic["name_norm"].to_numpy(),
        "permit_addr": lic["addr_raw"].to_numpy(),
        "permit_road_addr": lic["road_addr_raw"].to_numpy(),
        "permit_pnu": lic["pnu"].to_numpy(),
        "license_date": lic["license_date"].to_numpy(),
        "close_date": lic["close_date"].to_numpy(),
        "old_status": [status(o.loc[i]) for i in ids],
        "old_sj_name": [ent_name(o.at[i, "sj_store_id"]) or o.at[i, "best_name_norm_sj"]
                        for i in ids],
        "old_name_score": o.loc[ids, "name_score"].to_numpy(),
        "old_distance_m": o.loc[ids, "distance_m"].to_numpy(),
        "new_status": [status(n.loc[i]) for i in ids],
        "new_sj_name": [ent_name(n.at[i, "sj_store_id"]) or n.at[i, "best_name_norm_sj"]
                        for i in ids],
        "new_name_score": n.loc[ids, "name_score"].to_numpy(),
        "new_distance_m": n.loc[ids, "distance_m"].to_numpy(),
        "new_name_structure": n.loc[ids, "name_structure"].to_numpy(),
        "new_unmatched_reason": n.loc[ids, "unmatched_reason"].to_numpy(),
        "permit_lat": plat, "permit_lon": plon,
    })
    summary["change"] = summary["old_status"] + " → " + summary["new_status"]
    summary["permit_map"] = [_kakao_link(nm, la, lo) for nm, la, lo in
                             zip(summary["permit_name"], plat, plon)]
    summary["your_label"] = ""
    summary["your_note"] = ""

    # 반경 내 후보 전체 (matching.match_coord_tier와 같은 점수·구조 계산)
    ev = ents.dropna(subset=["x_5179", "y_5179", "name_norm_last"]).reset_index(drop=True)
    ex, ey = ev["x_5179"].to_numpy(float), ev["y_5179"].to_numpy(float)
    elon, elat = _to_wgs84(ex, ey)
    rows = []
    for r, px, py in zip(summary.itertuples(index=False),
                         lic["x_5179"].to_numpy(float), lic["y_5179"].to_numpy(float)):
        if not (np.isfinite(px) and np.isfinite(py)):
            continue
        d = np.hypot(ex - px, ey - py)
        for i in np.flatnonzero(d <= radius):
            e = ev.iloc[i]
            cmp_str = e["name_norm_last"]
            s = seq_ratio(r.permit_name_norm, cmp_str)
            nb = e["name_branch_norm_last"]
            if isinstance(nb, str) and nb != e["name_norm_last"]:
                s_nb = seq_ratio(r.permit_name_norm, nb)
                if s_nb > s:
                    s, cmp_str = s_nb, nb
            struct = name_structure(r.permit_name_norm, cmp_str)
            rows.append({
                "store_id": r.store_id, "permit_name_norm": r.permit_name_norm,
                "change": r.change,
                "cand_sj_entity_id": e["sj_entity_id"], "cand_sj_store_id": e["sj_store_id"],
                "cand_name": e["name_raw_last"], "cand_branch": e["branch_raw_last"],
                "compared_string": cmp_str, "name_score": round(s, 4),
                "name_structure": struct, "distance_m": round(float(d[i]), 2),
                "passes_score": s >= threshold,
                "passes_structure": struct != NAME_OTHER,
                "was_old_pick": e["sj_entity_id"] == o.at[r.store_id, "sj_entity_id"],
                "is_new_pick": e["sj_entity_id"] == n.at[r.store_id, "sj_entity_id"],
                "cand_addr": e["addr_raw_last"], "cand_pnu": e["pnu_last"],
                "cand_category": e["cat3_name_last"],
                "cand_first_snapshot": e["entity_first_snapshot"],
                "cand_last_snapshot": e["entity_last_snapshot"],
                "cand_map": _kakao_link(e["name_raw_last"], elat[i], elon[i]),
            })
    cands = pd.DataFrame(rows)
    if len(cands):
        cands = cands.sort_values(["store_id", "passes_score", "distance_m"],
                                  ascending=[True, False, True])
    return summary.sort_values(["change", "store_id"]), cands


# ---------------------------------------------------------------------------
# 전체 영향·provenance QA
# ---------------------------------------------------------------------------
def summarize_matches(d: pd.DataFrame) -> dict:
    r = {"rows": len(d), "matched": int(d["matched"].sum()),
         "match_rate": round(float(d["matched"].mean()), 4),
         "ambiguous": int(d["ambiguous"].sum())}
    for t in (1, 2, 3, 4):
        r[f"tier{t}"] = int(((d["match_tier"] == t) & d["matched"]).sum())
    for st, g in d.groupby("status_name"):
        r[f"rate_{st}"] = round(float(g["matched"].mean()), 4)
    rc = d[(d["business_type"] == "일반음식점") & (d["close_date"] >= "2025-01-01")
           & (d["close_date"] <= "2026-06-30")]
    r["rate_recent_closed_GR_2501_2606"] = round(float(rc["matched"].mean()), 4)
    return r


def reissue_provenance_qa(ents: pd.DataFrame, links: pd.DataFrame) -> dict:
    eid = ents.set_index("sj_store_id")["sj_entity_id"]
    unamb, amb = links[~links["ambiguous"]], links[links["ambiguous"]]
    return {
        "original_ids": len(ents),
        "canonical_entities": int(ents["sj_entity_id"].nunique()),
        "merged_ids(entity!=self)": int((ents["sj_entity_id"] != ents["sj_store_id"]).sum()),
        "id_reissued_entities": int(ents.loc[ents["id_reissued"], "sj_entity_id"].nunique()),
        "id_reissued_member_ids": int(ents["id_reissued"].sum()),
        "unambiguous_links_same_entity": int(
            (unamb["old_id"].map(eid) == unamb["new_id"].map(eid)).sum()),
        "unambiguous_links_total": len(unamb),
        "ambiguous_links_same_entity(0 정상)": int(
            (amb["old_id"].map(eid) == amb["new_id"].map(eid)).sum()),
        "ambiguous_links_total": len(amb),
        "ids_with_last!=entity_last(재발급으로 ID만 소멸)": int(
            (ents["last_snapshot"] != ents["entity_last_snapshot"]).sum()),
        "entities_coord_from_202503(0 정상)": int((ents["coord_snapshot"] == "202503").sum()),
    }


def period_overlap_qa(matches: pd.DataFrame, ents: pd.DataFrame,
                      asof: str = "2026-09-19") -> pd.DataFrame:
    """인허가 영업기간 × SEMAS entity 관측구간 겹침 (B-3 판단용 참고치, ER에는 미적용)."""
    ent = ents.drop_duplicates("sj_entity_id").set_index("sj_entity_id")[
        ["entity_first_snapshot", "entity_last_snapshot"]]
    m = matches[matches["matched"]].join(ent, on="sj_entity_id")
    ws = pd.to_datetime(m["entity_first_snapshot"], format="%Y%m")
    we = pd.to_datetime(m["entity_last_snapshot"], format="%Y%m") + pd.offsets.MonthEnd(0)
    pe = m["close_date"].fillna(pd.Timestamp(asof))
    ov = (pd.concat([pe, we], axis=1).min(axis=1)
          - pd.concat([m["license_date"], ws], axis=1).max(axis=1)).dt.days
    m = m.assign(overlap_ge_90d=ov >= 90)
    return pd.crosstab([m["status_name"]], m["overlap_ge_90d"], margins=True)


def _md(df: pd.DataFrame, index: bool = True) -> str:
    """DataFrame → markdown 표 (tabulate 의존 없이)."""
    d = df.reset_index() if index else df
    cols = [str(c) for c in d.columns]
    def fmt(v) -> str:
        if isinstance(v, (float, np.floating)):
            if np.isnan(v):
                return ""
            return f"{int(v):,}" if float(v).is_integer() else f"{v:.4g}"
        if isinstance(v, (int, np.integer)):
            return f"{v:,}"
        return str(v).replace("|", r"\|")
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |"
              for row in d.itertuples(index=False)]
    return "\n".join(lines)


def write_report(res: dict, base: pd.DataFrame, new: pd.DataFrame,
                 ents: pd.DataFrame, links: pd.DataFrame, label_path: Path | None) -> Path:
    s = res["sample"]
    L = ["# ER 매칭 규칙 validation 리포트", ""]
    L.append("생성: `python -m src.data.matching_validation` — 모든 수치는 현재 파일에서 재계산.")
    L.append(f"- 판정 파일: `{label_path.name if label_path else '없음'}`")
    L.append("- 판정은 1차 screening이며 ground truth가 아니다. precision = yes/(yes+no), "
             "uncertain은 제외하고 하한/상한을 따로 표기한다.")
    L.append("")
    lab = s.groupby(["band", "firstpass_label"]).size().unstack(fill_value=0)
    L.append("## 1차 판정 구성 (고정 표본 v1, 140건)")
    L.append("")
    L.append(_md(lab.reindex(columns=["yes", "no", "uncertain"], fill_value=0)))
    L.append("")
    for key, title in (("tier3", "Tier3 규칙 비교"), ("tier4", "Tier4 규칙 비교")):
        L.append(f"## {title}")
        L.append("")
        L.append(_md(res[key], index=False))
        L.append("")
    m3 = s[(s["tier"] == 3) & (s["name_score"] >= FUZZY_THRESHOLD)]
    L.append("## Tier3 matched: candidate_count 구간별 판정")
    L.append("")
    L.append(_md(pd.crosstab(m3["cc_bin"], m3["firstpass_label"])))
    L.append("")
    t4 = s[s["tier"] == 4]
    L.append("## Tier4: 이름 구조별 판정")
    L.append("")
    L.append(_md(pd.crosstab(t4["name_structure"], t4["firstpass_label"])))
    L.append("")
    L.append("## no/uncertain 유형")
    L.append("")
    L.append(_md(pd.crosstab(res["review"]["error_type"],
                             res["review"]["firstpass_label"])))
    L.append("")
    san = res["sanity"]
    L.append("## Tier1/2 sanity (56건 중 exact 16건)")
    L.append("")
    L.append(f"- PNU 일치 {int(san['pnu_equal'].sum())}/{len(san)}, "
             f"후보 유일(cc=1) {int(san['cc_unique'].sum())}/{len(san)}")
    L.append("")
    L.append("## 규칙 적용 전후 전체 영향")
    L.append("")
    comp = pd.DataFrame({"old": summarize_matches(base), "new": summarize_matches(new)})
    comp["delta"] = comp["new"] - comp["old"]
    comp = comp.round(4)
    L.append(_md(comp))
    L.append("")
    chg = res.get("changed")
    if chg is not None and len(chg):
        L.append(f"## 규칙 변경으로 바뀐 행 ({len(chg)}건)")
        L.append("")
        L.append("후보 전체는 `changed_rows_candidates.csv`, 판정 기입란은 `changed_rows_summary.csv`.")
        L.append("")
        L.append(_md(chg[["change", "permit_name", "old_sj_name", "old_distance_m",
                          "new_sj_name", "new_distance_m", "new_name_structure"]],
                     index=False))
        L.append("")
    L.append("## reissue provenance")
    L.append("")
    for k, v in reissue_provenance_qa(ents, links).items():
        L.append(f"- {k}: {v:,}")
    L.append("")
    L.append("## (참고) 인허가 영업기간 × entity 관측구간 겹침 ≥ 90일")
    L.append("")
    L.append("ER에는 적용하지 않은 조건이다. B-3 sj_status 파생 시 판단 근거로만 제공한다.")
    L.append("")
    L.append(_md(period_overlap_qa(new, ents)))
    L.append("")
    path = VAL_DIR / "validation_report.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _find_labels() -> Path | None:
    files = sorted(VAL_DIR.glob(LABEL_GLOB))
    return files[-1] if files else None


def run() -> dict:
    VAL_DIR.mkdir(parents=True, exist_ok=True)
    label_path = _find_labels()
    s = load_sample_with_labels(VAL_DIR / "match_precision_sample_v1.csv", label_path)
    s = add_name_features(s)

    base = pd.read_parquet(VAL_DIR / "license_semas_matches_baseline.parquet")
    new = pd.read_parquet(MATCH_DIR / "license_semas_matches.parquet")
    ents = pd.read_parquet(OUTPUT_DIR / "semas_entities.parquet")

    pop3 = population_tier3(base, ents)
    pop4 = population_tier4_structure(base, ents)

    t3 = compare_tier3(s, pop3)
    t4 = compare_tier4(s, pop4)
    rev = no_uncertain_review(s, new)
    san = sanity_review(pd.read_csv(VAL_DIR / "match_validation_sample_v1.csv", dtype=str))

    t3.to_csv(VAL_DIR / "tier3_rule_comparison.csv", index=False, encoding="utf-8-sig")
    t4.to_csv(VAL_DIR / "tier4_rule_comparison.csv", index=False, encoding="utf-8-sig")
    rev.to_csv(VAL_DIR / "no_uncertain_review.csv", index=False, encoding="utf-8-sig")
    san.to_csv(VAL_DIR / "tier12_sanity_review.csv", index=False, encoding="utf-8-sig")
    s.to_csv(VAL_DIR / "precision_sample_labeled_features.csv", index=False,
             encoding="utf-8-sig")
    figs = make_figures(s, VAL_DIR)
    print(f"labels: {label_path}")
    for f in figs:
        print(f"figure: {f}")
    chg_sum, chg_cand = changed_rows_review(base, new, ents)
    chg_sum.to_csv(VAL_DIR / "changed_rows_summary.csv", index=False, encoding="utf-8-sig")
    chg_cand.to_csv(VAL_DIR / "changed_rows_candidates.csv", index=False,
                    encoding="utf-8-sig")
    print(f"changed rows: {len(chg_sum)} -> changed_rows_summary.csv / "
          f"changed_rows_candidates.csv ({len(chg_cand)} 후보)")
    res = {"tier3": t3, "tier4": t4, "review": rev, "sanity": san, "sample": s,
           "pop4": pop4, "changed": chg_sum}
    links = pd.read_parquet(OUTPUT_DIR / "semas_id_links.parquet")
    rep = write_report(res, base, new, ents, links, label_path)
    print(f"report: {rep}")
    return res


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
