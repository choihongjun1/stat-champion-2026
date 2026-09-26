# -*- coding: utf-8 -*-
"""W2-5 SQLite 정본 빌더 테스트 (합성 데이터).

점포·정책은 모두 지어낸 값이다. serve 입력은 PR #36 reports.jsonl 구버전 형식(0.1)과 현재 serve 출력 0.2(PR #36 d6cfeb9)를 흉내 낸다.
"""
import contextlib
import copy
import hashlib
import json
import sqlite3

import pandas as pd
import pytest

from src.data import config
from src.serving import build_db as bd
from src.serving import report_validation as rv

AS_OF = "2026-06-30"
A, B, C, X = "GR_3040000-101-2023-00001", "SR_3130000-104-2020-00002", "BT_3220000-215-2019-00003", \
    "GR_3040000-101-2010-00009"
INTERVAL_NOTE = "학습 데이터가 달랐다면 예측이 얼마나 흔들렸을지의 범위이며, 폐업 확률 자체의 범위가 아닙니다."
DISCLAIMER = "위험요인 기여도는 예측모형의 변수 기여도이며 인과적 원인이 아닙니다."


def _licenses(path):
    rows = [
        # store_id, business_type, gu, dong, name_raw, name_norm, road, jibun, license, close, status
        (A, "일반음식점", "광진구", "샘플동", "샘플식당", "샘플식당", "서울특별시 광진구 샘플로 1",
         "서울특별시 광진구 샘플동 1", "2023-01-15", None, "영업/정상"),
        (B, "휴게음식점", "마포구", "예시동", "예시카페", "예시카페", None, "서울특별시 마포구 예시동 2",
         "2020-05-01", "2026-08-14", "폐업"),
        (C, "미용업", "영등포구", None, "가상미용실", "가상미용실", None, None, None, None, "폐업"),
        (X, "일반음식점", "광진구", "샘플동", "옛식당", "옛식당", None, "서울특별시 광진구 샘플동 9",
         "2010-03-01", "2024-01-01", "폐업"),
    ]
    df = pd.DataFrame(rows, columns=bd.LICENSE_COLS)
    df["license_date"] = pd.to_datetime(df["license_date"])
    df["close_date"] = pd.to_datetime(df["close_date"])
    df.to_parquet(path, index=False)
    return path


def _f(fid, c, version, *, display=True, data_missing=False, missing_reason=None, hold_reason=None, driver=None):
    name, cat, act = rv.FACTOR_CONTRACT[fid]
    if version == "0.1":
        direction = "위험 증가" if c > 0 else "위험 감소"
    else:
        direction = rv.direction_of(c)
    f = {"category": cat, "name": name, "factor_id": fid, "contribution": c, "direction": direction,
         "peer_percentile": 60, "actionability": act,
         "explanation": (f"이 점포는 {name} 데이터가 없어(상권 경계 밖) 이 요인은 진단하지 않습니다." if data_missing
                         else f"{name} 요인이 예측 위험도를 약 {abs(c) * 100:.1f}%p 움직이는 쪽으로 기여했습니다."),
         "driver": driver, "display": display, "display_note": None if display else "내부 메모",
         "data_missing": data_missing, "values": {"x": 1.0}}
    if version != "0.1":
        f["missing_reason"] = missing_reason
        f["hold_reason"] = hold_reason or ("data_missing" if data_missing else None)
    return f


def _record(sid, gu, biz, p, band, factors, version, store_extra=None):
    rec = {"_schema_version": version, "store_id": sid, "as_of": AS_OF,
           "store": {"biz_type": biz, "gu": gu, **(store_extra or {})},
           "risk": {"probability_12m": p, "ci_low": round(p - 0.03, 4), "ci_high": round(p + 0.03, 4),
                    "interval_note": INTERVAL_NOTE, "band": band, "percentile": 70,
                    "peer_group": f"{gu} {biz}", "peer_median": 0.12, "model": "detect_v0_enriched",
                    "calibrated": False},
           "factors": factors, "unavailable_categories": ["비용"], "disclaimer": DISCLAIMER}
    if version != "0.1":
        rec["score_origin"] = "2026Q2"
    return rec


