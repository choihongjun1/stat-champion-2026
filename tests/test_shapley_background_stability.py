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


def test_recommend_uses_seed_median_and_fastest():
    t = pd.DataFrame(_rows("random", 64, [0.80, 0.95, 0.91, 0.92, 0.93], [0.96] * 5, 100)  # 중앙값 0.92 — 충족
                     + _rows("random", 16, [0.95, 0.60, 0.60, 0.95, 0.60], [0.99] * 5, 30)  # 중앙값 0.60 — 미달
                     + _rows("stratified", 128, [0.97] * 5, [0.98] * 5, 200))
    rec = sbs.recommend(t)
    assert rec["met_rule"] and (rec["chosen"]["method"], rec["chosen"]["n_background"]) == ("random", 64)
    # 부호 기준(95%) 미달이면 채택하지 않는다
    t2 = pd.DataFrame(_rows("random", 64, [0.95] * 5, [0.94] * 5, 10) + _rows("random", 256, [0.93] * 5, [0.96] * 5, 50))
    assert sbs.recommend(t2)["chosen"]["n_background"] == 256
    # 아무것도 충족하지 않으면 두 일치율 중앙값의 최솟값이 가장 높은 설정 + met_rule False
    t3 = pd.DataFrame(_rows("random", 16, [0.7] * 5, [0.9] * 5, 10) + _rows("random", 64, [0.85] * 5, [0.88] * 5, 50))
    rec3 = sbs.recommend(t3)
    assert not rec3["met_rule"] and rec3["chosen"]["n_background"] == 64


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


def test_run_checkpoints_resume_and_saves_background(small_design, tmp_path, monkeypatch):
    mp, op = small_design
    out, bg_dir = tmp_path / "exp", tmp_path / "bg"
    res = sbs.run(mp, op, out, bg_dir)
    lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 * 2 and set(res["summary"]["method"]) == {"random", "stratified", "kmeans"}
    assert (bg_dir / "background_manifest.json").exists() and res["manifest"]["n"] == res["chosen"]["n_background"]
    # 다시 실행하면 끝난 설정·기준 배경은 다시 계산하지 않는다
    calls = []
    real = sbs._shapley
    monkeypatch.setattr(sbs, "_shapley", lambda s, bg: calls.append(len(bg)) or real(s, bg))
    sbs.run(mp, op, out, bg_dir)
    assert calls == [] and len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 6
    # 설계가 바뀌면(평가 점포 수) 이어 쓰지 않고 멈춘다
    monkeypatch.setattr(sbs, "EVAL_N", 10)
    with pytest.raises(RuntimeError, match="설계"):
        sbs.run(mp, op, out, bg_dir)
    design = json.loads((out / "design.json").read_text(encoding="utf-8"))
    assert design["eval_n"] == 12 and design["ref_n"] == 16


def test_background_experiment_uses_serving_params(small_design, tmp_path):
    """S13: 배경 실험의 모형 파라미터가 서빙(채택) 파라미터와 같고, 설계 기록에 params_name이 남는다."""
    mp, op = small_design
    params, name = sbs.serving_params(None)
    assert params == detect.ADOPTED_PARAMS and name == "adopted"
    s = sbs.setup(mp, op, params=params)
    assert s["model"].params == detect.ADOPTED_PARAMS
    out = tmp_path / "exp"
    sbs.run(mp, op, out, tmp_path / "bg")
    design = json.loads((out / "design.json").read_text(encoding="utf-8"))
    assert design["params"] == detect.ADOPTED_PARAMS and design["params_name"] == "adopted"


def test_serving_params_prefers_run_meta(tmp_path):
    meta = tmp_path / "run_meta.json"
    meta.write_text(json.dumps({"params": {**detect.ADOPTED_PARAMS, "max_iter": 7}, "params_name": "adopted"}), encoding="utf-8")
    params, name = sbs.serving_params(meta)
    assert params["max_iter"] == 7 and name == "run_meta:adopted"
    assert sbs.serving_params(tmp_path / "missing.json")[1] == "adopted"
