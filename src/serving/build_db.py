"""W2-5 SQLite 정본 빌더.

서빙 출력(PR #36 `reports.jsonl` + `serve_meta.json`)과 인허가 표준화 테이블, 선택 입력(W2-7 정책 원천,
온라인 존재감 스냅샷)을 합쳐 로컬 정본 SQLite를 만든다. 계약: `docs/REPORT_SCHEMA.md`.

- 모델 코드(`src/models/`)는 import하지 않는다. 입력은 `reports.jsonl` 형식에만 의존한다 (DECISIONS 2026-09-26 D9).
- 입력 검증(serve 출력 0.2 = `serve_record_v0_2`, 구버전 0.1 = `serve_record_v0_1`)과 최종 리포트 0.2 검증을 분리한다.
  serve 0.2와 최종 리포트 0.2는 버전 번호만 같고 다른 구조다. serve 0.2의 missing_reason·hold_reason 코드는 그대로 보존한다.
  구버전 0.1 입력에는 이 값들과 '영향 미미'가 없다 — 설명문에서 추측하지 않고 NULL로 두며
  그 실행은 `runs.final_contract = 'not_ready'`로 기록한다 (정적 배포 불가).
- 임시 파일에 한 트랜잭션으로 쓰고 모든 검증을 통과한 뒤에만 정본 경로로 교체한다. 실패하면 기존 정본은 그대로다.
- 출력 경로가 저장소 안이면 git이 무시하는 경로여야 한다 (실제 점포 결과를 커밋하지 않는다, D2).
- `--purpose release`(공개 배포용)는 최종 0.2 검증 통과, 인허가 기준일 입력, 그 날짜가 원천 데이터갱신일자 최댓값 이후임을
  요구한다. 기본 `dev`는 개발·구조 확인용이며 공개 배포 대상이 아니다 (`release_blockers`).
- 이미 `final_contract = passed`인 정본은 통과하지 못한 재빌드로 덮어쓰지 않는다 (`--allow-downgrade`로만 허용).

실행:
    python -m src.serving.build_db --serve-dir outputs/serve/<run> \\
        [--licenses outputs/standardized/licenses_3gu.parquet] [--policies <policies.json>] \\
        [--online-presence <online_presence.jsonl>] [--license-snapshot-date YYYY-MM-DD] \\
        [--purpose dev|release] [--allow-downgrade] [--out outputs/serving/report.sqlite]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

from src.data import config
from src.serving import paths
from src.serving import report_validation as rv

BUILDER_VERSION = "w2-5-build-0.1"
# serve 출력 버전 → 입력 정의. serve 0.2 (PR #36 d6cfeb9, R1~R5 반영)만 최종 리포트 0.2가 된다
INPUT_DEFS = {"0.1": "serve_record_v0_1", "0.2": "serve_record_v0_2"}
FINAL_READY_INPUTS = {"0.2"}
NOT_READY_NOTE = ("구버전 serve 입력 0.1에는 missing_reason·hold_reason·'영향 미미'가 없다 (REPORT_SCHEMA R1~R3). "
                  "설명문에서 추측하지 않고 NULL로 두었으므로 최종 0.2 검증을 통과할 수 없다")
DEFAULT_OUT = config.REPO_ROOT / "outputs" / "serving" / "report.sqlite"
DEFAULT_LICENSES = config.OUTPUT_DIR / "licenses_3gu.parquet"
LICENSE_COLS = ["store_id", "business_type", "gu", "dong", "name_raw", "name_norm", "road_addr_raw", "addr_raw",
                "license_date", "close_date", "status_name"]
LICENSE_UPDATED_COL = "data_updated_raw"  # 인허가 원천 '데이터갱신일자' — 기준일 입력값의 하한 검사에 쓴다 (없으면 검사 생략)
PURPOSES = ("dev", "release")
# reports.jsonl store 필드 → 인허가 컬럼 (serve --licenses가 붙인 값이 있으면 일치해야 한다)
STORE_META_MAP = {"biz_type": "business_type", "gu": "gu", "name": "name_raw", "address_road": "road_addr_raw",
                  "address_jibun": "addr_raw", "dong": "dong", "license_date": "license_date"}
STATUS_NAME_CLOSED = "폐업"
MAX_ERRORS_SHOWN = 10

DDL = """
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  builder_version TEXT NOT NULL,
  output_schema_version TEXT NOT NULL,
  input_schema_version TEXT NOT NULL,
  score_origin TEXT NOT NULL,
  as_of TEXT NOT NULL,
  n_stores INTEGER NOT NULL,
  reports_file TEXT NOT NULL,
  reports_sha256 TEXT NOT NULL,
  serve_meta_sha256 TEXT NOT NULL,
  serve_meta_json TEXT NOT NULL,
  model TEXT,
  detect_run TEXT,
  band_cutoffs_json TEXT,
  licenses_sha256 TEXT NOT NULL,
  licenses_sha256_in_serve_meta TEXT,
  license_snapshot_date TEXT,
  license_snapshot_date_basis TEXT NOT NULL CHECK (license_snapshot_date_basis IN ('not_provided', 'user_supplied')),
  license_max_updated_date TEXT,
  license_snapshot_check TEXT NOT NULL CHECK (license_snapshot_check IN ('not_checked', 'consistent')),
  build_purpose TEXT NOT NULL CHECK (build_purpose IN ('dev', 'release')),
  policy_matching TEXT NOT NULL CHECK (policy_matching IN ('performed', 'not_performed')),
  policies_sha256 TEXT,
  n_policies INTEGER NOT NULL,
  online_presence_sha256 TEXT,
  n_online_presence INTEGER NOT NULL,
  n_online_presence_ignored INTEGER NOT NULL,
  final_contract TEXT NOT NULL CHECK (final_contract IN ('passed', 'not_ready')),
  final_contract_note TEXT,
  n_final_invalid INTEGER NOT NULL
);
CREATE TABLE stores (
  store_id TEXT PRIMARY KEY,
  biz_type TEXT NOT NULL,
  gu TEXT NOT NULL,
  dong TEXT,
  name TEXT,
  name_norm TEXT,
  address_road TEXT,
  address_jibun TEXT,
  license_date TEXT,
  mdis_industry_code TEXT,
  open_at_as_of INTEGER NOT NULL CHECK (open_at_as_of = 1),
  status_current TEXT NOT NULL CHECK (status_current IN ('open', 'closed', 'unknown')),
  close_date TEXT,
  unavailable_categories_json TEXT NOT NULL,
  disclaimer TEXT NOT NULL
);
CREATE TABLE risk (
  store_id TEXT PRIMARY KEY REFERENCES stores(store_id),
  probability_12m REAL NOT NULL,
  ci_low REAL NOT NULL,
  ci_high REAL NOT NULL,
  interval_note TEXT NOT NULL,
  band TEXT NOT NULL CHECK (band IN ('low', 'mid', 'high')),
  percentile INTEGER,
  peer_group TEXT NOT NULL,
  peer_median REAL NOT NULL,
  model TEXT NOT NULL,
  calibrated INTEGER NOT NULL
);
CREATE TABLE factors (
  store_id TEXT NOT NULL REFERENCES stores(store_id),
  factor_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  actionability TEXT NOT NULL,
  contribution REAL NOT NULL,
  direction TEXT NOT NULL,
  peer_percentile INTEGER,
  explanation TEXT NOT NULL,
  driver TEXT,
  values_json TEXT NOT NULL,
  display INTEGER NOT NULL,
  data_missing INTEGER NOT NULL,
  missing_reason TEXT,
  hold_reason TEXT,
  display_note TEXT,
  PRIMARY KEY (store_id, factor_id),
  UNIQUE (store_id, rank)
);
CREATE TABLE prescriptions (
  store_id TEXT NOT NULL REFERENCES stores(store_id),
  prescription_id TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  title TEXT NOT NULL,
  related_factor_ids_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status = 'unavailable'),
  unavailable_reason TEXT NOT NULL,
  evidence_level TEXT CHECK (evidence_level IS NULL),
  effect_value REAL CHECK (effect_value IS NULL),
  effect_summary TEXT CHECK (effect_summary IS NULL),
  source TEXT,
  caveat TEXT,
  actionability TEXT NOT NULL,
  PRIMARY KEY (store_id, prescription_id)
);
CREATE TABLE policies (
  policy_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  operator TEXT NOT NULL,
  link TEXT,
  announce_year INTEGER,
  collected_at TEXT NOT NULL,
  eligibility_text TEXT NOT NULL,
  conditions_json TEXT NOT NULL,
  unverifiable_conditions_json TEXT NOT NULL,
  related_factor_ids_json TEXT NOT NULL
);
CREATE TABLE store_policies (
  store_id TEXT NOT NULL REFERENCES stores(store_id),
  policy_id TEXT NOT NULL REFERENCES policies(policy_id),
  sort_order INTEGER NOT NULL,
  match_status TEXT NOT NULL CHECK (match_status IN ('matched', 'check_required')),
  matched_by_json TEXT NOT NULL,
  unverifiable_conditions_json TEXT NOT NULL,
  linked_factor_ids_json TEXT NOT NULL,
  check_note TEXT,
  PRIMARY KEY (store_id, policy_id)
);
CREATE TABLE online_presence (
  store_id TEXT PRIMARY KEY REFERENCES stores(store_id),
  basis TEXT NOT NULL CHECK (basis = 'current_snapshot'),
  collected_at TEXT NOT NULL,
  naver_local_registered INTEGER,
  kakao_registered INTEGER,
  naver_blog_total_12m INTEGER,
  first_date_truncated INTEGER,
  note TEXT NOT NULL
);
CREATE INDEX idx_stores_gu_dong ON stores (gu, dong, biz_type);
CREATE INDEX idx_stores_name_norm ON stores (name_norm);
CREATE INDEX idx_risk_band ON risk (band);
CREATE INDEX idx_factors_factor ON factors (factor_id);
CREATE INDEX idx_store_policies_policy ON store_policies (policy_id);
"""


class BuildError(RuntimeError):
    """입력 검증·결합 실패. 정본은 만들지 않는다."""


def _fail(title: str, errors: list[str]) -> None:
    if errors:
        shown = "\n  - ".join(errors[:MAX_ERRORS_SHOWN])
        more = f"\n  … 외 {len(errors) - MAX_ERRORS_SHOWN}건" if len(errors) > MAX_ERRORS_SHOWN else ""
        raise BuildError(f"{title} ({len(errors)}건)\n  - {shown}{more}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _plain(v):
    """pandas 값 → JSON/SQLite 값 (NaN·NaT → None, 날짜 → 'YYYY-MM-DD')."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return str(v.date())
    return v


