"""Operational serve metadata contract, independent of model execution/imports."""
import re

MODEL_CLASS = "sklearn.ensemble.HistGradientBoostingClassifier"
S8_VERSION = "S8-2026-10-01"
SEEDS = {"primary": 20260931, "sensitivity": 20261001}


def operational_errors(records, meta):
    errors = []
    for key, expected in (("params_name", "adopted"), ("params_contract", "adopted"),
                          ("model_class", MODEL_CLASS)):
        if meta.get(key) != expected:
            errors.append(f"{key} must be {expected}")
    def digest(value, label):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            errors.append(f"{label}: sha256 required")
    detect = meta.get("detect_run_provenance")
    if not isinstance(detect, dict):
        detect = {}
    digest(detect.get("run_meta_sha256"), "detect_run_provenance.run_meta_sha256")
    if detect.get("params_name") != "adopted":
        errors.append("detect_run_provenance.params_name must be adopted")
    rule = meta.get("s8_rule")
    if not isinstance(rule, dict):
        rule = {}
    if rule.get("rule_version") != S8_VERSION or not rule.get("rule_ref"):
        errors.append("s8_rule: S8 version/reference required")
    if rule.get("method") != "stratified" or rule.get("n_background") != 256 or not rule.get("comparison"):
        errors.append("s8_rule: stratified 256 / comparison required")
    bg = meta.get("background")
    if not isinstance(bg, dict):
        bg = {}
    if bg.get("operational") is not True:
        errors.append("background.operational must be true")
    backgrounds = bg.get("backgrounds")
    if not isinstance(backgrounds, dict):
        backgrounds = {}
    for role, seed in SEEDS.items():
        entry = backgrounds.get(role)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("operational") is not True or type(entry.get("seed")) is not int or entry.get("seed") != seed:
            errors.append(f"background.{role}: operational seed {seed} required")
        if entry.get("n") != 256 or entry.get("method") != "stratified":
            errors.append(f"background.{role}: stratified 256 required")
        for key in ("rows_sha256", "index_sha256"):
            digest(entry.get(key), f"background.{role}.{key}")
    for key in ("s8_summary", "cutoff_provenance"):
        if not isinstance(meta.get(key), dict) or not meta[key]:
            errors.append(f"{key} required")
    cut = meta.get("band_cutoffs")
    if not isinstance(cut, dict) or set(cut) != {"cut_mid", "cut_high", "base_rate"}:
        errors.append("band_cutoffs: canonical cut_mid/cut_high/base_rate required")
    for rec in records:
        for f in rec["factors"]:
            label = f"{rec['store_id']}/{f['factor_id']}"
            if type(f.get("interpretation_sensitive")) is not bool or f.get("sensitivity_label") != ("해석 민감" if f.get("interpretation_sensitive") else ""):
                errors.append(f"{label}: sensitivity pair required")
            if f["factor_id"] == "online_attention" and f.get("driver_code") not in ("decline", "lapse", "absent", "unobservable", "no_change", "presence"):
                errors.append(f"{label}: canonical driver_code required")
    return errors


def synthetic_provenance():
    """Fabricated metadata for synthetic tests/samples; no real hashes or model claims."""
    return {"params_name": "adopted", "params_contract": "adopted", "model_class": MODEL_CLASS,
            "detect_run_provenance": {"run_meta_sha256": "0" * 64, "params_name": "adopted"},
            "s8_rule": {"rule_ref": "Issue #49 S8 (synthetic fixture)", "rule_version": S8_VERSION,
                        "method": "stratified", "n_background": 256, "comparison": "direction or display"},
            "background": {"operational": True, "backgrounds": {
                role: {"seed": seed, "operational": True, "n": 256, "method": "stratified",
                       "rows_sha256": "1" * 64, "index_sha256": "2" * 64}
                for role, seed in SEEDS.items()}},
            "s8_summary": {"synthetic": True}, "cutoff_provenance": {"synthetic": True}}
