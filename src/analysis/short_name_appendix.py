"""S1 aggregate appendix and a flag-only helper, using the #55 name policy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
# train_detect.py:548-553 saves store_id/origin/config/p_oof/y (no idx).
# train_detect.py:609 names detect_v0_<tag>; regen_w3.json:91 fixes 10 origins.
DEFAULT_OOF = ROOT / "outputs/models/detect_v0_enriched/oof_predictions.parquet"
N_BOOT = 1000
SEED = 20261003
HEADER = "#44 A안 사전 규칙(S1)에 따른 부록 표입니다. 집단 간 차이는 기술 통계이며 원인을 뜻하지 않습니다."
GROUPS = ("≤2자", "≥3자", "전체")


def read_selected(path: Path, columns: set[str]) -> pd.DataFrame:
    """Read only named fields: appendix input never loads original names."""
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        present = set(pq.read_schema(path).names)
        return pd.read_parquet(path, columns=sorted(columns & present))
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, usecols=lambda x: x in columns)
    raise ValueError("입력 형식은 parquet 또는 CSV여야 합니다")


def binary(values: pd.Series) -> pd.Series:
    normalized = values.replace({"true": 1, "false": 0, "True": 1, "False": 0})
    numeric = pd.to_numeric(normalized, errors="coerce")
    if numeric.isna().any() or not numeric.isin([0, 1]).all():
        raise ValueError("플래그와 y는 결측 없는 bool 또는 0/1이어야 합니다")
    return numeric.astype(int)


def attach(frame: pd.DataFrame, lookup: pd.DataFrame, column: str, *, row_level=False) -> pd.Series:
    if "store_id" in frame and "store_id" in lookup:
        keys = ["store_id", "origin"] if row_level else ["store_id"]
    elif "idx" in frame and "idx" in lookup:
        keys = ["idx"]
    else:
        raise ValueError("입력 키가 OOF와 맞지 않습니다")
    if not set(keys + [column]) <= set(lookup.columns):
        raise ValueError("키 또는 플래그 열이 없습니다")
    if lookup[keys].isna().any().any() or lookup.duplicated(keys).any():
        raise ValueError("입력 키 결측 또는 중복")
    right = lookup[keys].copy()
    right[column] = binary(lookup[column])
    aligned = frame[keys].merge(right, on=keys, how="left", validate="many_to_one", sort=False)
    if aligned[column].isna().any():
        raise ValueError("OOF 키에 대응하는 플래그가 없습니다")
    return aligned[column].astype(int).set_axis(frame.index)


def prepare(oof: pd.DataFrame, flags: pd.DataFrame, cluster_map: pd.DataFrame | None = None,
            missing: pd.DataFrame | None = None, oof_config="현 설정") -> pd.DataFrame:
    required = {"origin", "p_oof", "y"}
    if not required <= set(oof):
        raise ValueError("OOF 필수 열이 없습니다")
    frame = oof.copy()
    if "config" in frame:
        frame = frame.loc[frame["config"].eq(oof_config)].copy()
    if frame.empty:
        raise ValueError("선택한 OOF 행이 없습니다")
    frame = frame.reset_index(drop=True)
    if "store_id" in frame:
        frame["cluster"] = frame["store_id"]
    else:
        if cluster_map is None or not {"idx", "cluster_key"} <= set(cluster_map) or "idx" not in frame:
            raise ValueError("store_id가 없는 OOF에는 idx → cluster_key 파일이 필요합니다")
        if cluster_map[["idx", "cluster_key"]].isna().any().any() or cluster_map["idx"].duplicated().any():
            raise ValueError("cluster-map 키 결측 또는 중복")
        frame = frame.merge(cluster_map[["idx", "cluster_key"]], on="idx", how="left", validate="many_to_one", sort=False)
        frame["cluster"] = frame.pop("cluster_key")
    if frame[["origin", "cluster"]].isna().any().any() or frame.duplicated(["origin", "cluster"]).any():
        raise ValueError("OOF 점포·origin 결측 또는 중복")
    if not frame["origin"].astype(str).str.fullmatch(r"\d{4}Q[1-4]").all():
        raise ValueError("origin은 YYYYQ1~YYYYQ4 형식이어야 합니다")
    frame["y"] = binary(frame["y"])
    frame["p_oof"] = pd.to_numeric(frame["p_oof"], errors="coerce")
    if not np.isfinite(frame["p_oof"]).all() or not frame["p_oof"].between(0, 1).all():
        raise ValueError("예측 점수는 유한한 0~1 값이어야 합니다")
    frame["is_short"] = attach(frame, flags, "is_short")
    if frame.groupby("cluster")["is_short"].nunique().max() != 1:
        raise ValueError("점포별 짧은 상호 플래그가 origin마다 다릅니다")
    if missing is not None:
        frame["online_missing"] = attach(frame, missing, "online_missing", row_level=True)
    return frame


class AUCPlan:
    """Weighted Mann–Whitney AUC; tied scores receive half credit."""
    def __init__(self, frame: pd.DataFrame, rows: np.ndarray, cluster_codes: np.ndarray):
        self.clusters = cluster_codes[rows]
        self.y = frame["y"].to_numpy(dtype=float)[rows]
        _, self.ties = np.unique(frame["p_oof"].to_numpy()[rows], return_inverse=True)

    def value(self, counts: np.ndarray) -> float:
        w = counts[self.clusters]
        pos = np.bincount(self.ties, weights=w * self.y)
        neg = np.bincount(self.ties, weights=w * (1 - self.y), minlength=len(pos))
        denom = pos.sum() * neg.sum()
        if denom == 0:
            return float("nan")
        return float(np.dot(pos, np.cumsum(neg) - neg / 2) / denom)


def interval(values: list[float]) -> dict:
    finite = np.asarray(values)[np.isfinite(values)]
    return {"ci95": np.quantile(finite, [.025, .975]).tolist() if len(finite) else None,
            "valid_replicates": int(len(finite))}


def calculate(frame: pd.DataFrame, *, n_boot=N_BOOT, seed=SEED) -> dict:
    codes, clusters = pd.factorize(frame["cluster"], sort=True)
    origins = sorted(frame["origin"].unique())
    plans, rows_by_group = {}, {}
    for label, mask in zip(GROUPS, (frame.is_short.eq(1), frame.is_short.eq(0), np.ones(len(frame), dtype=bool))):
        rows = np.flatnonzero(mask)
        rows_by_group[label] = rows
        plans[label] = (AUCPlan(frame, rows, codes),
                        [AUCPlan(frame, rows[frame.origin.to_numpy()[rows] == origin], codes) for origin in origins])

    def metrics(counts):
        result = {}
        for label, (pooled, per_origin) in plans.items():
            aucs = [plan.value(counts) for plan in per_origin]
            available = [v for v in aucs if np.isfinite(v)]
            result[label] = (float(np.mean(available)) if available else float("nan"), pooled.value(counts), len(available), aucs)
        return result

    point = metrics(np.ones(len(clusters)))
    draws = {label: [[], []] for label in GROUPS + ("≤2자 − ≥3자",)}
    origin_counts = {label: [] for label in GROUPS}
    rng = np.random.default_rng(seed)
    for _ in range(n_boot):
        # Resample stores globally, preserving every origin of each selected store.
        # Both groups and both metrics share the same draw for paired differences.
        counts = np.bincount(rng.integers(len(clusters), size=len(clusters)), minlength=len(clusters))
        boot = metrics(counts)
        for label in GROUPS:
            origin_counts[label].append(boot[label][2])
            for metric in range(2):
                draws[label][metric].append(boot[label][metric])
        for metric in range(2):
            draws["≤2자 − ≥3자"][metric].append(boot[GROUPS[0]][metric] - boot[GROUPS[1]][metric])
    groups, diagnostics = [], []
    for label in GROUPS:
        part = frame.iloc[rows_by_group[label]]
        mean, pooled, used, aucs = point[label]
        groups.append({"group": label, "n_stores": int(part.cluster.nunique()), "n_rows": len(part),
                       "closure_rate": float(part.y.mean()) if len(part) else None,
                       "mean_origin_auc": mean if np.isfinite(mean) else None,
                       "mean_origin_auc_uncertainty": interval(draws[label][0]), "used_origins": used,
                       "bootstrap_used_origins_range": [min(origin_counts[label]), max(origin_counts[label])],
                       "pooled_auc": pooled if np.isfinite(pooled) else None,
                       "pooled_auc_uncertainty": interval(draws[label][1]),
                       "online_missing_rate": float(part.online_missing.mean()) if "online_missing" in part and len(part) else None})
        for origin, auc in zip(origins, aucs):
            sub = part.loc[part.origin.eq(origin)]
            diagnostics.append({"group": label, "origin": str(origin), "n_rows": len(sub),
                                "n_positive": int(sub.y.sum()), "n_negative": int(len(sub) - sub.y.sum()),
                                "auc": auc if np.isfinite(auc) else None})
    difference = {"group": "≤2자 − ≥3자"}
    for index, name in enumerate(("mean_origin_auc", "pooled_auc")):
        delta = point[GROUPS[0]][index] - point[GROUPS[1]][index]
        difference[name] = delta if np.isfinite(delta) else None
        difference[name + "_uncertainty"] = interval(draws[difference["group"]][index])
    return {"groups": groups, "difference": difference, "origin_diagnostics": diagnostics,
            "origins": [str(x) for x in origins], "n_boot": n_boot, "seed": seed, "unit": "store_id",
            "method": "점포 클러스터 백분위 CI(2.5, 97.5). 동일 추출로 두 집단의 차이를 계산. 각 반복에서 산출 가능한 origin의 AUC를 무가중 평균.",
            "online_missing_definition": "제공된 행별 플래그의 평균; 온라인 표 입력 시 6개 predictor 중 하나 이상 결측"}


def build_flags(licenses: pd.DataFrame) -> pd.DataFrame:
    # Reuse exactly #55: online_features.py:265-269 imports normalize_name at :58.
    from src.data.online_features import short_name_store_ids
    if not {"store_id", "name_raw"} <= set(licenses):
        raise ValueError("store_id와 name_raw가 필요합니다")
    if licenses.store_id.isna().any() or licenses.store_id.duplicated().any():
        raise ValueError("인허가 점포 키 결측 또는 중복")
    return pd.DataFrame({"store_id": licenses.store_id, "is_short": licenses.store_id.isin(short_name_store_ids(licenses))})


def commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_outputs(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for group in result["groups"] + [result["difference"]]:
        row = {k: v for k, v in group.items() if not isinstance(v, (dict, list))}
        for metric in ("mean_origin_auc", "pooled_auc"):
            info = group[metric + "_uncertainty"]
            ci = info["ci95"] or [None, None]
            row.update({metric + "_ci_low": ci[0], metric + "_ci_high": ci[1], metric + "_valid_boot": info["valid_replicates"]})
        rows.append(row)
    pd.DataFrame(rows).to_csv(out_dir / "short_name_appendix.csv", index=False)
    (out_dir / "short_name_appendix.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    def number(value):
        return "산출 불가" if value is None else f"{value:.4f}"
    def ci(item):
        return "산출 불가" if item["ci95"] is None else f"[{number(item['ci95'][0])}, {number(item['ci95'][1])}]"
    lines = [HEADER, "", "| 집단 | 점포 N | 행 N | 폐업률 | 10-origin 평균 AUC | 95% CI | 사용 origin 수 | pooled AUC | 95% CI | 온라인 predictor 결측률 |",
             "|---|---:|---:|---:|---:|---|---:|---:|---|---:|"]
    for group in result["groups"] + [result["difference"]]:
        missing = group.get("online_missing_rate")
        lines.append(f"| {group['group']} | {group.get('n_stores', '—')} | {group.get('n_rows', '—')} | {number(group.get('closure_rate')) if 'closure_rate' in group else '—'} | {number(group['mean_origin_auc'])} | {ci(group['mean_origin_auc_uncertainty'])} | {group.get('used_origins', '—')} | {number(group['pooled_auc'])} | {ci(group['pooled_auc_uncertainty'])} | {number(missing) if missing is not None else '미산출'} |")
    lines += ["", f"입력 origin 수: {len(result['origins'])}. 평균 AUC는 각 origin을 동일한 비중으로 평균하며, pooled AUC는 모든 행을 합쳐 계산합니다.",
              "폐업률·결측률은 행 기준 비율입니다. 점포 N은 중복 없는 점포 수입니다.", result["method"],
              "CI마다 유효 반복 수는 JSON·CSV에, 반복별 사용 origin 수 범위는 JSON에 기록합니다.",
              "", "| 집단 | origin | 행 N | 양성 N | 음성 N | AUC |", "|---|---|---:|---:|---:|---:|"]
    lines += [f"| {x['group']} | {x['origin']} | {x['n_rows']} | {x['n_positive']} | {x['n_negative']} | {number(x['auc'])} |" for x in result["origin_diagnostics"]]
    lines += ["", result["online_missing_definition"], f"생성 commit: {result['generating_commit']}"]
    (out_dir / "short_name_appendix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="S1 짧은 상호 부록 (집계값만 출력)")
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--oof-config", default="현 설정")
    parser.add_argument("--short-flag", type=Path)
    parser.add_argument("--cluster-map", type=Path)
    parser.add_argument("--online-missing", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs/models")
    parser.add_argument("--make-short-flag", type=Path, metavar="LICENSES", help="보조 모드: 인허가 표에서 플래그만 저장")
    parser.add_argument("--flag-out", type=Path)
    args = parser.parse_args(argv)
    if args.make_short_flag and not args.flag_out:
        parser.error("보조 모드는 --flag-out이 필요합니다")
    if not args.make_short_flag and not args.short_flag:
        parser.error("--short-flag가 필요합니다")
    try:
        if args.make_short_flag:
            flags = build_flags(read_selected(args.make_short_flag, {"store_id", "name_raw"}))
            args.flag_out.parent.mkdir(parents=True, exist_ok=True)
            if args.flag_out.suffix.lower() != ".parquet":
                raise ValueError("플래그 출력은 parquet여야 합니다")
            flags.to_parquet(args.flag_out, index=False)
            return 0
        oof = read_selected(args.oof, {"store_id", "origin", "idx", "p_oof", "y", "config"})
        flags = read_selected(args.short_flag, {"store_id", "idx", "is_short"})
        clusters = read_selected(args.cluster_map, {"idx", "cluster_key"}) if args.cluster_map and "store_id" not in oof else None
        missing = None
        if args.online_missing:
            from src.data.online_features import FEATURES
            missing = read_selected(args.online_missing, {"store_id", "origin", "idx", "online_missing", *FEATURES})
            if "online_missing" not in missing:
                if not set(FEATURES) <= set(missing):
                    raise ValueError("online_missing または 6個のオンライン predictor が必要です")
                missing["online_missing"] = missing[FEATURES].isna().any(axis=1)
        result = calculate(prepare(oof, flags, clusters, missing, args.oof_config))
        result["generating_commit"] = commit()
        result["oof_config"] = args.oof_config
        result["input_sha256"] = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in
                                  (("oof", args.oof), ("short_flag", args.short_flag), ("cluster_map", args.cluster_map if clusters is not None else None), ("online_missing", args.online_missing)) if path}
        write_outputs(result, args.out_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if not args.make_short_flag:
            for extension in ("csv", "md", "json"):
                (args.out_dir / f"short_name_appendix.{extension}").unlink(missing_ok=True)
        # Do not include exception text: pandas errors can contain row-level keys.
        print(f"입력을 처리할 수 없습니다 ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
