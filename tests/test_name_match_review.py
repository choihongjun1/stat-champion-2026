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
            display, missing, hold = True, False, ""
            if f == "online_attention" and kind == 0:
                display, hold = False, "online_review"
            if f == "online_attention" and kind == 2:
                display, missing, hold = False, True, "data_missing"
            if f == "peer_sales" and kind == 3:
                display, missing, hold = False, True, "data_missing"
            rows.append({"store_id": sid, "gu": "마포구", "biz_type": "일반음식점", "factor_id": f,
                         "display": display, "data_missing": missing, "hold_reason": hold})
        probs.append({"store_id": sid, "probability_12m": float(rng.random())})
    return pd.DataFrame(rows), pd.DataFrame(probs)


def _licenses(ids):
    return pd.DataFrame({"store_id": ids, "name_raw": [f"가게{i}" for i in range(len(ids))],
                         "name_norm": [f"가게{i}" for i in range(len(ids))], "dong": "서교동"})


def _filter_args(tmp_path, store_ids, serve_as_of="2026-06-30"):
    """round2 날짜 필터 필수 입력(--licenses·--master·--serve-as-of) — 인허가 2015년, 영업 중, master 2020Q1~2025Q2."""
    lic = pd.DataFrame({"store_id": list(store_ids), "license_date": pd.Timestamp("2015-01-01"), "close_date": pd.NaT})
    master = pd.DataFrame({"store_id": list(store_ids), "origin_start": pd.Timestamp("2020-01-01"),
                           "origin_end": pd.Timestamp("2025-06-30")})
    lp, mp = tmp_path / "lic.parquet", tmp_path / "master.parquet"
    lic.to_parquet(lp, index=False)
    master.to_parquet(mp, index=False)
    return ["--licenses", str(lp), "--master", str(mp), "--serve-as-of", serve_as_of]


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
         "R03": ["해당가게", "해당가게"],             # 정상
         "R04": ["판단불가"]}                         # 판정 가능한 글 없음 → 판정불가
    rows = [{"review_id": r, "item_no": i + 1, "verdict": x} for r, xs in v.items() for i, x in enumerate(xs)]
    rows.append({"review_id": "R04", "item_no": 0, "verdict": ""})  # 매칭 글 없음 sentinel — item_no=0은 검사 대상 아님
    sheet = pd.DataFrame(rows)
    items, stores, n_blank = nm.judge(sheet, targets, key)
    assert n_blank == 0
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


def test_judge_raises_on_blank_verdict():
    """#39 리뷰 M3: 미입력을 조용히 빼지 않는다 — 판단불가와 미입력은 다르다."""
    targets = pd.DataFrame({"review_id": ["R01", "R02"], "store_id": ["A", "B"]})
    sheet = pd.DataFrame({"review_id": ["R01", "R02"], "item_no": [1, 1], "verdict": ["해당가게", ""]})
    with pytest.raises(ValueError, match="미입력"):
        nm.judge(sheet, targets)
    # item_no=0(매칭 글 없음 sentinel)의 빈 verdict는 대상이 아니다 — 정상 통과
    sheet0 = pd.DataFrame({"review_id": ["R01", "R02"], "item_no": [1, 0], "verdict": ["해당가게", ""]})
    items, stores, n_blank = nm.judge(sheet0, targets)
    assert n_blank == 0 and len(items) == 1


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
    # 가중치용 메타: 층별 모집단 크기 (점포 식별 정보 없음)
    meta = json.loads((out.parent / "name_match_key_meta.json").read_text(encoding="utf-8"))
    assert meta["short_pool_n"] == {nm.OPEN: 11, nm.CLOSED: 11} and meta["seed"] == 20260927
    assert meta["online_review_pool_n"] == 15


def test_weighted_rate():
    pool = {nm.OPEN: 1524, nm.CLOSED: 6624}
    est, lo, hi = nm.weighted_rate({nm.OPEN: (5, 50), nm.CLOSED: (20, 50)}, pool)
    w_c = 6624 / (1524 + 6624)
    assert math.isclose(est, (1 - w_c) * 0.1 + w_c * 0.4)  # 비가중 (5+20)/100 = 0.25와 다르다
    assert 0 <= lo < est < hi <= 1
    # 모집단이 같은 크기면 층별 같은 수 표본의 비가중 비율과 같다
    same, _, _ = nm.weighted_rate({nm.OPEN: (5, 50), nm.CLOSED: (20, 50)}, {nm.OPEN: 1, nm.CLOSED: 1})
    assert math.isclose(same, 0.25)
    # 한 층이 오탐 0이어도 구간 폭이 0이 되지 않는다 (Wilson 결합)
    _, lo0, hi0 = nm.weighted_rate({nm.OPEN: (0, 50), nm.CLOSED: (0, 50)}, pool)
    assert lo0 == 0.0 and hi0 > 0
    assert all(math.isnan(x) for x in nm.weighted_rate({nm.OPEN: (0, 0), nm.CLOSED: (1, 2)}, pool))


def test_summarize_short_weighted(tmp_path):
    targets = pd.DataFrame({"review_id": ["R01", "R02", "R03", "R04"], "store_id": list("ABCD"),
                            "biz_type": ["미용업", "일반음식점", "미용업", "미용업"]})
    key = targets.assign(groups="short_name", stratum=["영업", "영업", "폐업", "폐업"])
    sheet = pd.DataFrame({"review_id": ["R01", "R02", "R03", "R04"], "item_no": 1,
                          "verdict": ["해당가게", "해당가게", "다른가게", "해당가게"]})  # 영업 0/2, 폐업 1/2
    pool = {nm.OPEN: 1524, nm.CLOSED: 6624}
    items, stores, _ = nm.judge(sheet, targets, key)
    tab = nm.rates(items, stores, pool).set_index("group")
    w_c = 6624 / 8148
    assert bool(tab.at["short_name", "weighted"]) and math.isclose(tab.at["short_name", "store_fp_rate"], w_c * 0.5)
    assert math.isclose(tab.at["short_name", "store_fp_rate_unweighted"], 0.25)
    assert not tab.at["short_name:폐업", "weighted"]  # 층별 행은 가중하지 않는다
    assert math.isclose(tab.at["short_name:폐업", "store_fp_rate"], 0.5)
    # 층 차이(Newcombe)는 가중과 무관
    assert math.isclose(nm.stratum_gap(items, stores).set_index("level").at["store", "diff_closed_minus_open"], 0.5)

    tp, sp, kp = tmp_path / "t.csv", tmp_path / "s.csv", tmp_path / "name_match_key.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    key.to_csv(kp, index=False, encoding="utf-8-sig")
    nm.key_meta_path(kp).write_text(json.dumps({"short_pool_n": pool}, ensure_ascii=False), encoding="utf-8")
    nm.main(["summarize", "--targets", str(tp), "--key", str(kp), "--sheet", str(sp), "--out", str(tmp_path / "out")])
    r = pd.read_csv(tmp_path / "out" / "name_match_rates.csv").set_index("group")
    assert math.isclose(r.at["short_name", "store_fp_rate"], w_c * 0.5)
    md = (tmp_path / "out" / "name_match_summary.md").read_text(encoding="utf-8")
    assert "모집단 가중" in md and "비가중 25.0%" in md and "1,524 : 폐업 6,624" in md
    comp = pd.read_csv(tmp_path / "out" / "name_match_short_biz_composition.csv")
    assert comp.set_index(["stratum", "biz_type"]).at[("영업", "미용업"), "n"] == 1


