"""W2-5 결과 레코드 검증 — JSON Schema(`report_schema.json`) + 스키마로 표현할 수 없는 규칙.

계약 문서: `docs/REPORT_SCHEMA.md`. 모델 코드(`src/models/`, PR #32~#36)는 import하지 않는다 (DECISIONS 2026-09-26 D9).
요인 표(`FACTOR_CONTRACT`)는 진단(W2-3) `diagnose.FACTORS`와 같아야 하는 **계약 사본**이다 — 진단 쪽 표가 바뀌면
이 표와 스키마의 `factor_id` enum을 같은 PR에서 함께 바꾼다.

사용:
    from src.serving.report_validation import validate_report, validate_def
    errors = validate_report(rec)            # [] 이면 통과
    errors = validate_def("dong_summary_row", row)
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).with_name("report_schema.json")
SCHEMA_VERSION = "0.2"

# factor_id → (name, category, actionability). 진단 요인 매핑표(DECISIONS 2026-09-25 W2-3, PR #34)의 사본.
FACTOR_CONTRACT: dict[str, tuple[str, str, str]] = {
    "tenure": ("업력", "사업체 구조", "external"),
    "store_profile": ("업종·점포 규모", "사업체 구조", "external"),
    "district": ("자치구", "입지·수요", "external"),
    "trdar_population": ("상권 유동·배후 인구", "입지·수요", "external"),
    "trdar_vitality": ("상권 변화·영업 지속", "입지·수요", "external"),
    "online_attention": ("온라인 언급(블로그)", "입지·수요", "owner"),
    "peer_competition": ("동종 업종 경쟁·개폐업", "경쟁", "external"),
    "peer_sales": ("동종 업종 매출 수준", "경쟁", "external"),
    "rent_level": ("임대료 수준(공시지가)", "비용", "policy"),
}
# 화면에 나가는 진단문에 쓰면 안 되는 인과 표현 (W2-3 테스트와 같은 취지)
CAUSAL_WORDS = ("때문에", "원인", "고치면", "개선하면", "줄어듭니다")
# 불확실성 구간을 신뢰구간으로 부르지 않는다 (DECISIONS 2026-09-25 W2-2 불확실성 구간)
INTERVAL_FORBIDDEN = ("신뢰구간", "신뢰 구간", "신뢰수준")
NEGLIGIBLE = 0.001  # 확률 단위 0.1%p


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(def_name: str | None) -> Draft202012Validator:
    schema = load_schema()
    if def_name is not None:
        if def_name not in schema["$defs"]:
            raise KeyError(f"정의 없음: {def_name}")
        schema = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{def_name}"}
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_errors(validator: Draft202012Validator, obj) -> list[str]:
    errs = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errs]


def validate_def(def_name: str, obj) -> list[str]:
    """`$defs`의 한 정의(search_index_entry, dong_summary_row, policy_source, serve_record_v0_2 등)로 검증한다."""
    return _schema_errors(_validator(def_name), obj)


def direction_of(contribution: float) -> str:
    """기여 방향. |기여| < NEGLIGIBLE이면 '영향 미미' — 진단문이 '거의 영향을 주지 않았습니다'를 쓰는 기준과 같다
    (W2-3 `diagnose.explanation`). 반올림된 0.0에 '위험 증가'가 붙는 것을 막는다."""
    if abs(contribution) < NEGLIGIBLE:
        return "영향 미미"
    return "위험 증가" if contribution > 0 else "위험 감소"


def allowed_directions(contribution: float) -> set[str]:
    """반올림된(소수 4자리) 기여값에 허용되는 방향. 서빙은 반올림 전 값으로 '영향 미미'를 정하므로(PR #36
    `diagnose.direction_label`), 반올림 후 정확히 ±0.0010인 값은 원래 0.00095~0.00105라 두 방향 모두 가능하다.
    그 밖에서는 `direction_of`와 같아야 한다."""
    if abs(abs(contribution) - NEGLIGIBLE) < 1e-9:
        return {"영향 미미", "위험 증가" if contribution > 0 else "위험 감소"}
    return {direction_of(contribution)}


# 온라인 요인 driver 문구 → 분류. PR #36 `diagnose.online_driver_text`의 템플릿 전부를 전체 일치로 옮긴 계약 사본이다
# (부분 문자열 검색 아님). 서빙 쪽 문구가 바뀌면 이 표를 같은 PR에서 바꾼다 — 모르는 문구는 검증 오류다.
# lapse 경계(마지막 언급 후 3개월 이하 = 언급 있음)는 `diagnose.online_signal_is_presence`와 같다.
ONLINE_LAPSE_MAX_PRESENT_MONTHS = 3
_ONLINE_DRIVER_PATTERNS: list[tuple[re.Pattern, object]] = [
    (re.compile(r"최근 6개월 블로그 언급이 그 전 6개월보다 (\d+)건 줄어듦"), "decline"),
    (re.compile(r"최근 6개월 블로그 언급이 그 전 6개월보다 (\d+)건 늘어남"), "presence"),
    (re.compile(r"최근 1년 블로그 언급 수 변화 없음"), "no_change"),
    (re.compile(r"최근 12개월 블로그 언급 (\d+)건"), lambda n: "absent" if n == 0 else "presence"),
    (re.compile(r"최근 3개월 블로그 언급 (\d+)건"), lambda n: "absent" if n == 0 else "presence"),
    (re.compile(r"최근 12개월 블로그 언급 있음"), "presence"),
    (re.compile(r"최근 12개월 블로그 언급 없음"), "absent"),
    (re.compile(r"과거 블로그 언급 이력 있음"), "presence"),
    (re.compile(r"블로그 언급 이력 없음"), "absent"),
    (re.compile(r"이번 달에도 블로그 언급 있음"), "presence"),
    (re.compile(r"마지막 블로그 언급 이후 (\d+)개월"),
     lambda n: "lapse" if n > ONLINE_LAPSE_MAX_PRESENT_MONTHS else "presence"),
    (re.compile(r"관측 불가\(검색 결과 상한\)"), "unobservable"),
]
# PR #38 §2: 온라인 요인은 근거가 언급 감소·끊김·없음(온라인 노출 부족)일 때만 정책에 연결한다.
# 관측 불가(검색 결과 상한)·변화 없음·언급 있음/많음은 연결하지 않는다.
ONLINE_DRIVER_LINKABLE = frozenset({"decline", "lapse", "absent"})


def classify_online_driver(text: str | None) -> str | None:
    """온라인 driver 문구 → decline/lapse/absent/presence/no_change/unobservable. 템플릿에 없는 문구면 None."""
    if text is None:
        return None
    for pat, cls in _ONLINE_DRIVER_PATTERNS:
        m = pat.fullmatch(text)
        if m:
            return cls(int(m.group(1))) if callable(cls) else cls
    return None


def online_driver_errors(factors: list[dict]) -> list[str]:
    """온라인 요인 driver가 알려진 템플릿인지 (serve 입력·최종 리포트 공통)."""
    return [f"online_attention: 알 수 없는 driver 문구 '{f['driver']}' (PR #36 online_driver_text 템플릿과 다름)"
            for f in factors if f["factor_id"] == "online_attention" and f["driver"] is not None
            and classify_online_driver(f["driver"]) is None]


def policy_linkable_factors(factors: list[dict]) -> dict[str, float]:
    """정책을 요인에 연결할 수 있는 요인 → 기여 (FACTOR_POLICY_LINKS.md §1·§2, PR #38 초안).
    공통: 표시되고(display=true) 위험을 올린(contribution > 0) 요인 — factors에 없는 비활성 요인은 자연히 빠진다.
    온라인 요인은 driver가 노출 부족(ONLINE_DRIVER_LINKABLE)일 때만."""
    out = {}
    for f in factors:
        if not (f["display"] and f["contribution"] > 0):
            continue
        if f["factor_id"] == "online_attention" and classify_online_driver(f["driver"]) not in ONLINE_DRIVER_LINKABLE:
            continue
        out[f["factor_id"]] = f["contribution"]
    return out


def quarter_end(quarter: str) -> str:
    return str(pd.Period(quarter, freq="Q").end_time.date())


def semantic_errors(rec: dict) -> list[str]:
    """스키마 통과를 전제로 레코드 안의 교차 규칙을 검사한다."""
    errs: list[str] = []
    store, risk, factors = rec["store"], rec["risk"], rec["factors"]

    if rec["as_of"] != quarter_end(rec["score_origin"]):
        errs.append(f"as_of {rec['as_of']}가 score_origin {rec['score_origin']}의 분기 말일이 아니다")
    if store["license_date"] is not None and store["license_date"] > rec["as_of"]:
        errs.append("license_date가 as_of 이후다 (as_of 당시 영업 점포가 아니다)")
    st = store["status"]
    if st["close_date"] is not None and st["close_date"] <= rec["as_of"]:
        errs.append("close_date가 as_of 이전이다 — as_of 당시 영업 점포만 리포트 대상")

    if not risk["ci_low"] <= risk["probability_12m"] <= risk["ci_high"]:
        errs.append("ci_low ≤ probability_12m ≤ ci_high 위반")
    if risk["peer_group"] != f"{store['gu']} {store['biz_type']}":
        errs.append(f"peer_group '{risk['peer_group']}' ≠ '{store['gu']} {store['biz_type']}'")
    if any(w in risk["interval_note"] for w in INTERVAL_FORBIDDEN):
        errs.append("interval_note에 신뢰구간 표현이 있다")

    ids = [f["factor_id"] for f in factors]
    if len(ids) != len(set(ids)):
        errs.append(f"factor_id 중복: {ids}")
    contribs = [f["contribution"] for f in factors]
    if contribs != sorted(contribs, reverse=True):
        errs.append("factors가 기여 큰 순서가 아니다")
    for f in factors:
        name, cat, act = FACTOR_CONTRACT[f["factor_id"]]
        if (f["name"], f["category"], f["actionability"]) != (name, cat, act):
            errs.append(f"{f['factor_id']}: 이름·유형·조치 가능성이 요인 계약과 다르다")
        if f["direction"] not in allowed_directions(f["contribution"]):
            errs.append(f"{f['factor_id']}: direction이 기여 부호와 다르다")
        if any(w in f["explanation"] for w in CAUSAL_WORDS):
            errs.append(f"{f['factor_id']}: 진단문에 인과 표현")
        if f["driver"] is not None and f["factor_id"] != "online_attention":
            errs.append(f"{f['factor_id']}: driver는 온라인 요인에만 있다")
    errs += online_driver_errors(factors)

    present = {f["category"] for f in factors}
    both = present & set(rec["unavailable_categories"])
    if both:
        errs.append(f"unavailable_categories {sorted(both)}에 속한 요인이 factors에 있다")

    linkable = set(policy_linkable_factors(factors))
    pol_ids = [p["id"] for p in rec["policies"]]
    if len(pol_ids) != len(set(pol_ids)):
        errs.append("policies id 중복")
    for p in rec["policies"]:
        bad = set(p["linked_factor_ids"]) - linkable
        if bad:
            errs.append(f"정책 {p['id']}: 연결할 수 없는 요인에 연결 {sorted(bad)} "
                        "(표시 안 됨·위험을 올리지 않음·온라인 근거가 노출 부족이 아님)")
    rx_ids = [p["id"] for p in rec["prescriptions"]]
    if len(rx_ids) != len(set(rx_ids)):
        errs.append("prescriptions id 중복")
    return errs


def validate_report(rec: dict) -> list[str]:
    """최종 리포트 레코드 1건 검증. 스키마 오류가 있으면 교차 규칙은 검사하지 않는다."""
    errs = _schema_errors(_validator(None), rec)
    return errs or semantic_errors(rec)
