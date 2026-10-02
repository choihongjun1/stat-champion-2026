import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from src.analysis import short_name_appendix as app


def fixture():
    rows = []
    for origin, scores in (("2023Q1", [.1, .2, .1, .2]), ("2023Q2", [.8, .9, .9, .8])):
        for index, score in enumerate(scores):
            rows.append({"store_id": f"SYN-{index:06d}", "origin": origin,
                         "p_oof": score, "y": index % 2})
    return pd.DataFrame(rows), pd.DataFrame({"store_id": [f"SYN-{i:06d}" for i in range(4)], "is_short": [True, True, False, False]})


def test_separate_auc_and_counts():
    oof, flags = fixture()
    frame = app.prepare(oof, flags)
    result = app.calculate(frame)
    for label, part in zip(app.GROUPS, (frame[frame.is_short.eq(1)], frame[frame.is_short.eq(0)], frame)):
        group = next(x for x in result["groups"] if x["group"] == label)
        expected_mean = np.mean([roc_auc_score(x.y, x.p_oof) for _, x in part.groupby("origin")])
        assert group["mean_origin_auc"] == pytest.approx(expected_mean)
        assert group["pooled_auc"] == pytest.approx(roc_auc_score(part.y, part.p_oof))
        assert group["n_stores"] == part.store_id.nunique()
        assert group["n_rows"] == len(part)
        assert group["closure_rate"] == .5
        assert group["used_origins"] == 2
    assert result["groups"][0]["mean_origin_auc"] == 1
    assert result["groups"][0]["pooled_auc"] == .75
    assert result["difference"]["mean_origin_auc"] == .5
    assert result["difference"]["pooled_auc"] == .25


def test_bootstrap_reproducible_and_paired():
    oof, flags = fixture()
    frame = app.prepare(oof, flags)
    first = app.calculate(frame)
    assert first == app.calculate(frame)
    assert first["seed"] == 20261003 and first["n_boot"] == 1000
    # Every store contributes both origins; row bootstrap would break this relation.
    for row in first["groups"]:
        assert row["bootstrap_used_origins_range"] in ([0, 2], [2, 2])
    assert 0 < first["difference"]["mean_origin_auc_uncertainty"]["valid_replicates"] < 1000


def test_unavailable_origin():
    oof, flags = fixture()
    oof.loc[(oof.origin == "2023Q2") & oof.store_id.isin(flags.store_id[:2]), "y"] = 0
    result = app.calculate(app.prepare(oof, flags))
    assert result["groups"][0]["used_origins"] == 1
    assert result["groups"][0]["mean_origin_auc"] == 1
    assert next(x for x in result["origin_diagnostics"] if x["group"] == "≤2자" and x["origin"] == "2023Q2")["auc"] is None


@pytest.mark.parametrize("problem", ["missing", "duplicate", "invalid", "key"])
def test_flags_mismatch(problem):
    oof, flags = fixture()
    if problem == "missing":
        flags = flags.iloc[1:]
    elif problem == "duplicate":
        flags = pd.concat([flags, flags.iloc[:1]])
    elif problem == "invalid":
        flags["is_short"] = 2
    else:
        flags = flags.rename(columns={"store_id": "wrong"})
    with pytest.raises(ValueError):
        app.prepare(oof, flags)


def test_cluster_map_and_idx_flags():
    oof, flags = fixture()
    oof["idx"] = np.arange(len(oof))
    mapping = oof[["idx", "store_id"]].rename(columns={"store_id": "cluster_key"})
    indexed = pd.DataFrame({"idx": oof.idx, "is_short": oof.store_id.isin(flags.store_id[:2])})
    by_idx = app.prepare(oof.drop(columns="store_id"), indexed, mapping)
    result = app.calculate(by_idx)
    assert result == app.calculate(app.prepare(oof, flags))
    with pytest.raises(ValueError):
        app.prepare(oof.drop(columns="store_id"), indexed, mapping.iloc[1:])
    indexed.loc[4, "is_short"] = False
    with pytest.raises(ValueError):
        app.prepare(oof.drop(columns="store_id"), indexed, mapping)


def test_config_selection_and_duplicate_rejection():
    oof, flags = fixture()
    combined = pd.concat([oof.assign(config="현 설정"), oof.assign(config="튜닝", p_oof=.5)])
    assert len(app.prepare(combined, flags)) == len(oof)
    with pytest.raises(ValueError):
        app.prepare(pd.concat([oof, oof]), flags)
    with pytest.raises(ValueError):
        app.prepare(combined, flags, oof_config="없는 설정")


def test_missing_rate_keys():
    oof, flags = fixture()
    missing = oof[["store_id", "origin"]].assign(online_missing=[True, True, False, False] * 2)
    result = app.calculate(app.prepare(oof, flags, missing=missing))
    assert [x["online_missing_rate"] for x in result["groups"]] == [1, 0, .5]
    with pytest.raises(ValueError):
        app.prepare(oof, flags, missing=missing.iloc[1:])