def test_summarize_short_without_meta_is_unweighted(tmp_path, capsys):
    targets = pd.DataFrame({"review_id": ["R01", "R02"], "store_id": ["A", "B"]})
    key = targets.assign(groups="short_name", stratum=["영업", "폐업"])
    sheet = pd.DataFrame({"review_id": ["R01", "R02"], "item_no": 1, "verdict": ["해당가게", "다른가게"]})
    tp, sp, kp = tmp_path / "t.csv", tmp_path / "s.csv", tmp_path / "k.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    key.to_csv(kp, index=False, encoding="utf-8-sig")
    nm.main(["summarize", "--targets", str(tp), "--key", str(kp), "--sheet", str(sp), "--out", str(tmp_path / "out")])
    assert "가중하지 못했다" in capsys.readouterr().out
    r = pd.read_csv(tmp_path / "out" / "name_match_rates.csv").set_index("group")
    assert not r.at["short_name", "weighted"] and math.isclose(r.at["short_name", "store_fp_rate"], 0.5)


# ---------------------------------------------------------------------------- #39 리뷰 M1/M2/M4/재사용
def test_sha256_file(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("hello", encoding="utf-8")
    import hashlib
    assert nm.sha256_file(p) == hashlib.sha256(b"hello").hexdigest()


def test_serve_meta_near(tmp_path):
    (tmp_path / "serve_meta.json").write_text(json.dumps({"score_origin": "2026Q2", "as_of": "2026-06-30"}),
                                              encoding="utf-8")
    m = nm.serve_meta_near(tmp_path / "diagnosis.parquet")
    assert m["serve_score_origin"] == "2026Q2" and m["serve_as_of"] == "2026-06-30" and "serve_meta_sha256" in m
    assert nm.serve_meta_near(tmp_path / "other" / "diagnosis.parquet") == {}


def test_review_pool_uses_hold_reason():
    d, _ = _diagnosis()
    pool = nm.review_pool(d)
    assert set(pool["store_id"]) == {f"GR_{i:04d}" for i in range(0, 60, 4)}
    # data_missing인 online_attention 행(kind==2)은 온라인 표시 보류가 아니다 — pool에 없어야 한다
    assert not set(pool["store_id"]) & {f"GR_{i:04d}" for i in range(2, 60, 4)}


def test_master_base_span():
    m = pd.DataFrame({"store_id": ["A", "A", "A", "B"],
                      "origin_start": pd.to_datetime(["2021-01-01", "2021-04-01", "2021-07-01", "2022-01-01"]),
                      "origin_end": pd.to_datetime(["2021-03-31", "2021-06-30", "2021-09-30", "2022-03-31"])})
    span = nm.master_base_span(m).set_index("store_id")
    assert span.at["A", "span_start"] == pd.Timestamp("2020-10-01")  # 첫 origin_start(2021-01-01) − 3개월
    assert span.at["A", "span_end"] == pd.Timestamp("2021-09-30")
    assert span.at["B", "span_start"] == pd.Timestamp("2021-10-01")


def test_date_bounds_by_group_and_intersection():
    lic = pd.DataFrame({"store_id": ["S", "P", "O"],
                        "license_date": pd.to_datetime(["2020-01-01", None, "2019-01-01"]),
                        "close_date": pd.to_datetime(["2023-01-01", None, None])})
    key = pd.DataFrame({"store_id": ["S", "P", "O"], "groups": ["short_name", "random", "short_name;priority"]})
    span = pd.DataFrame({"store_id": ["S", "O"], "span_start": pd.to_datetime(["2020-06-01", "2018-01-01"]),
                        "span_end": pd.to_datetime(["2022-06-01", "2026-01-01"])})
    b = nm.date_bounds_by_group(lic, key, master_span=span, serve_as_of="2026-06-30")
    assert b["S"] == {"short_name": (pd.Timestamp("2020-06-01"), pd.Timestamp("2022-06-01"))}  # span이 더 좁음
    assert b["P"] == {"random": (None, pd.Timestamp("2026-06-30"))}  # priority: 하한 없음, serve_as_of만 상한
    # 겹침 점포(O)는 그룹마다 자기 구간을 따로 갖는다 — 교집합하지 않는다(추가 리뷰)
    assert b["O"] == {"short_name": (pd.Timestamp("2019-01-01"), pd.Timestamp("2026-01-01")),
                      "priority": (None, pd.Timestamp("2026-06-30"))}

    b_nocutoff = nm.date_bounds_by_group(lic, key, master_span=span, serve_as_of=None)
    assert b_nocutoff["P"] == {"random": (None, None)}  # serve_as_of 없으면 priority는 무제한


def test_within_union_uses_any_group_bound():
    bounds_by_group = {"O": {"short_name": (pd.Timestamp("2019-01-01"), pd.Timestamp("2020-01-01")),
                             "priority": (None, pd.Timestamp("2026-06-30"))}}
    dates = pd.to_datetime(pd.Series(["2019-06-01", "2021-01-01", "2027-01-01"]))
    store_ids = pd.Series(["O", "O", "O"])
    # 2019-06-01: 둘 다 통과 / 2021-01-01: short_name 밖이지만 priority 안(합집합) / 2027-01-01: 둘 다 밖
    assert nm._within_union(dates, store_ids, bounds_by_group).tolist() == [True, True, False]
    # 정보가 없는 store_id는 제한 없음
    assert nm._within_union(pd.to_datetime(pd.Series(["2099-01-01"])), pd.Series(["Z"]),
                            bounds_by_group).tolist() == [True]


def test_apply_date_bounds_filters_rows():
    items = pd.DataFrame({"store_id": ["S", "S", "P"], "postdate": ["20191231", "20210101", "20991231"]})
    bounds = {"S": (pd.Timestamp("2020-01-01"), pd.Timestamp("2022-01-01")), "P": (None, pd.Timestamp("2026-06-30"))}
    kept, n_excl = nm.apply_date_bounds(items, bounds)
    assert n_excl == 2 and kept["postdate"].tolist() == ["20210101"]
    # bounds에 없는 store_id는 제한 없음
    items2 = pd.DataFrame({"store_id": ["X"], "postdate": ["20991231"]})
    kept2, n2 = nm.apply_date_bounds(items2, bounds)
    assert n2 == 0 and len(kept2) == 1


def test_stratum_biz_composition():
    targets = pd.DataFrame({"review_id": ["R1", "R2", "R3", "R4"], "biz_type": ["미용업", "일반음식점", "미용업", "미용업"]})
    key = pd.DataFrame({"review_id": ["R1", "R2", "R3", "R4"], "groups": ["short_name"] * 4,
                        "stratum": ["영업", "영업", "폐업", "폐업"]})
    comp = nm.stratum_biz_composition(targets, key).set_index(["stratum", "biz_type"])
    assert comp.at[("영업", "미용업"), "n"] == 1 and comp.at[("영업", "일반음식점"), "n"] == 1
    assert comp.at[("폐업", "미용업"), "n"] == 2
    assert math.isclose(comp.at[("영업", "미용업"), "share"], 0.5)
    assert math.isclose(comp.at[("폐업", "미용업"), "share"], 1.0)


def test_carry_over_reuses_matching_pairs_and_flags_blocked():
    old_key = pd.DataFrame({"review_id": ["R1", "R2", "R3"], "store_id": ["A", "B", "C"],
                            "groups": ["short_name"] * 3, "stratum": ["영업", "폐업", "영업"]})
    old_sheet = pd.DataFrame({
        "review_id": ["R1", "R1", "R2", "R2", "R3"], "item_no": [1, 2, 1, 0, 1],
        "post_date": ["2021-05-01", "2021-06-01", "2021-01-01", "", "2021-01-01"],
        "link": ["a1", "a2", "b1", "", "c1"], "verdict": ["해당가게", "다른가게", "해당가게", "", "판단불가"], "note": ""})
    # 새 회차: A·C는 그대로, B는 빠지고(모집단 변경), D는 새로 뽑힘 — 재사용할 옛 판정이 없다
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "D"],
                            "groups": ["short_name", "short_name"], "stratum": ["영업", "폐업"]})
    new_targets = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "D"]})

    reused, need, stats = nm.carry_over(old_sheet, old_key, new_targets, new_key)
    assert set(reused["review_id"]) == {"N1"} and len(reused) == 2  # A의 글 2건만 재사용 (B는 new_key에 없음)
    assert set(reused["link"]) == {"a1", "a2"} and "store_id" not in stats
    nd = need.set_index("review_id")
    assert nd.at["N1", "n_valid"] == 2 and nd.at["N1", "n_needed"] == 1  # 목표 3건에서 1건 부족(추가 리뷰)
    assert nd.at["N2", "n_valid"] == 0 and nd.at["N2", "n_needed"] == 3  # D는 재사용 글이 하나도 없다
    assert stats == {"n_reused_items": 2, "n_stores_total": 2, "n_stores_covered": 1, "n_stores_blocked": 1,
                     "covered_by_group": {"priority": 0, "random": 0, "short_name": 1},
                     "blocked_by_group": {"priority": 0, "random": 0, "short_name": 1},
                     "n_store_group_needing_more": 2}


