"""Attach existing S8 diagnosis fields to older serve JSON, without model computation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from src.serving import build_db as bd


def upgrade(serve_dir: Path, out: Path) -> dict:
    serve_dir, out = Path(serve_dir).resolve(), Path(out).resolve()
    bd.guard_output_path(out / "reports.jsonl")
    if out == serve_dir or out.exists():
        raise bd.BuildError("upgrade destination must be new and different from serve input")
    report_path, meta_path = serve_dir / "reports.jsonl", serve_dir / "serve_meta.json"
    diagnosis_path = serve_dir / "diagnosis.parquet"
    records = bd.read_jsonl(report_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    bd.validate_serve_input(records, meta)
    key = ["store_id", "origin", "factor_id"]
    pair = ["interpretation_sensitive", "sensitivity_label"]
    primary = ["contribution", "direction", "display"]
    diagnosis = pd.read_parquet(diagnosis_path, columns=key + pair + primary)
    diagnosis = diagnosis[diagnosis["origin"].astype(str) == meta["score_origin"]].copy()
    if diagnosis.duplicated(key).any():
        raise bd.BuildError("duplicate S8 diagnosis store/origin/factor")
    expected = {(r["store_id"], meta["score_origin"], f["factor_id"]) for r in records for f in r["factors"]}
    actual = set(map(tuple, diagnosis[key].itertuples(index=False, name=None)))
    if actual != expected:
        raise bd.BuildError("S8 diagnosis factor keys do not match serve reports")
    source = diagnosis.set_index(key)[pair + primary].to_dict("index")
    for r in records:
        for f in r["factors"]:
            row = source[(r["store_id"], meta["score_origin"], f["factor_id"])]
            if (round(float(row["contribution"]), 4) != f["contribution"]
                    or row["direction"] != f["direction"] or bool(row["display"]) != f["display"]):
                raise bd.BuildError("S8 diagnosis primary values do not match serve reports")
            flag, label = row["interpretation_sensitive"], row["sensitivity_label"]
            if pd.isna(flag) or flag not in (True, False) or type(flag).__name__ not in ("bool", "bool_"):
                raise bd.BuildError("S8 diagnosis interpretation_sensitive must be boolean")
            flag = bool(flag)
            if not isinstance(label, str) or label != ("해석 민감" if flag else ""):
                raise bd.BuildError("S8 diagnosis sensitivity label mismatch")
            for k, value in zip(pair, (flag, label)):
                if k in f and f[k] != value:
                    raise bd.BuildError(f"serve/diagnosis {k} mismatch; do not overwrite")
                f[k] = value
    bd.validate_serve_input(records, meta, purpose="release")
    meta["report_sensitivity_upgrade"] = {
        "source_reports_sha256": bd.sha256(report_path), "source_serve_meta_sha256": bd.sha256(meta_path),
        "source_diagnosis_sha256": bd.sha256(diagnosis_path),
        "fields": pair, "note": "source S8 diagnosis fields only; primary contribution/direction/display unchanged"}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        (tmp / "reports.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                                          encoding="utf-8", newline="\n")
        (tmp / "serve_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
        bd.validate_serve_input(bd.read_jsonl(tmp / "reports.jsonl"), meta, purpose="release")
        os.rename(tmp, out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serve-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    upgrade(args.serve_dir, args.out)
    print(f"S8 source fields attached -> {args.out}")


if __name__ == "__main__":
    main()
