# -*- coding: utf-8 -*-
"""#34 리뷰 ① — Shapley 배경 안정성 실험·서빙 배경 저장/재현 테스트."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.analysis import shapley_background_stability as sbs
from src.models import background, detect
from tests.test_models import _online_table, synthetic_master


def test_samplers_distinct_and_sized():
    pool = np.arange(200)
    assert len(set(sbs.sample_random(pool, 20, 1))) == 20
    strata = pd.Series(["A"] * 180 + ["B"] * 20)
    out = sbs.sample_stratified(pool, strata, 20, 1)
    assert len(set(out)) == 20 and {"A", "B"} <= set(strata.loc[out])
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=200), "b": rng.normal(size=200)})
    km = sbs.sample_kmeans(X, pool, 10, 0)
    assert len(set(km)) == 10 and set(km) <= set(pool)


def test_agreement_metrics():
    ref = np.array([[0.1, 0.9], [0.5, 0.1], [0.2, -0.3]])
    phi = np.array([[0.2, 0.8], [0.1, 0.5], [0.3, 0.1]])
    assert sbs.top1_agreement(phi, ref) == pytest.approx(2 / 3)
    assert sbs.sign_agreement(phi[:, 1], ref[:, 1]) == pytest.approx(2 / 3)


def _rows(method, n, top1s, signs, sec):
    return [{"method": method, "n_background": n, "seed": i, "top1": t, "sign": s, "seconds_per_1000": sec,
             "base_value": 0.1 + i / 100} for i, (t, s) in enumerate(zip(top1s, signs))]


def test_summarize_candidates_reports_criterion_without_selecting():
    """평가표만 낸다 — 하나를 고르는 규칙(이전의 사후 대체 규칙)은 없다."""
    t = pd.DataFrame(_rows("random", 64, [0.80, 0.95, 0.91, 0.92, 0.93], [0.96] * 5, 100)  # 중앙값 0.92·0.96 — 충족
                     + _rows("random", 16, [0.95, 0.60, 0.60, 0.95, 0.60], [0.99] * 5, 30)  # 중앙값 0.60 — 미달
                     + _rows("random", 256, [0.95] * 5, [0.94] * 5, 50))  # 부호 0.94 — 미달
    s = sbs.summarize_candidates(t).set_index(["method", "n_background"])
    assert s.at[("random", 64), "meets_criterion"] and not s.at[("random", 16), "meets_criterion"]
    assert not s.at[("random", 256), "meets_criterion"] and s.at[("random", 64), "top1_median"] == pytest.approx(0.92)
    assert not hasattr(sbs, "recommend")  # 선택 함수가 남아 있지 않다


def _strata_df(n=1000):
    rng = np.random.default_rng(0)
    return pd.DataFrame({"store_id": [f"S{i:04d}" for i in range(n)], "origin": ["2024Q1"] * n,
                         "biz_type": rng.choice(["일반음식점", "휴게음식점", "미용업"], n),
                         "gu": rng.choice(["광진구", "마포구", "영등포구"], n)})


def test_s8_rule_is_issue49_and_two_backgrounds_are_deterministic():
    r = background.S8_RULE
    assert (r["method"], r["n_background"]) == ("stratified", 256)
    assert r["seeds"] == {"primary": 20260931, "sensitivity": 20261001} and "#49 S8" in r["rule_ref"]
    df = _strata_df()
    mask = np.ones(len(df), dtype=bool)
    mask[:100] = False  # 학습 구간 밖
    a, b = background.draw_s8(df, mask), background.draw_s8(df.copy(), mask.copy())
    for role in background.ROLES:
        assert (a[role] == b[role]).all() and len(set(a[role])) == 256 and a[role].min() >= 100
    assert set(a["primary"]) != set(a["sensitivity"])  # 독립 배경 두 개
    # 실험 모듈과 같은 알고리즘 (작성자 manifest 해시 ffedb33b…를 만든 추출과 같다)
    pool, st = np.flatnonzero(mask), background.strata_of(df)
    assert (sbs.sample_stratified(pool, st, 256, 20260931) == a["primary"]).all()
    with pytest.raises(ValueError, match="배경 크기"):
        background.draw_s8(df, np.arange(len(df)) < 10)


def test_create_s8_writes_manifest_with_provenance_and_load_roundtrips(tmp_path):
    panel = synthetic_master(n_stores=80)
    mp = tmp_path / "master.parquet"
    panel.to_parquet(mp, index=False)
    rule = {**background.S8_RULE, "n_background": 32, "reference_origin": sorted(panel["origin"].unique())[-1]}
    m1 = background.create(mp, tmp_path / "bg1", rule=rule)
    m2 = background.create(mp, tmp_path / "bg2", rule=rule)
    for role in background.ROLES:  # 같은 입력 → 같은 배경
        assert m1["backgrounds"][role]["sha256"] == m2["backgrounds"][role]["sha256"]
    assert {r: m1["backgrounds"][r]["seed"] for r in background.ROLES} == {"primary": 20260931, "sensitivity": 20261001}
    for k in ("rule_ref", "rule_version", "method", "n_background", "pool", "pool_origins", "comparison",
              "ui_check_note", "check_1024", "prior_evidence", "affects", "source_master_sha256"):
        assert k in m1, k
    assert m1["check_1024"]["seed"] == 20261002 and m1["check_1024"]["per_band"] == {"low": 66, "mid": 66, "high": 68}
    assert "통계적 안정성의 증명이 아니다" in m1["ui_check_note"] and "25.9%" in m1["prior_evidence"]
    from src.models import train_detect
    df = train_detect.load_master(mp)
    for role in background.ROLES:
        idx, man = background.load_background(tmp_path / "bg1" / "background_manifest.json", df, role)
        assert len(idx) == 32 and background.index_sha256(df, idx) == man["backgrounds"][role]["index_sha256"]
    # 한쪽 행 파일을 변조하면 그 배경만 멈춘다
    f = tmp_path / "bg1" / "background_rows_sensitivity.csv"
    f.write_text(f.read_text(encoding="utf-8") + "0" * 32 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="해시"):
        background.load_background(tmp_path / "bg1" / "background_manifest.json", df, "sensitivity")


def test_check_sample_is_deterministic_and_carries_shortfall():
    rng = np.random.default_rng(1)
    risk = pd.DataFrame({"store_id": [f"S{i:05d}" for i in range(1000)],
                         "band": rng.choice(["low", "mid", "high"], 1000, p=[0.6, 0.3, 0.1])})
    a, b = background.check_sample(risk), background.check_sample(risk.sample(frac=1, random_state=3))
    assert a.equals(b)  # 입력 순서와 무관하게 같은 표본
    assert len(a) == 200 and a["band"].value_counts().to_dict() == {"low": 66, "mid": 66, "high": 68}
    assert not a["store_id"].duplicated().any()
    # mid가 40곳뿐이면 전수 + 부족분 26은 high로 이월 (high 68+26=94)
    small = pd.concat([risk[risk["band"] == "low"], risk[risk["band"] == "mid"].head(40), risk[risk["band"] == "high"]])
    c = background.check_sample(small)
    assert c["band"].value_counts().to_dict() == {"low": 66, "mid": 40, "high": 94}
    with pytest.raises(ValueError, match="중복"):
        background.check_sample(pd.concat([risk, risk.head(1)]))


def test_check_background_is_random1024_with_s8_seed():
    df = _strata_df(3000)
    mask = np.ones(len(df), dtype=bool)
    a, b = background.draw_check_background(df, mask), background.draw_check_background(df, mask)
    assert (a == b).all() and len(set(a)) == 1024
    assert background.CHECK_1024["purpose"].startswith("대조용")


def test_load_background_legacy_single_format_has_no_sensitivity(tmp_path):
    df = _panel_df()
    background.save_background(df, np.array([1, 2, 3]), tmp_path)
    assert len(background.load_background(tmp_path / "background_manifest.json", df)[0]) == 3
    with pytest.raises(ValueError, match="sensitivity"):
        background.load_background(tmp_path / "background_manifest.json", df, "sensitivity")


def test_load_background_fails_on_count_or_index_hash_mismatch(tmp_path):
    df = _panel_df()
    background.save_background(df, np.array([1, 2, 3]), tmp_path, meta={"index_sha256": background.index_sha256(df, [1, 2, 3])})
    mp = tmp_path / "background_manifest.json"
    man = json.loads(mp.read_text(encoding="utf-8"))
    mp.write_text(json.dumps({**man, "n": 4}), encoding="utf-8")
    with pytest.raises(ValueError, match="행 수"):
        background.load_background(mp, df)
    mp.write_text(json.dumps({**man, "index_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="index_sha256"):
        background.load_background(mp, df)
    mp.write_text(json.dumps(man), encoding="utf-8")
    assert len(background.load_background(mp, df)[0]) == 3


def _panel_df(n=30):
    return pd.DataFrame({"store_id": [f"S{i:03d}" for i in range(n)], "origin": ["2024Q1"] * n})


def test_background_roundtrip_preserves_rows_and_order(tmp_path):
    df = _panel_df()
    idx = np.array([7, 2, 19, 11])
    m = background.save_background(df, idx, tmp_path, meta={"method": "random"})
    raw = (tmp_path / background.ROWS_FILE).read_text(encoding="utf-8")
    assert "S0" not in raw  # store_id를 파일에 남기지 않는다
    # 다른 순서·더 큰 패널(서빙 학습 구간이 넓어진 경우)에서도 같은 행을 같은 순서로 찾는다
    df2 = pd.concat([_panel_df(), pd.DataFrame({"store_id": ["X1"], "origin": ["2024Q2"]})]).iloc[::-1].reset_index(drop=True)
    pos, man = background.load_background(tmp_path / "background_manifest.json", df2)
    assert df2.iloc[pos]["store_id"].tolist() == df.iloc[idx]["store_id"].tolist()
    assert man["sha256"] == m["sha256"] and man["n"] == 4 and man["method"] == "random"


def test_background_errors_on_missing_or_tampered(tmp_path):
    df = _panel_df()
    with pytest.raises(FileNotFoundError, match="manifest"):
        background.load_background(tmp_path / "background_manifest.json", df)
    background.save_background(df, np.array([1, 2, 3]), tmp_path)
    rows = tmp_path / background.ROWS_FILE
    rows.write_text(rows.read_text(encoding="utf-8").replace(rows.read_text(encoding="utf-8").splitlines()[1], "0" * 32),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="해시"):
        background.load_background(tmp_path / "background_manifest.json", df)
    background.save_background(df, np.array([1, 2, 3]), tmp_path)
    with pytest.raises(ValueError, match="학습 패널에 없다"):
        background.load_background(tmp_path / "background_manifest.json", df.iloc[5:])
    rows.unlink()
    with pytest.raises(FileNotFoundError, match="행 파일"):
        background.load_background(tmp_path / "background_manifest.json", df)


@pytest.fixture
def small_design(monkeypatch, tmp_path):
    panel = synthetic_master(n_stores=80)
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    monkeypatch.setattr(sbs, "ORIGIN", sorted(panel["origin"].unique())[-1])
    monkeypatch.setattr(sbs, "EVAL_N", 12)
    monkeypatch.setattr(sbs, "REF_N", 16)
    monkeypatch.setattr(sbs, "SEEDS", (1, 2))
    monkeypatch.setattr(sbs, "CANDIDATES", [("random", 4), ("stratified", 4), ("kmeans", 4)])
    return mp, op


def test_run_checkpoints_resume_and_does_not_write_operational_background(small_design, tmp_path, monkeypatch):
    mp, op = small_design
    out = tmp_path / "exp"
    monkeypatch.setattr(background, "DEFAULT_DIR", tmp_path / "bg")
    res = sbs.run(mp, op, out)
    lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 * 2 and set(res["summary"]["method"]) == {"random", "stratified", "kmeans"}
    assert set(res) == {"summary"} and "meets_criterion" in res["summary"].columns
    assert not (tmp_path / "bg").exists()  # 실험은 운영 배경 manifest를 쓰지 않는다
    # 다시 실행하면 끝난 설정·기준 배경은 다시 계산하지 않는다
    calls = []
    real = sbs._shapley
    monkeypatch.setattr(sbs, "_shapley", lambda s, bg: calls.append(len(bg)) or real(s, bg))
    sbs.run(mp, op, out)
    assert calls == [] and len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 6
    # 설계가 바뀌면(평가 점포 수) 이어 쓰지 않고 멈춘다
    monkeypatch.setattr(sbs, "EVAL_N", 10)
    with pytest.raises(RuntimeError, match="설계"):
        sbs.run(mp, op, out)
    design = json.loads((out / "design.json").read_text(encoding="utf-8"))
    assert design["eval_n"] == 12 and design["ref_n"] == 16


def test_background_experiment_uses_serving_params(small_design, tmp_path):
    """S13: 배경 실험의 모형 파라미터가 서빙(채택) 파라미터와 같고, 설계 기록에 params_name이 남는다."""
    mp, op = small_design
    params, name = sbs.serving_params(None)
    assert params == detect.ADOPTED_PARAMS and name == "adopted"
    s = sbs.setup(mp, op, params=params)
    assert s["model"].params == detect.ADOPTED_PARAMS
    # run_meta를 주면 그 params·params_name이 설계 기록에 남는다 (#53 criterion 구조는 그대로)
    rm = tmp_path / "run_meta.json"
    rm.write_text(json.dumps({"params": detect.ADOPTED_PARAMS, "params_name": "adopted"}), encoding="utf-8")
    out = tmp_path / "exp"
    sbs.run(mp, op, out, run_meta_path=rm)
    design = json.loads((out / "design.json").read_text(encoding="utf-8"))
    assert design["params"] == detect.ADOPTED_PARAMS and design["params_name"] == "run_meta:adopted"
    assert "criterion" in design and "rule" not in design


def test_serving_params_prefers_run_meta(tmp_path):
    meta = tmp_path / "run_meta.json"
    meta.write_text(json.dumps({"params": {**detect.ADOPTED_PARAMS, "max_iter": 7}, "params_name": "adopted"}), encoding="utf-8")
    params, name = sbs.serving_params(meta)
    assert params["max_iter"] == 7 and name == "run_meta:adopted"
    assert sbs.serving_params(tmp_path / "missing.json")[1] == "adopted"


def test_serving_params_marks_legacy_run_meta_without_params_name(tmp_path):
    """#51 이전 run_meta(params_name 없음)는 unknown으로 숨기지 않고 legacy 여부·DEFAULT 여부를 구분해 기록한다."""
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"params": detect.DEFAULT_PARAMS}), encoding="utf-8")
    params, name = sbs.serving_params(legacy)
    assert params == detect.DEFAULT_PARAMS and name == "run_meta:legacy_default"
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"params": {**detect.DEFAULT_PARAMS, "max_iter": 7}}), encoding="utf-8")
    assert sbs.serving_params(other)[1] == "run_meta:legacy_unnamed"
    named = tmp_path / "named.json"
    named.write_text(json.dumps({"params": detect.DEFAULT_PARAMS, "params_name": "default"}), encoding="utf-8")
    assert sbs.serving_params(named)[1] == "run_meta:default"


def test_cli_has_run_meta_and_no_background_dir(monkeypatch, tmp_path):
    """#53이 지운 --background-dir(운영 배경 선택)는 되살리지 않고 --run-meta만 run()에 넘긴다."""
    seen = {}
    monkeypatch.setattr(sbs, "run", lambda *a, **k: seen.update(args=a, kw=k))
    sbs.main(["--run-meta", str(tmp_path / "rm.json")])
    assert seen["args"][-1] == tmp_path / "rm.json"
    with pytest.raises(SystemExit):
        sbs.main(["--background-dir", str(tmp_path)])