def test_carry_over_applies_date_bounds_to_old_verdicts():
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    old_sheet = pd.DataFrame({"review_id": ["R1", "R1"], "item_no": [1, 2], "post_date": ["2019-01-01", "2021-06-01"],
                              "link": ["old", "new"], "verdict": ["해당가게", "다른가게"], "note": ""})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    new_targets = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"]})
    bounds_by_group = {"A": {"short_name": (pd.Timestamp("2020-01-01"), pd.Timestamp("2022-01-01"))}}
    reused, need, stats = nm.carry_over(old_sheet, old_key, new_targets, new_key, bounds_by_group)
    assert reused["link"].tolist() == ["new"]  # 인허가일 이전 글(old)은 M1로 제외
    assert need.set_index("review_id").at["N1", "n_valid"] == 1


def test_cmd_round2_end_to_end(tmp_path):
    old_key = pd.DataFrame({"review_id": ["R1", "R2"], "store_id": ["A", "B"],
                            "groups": ["priority", "random"], "stratum": ["", ""]})
    old_sheet = pd.DataFrame({"review_id": ["R1", "R2"], "item_no": [1, 1], "post_date": ["2021-01-01", "2021-01-01"],
                              "link": ["a1", "b1"], "verdict": ["해당가게", "다른가게"], "note": ""})
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "C"],
                            "groups": ["priority", "random"], "stratum": ["", ""]})
    new_targets = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "C"], "name_raw": ["가A", "가C"],
                                "name_norm": ["가a", "가c"], "gu": ["마포구"] * 2, "dong": ["서교동"] * 2,
                                "biz_type": ["일반음식점"] * 2})
    paths = {n: tmp_path / f"{n}.csv" for n in ("old_sheet", "old_key", "new_targets", "new_key")}
    old_sheet.to_csv(paths["old_sheet"], index=False, encoding="utf-8-sig")
    old_key.to_csv(paths["old_key"], index=False, encoding="utf-8-sig")
    new_targets.to_csv(paths["new_targets"], index=False, encoding="utf-8-sig")
    new_key.to_csv(paths["new_key"], index=False, encoding="utf-8-sig")
    out = tmp_path / "out"
    nm.main(["round2", "--old-sheet", str(paths["old_sheet"]), "--old-key", str(paths["old_key"]),
             "--new-targets", str(paths["new_targets"]), "--new-key", str(paths["new_key"]), "--out", str(out)]
            + _filter_args(tmp_path, ["A", "B", "C"]))
    t2 = pd.read_csv(out / "name_match_targets_round2.csv", encoding="utf-8-sig")
    s2 = pd.read_csv(out / "name_match_sheet_round2.csv", encoding="utf-8-sig")
    # A는 1건 재사용됐지만 목표(3건)에는 못 미치고, C는 재사용이 전혀 없다 — --raw 없이는 둘 다 대상에 남고
    # 판정표는 0행이다(추가 리뷰: 점포×그룹 단위 3건 채우기, 원본 없으면 못 채운다).
    assert set(t2["review_id"]) == {"N1", "N2"} and len(s2) == 0
    assert list(s2.columns) == nm.SHEET_COLS
    report = json.loads((out / "round2_report.json").read_text(encoding="utf-8"))
    assert report["n_reused_items"] == 1 and report["n_new_items_found"] == 0
    assert report["n_stores_pending_raw_access"] == 2
    assert "store_id" not in json.dumps(report)


def test_cmd_round2_defaults_serve_as_of_from_new_key_meta(tmp_path):
    """실제 버그: serve_as_of를 --targets 옆 serve_meta.json에서 찾으면 안 된다 (없는 경로) —
    targets가 아니라 new_key의 메타(name_match_key_meta.json, cmd_targets가 써 둔 값)에서 읽어야 한다."""
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "groups": ["random"], "stratum": [""]})
    old_sheet = pd.DataFrame({"review_id": ["R1", "R1"], "item_no": [1, 2],
                              "post_date": ["2021-01-01", "2026-09-01"],  # 2건째는 serve_as_of 이후
                              "link": ["a1", "a2"], "verdict": ["해당가게", "다른가게"], "note": ""})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["random"], "stratum": [""]})
    new_targets = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "name_raw": ["가A"], "name_norm": ["가a"],
                                "gu": ["마포구"], "dong": ["서교동"], "biz_type": ["일반음식점"]})
    lic = pd.DataFrame({"store_id": ["A"], "license_date": pd.to_datetime(["2015-01-01"]), "close_date": [pd.NaT]})
    paths = {n: tmp_path / f"{n}.csv" for n in ("old_sheet", "old_key", "new_targets", "new_key")}
    old_sheet.to_csv(paths["old_sheet"], index=False, encoding="utf-8-sig")
    old_key.to_csv(paths["old_key"], index=False, encoding="utf-8-sig")
    new_targets.to_csv(paths["new_targets"], index=False, encoding="utf-8-sig")
    new_key.to_csv(paths["new_key"], index=False, encoding="utf-8-sig")
    nm.key_meta_path(paths["new_key"]).write_text(json.dumps({"serve_as_of": "2026-06-30"}), encoding="utf-8")
    lic_path = tmp_path / "lic.parquet"
    lic.to_parquet(lic_path, index=False)
    master_path = tmp_path / "master.parquet"
    pd.DataFrame({"store_id": ["A"], "origin_start": pd.Timestamp("2020-01-01"),
                  "origin_end": pd.Timestamp("2025-06-30")}).to_parquet(master_path, index=False)
    out = tmp_path / "out"
    # --serve-as-of를 주지 않는다 — new_key 메타에서 자동으로 읽혀야 한다
    nm.main(["round2", "--old-sheet", str(paths["old_sheet"]), "--old-key", str(paths["old_key"]),
             "--new-targets", str(paths["new_targets"]), "--new-key", str(paths["new_key"]),
             "--licenses", str(lic_path), "--master", str(master_path), "--out", str(out)])
    report = json.loads((out / "round2_report.json").read_text(encoding="utf-8"))
    assert report["n_reused_items"] == 1  # 2026-09-01 글은 제외되고 1건만 재사용