def test_weighted_ties_match_repeated_sklearn():
    oof, flags = fixture()
    oof["p_oof"] = [.1, .1, .3, .9, .1, .2, .9, .9]
    frame = app.prepare(oof, flags)
    codes, _ = pd.factorize(frame.cluster, sort=True)
    counts = np.array([3, 0, 2, 1])
    plan = app.AUCPlan(frame, np.arange(len(frame)), codes)
    expanded = frame.iloc[np.repeat(np.arange(len(frame)), counts[codes])]
    assert plan.value(counts) == pytest.approx(roc_auc_score(expanded.y, expanded.p_oof))


def test_flag_helper_same_normalizer_no_original_names(tmp_path):
    from src.data.collect_online_presence import normalize_name
    names = ["가 나", "(주)가나", "합성상호비공개세글자", None]
    licenses = pd.DataFrame({"store_id": [f"SYN-{i:06d}" for i in range(4)], "name_raw": names})
    expected = [len(normalize_name(x or "")) <= 2 for x in names]
    assert app.build_flags(licenses).is_short.tolist() == expected
    source, output = tmp_path / "licenses.parquet", tmp_path / "flags.parquet"
    licenses.to_parquet(source, index=False)
    assert app.main(["--make-short-flag", str(source), "--flag-out", str(output)]) == 0
    flags = pd.read_parquet(output)
    assert list(flags) == ["store_id", "is_short"]
    assert "합성상호비공개세글자" not in flags.to_json(force_ascii=False)


def test_cli_outputs_and_privacy(tmp_path):
    oof, flags = fixture()
    # Extra raw-name columns in appendix inputs are never requested by the reader.
    oof["name_raw"] = "합성상호원문비공개"
    oof_path, flag_path = tmp_path / "oof.parquet", tmp_path / "flags.csv"
    oof.to_parquet(oof_path, index=False)
    flags.to_csv(flag_path, index=False)
    out = tmp_path / "out"
    argv = ["--oof", str(oof_path), "--short-flag", str(flag_path), "--out-dir", str(out)]
    assert app.main(argv) == 0
    for extension in ("csv", "md", "json"):
        text = (out / f"short_name_appendix.{extension}").read_text(encoding="utf-8")
        assert "합성상호원문비공개" not in text
        assert "SYN-000" not in text
        assert all(word not in text for word in ("효과", "유의", "확률"))
    md = (out / "short_name_appendix.md").read_text(encoding="utf-8")
    assert md.startswith(app.HEADER) and "미산출" in md
    result = json.loads((out / "short_name_appendix.json").read_text(encoding="utf-8"))
    assert result["n_boot"] == 1000 and result["seed"] == 20261003
    flags.iloc[1:].to_csv(flag_path, index=False)
    assert app.main(argv) == 1
    assert not list(out.glob("short_name_appendix.*"))


def test_ci_matches_explicit_store_resampling():
    oof, flags = fixture()
    frame = app.prepare(oof, flags)
    result = app.calculate(frame, n_boot=37)
    stores = sorted(frame.cluster.unique())
    rng = np.random.default_rng(app.SEED)
    values = {group: [[], []] for group in app.GROUPS + ("≤2자 − ≥3자",)}
    for _ in range(37):
        sampled = rng.integers(len(stores), size=len(stores))
        expanded = pd.concat([frame.loc[frame.cluster.eq(stores[index])] for index in sampled])
        points = {}
        for group, part in zip(app.GROUPS, (expanded[expanded.is_short.eq(1)], expanded[expanded.is_short.eq(0)], expanded)):
            origins = [roc_auc_score(sub.y, sub.p_oof) for _, sub in part.groupby("origin") if sub.y.nunique() == 2]
            points[group] = [float(np.mean(origins)) if origins else float("nan"),
                             roc_auc_score(part.y, part.p_oof) if part.y.nunique() == 2 else float("nan")]
            for metric in range(2):
                values[group][metric].append(points[group][metric])
        for metric in range(2):
            values["≤2자 − ≥3자"][metric].append(points["≤2자"][metric] - points["≥3자"][metric])
    for row in result["groups"] + [result["difference"]]:
        for index, metric in enumerate(("mean_origin_auc", "pooled_auc")):
            expected = np.asarray(values[row["group"]][index])
            expected = expected[np.isfinite(expected)]
            assert row[metric + "_uncertainty"]["ci95"] == pytest.approx(np.quantile(expected, [.025, .975]))


def test_online_feature_table_input(tmp_path):
    from src.data.online_features import FEATURES
    oof, flags = fixture()
    paths = [tmp_path / x for x in ("oof.parquet", "flags.parquet", "online.parquet")]
    oof.to_parquet(paths[0], index=False)
    flags.to_parquet(paths[1], index=False)
    online = oof[["store_id", "origin"]].copy()
    for col in FEATURES:
        online[col] = [np.nan, np.nan, 0, 1] * 2
    online.to_parquet(paths[2], index=False)
    assert app.main(["--oof", str(paths[0]), "--short-flag", str(paths[1]), "--online-missing", str(paths[2]), "--out-dir", str(tmp_path / "out")]) == 0
    result = json.loads((tmp_path / "out/short_name_appendix.json").read_text(encoding="utf-8"))
    assert [x["online_missing_rate"] for x in result["groups"]] == [1, 0, .5]
