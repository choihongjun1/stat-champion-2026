# -*- coding: utf-8 -*-
"""W2-6 화면 개발용 샘플 — 서빙 결과(reports.jsonl)에서 화면이 처리해야 할 경우를 골고루 뽑는다.

실제 예측용 master(#35)가 오기 전에 화면을 만들 수 있게 하려는 것이다. 값은 검증 구간(2025Q2)
시험 실행 결과이므로 "현재 점포의 실제 위험도"로 쓰면 안 된다 (README에 명시).

뽑는 경우 (한 점포가 여러 경우에 해당해도 한 번만 넣고, 해당하는 경우를 모두 cases에 적는다)
- band_high / band_mid / band_low          : 등급별 대표 (각 2곳, 중앙값 근처)
- top_risk                                 : 전체 최고 위험
- near_cut_high                            : high 컷오프 바로 위 (경계 표시 확인)
- wide_interval                            : 불확실성 구간이 가장 넓은 점포
- online_hold                              : 온라인 요인 표시 보류 (오탐 검토 대기, data_missing=false)
- online_decline                           : 온라인 요인이 위험을 올리고 근거가 "언급 줄어듦/마지막 언급 이후 N개월"
- online_absent                            : 온라인 요인이 위험을 올리고 근거가 "언급 이력 없음"
- missing_outside_trdar                    : 상권 경계 밖 — 상권 요인 4개 모두 데이터 없음
- missing_sales_only                       : 상권 안인데 매출 요인만 데이터 없음
- missing_online                           : 온라인 관측 불가
- peer_top                                 : 동종 점포 대비 상위 5% 이내 위험 요인(표시, 기여 > 0)이 있는 점포
- no_standout                              : 눈에 띄는 위험 요인 없음 (가장 큰 위험 기여 < 1%p)

`--licenses`를 주면 샘플 레코드 store 블록에 사업장명·주소를 붙인다 (reports.jsonl에 이미 있으면 그대로).

출력 (`<serve 결과 폴더>/sample/`)
- sample_reports.jsonl  : 원본 형식 그대로 (점포당 1줄)
- sample_reports.json   : 같은 내용, 들여쓴 배열 + 각 레코드에 "_sample_cases"
- sample_index.csv      : 점포 · 경우 · 등급 · 확률 · 한 줄 설명
- README_W2-6.md        : 필드 설명과 화면 처리 규칙

실행:
    python -m src.models.sample_reports --serve-dir outputs/serve/_trial/out
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

TRDAR_FACTORS = ("trdar_population", "trdar_vitality", "peer_competition", "peer_sales")
SMALL = 0.01          # no_standout: 가장 큰 위험(+) 기여가 이보다 작으면
PEER_TOP_MIN = 95     # peer_top: 동종 대비 상위 5% 이내
DECLINE_WORDS = ("줄어듦", "이후")
# 경우 판정 목록 (등급·최고 위험·경계·구간 대표 외). 순서 = 샘플 선택·README 표 순서
CASES = ("online_hold", "online_decline", "online_absent", "missing_outside_trdar", "missing_sales_only",
         "missing_online", "peer_top", "no_standout")


def load_reports(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _factor(rec: dict, fid: str) -> dict | None:
    return next((f for f in rec["factors"] if f["factor_id"] == fid), None)


def _missing(f: dict | None) -> bool:
    return bool(f and f.get("data_missing"))


def case_flags(rec: dict) -> dict[str, bool]:
    """레코드 하나가 어떤 경우에 해당하는지."""
    on = _factor(rec, "online_attention")
    tr = [_factor(rec, k) for k in TRDAR_FACTORS]
    sales = _factor(rec, "peer_sales")
    on_up = bool(on and on["display"] and not _missing(on) and on["contribution"] > 0)
    drv = (on or {}).get("driver") or ""
    return {
        "online_hold": bool(on and not on["display"] and not _missing(on)),
        "online_decline": on_up and any(w in drv for w in DECLINE_WORDS),
        # "없음" 중 "변화 없음"(최근 1년 언급 수 변화 없음)은 이력 없음이 아니다
        "online_absent": on_up and "없음" in drv and "변화 없음" not in drv,
        "missing_outside_trdar": all(_missing(f) for f in tr if f is not None) and any(f is not None for f in tr),
        "missing_sales_only": _missing(sales) and not _missing(_factor(rec, "trdar_vitality")),
        "missing_online": _missing(on),
        "peer_top": any(f["display"] and f["contribution"] > 0 and f.get("peer_percentile") is not None
                        and f["peer_percentile"] >= PEER_TOP_MIN for f in rec["factors"]),
        "no_standout": max((f["contribution"] for f in rec["factors"]), default=0.0) < SMALL,
    }


def summary_frame(recs: list[dict]) -> pd.DataFrame:
    rows = []
    for i, r in enumerate(recs):
        k = r["risk"]
        rows.append({"i": i, "store_id": r["store_id"], "band": k["band"], "p": k["probability_12m"],
                     "width": k["ci_high"] - k["ci_low"], "gu": r["store"]["gu"],
                     "biz_type": r["store"]["biz_type"], **case_flags(r)})
    return pd.DataFrame(rows)


def select(df: pd.DataFrame, cut_high: float | None, per_band: int = 2) -> dict[int, list[str]]:
    """경우별로 점포를 고른다. 반환: {레코드 위치: [경우,...]}."""
    picked: dict[int, list[str]] = {}

    def add(idx, case):
        for i in idx:
            picked.setdefault(int(i), [])
            if case not in picked[int(i)]:
                picked[int(i)].append(case)

    for b in ("high", "mid", "low"):
        sub = df[df["band"] == b]
        if len(sub):
            med = sub["p"].median()
            add(sub.assign(d=(sub["p"] - med).abs()).nsmallest(per_band, "d")["i"], f"band_{b}")
    add(df.nlargest(1, "p")["i"], "top_risk")
    if cut_high is not None:
        above = df[df["p"] >= cut_high]
        add(above.nsmallest(1, "p")["i"], "near_cut_high")
    add(df.nlargest(1, "width")["i"], "wide_interval")
    for case in CASES:
        sub = df[df[case]]
        if len(sub):
            # 등급이 다양하게 나오도록 위험도가 높은 점포 하나 + 중앙값 근처 하나
            add(sub.nlargest(1, "p")["i"], case)
            if len(sub) > 1:
                med = sub["p"].median()
                add(sub.assign(d=(sub["p"] - med).abs()).nsmallest(1, "d")["i"], case)
    # 이미 뽑힌 점포가 다른 경우에도 해당하면 모두 적는다
    for i in picked:
        row = df.loc[df["i"] == i].iloc[0]
        for case in CASES:
            if row[case] and case not in picked[i]:
                picked[i].append(case)
    return picked


def one_line(rec: dict) -> str:
    shown = [f for f in rec["factors"] if f["display"]]
    up = [f for f in shown if f["contribution"] > 0]
    top = max(up, key=lambda f: f["contribution"], default=None)
    held = [f["name"] for f in rec["factors"] if not f["display"]]
    s = f"최대 위험 요인: {top['name']} {top['contribution'] * 100:+.1f}%p" if top else "위험을 높이는 요인 없음"
    return s + (f" / 표시 보류: {', '.join(held)}" if held else "")


def _letters(k: int) -> str:
    """0 → A, 25 → Z, 26 → AA …"""
    s = ""
    k += 1
    while k:
        k, r = divmod(k - 1, 26)
        s = chr(65 + r) + s
    return s


def mask_records(recs: list[dict]) -> list[dict]:
    """공개 저장소용: 점포를 식별할 수 있는 값(store_id·상호·주소·동·인허가일)을 가린 사본. 순서 = 입력 순서.

    store_id는 공개 인허가 데이터로 역추적되므로 함께 가린다. 자치구·업종은 두고, 인허가일은 연 단위("YYYY-01-01"),
    factors[].values의 면적·업력은 뭉갠다(`_coarsen_values`). 나머지 values(상권·온라인 수치)는 그대로 둔다.
    """
    out, per_biz = [], {}
    for n, r in enumerate(recs, start=1):
        s = dict(r["store"])
        for legacy in ("road_address", "address"):  # 필드명 변경 전 서빙 출력이 섞여도 원래 주소가 남지 않게
            s.pop(legacy, None)
        k = per_biz.get(s["biz_type"], 0)
        per_biz[s["biz_type"]] = k + 1
        addr = f"서울특별시 {s['gu']} 샘플로 {n}"
        lic = s.get("license_date")
        s.update(name=f"(샘플) {s['biz_type']} {_letters(k)}", address_road=addr, address_jibun=addr, dong=None,
                 license_date=f"{str(lic)[:4]}-01-01" if lic else None)
        factors = [{**f, "values": _coarsen_values(f.get("values") or {})} for f in r["factors"]]
        out.append({**r, "store_id": f"SAMPLE-{n:03d}", "store": s, "factors": factors})
    return out


def _coarsen_values(values: dict) -> dict:
    """면적·업력은 인허가 공개 데이터와 맞춰 점포를 좁힐 수 있어 공개용에서는 뭉갠다.

    area: 10㎡ 단위 반올림 / age_months: 연 단위 내림 (41 → 36).
    """
    v = dict(values)
    if v.get("area") is not None:
        v["area"] = float(round(float(v["area"]) / 10) * 10)
    if v.get("age_months") is not None:
        v["age_months"] = int(v["age_months"]) // 12 * 12
    return v


def _is_under_docs(path: Path) -> bool:
    from src.data import config

    try:
        Path(path).resolve().relative_to((config.REPO_ROOT / "docs").resolve())
        return True
    except ValueError:
        return False


MASK_NOTE = ("- **가게 이름·주소·store_id는 가린 값이고, 면적(10㎡)·업력·인허가일(연 단위)은 뭉갠 값입니다 (공개 저장소).** 실명판은 팀 드라이브에만 있습니다"
             " (`--mask` 없이 `outputs/` 아래로 추출).\n")

README = """# W2-6 화면 개발용 샘플 결과