# ---------------------------------------------------------------------------- 추가 리뷰: 겹침 점포·round2 --raw·merge
def test_rates_group_view_recomputes_verdict_for_overlap_store_with_date_bounds():
    """겹치는 점포는 그룹마다 자기 구간을 통과한 글로 점포 판정을 다시 낸다 — 같은 점포가 한 그룹에서는
    오탐, 다른 그룹에서는 정상일 수 있다(추가 리뷰: 추출은 합집합, 집계는 그룹별 구간)."""
    targets = pd.DataFrame({"review_id": ["R1"], "store_id": ["O"]})
    key = pd.DataFrame({"review_id": ["R1"], "store_id": ["O"], "groups": ["short_name;random"], "stratum": ["영업"]})
    sheet = pd.DataFrame({
        "review_id": ["R1", "R1", "R1"], "item_no": [1, 2, 3],
        "post_date": ["2019-06-01", "2019-07-01", "2025-01-01"],  # 처음 둘은 short_name 구간, 셋째는 random 구간
        "verdict": ["다른가게", "다른가게", "해당가게"]})
    items, stores, _ = nm.judge(sheet, targets, key)
    # random 구간은 보통 하한이 없지만(#39 M1), 이 테스트는 _group_view가 그룹마다 다른 구간으로 다시
    # 거르는 매커니즘 자체를 확인하는 것이라 임의의 구간을 쓴다 — 셋째 글(2025-01-01)만 통과시킨다.
    bounds_by_group = {"O": {"short_name": (pd.Timestamp("2019-01-01"), pd.Timestamp("2020-01-01")),
                             "random": (pd.Timestamp("2024-01-01"), pd.Timestamp("2026-06-30"))}}
    tab = nm.rates(items, stores, bounds_by_group=bounds_by_group).set_index("group")
    assert tab.at["short_name", "stores_fp"] == 1 and tab.at["short_name", "items_decided"] == 2  # 오탐
    assert tab.at["random", "stores_fp"] == 0 and tab.at["random", "items_decided"] == 1  # 같은 점포, 정상
    assert tab.at["전체", "items_decided"] == 3  # "전체"는 추출 시점 합집합 그대로, 다시 거르지 않는다


def test_fill_from_raw_tops_up_to_three_and_records_shortfall():
    """점포×그룹의 유효 판정 글이 3건 미만이면 그 그룹 구간 안 원본 글로 3건까지 채운다. 이미 판정한 글은
    제외하고, 구간 안 글이 부족하면 있는 만큼 쓰고 실제 글 수를 review_id별로 기록한다."""
    raw = pd.DataFrame({
        "store_id": ["A", "A", "A", "A", "B"], "link": ["a1", "a2", "a3", "a4", "b1"],
        "title": ["t"] * 5, "description": ["d"] * 5,
        "postdate": ["20210101", "20210201", "20210301", "20210401", "20210101"], "matched": [True] * 5})
    need = pd.DataFrame({"review_id": ["N1", "N2"], "group": ["short_name", "short_name"],
                        "n_valid": [1, 0], "n_needed": [2, 3]})
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"],
                            "groups": ["short_name", "short_name"], "stratum": ["영업", "폐업"]})
    new_targets = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "name_raw": ["가A", "가B"],
                                "gu": ["마포구"] * 2, "dong": ["서교동"] * 2, "biz_type": ["일반음식점"] * 2})
    sheet, actual = nm.fill_from_raw(raw, need, new_key, new_targets, None, {"A": {"a1"}}, seed=20260927)
    assert actual["N1"] == 2 and "a1" not in set(sheet.loc[sheet["review_id"] == "N1", "link"])  # 이미 판정한 글 제외
    assert actual["N2"] == 1  # B는 원본에 매칭 글이 1건뿐 — 있는 만큼만
    assert list(sheet.columns) == nm.SHEET_COLS
    assert (sheet["verdict"] == "").all() and (sheet["item_no"] >= 1).all()


def test_fill_from_raw_respects_group_window():
    raw = pd.DataFrame({"store_id": ["A", "A"], "link": ["old", "new"], "title": ["t"] * 2, "description": ["d"] * 2,
                        "postdate": ["20180101", "20210101"], "matched": [True] * 2})
    need = pd.DataFrame({"review_id": ["N1"], "group": ["short_name"], "n_valid": [0], "n_needed": [3]})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    new_targets = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "name_raw": ["가A"], "gu": ["마포구"],
                                "dong": ["서교동"], "biz_type": ["일반음식점"]})
    bounds = {"A": {"short_name": (pd.Timestamp("2020-01-01"), pd.Timestamp("2022-01-01"))}}
    sheet, actual = nm.fill_from_raw(raw, need, new_key, new_targets, bounds, {}, seed=1)
    assert sheet["link"].tolist() == ["new"] and actual["N1"] == 1  # old는 구간 밖 — 있는 만큼(1건)만 채움


def test_cmd_round2_with_raw_fills_sheet(tmp_path):
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    old_sheet = pd.DataFrame({"review_id": ["R1"], "item_no": [1], "post_date": ["2021-01-01"],
                              "link": ["a1"], "verdict": ["해당가게"], "note": ""})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    new_targets = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "name_raw": ["가A"], "name_norm": ["가a"],
                                "gu": ["마포구"], "dong": ["서교동"], "biz_type": ["일반음식점"]})
    paths = {n: tmp_path / f"{n}.csv" for n in ("old_sheet", "old_key", "new_targets", "new_key")}
    old_sheet.to_csv(paths["old_sheet"], index=False, encoding="utf-8-sig")
    old_key.to_csv(paths["old_key"], index=False, encoding="utf-8-sig")
    new_targets.to_csv(paths["new_targets"], index=False, encoding="utf-8-sig")
    new_key.to_csv(paths["new_key"], index=False, encoding="utf-8-sig")
    raw_path = tmp_path / "blog_items.jsonl.gz"
    with gzip.open(raw_path, "wt", encoding="utf-8") as f:
        for j, link in enumerate(["a1", "a2", "a3"]):  # a1은 이미 판정됨(재사용) — 새 글은 a2·a3뿐
            f.write(json.dumps({"store_id": "A", "link": link, "title": f"글{j}", "description": "",
                                "postdate": "20210201", "matched": True}) + "\n")
    out = tmp_path / "out"
    nm.main(["round2", "--old-sheet", str(paths["old_sheet"]), "--old-key", str(paths["old_key"]),
             "--new-targets", str(paths["new_targets"]), "--new-key", str(paths["new_key"]),
             "--raw", str(raw_path), "--out", str(out)] + _filter_args(tmp_path, ["A"]))
    s2 = pd.read_csv(out / "name_match_sheet_round2.csv", encoding="utf-8-sig")
    assert set(s2["link"]) == {"a2", "a3"}  # a1(재사용)은 다시 뽑지 않는다
    report = json.loads((out / "round2_report.json").read_text(encoding="utf-8"))
    assert report["n_new_items_found"] == 2
    assert "store_id" not in json.dumps(report)


