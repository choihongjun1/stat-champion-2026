# -*- coding: utf-8 -*-
"""서빙 테스트 — 검증 구간 마지막 origin을 라벨 없이 넣으면 train_detect 결과를 그대로 재현해야 한다."""
from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from src.models import background, detect, diagnose, features, serve, train_detect
from tests.test_models import _online_table, synthetic_master


@pytest.fixture(scope="module")
def detect_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("serve")
    panel = synthetic_master(n_stores=200)
    mp, op = d / "master.parquet", d / "online.parquet"
    panel.to_parquet(mp, index=False)
    _online_table(panel).to_parquet(op, index=False)
    ddir = d / "detect_v0_enriched"
    train_detect.run(mp, ddir, ["enriched"], n_boot=0, with_split_comparison=False,
                     online_path=op, primary="enriched")  # #51: 기본 params_name="adopted"
    origins = sorted(panel["origin"].unique())
    last = origins[-1]
    score = panel[panel["origin"] == last].drop(columns="event_12m")
    sp, osp = d / "score.parquet", d / "online_score.parquet"
    score.to_parquet(sp, index=False)
    pd.read_parquet(op).query("origin == @last").to_parquet(osp, index=False)
    # S8 두 배경(작게): 서빙 학습 구간(≤ last−5분기) 행에서
    pool = np.flatnonzero((panel["origin"] < origins[-5]).to_numpy())
    background.save_s8(panel, {"primary": pool[:6], "sensitivity": pool[6:12]}, d / "bg")
    return dict(d=d, mp=mp, op=op, sp=sp, osp=osp, ddir=ddir, last=last, man=d / "bg" / "background_manifest.json")


def _serve(r, out, **kw):
    kw.setdefault("background_manifest", r["man"])
    return serve.run(r["mp"], kw.pop("sp", r["sp"]), kw.pop("ddir", r["ddir"]), out, primary="enriched",
                     online_path=r["op"], online_score_path=kw.pop("osp", r["osp"]), n_boot=kw.pop("n_boot", 0), **kw)


def _copy_run(r, tmp_path, edit):
    ddir = tmp_path / "detect_copy"
    shutil.copytree(r["ddir"], ddir)
    rm = json.loads((ddir / "run_meta.json").read_text(encoding="utf-8"))
    edit(rm)
    (ddir / "run_meta.json").write_text(json.dumps(rm), encoding="utf-8")
    return ddir


def test_reproduces_detect_risk_scores(detect_run, tmp_path):
    risk = _serve(detect_run, tmp_path / "out")
    ref = pd.read_parquet(detect_run["ddir"] / "risk_scores.parquet").query("origin == @detect_run['last']")
    m = risk.merge(ref, on="store_id", suffixes=("", "_ref"))
    assert len(m) == len(ref) == len(risk)
    assert np.abs(m["probability_12m"] - m["probability_12m_ref"]).max() < 1e-12
    assert (m["band"] == m["band_ref"]).all()
    assert (m["percentile"] == m["percentile_ref"]).all()
    assert (risk["model"] == "detect_v0_enriched").all()


def test_training_window_embargo():
    lab = pd.DataFrame({"origin": ["2023Q4", "2024Q1", "2024Q2", "2025Q1"]})
    assert serve.training_mask(lab, "2025Q2").tolist() == [True, True, False, False]  # ≤ 2024Q1 (s−5)


