"""제출용 비식별 사례 projection — `submission-static-0.1` (H1/H8, Issue #49 A3·T3·T5).

세 층을 구분한다 (docs/REPORT_SCHEMA.md §15):
- 내부 report 0.3 (`static_private`): 전체 점포 검색·E2E·QA용. 상호·주소·store_id가 들어 있다. 제출하지 않는다.
- public-static-0.1 (#59): report 0.3에서 개인 확률·구간만 뺀 기술적 projection. 상호·주소·store_id는 남는다 — 제출 범위 아님.
- **submission-static-0.1 (이 모듈):** 비식별 실제 사례 A/B/C(T5)만 담는 제출용 최소 projection.
  식별자는 `CASE-A`·`CASE-B`·`CASE-C`뿐이고, 지역·업종은 T5가 허용한 구·업종만 남긴다.

빼는 것 (내부 정본·static_private에는 그대로 남는다):
- 식별정보: store_id, 상호, 법정동, 도로명·지번 주소, 인허가일, 영업 상태·폐업일, MDIS 코드, 온라인 존재감
- 개인 위험 수치: probability_12m, ci_low, ci_high, interval_note, peer_median, model, calibrated (T3)
- 요인 정량값: contribution(확률 기여·%p), peer_percentile, 원 feature 값(values), driver 문구(건수 포함),
  원 explanation(“약 N%p” 포함) → 방향·표시 상태만으로 만든 고정 문구(summary_text)로 바꾼다.
  표시 보류된 요인(display=false)은 방향도 내보내지 않는다 — 보류의 뜻을 지킨다.
- 처방(prescriptions): 제출 범위 밖

남기는 것: 구·업종, 등급(band)·동종 순위(percentile, 0–100)·동종 집단 이름(peer_group = 구·업종),
요인 범주·방향·표시 상태·보류 사유 코드·driver_code·S8 해석 민감 pair, 정책 매칭 결과, 면책 문구, score_origin·as_of.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from src.serving import report_validation as rv

CONTRACT_VERSION = "submission-static-0.1"
SCHEMA_PATH = Path(__file__).with_name("submission_schema.json")
CASE_LABELS = ("A", "B", "C")
RULE_STAGES = ("base", "alt1", "alt2", "none")  # H2: 기본 / 대안 1 / 대안 2 / 없음
REAL_TITLE = "비식별 실제 사례 {label}"  # T5 표기
SYNTHETIC_TITLE = "합성 사례 {label} (실제 점포 아님)"
DIRECTION_TEXT = {"위험 증가": "높이는 쪽으로 작용했습니다", "위험 감소": "낮추는 쪽으로 작용했습니다"}
# 어느 깊이에서도 나오면 안 되는 키 (스키마 additionalProperties=false와 별개의 2차 방어)
FORBIDDEN_KEYS = frozenset({
    "store_id", "dong", "address_road", "address_jibun", "license_date", "mdis_industry_code", "status",
    "close_date", "license_snapshot_date", "online_presence", "prescriptions",
    "probability_12m", "ci_low", "ci_high", "interval_note", "peer_median", "model", "calibrated",
    "contribution", "peer_percentile", "values", "driver", "explanation", "display_note",
    "unverifiable_conditions", "check_note",
})


def public_id(label: str) -> str:
    if label not in CASE_LABELS:
        raise ValueError(f"case_label은 {CASE_LABELS} 중 하나: {label}")
    return f"CASE-{label}"


def case_title(label: str, data_kind: str) -> str:
    return (REAL_TITLE if data_kind == "real" else SYNTHETIC_TITLE).format(label=label)


def level_text(factor: dict) -> str:
    """표시 상태와 방향만으로 정한 짧은 수준 표기 (정량값 없음)."""
    if not factor["display"]:
        return "데이터 없음" if factor.get("data_missing") else "표시 보류"
    return factor["direction"]


def summary_text(factor: dict) -> str:
    """원 explanation 대신 쓰는 고정 문구. 확률·%p·건수·원 feature 값을 넣지 않는다.
    기여는 예측 분해이며 원인이 아니다 — '원인'·'때문에' 표현을 쓰지 않는다(CL-15)."""
    name = factor["name"]
    if not factor["display"]:
        if factor.get("data_missing"):
            return f"이 점포는 '{name}' 데이터가 없어 이 요인은 진단하지 않습니다."
        return f"'{name}' 요인은 검토가 끝날 때까지 표시하지 않습니다."
    if factor["direction"] in DIRECTION_TEXT:
        return f"'{name}' 요인은 이 점포의 예측 위험도를 {DIRECTION_TEXT[factor['direction']]}."
    return f"'{name}' 요인은 이 점포의 예측 위험도에 거의 영향을 주지 않았습니다."


def project_factor(f: dict) -> dict:
    shown = bool(f["display"])
    return {
        "factor_id": f["factor_id"], "name": f["name"], "category": f["category"],
        "actionability": f["actionability"],
        "direction": f["direction"] if shown else None,
        "level_text": level_text(f), "summary_text": summary_text(f),
        "display": shown, "data_missing": bool(f.get("data_missing")),
        "missing_reason": f.get("missing_reason"), "hold_reason": f.get("hold_reason"),
        "driver_code": f.get("driver_code"),
        "interpretation_sensitive": f["interpretation_sensitive"], "sensitivity_label": f["sensitivity_label"],
    }


# Only public policy facts cross this boundary; internal review prose stays private.
PUBLIC_POLICY_FIELDS = (
    "id", "name", "operator", "link", "eligibility_text", "announce_year", "collected_at",
    "match_status", "matched_by",
)


def project_policy(policy: dict, application: dict | None = None) -> dict:
    public = {key: json.loads(json.dumps(policy[key], ensure_ascii=False)) for key in PUBLIC_POLICY_FIELDS}
    public["unverified_condition_count"] = len(policy["unverifiable_conditions"])
    public["linked_factor_ids"] = []
    application = application or dict(apply_status="unknown", apply_end=None, checked_at=None)
    public.update({key: application[key] for key in ("apply_status", "apply_end", "checked_at")})
    return public


def project_case(record: dict, *, label: str, data_kind: str, rule_stage: str, n_candidates: int | None,
                 seed: int | None, policy_apply: dict | None = None) -> dict:
    """내부 report 0.3 레코드 → 제출용 사례 1건. 원 레코드를 먼저 0.3 계약으로 검증하고, 결과를 제출 계약으로 검증한다.
    원 레코드(정본·static_private 원천)는 바꾸지 않는다."""
    errors = rv.validate_report(record)
    if errors:
        raise ValueError("internal report contract: " + " / ".join(errors[:3]))
    if rule_stage not in RULE_STAGES or rule_stage == "none":
        raise ValueError(f"사례를 만들 수 있는 규칙 단계가 아니다: {rule_stage}")
    if policy_apply is not None and any(p["id"] not in policy_apply for p in record["policies"]):
        raise ValueError("incomplete policy application join")
    case = {
        "_submission_contract_version": CONTRACT_VERSION,
        "source_schema_version": record["_schema_version"],
        "public_id": public_id(label), "case_label": label, "case_title": case_title(label, data_kind),
        "data_kind": "synthetic" if data_kind == "synthetic_sample" else data_kind,
        "score_origin": record["score_origin"], "as_of": record["as_of"],
        "store": {"gu": record["store"]["gu"], "biz_type": record["store"]["biz_type"]},
        "risk": {k: record["risk"][k] for k in ("band", "percentile", "peer_group")},
        "factors": [project_factor(f) for f in record["factors"]],
        "unavailable_categories": list(record["unavailable_categories"]),
        "policy_matching": record["policy_matching"],
        "policies": [project_policy(p, (policy_apply or {}).get(p["id"])) for p in record["policies"]],
        "selection": {"rule_stage": rule_stage, "n_candidates": n_candidates, "seed": seed},
        "disclaimer": record["disclaimer"],
    }
    errors = validate_case(case)
    if errors:
        raise ValueError("submission contract: " + " / ".join(errors[:3]))
    return case


# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(def_name: str) -> Draft202012Validator:
    schema = load_schema()
    return Draft202012Validator({"$ref": f"#/$defs/{def_name}", "$defs": schema["$defs"]})


def validate_def(def_name: str, obj) -> list[str]:
    errs = sorted(_validator(def_name).iter_errors(obj), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errs]


def forbidden_key_paths(value, path="") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            loc = f"{path}/{key}"
            if key in FORBIDDEN_KEYS:
                found.append(loc)
            found.extend(forbidden_key_paths(child, loc))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(forbidden_key_paths(child, f"{path}/{i}"))
    return found


def validate_case(case: dict) -> list[str]:
    errs = [f"{p}: 제출용 사례에 금지된 키" for p in forbidden_key_paths(case)]
    errs += validate_def("submission_case", case)
    if errs:
        return errs
    if len({p['id'] for p in case['policies']}) != len(case['policies']):
        errs.append("duplicate submission policy id")
    if case["public_id"] != f"CASE-{case['case_label']}":
        errs.append("public_id는 CASE-{case_label}이어야 한다")
    if case["case_title"] != case_title(case["case_label"], case["data_kind"]):
        errs.append("case_title이 data_kind와 맞지 않는다")
    expected_peer = f"{case['store']['gu']} {case['store']['biz_type']}"
    if case["risk"]["peer_group"] != expected_peer:
        errs.append("peer_group은 구·업종 이름이어야 한다")
    for i, f in enumerate(case["factors"]):
        if f["display"] != (f["hold_reason"] is None):
            errs.append(f"factors/{i}: display=false ⇔ hold_reason")
        if (f["direction"] is None) == f["display"]:
            errs.append(f"factors/{i}: 표시 보류 요인만 direction이 없다")
        if f["sensitivity_label"] != ("해석 민감" if f["interpretation_sensitive"] else ""):
            errs.append(f"factors/{i}: sensitivity pair 불일치")
        if f["level_text"] != level_text(f) or f["summary_text"] != summary_text(f):
            errs.append(f"factors/{i}: 수준 표기·문구는 고정 템플릿이어야 한다")
    return errs