def test_merge_judgments_new_wins_on_conflict_and_reports_distribution():
    reused = pd.DataFrame({"review_id": ["R1", "R1"], "store_id": ["A", "A"], "link": ["l1", "l2"],
                          "post_date": ["2021-01-01", "2021-02-01"], "verdict": ["해당가게", "다른가게"], "note": ["", ""]})
    new_sheet = pd.DataFrame({"review_id": ["N1", "N1"], "item_no": [1, 2], "link": ["l2", "l3"],
                              "post_date": ["2021-02-01", "2021-03-01"], "verdict": ["판단불가", "해당가게"], "note": ["", ""]})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    merged, meta = nm.merge_judgments(reused, new_sheet, new_key)
    m = merged.set_index("link")
    assert m.at["l2", "verdict"] == "판단불가"  # 양쪽에 있으면 새 판정 우선
    assert set(merged["link"]) == {"l1", "l2", "l3"}
    assert meta["n_conflict"] == 1 and meta["n_reused_kept"] == 1 and meta["n_new"] == 2
    assert meta["items_per_store_distribution"][3] == 1  # A는 최종 3건
    # summarize 입력 형식: item_no(점포별 1부터)와 post_date가 있다
    assert list(merged.columns) == ["review_id", "item_no", "post_date", "link", "verdict", "note"]
    assert sorted(merged["item_no"]) == [1, 2, 3] and set(merged["review_id"]) == {"N1"}
    assert m.at["l1", "post_date"] == "2021-01-01" and m.at["l3", "post_date"] == "2021-03-01"


def test_merge_judgments_skips_store_groups_without_items_and_reports_them():
    """유효 글 0건인 점포×그룹은 에러 대신 건너뛰고 meta에 남긴다 (summarize가 '매칭 없음'으로 센다)."""
    reused = pd.DataFrame({"review_id": [], "store_id": [], "link": [], "post_date": [], "verdict": [], "note": []})
    new_sheet = pd.DataFrame({"review_id": ["N2"], "item_no": [1], "link": ["l1"], "post_date": ["2021-01-01"],
                              "verdict": ["해당가게"], "note": [""]})
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "groups": ["short_name", "random;short_name"],
                            "stratum": ["영업", "폐업"]})
    merged, meta = nm.merge_judgments(reused, new_sheet, new_key)
    assert list(merged["review_id"]) == ["N2"]  # N1은 건너뜀
    assert meta["n_no_match_store_groups"] == 1 and meta["no_match_review_ids"] == ["N1"]
    # 모두 비어 있어도 에러가 아니다
    merged0, meta0 = nm.merge_judgments(reused, new_sheet.iloc[:0], new_key)
    assert len(merged0) == 0 and meta0["n_no_match_store_groups"] == 3  # N1(short_name) + N2(random, short_name)


def test_merge_judgments_fills_reused_post_date_from_old_sheet_or_raises():
    reused = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "link": ["l1"], "verdict": ["해당가게"], "note": [""]})
    new_sheet = pd.DataFrame({"review_id": ["N1"], "item_no": [1], "link": ["l2"], "post_date": ["2021-02-01"],
                              "verdict": ["다른가게"], "note": [""]})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["random"], "stratum": [""]})
    with pytest.raises(ValueError, match="post_date"):  # 재사용 파일에 날짜가 없고 old_dates도 없으면 조용히 비우지 않는다
        nm.merge_judgments(reused, new_sheet, new_key)
    old_dates = pd.DataFrame({"store_id": ["A", "A"], "link": ["l1", "zz"], "post_date": ["2020-05-05", "2020-06-06"]})
    merged, _ = nm.merge_judgments(reused, new_sheet, new_key, old_dates)
    assert merged.set_index("link").at["l1", "post_date"] == "2020-05-05"
    with pytest.raises(ValueError, match="post_date"):  # old_dates에 그 글이 없으면 역시 멈춘다
        nm.merge_judgments(reused.assign(link="other"), new_sheet, new_key, old_dates)


def test_merge_judgments_raises_on_blank_verdict():
    reused = pd.DataFrame({"review_id": [], "store_id": [], "link": [], "verdict": [], "note": []})
    new_sheet = pd.DataFrame({"review_id": ["N1"], "item_no": [1], "link": ["l1"], "verdict": [""], "note": [""]})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    with pytest.raises(ValueError, match="미입력"):
        nm.merge_judgments(reused, new_sheet, new_key)


def test_cmd_merge_end_to_end(tmp_path):
    reused = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "link": ["l1"], "post_date": ["2021-01-01"],
                           "verdict": ["해당가게"], "note": [""]})
    new_sheet = pd.DataFrame({"review_id": ["N1"], "item_no": [1], "link": ["l2"], "post_date": ["2021-02-01"],
                              "verdict": ["다른가게"], "note": [""]})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["short_name"], "stratum": ["영업"]})
    paths = {n: tmp_path / f"{n}.csv" for n in ("reused", "new_sheet", "new_key")}
    reused.to_csv(paths["reused"], index=False, encoding="utf-8-sig")
    new_sheet.to_csv(paths["new_sheet"], index=False, encoding="utf-8-sig")
    new_key.to_csv(paths["new_key"], index=False, encoding="utf-8-sig")
    out = tmp_path / "name_match_sheet_merged.csv"
    args = ["merge", "--reused", str(paths["reused"]), "--new-sheet", str(paths["new_sheet"]),
            "--new-key", str(paths["new_key"]), "--out", str(out)]
    with pytest.raises(FileNotFoundError, match="manifest"):  # 누락 검증 기준이 없으면 멈춘다
        nm.main(args)
    pd.DataFrame({"review_id": ["N1", "N1"], "link": ["l1", "l2"], "post_date": ["2021-01-01", "2021-02-01"],
                  "source": ["reused", "new"], "groups_in_window": ["short_name", "short_name"]}).to_csv(
        tmp_path / nm.MANIFEST_FILE, index=False, encoding="utf-8-sig")  # --reused와 같은 폴더(기본 위치)
    nm.main(args)
    merged = pd.read_csv(out, encoding="utf-8-sig")
    assert set(merged["link"]) == {"l1", "l2"}
    meta = json.loads(out.with_name("name_match_sheet_merged_meta.json").read_text(encoding="utf-8"))
    assert meta["n_merged_items"] == 2 and meta["no_match_basis"] == "store_group_window"