def _b(v):
    return None if v is None else int(bool(v))


# ---------------------------------------------------------------------------
# 입력 읽기·검증
def read_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise BuildError(f"{path.name} {i}번째 줄 JSON 오류: {e}") from e
    return out


def validate_serve_input(records: list[dict], serve_meta: dict) -> str:
    """serve 입력 계약 검증. 반환: 입력 schema 버전."""
    if not records:
        raise BuildError("reports.jsonl이 비어 있다")
    versions = {r.get("_schema_version") for r in records}
    if len(versions) != 1 or next(iter(versions)) not in INPUT_DEFS:
        raise BuildError(f"serve 입력 schema 버전이 하나가 아니거나 지원하지 않는다: {sorted(map(str, versions))} "
                         f"(지원: {sorted(INPUT_DEFS)})")
    version = next(iter(versions))

    errs = []
    for i, r in enumerate(records, 1):
        errs += [f"{i}번째 줄 {r.get('store_id')}: {e}" for e in rv.validate_def(INPUT_DEFS[version], r)]
    _fail(f"serve 입력 스키마({INPUT_DEFS[version]}) 위반", errs)
    errs = [f"{r['store_id']}: {e}" for r in records for e in rv.online_driver_errors(r["factors"])]
    _fail("serve 입력의 온라인 driver 문구가 알려진 템플릿이 아니다 — 정책 연결 조건을 판정할 수 없다", errs)

    for key in ("score_origin", "as_of", "n_stores", "band_cutoffs"):
        if key not in serve_meta:
            raise BuildError(f"serve_meta.json에 {key}가 없다")
    validate_band_cutoffs(serve_meta["band_cutoffs"])
    s, as_of = serve_meta["score_origin"], serve_meta["as_of"]
    if as_of != rv.quarter_end(s):
        raise BuildError(f"serve_meta as_of {as_of}가 score_origin {s}의 분기 말일이 아니다")

    ids = pd.Series([r["store_id"] for r in records])
    dup = ids[ids.duplicated()].unique().tolist()
    _fail("reports.jsonl store_id 중복", [str(x) for x in dup])
    if len(records) != serve_meta["n_stores"]:
        raise BuildError(f"점포 수 불일치: reports.jsonl {len(records):,} ≠ serve_meta n_stores {serve_meta['n_stores']:,}")
    errs = [f"{r['store_id']}: as_of {r['as_of']} ≠ {as_of}" for r in records if r["as_of"] != as_of]
    errs += [f"{r['store_id']}: score_origin {r['score_origin']} ≠ {s}"
             for r in records if "score_origin" in r and r["score_origin"] != s]
    _fail("기준 시점 불일치", errs)
    return version


