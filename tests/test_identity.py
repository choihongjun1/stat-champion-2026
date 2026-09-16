# -*- coding: utf-8 -*-
"""소진공 ID 안정성/재발급 탐지 테스트 (raw 데이터 불필요 — 합성 panel)."""
import pandas as pd

from src.data.identity import (
    build_entities,
    detect_id_reissue,
    snapshot_presence,
    transition_qa,
)

SNAPS = ["202412", "202503", "202506"]


def make_panel(rows):
    """rows: (snapshot, sj_store_id, pnu, name_norm) — 나머지 컬럼은 기본값."""
    df = pd.DataFrame(rows, columns=["snapshot", "sj_store_id", "pnu", "name_norm"])
    for c in ["name_raw", "branch_raw", "branch_norm", "name_branch_norm",
              "gu", "bjd_code", "cat3_code", "cat3_name", "addr_raw"]:
        df[c] = df["name_norm"] if c in ("name_raw", "name_branch_norm") else "x"
    df["coord_missing"] = False
    df["coord_suspect"] = False
    df["x_5179"] = 950000.0
    df["y_5179"] = 1950000.0
    return df


def test_presence_and_snapshot_order():
    # 입력 순서가 뒤섞여도 snapshot 열은 시간순으로 정렬된다
    panel = make_panel([
        ("202506", "A", "P1", "가게"),
        ("202412", "A", "P1", "가게"),
        ("202503", "B", "P1", "가게"),
    ])
    pres = snapshot_presence(panel)
    assert list(pres.columns) == SNAPS
    assert pres.loc["A"].tolist() == [True, False, True]
    assert pres.loc["B"].tolist() == [False, True, False]


def test_transition_qa_reappearance():
    panel = make_panel([
        ("202412", "A", "P1", "가게"),
        ("202503", "B", "P2", "다른가게"),
        ("202506", "A", "P1", "가게"),
    ])
    qa = transition_qa(snapshot_presence(panel))
    # 202503→202506: A가 공백 후 재등장
    row = qa[qa["to"] == "202506"].iloc[0]
    assert row["reappeared_after_gap"] == 1
    assert row["brand_new"] == 0
    row1 = qa[qa["to"] == "202503"].iloc[0]
    assert row1["lost"] == 1  # A가 202503에 없음
    assert row1["brand_new"] == 1  # B 최초 등장


def test_id_reissue_detected():
    # OLD가 202503 이후 완전 소멸, NEW가 202506에 동일 PNU+상호로 최초 등장
    panel = make_panel([
        ("202412", "OLD", "P1", "국밥집"),
        ("202503", "OLD", "P1", "국밥집"),
        ("202506", "NEW", "P1", "국밥집"),
    ])
    links = detect_id_reissue(panel)
    assert len(links) == 1
    r = links.iloc[0]
    assert (r["old_id"], r["new_id"]) == ("OLD", "NEW")
    assert (r["from_snapshot"], r["to_snapshot"]) == ("202503", "202506")
    assert not r["ambiguous"]

    ents = build_entities(panel, links)
    e = ents.set_index("sj_store_id")
    # unambiguous 링크는 최초 ID로 병합
    assert e.loc["NEW", "sj_entity_id"] == "OLD"
    assert e.loc["OLD", "sj_entity_id"] == "OLD"
    assert bool(e.loc["NEW", "id_linked"])


def test_id_reissue_ambiguous_not_merged():
    # 동일 필지에 동명 점포 2개 소멸 + 1개 신규 → ambiguous, 병합 금지
    panel = make_panel([
        ("202412", "OLD1", "P1", "미용실"),
        ("202412", "OLD2", "P1", "미용실"),
        ("202503", "NEW", "P1", "미용실"),
    ])
    links = detect_id_reissue(panel)
    assert len(links) == 2
    assert links["ambiguous"].all()
    ents = build_entities(panel, links)
    e = ents.set_index("sj_store_id")
    assert e.loc["NEW", "sj_entity_id"] == "NEW"  # 병합 안 됨
    assert bool(e.loc["NEW", "id_link_ambiguous"])


def test_no_reissue_when_id_survives():
    # ID가 유지되면 소멸이 아니므로 후보 없음
    panel = make_panel([
        ("202412", "A", "P1", "가게"),
        ("202503", "A", "P1", "가게"),
    ])
    links = detect_id_reissue(panel)
    assert len(links) == 0


def test_entity_gap_flag():
    panel = make_panel([
        ("202412", "A", "P1", "가게"),
        ("202506", "A", "P1", "가게"),
        ("202412", "B", "P2", "가게2"),
        ("202503", "B", "P2", "가게2"),
    ])
    ents = build_entities(panel, detect_id_reissue(panel)).set_index("sj_store_id")
    assert bool(ents.loc["A", "has_gap"])
    assert not bool(ents.loc["B", "has_gap"])
    assert ents.loc["A", "first_snapshot"] == "202412"
    assert ents.loc["A", "last_snapshot"] == "202506"


def test_entity_coord_skips_suspect_snapshot():
    # 마지막 스냅샷 좌표가 suspect면 그 이전의 정상 좌표를 사용
    panel = make_panel([
        ("202412", "A", "P1", "가게"),
        ("202503", "A", "P1", "가게"),
    ])
    panel.loc[panel["snapshot"] == "202503", "coord_suspect"] = True
    panel.loc[panel["snapshot"] == "202503", "x_5179"] = 111.0
    ents = build_entities(panel, detect_id_reissue(panel)).set_index("sj_store_id")
    assert ents.loc["A", "x_5179"] == 950000.0
    assert ents.loc["A", "coord_snapshot"] == "202412"