def _records(version):
    v = version
    return [
        _record(A, "광진구", "일반음식점", 0.15, "mid", [
            _f("online_attention", 0.03, v, driver="마지막 블로그 언급 후 14개월"),
            _f("tenure", 0.01, v), _f("district", 0.0004, v), _f("store_profile", -0.005, v)], v,
            store_extra={"name": "샘플식당", "address_road": "서울특별시 광진구 샘플로 1", "dong": "샘플동",
                         "license_date": "2023-01-15"}),
        _record(B, "마포구", "휴게음식점", 0.22, "high", [
            _f("online_attention", 0.05, v, display=False, hold_reason="online_review",
               driver="최근 12개월 블로그 언급 90건"),
            _f("trdar_population", 0.006, v, display=False, data_missing=True, missing_reason="out_of_trdar"),
            _f("tenure", 0.002, v), _f("store_profile", -0.01, v)], v,
            store_extra={"name": None, "address_road": None}),
        _record(C, "영등포구", "미용업", 0.09, "low", [
            _f("tenure", 0.02, v), _f("store_profile", 0.01, v), _f("district", -0.002, v)], v),
    ]


def _policies():
    def pol(pid, *, gu=None, biz=None, tmin=None, tmax=None, unverifiable=(), related=()):
        return {"id": pid, "name": f"(예시) {pid}", "operator": "(예시) 운영기관", "link": "https://example.org",
                "announce_year": 2026, "collected_at": "2026-09-20", "eligibility_text": "(예시) 조건",
                "conditions": {"gu": gu, "biz_type": biz, "tenure_months_min": tmin, "tenure_months_max": tmax},
                "unverifiable_conditions": list(unverifiable), "related_factor_ids": list(related)}
    return [
        pol("p_online", biz=["일반음식점", "휴게음식점", "미용업"], tmin=12, related=["online_attention"]),
        pol("p_rent", unverifiable=["연매출 3억 원 이하"], related=["rent_level"]),
        pol("p_young", tmax=12),
        pol("p_gu", gu=["광진구"], related=["tenure", "store_profile"]),
    ]


def _online():
    op = {"basis": "current_snapshot", "collected_at": "2026-09-20", "naver_local_registered": True,
          "kakao_registered": None, "naver_blog_total_12m": 3, "first_date_truncated": False,
          "note": "현재 시점 스냅샷 — 표시 전용"}
    return [{"store_id": A, "online_presence": op},
            {"store_id": X, "online_presence": op}]  # 리포트 대상이 아닌 점포 → 무시


