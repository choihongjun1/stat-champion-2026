# -*- coding: utf-8 -*-
"""소진공 ID 안정성 분석과 entity 테이블 구축.

상가업소번호는 영구 사업체 ID가 아니다 (audit: 202603→202606 소멸의 30.5%가
동일 필지+정규화 상호의 새 업소번호로 재등장 = ID 재발급/DB 정비).

처리 원칙:
- 인접 스냅샷 쌍마다 소멸 ID ↔ 완전 신규 ID를 (PNU, 정규화 상호)로 대조해
  ID 재발급 후보 링크를 탐지한다.
- (PNU, 상호) 그룹 내에서 소멸 1개 ↔ 신규 1개인 경우만 unambiguous 링크로 인정하고,
  다대다는 자동 병합하지 않고 ambiguous로 보존한다 (동일 필지 동명 점포 가능성).
- unambiguous 링크만 union-find로 병합해 sj_entity_id(대표 = 가장 이른 ID)를 부여한다.
- 소진공 '소멸'은 폐업 label로 사용하지 않는다 (DECISIONS 2026-09-13). 여기서의
  소멸/신규 집계는 ER 품질 검증용 QA일 뿐이다.
"""
from __future__ import annotations

import pandas as pd


def snapshot_presence(panel: pd.DataFrame) -> pd.DataFrame:
    """sj_store_id × snapshot 존재 여부 (wide bool). 컬럼은 시간순."""
    snaps = sorted(panel["snapshot"].unique())
    pres = (
        panel.assign(v=True)
        .pivot_table(index="sj_store_id", columns="snapshot", values="v",
                     aggfunc="any", fill_value=False)
        .reindex(columns=snaps, fill_value=False)
    )
    return pres.astype(bool)


def transition_qa(presence: pd.DataFrame) -> pd.DataFrame:
    """인접 스냅샷 쌍별 유지/소멸/신규/재등장(중간 공백 후) 집계."""
    snaps = list(presence.columns)
    rows = []
    seen_before = pd.Series(False, index=presence.index)
    for i, s in enumerate(snaps):
        if i > 0:
            prev, cur = presence[snaps[i - 1]], presence[s]
            retained = int((prev & cur).sum())
            lost = int((prev & ~cur).sum())
            # 완전 소멸: 직전 스냅샷에 있고 현재 이후 어디에도 없음
            # (재발급 후보 탐지의 old 모집단과 동일한 정의 — QA 분모로 사용)
            lost_forever = int((prev & ~presence[snaps[i:]].any(axis=1)).sum())
            new = cur & ~prev
            reappeared = int((new & seen_before).sum())
            brand_new = int(new.sum()) - reappeared
            rows.append({
                "from": snaps[i - 1], "to": s,
                "retained": retained,
                "retention_rate": retained / int(prev.sum()) if prev.sum() else float("nan"),
                "lost": lost,
                "lost_forever": lost_forever,
                "new_total": int(new.sum()),
                "brand_new": brand_new,
                "reappeared_after_gap": reappeared,
            })
        seen_before = seen_before | presence[s]
    return pd.DataFrame(rows)


def detect_id_reissue(panel: pd.DataFrame) -> pd.DataFrame:
    """인접 스냅샷 쌍별 ID 재발급 후보 링크 탐지.

    old: t에 존재하나 t+1 이후 어디에도 없는 ID (완전 소멸)
    new: t+1에 처음 등장한 ID (이전 어느 스냅샷에도 없음)
    링크 키: (PNU, name_norm) — old는 t 시점, new는 t+1 시점 값.
    그룹 내 old 1 × new 1만 unambiguous, 그 외는 ambiguous로 보존.
    """
    snaps = sorted(panel["snapshot"].unique())
    pres = snapshot_presence(panel)
    keyed = panel.set_index(["snapshot", "sj_store_id"])[["pnu", "name_norm"]]

    links = []
    for i in range(len(snaps) - 1):
        t0, t1 = snaps[i], snaps[i + 1]
        later = snaps[i + 1:]
        earlier = snaps[: i + 1]
        in_t0 = pres[t0]
        gone = in_t0 & ~pres[later].any(axis=1)            # t0 이후 완전 소멸
        first_appear = pres[t1] & ~pres[earlier].any(axis=1)  # t1에 최초 등장

        old_ids = pres.index[gone]
        new_ids = pres.index[first_appear]
        if len(old_ids) == 0 or len(new_ids) == 0:
            continue

        old = keyed.loc[[(t0, i_) for i_ in old_ids]].reset_index()
        new = keyed.loc[[(t1, i_) for i_ in new_ids]].reset_index()
        old = old.dropna(subset=["pnu", "name_norm"])
        new = new.dropna(subset=["pnu", "name_norm"])

        cand = old.merge(
            new, on=["pnu", "name_norm"], suffixes=("_old", "_new")
        )
        if cand.empty:
            continue
        grp = cand.groupby(["pnu", "name_norm"])
        n_old = grp["sj_store_id_old"].transform("nunique")
        n_new = grp["sj_store_id_new"].transform("nunique")
        cand = cand.assign(
            from_snapshot=t0, to_snapshot=t1,
            n_old_candidates=n_old, n_new_candidates=n_new,
            ambiguous=(n_old > 1) | (n_new > 1),
        )
        links.append(cand.rename(columns={
            "sj_store_id_old": "old_id", "sj_store_id_new": "new_id",
            "snapshot_old": "old_snapshot", "snapshot_new": "new_snapshot",
        })[[
            "from_snapshot", "to_snapshot", "old_id", "new_id",
            "pnu", "name_norm", "n_old_candidates", "n_new_candidates", "ambiguous",
        ]])

    if not links:
        return pd.DataFrame(columns=[
            "from_snapshot", "to_snapshot", "old_id", "new_id",
            "pnu", "name_norm", "n_old_candidates", "n_new_candidates", "ambiguous",
        ])
    return pd.concat(links, ignore_index=True)