def test_merge_then_summarize_reads_merge_output_directly_with_no_match_store(tmp_path, capsys):
    """유효 글 0건 점포(R3)가 있어도 merge가 경고만 내고, 그 출력을 summarize가 그대로 읽어 '매칭 없음'으로 센다.
    재사용 파일에 post_date가 없으면(이전 round2 산출물) --old-sheet/--old-key로 채운다."""
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "groups": ["random"], "stratum": [""]})
    old_sheet = pd.DataFrame({"review_id": ["R1"], "item_no": [1], "post_date": ["2021-01-01"], "link": ["l1"],
                              "verdict": ["해당가게"], "note": [""]})
    reused = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "link": ["l1"], "verdict": ["해당가게"], "note": [""]})
    new_sheet = pd.DataFrame({"review_id": ["N2"], "item_no": [1], "post_date": ["2021-03-01"], "link": ["l2"],
                              "verdict": ["다른가게"], "note": [""]})
    key = pd.DataFrame({"review_id": ["N1", "N2", "N3"], "store_id": ["A", "B", "C"], "groups": ["random"] * 3,
                        "stratum": [""] * 3})
    targets = key[["review_id", "store_id"]].assign(name_raw="가", name_norm="가", gu="마포구", dong="서교동", biz_type="일반음식점")
    manifest = pd.DataFrame({"review_id": ["N1", "N2"], "link": ["l1", "l2"], "post_date": ["2021-01-01", "2021-03-01"],
                             "source": ["reused", "new"], "groups_in_window": ["random", "random"]})
    p = {n: tmp_path / f"{n}.csv" for n in ("old_key", "old_sheet", "reused", "new_sheet", "key", "targets")}
    for n, d in (("old_key", old_key), ("old_sheet", old_sheet), ("reused", reused), ("new_sheet", new_sheet),
                 ("key", key), ("targets", targets)):
        d.to_csv(p[n], index=False, encoding="utf-8-sig")
    manifest.to_csv(tmp_path / nm.MANIFEST_FILE, index=False, encoding="utf-8-sig")
    merged_path = tmp_path / "merged.csv"
    nm.main(["merge", "--reused", str(p["reused"]), "--new-sheet", str(p["new_sheet"]), "--new-key", str(p["key"]),
             "--old-sheet", str(p["old_sheet"]), "--old-key", str(p["old_key"]), "--out", str(merged_path)])
    assert "경고: 유효 글 0건 점포×그룹 1건" in capsys.readouterr().out  # N3(C)는 글이 없다
    merged = pd.read_csv(merged_path, encoding="utf-8-sig", dtype=str)
    assert set(merged["link"]) == {"l1", "l2"} and (merged["post_date"] != "").all() and "item_no" in merged
    nm.main(["summarize", "--targets", str(p["targets"]), "--key", str(p["key"]), "--sheet", str(merged_path),
             "--separate-no-match", "--out", str(tmp_path / "out")])
    r = pd.read_csv(tmp_path / "out" / "name_match_rates.csv").set_index("group")
    assert r.at["random", "stores_no_match"] == 1 and r.at["random", "items_decided"] == 2
    assert r.at["random", "items_other"] == 1


# ---------------------------------------------------------------------------- round2 재사용 파일 저장·merge 기본값·매칭 없음 분리
def test_round2_writes_reused_file_and_merge_defaults_to_it(tmp_path):
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"], "groups": ["random"], "stratum": [""]})
    old_sheet = pd.DataFrame({"review_id": ["R1", "R1", "R1"], "item_no": [1, 2, 3], "post_date": ["2021-01-01"] * 3,
                              "link": ["a1", "a2", "a3"], "verdict": ["해당가게", "다른가게", "해당가게"], "note": ""})
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "groups": ["random", "random"],
                            "stratum": ["", ""]})
    new_targets = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "name_raw": ["가A", "가B"],
                                "name_norm": ["가a", "가b"], "gu": ["마포구"] * 2, "dong": ["서교동"] * 2,
                                "biz_type": ["일반음식점"] * 2})
    paths = {n: tmp_path / f"{n}.csv" for n in ("old_sheet", "old_key", "new_targets", "new_key")}
    for n, d in (("old_sheet", old_sheet), ("old_key", old_key), ("new_targets", new_targets), ("new_key", new_key)):
        d.to_csv(paths[n], index=False, encoding="utf-8-sig")
    raw_path = tmp_path / "blog_items.jsonl.gz"
    with gzip.open(raw_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"store_id": "B", "link": "b1", "title": "글", "description": "", "postdate": "20210201",
                            "matched": True}) + "\n")
    out = tmp_path / "out"
    nm.main(["round2", "--old-sheet", str(paths["old_sheet"]), "--old-key", str(paths["old_key"]),
             "--new-targets", str(paths["new_targets"]), "--new-key", str(paths["new_key"]), "--raw", str(raw_path),
             "--out", str(out)] + _filter_args(tmp_path, ["A", "B"]))
    reused = pd.read_csv(out / nm.REUSED_FILE, encoding="utf-8-sig")
    assert list(reused.columns) == ["review_id", "store_id", "link", "post_date", "verdict", "note"]
    assert len(reused) == 3 and set(reused["review_id"]) == {"N1"}  # 새 회차 review_id로 다시 매겨 저장
    assert (reused["post_date"] == "2021-01-01").all()  # merge가 그룹별 구간 재집계에 쓸 날짜도 함께 저장
    assert (out / nm.MANIFEST_FILE).exists()
    # merge: --reused 생략 → 새 판정표와 같은 폴더의 재사용 파일(과 manifest)을 쓴다
    new_sheet = pd.read_csv(out / "name_match_sheet_round2.csv", dtype=str, keep_default_na=False,
                            encoding="utf-8-sig").assign(verdict="해당가게")
    assert new_sheet["link"].tolist() == ["b1"]
    new_sheet.to_csv(out / "judged.csv", index=False, encoding="utf-8-sig")
    merged = nm.cmd_merge(type("A", (), {"reused": None, "new_sheet": out / "judged.csv", "new_key": paths["new_key"],
                                         "out": tmp_path / "merged.csv"})())
    assert set(merged["link"]) == {"a1", "a2", "a3", "b1"}
    with pytest.raises(FileNotFoundError, match="재사용 판정 파일"):  # 기본 위치에 파일이 없으면 명확히 멈춘다
        (tmp_path / "x").mkdir()
        new_sheet.to_csv(tmp_path / "x" / "judged.csv", index=False, encoding="utf-8-sig")
        nm.cmd_merge(type("A", (), {"reused": None, "new_sheet": tmp_path / "x" / "judged.csv",
                                    "new_key": paths["new_key"], "out": tmp_path / "m2.csv"})())


def test_rates_separate_no_match_reports_count_without_changing_denominator():
    targets = pd.DataFrame({"review_id": ["R1", "R2", "R3"], "store_id": ["A", "B", "C"]})
    key = pd.DataFrame({"review_id": ["R1", "R2", "R3"], "store_id": ["A", "B", "C"],
                        "groups": ["random"] * 3, "stratum": [""] * 3})
    sheet = pd.DataFrame({"review_id": ["R1", "R2"], "item_no": [1, 1], "post_date": ["2021-01-01"] * 2,
                          "verdict": ["다른가게", "판단불가"]})  # R3: 글 0건(매칭 없음), R2: 판단불가만
    items, stores, _ = nm.judge(sheet, targets, key)
    base = nm.rates(items, stores).set_index("group")
    sep = nm.rates(items, stores, separate_no_match=True).set_index("group")
    assert "stores_no_match" not in base.columns  # 기본 출력은 그대로
    assert base.at["random", "stores_undecided"] == 2  # R2(판단불가)와 R3(글 없음)이 섞여 있다
    assert sep.at["random", "stores_no_match"] == 1 and sep.at["random", "stores_undecided"] == 1
    for c in ("stores_decided", "stores_fp", "store_fp_rate", "items_decided"):
        assert base.at["random", c] == sep.at["random", c]  # 분모·오탐률 불변