- **주의: 검증 구간 시험 결과입니다. 실제 "현재 점포 위험도"가 아니므로 화면 개발·테스트에만 쓰고
  외부 시연·보고서에 수치로 쓰지 마세요.** 실제 값은 예측용 master(#35)가 나오면 같은 형식으로 다시 만듭니다.
{mask_note}- 출처: `{serve_dir}` (서빙 시험 실행, 기준 시점 {as_of})
- 점포 {n}곳. 어떤 경우를 대표하는지는 `sample_index.csv`와 각 레코드의 `_sample_cases`에 있습니다.

## 파일
| 파일 | 내용 |
|---|---|
| `sample_reports.jsonl` | 서빙 출력과 같은 형식 (점포당 1줄) — 화면 파서는 이 형식 기준 |
| `sample_reports.json` | 같은 내용을 보기 쉽게 들여쓴 배열 (+ `_sample_cases`) |
| `sample_index.csv` | 점포별 경우·등급·확률·한 줄 요약 |

## 레코드 구조
```
{{
  "_schema_version": "0.2",
  "store_id": "...",
  "score_origin": "YYYYQn",         // 예측 기준 분기 (0.2에서 추가)
  "as_of": "YYYY-MM-DD",            // 기준 시점 (score_origin 분기 말일)
  "store": {{"biz_type": "...", "gu": "...",
            "name": "...", "address_road": "...", "address_jibun": "...", "dong": "...",
            "license_date": "YYYY-MM-DD"}},
  "risk": {{ ... }},
  "factors": [ ... ],               // 기여도 큰 순서 (위험을 올리는 요인 먼저)
  "unavailable_categories": ["비용"],
  "disclaimer": "..."
}}
```

## store 블록
| 필드 | 뜻 | 화면 처리 |
|---|---|---|
| `biz_type`, `gu` | 업종(인허가 종류), 자치구 | 점포 헤더 |
| `name` | 사업장명 (인허가 원문) | 점포 헤더 제목. 검색 결과 목록 |
| `address_road`, `address_jibun` | 도로명 주소, 지번 주소 (인허가 원문) | 도로명 우선, 없으면 지번 |
| `dong` | 법정동 (인허가 데이터 기준, 예: 당산동1가·문래동3가) | 보조 표시 |
| `license_date` | 인허가일 ("YYYY-MM-DD") | "개업 N년차" 등 보조 표시 |

`name`·주소·인허가일은 서빙을 `--licenses`로 돌렸을 때만 있다. 인허가 데이터에 없는 점포는 값이 null.
필드명은 화면 더미(`docs/samples/w2-6_dummy/`, PR #37)와 같다. 공개용(`--mask`) 샘플의 인허가일은 연 단위("YYYY-01-01").

## risk 블록
| 필드 | 뜻 | 화면 처리 |
|---|---|---|
| `probability_12m` | 12개월 안 폐업 예측 확률 (0~1) | "%"로 표시 (예: 0.2394 → 23.9%) |
| `ci_low`, `ci_high` | 불확실성 구간 | **문구 고정**: "예측이 흔들릴 수 있는 범위". "폐업 확률의 범위"라고 쓰지 않는다 (`interval_note`) |
| `band` | `low` / `mid` / `high` | 표시명 예: 낮음 / 주의 / 높음. 기준: mid ≥ {cut_mid:.1%}, high ≥ {cut_high:.1%} (평균 폐업률 {base:.1%}) |
| `percentile` | 같은 자치구·업종 점포 중 위험도 백분위 (0~100, 높을수록 위험) | "마포구 미용업 중 상위 N%" = 100 − percentile |
| `peer_group`, `peer_median` | 비교 집단 이름, 집단 중앙 위험도 | 막대/점으로 "우리 점포 vs 비슷한 점포" |
| `model`, `calibrated` | 모형 이름, 보정 여부 | 화면에 노출하지 않아도 됨 |

## factors[] 블록 (요인 진단)
| 필드 | 뜻 | 화면 처리 |
|---|---|---|
| `category` | 유형 4개: 사업체 구조 / 입지·수요(온라인 언급 포함) / 경쟁 / 비용 | 유형별 묶음 |
| `name`, `factor_id` | 요인 이름, 고정 id | W2-7 정책 연결 키는 `factor_id` |
| `contribution` | 예측 확률에 대한 기여 (확률 단위, +면 위험 증가) | "%p"로 표시. 모든 요인 합 + 기준값 = 예측 확률 |
| `direction` | 위험 증가 / 위험 감소 / **영향 미미** (기여 절댓값 < 0.001) | 색 구분. "영향 미미"는 중립 색 (기여 원값은 그대로) |
| `peer_percentile` | 같은 업종·자치구·업력대 점포 중 이 요인의 위험 기여 백분위 | 70 이상이면 설명문에 "상위 N%" 문장이 이미 들어 있음 |
| `actionability` | owner(사업자 직접) / policy(정책 지원) / external(외부 환경) | 처방·정책 연결 여부 판단 |
| `explanation` | 화면용 설명문 (인과 표현 없음) | 그대로 출력 |
| `driver` | 온라인 요인의 주된 근거 (다른 요인은 null) | 설명문에 이미 포함 |
| `values` | 판단에 쓴 원래 값 | "근거 데이터 보기" 펼침 영역 (선택) |
| `display` | **false면 진단문으로 내보내지 않는 요인** | 숨기거나 회색 처리. 기여값은 합계에 포함되어 있음 |
| `hold_reason` | display=false인 이유 코드 (display=true면 null) | 아래 표 — 화면 분기는 이 값으로 |
| `data_missing` | display=false의 이유가 "데이터 없음"인지 (`hold_reason == "data_missing"`과 같음) | |
| `missing_reason` | 데이터 없음의 세부 사유 코드 (data_missing=false면 null) | 아래 표 |
| `display_note` | 보류 이유 (내부용 문장) | 화면에는 아래 권장 문구 사용 |

### display=false 두 종류
| 경우 | 조건 | 권장 화면 처리 |
|---|---|---|
| 데이터 없음 | `hold_reason="data_missing"` (`data_missing=true`) | 회색 "데이터 없음" 배지 + `explanation` (예: "…데이터가 없어(상권 경계 밖) 이 요인은 진단하지 않습니다.") |
| 검토 대기 | `hold_reason="online_review"` (현재 온라인 요인만) | 숨김. 블로그 언급이 많은 쪽에서 위험이 높게 나온 경우로, 상호 오탐 검수(#28) 전까지 보류 |

`hold_reason` 코드는 보류 사유가 늘면 추가된다 (코드 목록: `src/models/diagnose.py` `HOLD_REASONS`).

### `missing_reason` 코드
| 코드 | 사유 (explanation 괄호 안 문구) | 대상 요인 |
|---|---|---|
| `out_of_trdar` | 상권 경계 밖 | 상권 요인 4개 (trdar_population·trdar_vitality·peer_competition·peer_sales) |
| `sales_unpublished` | 해당 상권에 이 업종 매출 공개 자료 없음 | peer_sales (상권 안) |
| `industry_unpublished` | 해당 상권에 이 업종 자료 없음 | peer_competition (상권 안) |
| `trdar_quarter_unavailable` | 해당 분기 상권 자료 없음 | trdar_population·trdar_vitality (상권 안) |
| `online_unobservable` | 관측 불가 | online_attention |
| `trdar_unknown` | 상권 데이터 없음 (입력에 상권 코드가 없어 상권 밖인지 판단 불가) | 상권 요인 |
| `unknown` | 데이터 없음 (예비값) | 그 밖의 요인 |

### 기타 규칙
- `unavailable_categories`에 있는 유형(현재 "비용")은 **0이 아니라 "판단 불가"**로 표시. 0%p로 그리지 않는다.
- 요인 기여는 예측모형의 변수 기여도이며 원인이 아님 → `disclaimer` 문구를 진단 화면 하단에 항상 표시.
- 기여 크기가 0.1%p 미만(기여 절댓값 < 0.001)인 요인은 `direction`이 "영향 미미", 설명문이 "거의 영향을 주지 않았습니다"로
  나온다. 목록에서 접어도 됨. (샘플 경우 `no_standout`의 1%p 기준과는 다른, 요인 하나의 방향 표시 기준이다.)

## 샘플에 들어 있는 경우
{case_table}

## 미정 사항 (팀 확인)
- 상호·주소: store_id로 인허가 데이터에서 붙임 (서빙 --licenses). W2-5 스키마의 store 필드 반영 필요. 좌표는 아직 없음
- 처방(W2-4)·정책(W2-7) 블록: 이 샘플에는 없음. `factor_id`로 연결 예정 (PR #34 코멘트)
"""

CASE_DESC = {
    "band_high": "등급 높음 대표", "band_mid": "등급 주의 대표", "band_low": "등급 낮음 대표",
    "top_risk": "전체 최고 위험", "near_cut_high": "높음 컷오프 바로 위",
    "wide_interval": "불확실성 구간이 가장 넓음",
    "online_hold": "온라인 요인 검토 대기 (display=false, data_missing=false)",
    "online_decline": "온라인 언급 감소·끊김",
    "online_absent": "온라인 언급 이력 없음",
    "missing_outside_trdar": "상권 경계 밖 — 상권 요인 4개 데이터 없음",
    "missing_sales_only": "상권 안, 매출 요인만 데이터 없음",
    "missing_online": "온라인 관측 불가",
    "peer_top": "동종 점포 대비 상위 5% 이내 위험 요인 보유",
    "no_standout": "눈에 띄는 위험 요인 없음 (가장 큰 위험 기여 < 1%p)",
}


def attach_store_meta(recs: list[dict], licenses_path: Path) -> int:
    """store 블록에 이름·주소를 붙인다. 이미 name이 있는 레코드(서빙 --licenses)는 건드리지 않는다. 반환: 붙인 수."""
    from src.models import serve  # 모형 모듈 import는 이 옵션을 쓸 때만

    todo = [r for r in recs if "name" not in r["store"]]
    if not todo:
        return 0
    meta = serve.store_meta([r["store_id"] for r in todo], licenses_path)
    for r in todo:
        r["store"].update(meta[r["store_id"]])
    return len(todo)


def run(serve_dir: Path, out_dir: Path | None = None, per_band: int = 2,
        licenses_path: Path | None = None, mask: bool = False) -> pd.DataFrame:
    out_dir = out_dir or serve_dir / "sample"
    if _is_under_docs(out_dir) and not mask:
        raise ValueError(f"docs/ 아래({out_dir})에는 가린 샘플만 쓴다 — --mask를 붙인다 (공개 저장소)")
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = load_reports(serve_dir / "reports.jsonl")
    meta = {}
    if (serve_dir / "serve_meta.json").exists():
        meta = json.loads((serve_dir / "serve_meta.json").read_text(encoding="utf-8"))
    cut = meta.get("band_cutoffs", {})
    df = summary_frame(recs)
    picked = select(df, cut.get("cut_high"), per_band=per_band)
    order = sorted(picked, key=lambda i: -recs[i]["risk"]["probability_12m"])
    if licenses_path is not None:
        n = attach_store_meta([recs[i] for i in order], licenses_path)
        print(f"가게 이름·주소 결합: {n}점포 (이미 있던 레코드는 그대로)")
    chosen = [recs[i] for i in order]
    cases = [picked[i] for i in order]
    if mask:
        chosen = mask_records(chosen)
        print(f"공개용 가림: store_id·상호·주소·동 → SAMPLE-001~{len(chosen):03d}")

    with open(out_dir / "sample_reports.jsonl", "w", encoding="utf-8") as f:
        for r in chosen:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    pretty = [{**r, "_sample_cases": c} for r, c in zip(chosen, cases)]
    (out_dir / "sample_reports.json").write_text(json.dumps(pretty, ensure_ascii=False, indent=2), encoding="utf-8")

    idx = pd.DataFrame([{
        "store_id": r["store_id"], "name": r["store"].get("name"),
        "cases": ";".join(c), "band": r["risk"]["band"],
        "probability_12m": r["risk"]["probability_12m"], "gu": r["store"]["gu"],
        "biz_type": r["store"]["biz_type"], "summary": one_line(r),
    } for r, c in zip(chosen, cases)])
    idx.to_csv(out_dir / "sample_index.csv", index=False, encoding="utf-8-sig")

    counts = {c: int(df[c].sum()) for c in CASE_DESC if c in df.columns}
    covered = {c for i in order for c in picked[i]}
    rows = ["| 경우 | 설명 | 샘플 수 | 전체 해당 점포 |", "|---|---|---|---|"]
    for c, d in CASE_DESC.items():
        n = sum(c in picked[i] for i in order)
        rows.append(f"| `{c}` | {d} | {n} | {counts.get(c, '—')} |")
    missing_cases = [c for c in CASE_DESC if c not in covered]
    if missing_cases:
        rows.append(f"\n해당 점포가 없어 빠진 경우: {', '.join(missing_cases)}")
    (out_dir / "README_W2-6.md").write_text(README.format(
        mask_note=MASK_NOTE if mask else "",
        serve_dir=serve_dir, as_of=recs[0]["as_of"] if recs else "", n=len(order),
        cut_mid=cut.get("cut_mid", float("nan")), cut_high=cut.get("cut_high", float("nan")),
        base=cut.get("base_rate", float("nan")), case_table="\n".join(rows)), encoding="utf-8")
    print(f"샘플 {len(order)}점포 → {out_dir}")
    if missing_cases:
        print(f"해당 점포가 없어 빠진 경우: {missing_cases}")
    return idx


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-6 화면 개발용 샘플 추출")
    ap.add_argument("--serve-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--per-band", type=int, default=2)
    ap.add_argument("--licenses", type=Path, default=None, help="인허가 표준화 테이블 — store 블록에 이름·주소")
    ap.add_argument("--mask", action="store_true",
                    help="store_id·상호·주소·동을 가린다 (docs/ 등 공개 저장소에 쓸 때 필수)")
    a = ap.parse_args(argv)
    run(a.serve_dir, a.out, a.per_band, licenses_path=a.licenses, mask=a.mask)


if __name__ == "__main__":
    main()