def validate_band_cutoffs(cut) -> None:
    """serve_meta.band_cutoffs = PR #36 train_detect의 `bands.suggest_cutoffs` 결과 ({cut_mid, cut_high, base_rate}).
    정본 이름은 cut_mid·cut_high다. 예전 합성 fixture의 {mid, high} 같은 다른 이름은 받지 않는다 (실제 생산자가 없다)."""
    errs = rv.validate_def("serve_band_cutoffs", cut)
    if not errs and not 0 < cut["cut_mid"] < cut["cut_high"] < 1:
        errs = [f"0 < cut_mid < cut_high < 1 위반: {cut['cut_mid']}, {cut['cut_high']}"]
    _fail("serve_meta.band_cutoffs 계약 위반 (PR #36 형식 {cut_mid, cut_high, base_rate})", errs)


def load_licenses(path: Path, store_ids: list[str]) -> tuple[pd.DataFrame, str | None]:
    """인허가 테이블 → (store_id 인덱스 테이블, 원천 데이터갱신일자 최댓값 'YYYY-MM-DD' 또는 None).
    테이블 자체의 store_id 유일성과 reports 점포 누락을 검사한다."""
    lic = pd.read_parquet(path)
    absent = [c for c in LICENSE_COLS if c not in lic.columns]
    if absent:
        raise BuildError(f"인허가 테이블에 필요한 컬럼이 없다: {absent}")
    max_updated = None
    if LICENSE_UPDATED_COL in lic.columns:
        upd = pd.to_datetime(lic[LICENSE_UPDATED_COL], errors="coerce").max()
        max_updated = None if pd.isna(upd) else str(upd.date())
    lic = lic[LICENSE_COLS]
    dup = lic.loc[lic["store_id"].duplicated(), "store_id"].unique().tolist()
    _fail("인허가 테이블 store_id 중복 — 1:1 결합 불가", [str(x) for x in dup])
    lic = lic.set_index("store_id")
    missing = sorted(set(store_ids) - set(lic.index))
    _fail("인허가 테이블에 없는 점포", missing)
    return lic.loc[store_ids], max_updated