def test_summarize_cli_separate_no_match_flag(tmp_path):
    targets = pd.DataFrame({"review_id": ["R1", "R2"], "store_id": ["A", "B"]})
    key = targets.assign(groups="random", stratum="")
    sheet = pd.DataFrame({"review_id": ["R1"], "item_no": [1], "post_date": ["2021-01-01"], "verdict": ["해당가게"]})
    tp, sp, kp = tmp_path / "t.csv", tmp_path / "s.csv", tmp_path / "k.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    key.to_csv(kp, index=False, encoding="utf-8-sig")
    nm.main(["summarize", "--targets", str(tp), "--key", str(kp), "--sheet", str(sp), "--separate-no-match",
             "--out", str(tmp_path / "out")])
    r = pd.read_csv(tmp_path / "out" / "name_match_rates.csv").set_index("group")
    assert r.at["random", "stores_no_match"] == 1
    assert "매칭 없음" in (tmp_path / "out" / "name_match_summary.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------- 재검증: 판정 누락·키 불일치·점포×그룹 매칭 없음·필터 필수
def _merge_fixture():
    reused = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "link": ["a1"], "post_date": ["2021-01-01"],
                           "verdict": ["해당가게"], "note": [""]})
    new_sheet = pd.DataFrame({"review_id": ["N2", "N2"], "item_no": [1, 2], "post_date": ["2021-02-01", "2021-03-01"],
                              "link": ["b1", "b2"], "verdict": ["해당가게", "다른가게"], "note": ["", ""]})
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "groups": ["random", "short_name"],
                            "stratum": ["", "폐업"]})
    manifest = pd.DataFrame({"review_id": ["N1", "N2", "N2"], "link": ["a1", "b1", "b2"],
                             "post_date": ["2021-01-01", "2021-02-01", "2021-03-01"], "source": ["reused", "new", "new"],
                             "groups_in_window": ["random", "short_name", "short_name"]})
    return reused, new_sheet, new_key, manifest


def test_merge_fails_when_judged_sheet_drops_rows():
    """판정자가 행을 지우면 '매칭 없음'으로 넘어가지 않고 멈춘다 (판정 누락 ≠ 유효 글 없음)."""
    reused, new_sheet, new_key, manifest = _merge_fixture()
    nm.merge_judgments(reused, new_sheet, new_key, manifest=manifest)  # 온전한 판정본은 통과
    with pytest.raises(ValueError, match="빠진 글 1건"):
        nm.merge_judgments(reused, new_sheet.iloc[:1], new_key, manifest=manifest)
    with pytest.raises(ValueError, match="빠진 글 2건"):  # 점포 하나를 통째로 지워도
        nm.merge_judgments(reused, new_sheet.iloc[:0], new_key, manifest=manifest)
    with pytest.raises(ValueError, match="없던 글"):  # link를 고치거나 행을 더해도
        nm.merge_judgments(reused, new_sheet.assign(link=["b1", "bX"]).iloc[[1]].pipe(
            lambda d: pd.concat([new_sheet, d])), new_key, manifest=manifest)
    with pytest.raises(ValueError, match="재사용 판정 파일이 round2 manifest와 다르다"):
        nm.merge_judgments(reused.assign(link="zz"), new_sheet, new_key, manifest=manifest)


def test_merge_fails_on_review_id_not_in_key():
    reused, new_sheet, new_key, manifest = _merge_fixture()
    with pytest.raises(ValueError, match="key에 없는 review_id"):
        nm.merge_judgments(reused, new_sheet.assign(review_id=["N2", "R999"]), new_key, manifest=manifest)
    with pytest.raises(ValueError, match="key에 없는 review_id"):  # manifest 없이도 멈춘다
        nm.merge_judgments(reused, new_sheet.assign(review_id=["N2", "R999"]), new_key)


def test_merge_counts_no_match_per_store_group():
    """겹침 점포는 한 그룹만 '매칭 없음'일 수 있다 — 점포 단위가 아니라 점포×그룹 단위로 센다."""
    reused = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "link": ["a1"], "post_date": ["2024-01-01"],
                           "verdict": ["해당가게"], "note": [""]})
    new_sheet = pd.DataFrame(columns=["review_id", "item_no", "post_date", "link", "verdict", "note"])
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"],
                            "groups": ["random;short_name", "short_name"], "stratum": ["영업", "폐업"]})
    # a1은 random 구간에만 든다 (short_name 구간 밖) → N1의 short_name과 N2의 short_name이 매칭 없음
    manifest = pd.DataFrame({"review_id": ["N1"], "link": ["a1"], "post_date": ["2024-01-01"], "source": ["reused"],
                             "groups_in_window": ["random"]})
    merged, meta = nm.merge_judgments(reused, new_sheet, new_key, manifest=manifest)
    assert len(merged) == 1
    assert meta["n_no_match_store_groups"] == 2 and meta["no_match_basis"] == "store_group_window"
    assert meta["no_match_store_groups"] == [{"review_id": "N1", "group": "short_name"},
                                             {"review_id": "N2", "group": "short_name"}]
    assert meta["no_match_by_group"] == {"short_name:영업": 1, "short_name:폐업": 1}
    # manifest가 없으면 N1은 글이 있어 점포 단위로는 빠진다 — 이전 계산이 1건 적게 나온 이유
    _, meta0 = nm.merge_judgments(reused, new_sheet, new_key)
    assert meta0["n_no_match_store_groups"] == 1


def test_groups_in_window_uses_each_group_bound():
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["random;short_name"], "stratum": ["영업"]})
    bounds = {"A": {"random": (None, pd.Timestamp("2026-06-30")),
                    "short_name": (pd.Timestamp("2020-10-01"), pd.Timestamp("2025-06-30"))}}
    g = nm.groups_in_window(pd.Series(["N1", "N1", "N1"]), pd.Series(["2019-01-01", "2023-01-01", "2026-01-01"]),
                            new_key, bounds)
    assert g.tolist() == ["random", "random;short_name", "random"]


def _round2_files(tmp_path, old_sheet, old_key, new_targets, new_key):
    paths = {n: tmp_path / f"{n}.csv" for n in ("old_sheet", "old_key", "new_targets", "new_key")}
    for n, d in (("old_sheet", old_sheet), ("old_key", old_key), ("new_targets", new_targets), ("new_key", new_key)):
        d.to_csv(paths[n], index=False, encoding="utf-8-sig")
    return paths, ["round2", "--old-sheet", str(paths["old_sheet"]), "--old-key", str(paths["old_key"]),
                   "--new-targets", str(paths["new_targets"]), "--new-key", str(paths["new_key"])]


def _write_raw(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for sid, link, d in rows:
            f.write(json.dumps({"store_id": sid, "link": link, "title": "t", "description": "", "postdate": d,
                                "matched": True}) + "\n")
    return path


def test_round2_requires_date_filter_inputs(tmp_path):
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"]})
    old_sheet = pd.DataFrame({"review_id": ["R1"], "item_no": [1], "post_date": ["2021-01-01"], "link": ["a1"],
                              "verdict": ["해당가게"], "note": [""]})
    new_key = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "groups": ["random"], "stratum": [""]})
    new_targets = pd.DataFrame({"review_id": ["N1"], "store_id": ["A"], "name_raw": ["가A"], "name_norm": ["가a"],
                                "gu": ["마포구"], "dong": ["서교동"], "biz_type": ["일반음식점"]})
    paths, base = _round2_files(tmp_path, old_sheet, old_key, new_targets, new_key)
    base += ["--out", str(tmp_path / "o")]
    f = _filter_args(tmp_path, ["A"])
    for drop in ("--licenses", "--master"):
        i = f.index(drop)
        with pytest.raises(SystemExit):  # argparse 필수 인자
            nm.main(base + f[:i] + f[i + 2:])
    args = type("A", (), {"old_sheet": paths["old_sheet"], "old_key": paths["old_key"],
                          "new_targets": paths["new_targets"], "new_key": paths["new_key"], "licenses": None,
                          "master": None, "serve_as_of": None, "raw": None, "seed": 1, "out": tmp_path / "o"})()
    with pytest.raises(ValueError, match="날짜 필터 입력"):  # 함수를 직접 불러도 멈춘다
        nm.cmd_round2(args)
    with pytest.raises(ValueError, match="serve_as_of"):  # 상한을 인자·key 메타 어디서도 못 찾으면 멈춘다
        nm.main(base + f[:4])


