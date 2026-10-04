"""S10 wording gate; consume saved training statistics without recalculation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RULE = 'S10: "약 2배"는 독립 검증 구간의 lift 95% CI 하한이 1.5 이상일 때만 쓴다.'
BASE_WORDING = "평균보다 폐업이 많이 관측된 집단"


def finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("유한한 숫자가 필요합니다")
    return float(value)


def evaluate(meta: dict) -> dict:
    # Producer: src/models/train_detect.py:518-532; seed: bands.py:32.
    try:
        band = meta["band_provenance"]
        lift = finite_number(band["high_lift_test"])
        ci = band["high_lift_ci95"]
        if not isinstance(ci, list) or len(ci) != 2:
            raise ValueError("CI는 하한·상한 두 숫자여야 합니다")
        low, high = map(finite_number, ci)
        if low > high:
            raise ValueError("CI 하한이 상한보다 큽니다")
        provenance = band["high_lift_ci"]
        for key, expected in (("n_boot", 1000), ("seed", 20260927), ("unit", "store_id")):
            value = provenance[key]
            if value != expected or isinstance(value, bool):
                raise ValueError(f"{key} 설정 불일치 (필수: {expected})")
        origins = band["test_origins"]
        if not isinstance(origins, list) or not origins or not all(isinstance(x, str) and x for x in origins):
            raise ValueError("test_origins가 필요합니다")
    except (KeyError, TypeError) as exc:
        raise ValueError("band_provenance 필수 필드 누락 또는 형식 오류") from exc
    allowed = low >= 1.5
    return {"lift": lift, "ci95": [low, high],
            "decision": "allowed" if allowed else "not_allowed",
            "wording": BASE_WORDING + "(약 2배)" if allowed else BASE_WORDING,
            "rule": RULE, "test_origins": origins,
            "n_boot": 1000, "seed": 20260927, "unit": "store_id"}


def generating_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S10 저장값 문구 게이트")
    parser.add_argument("--run-meta", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs/models")
    args = parser.parse_args(argv)
    # Remove previous decisions on invalid input so stale approvals cannot survive.
    destinations = [args.out_dir / f"s10_gate.{ext}" for ext in ("json", "md")]
    try:
        raw = args.run_meta.read_bytes()
        result = evaluate(json.loads(raw.decode("utf-8-sig")))
        result.update(input_sha256=hashlib.sha256(raw).hexdigest(), generating_commit=generating_commit())
        args.out_dir.mkdir(parents=True, exist_ok=True)
        destinations[0].write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        destinations[1].write_text(
            f"# S10 문구 게이트\n\n{RULE}\n\n"
            f"- lift: {result['lift']}\n- 95% CI: [{result['ci95'][0]}, {result['ci95'][1]}]\n"
            f"- 판정: {result['decision']}\n- 문구: {result['wording']}\n"
            f"- test_origins: {', '.join(result['test_origins'])}\n"
            f"- unit: store_id / n_boot: 1000 / seed: 20260927\n"
            f"- 입력 sha256: {result['input_sha256']}\n- 생성 commit: {result['generating_commit']}\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        for destination in destinations:
            destination.unlink(missing_ok=True)
        print(f"판정하지 않았습니다: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
