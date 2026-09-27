# -*- coding: utf-8 -*-
"""#28 상호 매칭 검수 보조 테스트 (합성 데이터)."""
from __future__ import annotations

import gzip
import json
import math

import numpy as np
import pandas as pd
import pytest

from src.analysis import name_match_review as nm

FIDS = ["tenure", "store_profile", "online_attention", "peer_sales"]


def _diagnosis(n=60, seed=0):
    rng = np.random.default_rng(seed)
    rows, probs = [], []
    for i in range(n):
        sid = f"GR_{i:04d}"
        kind = i % 4  # 0: 검토 대기, 1: 온라인 표시, 2: 온라인 데이터 없음, 3: 다른 요인만 보류
        for f in FIDS:
            display, missing = True, False
            if f == "online_attention" and kind == 0:
                display = False
            if f == "online_attention" and kind == 2:
                display, missing = False, True
            if f == "peer_sales" and kind == 3:
                display, missing = False, True
            rows.append({"store_id": sid, "gu": "마포구", "biz_type": "일반음식점", "factor_id": f,
                         "display": display, "data_missing": missing})
        probs.append({"store_id": sid, "probability_12m": float(rng.random())})
    return pd.DataFrame(rows), pd.DataFrame(probs)


def _licenses(ids):
    return pd.DataFrame({"store_id": ids, "name_raw": [f"가게{i}" for i in range(len(ids))],
                         "name_norm": [f"가게{i}" for i in range(len(ids))], "dong": "서교동"})


def _targets(n_pri=3, n_rnd=5, seed=7):
    d, p = _diagnosis()
    pool = nm.review_pool(d)
    prob = p.set_index("store_id")["probability_12m"]
    t, key = nm.select_targets(pool, prob, _licenses(sorted(d["store_id"].unique())),
                               n_priority=n_pri, n_random=n_rnd, seed=seed)
    return t, pool, p, key


def test_targets_pool_and_counts():
    t, pool, p, key = _targets()
    assert set(pool["store_id"]) == {f"GR_{i:04d}" for i in range(0, 60, 4)}  # 검토 대기만
    assert (key["groups"] == "priority").sum() == 3 and (key["groups"] == "random").sum() == 5
    assert set(t["store_id"]) <= set(pool["store_id"]) and t["store_id"].is_unique
    top3 = p[p["store_id"].isin(pool["store_id"])].nlargest(3, "probability_12m")["store_id"]
    assert set(key.loc[key["groups"] == "priority", "store_id"]) == set(top3)
    assert t["review_id"].tolist() == [f"R{i:02d}" for i in range(1, 9)]
    # 전달용 대상 목록: 선정 그룹·확률·폐업 관련 열 없음
    assert list(t.columns) == nm.TARGET_COLS and "group" not in t.columns
    nm.assert_blind(t.columns)
    assert not any(w in c for c in t.columns for w in ("prob", "band", "close", "폐업", "group"))
    # 보관용 키: review_id·store_id·groups·stratum, 대상 목록과 같은 점포·같은 순서
    assert list(key.columns) == nm.KEY_COLS
    assert key[["review_id", "store_id"]].equals(t[["review_id", "store_id"]])
    assert set(key["groups"]) == {"priority", "random"} and (key["stratum"] == "").all()


def test_targets_seed_reproducible_and_shuffled():
    a, _, _, ka = _targets(seed=11)
    b, _, _, kb = _targets(seed=11)
    c, _, _, _ = _targets(seed=12)
    pd.testing.assert_frame_equal(ka, kb)
    pd.testing.assert_frame_equal(a, b)
    assert a["store_id"].tolist() != c["store_id"].tolist()
    heads = [_targets(seed=s)[3]["groups"].head(3).tolist() for s in range(10)]
    assert any(g != ["priority"] * 3 for g in heads)  # priority가 항상 위에 몰리지 않는다


def test_assert_blind_rejects_risk_columns():
    with pytest.raises(ValueError):
        nm.assert_blind(["review_id", "probability_12m"])
    with pytest.raises(ValueError):
        nm.assert_blind(["review_id", "close_date"])


