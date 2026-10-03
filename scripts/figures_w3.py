"""W3 report figures, saved artifacts only; no fitting or bootstrap.

F1: train_detect.py:176-182,429,438 -> origin/auc/feature_set CSV;
     adopted and DEFAULT are separate runs, not '현 설정' vs '튜닝'.
F2: bands.py:138-159 -> band/n/obs_rate (no observed-rate CI);
     train_detect.py:518-532 -> band_provenance, high lift CI only.
F3: PR52 scripts/run_w3_dml.py:195-209 -> results/sample/analyses,
     ate/ci_low/ci_high. Read format only; never import PR52 code.
F4: that producer removes _ps_main/_d at :208; no persisted scores.
F5: shapley_background_stability.py:96-110,215 -> summary.csv has
     candidate agreement, not per-factor S8 fractions. Explicit saved-column
     mappings are required for optional future CI/score/S8 artifacts.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager, pyplot as plt
from matplotlib.text import Text
from matplotlib.ticker import FuncFormatter
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = [f"{year}Q{q}" for year in (2023, 2024, 2025) for q in range(1, 5) if year < 2025 or q <= 2]
ROUNDING = "Decimal(str(value)), ROUND_HALF_UP, 소수 셋째 자리"


class MissingInput(ValueError):
    pass


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite stored value")
    return result


def rounded(value):
    return str(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


FONT_ORDER = ("Noto Sans CJK KR", "Noto Sans KR", "Noto Sans CJK", "Malgun Gothic")


def font(path=None):
    """--font-file이 있으면 그 파일, 없으면 FONT_ORDER 순서로 설치된 글꼴을 찾는다.
    "Noto Sans CJK"는 지역판(JP/SC/TC/KR)과 .ttc 첫 이름을 모두 포함한다. 한글 포함 여부는 그림을 그린 뒤 cmap으로 검사한다."""
    if path is None:
        entries = font_manager.fontManager.ttflist
        for wanted in FONT_ORDER:
            for entry in entries:
                name = entry.name
                if (name == wanted or (wanted == "Noto Sans CJK" and name.startswith("Noto Sans CJK"))) and Path(entry.fname).is_file():
                    path = entry.fname
                    break
            if path is not None:
                break
        if path is None:
            raise MissingInput("CJK 글꼴 없음: " + ", ".join(FONT_ORDER))
    if not Path(path).is_file():
        raise MissingInput("글꼴 파일 없음")
    return font_manager.FontProperties(fname=str(path))


def missing_glyphs(font_path, strings):
    """그림에 들어간 문자 중 글꼴 cmap에 없는 것 (공백·제어 문자 제외). .ttc는 첫 글꼴(fontNumber=0)."""
    from fontTools.ttLib import TTFont
    ttf = TTFont(str(font_path), fontNumber=0, lazy=True)
    try:
        cmap = ttf.getBestCmap() or {}
    finally:
        ttf.close()
    chars = {ch for text in strings for ch in text if not ch.isspace() and ord(ch) >= 32}
    return sorted(ch for ch in chars if ord(ch) not in cmap)


def load_csv(path):
    if path is None or not path.is_file():
        raise MissingInput("입력 없음: 저장 CSV 없음")
    return pd.read_csv(path)


def load_json(path):
    if path is None or not path.is_file():
        raise MissingInput("입력 없음: 저장 JSON 없음")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require(frame, columns, reason):
    if any(col is None or col not in frame for col in columns):
        raise MissingInput("입력 없음: " + reason)


def f1(args, ax):
    values = []
    for path, label, marker, style, mean_style in ((args.adopted_metrics, args.adopted_label, "o", "-", ":"),
                                                   (args.previous_metrics, args.previous_label, "s", "--", "-.")):
        data = load_csv(path)
        require(data, ["origin", "auc", "feature_set"], "origin/auc/feature_set 열 없음")
        data = data[data.feature_set.eq(args.feature_set) & data.origin.isin(ORIGINS)]
        if len(data) != 10 or data.origin.duplicated().any() or set(data.origin) != set(ORIGINS):
            raise MissingInput("입력 없음: 모형별 10-origin 지표가 필요")
        data = data.set_index("origin").loc[ORIGINS]
        auc = [number(v) for v in data.auc]
        mean = sum(auc) / 10
        line = ax.plot(range(10), auc, marker=marker, ls=style, label=label)[0]
        ax.axhline(mean, ls=mean_style, color=line.get_color(), label=label + " 평균 " + rounded(mean))
        ci = None
        if args.auc_ci_low_col or args.auc_ci_high_col:
            require(data, [args.auc_ci_low_col, args.auc_ci_high_col], "지정된 저장 origin CI 열 없음")
            ci = [[number(lo), number(hi)] for lo, hi in zip(data[args.auc_ci_low_col], data[args.auc_ci_high_col])]
            for x, (lo, hi) in enumerate(ci):
                if lo > hi:
                    raise ValueError("reversed CI")
                ax.vlines(x, lo, hi, linewidth=1)
        values.append({"label": label, "origins": ORIGINS, "auc": auc, "mean10": mean, "ci95": ci})
    ax.set_xticks(range(10), ORIGINS, rotation=45)
    ax.set_xlabel("origin")
    ax.set_ylabel("AUC")
    ax.legend()
    return {"series": values, "note": "origin별 CI 미지정 시 오차막대 없음"}, [args.adopted_metrics, args.previous_metrics]


def f2(args, ax):
    data = load_csv(args.band_profile)
    with_ci = bool(args.band_ci_low_col or args.band_ci_high_col)
    if with_ci:
        require(data, ["band", "n", "obs_rate", args.band_ci_low_col, args.band_ci_high_col], "지정된 저장 등급별 CI 열 없음; high lift CI로 대체하지 않음")
    else:
        require(data, ["band", "n", "obs_rate"], "band/n/obs_rate 열 없음")
    if data.band.duplicated().any() or not {"low", "mid", "high"} <= set(data.band):
        raise ValueError("invalid bands")
    data = data.set_index("band").loc[["low", "mid", "high"]]
    meta = load_json(args.run_meta)
    # provenance.base_rate is the calibration-window rate, not the test rate.
    counts = [number(v) for v in data.n]
    if any(n < 0 for n in counts) or sum(counts) == 0:
        raise MissingInput("입력 없음: 등급별 유효 N 없음")
    base = sum(n * number(rate) for n, rate in zip(counts, data.obs_rate)) / sum(counts)
    rates = [number(v) for v in data.obs_rate]
    ci = [[number(lo), number(hi)] for lo, hi in zip(data[args.band_ci_low_col], data[args.band_ci_high_col])] if with_ci else None
    bars = ax.bar(range(3), rates, color=["#dddddd", "#999999", "#555555"], edgecolor="black")
    for x, bar, hatch in zip(range(3), bars, ("/", "\\", "xx")):
        bar.set_hatch(hatch)
        if ci is not None:
            lo, hi = ci[x]
            if lo > hi:
                raise ValueError("reversed CI")
            ax.vlines(x, lo, hi, color="black")
            ax.plot([x-.08, x+.08], [lo, lo], color="black")
            ax.plot([x-.08, x+.08], [hi, hi], color="black")
    ax.axhline(base, color="black", ls="--", label="전체 평균 " + rounded(base))
    paths = [args.band_profile, args.run_meta]
    gate_allowed = False
    if args.s10_gate and args.s10_gate.is_file():
        gate = load_json(args.s10_gate)
        paths.append(args.s10_gate)
        gate_allowed = gate.get("decision") == "allowed"
        # A stored gate from a different run cannot authorize this figure.
        if gate.get("input_sha256") and gate["input_sha256"] != hashlib.sha256(args.run_meta.read_bytes()).hexdigest():
            gate_allowed = False
        if gate_allowed:
            ax.annotate("약 2배", (2, rates[2]), xytext=(0, 12), textcoords="offset points", ha="center")
    ax.set_xticks(range(3), ["낮음", "주의", "높음"])
    ax.set_ylabel("관측 폐업 비율")
    ax.legend()
    return {"bands": ["low", "mid", "high"], "n": counts, "obs_rate": rates, "ci95": ci, "overall_mean": base, "overall_mean_definition": "저장 test 등급별 n × obs_rate 합 / n 합", "s10_allowed": gate_allowed,
            "note": None if ci is not None else "등급별 CI 미저장 — 오차막대 없음"}, paths


def f3(args, ax):
    payload = load_json(args.dml)
    rows = []
    labels = {"1_main": "주 분석", "2_weighted": "가중 민감도", "3_excluded": "행 제외 민감도", "4_simple": "단순 학습기"}
    for sample, prefix in (("56 주", "음식점(56)"), ("전체 보조", "전체 업종 보조")):
        matches = [r for r in payload["results"] if r["sample"] == sample]
        if len(matches) != 1:
            raise ValueError("sample must match once")
        for key, label in labels.items():
            row = matches[0]["analyses"].get(key)
            if row is None or "status" in row:
                continue
            low, high = number(row["ci_low"]), number(row["ci_high"])
            if low > high:
                raise ValueError("reversed CI")
            rows.append({"label": prefix + " · " + label, "sample": sample, "analysis": key,
                         "ate": number(row["ate"]), "ci95": [low, high]})
    if not rows:
        raise MissingInput("입력 없음: 저장 추정치·CI 없음")
    for y, row in enumerate(rows):
        ax.hlines(y, *row["ci95"], color="black")
        ax.plot(row["ate"], y, "o" if row["sample"] == "56 주" else "s", color="black")
    split = sum(row["sample"] == "56 주" for row in rows)
    if 0 < split < len(rows):
        ax.axhline(split-.5, color="gray", ls="--")
    ax.axvline(0, color="black", ls=":")
    ax.set_yticks(range(len(rows)), [row["label"] for row in rows])
    ax.invert_yaxis()
    ax.set_xlabel("조건부 연관성 추정치 (95% CI)")
    return {"rows": rows, "zero_line": 0, "note": "OVB 경계는 추론 CI와 별도이며, 서울 부분집합은 부호만 보고하므로 이 그림에서 제외"}, [args.dml]


def f4(args, ax):
    if args.propensity is None:
        raise MissingInput("입력 없음: DML JSON은 _ps_main/_d를 제외하여 성향점수를 저장하지 않음")
    data = load_csv(args.propensity)
    require(data, [args.ps_col, args.treat_col], "저장 성향점수·처치 열을 명시해야 함")
    ps = pd.to_numeric(data[args.ps_col], errors="raise")
    d = pd.to_numeric(data[args.treat_col], errors="raise")
    if ps.isna().any() or not ps.between(0, 1).all() or d.isna().any() or not d.isin([0, 1]).all():
        raise ValueError("invalid saved scores")
    histograms = []
    bins = [index / 40 for index in range(41)]
    for value, label, hatch in ((0, "비처치", "/"), (1, "처치", "\\")):
        group = ps[d.eq(value)]
        if group.empty:
            raise MissingInput("입력 없음: 두 처치 집단 필요")
        heights, edges, _ = ax.hist(group, bins=bins, density=True, histtype="stepfilled", alpha=.4, hatch=hatch, label=label)
        histograms.append({"label": label, "n": len(group), "density": heights.tolist(), "edges": edges.tolist()})
    for line in (.05, .95):
        ax.axvline(line, color="black", ls="--")
    ax.set_xlabel("성향점수")
    ax.set_ylabel("밀도")
    ax.legend()
    return {"histograms": histograms, "truncate_lines": [.05, .95]}, [args.propensity]


def f5(args, ax):
    data = load_csv(args.s8_summary)
    require(data, [args.s8_factor_col, args.s8_rate_col, args.s8_n_col], "지정 summary.csv는 배경 설정별 일치도이며 요인별 해석 민감 비율이 없음")
    rates = [number(v) for v in data[args.s8_rate_col]]
    if not all(0 <= v <= 1 for v in rates) or not data[args.s8_n_col].eq(200).all():
        raise ValueError("invalid S8 stored fraction or sample size")
    labels = data[args.s8_factor_col].astype(str).tolist()
    bars = ax.bar(range(len(rates)), rates, color="#999999", edgecolor="black")
    for index, bar in enumerate(bars):
        bar.set_hatch(("/", "\\", "xx")[index % 3])
    ax.set_xticks(range(len(rates)), labels)
    ax.set_ylabel("해석 민감 비율")
    ax.set_xlabel("점검 표본 200곳")
    return {"factors": labels, "rates": rates, "sample_n": 200, "background_seeds": [20260931, 20261001]}, [args.s8_summary]


def provenance():
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="F1,F2,F3,F4,F5")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs/figures/w3")
    parser.add_argument("--font-file", type=Path)
    parser.add_argument("--adopted-metrics", type=Path, default=ROOT / "outputs/models/detect_v0_enriched/oof_metrics_by_origin.csv")
    parser.add_argument("--previous-metrics", type=Path)
    parser.add_argument("--feature-set", default="enriched")
    parser.add_argument("--adopted-label", default="채택 모형")
    parser.add_argument("--previous-label", default="이전 모형(DEFAULT)")
    parser.add_argument("--band-profile", type=Path, default=ROOT / "outputs/models/detect_v0_enriched/band_profile.csv")
    parser.add_argument("--run-meta", type=Path, default=ROOT / "outputs/models/detect_v0_enriched/run_meta.json")
    parser.add_argument("--s10-gate", type=Path)
    parser.add_argument("--dml", type=Path, default=ROOT / "outputs/prescribe/dml_results.json")
    parser.add_argument("--propensity", type=Path)
    parser.add_argument("--s8-summary", type=Path, default=ROOT / "outputs/diagnosis/background/experiment/summary.csv")
    for name in ("auc-ci-low-col", "auc-ci-high-col", "band-ci-low-col", "band-ci-high-col", "ps-col", "treat-col", "s8-factor-col", "s8-rate-col", "s8-n-col"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    requested = args.only.split(",")
    if not requested or len(set(requested)) != len(requested) or any(x not in {"F1", "F2", "F3", "F4", "F5"} for x in requested):
        parser.error("--only는 F1~F5의 목록이어야 합니다")
    try:
        chosen_font = font(args.font_file)
    except (MissingInput, OSError, RuntimeError):
        print("한글 글꼴 없음: 종료 코드 3", file=sys.stderr)
        return 3
    args.out_dir.mkdir(parents=True, exist_ok=True)
    font_manager.fontManager.addfont(chosen_font.get_file())
    report = {"figures": {}, "rounding": ROUNDING, "generating_commit": provenance(),
              "generated_at": datetime.now(timezone.utc).isoformat(), "font_family": chosen_font.get_name(),
              "font_sha256": hashlib.sha256(Path(chosen_font.get_file()).read_bytes()).hexdigest()}
    font_info = {"name": chosen_font.get_name(), "path": str(chosen_font.get_file()), "sha256": report["font_sha256"]}
    all_missing = set()
    strings, code = [], 0
    with matplotlib.rc_context({"svg.fonttype": "path", "axes.unicode_minus": False, "font.family": chosen_font.get_name()}):
        for name in requested:
            fig, ax = plt.subplots(figsize=(9, 5))
            for ext in ("png", "svg", "json"):
                (args.out_dir / f"{name}.{ext}").unlink(missing_ok=True)
            try:
                values, paths = globals()[name.lower()](args, ax)
                ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: rounded(value))) if name not in {"F1", "F2", "F5"} else None
                ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: rounded(value))) if name != "F3" else None
                for text in fig.findobj(Text):
                    text.set_fontproperties(chosen_font)
                fig.tight_layout()
                fig.canvas.draw()
                labels = sorted({text.get_text() for text in fig.findobj(Text) if text.get_text()})
                strings += labels
                gaps = missing_glyphs(chosen_font.get_file(), labels)
                all_missing.update(gaps)
                info = {**report, "font": {**font_info, "cmap_check_passed": not gaps, "missing_glyphs": gaps},
                        "values": values, "strings": labels,
                        "inputs": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
                info.pop("figures")
                for ext in ("png", "svg"):
                    fig.savefig(args.out_dir / f"{name}.{ext}", dpi=300)
                (args.out_dir / f"{name}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                report["figures"][name] = {"status": "generated"}
            except MissingInput as exc:
                report["figures"][name] = {"status": "입력 없음", "reason": str(exc)}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report["figures"][name] = {"status": "입력 오류", "reason": type(exc).__name__}
                code = 2
            finally:
                plt.close(fig)
    text_file = args.out_dir / "figure_strings.txt"
    text_file.write_text("\n".join(strings) + "\n", encoding="utf-8")
    checked = subprocess.run([sys.executable, str(ROOT / "scripts/check_claims.py"), str(text_file), "--format", "json"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    report["checker_exit_code"] = checked.returncode
    try:
        report["checker"] = json.loads(checked.stdout)
    except ValueError:
        report["checker"] = {"message": "검사 결과를 읽을 수 없습니다"}
    if any(word in label for label in strings for word in ("확률", "효과", "유의")) or checked.returncode == 1:
        code = 1
    elif strings and checked.returncode != 0:
        code = checked.returncode
    report["font"] = {**font_info, "cmap_check_passed": not all_missing, "missing_glyphs": sorted(all_missing)}
    if all_missing:
        print("글꼴에 없는 글자: " + " ".join(sorted(all_missing)) + " — 종료 코드 3", file=sys.stderr)
        code = 3
    report["exit_code"] = code
    (args.out_dir / "figures_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