def test_round2_report_records_date_filter_and_is_deterministic(tmp_path):
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"]})
    old_sheet = pd.DataFrame({"review_id": ["R1", "R1"], "item_no": [1, 2], "post_date": ["2021-01-01", "2026-09-01"],
                              "link": ["a1", "a2"], "verdict": ["해당가게", "다른가게"], "note": ["", ""]})
    new_key = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "groups": ["random", "short_name"],
                            "stratum": ["", "폐업"]})
    new_targets = pd.DataFrame({"review_id": ["N1", "N2"], "store_id": ["A", "B"], "name_raw": ["가A", "가B"],
                                "name_norm": ["가a", "가b"], "gu": ["마포구"] * 2, "dong": ["서교동"] * 2,
                                "biz_type": ["일반음식점"] * 2})
    _, base = _round2_files(tmp_path, old_sheet, old_key, new_targets, new_key)
    raw = _write_raw(tmp_path / "blog_items.jsonl.gz", [("B", "b1", "20210101"), ("B", "b2", "20190101"),
                                                        ("B", "b3", "20220101"), ("B", "b4", "20230101")])
    files = ("name_match_sheet_round2.csv", nm.REUSED_FILE, nm.MANIFEST_FILE, "round2_report.json")
    runs = []
    for k in (1, 2):
        out = tmp_path / f"out{k}"
        nm.main(base + ["--raw", str(raw), "--out", str(out)] + _filter_args(tmp_path, ["A", "B"]))
        runs.append({f: (out / f).read_bytes() for f in files})
    assert runs[0] == runs[1]  # 같은 입력 → 같은 출력
    report = json.loads(runs[0]["round2_report.json"])
    df = report["date_filter"]
    assert df["applied"] and df["serve_as_of"] == "2026-06-30"
    assert df["old_verdicts"]["items_excluded"] == 1 and df["old_verdicts"]["items_excluded_by_group"] == {"random": 1}
    assert df["raw_items"]["items_excluded"] == 1  # b2(2019)는 short_name 구간(2019-10-01~) 밖
    assert df["raw_items"]["items_excluded_by_group"] == {"short_name:폐업": 1}
    assert report["n_new_sheet_rows"] == 3 and report["n_no_match_store_groups"] == 0
    assert "store_id" not in json.dumps(report)
    sheet = pd.read_csv(tmp_path / "out1" / "name_match_sheet_round2.csv", encoding="utf-8-sig")
    nm.assert_blind(sheet.columns)  # 판정자에게 가는 파일은 blind
    assert "store_id" not in sheet.columns and not {"groups", "stratum", "groups_in_window"} & set(sheet.columns)


def test_round2_then_merge_then_summarize_end_to_end(tmp_path):
    """round2 → (판정) → merge → summarize가 수작업 없이 이어진다. 판정본 행을 지우면 merge가 멈춘다."""
    old_key = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"]})
    old_sheet = pd.DataFrame({"review_id": ["R1"], "item_no": [1], "post_date": ["2021-01-01"], "link": ["a1"],
                              "verdict": ["다른가게"], "note": [""]})
    key = pd.DataFrame({"review_id": ["N1", "N2", "N3"], "store_id": ["A", "B", "C"],
                        "groups": ["random", "short_name", "short_name"], "stratum": ["", "영업", "폐업"]})
    targets = key[["review_id", "store_id"]].assign(name_raw="가", name_norm="가", gu="마포구", dong="서교동",
                                                    biz_type="일반음식점")
    paths, base = _round2_files(tmp_path, old_sheet, old_key, targets, key)
    raw = _write_raw(tmp_path / "blog_items.jsonl.gz", [("B", "b1", "20210101"), ("B", "b2", "20220101"),
                                                        ("C", "c1", "20100101")])
    out = tmp_path / "round2"
    nm.main(base + ["--raw", str(raw), "--out", str(out)] + _filter_args(tmp_path, ["A", "B", "C"]))
    report = json.loads((out / "round2_report.json").read_text(encoding="utf-8"))
    assert report["no_match_by_group"] == {"short_name:폐업": 1}  # C의 글(2010년)은 구간 밖
    judged_dir = tmp_path / "returned"  # 판정자가 돌려준 파일은 다른 폴더에 둔다
    judged_dir.mkdir()
    judged = pd.read_csv(out / "name_match_sheet_round2.csv", dtype=str, keep_default_na=False,
                         encoding="utf-8-sig").assign(verdict=["해당가게", "다른가게"])
    judged.to_csv(judged_dir / "name_match_sheet_round2.csv", index=False, encoding="utf-8-sig")
    merged_path = out / "name_match_merged.csv"
    merge_args = ["merge", "--reused", str(out / nm.REUSED_FILE), "--new-sheet",
                  str(judged_dir / "name_match_sheet_round2.csv"), "--new-key", str(paths["new_key"]),
                  "--out", str(merged_path)]
    nm.main(merge_args)
    meta = json.loads((out / "name_match_merged_meta.json").read_text(encoding="utf-8"))
    assert meta["n_no_match_store_groups"] == 1 and meta["no_match_by_group"] == {"short_name:폐업": 1}
    nm.main(["summarize", "--targets", str(paths["new_targets"]), "--key", str(paths["new_key"]),
             "--sheet", str(merged_path), "--separate-no-match", "--out", str(tmp_path / "sum")])
    r = pd.read_csv(tmp_path / "sum" / "name_match_rates.csv").set_index("group")
    assert r.at["random", "items_other"] == 1 and r.at["short_name", "stores_no_match"] == 1
    judged.iloc[:1].to_csv(judged_dir / "name_match_sheet_round2.csv", index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="빠진 글"):
        nm.main(merge_args)


def test_summarize_checks_key_meta_pair(tmp_path, capsys):
    targets = pd.DataFrame({"review_id": ["R1"], "store_id": ["A"]})
    key = targets.assign(groups="random", stratum="")
    sheet = pd.DataFrame({"review_id": ["R1"], "item_no": [1], "post_date": ["2021-01-01"], "verdict": ["해당가게"]})
    tp, sp, kp = tmp_path / "t.csv", tmp_path / "s.csv", tmp_path / "k.csv"
    targets.to_csv(tp, index=False, encoding="utf-8-sig")
    sheet.to_csv(sp, index=False, encoding="utf-8-sig")
    key.to_csv(kp, index=False, encoding="utf-8-sig")
    args = ["summarize", "--targets", str(tp), "--key", str(kp), "--sheet", str(sp), "--out", str(tmp_path / "out")]
    mp = nm.key_meta_path(kp)
    mp.write_text(json.dumps({"short_pool_n": None}), encoding="utf-8")  # 이전 버전 메타 — 경고만
    nm.main(args)
    assert "짝을 확인하지 못했다" in capsys.readouterr().out
    mp.write_text(json.dumps({"targets_sha256": nm.sha256_file(tp), "key_sha256": nm.sha256_file(kp)}),
                  encoding="utf-8")
    nm.main(args)  # 짝이 맞으면 통과
    mp.write_text(json.dumps({"targets_sha256": nm.sha256_file(tp), "key_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="짝이 아니다"):
        nm.main(args)
