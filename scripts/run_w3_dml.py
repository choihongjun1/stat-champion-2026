"""W3-6 DML 실행 스크립트 (전자상거래 매출실적 -> 영업이익률, IRM·ATE).

로직은 src/prescribe/dml_ecommerce.py. 이 스크립트는 입력 읽기, 두 표본 실행, 결과 파일 쓰기만 한다.
입력: outputs/mdis/mdis_stage_a.parquet (없으면 scripts/run_mdis_stage_a.py 먼저)
출력(gitignore): outputs/prescribe/dml_results.json, dml_results.md, figures/dml_overlap.png
실행: python scripts/run_w3_dml.py (프로젝트 루트에서)
"""

import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import doubleml  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.prescribe import dml_ecommerce as de  # noqa: E402

INPUT = REPO_ROOT / "outputs" / "mdis" / "mdis_stage_a.parquet"
OUT_DIR = REPO_ROOT / "outputs" / "prescribe"
ANALYSIS_LABELS = {
    "1_main": "(1) 주: 무가중 HGB, truncate 0.05",
    "2_weighted": "(2) 가중 민감도",
    "3_excluded": "(3) 행 제외 민감도",
    "4_simple": "(4) 단순 학습기",
    "5_ovb": "(5) OVB 민감도",
    "6_seoul": "(6) 서울 부분집합(부호만)",
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_provenance() -> dict:
    """결과를 만든 코드의 commit SHA와 작업트리 dirty 여부(추적 파일 변경 + 신규 파일)."""

    def git(*a):
        return subprocess.run(["git", "-C", str(REPO_ROOT), *a], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()

    return {"commit": git("rev-parse", "HEAD") or "unknown", "dirty": bool(git("status", "--porcelain"))}


def env_versions() -> dict:
    import sklearn

    return {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "scikit-learn": sklearn.__version__, "doubleml": doubleml.__version__}


def fmt(v, nd=4):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{nd}f}"


def overlap_figure(results, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["font.family"] = ["Malgun Gothic", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, len(results), figsize=(5.5 * len(results), 4), sharey=False)
    for ax, r in zip(np.atleast_1d(axes), results):
        ps, d = r["_ps_main"], r["_d"]
        bins = np.linspace(0, 1, 41)
        ax.hist(ps[d == 0], bins=bins, alpha=0.55, label="비처치", density=True)
        ax.hist(ps[d == 1], bins=bins, alpha=0.55, label="처치", density=True)
        for t in (de.TRIM, 1 - de.TRIM):
            ax.axvline(t, color="k", ls="--", lw=0.8)
        ax.set_title(f"{r['sample']} (N={r['n']}, 처치 {r['n_treated']})")
        ax.set_xlabel("성향점수(반복 평균)")
        ax.legend()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def results_markdown(results, meta) -> str:
    L = ["# W3-6 DML 결과 (집계값만)", ""]
    L.append(f"- 입력 sha256: {meta['input_sha256']} / 행 수 {meta['input_rows']}")
    L.append(f"- doubleml {meta['doubleml']}, 실행 {meta['started']}, 소요 {meta['runtime_sec']:.0f}초")
    v = meta["versions"]
    L.append(f"- 코드 commit {meta['code']['commit'][:12]} (작업트리 dirty: {'예' if meta['code']['dirty'] else '아니오'}), "
             f"python {v['python']}, numpy {v['numpy']}, pandas {v['pandas']}, scikit-learn {v['scikit-learn']}, doubleml {v['doubleml']}")
    L.append("- 처치 treat_binary, 결과 profit_margin(표본별 1%·99% clip), IRM ATE, n_folds 5, n_rep 3, truncate 0.05, seed 20261001")
    L.append("- clip 건수 = 절단 전 교차적합 성향점수(반복 평균)가 [하한 <0.05 / 상한 >0.95]인 행 수(같은 표본 분할로 ml_m 재적합; DoubleML 저장값과의 최대 차이는 JSON의 ps_refit_max_abs_diff). 유효 표본 = 절단 IPW의 Kish 유효 표본")
    L.append("")
    L.append("| 표본 | 분석 | N | 처치 N | ATE | SE | 95% CI | CI의 0 포함 | clip 건수(하/상) | 제외 건수 | 유효 표본 | 판정 문구 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        for key, label in ANALYSIS_LABELS.items():
            a = r["analyses"][key]
            if "status" in a:
                L.append(f"| {r['sample']} | {label} | — | — | — | — | — | — | — | — | — | {a['status']} ({a['error'][:60]}) |")
                continue
            seoul = key == "6_seoul"
            ci = f"[{fmt(a['ci_low'])}, {fmt(a['ci_high'])}]"
            L.append(
                f"| {r['sample']} | {label} | {a['n']} | {a['n_treated']} | "
                f"{('부호 ' + a['sign']) if seoul else fmt(a['ate'])} | {'—' if seoul else fmt(a['se'])} | {'—' if seoul else ci} | "
                f"{'예' if a['ci_contains_zero'] else '아니오'} | "
                f"{a.get('n_clip_lower', '—')}/{a.get('n_clip_upper', '—')} | {a.get('n_excluded', '—')} | {fmt(a.get('kish_ess'), 1)} | "
                f"{de.verdict(a['ci_contains_zero'])} |"
            )
    L += ["", "## 결과변수 clip (표본별 무가중 분위수)", "", "| 표본 | 하한(1%) | 상한(99%) | 하한 아래 건수 | 상한 위 건수 |", "|---|---|---|---|---|"]
    for r in results:
        c = r["clip"]
        L.append(f"| {r['sample']} | {fmt(c['lower'])} | {fmt(c['upper'])} | {c['n_clipped_lower']} | {c['n_clipped_upper']} |")
    L += ["", "## OVB 민감도 (S6-A)", ""]
    for r in results:
        a = r["analyses"]["5_ovb"]
        if "status" in a:
            L.append(f"- {r['sample']}: {a['status']} ({a['error']})")
            continue
        s = a["sensitivity"]
        L.append(f"- {r['sample']}: cf_y={s['cf_y']}, cf_d={s['cf_d']}, rho={s['rho']}; RV={fmt(s['rv'][0])}, RVa(95%)={fmt(s['rva'][0])}; "
                 f"theta 하한/상한 {[fmt(v) for v in s['theta_bounds'].get('lower', [])]} / {[fmt(v) for v in s['theta_bounds'].get('upper', [])]}")
        for name, b in s["benchmarks"].items():
            if "error" in b:
                L.append(f"  - 벤치마크 {name}: 실패 ({b['error'][:100]})")
            else:
                L.append(f"  - 벤치마크 {name} ({len(b['columns'])}열): " + ", ".join(f"{k}={fmt(v)}" for k, v in b.items() if k != "columns"))
    L += ["", "## 진단", "", "| 표본 | 성향 min | max | p05 | p25 | p50 | p75 | p95 | 처치군 비율 | Kish(전체/처치/비처치) | SMD 최대 |절대값|(전→후) | SMD>0.1 개수(전→후) |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        g, q = r["diagnostics"], r["diagnostics"]["ps_quantiles"]
        smd = pd.DataFrame(r["smd"])
        L.append(
            f"| {r['sample']} | {fmt(g['ps_min'])} | {fmt(g['ps_max'])} | {fmt(q['p05'])} | {fmt(q['p25'])} | {fmt(q['p50'])} | {fmt(q['p75'])} | {fmt(q['p95'])} | "
            f"{fmt(g['treated_share'], 3)} | {g['kish_ess']:.1f}/{g['kish_ess_treated']:.1f}/{g['kish_ess_control']:.1f} | "
            f"{smd['smd_before'][np.isfinite(smd['smd_before'])].abs().max():.3f} → {smd['smd_after'][np.isfinite(smd['smd_after'])].abs().max():.3f} | "
            f"{int((smd['smd_before'].abs() > 0.1).sum())} → {int((smd['smd_after'].abs() > 0.1).sum())} (정의 불가 {int(smd['smd_undefined'].sum())}) |"
        )
    return "\n".join(L) + "\n"


def gate_table(results) -> str:
    L = ["## A1 게이트 (확인됨/미확인, 해석·권고 없음)", "", "| 항목 | " + " | ".join(r["sample"] for r in results) + " |", "|---|" + "---|" * len(results)]

    def cell(fn):
        return " | ".join(fn(r) for r in results)

    def ci(r):
        a = r["analyses"]["1_main"]
        return f"확인됨 (CI의 0 포함: {'예' if a['ci_contains_zero'] else '아니오'})"

    def overlap(r):
        g = r["diagnostics"]
        return f"미확인 (기준 TBD; 성향 {g['ps_min']:.3f}~{g['ps_max']:.3f}, clip {g['n_clip_lower']}/{g['n_clip_upper']})"

    def direction(r):
        a, b = r["analyses"]["1_main"], r["analyses"]["4_simple"]
        if "status" in b:
            return "미확인 (단순 학습기 적합 불가)"
        same = np.sign(a["ate"]) == np.sign(b["ate"])
        return f"{'확인됨' if same else '미확인'} (주 {np.sign(a['ate']):+.0f} / 단순 {np.sign(b['ate']):+.0f})"

    L.append("| CI | " + cell(ci) + " |")
    L.append("| 겹침 | " + cell(overlap) + " |")
    L.append("| 학습기 방향 일치 | " + cell(direction) + " |")
    L.append("| 공변량 시점 | " + cell(lambda r: "미확인 (조사 기준 시점 문구 없음, 작업 C2)") + " |")
    L.append("| 표본 정의 | " + cell(lambda r: "확인됨 (#49 S2-A 코멘트에 등록)") + " |")
    return "\n".join(L) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔에서 '—' 출력 실패 방지
    t0 = time.time()
    if not INPUT.exists():
        sys.exit(f"입력 없음: {INPUT} — 먼저 python scripts/run_mdis_stage_a.py")
    df = pd.read_parquet(INPUT)
    meta = {
        "input": str(INPUT.relative_to(REPO_ROOT)),
        "input_sha256": sha256_of(INPUT),
        "input_rows": int(len(df)),
        "doubleml": doubleml.__version__,
        "code": git_provenance(),
        "versions": env_versions(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(f"입력 {meta['input_rows']}행, sha256 {meta['input_sha256'][:16]}…, doubleml {meta['doubleml']}")
    samples = [
        ("56 주", df[df["산업중분류코드"].astype(str) == "56"], False, {"tenure_months": None, "행정구역시도코드": None}),
        ("전체 보조", df, True, {"tenure_months": None, "산업중분류코드": None}),
    ]
    results = []
    for name, sub, incl, bm in samples:
        t1 = time.time()
        r = de.run_sample(sub.reset_index(drop=True), name, incl, bm)
        r["runtime_sec"] = time.time() - t1
        print(f"[{name}] N={r['n']} 처치={r['n_treated']} 소요 {r['runtime_sec']:.0f}초")
        results.append(r)
    meta["runtime_sec"] = time.time() - t0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    overlap_figure(results, OUT_DIR / "figures" / "dml_overlap.png")
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    (OUT_DIR / "dml_results.json").write_text(json.dumps({"meta": meta, "results": clean}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = results_markdown(results, meta) + "\n" + gate_table(results)
    (OUT_DIR / "dml_results.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