def test_reports_jsonl_schema(detect_run, tmp_path):
    out = tmp_path / "out"
    _serve(detect_run, out, n_boot=2)
    recs = [json.loads(l) for l in (out / "reports.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == pd.read_parquet(detect_run["sp"]).shape[0]
    r = recs[0]
    assert set(r) == {"_schema_version", "store_id", "score_origin", "as_of", "store", "risk", "factors",
                      "unavailable_categories", "disclaimer"}
    assert r["_schema_version"] == "0.2" and r["score_origin"] == detect_run["last"]
    for f in r["factors"]:
        assert {"missing_reason", "hold_reason", "direction"} <= set(f)
        assert (f["hold_reason"] is None) == f["display"]  # display=false ⇔ hold_reason 있음
        assert (f["missing_reason"] is None) == (not f["data_missing"])
        assert f["direction"] in {"위험 증가", "위험 감소", "영향 미미"}
        assert "interpretation_sensitive" not in f  # #41 factor 스키마(additionalProperties=false) 호환
    k = r["risk"]
    assert k["ci_low"] <= k["probability_12m"] <= k["ci_high"]
    meta_d = json.loads((detect_run["ddir"] / "run_meta.json").read_text(encoding="utf-8"))
    assert k["band"] in {"low", "mid", "high"} and k["calibrated"] is bool(meta_d["calibration_applied"])
    assert r["as_of"] == str(pd.Period(detect_run["last"], freq="Q").end_time.date())
    assert r["unavailable_categories"] == ["비용"]
    assert {f["category"] for f in r["factors"]} <= set(diagnose.CATEGORIES)
    meta = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert meta["score_origin"] == detect_run["last"] and meta["n_boot"] == 2
    cat = pd.read_parquet(out / "diagnosis_by_category.parquet")
    total = cat[list(diagnose.CATEGORIES)].sum(axis=1) + cat["base_value"]
    assert np.allclose(total, cat["probability_12m"])  # 보정 전 척도에서 정확히 합산


def test_rejects_multi_origin_and_mismatched_feature_set(detect_run, tmp_path):
    two = pd.read_parquet(detect_run["mp"])
    o1, o2 = sorted(two["origin"].unique())[-2:]
    ids = sorted(two["store_id"].unique())
    two = pd.concat([two[(two["origin"] == o1) & two["store_id"].isin(ids[:50])],
                     two[(two["origin"] == o2) & two["store_id"].isin(ids[50:100])]])
    p = tmp_path / "two.parquet"
    two.to_parquet(p, index=False)
    with pytest.raises(ValueError, match="origin이 하나"):
        serve.load_score_panel(p)
    with pytest.raises(ValueError, match="feature set"):
        serve.run(detect_run["mp"], detect_run["sp"], detect_run["ddir"], tmp_path / "x", primary="base")


def test_calibrated_run_needs_calibrator(tmp_path):
    (tmp_path / "run_meta.json").write_text(json.dumps({"calibration_applied": True}), encoding="utf-8")
    pd.DataFrame([{"cut_mid": 0.1, "cut_high": 0.2}]).to_csv(tmp_path / "band_cutoffs.csv", index=False)
    with pytest.raises(FileNotFoundError):
        serve.read_detect_run(tmp_path)


def test_future_origin_excludes_unvalidated_land_price(detect_run, tmp_path):
    """score origin이 라벨 구간 뒤(현재 시점)여도 검증에서 못 쓴 land_price는 학습에 넣지 않는다."""
    lab = pd.read_parquet(detect_run["mp"])
    nxt = pd.Period(detect_run["last"], freq="Q") + 4
    sc = lab[lab["origin"] == detect_run["last"]].drop(columns="event_12m").copy()
    sc["origin"] = str(nxt)
    sc["origin_end"] = nxt.end_time.normalize()
    sp = tmp_path / "score_future.parquet"
    sc.to_parquet(sp, index=False)
    # 온라인 표의 시점 메타도 같은 origin으로 옮긴다(attach_online: online_feature_asof == origin_end 검사)
    on = pd.read_parquet(detect_run["osp"]).assign(origin=str(nxt), origin_end=nxt.end_time.normalize(),
                                                   online_feature_asof=nxt.end_time.normalize())
    osp = tmp_path / "online_future.parquet"
    on.to_parquet(osp, index=False)
    tr = serve.training_mask(lab, str(nxt))
    assert lab.loc[tr, "land_price"].notna().any()  # 넓어진 학습 구간에는 값이 있다
    out = tmp_path / "out"
    _serve(detect_run, out, sp=sp, osp=osp)
    meta = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert "land_price" in meta["excluded_unvalidated"] and "land_price" not in meta["features_used"]
    assert meta["as_of"] == str(nxt.end_time.date())


def test_store_block_gets_name_and_address_from_licenses(detect_run, tmp_path):
    """--licenses: store_id로 1:1 조인해 store 블록에 이름·주소를 붙이고, 없는 점포는 None."""
    ids = pd.read_parquet(detect_run["sp"], columns=["store_id"])["store_id"].tolist()
    known, unknown = ids[:-3], ids[-3:]
    lic = pd.DataFrame({"store_id": known, "name_raw": [f"가게{i}" for i in range(len(known))],
                        "road_addr_raw": "서울특별시 마포구 월드컵로 1", "addr_raw": "서울특별시 마포구 망원동 1",
                        "dong": "망원동", "license_date": pd.Timestamp("2019-04-11")})
    lp = tmp_path / "licenses.parquet"
    lic.to_parquet(lp, index=False)
    out = tmp_path / "out"
    _serve(detect_run, out, licenses_path=lp)
    recs = {r["store_id"]: r for r in map(json.loads, (out / "reports.jsonl").read_text(encoding="utf-8").splitlines())}
    s = recs[known[0]]["store"]
    assert s == {"biz_type": s["biz_type"], "gu": s["gu"], "name": "가게0", "address_road": "서울특별시 마포구 월드컵로 1",
                 "address_jibun": "서울특별시 마포구 망원동 1", "dong": "망원동", "license_date": "2019-04-11"}
    for sid in unknown:
        assert {k: recs[sid]["store"][k] for k in serve.STORE_META_COLS} == dict.fromkeys(serve.STORE_META_COLS)
    meta = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert meta["n_stores_without_name"] == 3 and meta["licenses_sha256"] == train_detect.sha256(lp)

    pd.concat([lic, lic.head(1)]).to_parquet(tmp_path / "dup.parquet", index=False)
    with pytest.raises(ValueError, match="중복"):
        serve.store_meta(ids, tmp_path / "dup.parquet")


# ---------------------------------------------------------------- #51 채택 설정 계약
def test_serve_meta_records_adopted_params_and_cutoff_provenance(detect_run, tmp_path):
    """#45/#51: 서빙 모형 설정은 run_meta의 adopted params이고, params_name·model_class·컷오프 provenance를 남긴다."""
    _serve(detect_run, tmp_path / "out")
    sm = json.loads((tmp_path / "out" / "serve_meta.json").read_text(encoding="utf-8"))
    rm = json.loads((detect_run["ddir"] / "run_meta.json").read_text(encoding="utf-8"))
    assert sm["params_name"] == rm["params_name"] == "adopted" and sm["params_contract"] == "adopted"
    assert sm["model_params"] == rm["params"] == detect.ADOPTED_PARAMS
    assert sm["model_class"] == rm["model_class"] == detect.MODEL_CLASS
    assert sm["model_params_source"] == "detect run_meta.params" and sm["model_params_is_default"] is False
    prov = sm["detect_run_provenance"]
    assert prov["run_meta_sha256"] == train_detect.sha256(detect_run["ddir"] / "run_meta.json")
    assert prov["master_sha256"] == sm["master_sha256"] and sm["detect_run"] == str(detect_run["ddir"])
    assert sm["band_definition"] == rm["band_definition"] and "1.2 × base_rate" in sm["band_definition"]["cut_mid"]
    cp = sm["cutoff_provenance"]
    assert {"calib_origins", "base_rate", "cut_mid", "cut_high", "high_fallback", "mid_fallback", "high_share_test",
            "high_lift_test", "high_lift_ci95", "high_share_served"} <= set(cp)
    # #41 serve_band_cutoffs 계약: 세 키만(additionalProperties=false). fallback 여부는 cutoff_provenance에
    assert set(sm["band_cutoffs"]) == {"cut_mid", "cut_high", "base_rate"}
    for k in ("cut_mid", "cut_high"):  # 최신 run_meta·band_cutoffs.csv·serve_meta가 같은 값
        assert cp[k] == pytest.approx(sm["band_cutoffs"][k]) == pytest.approx(rm["band_cutoffs"][k])


@pytest.mark.parametrize("edit, match", [
    (lambda rm: rm.pop("params"), "params가 없다"),
    (lambda rm: rm.pop("params_name"), "params_name=legacy_unnamed"),  # adopted 값이지만 이름 없음(#51 이전 형식)
    (lambda rm: rm.update(params=dict(detect.DEFAULT_PARAMS), params_name=None), "params_name=legacy_default"),
    (lambda rm: rm.update(params=dict(detect.DEFAULT_PARAMS), params_name="default"), "params_name=default"),
    (lambda rm: rm.update(params={**rm["params"], "max_iter": 50}), "ADOPTED_PARAMS와 다르다"),
    (lambda rm: rm.pop("model_class"), "model_class 없음"),
    (lambda rm: rm.update(model_class="sklearn.ensemble.RandomForestClassifier"), "model_class"),
])
def test_model_params_rejects_non_adopted_run_meta(detect_run, edit, match):
    rm = json.loads((detect_run["ddir"] / "run_meta.json").read_text(encoding="utf-8"))
    edit(rm)
    with pytest.raises(ValueError, match=match):
        serve.model_params(rm)


def test_model_params_override_flag_is_explicit_and_recorded(detect_run, tmp_path):
    """개발·과거 재현용 우회는 명시적 플래그로만 — 실제로 그 설정을 쓰고 contract에 비운영으로 남긴다.
    params가 없거나 model_class가 다르면 플래그로도 허용하지 않는다."""
    with pytest.raises(ValueError, match="params가 없다"):
        serve.model_params({"model_class": detect.MODEL_CLASS}, allow_non_adopted=True)
    with pytest.raises(ValueError, match="model_class"):
        serve.model_params({"params": detect.ADOPTED_PARAMS, "params_name": "adopted", "model_class": "x.Y"},
                           allow_non_adopted=True)
    ddir = _copy_run(detect_run, tmp_path, lambda rm: rm.update(params={**rm["params"], "max_iter": 50,
                                                                         "learning_rate": 0.1}))
    with pytest.raises(ValueError, match="ADOPTED_PARAMS와 다르다"):
        _serve(detect_run, tmp_path / "rejected", ddir=ddir)
    base = _serve(detect_run, tmp_path / "out_default")
    tweaked = _serve(detect_run, tmp_path / "out_tweaked", ddir=ddir, allow_non_adopted_params=True)
    sm = json.loads((tmp_path / "out_tweaked" / "serve_meta.json").read_text(encoding="utf-8"))
    assert sm["model_params"]["max_iter"] == 50 and sm["params_contract"].startswith("override")
    assert np.abs(base["probability_12m"].to_numpy() - tweaked["probability_12m"].to_numpy()).max() > 1e-6


def test_cli_rejects_legacy_run_meta_by_default(detect_run, tmp_path):
    ddir = _copy_run(detect_run, tmp_path, lambda rm: [rm.pop("params_name"), rm.pop("model_class"),
                                                       rm.update(params=dict(detect.DEFAULT_PARAMS))])
    args = ["--master", str(detect_run["mp"]), "--score", str(detect_run["sp"]), "--primary", "enriched",
            "--detect-dir", str(ddir), "--online", str(detect_run["op"]), "--online-score", str(detect_run["osp"]),
            "--n-boot", "0", "--out", str(tmp_path / "o"), "--background-manifest", str(detect_run["man"])]
    with pytest.raises(ValueError, match="legacy_default"):
        serve.main(args)


# ---------------------------------------------------------------- 같은 탐지 실행
def test_same_run_rejects_other_master_online_or_cutoffs(detect_run, tmp_path):
    lab = pd.read_parquet(detect_run["mp"])
    other = tmp_path / "master_other.parquet"
    lab.iloc[:-1].to_parquet(other, index=False)
    with pytest.raises(ValueError, match="master 해시"):
        serve.run(other, detect_run["sp"], detect_run["ddir"], tmp_path / "a", primary="enriched",
                  online_path=detect_run["op"], online_score_path=detect_run["osp"], n_boot=0,
                  background_manifest=detect_run["man"])
    on_other = tmp_path / "online_other.parquet"
    pd.read_parquet(detect_run["op"]).iloc[:-1].to_parquet(on_other, index=False)
    with pytest.raises(ValueError, match="온라인 표 해시"):
        serve.run(detect_run["mp"], detect_run["sp"], detect_run["ddir"], tmp_path / "b", primary="enriched",
                  online_path=on_other, online_score_path=detect_run["osp"], n_boot=0,
                  background_manifest=detect_run["man"])
    ddir = tmp_path / "detect_cut"
    shutil.copytree(detect_run["ddir"], ddir)
    cut = pd.read_csv(ddir / "band_cutoffs.csv")
    cut["cut_high"] = cut["cut_high"] + 0.01
    cut.to_csv(ddir / "band_cutoffs.csv", index=False)
    with pytest.raises(ValueError, match="band_cutoffs"):
        _serve(detect_run, tmp_path / "c", ddir=ddir)


# ---------------------------------------------------------------- S8 배경 (#53)
def test_serve_uses_s8_backgrounds_and_keeps_probability_and_band(detect_run, tmp_path):
    """#53: 두 배경(primary·sensitivity)으로 진단하고 해석 민감을 diagnosis.parquet에 남긴다. 배경은 위험 확률·등급을
    바꾸지 않는다. reports는 #41 스키마 호환으로 interpretation_sensitive를 기본으로 넣지 않는다."""
    risk = _serve(detect_run, tmp_path / "o1")
    sm = json.loads((tmp_path / "o1" / "serve_meta.json").read_text(encoding="utf-8"))
    man = json.loads(detect_run["man"].read_text(encoding="utf-8"))
    bg = sm["background"]["backgrounds"]
    assert set(bg) == {"primary", "sensitivity"} and sm["background"]["operational"] is True
    for role in ("primary", "sensitivity"):
        assert bg[role]["rows_sha256"] == man["backgrounds"][role]["sha256"]
        assert bg[role]["index_sha256"] == man["backgrounds"][role]["index_sha256"]
        assert bg[role]["seed"] == background.S8_RULE["seeds"][role]
    assert sm["s8_rule"]["rule_version"] == background.S8_RULE["rule_version"]
    assert sm["s8_summary"]["n_store_factor"] > 0 and "n_interpretation_sensitive" in sm["s8_summary"]
    d = pd.read_parquet(tmp_path / "o1" / "diagnosis.parquet")
    assert set(diagnose.SENSITIVITY_COLS) <= set(d.columns)
    assert d["interpretation_sensitive"].dtype == bool
    assert sm["diagnosis"]["same_model_as_risk"] and not sm["diagnosis"]["sensitivity_exposed_in_reports"]
    # 무작위 배경(비교용)과 위험 확률·등급이 같다 — 배경은 설명만 바꾼다
    rnd = _serve(detect_run, tmp_path / "o2", background_manifest=None, random_background=4)
    assert np.array_equal(risk["probability_12m"].to_numpy(), rnd["probability_12m"].to_numpy())
    assert (risk["band"] == rnd["band"]).all()
    assert json.loads((tmp_path / "o2" / "serve_meta.json").read_text(encoding="utf-8"))["background"]["operational"] is False
    # #41 스키마 갱신 후 노출
    _serve(detect_run, tmp_path / "o3", expose_sensitivity=True)
    rec = json.loads((tmp_path / "o3" / "reports.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert all(isinstance(f["interpretation_sensitive"], bool) for f in rec["factors"])


def test_cli_requires_s8_manifest(detect_run, tmp_path):
    with pytest.raises(FileNotFoundError, match="manifest"):
        serve.main(["--master", str(detect_run["mp"]), "--score", str(detect_run["sp"]), "--primary", "enriched",
                    "--detect-dir", str(detect_run["ddir"]), "--online", str(detect_run["op"]),
                    "--online-score", str(detect_run["osp"]), "--n-boot", "0", "--out", str(tmp_path / "o3"),
                    "--background-manifest", str(tmp_path / "missing.json")])


# ---------------------------------------------------------------- 온라인 보류·driver_code (#33/#44/#41)
def test_short_name_and_truncated_online_factor_is_held_as_data_missing(detect_run, tmp_path):
    """#44 A안(정규화 상호 ≤2자 → 온라인 feature 전 origin NA)은 upstream 온라인 표의 몫이다. serve는 그 NA를
    그대로 소비해 온라인 요인을 data_missing/online_unobservable로 보류한다. 절단 점포(#33 QA)도 같다."""
    ids = pd.read_parquet(detect_run["sp"], columns=["store_id"])["store_id"].tolist()
    short, trunc = ids[0], ids[1]
    on = pd.read_parquet(detect_run["osp"])
    on.loc[on["store_id"] == short, list(features.ONLINE_PREDICTORS)] = np.nan  # upstream A안 결과를 흉내
    # 절단 점포는 관측 시작 이전을 몰라 일부가 NA다(online_features unknown 마스크) — 일부 결측이면 보류(#34)
    on.loc[on["store_id"] == trunc, "online_blog_months_since_last"] = np.nan
    osp = tmp_path / "online_score_short.parquet"
    on.to_parquet(osp, index=False)
    qa = tmp_path / "qa.csv"
    pd.DataFrame({"store_id": ids, "error": "", "first_date_truncated": [str(s == trunc) for s in ids],
                  "oldest_raw_postdate": ["20200101" if s == trunc else "" for s in ids]}).to_csv(qa, index=False)
    out = tmp_path / "out"
    _serve(detect_run, out, osp=osp, qa_path=qa)
    recs = {r["store_id"]: r for r in map(json.loads, (out / "reports.jsonl").read_text(encoding="utf-8").splitlines())}
    for sid in (short, trunc):
        f = next(f for f in recs[sid]["factors"] if f["factor_id"] == "online_attention")
        assert f["display"] is False and f["hold_reason"] == "data_missing" and f["data_missing"] is True
        assert f["missing_reason"] == "online_unobservable"
    sm = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert sm["n_truncated_stores"] == 1


def test_driver_code_comes_from_diagnose_and_matches_41_enum(detect_run, tmp_path):
    _serve(detect_run, tmp_path / "out")
    seen = set()
    for r in map(json.loads, (tmp_path / "out" / "reports.jsonl").read_text(encoding="utf-8").splitlines()):
        for f in r["factors"]:
            if f["factor_id"] != "online_attention":
                assert f["driver_code"] is None  # #41: driver_code는 온라인 요인에만
            elif f["driver"] is not None:
                assert f["driver_code"] in diagnose.ONLINE_DRIVER_CODES
                assert f["driver_code"] == diagnose.classify_online_driver(f["driver"])  # #41 교차 검증과 같은 규칙
                seen.add(f["driver_code"])
    assert seen  # 합성 데이터에서 적어도 한 종류는 나온다


# ---------------------------------------------------------------- E2E: train_detect → diagnose → serve
def test_e2e_train_detect_diagnose_serve_use_the_same_adopted_run(detect_run, tmp_path):
    """train_detect(adopted) → diagnose --detect-run(같은 run) → serve --diagnose-meta: 모형 설정·클래스가 전 경로에서
    같고, 다른 run의 진단이면 멈춘다."""
    diagnose.main(["--master", str(detect_run["mp"]), "--online", str(detect_run["op"]), "--primary", "enriched",
                   "--max-stores", "10", "--background-manifest", str(detect_run["man"]),
                   "--detect-run", str(detect_run["ddir"]), "--out", str(tmp_path / "diag")])
    dm_path = tmp_path / "diag" / "diagnose_meta.json"
    dm = json.loads(dm_path.read_text(encoding="utf-8"))
    rm = json.loads((detect_run["ddir"] / "run_meta.json").read_text(encoding="utf-8"))
    assert dm["model_params"] == rm["params"] == detect.ADOPTED_PARAMS and "params_name=adopted" in dm["model_params_source"]
    assert dm["model_class"] == rm["model_class"] == detect.MODEL_CLASS
    assert dm["detect_run_meta_sha256"] == train_detect.sha256(detect_run["ddir"] / "run_meta.json")
    out = tmp_path / "serve"
    _serve(detect_run, out, diagnose_meta=dm_path)
    sm = json.loads((out / "serve_meta.json").read_text(encoding="utf-8"))
    assert sm["model_params"] == dm["model_params"] and sm["params_name"] == "adopted"
    assert sm["model_class"] == dm["model_class"]
    assert sm["diagnosis"]["diagnose_meta_check"]["detect_run_meta_sha256"] == sm["detect_run_provenance"]["run_meta_sha256"]
    assert "diagnose_meta" in sm["detect_run_provenance"]["same_run_checks"]
    # 다른 run(같은 내용이어도 run_meta 파일이 다르면)의 진단은 거부
    ddir = _copy_run(detect_run, tmp_path, lambda rm: rm.update(seconds=-1))
    with pytest.raises(ValueError, match="같은 탐지 실행이 아니다"):
        _serve(detect_run, tmp_path / "serve2", ddir=ddir, diagnose_meta=dm_path)


# ---------------------------------------------------------------- #44 A안 소비 (upstream online_features가 정본)
def test_short_name_masked_online_tables_flow_through_detect_diagnose_serve(tmp_path):
    """online_features.apply_short_name_policy(≤2자 → 온라인 predictor 전 origin NA)를 거친 학습용·예측용 표를
    train_detect → diagnose → serve가 그대로 소비한다: 짧은 상호 점포는 탐지 대상에 남고 온라인 요인은
    data_missing/online_unobservable(display=false), ≥3자 점포는 온라인 값·기여 경로가 그대로다. serve/diagnose는
    정책을 다시 구현하지 않는다."""
    from src.data import online_features as of

    panel = synthetic_master(n_stores=200)
    ids = sorted(panel["store_id"].unique())
    short_ids = set(ids[:20])
    lic = pd.DataFrame({"store_id": ids, "name_raw": ["가나" if s in short_ids else "가나다" for s in ids]})
    raw_online = _online_table(panel)
    online, rep = of.apply_short_name_policy(raw_online, lic, "na")
    assert rep["short_name_masked_store_count"] == 20 and len(online) == len(raw_online)
    mp, op = tmp_path / "master.parquet", tmp_path / "online.parquet"
    panel.to_parquet(mp, index=False)
    online.to_parquet(op, index=False)
    ddir = tmp_path / "detect"
    train_detect.run(mp, ddir, ["enriched"], n_boot=0, with_split_comparison=False, online_path=op, primary="enriched")
    origins = sorted(panel["origin"].unique())
    last = origins[-1]
    sp, osp = tmp_path / "score.parquet", tmp_path / "online_score.parquet"
    panel[panel["origin"] == last].drop(columns="event_12m").to_parquet(sp, index=False)
    online.query("origin == @last").to_parquet(osp, index=False)
    pool = np.flatnonzero((panel["origin"] < origins[-5]).to_numpy())
    background.save_s8(panel, {"primary": pool[:6], "sensitivity": pool[6:12]}, tmp_path / "bg")
    man = tmp_path / "bg" / "background_manifest.json"

    # 탐지: 짧은 상호 점포도 risk_scores에 남는다(모집단에서 빠지지 않음)
    risk_ref = pd.read_parquet(ddir / "risk_scores.parquet")
    assert short_ids <= set(risk_ref.loc[risk_ref["origin"] == last, "store_id"])

    # 진단(#53) — 같은 run
    long = diagnose.run(mp, tmp_path / "diag", online_path=op, primary="enriched", origin=None, max_stores=None,
                        background_manifest=man, detect_run=ddir)
    on = long[long["factor_id"] == "online_attention"].set_index("store_id")
    s = on.loc[sorted(short_ids)]
    assert (s["hold_reason"] == "data_missing").all() and (~s["display"]).all()
    assert (s["missing_reason_code"] == "online_unobservable").all()

    # 서빙(#36)
    out = tmp_path / "serve"
    risk = serve.run(mp, sp, ddir, out, primary="enriched", online_path=op, online_score_path=osp, n_boot=0,
                     background_manifest=man, diagnose_meta=tmp_path / "diag" / "diagnose_meta.json")
    assert short_ids <= set(risk["store_id"]) and len(risk) == len(pd.read_parquet(sp))
    recs = {r["store_id"]: r for r in map(json.loads, (out / "reports.jsonl").read_text(encoding="utf-8").splitlines())}
    for sid in short_ids:
        f = next(f for f in recs[sid]["factors"] if f["factor_id"] == "online_attention")
        assert f["display"] is False and f["data_missing"] is True and f["hold_reason"] == "data_missing"
        assert f["missing_reason"] == "online_unobservable"
    # ≥3자: 온라인 값은 마스크 전과 같고, 값이 있는 점포는 데이터 없음으로 보류되지 않는다
    long_ids = [s for s in ids if s not in short_ids]
    a = raw_online.query("origin == @last").set_index("store_id").loc[long_ids, of.FEATURES]
    b = online.query("origin == @last").set_index("store_id").loc[long_ids, of.FEATURES]
    pd.testing.assert_frame_equal(a, b)
    has_val = b.notna().all(axis=1)
    assert has_val.any()
    for sid in b.index[has_val]:
        f = next(f for f in recs[sid]["factors"] if f["factor_id"] == "online_attention")
        assert f["data_missing"] is False and f["missing_reason"] is None