def _union_find_roots(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """unambiguous old-new 쌍들을 연결해 각 ID의 대표(가장 이른 ID)를 찾는다."""
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for old, new in pairs:
        ra, rb = find(old), find(new)
        if ra != rb:
            # 대표는 사전순이 아니라 '먼저 등장한 쪽'이어야 하므로 old의 root를 우선
            parent[rb] = ra
    return {k: find(k) for k in parent}


def build_entities(panel: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    """entity-level resolution 테이블.

    sj_entity_id: unambiguous 재발급 링크만 병합한 canonical ID (대표 = 최초 ID).
    ambiguous 링크는 병합하지 않고 id_link_ambiguous 플래그로만 표시한다.
    좌표는 '서울 bbox 안이면서 결측이 아닌' 가장 최근 스냅샷 값 (202503 이상 좌표 배제).
    """
    snaps = sorted(panel["snapshot"].unique())
    pres = snapshot_presence(panel)

    good = _union_find_roots(
        [tuple(r) for r in links.loc[~links["ambiguous"], ["old_id", "new_id"]]
         .itertuples(index=False)]
    )
    amb_ids = set(links.loc[links["ambiguous"], "old_id"]) | set(
        links.loc[links["ambiguous"], "new_id"]
    )

    # 관측 기반 대표 속성: 마지막 관측 스냅샷의 값 + 신뢰 가능한 마지막 좌표
    panel_sorted = panel.sort_values("snapshot")
    last = panel_sorted.groupby("sj_store_id").tail(1).set_index("sj_store_id")
    coord_ok = panel_sorted[
        ~panel_sorted["coord_missing"] & ~panel_sorted["coord_suspect"]
    ]
    last_coord = coord_ok.groupby("sj_store_id").tail(1).set_index("sj_store_id")

    ent = pd.DataFrame(index=pres.index)
    ent["sj_entity_id"] = [good.get(i, i) for i in ent.index]
    # id_linked: unambiguous 재발급 링크(union-find)에 참여한 ID (대표 포함)
    linked_ids = set(good.keys())
    ent["id_linked"] = [i in linked_ids for i in ent.index]
    ent["id_link_ambiguous"] = [i in amb_ids for i in ent.index]
    ent["first_snapshot"] = [snaps[row.argmax()] for row in pres.to_numpy()]
    ent["last_snapshot"] = [
        snaps[len(snaps) - 1 - row[::-1].argmax()] for row in pres.to_numpy()
    ]
    ent["n_snapshots"] = pres.sum(axis=1).to_numpy()
    # 중간 공백(관측 구간 내 미등장) 여부
    first_i = pres.to_numpy().argmax(axis=1)
    last_i = len(snaps) - 1 - pres.to_numpy()[:, ::-1].argmax(axis=1)
    ent["has_gap"] = (last_i - first_i + 1) != ent["n_snapshots"].to_numpy()

    for col in ["pnu", "name_raw", "name_norm", "branch_raw", "branch_norm",
                "name_branch_norm", "gu", "bjd_code", "cat3_code", "cat3_name",
                "addr_raw"]:
        ent[col + "_last"] = last[col]
    ent["pnu_nunique"] = panel.groupby("sj_store_id")["pnu"].nunique()
    ent["name_norm_nunique"] = panel.groupby("sj_store_id")["name_norm"].nunique()
    ent["x_5179"] = last_coord["x_5179"]
    ent["y_5179"] = last_coord["y_5179"]
    ent["coord_snapshot"] = last_coord["snapshot"]

    ent = ent.reset_index().rename(columns={"index": "sj_store_id"})
    return add_entity_provenance(ent)


def add_entity_provenance(ent: pd.DataFrame) -> pd.DataFrame:
    """entity 단위 재발급 provenance를 각 업소번호 행에 붙인다.

    `first_snapshot`/`last_snapshot`은 **원 업소번호(ID) 기준**이라 재발급된 entity에서는
    구 ID가 중간에 '소멸'한 것처럼 보인다. 폐업 보조정보(sj_status)는 반드시 아래
    `entity_*` 값(sj_entity_id 기준)을 써야 재발급을 소멸로 오인하지 않는다.
    재발급 전후 ID를 다른 entity로 쪼개지 않으며, 재발급 여부는 `id_reissued`로만 남긴다.

    이 값들은 7개 스냅샷 union에서 나온다. 라벨 보조정보·ER 검증용이며
    과거 origin의 prediction feature로 쓰면 미래 정보 누수다.
    """
    grp = ent.groupby("sj_entity_id")
    ent["entity_n_ids"] = grp["sj_store_id"].transform("size").astype(int)
    ent["id_reissued"] = ent["entity_n_ids"] > 1
    ent["entity_first_snapshot"] = grp["first_snapshot"].transform("min")
    ent["entity_last_snapshot"] = grp["last_snapshot"].transform("max")
    latest = (
        ent.sort_values(["sj_entity_id", "last_snapshot", "first_snapshot", "sj_store_id"])
        .groupby("sj_entity_id")["sj_store_id"].last()
    )
    ent["entity_latest_sj_store_id"] = ent["sj_entity_id"].map(latest)
    return ent