def _items(tmp_path, targets, per=8):
    p = tmp_path / "blog_items.jsonl.gz"
    ids = targets["store_id"].tolist()
    with gzip.open(p, "wt", encoding="utf-8") as f:
        for k, sid in enumerate(ids[:-1]):  # 마지막 점포는 매칭 글 0건
            for j in range(per):
                date = f"2026{(j % 9) + 1:02d}15" if j < 4 else f"2023{(j % 9) + 1:02d}15"
                f.write(json.dumps({"store_id": sid, "link": f"https://blog.naver.com/user{k}/{j}",
                                    "title": f"<b>가게{k}</b> 후기 &amp; 메뉴 {j}",
                                    "description": "<b>맛있</b>는 " + "가" * 300,
                                    "postdate": date, "matched": j != 1}, ensure_ascii=False) + "\n")
            # 이어받기 수집으로 생긴 중복 글
            f.write(json.dumps({"store_id": sid, "link": f"https://blog.naver.com/user{k}/0", "title": "dup",
                                "description": "", "postdate": "20260115", "matched": True}) + "\n")
        f.write(json.dumps({"store_id": "OTHER", "link": "x", "title": "", "description": "", "postdate": "",
                            "matched": True}) + "\n")
    return p


def test_sheet_shape_and_blind(tmp_path):
    t, _, _, _ = _targets()
    items = nm.read_items([_items(tmp_path, t)], set(t["store_id"]))
    assert not items.duplicated(["store_id", "link"]).any() and items["matched"].all()
    sheet = nm.build_sheet(t, items, per_store=5, seed=1)
    assert list(sheet.columns) == nm.SHEET_COLS
    assert "store_id" not in sheet.columns and "group" not in sheet.columns
    nm.assert_blind(sheet.columns)
    per = sheet[sheet["item_no"] > 0].groupby("review_id").size()
    assert per.max() <= 5 and len(per) == len(t) - 1
    zero = sheet[sheet["item_no"] == 0]
    assert len(zero) == 1 and zero["title"].iloc[0] == nm.NO_POSTS
    body = sheet[sheet["item_no"] > 0]
    assert not body["title"].str.contains("<|>|&amp;").any() and body["title"].str.contains("&").all()
    assert body["description"].str.len().max() <= nm.DESC_LEN
    assert not body["description"].str.contains("<b>").any()
    assert body["blog_name"].str.startswith("user").all()
    # 최근 12개월 글(2026년, 점포당 매칭 3건)을 먼저 채운다
    first = body.groupby("review_id").head(3)
    assert first["post_date"].str.startswith("2026").all()
    assert (sheet["verdict"] == "").all() and (sheet["note"] == "").all()


def test_sheet_seed_reproducible(tmp_path):
    t, _, _, _ = _targets()
    items = nm.read_items([_items(tmp_path, t)], set(t["store_id"]))
    a = nm.build_sheet(t, items, 3, seed=5)
    b = nm.build_sheet(t, items.sample(frac=1, random_state=0), 3, seed=5)
    pd.testing.assert_frame_equal(a, b)


def test_wilson_interval():
    lo, hi = nm.wilson(5, 20)
    assert math.isclose(lo, 0.1119, abs_tol=1e-3) and math.isclose(hi, 0.4687, abs_tol=1e-3)
    assert nm.wilson(0, 10)[0] == 0.0 and math.isclose(nm.wilson(10, 10)[1], 1.0)
    assert all(math.isnan(x) for x in nm.wilson(0, 0))


def test_summarize_rules(tmp_path):
    targets = pd.DataFrame({"review_id": ["R01", "R02", "R03", "R04"], "store_id": ["A", "B", "C", "D"]})
    key = targets.assign(group=["priority", "random", "random", "random"])
    v = {"R01": ["다른가게", "다른가게", "해당가게"],  # 2/3 ≥ 0.5 → 오탐
         "R02": ["해당가게", "다른가게", "판단불가"],  # 1/2 = 0.5 → 오탐 (경계 포함)
         "R03": ["해당가게", "해당가게", ""],         # 빈 verdict 제외 → 정상
         "R04": ["판단불가"]}                         # 판정 가능한 글 없음 → 판정불가
    rows = [{"review_id": r, "item_no": i + 1, "verdict": x} for r, xs in v.items() for i, x in enumerate(xs)]
    rows.append({"review_id": "R04", "item_no": 0, "verdict": ""})
    sheet = pd.DataFrame(rows)
    items, stores, n_blank = nm.judge(sheet, targets, key)
    assert n_blank == 1
    sv = dict(zip(stores["review_id"], stores["store_verdict"]))
    assert sv == {"R01": "오탐", "R02": "오탐", "R03": "정상", "R04": "판정불가"}
    tab = nm.rates(items, stores).set_index("group")
    assert tab.at["random", "items_decided"] == 4 and tab.at["random", "items_other"] == 1
    assert tab.at["random", "stores_fp"] == 1 and tab.at["random", "stores_decided"] == 2
    assert not math.isnan(tab.at["random", "store_ci_low"]) and math.isnan(tab.at["priority", "store_ci_low"])

    tp, sp, kp = tmp_path / "t.csv", tmp_path / "s.csv", tmp_path / "k.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    key.to_csv(kp, index=False, encoding="utf-8-sig")
    nm.main(["summarize", "--targets", str(tp), "--key", str(kp), "--sheet", str(sp), "--out", str(tmp_path / "out")])
    assert set(pd.read_csv(tmp_path / "out" / "name_match_rates.csv")["group"]) == {"priority", "random", "전체"}
    fp = pd.read_csv(tmp_path / "out" / "false_positive_stores.csv")
    assert list(fp.columns) == ["store_id"] and set(fp["store_id"]) == {"A", "B"}
    assert "store_id" not in pd.read_csv(tmp_path / "out" / "name_match_store_verdicts.csv").columns