@pytest.fixture
def inputs(tmp_path):
    def make(version="0.1", records=None, meta_update=None, policies=False, online=False):
        d = tmp_path / f"in_{version}_{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        recs = records if records is not None else _records(version)
        (d / "reports.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                                         encoding="utf-8")
        meta = {"score_origin": "2026Q2", "as_of": AS_OF, "n_stores": len(recs), "detect_run": "detect_v0_enriched",
                "band_cutoffs": {"mid": 0.1493, "high": 0.2142}, "licenses_sha256": "0" * 64,
                **(meta_update or {})}
        (d / "serve_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        kw = {"licenses_path": _licenses(d / "licenses.parquet")}
        if policies:
            (d / "policies.json").write_text(json.dumps(_policies(), ensure_ascii=False), encoding="utf-8")
            kw["policies_path"] = d / "policies.json"
        if online:
            (d / "online.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in _online()),
                                            encoding="utf-8")
            kw["online_presence_path"] = d / "online.jsonl"
        return d / "reports.jsonl", d / "serve_meta.json", kw
    return make


def _build(inputs, out, **kw):
    reports, meta, extra = inputs(**kw)
    run = bd.build(reports, meta, extra.pop("licenses_path"), out, license_snapshot_date="2026-09-11", **extra)
    return run


def _q(path, sql):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def _reports(path):
    conn = sqlite3.connect(path)
    try:
        return {r["store_id"]: r for r in bd.iter_reports(conn)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
def test_v0_1_input_builds_structure_but_final_not_ready(inputs, tmp_path):
    out = tmp_path / "report.sqlite"
    run = _build(inputs, out, version="0.1")
    assert run["input_schema_version"] == "0.1"
    assert run["final_contract"] == "not_ready" and "R1~R3" in run["final_contract_note"]
    assert run["policy_matching"] == "not_performed" and run["n_policies"] == 0
    assert _q(out, "SELECT COUNT(*) FROM stores")[0][0] == 3
    assert _q(out, "SELECT COUNT(*) FROM factors")[0][0] == 11
    # R1·R2 값은 추측하지 않는다: 0.1 입력이면 전부 NULL
    assert _q(out, "SELECT COUNT(*) FROM factors WHERE missing_reason IS NOT NULL OR hold_reason IS NOT NULL")[0][0] == 0
    assert _q(out, "SELECT COUNT(*) FROM store_policies")[0][0] == 0
    # 데이터 없음·검토 대기 요인이 있는 점포 B, 반올림 부근 기여가 있는 점포 A는 최종 검증을 통과할 수 없다
    assert run["n_final_invalid"] == 2
    reps = _reports(out)
    assert all(r["policy_matching"] == "not_performed" and r["policies"] == [] for r in reps.values())


def test_v0_2_input_passes_final_contract(inputs, tmp_path):
    out = tmp_path / "report.sqlite"
    run = _build(inputs, out, version="0.2", policies=True, online=True)
    assert run["final_contract"] == "passed" and run["n_final_invalid"] == 0
    assert run["policy_matching"] == "performed" and run["n_policies"] == 4
    assert run["n_online_presence"] == 1 and run["n_online_presence_ignored"] == 1
    assert run["score_origin"] == "2026Q2" and run["license_snapshot_date"] == "2026-09-11"
    assert json.loads(run["band_cutoffs_json"]) == {"mid": 0.1493, "high": 0.2142}
    reps = _reports(out)
    for r in reps.values():
        assert rv.validate_report(r) == []

    a, b, c = reps[A], reps[B], reps[C]
    assert a["store"]["status"] == {"open_at_as_of": True, "current": "open", "close_date": None,
                                    "license_snapshot_date": "2026-09-11"}
    assert b["store"]["status"]["current"] == "closed" and b["store"]["status"]["close_date"] == "2026-08-14"
    assert c["store"]["status"]["current"] == "unknown" and c["store"]["dong"] is None
    assert b["store"]["name"] == "예시카페"  # 인허가 테이블이 단일 출처
    assert a["online_presence"]["naver_local_registered"] is True and b["online_presence"] is None
    assert a["prescriptions"] == []
    assert [f["factor_id"] for f in a["factors"]] == ["online_attention", "tenure", "district", "store_profile"]
    assert next(f for f in b["factors"] if f["factor_id"] == "trdar_population")["missing_reason"] == "out_of_trdar"
    # 원천(serve 0.2) 코드값을 그대로 보존한다
    assert _q(out, "SELECT factor_id, display, data_missing, missing_reason, hold_reason FROM factors "
                   f"WHERE store_id = '{B}' AND display = 0 ORDER BY factor_id") == [
        ("online_attention", 0, 0, None, "online_review"), ("trdar_population", 0, 1, "out_of_trdar", "data_missing")]

    # 정책 (FACTOR_POLICY_LINKS.md §4): A 업력 41개월
    assert [(p["id"], p["match_status"], p["linked_factor_ids"]) for p in a["policies"]] == [
        ("p_online", "matched", ["online_attention"]),
        ("p_gu", "matched", ["tenure"]),            # store_profile은 위험을 낮춰 연결하지 않는다
        ("p_rent", "check_required", []),           # rent_level은 비활성 → 연결 없음
    ]
    assert a["policies"][0]["matched_by"] == ["biz_type", "tenure"]
    # B: 온라인 요인은 검토 대기라 연결하지 않는다, 마포구라 p_gu 제외, 73개월이라 p_young 제외
    assert [(p["id"], p["linked_factor_ids"]) for p in b["policies"]] == [("p_online", []), ("p_rent", [])]
    # C: 인허가일이 없어 업력 조건을 확인할 수 없다 → 추정하지 않고 check_required
    cp = {p["id"]: p for p in c["policies"]}
    assert set(cp) == {"p_online", "p_rent", "p_young"}
    assert cp["p_online"]["match_status"] == "check_required"
    assert "업력 조건 (인허가일 정보 없음)" in cp["p_online"]["unverifiable_conditions"]
    assert cp["p_online"]["matched_by"] == ["biz_type"]


def test_rebuild_is_reproducible(inputs, tmp_path):
    reports, meta, kw = inputs(version="0.2", policies=True, online=True)
    lic = kw.pop("licenses_path")
    dumps, runs = [], []
    for name in ("one.sqlite", "two.sqlite"):
        runs.append(bd.build(reports, meta, lic, tmp_path / name, license_snapshot_date="2026-09-11", **kw))
        with contextlib.closing(sqlite3.connect(tmp_path / name)) as conn:
            dumps.append(list(conn.iterdump()))
    assert runs[0]["run_id"] == runs[1]["run_id"]
    assert dumps[0] == dumps[1]


def test_failed_build_keeps_previous_canonical(inputs, tmp_path):
    out = tmp_path / "report.sqlite"
    _build(inputs, out, version="0.2")
    before = hashlib.sha256(out.read_bytes()).hexdigest()
    bad = _records("0.2")
    bad[0]["factors"][2]["direction"] = "위험 증가"  # 0.0004 → '영향 미미'여야 한다 (최종 검증에서 실패)
    with pytest.raises(bd.BuildError, match="최종 0.2"):
        _build(inputs, out, version="0.2", records=bad)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == before
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".report.sqlite.tmp")] == []


def _dup(recs):
    return recs + [copy.deepcopy(recs[0])]


def _set(i, path, value):
    def fn(recs):
        obj = recs[i]
        for k in path[:-1]:
            obj = obj[k]
        obj[path[-1]] = value
        return recs
    return fn


FAILURES = {
    "duplicate_store": (lambda r: _dup(r), {}, "store_id 중복"),
    "missing_store_vs_meta": (lambda r: r[:2], {"n_stores": 3}, "점포 수 불일치"),
    "record_as_of_mismatch": (_set(0, ["as_of"], "2026-03-31"), {}, "serve 입력 스키마|기준 시점"),
    "record_origin_mismatch": (_set(0, ["score_origin"], "2026Q1"), {}, "기준 시점 불일치"),
    "meta_as_of_not_quarter_end": (lambda r: r, {"as_of": "2026-06-15"}, "분기 말일"),
    "unknown_store": (_set(0, ["store_id"], "GR_9999999-101-2026-99999"), {}, "인허가 테이블에 없는 점포"),
    "name_mismatch": (_set(0, ["store", "name"], "다른이름"), {}, "store.name"),
    "gu_mismatch": (_set(2, ["store", "gu"], "마포구"), {}, "store.gu|peer_group"),
    "input_schema_violation": (lambda r: [{k: v for k, v in r[0].items() if k != "risk"}, *r[1:]], {},
                               "serve 입력 스키마"),
    "mixed_versions": (_set(1, ["_schema_version"], "0.1"), {}, "버전"),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_input_failures(inputs, tmp_path, case):
    mutate, meta_update, msg = FAILURES[case]
    recs = mutate(_records("0.2"))
    out = tmp_path / "report.sqlite"
    with pytest.raises(bd.BuildError, match=msg):
        _build(inputs, out, version="0.2", records=recs, meta_update=meta_update)
    assert not out.exists()


def test_store_not_open_at_as_of(inputs, tmp_path):
    """reports에 as_of 이전 폐업 점포가 들어오면 멈춘다 (리포트 모집단 = as_of 당시 영업 점포)."""
    recs = _records("0.2")
    recs[0]["store_id"] = X
    recs[0]["store"] = {"biz_type": "일반음식점", "gu": "광진구"}
    with pytest.raises(bd.BuildError, match="as_of 당시 영업 점포가 아니다"):
        _build(inputs, tmp_path / "r.sqlite", version="0.2", records=recs)


def test_license_table_duplicate_store(inputs, tmp_path):
    reports, meta, kw = inputs(version="0.2")
    lic = pd.read_parquet(kw["licenses_path"])
    pd.concat([lic, lic.iloc[[0]]]).to_parquet(kw["licenses_path"], index=False)
    with pytest.raises(bd.BuildError, match="1:1 결합 불가"):
        bd.build(reports, meta, kw["licenses_path"], tmp_path / "r.sqlite")


def test_invalid_policy_source(inputs, tmp_path):
    reports, meta, kw = inputs(version="0.2", policies=True)
    pols = _policies()
    pols[0]["related_factor_ids"] = ["온라인 노출 채널 수"]  # 이름 문자열은 연결 키가 아니다
    kw["policies_path"].write_text(json.dumps(pols, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(bd.BuildError, match="policy_source"):
        bd.build(reports, meta, kw.pop("licenses_path"), tmp_path / "r.sqlite", **kw)


def test_duplicate_online_presence(inputs, tmp_path):
    reports, meta, kw = inputs(version="0.2", online=True)
    rows = _online() + [_online()[0]]
    kw["online_presence_path"].write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                          encoding="utf-8")
    with pytest.raises(bd.BuildError, match="온라인 존재감 store_id 중복"):
        bd.build(reports, meta, kw.pop("licenses_path"), tmp_path / "r.sqlite", **kw)


def test_tenure_bounds_are_inclusive():
    store = {"gu": "광진구", "biz_type": "미용업", "license_date": "2025-06-10"}  # as_of 기준 12개월
    pol = _policies()[0]
    assert bd.tenure_months("2025-06-10", AS_OF) == 12
    assert [m["policy_id"] for m in bd.match_policies(store, [], [pol], AS_OF)] == ["p_online"]
    store["license_date"] = "2025-07-01"  # 11개월
    assert bd.match_policies(store, [], [pol], AS_OF) == []


def test_output_guard_blocks_tracked_paths():
    with pytest.raises(bd.BuildError, match="docs"):
        bd.guard_output_path(config.REPO_ROOT / "docs" / "report.sqlite")
    with pytest.raises(bd.BuildError, match="git 무시 대상이 아닌"):
        bd.guard_output_path(config.REPO_ROOT / "src" / "serving" / "report_out.json")
    bd.guard_output_path(bd.DEFAULT_OUT)  # outputs/는 무시 대상
    bd.guard_output_path(config.REPO_ROOT / "src" / "serving" / "report.sqlite")  # *.sqlite도 무시 대상


def test_output_guard_covers_other_git_worktrees(tmp_path, inputs):
    """정본이 다른 git 작업 트리(프론트 저장소 등)의 추적 가능 경로에 생기지 않는다."""
    import subprocess
    front = tmp_path / "frontend"
    front.mkdir()
    subprocess.run(["git", "init", "-q", str(front)], check=True)
    (front / ".gitignore").write_text("private/\n", encoding="utf-8", newline="\n")
    plain = tmp_path / "plain" / "report.sqlite"                          # git 작업 트리 밖

    with pytest.raises(bd.BuildError, match="git 무시 대상이 아닌"):
        bd.guard_output_path(front / "data" / "report.sqlite")            # 다른 저장소, 추적 가능
    with pytest.raises(bd.BuildError, match="public"):
        bd.guard_output_path(front / "private" / "public" / "report.sqlite")  # 무시 경로라도 공개 디렉터리
    bd.guard_output_path(front / "private" / "report.sqlite")             # 다른 저장소, gitignore 적용
    bd.guard_output_path(plain)

    # 실제 빌드도 같은 규칙: 추적 가능 경로는 아무 파일도 남기지 않고 멈춘다
    reports, meta, kw = inputs(version="0.2")
    lic = kw.pop("licenses_path")
    with pytest.raises(bd.BuildError):
        bd.build(reports, meta, lic, front / "data" / "report.sqlite")
    assert not (front / "data").exists()
    bd.build(reports, meta, lic, front / "private" / "report.sqlite")
    bd.build(reports, meta, lic, plain)
    assert (front / "private" / "report.sqlite").exists() and plain.exists()


# ---------------------------------------------------------------------------
# 배포용 빌드·정본 보호 (W2-5 3단계 보강)
from tests.serving_synth import make_db, store as synth_store, write_inputs  # noqa: E402

SYNTH = [synth_store(1, "마포구", "망원동", "미용업", "high"), synth_store(2, "광진구", "화양동", "일반음식점")]


def _runs(path):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return bd.read_run(conn)


def test_snapshot_provenance_separates_supplied_and_checked(tmp_path):
    run = _runs(make_db(tmp_path, SYNTH, name="a.sqlite"))
    assert (run["license_snapshot_date_basis"], run["license_snapshot_check"]) == ("user_supplied", "consistent")
    assert run["license_max_updated_date"] == "2026-09-10" and run["build_purpose"] == "dev"
    run = _runs(make_db(tmp_path, SYNTH, name="b.sqlite", snapshot=None))
    assert (run["license_snapshot_date_basis"], run["license_snapshot_check"]) == ("not_provided", "not_checked")
    run = _runs(make_db(tmp_path, SYNTH, name="c.sqlite", updated=None))  # 데이터갱신일자 컬럼 없음
    assert (run["license_snapshot_date_basis"], run["license_snapshot_check"]) == ("user_supplied", "not_checked")
    assert bd.release_blockers(run)


def test_snapshot_date_before_source_update_is_rejected(tmp_path):
    with pytest.raises(bd.BuildError, match="데이터갱신일자"):
        make_db(tmp_path, SYNTH, snapshot="2026-09-01")


def test_release_build_requirements(tmp_path):
    with pytest.raises(bd.BuildError, match="license-snapshot-date"):
        make_db(tmp_path, SYNTH, purpose="release", snapshot=None, name="a.sqlite")
    with pytest.raises(bd.BuildError, match="대조"):
        make_db(tmp_path, SYNTH, purpose="release", updated=None, name="b.sqlite")
    with pytest.raises(bd.BuildError, match="최종 0.2 검증 통과"):
        make_db(tmp_path, SYNTH, purpose="release", version="0.1", name="c.sqlite")
    assert not (tmp_path / "c.sqlite").exists()
    run = _runs(make_db(tmp_path, SYNTH, purpose="release", name="d.sqlite"))
    assert run["build_purpose"] == "release" and bd.release_blockers(run) == []


def test_passed_canonical_is_not_downgraded(tmp_path):
    out = make_db(tmp_path, SYNTH)  # serve 0.2 → passed
    before = hashlib.sha256(out.read_bytes()).hexdigest()
    d = write_inputs(tmp_path / "v01", SYNTH, version="0.1")
    with pytest.raises(bd.BuildError, match="덮어쓰지 않는다"):
        bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == before
    run = bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out, allow_downgrade=True)
    assert run["final_contract"] == "not_ready" and _runs(out)["final_contract"] == "not_ready"
    # not_ready → not_ready, not_ready → passed 교체는 막지 않는다
    bd.build(d / "reports.jsonl", d / "serve_meta.json", d / "licenses.parquet", out)