def store_status(close_date, status_name) -> str:
    if close_date is not None:
        return "closed"
    if status_name == STATUS_NAME_CLOSED:
        return "unknown"  # 상태명은 폐업인데 폐업일자가 없다 (DECISIONS 2026-09-26 D6)
    return "open"


def build_store_rows(records: list[dict], lic: pd.DataFrame, as_of: str) -> list[dict]:
    rows, errs = [], []
    lic_rows = lic.to_dict("index")
    for r in records:
        sid, rs = r["store_id"], r["store"]
        L = {c: _plain(v) for c, v in lic_rows[sid].items()}
        for key, col in STORE_META_MAP.items():
            if rs.get(key) is not None and rs[key] != L[col]:
                errs.append(f"{sid}: store.{key} '{rs[key]}' ≠ 인허가 {col} '{L[col]}'")
        if L["license_date"] is not None and L["license_date"] > as_of:
            errs.append(f"{sid}: 인허가일 {L['license_date']}이 as_of {as_of} 이후")
        if L["close_date"] is not None and L["close_date"] <= as_of:
            errs.append(f"{sid}: 폐업일 {L['close_date']}이 as_of {as_of} 이전 — as_of 당시 영업 점포가 아니다")
        rows.append({
            "store_id": sid, "biz_type": rs["biz_type"], "gu": rs["gu"], "dong": L["dong"], "name": L["name_raw"],
            "name_norm": L["name_norm"], "address_road": L["road_addr_raw"], "address_jibun": L["addr_raw"],
            "license_date": L["license_date"], "mdis_industry_code": None, "open_at_as_of": 1,
            "status_current": store_status(L["close_date"], L["status_name"]), "close_date": L["close_date"],
            "unavailable_categories_json": _j(r["unavailable_categories"]), "disclaimer": r["disclaimer"],
        })
    _fail("점포 정보·기준 시점 검증 실패", errs)
    return rows