def test_summarize_rejects_unknown_verdict():
    targets = pd.DataFrame({"review_id": ["R01"], "store_id": ["A"]})
    with pytest.raises(ValueError):
        nm.judge(pd.DataFrame({"review_id": ["R01"], "item_no": [1], "verdict": ["모름"]}), targets)


def test_summarize_without_key_reports_total_only(tmp_path, capsys):
    targets = pd.DataFrame({"review_id": ["R01", "R02"], "store_id": ["A", "B"]})
    sheet = pd.DataFrame({"review_id": ["R01", "R02"], "item_no": [1, 1], "verdict": ["다른가게", "해당가게"]})
    tp, sp = tmp_path / "t.csv", tmp_path / "s.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    nm.main(["summarize", "--targets", str(tp), "--sheet", str(sp), "--out", str(tmp_path / "out")])
    assert "--key" in capsys.readouterr().out  # 경고
    tab = pd.read_csv(tmp_path / "out" / "name_match_rates.csv")
    assert tab["group"].tolist() == ["전체"] and tab["store_ci_low"].isna().all()


def test_sheet_rejects_targets_with_group(tmp_path):
    t, _, _, key = _targets()
    items = _items(tmp_path, t)
    bad = tmp_path / "with_group.csv"
    t.merge(key[["review_id", "groups"]], on="review_id").to_csv(bad, index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="group"):
        nm.main(["sheet", "--targets", str(bad), "--items", str(items), "--out", str(tmp_path / "sheet.csv")])
    good = tmp_path / "targets.csv"
    t.to_csv(good, index=False, encoding="utf-8-sig")
    nm.main(["sheet", "--targets", str(good), "--items", str(items), "--out", str(tmp_path / "sheet.csv")])
    assert (tmp_path / "sheet.csv").exists() and (tmp_path / "sheet_README.md").exists()


def test_targets_cli_writes_two_files(tmp_path):
    d, p = _diagnosis()
    d.to_parquet(tmp_path / "diagnosis.parquet", index=False)
    p.to_parquet(tmp_path / "risk_scores.parquet", index=False)
    _licenses(sorted(d["store_id"].unique())).to_parquet(tmp_path / "lic.parquet", index=False)
    out = tmp_path / "review" / "name_match_targets.csv"
    nm.main(["targets", "--diagnosis", str(tmp_path / "diagnosis.parquet"), "--licenses", str(tmp_path / "lic.parquet"),
             "--n-priority", "2", "--n-random", "4", "--out", str(out)])
    t = pd.read_csv(out, encoding="utf-8-sig")
    k = pd.read_csv(out.parent / "name_match_key.csv", encoding="utf-8-sig")
    assert "group" not in t.columns and list(k.columns) == nm.KEY_COLS and len(t) == len(k) == 6


# ---------------------------------------------------------------------------- 짧은 상호 그룹
@pytest.mark.parametrize("raw, norm", [
    ("요즘", "요즘"),
    ("카페 오늘", "카페오늘"),
    ("(주)ABC Cafe", "주abccafe"),
    ("BHC-치킨!", "bhc치킨"),
    ("밥&술", "밥술"),
    ("커피２", "커피"),      # 전각 숫자·영문은 남지 않는다 (PR #21 규칙 그대로)
    ("ＡＢ", ""),
    ("ㅋㅋ", ""),           # 자모만 있으면 빈 문자열
    ("", ""),
    (None, ""),
])
def test_match_name_norm_pinned_to_pr21(raw, norm):
    assert nm.match_name_norm(raw) == norm


def test_is_short_name():
    assert nm.is_short_name("밥&술") and nm.is_short_name("요 즘") and nm.is_short_name("ＡＢ")
    assert not nm.is_short_name("오늘 (본점)") and not nm.is_short_name("Cafe")


def _short_setup():
    d, p = _diagnosis()
    lic = _licenses(sorted(d["store_id"].unique())).assign(gu="마포구", business_type="일반음식점", status_code="01")
    # 검토 대기 점포 두 곳을 짧은 상호로 — online_review 그룹과 겹치게
    lic.loc[lic["store_id"] == "GR_0000", "name_raw"] = "요 즘"
    lic.loc[lic["store_id"] == "GR_0004", ["name_raw", "status_code"]] = ["오늘!", "03"]
    extra = pd.DataFrame({"store_id": [f"SH_{i:03d}" for i in range(24)] + ["LONG_1"],
                          "name_raw": [f"밥{i % 10}" for i in range(24)] + ["카페 오늘"],
                          "name_norm": "x", "dong": "합정동", "gu": "마포구", "business_type": "휴게음식점",
                          "status_code": ["01" if i % 2 == 0 else "03" for i in range(24)] + ["03"]})
    lic = pd.concat([lic, extra], ignore_index=True)
    with_mentions = ["GR_0000", "GR_0004", "LONG_1"] + [f"SH_{i:03d}" for i in range(20)]  # SH_020~023은 언급 없음
    mentions = pd.DataFrame({"store_id": with_mentions * 2, "platform": "blog",
                             "year_month": ["2025-01"] * len(with_mentions) + ["2025-02"] * len(with_mentions),
                             "mention_count": "1"})
    return d, p, lic, mentions


def test_short_name_pool():
    _, _, lic, mentions = _short_setup()
    pool = nm.short_name_pool(lic, nm.blog_mention_totals(mentions))
    expect = {"GR_0000", "GR_0004"} | {f"SH_{i:03d}" for i in range(20)}  # 긴 상호·언급 없음 제외
    assert set(pool["store_id"]) == expect
    st = pool.set_index("store_id")["stratum"]
    assert st["GR_0000"] == nm.OPEN and st["GR_0004"] == nm.CLOSED
    assert (st == nm.OPEN).sum() == 11 and (st == nm.CLOSED).sum() == 11
    assert pool.set_index("store_id").at["SH_001", "biz_type"] == "휴게음식점"


def _select_with_short(n_short, seed=7, n_random=20):
    d, p, lic, mentions = _short_setup()
    short_pool = nm.short_name_pool(lic, nm.blog_mention_totals(mentions))
    return nm.select_targets(nm.review_pool(d), p.set_index("store_id")["probability_12m"], lic,
                             n_priority=3, n_random=n_random, seed=seed,
                             short_pool=short_pool, n_short_per_stratum=n_short)


def test_targets_short_group_stratified_and_blind():
    t, key = _select_with_short(n_short=5)
    short = key[nm.in_group(key["groups"], nm.SHORT)]
    assert (short["stratum"] == nm.OPEN).sum() == 5 and (short["stratum"] == nm.CLOSED).sum() == 5
    assert (key.loc[~nm.in_group(key["groups"], nm.SHORT), "stratum"] == "").all()
    # 전달용 대상 목록: 층·그룹 없음, 기존 blind 가드 통과
    assert list(t.columns) == nm.TARGET_COLS
    nm.assert_blind(t.columns)
    assert key[["review_id", "store_id"]].equals(t[["review_id", "store_id"]]) and t["store_id"].is_unique


def test_targets_overlap_listed_once_with_all_groups():
    # 검토 대기 15곳 전부(priority 3 + random 12)와 짧은 상호 22곳 전부 → 겹치는 2곳은 한 줄
    t, key = _select_with_short(n_short=50)
    assert len(t) == 15 + 22 - 2 and t["store_id"].is_unique
    g = key.set_index("store_id")["groups"]
    for sid in ("GR_0000", "GR_0004"):
        parts = g[sid].split(nm.GROUP_SEP)
        assert nm.SHORT in parts and len(parts) == 2 and parts[0] in ("priority", "random")
    assert key.set_index("store_id").at["GR_0004", "stratum"] == nm.CLOSED


def test_short_group_does_not_change_online_review_selection():
    d, p, lic, _ = _short_setup()
    _, base = nm.select_targets(nm.review_pool(d), p.set_index("store_id")["probability_12m"], lic,
                                n_priority=3, n_random=5, seed=7)
    _, key = _select_with_short(n_short=5, n_random=5)
    for g in ("priority", "random"):
        assert set(base.loc[nm.in_group(base["groups"], g), "store_id"]) == \
               set(key.loc[nm.in_group(key["groups"], g), "store_id"])


def test_targets_short_seed_reproducible():
    _, a = _select_with_short(n_short=5, seed=3)
    _, b = _select_with_short(n_short=5, seed=3)
    pd.testing.assert_frame_equal(a, b)
    picks = {frozenset(_select_with_short(n_short=5, seed=s)[1].query("stratum != ''")["store_id"]) for s in range(5)}
    assert len(picks) > 1