def load_policy_source(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "policies" in data:
        data = data["policies"]
    if not isinstance(data, list):
        raise BuildError("정책 원천은 정책 배열(또는 {'policies': [...]})이어야 한다")
    errs = []
    for i, p in enumerate(data):
        errs += [f"{i}번째 정책 {p.get('id') if isinstance(p, dict) else ''}: {e}"
                 for e in rv.validate_def("policy_source", p)]
    _fail("정책 원천 스키마(policy_source) 위반", errs)
    ids = pd.Series([p["id"] for p in data])
    _fail("정책 id 중복", ids[ids.duplicated()].tolist())
    errs = [p["id"] for p in data
            if None not in (p["conditions"]["tenure_months_min"], p["conditions"]["tenure_months_max"])
            and p["conditions"]["tenure_months_min"] > p["conditions"]["tenure_months_max"]]
    _fail("업력 조건 min > max", errs)
    return data


def load_online_presence(path: Path, store_ids: set[str]) -> tuple[dict[str, dict], int]:
    """→ ({store_id: online_presence}, reports에 없는 점포라 무시한 행 수)."""
    rows = read_jsonl(path)
    errs = []
    for i, r in enumerate(rows, 1):
        errs += [f"{i}번째 줄: {e}" for e in rv.validate_def("online_presence_source", r)]
    _fail("온라인 존재감 입력(online_presence_source) 위반", errs)
    ids = pd.Series([r["store_id"] for r in rows], dtype=object)
    _fail("온라인 존재감 store_id 중복", ids[ids.duplicated()].unique().tolist())
    kept = {r["store_id"]: r["online_presence"] for r in rows if r["store_id"] in store_ids}
    return kept, len(rows) - len(kept)


# ---------------------------------------------------------------------------
# 정책 매칭 (FACTOR_POLICY_LINKS.md §1·§3·§4, PR #38)
def tenure_months(license_date: str, as_of: str) -> int:
    """업력(개월) = as_of와 인허가일의 연·월 차이 (라벨·master의 age_months와 같은 식)."""
    a, l = pd.Timestamp(as_of), pd.Timestamp(license_date)
    return (a.year - l.year) * 12 + (a.month - l.month)


def match_policies(store: dict, factors: list[dict], policies: list[dict], as_of: str) -> list[dict]:
    """자격 매칭 + 요인 연결. 데이터에 없는 자격정보는 추정하지 않고 check_required로 둔다."""
    linkable = rv.policy_linkable_factors(factors)  # 온라인 요인은 driver가 노출 부족일 때만 (PR #38 §2)
    out = []
    for p in policies:
        c = p["conditions"]
        matched_by, unverifiable = [], list(p["unverifiable_conditions"])
        if c["gu"] is not None:
            if store["gu"] not in c["gu"]:
                continue
            matched_by.append("gu")
        if c["biz_type"] is not None:
            if store["biz_type"] not in c["biz_type"]:
                continue
            matched_by.append("biz_type")
        if c["tenure_months_min"] is not None or c["tenure_months_max"] is not None:
            if store["license_date"] is None:
                unverifiable.append("업력 조건 (인허가일 정보 없음)")
            else:
                m = tenure_months(store["license_date"], as_of)
                if c["tenure_months_min"] is not None and m < c["tenure_months_min"]:
                    continue
                if c["tenure_months_max"] is not None and m > c["tenure_months_max"]:
                    continue
                matched_by.append("tenure")
        linked = [fid for fid in p["related_factor_ids"] if fid in linkable]
        status = "check_required" if unverifiable else "matched"
        out.append({
            "policy_id": p["id"], "match_status": status, "matched_by": matched_by,
            "unverifiable_conditions": unverifiable, "linked_factor_ids": linked,
            "check_note": ("데이터로 확인할 수 없는 조건이 있어 자동 매칭하지 않음: " + ", ".join(unverifiable))
                          if unverifiable else None,
            "_key": (0 if linked else 1, -max((linkable[f] for f in linked), default=0.0),
                     0 if status == "matched" else 1, p["id"]),
        })
    out.sort(key=lambda r: r["_key"])
    for i, r in enumerate(out):
        r["sort_order"] = i
        del r["_key"]
    return out


# ---------------------------------------------------------------------------
# 최종 0.2 레코드 조립 (SQLite → dict). export(다음 단계)도 이 함수를 쓴다.
def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def read_run(conn) -> dict:
    runs = _rows(conn, "SELECT * FROM runs")
    if len(runs) != 1:
        raise BuildError(f"runs 행이 1개가 아니다: {len(runs)}")
    return runs[0]


def assemble_report(conn, store_id: str, run: dict | None = None) -> dict:
    run = run or read_run(conn)
    s = _rows(conn, "SELECT * FROM stores WHERE store_id = ?", (store_id,))[0]
    k = _rows(conn, "SELECT * FROM risk WHERE store_id = ?", (store_id,))[0]
    factors = [{
        "factor_id": f["factor_id"], "name": f["name"], "category": f["category"],
        "actionability": f["actionability"], "contribution": f["contribution"], "direction": f["direction"],
        "peer_percentile": f["peer_percentile"], "explanation": f["explanation"], "driver": f["driver"],
        "values": json.loads(f["values_json"]), "display": bool(f["display"]),
        "data_missing": bool(f["data_missing"]), "missing_reason": f["missing_reason"],
        "hold_reason": f["hold_reason"], "display_note": f["display_note"],
    } for f in _rows(conn, "SELECT * FROM factors WHERE store_id = ? ORDER BY rank", (store_id,))]
    prescriptions = [{
        "id": p["prescription_id"], "title": p["title"], "related_factor_ids": json.loads(p["related_factor_ids_json"]),
        "status": p["status"], "unavailable_reason": p["unavailable_reason"], "evidence_level": p["evidence_level"],
        "effect_value": p["effect_value"], "effect_summary": p["effect_summary"], "source": p["source"],
        "caveat": p["caveat"], "actionability": p["actionability"],
    } for p in _rows(conn, "SELECT * FROM prescriptions WHERE store_id = ? ORDER BY sort_order", (store_id,))]
    op = _rows(conn, "SELECT * FROM online_presence WHERE store_id = ?", (store_id,))
    online = None
    if op:
        o = op[0]
        online = {"basis": o["basis"], "collected_at": o["collected_at"],
                  "naver_local_registered": None if o["naver_local_registered"] is None else bool(o["naver_local_registered"]),
                  "kakao_registered": None if o["kakao_registered"] is None else bool(o["kakao_registered"]),
                  "naver_blog_total_12m": o["naver_blog_total_12m"],
                  "first_date_truncated": None if o["first_date_truncated"] is None else bool(o["first_date_truncated"]),
                  "note": o["note"]}
    policies = [{
        "id": p["policy_id"], "name": p["name"], "operator": p["operator"], "link": p["link"],
        "eligibility_text": p["eligibility_text"], "announce_year": p["announce_year"],
        "collected_at": p["collected_at"], "match_status": p["match_status"],
        "matched_by": json.loads(p["matched_by_json"]),
        "unverifiable_conditions": json.loads(p["sp_unverifiable"]),
        "linked_factor_ids": json.loads(p["linked_factor_ids_json"]), "check_note": p["check_note"],
    } for p in _rows(conn, """
        SELECT sp.*, sp.unverifiable_conditions_json AS sp_unverifiable, p.name, p.operator, p.link,
               p.eligibility_text, p.announce_year, p.collected_at
        FROM store_policies sp JOIN policies p USING (policy_id)
        WHERE sp.store_id = ? ORDER BY sp.sort_order""", (store_id,))]
    return {
        "_schema_version": rv.SCHEMA_VERSION, "store_id": store_id,
        "score_origin": run["score_origin"], "as_of": run["as_of"],
        "store": {
            "biz_type": s["biz_type"], "gu": s["gu"], "dong": s["dong"], "name": s["name"],
            "address_road": s["address_road"], "address_jibun": s["address_jibun"],
            "license_date": s["license_date"], "mdis_industry_code": s["mdis_industry_code"],
            "status": {"open_at_as_of": bool(s["open_at_as_of"]), "current": s["status_current"],
                       "close_date": s["close_date"], "license_snapshot_date": run["license_snapshot_date"]},
        },
        "risk": {"probability_12m": k["probability_12m"], "ci_low": k["ci_low"], "ci_high": k["ci_high"],
                 "interval_note": k["interval_note"], "band": k["band"], "percentile": k["percentile"],
                 "peer_group": k["peer_group"], "peer_median": k["peer_median"], "model": k["model"],
                 "calibrated": bool(k["calibrated"])},
        "factors": factors,
        "unavailable_categories": json.loads(s["unavailable_categories_json"]),
        "prescriptions": prescriptions,
        "online_presence": online,
        "policy_matching": run["policy_matching"],
        "policies": policies,
        "disclaimer": s["disclaimer"],
    }


def iter_reports(conn):
    run = read_run(conn)
    for (sid,) in conn.execute("SELECT store_id FROM stores ORDER BY rowid"):
        yield assemble_report(conn, sid, run)


# ---------------------------------------------------------------------------
def guard_output_path(out: Path) -> None:
    """정본(및 search_index·dong_summary JSON) 출력 경로 방어 (실제 점포 결과 커밋 방지).
    이 저장소든 다른 git 작업 트리든: 공개·문서 디렉터리(docs·app·public·dist·site·www) 거부, git 무시 경로만 허용.
    git 작업 트리 밖은 허용 (`src/serving/paths.py`)."""
    try:
        paths.check_private_output(out)
    except paths.UnsafeOutputPath as e:
        raise BuildError(str(e)) from e


def release_blockers(run: dict) -> list[str]:
    """이 정본으로 공개 배포 산출물을 만들 수 없는 이유 (빈 목록이면 공개 가능)."""
    out = []
    if run["final_contract"] != "passed":
        out.append(f"최종 0.2 계약 미통과 (final_contract={run['final_contract']})")
    if run["build_purpose"] != "release":
        out.append(f"개발용 빌드 (build_purpose={run['build_purpose']})")
    if run["license_snapshot_check"] != "consistent":
        out.append(f"인허가 기준일 미확인 (basis={run['license_snapshot_date_basis']}, "
                   f"check={run['license_snapshot_check']})")
    return out


def _existing_final_contract(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT final_contract FROM runs").fetchone()[0]
        finally:
            conn.close()
    except (sqlite3.Error, TypeError) as e:
        raise BuildError(f"기존 정본을 읽을 수 없어 덮어쓰지 않는다: {path} ({e})") from e


def build(reports_path: Path, serve_meta_path: Path, licenses_path: Path, out_path: Path, *,
          policies_path: Path | None = None, online_presence_path: Path | None = None,
          license_snapshot_date: str | None = None, purpose: str = "dev", allow_downgrade: bool = False) -> dict:
    """정본을 만들고 runs 행(dict)을 돌려준다. 실패하면 BuildError이며 기존 정본은 바뀌지 않는다."""
    out_path = Path(out_path)
    guard_output_path(out_path)
    if purpose not in PURPOSES:
        raise BuildError(f"purpose는 {PURPOSES} 중 하나: {purpose}")
    if purpose == "release" and license_snapshot_date is None:
        raise BuildError("공개 배포용 빌드(--purpose release)에는 --license-snapshot-date가 필요하다")
    if license_snapshot_date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", license_snapshot_date):
        raise BuildError(f"--license-snapshot-date 형식 오류 (YYYY-MM-DD): {license_snapshot_date}")

    records = read_jsonl(reports_path)
    serve_meta = json.loads(Path(serve_meta_path).read_text(encoding="utf-8"))
    version = validate_serve_input(records, serve_meta)
    as_of, score_origin = serve_meta["as_of"], serve_meta["score_origin"]
    store_ids = [r["store_id"] for r in records]
    position = {sid: i for i, sid in enumerate(store_ids)}

    lic, max_updated = load_licenses(licenses_path, store_ids)
    # 기준일 provenance: '입력했다'(basis)와 '원천과 맞는지 확인했다'(check)를 따로 남긴다.
    # 원천 파일의 실제 수령일은 parquet에 없어 검증할 수 없다 — 확인하는 것은 입력일 ≥ 데이터갱신일자 최댓값뿐이다.
    basis = "user_supplied" if license_snapshot_date is not None else "not_provided"
    check = "not_checked"
    if license_snapshot_date is not None and max_updated is not None:
        if license_snapshot_date < max_updated:
            raise BuildError(f"인허가 기준일 {license_snapshot_date}이 원천 데이터갱신일자 최댓값 {max_updated}보다 이르다")
        check = "consistent"
    if purpose == "release" and check != "consistent":
        raise BuildError("공개 배포용 빌드는 인허가 기준일을 원천 데이터갱신일자와 대조할 수 있어야 한다 "
                         f"({LICENSE_UPDATED_COL} 컬럼 없음)")
    store_rows = build_store_rows(records, lic, as_of)
    stores_by_id = {s["store_id"]: s for s in store_rows}
    policies = load_policy_source(policies_path) if policies_path is not None else []
    online, n_online_ignored = (load_online_presence(online_presence_path, set(store_ids))
                                if online_presence_path is not None else ({}, 0))

    input_hashes = {"reports": sha256(reports_path), "serve_meta": sha256(serve_meta_path),
                    "licenses": sha256(licenses_path),
                    "policies": sha256(policies_path) if policies_path is not None else None,
                    "online_presence": sha256(online_presence_path) if online_presence_path is not None else None}
    run_id = hashlib.sha256(_j({"builder": BUILDER_VERSION, "schema": rv.SCHEMA_VERSION, **input_hashes,
                                "license_snapshot_date": license_snapshot_date,
                                "purpose": purpose}).encode()).hexdigest()[:16]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f".{out_path.name}.tmp-{os.getpid()}")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:  # 한 트랜잭션 — 예외가 나면 롤백
            conn.executescript("BEGIN;" + DDL)
            cols = list(store_rows[0])
            conn.executemany(f"INSERT INTO stores ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [tuple(s[c] for c in cols) for s in store_rows])
            for r in records:
                k, sid = r["risk"], r["store_id"]
                conn.execute("INSERT INTO risk VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (sid, k["probability_12m"], k["ci_low"], k["ci_high"], k["interval_note"], k["band"],
                              k["percentile"], k["peer_group"], k["peer_median"], k["model"], int(k["calibrated"])))
                conn.executemany(
                    "INSERT INTO factors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(sid, f["factor_id"], i, f["name"], f["category"], f["actionability"], f["contribution"],
                      f["direction"], f["peer_percentile"], f["explanation"], f["driver"], _j(f["values"]),
                      int(f["display"]), int(f["data_missing"]),
                      f.get("missing_reason"), f.get("hold_reason"),  # 0.1 입력에는 키가 없다 → NULL (추측 금지)
                      f["display_note"]) for i, f in enumerate(r["factors"])])
            conn.executemany(
                "INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(p["id"], p["name"], p["operator"], p["link"], p["announce_year"], p["collected_at"],
                  p["eligibility_text"], _j(p["conditions"]), _j(p["unverifiable_conditions"]),
                  _j(p["related_factor_ids"])) for p in policies])
            if policies:
                for r in records:
                    for m in match_policies(stores_by_id[r["store_id"]], r["factors"], policies, as_of):
                        conn.execute("INSERT INTO store_policies VALUES (?,?,?,?,?,?,?,?)",
                                     (r["store_id"], m["policy_id"], m["sort_order"], m["match_status"],
                                      _j(m["matched_by"]), _j(m["unverifiable_conditions"]),
                                      _j(m["linked_factor_ids"]), m["check_note"]))
            conn.executemany(
                "INSERT INTO online_presence VALUES (?,?,?,?,?,?,?,?)",
                [(sid, o["basis"], o["collected_at"], _b(o["naver_local_registered"]), _b(o["kakao_registered"]),
                  o["naver_blog_total_12m"], _b(o["first_date_truncated"]), o["note"])
                 for sid, o in sorted(online.items(), key=lambda kv: position[kv[0]])])

            run = {
                "run_id": run_id, "builder_version": BUILDER_VERSION, "output_schema_version": rv.SCHEMA_VERSION,
                "input_schema_version": version, "score_origin": score_origin, "as_of": as_of,
                "n_stores": len(records), "reports_file": Path(reports_path).name,
                "reports_sha256": input_hashes["reports"], "serve_meta_sha256": input_hashes["serve_meta"],
                "serve_meta_json": _j(serve_meta), "model": records[0]["risk"]["model"],
                "detect_run": serve_meta.get("detect_run"),
                "band_cutoffs_json": _j(serve_meta["band_cutoffs"]),  # serve 원문 그대로 (base_rate 포함)
                "licenses_sha256": input_hashes["licenses"],
                "licenses_sha256_in_serve_meta": serve_meta.get("licenses_sha256"),
                "license_snapshot_date": license_snapshot_date, "license_snapshot_date_basis": basis,
                "license_max_updated_date": max_updated, "license_snapshot_check": check,
                "build_purpose": purpose,
                "policy_matching": "performed" if policies_path is not None else "not_performed",
                "policies_sha256": input_hashes["policies"], "n_policies": len(policies),
                "online_presence_sha256": input_hashes["online_presence"], "n_online_presence": len(online),
                "n_online_presence_ignored": n_online_ignored,
                "final_contract": "not_ready", "final_contract_note": None, "n_final_invalid": 0,
            }
            conn.execute(f"INSERT INTO runs ({','.join(run)}) VALUES ({','.join('?' * len(run))})", tuple(run.values()))

            # 최종 0.2 검증 — 정본에서 다시 조립한 레코드로 한다
            invalid = {}
            for rec in iter_reports(conn):
                errs = rv.validate_report(rec)
                if errs:
                    invalid[rec["store_id"]] = errs
            if version in FINAL_READY_INPUTS:
                _fail("최종 0.2 리포트 검증 실패", [f"{sid}: {e[0]}" for sid, e in invalid.items()])
                final, note = "passed", None
            else:
                final, note = "not_ready", NOT_READY_NOTE
            conn.execute("UPDATE runs SET final_contract = ?, final_contract_note = ?, n_final_invalid = ?",
                         (final, note, len(invalid)))
            run.update(final_contract=final, final_contract_note=note, n_final_invalid=len(invalid))
            if purpose == "release" and final != "passed":
                raise BuildError(f"공개 배포용 빌드는 최종 0.2 검증 통과가 필요하다 — {note}")
        conn.close()
        existing = _existing_final_contract(out_path)
        if existing == "passed" and final != "passed" and not allow_downgrade:
            raise BuildError(f"기존 정본({out_path.name})은 final_contract=passed다 — {final} 결과로 덮어쓰지 않는다 "
                             "(의도한 경우 --allow-downgrade 또는 다른 --out)")
        os.replace(tmp, out_path)
    except BaseException:
        conn.close()
        if tmp.exists():
            tmp.unlink()
        raise
    return run


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="W2-5 SQLite 정본 빌드")
    ap.add_argument("--serve-dir", type=Path, help="reports.jsonl·serve_meta.json이 있는 serve 출력 폴더")
    ap.add_argument("--reports", type=Path)
    ap.add_argument("--serve-meta", type=Path)
    ap.add_argument("--licenses", type=Path, default=DEFAULT_LICENSES)
    ap.add_argument("--policies", type=Path, default=None, help="W2-7 정책 원천 JSON (없으면 매칭하지 않음)")
    ap.add_argument("--online-presence", type=Path, default=None, help="online_presence_source JSONL")
    ap.add_argument("--license-snapshot-date", default=None, help="인허가 원천 파일 기준일 YYYY-MM-DD")
    ap.add_argument("--purpose", choices=PURPOSES, default="dev",
                    help="release = 공개 배포용 (최종 검증 통과·기준일 확인 필수), dev = 개발용")
    ap.add_argument("--allow-downgrade", action="store_true",
                    help="final_contract=passed 정본을 통과하지 못한 결과로 덮어쓰는 것을 허용")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    reports = a.reports or (a.serve_dir / "reports.jsonl" if a.serve_dir else None)
    meta = a.serve_meta or (a.serve_dir / "serve_meta.json" if a.serve_dir else None)
    if reports is None or meta is None:
        ap.error("--serve-dir 또는 --reports와 --serve-meta를 준다")
    run = build(reports, meta, a.licenses, a.out, policies_path=a.policies,
                online_presence_path=a.online_presence, license_snapshot_date=a.license_snapshot_date,
                purpose=a.purpose, allow_downgrade=a.allow_downgrade)
    print(f"정본 빌드 완료 → {a.out}")
    print(f"  run_id {run['run_id']} · score_origin {run['score_origin']} · 점포 {run['n_stores']:,} · "
          f"입력 schema {run['input_schema_version']}")
    print(f"  정책 매칭 {run['policy_matching']} (정책 {run['n_policies']}) · 온라인 존재감 {run['n_online_presence']:,}")
    print(f"  최종 0.2 계약: {run['final_contract']} (검증 실패 {run['n_final_invalid']:,})"
          + (f"\n  {run['final_contract_note']}" if run["final_contract_note"] else ""))
    blockers = release_blockers(run)
    print("  공개 배포: " + ("가능" if not blockers else "불가 — " + " / ".join(blockers)))


if __name__ == "__main__":
    main()