def test_newcombe_diff():
    d, lo, hi = nm.newcombe_diff(56, 70, 48, 80)  # Newcombe (1998) 예시: 0.2 (0.0524, 0.3339)
    assert math.isclose(d, 0.2) and math.isclose(lo, 0.0524, abs_tol=1e-3) and math.isclose(hi, 0.3339, abs_tol=1e-3)
    assert all(math.isnan(x) for x in nm.newcombe_diff(1, 0, 1, 2))


def test_summarize_short_strata(tmp_path):
    targets = pd.DataFrame({"review_id": ["R01", "R02", "R03", "R04", "R05"], "store_id": list("ABCDE")})
    key = targets.assign(groups=["random;short_name", "short_name", "short_name", "short_name", "priority"],
                         stratum=["영업", "영업", "폐업", "폐업", ""])
    v = {"R01": ["해당가게", "해당가게"],   # 영업 정상
         "R02": ["다른가게", "해당가게"],   # 영업 오탐
         "R03": ["다른가게", "다른가게"],   # 폐업 오탐
         "R04": ["다른가게", "판단불가"],   # 폐업 오탐
         "R05": ["다른가게"]}              # priority 오탐
    sheet = pd.DataFrame([{"review_id": r, "item_no": i + 1, "verdict": x}
                          for r, xs in v.items() for i, x in enumerate(xs)])
    items, stores, _ = nm.judge(sheet, targets, key)
    tab = nm.rates(items, stores).set_index("group")
    assert tab.index.tolist() == ["priority", "random", "short_name", "short_name:영업", "short_name:폐업", "전체"]
    assert tab.at["short_name", "stores_decided"] == 4 and tab.at["random", "stores_decided"] == 1  # R01은 두 그룹 모두
    assert tab.at["전체", "stores_decided"] == 5
    assert tab.at["short_name:영업", "stores_fp"] == 1 and tab.at["short_name:폐업", "stores_fp"] == 2
    assert not math.isnan(tab.at["short_name:폐업", "store_ci_low"]) and math.isnan(tab.at["priority", "store_ci_low"])
    gap = nm.stratum_gap(items, stores).set_index("level")
    assert math.isclose(gap.at["store", "diff_closed_minus_open"], 0.5)
    assert math.isclose(gap.at["item", "diff_closed_minus_open"], 3 / 3 - 1 / 4)
    assert gap.at["store", "diff_ci_low"] < 0.5 < gap.at["store", "diff_ci_high"]

    tp, sp, kp = tmp_path / "t.csv", tmp_path / "s.csv", tmp_path / "k.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    key.to_csv(kp, index=False, encoding="utf-8-sig")  # 빈 stratum은 CSV에서 NaN으로 읽힌다
    nm.main(["summarize", "--targets", str(tp), "--key", str(kp), "--sheet", str(sp), "--out", str(tmp_path / "out")])
    strata = pd.read_csv(tmp_path / "out" / "name_match_short_strata.csv")
    assert strata["level"].tolist() == ["store", "item"]
    assert "폐업 − 영업" in (tmp_path / "out" / "name_match_summary.md").read_text(encoding="utf-8")


def test_targets_cli_with_mentions(tmp_path):
    d, p, lic, mentions = _short_setup()
    d.to_parquet(tmp_path / "diagnosis.parquet", index=False)
    p.to_parquet(tmp_path / "risk_scores.parquet", index=False)
    lic.to_parquet(tmp_path / "lic.parquet", index=False)
    mentions.to_parquet(tmp_path / "mentions.parquet", index=False)
    out = tmp_path / "review" / "name_match_targets.csv"
    nm.main(["targets", "--diagnosis", str(tmp_path / "diagnosis.parquet"), "--licenses", str(tmp_path / "lic.parquet"),
             "--mentions", str(tmp_path / "mentions.parquet"), "--n-priority", "2", "--n-random", "4",
             "--n-short-per-stratum", "3", "--out", str(out)])
    t = pd.read_csv(out, encoding="utf-8-sig")
    k = pd.read_csv(out.parent / "name_match_key.csv", encoding="utf-8-sig", keep_default_na=False)
    assert list(t.columns) == nm.TARGET_COLS and list(k.columns) == nm.KEY_COLS
    assert (k["stratum"] == nm.OPEN).sum() == 3 and (k["stratum"] == nm.CLOSED).sum() == 3
    assert not t.isin([nm.OPEN, nm.CLOSED]).any().any()
