# -*- coding: utf-8 -*-
"""#28 상호 매칭 검수 보조 — blind 판정표 생성·집계.

두 질문을 판정표 하나로 한 번에 검수한다.
1. 온라인 요인이 "언급이 많은 쪽에서 위험이 높게" 나와 화면 표시를 보류한 점포(W2-3, display=false이고
   data_missing=false)가 상호 오탐인지 — priority/random 그룹.
2. 짧은 상호(PR #21 정규화 후 2자 이하)의 블로그 매칭 오탐률이 영업/폐업 층에서 다른지 — short_name 그룹
   (DATA_CATALOG §6 "짧은 상호 100건 영업/폐업 층화 blind 검수"). 폐업 쪽 오탐이 더 높으면 온라인 feature에
   라벨과 상관된 노이즈가 있다는 뜻이다.

blind 원칙: 대상 목록·판정표 어디에도 위험도·등급·폐업 여부·폐업일을 넣지 않는다.
판정표에는 store_id, 선정 그룹, 층(영업/폐업)도 넣지 않는다 (review_id로만 연결).

하위 명령
- targets   (분석 쪽) 검토 대기 점포에서 priority n곳(예측 확률 상위) + random n곳, 짧은 상호 점포에서 영업·폐업
            층별 n곳을 뽑는다. 파일 2개: 대상 목록(전달용, 선정 그룹·층 없음)과 선정 그룹 키(분석 쪽 보관).
            그룹이 겹치는 점포는 대상 목록에 한 번만 싣고 키에 속한 그룹을 모두 적는다.
            확률은 선정에만 쓰고 파일에 넣지 않는다. 행 순서는 섞는다.
- sheet     (수집 쪽, 원본 blog_items.jsonl.gz가 있는 컴퓨터) 대상 점포별로 매칭된 글을 최대 N건 뽑아 판정표를 만든다.
- summarize (분석 쪽) 채워진 판정표로 글·점포 단위 오탐률을 집계한다.

원본 blog_items.jsonl.gz 한 줄 (PR #21 `collect_blog_monthly.py`가 쓰는 필드):
    store_id, link, title, description, postdate("YYYYMMDD"), matched(bool)
블로그명은 원본에 없어 링크에서 블로그 ID를 뽑아 `blog_name`으로 쓴다.

실행:
    python -m src.analysis.name_match_review targets --diagnosis outputs/serve/2026Q2/diagnosis.parquet \\
        --licenses outputs/standardized/licenses_3gu.parquet --mentions outputs/online/online_mentions_monthly.parquet \\
        --out outputs/review/name_match_targets.csv
    python -m src.analysis.name_match_review sheet --targets name_match_targets.csv \\
        --items data/00_raw/online/blog_items.jsonl.gz --out name_match_sheet.csv
    python -m src.analysis.name_match_review summarize --targets outputs/review/name_match_targets.csv \\
        --key outputs/review/name_match_key.csv --sheet name_match_sheet.csv --out outputs/review/
회차가 바뀌면 round2(재사용 + 부족분 추출) → 사람 판정 → merge → summarize (docs/REVIEW_NAME_MATCH.md).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import math
import re
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

VERDICTS = ("해당가게", "다른가게", "판단불가")
SAME, OTHER, UNKNOWN = VERDICTS
FP_SHARE = 0.5          # 점포 판정: 판정 가능한 글 중 다른가게 비율이 이 이상이면 오탐 점포
DESC_LEN = 200
RECENT_MONTHS = 12
NO_POSTS = "매칭 글 없음"
# blind: 대상 목록·판정표에 절대 들어가면 안 되는 열 (이름 일부로 검사)
FORBIDDEN_WORDS = ("prob", "risk", "band", "percentile", "event", "close", "폐업", "영업", "위험", "등급", "status",
                   "contribution", "group", "stratum")
TARGET_COLS = ["review_id", "store_id", "name_raw", "name_norm", "gu", "dong", "biz_type"]  # 전달용 (선정 그룹·층 없음)
KEY_COLS = ["review_id", "store_id", "groups", "stratum"]                                  # 분석 쪽 보관
GROUPS = ("priority", "random", "short_name")
GROUP_SEP = ";"                  # key의 groups: 속한 그룹을 모두 (예: "random;short_name")
SHORT = "short_name"
OPEN, CLOSED = "영업", "폐업"     # 짧은 상호 층 — key에만 둔다
STATUS_STRATUM = {"01": OPEN, "03": CLOSED}  # licenses status_code (01 영업/정상, 03 폐업)
SHORT_NAME_MAX = 2
SHEET_COLS = ["review_id", "name_raw", "gu", "dong", "biz_type", "item_no", "post_date", "blog_name", "title",
              "description", "link", "verdict", "note"]


def assert_blind(columns) -> None:
    bad = [c for c in columns if any(w in str(c).lower() for w in FORBIDDEN_WORDS)]
    if bad:
        raise ValueError(f"blind 원칙 위반 — 위험도·등급·폐업 관련 열: {bad}")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def serve_meta_near(path) -> dict:
    """diagnosis.parquet 등 serve 출력 폴더 옆의 serve_meta.json (M4: 재현성용 serve 기준 정보). 없으면 빈 dict."""
    p = Path(path).parent / "serve_meta.json"
    if not p.exists():
        return {}
    m = json.loads(p.read_text(encoding="utf-8"))
    return {"serve_score_origin": m.get("score_origin"), "serve_as_of": m.get("as_of"),
            "serve_meta_sha256": sha256_file(p)}


# ---------------------------------------------------------------------------- targets
# PR #21 (origin/feature/online-presence-collection, 커밋 f026602) src/data/collect_online_presence.py의
# normalize_name과 똑같이 맞춘다 — 블로그 언급 매칭에 쓴 정규화라야 "짧은 상호"가 DATA_CATALOG §6과 같은 뜻이 된다.
# licenses의 name_norm(standardize.py)과는 다른 규칙이므로 섞어 쓰지 않는다.
def match_name_norm(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", name or "").lower()


def is_short_name(name: str) -> bool:
    return len(match_name_norm(name)) <= SHORT_NAME_MAX


def blog_mention_totals(mentions: pd.DataFrame) -> pd.Series:
    """store_id별 블로그 언급 합계 (online_mentions_monthly)."""
    m = mentions[mentions["platform"] == "blog"]
    return pd.to_numeric(m["mention_count"]).groupby(m["store_id"]).sum()


def short_name_pool(licenses: pd.DataFrame, mention_totals: pd.Series,
                    master_store_ids: set[str] | None = None) -> pd.DataFrame:
    """짧은 상호이면서 블로그 언급이 1건 이상인 점포 (언급이 없으면 판정할 글이 없다). 층(영업/폐업) 포함.

    master_store_ids가 있으면 그 점포로만 한정한다 (#39 리뷰 M2: 모델이 실제로 쓰는 master_base 대상 점포 —
    검수 결과가 모델 feature 처리(NA 여부)에 쓰일 대상과 일치해야 한다).
    """
    lic = licenses[licenses["name_raw"].map(is_short_name)]
    lic = lic[mention_totals.reindex(lic["store_id"]).fillna(0).to_numpy() >= 1]
    if master_store_ids is not None:
        lic = lic[lic["store_id"].isin(master_store_ids)]
    stratum = lic["status_code"].map(STATUS_STRATUM)
    if stratum.isna().any():
        raise ValueError(f"영업/폐업으로 나눌 수 없는 status_code: {sorted(lic.loc[stratum.isna(), 'status_code'].unique())}")
    return (pd.DataFrame({"store_id": lic["store_id"], "gu": lic["gu"], "biz_type": lic["business_type"],
                          "stratum": stratum})
            .sort_values("store_id").reset_index(drop=True))


def sample_strata(short_pool: pd.DataFrame, n_per_stratum: int, seed: int) -> pd.DataFrame:
    """층(영업/폐업)별 n곳 무작위. 층이 n보다 작으면 전부."""
    # priority/random과 다른 난수열 — 짧은 상호 그룹을 더해도 priority/random 선정이 바뀌지 않게
    rng = np.random.default_rng([seed, 2])
    out = []
    for s in (OPEN, CLOSED):
        g = short_pool[short_pool["stratum"] == s].sort_values("store_id")
        k = min(n_per_stratum, len(g))
        out.append(g.iloc[np.sort(rng.choice(len(g), k, replace=False))] if k else g)
    return pd.concat(out)


def review_pool(diagnosis: pd.DataFrame) -> pd.DataFrame:
    """온라인 요인이 검토 대기인 점포 (#39 리뷰 M3: hold_reason으로 명시 — display=false·data_missing=false
    조합보다 그 사유 자체를 직접 쓴다. diagnose.py 불변식상 두 조건은 같은 집합이어야 한다)."""
    d = diagnosis[diagnosis["factor_id"] == "online_attention"]
    held = d[d["hold_reason"] == "online_review"]
    return held[["store_id", "gu", "biz_type"]].drop_duplicates("store_id").reset_index(drop=True)


# ---------------------------------------------------------------------------- M1: 날짜 필터
def master_base_span(master: pd.DataFrame) -> pd.DataFrame:
    """점포별 master_base 등장 구간: 첫 origin_start − 3개월 ~ 마지막 origin_end.
    #39 리뷰 M1 — short_name 글은 이 구간 안(모형이 실제로 그 점포를 보는 기간)으로 한정한다."""
    g = master.groupby("store_id").agg(span_start=("origin_start", "min"), span_end=("origin_end", "max"))
    g["span_start"] = g["span_start"] - pd.DateOffset(months=3)
    return g.reset_index()


def date_bounds_by_group(licenses: pd.DataFrame, key: pd.DataFrame, *, master_span: pd.DataFrame | None = None,
                         serve_as_of: str | None = None) -> dict[str, dict[str, tuple]]:
    """#39 리뷰 M1 + 추가 리뷰(겹침 점포): store_id → {그룹: (하한, 상한)} — 양끝 포함, 없으면 None.

    그룹마다 자기 구간을 따로 갖는다(겹치는 점포는 그룹별로 다른 구간). 한 점포가 여러 그룹에 속하면
    이 함수는 각 그룹의 구간을 그대로 반환한다 — 합치는 건(추출용 합집합) `_within_union`이, 그룹별로
    거르는 건(집계용) `_group_view`가 한다. 이 함수 자체는 교집합·합집합을 계산하지 않는다.

    - short_name 그룹: 인허가일 ~ 폐업일(영업 중이면 무제한), master_span이 있으면 그 구간과 교집합
      (모형이 그 점포를 실제로 보는 기간 안의 글만 — 폐업 이후 다른 가게 글이 폐업 층 오탐률을 구조적으로
      높이는 문제, 인허가 이전 글이 다른 가게/동명이인일 위험을 막는다).
    - priority/random 그룹: 하한 없음, serve_as_of가 상한 (예측 기준일 이후 글은 아직 모형이 못 본 정보).
    - serve_as_of는 모든 그룹의 상한에 공통으로 적용한다(짧은 상호도 미래 글을 쓰지 않는다 — 보통
      폐업일/구간 상한이 이미 더 좁아 영향이 없다).
    """
    lic = licenses.set_index("store_id")[["license_date", "close_date"]].to_dict("index")
    span = master_span.set_index("store_id")[["span_start", "span_end"]].to_dict("index") \
        if master_span is not None else {}
    cutoff = pd.Timestamp(serve_as_of) if serve_as_of else None
    groups_by_store = key.drop_duplicates("store_id").set_index("store_id")["groups"]
    out: dict[str, dict[str, tuple]] = {}
    for sid, groups in groups_by_store.items():
        parts = str(groups).split(GROUP_SEP)
        per_group: dict[str, tuple] = {}
        for g in parts:
            lo = hi = None
            if g == SHORT:
                l = lic.get(sid, {})
                lo, hi = l.get("license_date"), l.get("close_date")
                if pd.isna(lo):
                    lo = None
                if pd.isna(hi):
                    hi = None
                sp = span.get(sid)
                if sp is not None:
                    lo = sp["span_start"] if lo is None else max(lo, sp["span_start"])
                    hi = sp["span_end"] if hi is None else min(hi, sp["span_end"])
            if cutoff is not None:
                hi = cutoff if hi is None else min(hi, cutoff)
            per_group[g] = (lo, hi)
        out[sid] = per_group
    return out


def _within_bounds(dates: pd.Series, store_ids: pd.Series, bounds: dict[str, tuple]) -> pd.Series:
    """dates가 bounds[store_id]의 [하한, 상한](포함) 안인지. bounds에 없는 store_id는 제한 없음. 날짜 결측은 제외."""
    lo = pd.to_datetime(store_ids.map(lambda s: bounds.get(s, (None, None))[0]))
    hi = pd.to_datetime(store_ids.map(lambda s: bounds.get(s, (None, None))[1]))
    return dates.notna() & (lo.isna() | (dates >= lo)) & (hi.isna() | (dates <= hi))


def _within_union(dates: pd.Series, store_ids: pd.Series, bounds_by_group: dict[str, dict[str, tuple]]) -> pd.Series:
    """추출용(합집합): dates가 그 점포가 속한 그룹 중 하나라도의 구간 안이면 True. 겹치는 점포는 두 그룹
    구간의 합집합이 된다 — 교집합이 아니다(추가 리뷰). store_id가 bounds_by_group에 없으면 제한 없음."""
    def ok(d, sid):
        if pd.isna(d):
            return False
        groups = bounds_by_group.get(sid)
        if groups is None:
            return True
        for lo, hi in groups.values():
            if (lo is None or d >= lo) and (hi is None or d <= hi):
                return True
        return False
    return pd.Series([ok(d, s) for d, s in zip(dates, store_ids)], index=dates.index)


def apply_date_bounds(items: pd.DataFrame, bounds: dict[str, tuple], *, date_col: str = "postdate",
                      fmt: str = "%Y%m%d") -> tuple[pd.DataFrame, int]:
    """items에서 date_bounds 밖의 행을 뺀다 (M1). 반환: (남은 items, 제외된 행 수)."""
    dates = pd.to_datetime(items[date_col].astype(str), format=fmt, errors="coerce")
    keep = _within_bounds(dates, items["store_id"], bounds)
    return items[keep].reset_index(drop=True), int((~keep).sum())


def _probabilities(diagnosis_path: Path, risk_path: Path | None) -> pd.Series:
    cands = [risk_path] if risk_path else [diagnosis_path.parent / "diagnosis_by_category.parquet",
                                           diagnosis_path.parent / "risk_scores.parquet"]
    for p in cands:
        if p is not None and Path(p).exists():
            r = pd.read_parquet(p, columns=["store_id", "probability_12m"])
            return r.drop_duplicates("store_id").set_index("store_id")["probability_12m"]
    raise FileNotFoundError(f"priority 선정용 예측 확률 파일이 없다: {cands}")


def select_targets(pool: pd.DataFrame, prob: pd.Series, licenses: pd.DataFrame, *,
                   n_priority: int, n_random: int, seed: int,
                   short_pool: pd.DataFrame | None = None, n_short_per_stratum: int = 50
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """priority(확률 상위) + random(나머지에서 무작위) + short_name(짧은 상호 층별 무작위).
    반환: (대상 목록, 선정 그룹 키).

    확률은 어느 표에도 넣지 않는다. 대상 목록에는 선정 그룹·층도 없다 (판정하는 쪽이 선정 이유를 모르게).
    짧은 상호 표본은 online_review 선정과 독립으로 뽑고, 겹치는 점포는 한 번만 싣는다.
    """
    if pool.empty:
        raise ValueError("검토 대기 점포가 없다")
    p = prob.reindex(pool["store_id"])
    if p.isna().any():
        raise ValueError(f"예측 확률이 없는 대상 점포 {int(p.isna().sum())}곳")
    order = pool.assign(_p=p.to_numpy()).sort_values(["_p", "store_id"], ascending=[False, True])
    pri = order.head(n_priority)
    rest = order.iloc[n_priority:].sort_values("store_id")
    rng = np.random.default_rng(seed)
    k = min(n_random, len(rest))
    rnd = rest.iloc[np.sort(rng.choice(len(rest), k, replace=False))] if k else rest.iloc[:0]
    parts = [pri.assign(group="priority"), rnd.assign(group="random")]
    if short_pool is not None:
        parts.append(sample_strata(short_pool, n_short_per_stratum, seed).assign(group=SHORT))
    rows = pd.concat(parts).drop(columns="_p")
    rows["stratum"] = rows["stratum"].fillna("") if "stratum" in rows else ""
    rows["_order"] = rows["group"].map({g: i for i, g in enumerate(GROUPS)})
    # 겹치는 점포는 한 줄로 — groups에 속한 그룹을 모두 적는다
    sel = (rows.sort_values(["store_id", "_order"])
           .groupby("store_id", sort=True)
           .agg(gu=("gu", "first"), biz_type=("biz_type", "first"),
                groups=("group", GROUP_SEP.join), stratum=("stratum", "max"))
           .reset_index())

    if licenses["store_id"].duplicated().any():
        raise ValueError("인허가 테이블 store_id 중복")
    sel = sel.merge(licenses[["store_id", "name_raw", "name_norm", "dong"]], on="store_id", how="left")
    if sel["name_raw"].isna().any():
        raise ValueError(f"인허가 테이블에 없는 대상 점포 {int(sel['name_raw'].isna().sum())}곳")
    # blind: priority가 위에 몰리지 않게 섞은 뒤 번호를 붙인다
    sel = sel.iloc[rng.permutation(len(sel))].reset_index(drop=True)
    width = max(2, len(str(len(sel))))
    sel["review_id"] = [f"R{i:0{width}d}" for i in range(1, len(sel) + 1)]
    targets, key = sel[TARGET_COLS].copy(), sel[KEY_COLS].copy()
    assert_blind(targets.columns)
    assert not {"groups", "stratum"} & set(targets.columns)
    return targets, key


def key_meta_path(key_path: Path) -> Path:
    """선정 그룹 키 옆의 메타 (name_match_key.csv → name_match_key_meta.json)."""
    return Path(key_path).with_name(f"{Path(key_path).stem}_meta.json")


def check_key_meta_pair(meta: dict, targets_path, key_path) -> None:
    """메타에 적힌 targets·key sha256이 실제 파일과 같은지 확인한다 (다른 회차 파일과 섞이면 가중치·serve 기준이
    틀린다). 다르면 멈춘다. 이 기능 이전에 만든 메타(해시 없음)는 확인할 수 없어 경고만 한다."""
    pairs = [("targets_sha256", targets_path), ("key_sha256", key_path)]
    if not any(meta.get(k) for k, _ in pairs):
        print("경고: 선정 그룹 키 메타에 targets/key sha256이 없어 짝을 확인하지 못했다 (이전 버전 메타)")
        return
    bad = [f"{k}({Path(p).name})" for k, p in pairs if p is not None and meta.get(k) and meta[k] != sha256_file(p)]
    if bad:
        raise ValueError(f"선정 그룹 키 메타와 파일이 짝이 아니다: {bad} — 같은 targets 실행에서 나온 세 파일을 쓴다")


def cmd_targets(a) -> pd.DataFrame:
    diagnosis = pd.read_parquet(a.diagnosis, columns=["store_id", "gu", "biz_type", "factor_id", "hold_reason"])
    pool = review_pool(diagnosis)
    prob = _probabilities(Path(a.diagnosis), a.risk)
    lic = pd.read_parquet(a.licenses, columns=["store_id", "name_raw", "name_norm", "dong"]
                          + (["gu", "business_type", "status_code"] if a.mentions else []))
    master_ids = None
    n_short_before = None
    if a.mentions and a.master:
        master_ids = set(pd.read_parquet(a.master, columns=["store_id"])["store_id"].unique())
    short_pool = None
    if a.mentions:
        mention_totals = blog_mention_totals(pd.read_parquet(a.mentions, columns=["store_id", "platform",
                                                                                  "mention_count"]))
        if master_ids is not None:  # M2: master_base 제한 전후 모집단 크기를 함께 보고
            before = short_name_pool(lic, mention_totals)
            n_short_before = before["stratum"].value_counts()
        short_pool = short_name_pool(lic, mention_totals, master_store_ids=master_ids)
        n_s = short_pool["stratum"].value_counts()
        if n_short_before is not None:
            print(f"짧은 상호·블로그 언급 1건 이상 {len(before):,}점포 (영업 {int(n_short_before.get(OPEN, 0)):,}, "
                  f"폐업 {int(n_short_before.get(CLOSED, 0)):,}) → master_base 대상으로 한정 {len(short_pool):,}점포 "
                  f"(영업 {int(n_s.get(OPEN, 0)):,}, 폐업 {int(n_s.get(CLOSED, 0)):,})")
        else:
            print(f"짧은 상호(정규화 후 {SHORT_NAME_MAX}자 이하)·블로그 언급 1건 이상 {len(short_pool):,}점포 "
                  f"(영업 {int(n_s.get(OPEN, 0)):,}, 폐업 {int(n_s.get(CLOSED, 0)):,}) — --master 없어 미제한")
    else:
        print("--mentions가 없어 짧은 상호(short_name) 그룹은 뽑지 않는다")
    out, key = select_targets(pool, prob, lic, n_priority=a.n_priority, n_random=a.n_random, seed=a.seed,
                              short_pool=short_pool, n_short_per_stratum=a.n_short_per_stratum)
    key_out = a.key_out or Path(a.out).parent / "name_match_key.csv"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(key_out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False, encoding="utf-8-sig")
    key.to_csv(key_out, index=False, encoding="utf-8-sig")
    # 집계 때 short_name 가중치(층별 모집단 크기)로 쓴다. 점포 식별 정보 없이 수만 둔다.
    # #39 리뷰 M4: 입력 파일 해시와 serve 기준(score_origin/as_of)을 남긴다 — 재현성.
    meta = {"seed": a.seed, "diagnosis": Path(a.diagnosis).as_posix(), "diagnosis_sha256": sha256_file(a.diagnosis),
            "licenses_sha256": sha256_file(a.licenses), "mentions_sha256": sha256_file(a.mentions) if a.mentions else None,
            "master_sha256": sha256_file(a.master) if a.master else None,
            "online_review_pool_n": len(pool),
            "short_pool_n": ({s: int((short_pool["stratum"] == s).sum()) for s in (OPEN, CLOSED)}
                             if short_pool is not None else None),
            "short_pool_n_before_master_restriction": ({s: int(n_short_before.get(s, 0)) for s in (OPEN, CLOSED)}
                                                        if n_short_before is not None else None),
            **serve_meta_near(a.diagnosis),
            # 이 메타와 짝인 대상 목록·키 — summarize/round2가 다른 회차 파일과 섞이지 않았는지 확인한다
            "targets_sha256": sha256_file(a.out), "key_sha256": sha256_file(key_out)}
    key_meta_path(Path(key_out)).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    m = {g: in_group(key["groups"], g) for g in GROUPS}
    short = key[m[SHORT]]
    print(f"검토 대기 {len(pool):,}점포 → 대상 {len(out)}곳 (priority {int(m['priority'].sum())}, "
          f"random {int(m['random'].sum())}, short_name {len(short)} [영업 {int((short['stratum'] == OPEN).sum())}"
          f" / 폐업 {int((short['stratum'] == CLOSED).sum())}], 두 그룹 이상 {int(key['groups'].str.contains(GROUP_SEP).sum())})")
    print(f"  대상 목록(전달용, 선정 그룹·층 없음) → {a.out}")
    print(f"  선정 그룹 키(보관, 전달하지 않음) → {key_out}")
    return out


# ---------------------------------------------------------------------------- sheet
_TAG = re.compile(r"<[^>]+>")


def clean_text(s) -> str:
    """HTML 태그(<b> 등) 제거 + 엔티티(&amp; 등) 복원 + 공백 정리."""
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return ""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", str(s)))).strip()


def blog_name_from_link(link: str) -> str:
    """원본에 블로그명이 없어 링크에서 블로그 ID를 뽑는다 (blog.naver.com/<id>/<글번호>)."""
    u = urlparse(link or "")
    parts = [p for p in u.path.split("/") if p]
    if "blog.naver.com" in u.netloc and parts:
        return parts[0]
    return u.netloc or ""


def read_items(paths, store_ids: set[str]) -> pd.DataFrame:
    rows = []
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("store_id") in store_ids and r.get("matched"):
                    rows.append(r)
    df = pd.DataFrame(rows, columns=["store_id", "link", "title", "description", "postdate", "matched"])
    # 이어받기(resume) 수집으로 같은 글이 두 번 들어갈 수 있다
    return df.drop_duplicates(["store_id", "link"]).reset_index(drop=True)


def _post_date(s) -> str:
    s = str(s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


def pick_posts(items: pd.DataFrame, per_store: int, seed: int) -> pd.DataFrame:
    """점포별 최대 per_store건. 날짜 최신순이 아니라 seed 고정 무작위이며, 최근 12개월 글을 먼저 채운다."""
    if items.empty:
        return items.assign(item_no=pd.Series(dtype=int))
    dates = pd.to_datetime(items["postdate"], format="%Y%m%d", errors="coerce")
    ref = dates.max()
    recent = dates >= (ref - pd.DateOffset(months=RECENT_MONTHS)) if pd.notna(ref) else pd.Series(False, index=items.index)
    rng = np.random.default_rng(seed)
    out = []
    for sid, g in items.assign(_recent=recent.to_numpy()).groupby("store_id", sort=True):
        g = g.sort_values("link")  # 입력 순서와 무관하게 재현되도록
        g = g.assign(_r=rng.random(len(g)))
        g = g.sort_values(["_recent", "_r"], ascending=[False, True]).head(per_store)
        out.append(g.assign(item_no=range(1, len(g) + 1)))
    return pd.concat(out, ignore_index=True).drop(columns=["_recent", "_r"])


def build_sheet(targets: pd.DataFrame, items: pd.DataFrame, per_store: int, seed: int) -> pd.DataFrame:
    posts = pick_posts(items, per_store, seed)
    t = targets[["review_id", "store_id", "name_raw", "gu", "dong", "biz_type"]]
    rows = []
    for r in t.itertuples(index=False):
        g = posts[posts["store_id"] == r.store_id] if len(posts) else posts
        base = {"review_id": r.review_id, "name_raw": r.name_raw, "gu": r.gu, "dong": r.dong, "biz_type": r.biz_type}
        if len(g) == 0:
            rows.append({**base, "item_no": 0, "post_date": "", "blog_name": "", "title": NO_POSTS,
                         "description": "", "link": "", "verdict": "", "note": ""})
            continue
        for p in g.itertuples(index=False):
            rows.append({**base, "item_no": int(p.item_no), "post_date": _post_date(p.postdate),
                         "blog_name": blog_name_from_link(p.link), "title": clean_text(p.title),
                         "description": clean_text(p.description)[:DESC_LEN], "link": p.link,
                         "verdict": "", "note": ""})
    sheet = pd.DataFrame(rows, columns=SHEET_COLS)
    sheet = sheet.sort_values(["review_id", "item_no"]).reset_index(drop=True)
    assert_blind(sheet.columns)
    assert "store_id" not in sheet.columns and "group" not in sheet.columns
    return sheet


SHEET_README = """# 상호 매칭 판정표 안내 (#28)

같은 폴더의 `{sheet}`를 채워 주세요. 한 줄 = 블로그 글 1건입니다.

- `verdict` 칸에 셋 중 하나를 적습니다: **해당가게 / 다른가게 / 판단불가**
  - 해당가게: 글이 이 점포(상호·동·업종)에 대한 글이다
  - 다른가게: 같은 상호의 다른 지점·다른 지역 가게, 또는 상호가 일반 단어로 쓰인 글
  - 판단불가: 지역·메뉴·사진 정보로도 이 점포인지 알 수 없다
- 필요하면 `note`에 짧게 근거를 적습니다 (예: "다른 구 지점", "일반 명사로 쓰임").
- `item_no = 0`, 제목 "매칭 글 없음"인 줄은 판정하지 않고 비워 둡니다.
- `blog_name`은 원본에 블로그명이 없어 링크에서 뽑은 블로그 ID입니다.
- 이 표에는 위험도·등급·폐업 여부가 없습니다 (blind 검수). 판정은 글 내용만 보고 합니다.
- 다 채운 파일은 저장소가 아니라 **팀 드라이브**로 돌려 주세요 (상호가 들어 있습니다).
"""


def cmd_sheet(a) -> pd.DataFrame:
    targets = pd.read_csv(a.targets, dtype=str, encoding="utf-8-sig")
    if {"group", "groups", "stratum"} & set(targets.columns):
        raise ValueError("대상 목록에 선정 그룹(group)·층 열이 있다 — blind 판정이 깨진다. "
                         "targets가 만든 name_match_targets.csv(그룹 없음)를 쓴다 (name_match_key.csv 아님)")
    assert_blind(targets.columns)
    items = read_items(a.items, set(targets["store_id"]))

    # #39 리뷰 M1: 인허가일 이전/폐업일 이후(짧은 상호), serve 기준일 이후(priority/random) 글 제외.
    # --key(groups) 없이는 그룹을 몰라 필터를 적용할 수 없다 — 그때는 생략하고 경고한다.
    filter_meta = {"applied": False}
    if a.key is not None:
        key = pd.read_csv(a.key, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        lic = pd.read_parquet(a.licenses, columns=["store_id", "license_date", "close_date"]) if a.licenses else None
        master_span = master_base_span(pd.read_parquet(a.master, columns=["store_id", "origin_start", "origin_end"])) \
            if a.master else None
        serve_as_of = a.serve_as_of or serve_meta_near(a.targets).get("serve_as_of")
        if lic is None:
            print("경고: --licenses가 없어 short_name 날짜 필터(인허가일·폐업일)를 생략한다")
            lic = pd.DataFrame({"store_id": [], "license_date": [], "close_date": []})
        bounds_by_group = date_bounds_by_group(lic, key, master_span=master_span, serve_as_of=serve_as_of)
        before = items.merge(key[["store_id", "groups", "stratum"]].drop_duplicates("store_id"),
                             on="store_id", how="left")
        dates = pd.to_datetime(before["postdate"].astype(str), format="%Y%m%d", errors="coerce")
        # 추출은 합집합 — 겹치는 점포는 두 그룹 구간 중 하나라도 통과하면 글을 남긴다(추가 리뷰). 집계
        # 단계(summarize)에서 그룹마다 자기 구간으로 다시 거른다.
        keep = _within_union(dates, before["store_id"], bounds_by_group)
        bucket = np.where(before["stratum"].fillna("") != "", SHORT + ":" + before["stratum"].fillna(""),
                          before["groups"].fillna("").str.split(GROUP_SEP).str[0])
        excl_by = pd.Series(bucket[~keep.to_numpy()]).value_counts().to_dict()
        items = before[keep].drop(columns=["groups", "stratum"]).reset_index(drop=True)
        filter_meta = {"applied": True, "items_before": len(before), "items_excluded": int((~keep).sum()),
                      "items_excluded_by_group": excl_by, "serve_as_of": str(serve_as_of) if serve_as_of else None}
        print(f"M1 날짜 필터: {len(before):,}건 중 {int((~keep).sum()):,}건 제외 (그룹·층별 {excl_by}) → {len(items):,}건")
    else:
        print("경고: --key가 없어 M1 날짜 필터를 생략한다 (인허가일·폐업일·serve 기준일 확인 안 됨)")

    sheet = build_sheet(targets, items, a.per_store, a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(out, index=False, encoding="utf-8-sig")
    (out.parent / f"{out.stem}_README.md").write_text(SHEET_README.format(sheet=out.name), encoding="utf-8")
    meta = {"per_store": a.per_store, "seed": a.seed, "items_sha256": {str(p): sha256_file(p) for p in a.items},
            "date_filter": filter_meta}
    (out.parent / f"{out.stem}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    n0 = int((sheet["item_no"] == 0).sum())
    print(f"판정표 {len(sheet)}줄 (대상 {targets['review_id'].nunique()}곳, 매칭 글 없음 {n0}곳) → {out}")
    return sheet


# ---------------------------------------------------------------------------- summarize
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score 95% 구간."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, mid - half), min(1.0, mid + half))


def newcombe_diff(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float, float]:
    """p1 − p2와 95% 구간 (Newcombe 1998 method 10, 두 Wilson 구간 결합)."""
    if n1 == 0 or n2 == 0:
        return (float("nan"),) * 3
    p1, p2 = k1 / n1, k2 / n2
    (l1, u1), (l2, u2) = wilson(k1, n1), wilson(k2, n2)
    d = p1 - p2
    return (d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


NO_GROUP = "미구분"


def in_group(groups: pd.Series, g: str) -> pd.Series:
    return groups.fillna("").astype(str).str.split(GROUP_SEP).map(lambda xs: g in xs)


def judge(sheet: pd.DataFrame, targets: pd.DataFrame, key: pd.DataFrame | None = None
          ) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """글 표와 점포 판정 표. key(review_id, groups, stratum)가 없으면 그룹은 "미구분".
    이전 형식 키(group 한 열)도 읽는다. 반환: (items, stores, 빈 verdict 수)."""
    targets = targets[["review_id", "store_id"]].copy()
    if key is not None:
        key = key.rename(columns={"group": "groups"})
        if "stratum" not in key:
            key = key.assign(stratum="")
        targets = targets.merge(key[["review_id", "groups", "stratum"]], on="review_id", how="left")
        if targets["groups"].isna().any():
            raise ValueError(f"선정 그룹 키에 없는 review_id {int(targets['groups'].isna().sum())}건")
        targets["stratum"] = targets["stratum"].fillna("")
    else:
        targets["groups"], targets["stratum"] = NO_GROUP, ""
    s = sheet.copy()
    s["item_no"] = pd.to_numeric(s["item_no"], errors="coerce").fillna(0).astype(int)
    s = s[s["item_no"] > 0]
    s["verdict"] = s["verdict"].fillna("").astype(str).str.strip()
    blank = s["verdict"] == ""
    bad = ~s["verdict"].isin(VERDICTS) & ~blank
    if bad.any():
        raise ValueError(f"verdict 값은 {VERDICTS} 중 하나: {sorted(s.loc[bad, 'verdict'].unique())}")
    n_blank = int(blank.sum())
    if n_blank:
        # #39 리뷰 M3: 미입력을 조용히 빼지 않는다 — 사람이 판단불가로 정한 것과 아직 안 채운 것은 다르다.
        raise ValueError(f"verdict 미입력 글 {n_blank}건 — 모두 채운 뒤 다시 시도한다 (판단할 수 없으면 "
                         f"빈칸이 아니라 '{UNKNOWN}'으로 채운다)")
    s = s.merge(targets[["review_id", "store_id", "groups", "stratum"]], on="review_id", how="left")
    rows = []
    for t in targets.itertuples(index=False):
        g = s[s["review_id"] == t.review_id]
        n_same, n_other, n_unk = (int((g["verdict"] == v).sum()) for v in VERDICTS)
        dec = n_same + n_other
        share = n_other / dec if dec else float("nan")
        rows.append({"review_id": t.review_id, "store_id": t.store_id, "groups": t.groups, "stratum": t.stratum,
                     "n_items": len(g),
                     "n_same": n_same, "n_other": n_other, "n_unknown": n_unk, "other_share": share,
                     "store_verdict": ("판정불가" if not dec else "오탐" if share >= FP_SHARE else "정상")})
    return s, pd.DataFrame(rows), n_blank


def _rows_of(df: pd.DataFrame, grp: str) -> pd.DataFrame:
    """그룹("random"), 층("short_name:폐업"), "전체". 두 그룹에 속한 점포는 두 그룹 모두에 센다."""
    if grp == "전체":
        return df
    g, _, stratum = grp.partition(":")
    m = in_group(df["groups"], g)
    return df[m & (df["stratum"] == stratum)] if stratum else df[m]


def _store_verdicts_from_items(items_subset: pd.DataFrame) -> dict[str, str]:
    """review_id별 점포 판정(다수결, FP_SHARE 기준)을 주어진 글 부분집합에서 다시 계산한다."""
    out = {}
    for rid, g in items_subset.groupby("review_id"):
        n_same = int((g["verdict"] == SAME).sum())
        n_other = int((g["verdict"] == OTHER).sum())
        dec = n_same + n_other
        out[rid] = "판정불가" if not dec else ("오탐" if n_other / dec >= FP_SHARE else "정상")
    return out


def _group_view(items: pd.DataFrame, stores: pd.DataFrame, grp: str,
                bounds_by_group: dict[str, dict[str, tuple]] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """그룹(또는 그룹:층) 부분집합. bounds_by_group이 있으면(grp가 "전체"가 아닐 때) 그 그룹 자기 구간으로
    글을 다시 걸러 점포 판정도 그 글로 다시 계산한다 — 겹치는 점포는 그룹마다 판정이 다를 수 있다(추가 리뷰)."""
    it, st = _rows_of(items, grp), _rows_of(stores, grp)
    if bounds_by_group is None or grp == "전체":
        return it, st
    g, _, _ = grp.partition(":")
    g_bounds = {sid: gb[g] for sid, gb in bounds_by_group.items() if g in gb}
    dates = pd.to_datetime(it["post_date"], errors="coerce")
    it = it[_within_bounds(dates, it["store_id"], g_bounds)]
    sv = _store_verdicts_from_items(it)
    st = st.assign(store_verdict=st["review_id"].map(sv).fillna("판정불가"))
    return it, st


def _counts(it: pd.DataFrame, st: pd.DataFrame) -> tuple[int, int, int, int]:
    return (int((it["verdict"] == OTHER).sum()), int(it["verdict"].isin([SAME, OTHER]).sum()),
            int((st["store_verdict"] == "오탐").sum()), int(st["store_verdict"].isin(["오탐", "정상"]).sum()))


def _has_ci(grp: str) -> bool:
    """무작위 표본에만 신뢰구간 (priority는 확률 상위라 모집단 추정에 쓰지 않는다)."""
    return grp.partition(":")[0] in ("random", SHORT)


def weighted_rate(counts: dict[str, tuple[int, int]], pool_n: dict[str, int]) -> tuple[float, float, float]:
    """층별 모집단 비율로 가중한 비율과 95% 구간 (MOVER-Wilson: 층별 Wilson 구간을 가중합으로 결합 —
    층 차이의 Newcombe 구간과 같은 방식). counts: 층 → (k, n). 판정된 표본이 없는 층이 있으면 NaN."""
    total = sum(pool_n.values())
    if total == 0 or any(counts[s][1] == 0 for s in pool_n):
        return (float("nan"),) * 3
    est = lo_sq = hi_sq = 0.0
    for s, n_pop in pool_n.items():
        k, n = counts[s]
        w, p = n_pop / total, k / n
        lo, hi = wilson(k, n)
        est += w * p
        lo_sq += (w * (p - lo)) ** 2
        hi_sq += (w * (hi - p)) ** 2
    return est, max(0.0, est - math.sqrt(lo_sq)), min(1.0, est + math.sqrt(hi_sq))


def rates(items: pd.DataFrame, stores: pd.DataFrame, short_pool_n: dict[str, int] | None = None,
         bounds_by_group: dict[str, dict[str, tuple]] | None = None, separate_no_match: bool = False) -> pd.DataFrame:
    """그룹별 오탐률. short_pool_n(층 → 모집단 점포 수)이 있으면 short_name 행은 층별 모집단 비율로 가중한 값이고,
    비가중(표본 그대로, 층별 같은 수) 값은 *_unweighted 열에 참고로 둔다.

    bounds_by_group이 있으면(추가 리뷰: 겹침 점포) 그룹별 행은 그 그룹 자기 구간을 통과한 글만 세고
    점포 판정도 그 글로 다시 계산한다 — "전체" 행은 그대로(추출 때 이미 합집합으로 걸러졌다).

    separate_no_match=True(옵션, 기본 꺼짐): 그 그룹의 유효 글이 0건인 점포×그룹을 `stores_no_match`로 따로 세고
    `stores_undecided`(판단불가로 끝난 점포)에서 뺀다. 오탐률 분모는 어느 쪽이든 바뀌지 않는다(판정 글이 없으면
    원래 분모에 안 들어간다)."""
    out = []
    groups = [g for g in GROUPS if in_group(stores["groups"], g).any()]
    if SHORT in groups:
        i = groups.index(SHORT) + 1
        groups[i:i] = [f"{SHORT}:{s}" for s in (OPEN, CLOSED) if len(_rows_of(stores, f"{SHORT}:{s}"))]
    nan = float("nan")
    for grp in groups + ["전체"]:
        it, st = _group_view(items, stores, grp, bounds_by_group)
        ki, ni, ks, ns = _counts(it, st)
        lo_i, hi_i = wilson(ki, ni) if _has_ci(grp) else (nan, nan)
        lo_s, hi_s = wilson(ks, ns) if _has_ci(grp) else (nan, nan)
        row = {"group": grp, "items_decided": ni, "items_other": ki, "item_fp_rate": ki / ni if ni else nan,
               "item_ci_low": lo_i, "item_ci_high": hi_i,
               "stores_decided": ns, "stores_fp": ks, "store_fp_rate": ks / ns if ns else nan,
               "store_ci_low": lo_s, "store_ci_high": hi_s,
               "stores_undecided": int((st["store_verdict"] == "판정불가").sum()),
               "weighted": False, "item_fp_rate_unweighted": nan, "store_fp_rate_unweighted": nan}
        if separate_no_match:
            no_match = ~st["review_id"].isin(set(it["review_id"]))
            row["stores_no_match"] = int(no_match.sum())
            row["stores_undecided"] = int(((st["store_verdict"] == "판정불가") & ~no_match).sum())
        if grp == SHORT and short_pool_n:
            by = {s: _counts(*_group_view(items, stores, f"{SHORT}:{s}", bounds_by_group)) for s in short_pool_n}
            row.update(weighted=True, item_fp_rate_unweighted=row["item_fp_rate"],
                       store_fp_rate_unweighted=row["store_fp_rate"])
            row["item_fp_rate"], row["item_ci_low"], row["item_ci_high"] = weighted_rate(
                {s: (c[0], c[1]) for s, c in by.items()}, short_pool_n)
            row["store_fp_rate"], row["store_ci_low"], row["store_ci_high"] = weighted_rate(
                {s: (c[2], c[3]) for s, c in by.items()}, short_pool_n)
        out.append(row)
    return pd.DataFrame(out)


def stratum_gap(items: pd.DataFrame, stores: pd.DataFrame,
               bounds_by_group: dict[str, dict[str, tuple]] | None = None) -> pd.DataFrame:
    """짧은 상호 그룹 안에서 폐업 − 영업 오탐률 차이 (점포·글 단위). 양수면 폐업 쪽 오탐이 더 높다."""
    (ki_o, ni_o, ks_o, ns_o), (ki_c, ni_c, ks_c, ns_c) = (
        _counts(*_group_view(items, stores, f"{SHORT}:{s}", bounds_by_group)) for s in (OPEN, CLOSED))
    out = []
    for level, (ko, no, kc, nc) in (("store", (ks_o, ns_o, ks_c, ns_c)), ("item", (ki_o, ni_o, ki_c, ni_c))):
        d, lo, hi = newcombe_diff(kc, nc, ko, no)
        out.append({"level": level, "open_fp": ko, "open_decided": no, "closed_fp": kc, "closed_decided": nc,
                    "open_rate": ko / no if no else float("nan"), "closed_rate": kc / nc if nc else float("nan"),
                    "diff_closed_minus_open": d, "diff_ci_low": lo, "diff_ci_high": hi})
    return pd.DataFrame(out)


def stratum_biz_composition(targets: pd.DataFrame, key: pd.DataFrame) -> pd.DataFrame:
    """#39 리뷰 M2: short_name 표본의 영업/폐업 층별 업종 구성 — 업종 차이가 층 비교(오탐률 격차)에
    섞여 들어오는지 확인용. targets의 biz_type + key의 stratum을 review_id로 잇는다."""
    strat = key[in_group(key["groups"], SHORT)][["review_id", "stratum"]]
    m = targets[["review_id", "biz_type"]].merge(strat, on="review_id", how="inner")
    tab = m.groupby(["stratum", "biz_type"]).size().rename("n").reset_index()
    tab["share"] = tab["n"] / tab.groupby("stratum")["n"].transform("sum")
    return tab.sort_values(["stratum", "biz_type"]).reset_index(drop=True)


def _pp(x) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%p"


def _pct(x) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def cmd_summarize(a) -> pd.DataFrame:
    targets = pd.read_csv(a.targets, dtype=str, encoding="utf-8-sig")
    sheet = pd.read_csv(a.sheet, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    key = pd.read_csv(a.key, dtype=str, encoding="utf-8-sig") if a.key else None
    if key is None:
        print("경고: --key(선정 그룹 키)가 없어 그룹 구분 없이 전체만 집계한다 (신뢰구간 없음)")
    items, stores, n_blank = judge(sheet, targets, key)  # n_blank는 항상 0 — 미입력이 있으면 judge()가 멈춘다
    meta_path = a.key_meta or (key_meta_path(Path(a.key)) if a.key else None)
    short_pool_n = None
    if meta_path is not None and Path(meta_path).exists():
        key_meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        check_key_meta_pair(key_meta, a.targets, a.key)  # 가중치를 읽기 전에 짝부터 확인
        short_pool_n = key_meta.get("short_pool_n")
    if in_group(stores["groups"], SHORT).any() and not short_pool_n:
        print("경고: 선정 그룹 키 메타(short_pool_n)가 없어 short_name 전체 오탐률을 가중하지 못했다 (비가중 값만)")
    bounds_by_group = None
    if a.licenses and key is not None:
        lic = pd.read_parquet(a.licenses, columns=["store_id", "license_date", "close_date"])
        master_span = master_base_span(pd.read_parquet(a.master, columns=["store_id", "origin_start", "origin_end"])) \
            if a.master else None
        serve_as_of = a.serve_as_of
        if serve_as_of is None and meta_path is not None and Path(meta_path).exists():
            serve_as_of = json.loads(Path(meta_path).read_text(encoding="utf-8")).get("serve_as_of")
        bounds_by_group = date_bounds_by_group(lic, key, master_span=master_span, serve_as_of=serve_as_of)
        print("겹침 점포는 그룹마다 자기 구간을 통과한 글로 다시 집계한다 (추가 리뷰)")
    tab = rates(items, stores, short_pool_n, bounds_by_group, separate_no_match=a.separate_no_match)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tab.to_csv(out / "name_match_rates.csv", index=False, encoding="utf-8-sig")
    stores.drop(columns="store_id").to_csv(out / "name_match_store_verdicts.csv", index=False, encoding="utf-8-sig")
    stores.loc[stores["store_verdict"] == "오탐", ["store_id"]].to_csv(out / "false_positive_stores.csv",
                                                                     index=False, encoding="utf-8-sig")
    lines = ["# 상호 매칭 검수 결과 (#28)", "",
             f"- 판정표 {len(sheet)}줄 · 판정된 글 {len(items)}건 · 빈 verdict 제외 {n_blank}건",
             f"- 점포 판정: 판정 가능한 글(해당가게·다른가게) 중 다른가게 비율 ≥ {FP_SHARE:.0%} → 오탐", "",
             "| 그룹 | 글 오탐률 (다른가게/판정) | 95% CI | 점포 오탐률 | 95% CI | 판정불가 점포 |", "|---|---|---|---|---|---|"]
    for r in tab.itertuples(index=False):
        ci_i = f"{_pct(r.item_ci_low)}–{_pct(r.item_ci_high)}" if _has_ci(r.group) else "—"
        ci_s = f"{_pct(r.store_ci_low)}–{_pct(r.store_ci_high)}" if _has_ci(r.group) else "—"
        if r.weighted:  # 가중 추정치는 k/n으로 나타낼 수 없어 표본 수만, 비가중 값은 참고로
            lines.append(f"| {r.group} (모집단 가중) | {_pct(r.item_fp_rate)} (비가중 {_pct(r.item_fp_rate_unweighted)}"
                         f" = {r.items_other}/{r.items_decided}) | {ci_i} | {_pct(r.store_fp_rate)} (비가중 "
                         f"{_pct(r.store_fp_rate_unweighted)} = {r.stores_fp}/{r.stores_decided}) | {ci_s} | "
                         f"{r.stores_undecided} |")
            continue
        lines.append(f"| {r.group} | {_pct(r.item_fp_rate)} ({r.items_other}/{r.items_decided}) | {ci_i} | "
                     f"{_pct(r.store_fp_rate)} ({r.stores_fp}/{r.stores_decided}) | {ci_s} | {r.stores_undecided} |")
    if "stores_no_match" in tab.columns:
        lines += ["", "- **매칭 없음**(유효 글 0건, 오탐률 분모 제외 — `--separate-no-match`): " +
                  ", ".join(f"{r.group} {int(r.stores_no_match)}곳" for r in tab.itertuples(index=False))]
    lines += ["", "- 신뢰구간(Wilson)은 무작위 표본(random, short_name)에만 붙인다. priority는 선정 기준이 달라 "
              "모집단 추정에 쓰지 않는다.",
              "- 두 그룹에 속한 점포는 두 그룹 모두에 센다. `전체`는 점포당 한 번.",
              "- `false_positive_stores.csv`(store_id)는 온라인 feature를 NA로 두는 재실행(#33·#34)에 쓴다. 저장소에 올리지 않는다."]
    if in_group(stores["groups"], SHORT).any():
        gap = stratum_gap(items, stores, bounds_by_group)
        gap.to_csv(out / "name_match_short_strata.csv", index=False, encoding="utf-8-sig")
        if key is not None and "biz_type" in targets.columns:
            comp = stratum_biz_composition(targets, key)
            comp.to_csv(out / "name_match_short_biz_composition.csv", index=False, encoding="utf-8-sig")
        lines += ["", "## 짧은 상호: 폐업 − 영업 오탐률 차이", "",
                  "| 단위 | 영업 | 폐업 | 차이 (폐업−영업) | 95% CI (Newcombe) |", "|---|---|---|---|---|"]
        for r in gap.itertuples(index=False):
            lines.append(f"| {'점포' if r.level == 'store' else '글'} | {_pct(r.open_rate)} ({r.open_fp}/{r.open_decided})"
                         f" | {_pct(r.closed_rate)} ({r.closed_fp}/{r.closed_decided}) | {_pp(r.diff_closed_minus_open)}"
                         f" | {_pp(r.diff_ci_low)} – {_pp(r.diff_ci_high)} |")
        lines += ["", "- 차이 CI 하한이 0보다 크면 폐업 쪽 오탐이 더 높다 → 온라인 feature에 라벨과 상관된 노이즈가 있다.",
                  (f"- short_name 전체 오탐률은 층별 모집단 비율(영업 {short_pool_n.get(OPEN, 0):,} : 폐업 "
                   f"{short_pool_n.get(CLOSED, 0):,})로 가중한 값이다. 95% CI는 층별 Wilson 구간을 같은 가중으로 결합했다"
                   " (MOVER). 비가중 값은 층별 같은 수로 뽑은 표본 그대로라 참고용이다." if short_pool_n else
                   "- short_name 합계 행은 영업·폐업을 같은 수로 뽑은 표본 기준(비가중)이라 짧은 상호 모집단 오탐률이 아니다."),
                  "- 글 단위 CI는 같은 점포 글끼리의 상관을 무시해 좁게 나온다. 판단은 점포 단위를 기준으로 한다."]
    (out / "name_match_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return tab


# ---------------------------------------------------------------------------- 회차 간 판정 재사용 (재검수 부담 축소)
REUSED_FILE = "name_match_reused_round2.csv"  # round2 --out에 저장, merge --reused 기본값
TOP_N_PER_GROUP = 3  # 점포×그룹의 목표 유효 판정 글 수 (추가 리뷰)


def carry_over(old_sheet: pd.DataFrame, old_key: pd.DataFrame, new_targets: pd.DataFrame, new_key: pd.DataFrame,
              bounds_by_group: dict[str, dict[str, tuple]] | None = None
              ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """이전 회차 판정표(old_sheet+old_key, store_id는 old_key로만 안다)에서 새 회차(new_targets+new_key) 대상과
    겹치는 (store_id, link) 판정을 재사용한다. bounds_by_group이 있으면 M1 날짜 필터도 옛 판정에 다시 적용한다
    (필터로 빠지는 글은 재사용하지 않는다 — 회차가 바뀌어도 같은 규칙을 적용해야 하므로).

    재사용은 점포×그룹 단위로 판단한다(추가 리뷰) — 겹치는 점포는 그룹마다 유효한 재사용 글 수가 다를 수
    있다. `reused`는 store 기준 합집합(그룹 구분 없이 저장, summarize가 그룹별로 다시 거른다)이고,
    `need`가 그룹별로 몇 건이 더 필요한지(TOP_N_PER_GROUP=3 미만) 알려준다.

    반환
    - reused: [review_id(새 회차), store_id, link, post_date, verdict, note] — summarize 입력으로 그대로 합칠 수 있다
      (post_date는 merge가 그룹별 구간 재집계용 날짜를 채우는 데 쓴다)
    - need: [review_id, group, n_valid, n_needed] — n_needed = max(0, 3 − n_valid). store_id는 없다
      (review_id로 충분 — 점포 식별 정보 없음).
    - stats: 그룹별 재사용 건수 등 (점포 식별 정보 없음)
    """
    old = old_sheet.merge(old_key[["review_id", "store_id"]], on="review_id", how="left")
    old = old[pd.to_numeric(old["item_no"], errors="coerce").fillna(0) > 0]
    old = old[old["verdict"] != ""]

    new_map = new_key.drop_duplicates("store_id").set_index("store_id")["review_id"]
    old = old[old["store_id"].isin(new_map.index)].copy()
    old["review_id"] = old["store_id"].map(new_map)

    if bounds_by_group is not None:
        dates = pd.to_datetime(old["post_date"], format="%Y-%m-%d", errors="coerce")
        old = old[_within_union(dates, old["store_id"], bounds_by_group)]

    reused = (old[["review_id", "store_id", "link", "post_date", "verdict", "note"]]
              .drop_duplicates(["store_id", "link"]).reset_index(drop=True))

    all_new = new_key.drop_duplicates("store_id")
    need_rows = []
    for r in all_new.itertuples(index=False):
        sid, rid = r.store_id, r.review_id
        for g in str(r.groups).split(GROUP_SEP):
            g_items = old[old["store_id"] == sid]
            gb = bounds_by_group.get(sid, {}).get(g) if bounds_by_group is not None else None
            if gb is not None:
                d2 = pd.to_datetime(g_items["post_date"], format="%Y-%m-%d", errors="coerce")
                lo, hi = gb
                g_items = g_items[d2.notna() & (lo is None or d2 >= lo) & (hi is None or d2 <= hi)]
            n_valid = g_items["link"].nunique()
            need_rows.append({"review_id": rid, "group": g, "n_valid": int(n_valid),
                             "n_needed": max(0, TOP_N_PER_GROUP - int(n_valid))})
    need = pd.DataFrame(need_rows, columns=["review_id", "group", "n_valid", "n_needed"])

    covered = set(reused["store_id"])

    def _n(g):
        return int(all_new.loc[in_group(all_new["groups"], g), "store_id"].isin(covered).sum())

    blocked_by_group = {g: int(((need["group"] == g) & (need["n_valid"] == 0)).sum()) for g in GROUPS}
    stats = {"n_reused_items": len(reused), "n_stores_total": len(all_new), "n_stores_covered": len(covered),
             "n_stores_blocked": len(all_new) - len(covered),
             "covered_by_group": {g: _n(g) for g in GROUPS},
             "blocked_by_group": blocked_by_group,
             "n_store_group_needing_more": int((need["n_needed"] > 0).sum())}
    return reused, need, stats


def _sheet_rows(base: dict, posts: pd.DataFrame, start_no: int = 1) -> list[dict]:
    """post_date·blog_name·title·description을 판정표 형식으로 (build_sheet의 행 하나 만들기와 같은 규칙)."""
    return [{**base, "item_no": start_no + i, "post_date": _post_date(p.postdate),
            "blog_name": blog_name_from_link(p.link), "title": clean_text(p.title),
            "description": clean_text(p.description)[:DESC_LEN], "link": p.link, "verdict": "", "note": ""}
           for i, p in enumerate(posts.itertuples(index=False))]


def fill_from_raw(raw_items: pd.DataFrame, need: pd.DataFrame, new_key: pd.DataFrame, new_targets: pd.DataFrame,
                  bounds_by_group: dict[str, dict[str, tuple]] | None, exclude_links: dict[str, set[str]],
                  seed: int) -> tuple[pd.DataFrame, dict]:
    """#39 추가 리뷰(round2 --raw): need에서 n_needed>0인 점포×그룹마다 그 그룹 구간 안, 이미 판정한 글이
    아닌 원본 글로 최대 n_needed건을 seed 고정 무작위로 뽑는다. 한 점포가 여러 그룹에서 필요하면
    합집합(중복 없이)으로 합친다. 구간 안 글이 부족하면 있는 만큼.

    반환: (새 판정표(SHEET_COLS, verdict 빈칸), review_id별 실제로 채운 글 수 meta)
    """
    rid_to_sid = new_key.drop_duplicates("review_id").set_index("review_id")["store_id"]
    targets_by_rid = new_targets.set_index("review_id")
    rng = np.random.default_rng(seed)
    picked: dict[str, pd.DataFrame] = {}
    for row in need.sort_values(["review_id", "group"]).itertuples(index=False):
        if row.n_needed <= 0:
            continue
        sid = rid_to_sid.get(row.review_id)
        if sid is None:
            continue
        already = set(exclude_links.get(sid, ())) | set(picked.get(sid, pd.DataFrame({"link": []}))["link"])
        cand = raw_items[(raw_items["store_id"] == sid) & (~raw_items["link"].isin(already))]
        gb = bounds_by_group.get(sid, {}).get(row.group) if bounds_by_group is not None else None
        if gb is not None:
            lo, hi = gb
            d = pd.to_datetime(cand["postdate"], format="%Y%m%d", errors="coerce")
            cand = cand[d.notna() & (lo is None or d >= lo) & (hi is None or d <= hi)]
        cand = cand.sort_values("link")
        k = min(row.n_needed, len(cand))
        pick = cand.iloc[np.sort(rng.choice(len(cand), k, replace=False))] if k else cand.iloc[:0]
        picked[sid] = pd.concat([picked.get(sid, cand.iloc[:0]), pick]).drop_duplicates("link")

    rows, actual = [], {}
    for rid, sid in rid_to_sid.items():
        need_any = need.loc[need["review_id"] == rid, "n_needed"]
        if not (need_any > 0).any():
            continue
        posts = picked.get(sid, raw_items.iloc[:0])
        actual[rid] = len(posts)
        t = targets_by_rid.loc[rid]
        base = {"review_id": rid, "name_raw": t["name_raw"], "gu": t["gu"], "dong": t["dong"],
               "biz_type": t["biz_type"]}
        rows += _sheet_rows(base, posts)
    sheet = pd.DataFrame(rows, columns=SHEET_COLS)
    return sheet, actual


MANIFEST_FILE = "name_match_round2_manifest.csv"  # round2 --out에 저장, merge --manifest 기본값 (분석 쪽 보관)


def _bucket_labels(store_ids: pd.Series, key: pd.DataFrame) -> pd.Series:
    """제외 건수 집계용 그룹 표시 — "random", "short_name:폐업", "random;short_name:영업" (점포 식별 정보 없음)."""
    k = key.drop_duplicates("store_id").set_index("store_id")
    groups = store_ids.map(k["groups"]).fillna("")
    stratum = store_ids.map(k["stratum"]).fillna("")
    return groups + np.where(stratum != "", ":" + stratum, "")


def _excluded_by_bucket(store_ids: pd.Series, dates: pd.Series, bounds_by_group, key: pd.DataFrame) -> dict:
    """날짜 필터(그룹 구간의 합집합) 밖으로 빠진 글 수 — 전체·그룹별."""
    keep = _within_union(dates, store_ids, bounds_by_group)
    labels = _bucket_labels(store_ids, key)
    return {"items_before": int(len(keep)), "items_excluded": int((~keep).sum()),
            "items_excluded_by_group": {str(g): int(n) for g, n in labels[~keep.to_numpy()].value_counts().sort_index().items()}}


def groups_in_window(review_ids: pd.Series, post_dates: pd.Series, new_key: pd.DataFrame,
                     bounds_by_group: dict[str, dict[str, tuple]]) -> pd.Series:
    """글마다 그 날짜가 자기 구간 안에 드는 그룹(";"로 연결). 겹침 점포는 한 그룹에만 들 수 있다.
    merge가 점포×그룹 단위 '매칭 없음'을 셀 때 쓴다 (summarize의 그룹별 재필터와 같은 규칙)."""
    rid_map = new_key.drop_duplicates("review_id").set_index("review_id")
    dates = pd.to_datetime(post_dates, format="%Y-%m-%d", errors="coerce")
    out = []
    for rid, d in zip(review_ids, dates):
        sid, groups = rid_map.at[rid, "store_id"], str(rid_map.at[rid, "groups"]).split(GROUP_SEP)
        ok = []
        for g in groups:
            lo, hi = bounds_by_group.get(sid, {}).get(g, (None, None))
            if pd.notna(d) and (lo is None or d >= lo) and (hi is None or d <= hi):
                ok.append(g)
        out.append(GROUP_SEP.join(ok))
    return pd.Series(out, index=review_ids.index, dtype=object)


def _no_match_store_groups(manifest: pd.DataFrame, new_key: pd.DataFrame) -> list[tuple[str, str]]:
    """이번 회차 key의 점포×그룹 중 그 그룹 구간 안 글이 하나도 없는 것 [(review_id, group)].
    manifest는 review_id, groups_in_window 열을 가진다 (round2가 만든 것, 또는 merge 결과에 붙인 것)."""
    covered: set[tuple[str, str]] = set()
    for rid, gs in zip(manifest["review_id"], manifest["groups_in_window"].fillna("")):
        covered.update((rid, g) for g in str(gs).split(GROUP_SEP) if g)
    return [(r.review_id, g) for r in new_key.drop_duplicates("review_id").itertuples(index=False)
            for g in str(r.groups).split(GROUP_SEP) if (r.review_id, g) not in covered]


def _count_by_group(pairs: list[tuple[str, str]], new_key: pd.DataFrame) -> dict[str, int]:
    """(review_id, group) 목록을 그룹(short_name은 층까지)별 건수로 — 점포 식별 정보 없음."""
    stratum = new_key.drop_duplicates("review_id").set_index("review_id")["stratum"]
    labels = [g + (f":{stratum.get(rid, '')}" if g == SHORT and stratum.get(rid, "") else "") for rid, g in pairs]
    return {k: int(v) for k, v in sorted(pd.Series(labels, dtype=object).value_counts().items())}


def cmd_round2(a) -> dict:
    # 날짜 필터(M1)는 선택이 아니다 — 빠지면 재사용·새 추출이 조용히 달라진다(실데이터 56건·22곳 → 73건·25곳).
    missing = [f for f, v in (("--licenses", a.licenses), ("--master", a.master)) if not v]
    if missing:
        raise ValueError(f"round2에는 날짜 필터 입력 {missing}이 필요하다 (short_name 인허가일·폐업일·master 구간)")
    old_sheet = pd.read_csv(a.old_sheet, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    old_key = pd.read_csv(a.old_key, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    new_targets = pd.read_csv(a.new_targets, dtype=str, encoding="utf-8-sig")
    new_key = pd.read_csv(a.new_key, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    key_meta = json.loads(key_meta_path(a.new_key).read_text(encoding="utf-8")) \
        if key_meta_path(a.new_key).exists() else {}
    if key_meta:
        check_key_meta_pair(key_meta, a.new_targets, a.new_key)
    lic = pd.read_parquet(a.licenses, columns=["store_id", "license_date", "close_date"])
    master_span = master_base_span(pd.read_parquet(a.master, columns=["store_id", "origin_start", "origin_end"]))
    # --serve-as-of가 없으면 새 회차 key의 메타(name_match_key_meta.json, cmd_targets가 diagnosis 옆
    # serve_meta.json에서 읽어 둔 값)에서 가져온다 — targets.csv 옆에는 serve_meta.json이 없다.
    serve_as_of = a.serve_as_of or key_meta.get("serve_as_of")
    if not serve_as_of:
        raise ValueError("priority/random 날짜 상한(serve_as_of)이 없다 — --serve-as-of를 주거나 key 메타에 serve_as_of가 있어야 한다")
    bounds_by_group = date_bounds_by_group(lic, new_key, master_span=master_span, serve_as_of=serve_as_of)

    # 옛 판정 중 날짜 필터로 빠지는 글 (이번 대상 점포, 판정된 글만)
    old_all = old_sheet.merge(old_key[["review_id", "store_id"]], on="review_id", how="left")
    old_all = old_all[pd.to_numeric(old_all["item_no"], errors="coerce").fillna(0) > 0]
    old_new = old_all[old_all["store_id"].isin(set(new_key["store_id"])) & (old_all["verdict"] != "")]
    date_filter = {"applied": True, "serve_as_of": str(serve_as_of), "master_span": True,
                   "old_verdicts": _excluded_by_bucket(old_new["store_id"].reset_index(drop=True),
                                                       pd.to_datetime(old_new["post_date"].reset_index(drop=True),
                                                                      format="%Y-%m-%d", errors="coerce"),
                                                       bounds_by_group, new_key)}
    reused, need, stats = carry_over(old_sheet, old_key, new_targets, new_key, bounds_by_group)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.raw:
        # 이미 판정한 글(재사용 가능 여부와 무관하게 옛 판정표 전체)은 다시 후보로 뽑지 않는다.
        exclude_links: dict[str, set[str]] = {sid: set(g["link"]) for sid, g in old_all.groupby("store_id")}
        raw_items = read_items(a.raw, set(new_key["store_id"]))
        date_filter["raw_items"] = _excluded_by_bucket(
            raw_items["store_id"], pd.to_datetime(raw_items["postdate"].astype(str), format="%Y%m%d", errors="coerce"),
            bounds_by_group, new_key)
        sheet2, actual = fill_from_raw(raw_items, need, new_key, new_targets, bounds_by_group, exclude_links, a.seed)
        rid_needed = sorted(set(need.loc[need["n_needed"] > 0, "review_id"]))
        targets2 = new_targets[new_targets["review_id"].isin(rid_needed)][TARGET_COLS].reset_index(drop=True)
        n_short = int(sum(1 for rid, n in actual.items()
                         if n < int(need.loc[need["review_id"] == rid, "n_needed"].max())))
        note = f"원본에서 {len(actual)}개 점포에 새 글을 채웠다 (부족(구간 안 글 모자람) {n_short}개 점포 — meta 참고)."
    else:
        rid_needed = sorted(set(need.loc[need["n_needed"] > 0, "review_id"]))
        targets2 = new_targets[new_targets["review_id"].isin(rid_needed)][TARGET_COLS].reset_index(drop=True)
        # --raw 없이는 새로 뽑을 수 없다 — 판정표는 지금 0행이다. NO_POSTS(매칭 글 없음) sentinel은 쓰지
        # 않는다: "매칭 0건"이 아니라 "원본에 접근할 수 없어 모른다"이기 때문이다.
        sheet2 = pd.DataFrame(columns=SHEET_COLS)
        actual = {}
        note = "원본 blog_items.jsonl.gz 없이는 새 글을 뽑을 수 없다 — 재사용 가능한 기존 판정만 반영했다."
    assert_blind(targets2.columns)
    assert_blind(sheet2.columns)

    targets2.to_csv(out / "name_match_targets_round2.csv", index=False, encoding="utf-8-sig")
    sheet2.to_csv(out / "name_match_sheet_round2.csv", index=False, encoding="utf-8-sig")
    # merge --reused의 기본 입력. store_id가 있으니 분석 쪽에만 둔다(판정자에게 전달하지 않는다).
    reused.to_csv(out / REUSED_FILE, index=False, encoding="utf-8-sig")
    # merge가 판정본을 검증할 기준: 이번 회차 판정 대상 글 전체(재사용 + 새 판정표)와 글마다 날짜가 드는 그룹.
    # 선정 그룹이 있으므로 분석 쪽에만 둔다.
    manifest = pd.concat([reused[["review_id", "link", "post_date"]].assign(source="reused"),
                          sheet2[["review_id", "link", "post_date"]].assign(source="new")], ignore_index=True)
    manifest["groups_in_window"] = groups_in_window(manifest["review_id"], manifest["post_date"], new_key,
                                                    bounds_by_group) if len(manifest) else pd.Series(dtype=object)
    manifest.to_csv(out / MANIFEST_FILE, index=False, encoding="utf-8-sig")
    no_match = _no_match_store_groups(manifest, new_key)
    report = {**stats, "n_new_items_found": int(sum(actual.values())), "n_stores_pending_raw_access": len(rid_needed),
             "n_new_sheet_rows": int(len(sheet2)), "n_new_sheet_stores": int(sheet2["review_id"].nunique()),
             "n_no_match_store_groups": len(no_match), "no_match_by_group": _count_by_group(no_match, new_key),
             "date_filter": date_filter, "actual_new_items_by_review_id": actual, "note": note}
    (out / "round2_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"재사용 {stats['n_reused_items']}건 (점포 {stats['n_stores_covered']}/{stats['n_stores_total']}) · "
          f"새로 찾은 글 {report['n_new_items_found']}건 · 대기(부족) 점포×그룹 {stats['n_store_group_needing_more']}건")
    print(f"  그룹별 재사용 점포: {stats['covered_by_group']} / 전부 결측 점포×그룹: {stats['blocked_by_group']}")
    print(f"  판정자에게 보낼 판정표 {len(sheet2)}줄 ({report['n_new_sheet_stores']}개 점포) → {out / 'name_match_sheet_round2.csv'}")
    print(f"  구간 안 글이 없는 점포×그룹(매칭 없음) {len(no_match)}건: {report['no_match_by_group']}")
    print(f"  날짜 필터: 옛 판정 {date_filter['old_verdicts']['items_excluded']}건 제외"
          + (f", 원본 {date_filter['raw_items']['items_excluded']}건 구간 밖" if "raw_items" in date_filter else ""))
    return {"reused": reused, "need": need, "targets2": targets2, "sheet2": sheet2, "manifest": manifest,
            "report": report}


# ---------------------------------------------------------------------------- merge (재사용 + 새 판정 합치기)
def merge_judgments(reused: pd.DataFrame, new_sheet: pd.DataFrame, new_key: pd.DataFrame,
                    old_dates: pd.DataFrame | None = None, manifest: pd.DataFrame | None = None
                    ) -> tuple[pd.DataFrame, dict]:
    """재사용 판정 파일 + 새 판정표(판정 완료본)를 이번 회차 key 기준으로 합친다. 키는 (store, link) —
    review_id는 이번 회차 key로 다시 매긴다(재사용 파일의 review_id는 지난 회차 것일 수 있어 믿지 않는다).
    같은 (store, link)가 양쪽에 있으면 새 판정을 우선한다(사람이 다시 본 것이 더 최신).

    판정 누락과 '매칭 없음'을 구분한다 — 멈추는 경우:
    - 새 판정표에 이번 회차 key에 없는 review_id가 있다.
    - verdict 미입력(빈 칸)이 있다 (summarize까지 가서 judge()가 늦게 잡지 않도록 여기서 먼저 막는다).
    - manifest(round2가 만든 판정 대상 목록)가 있으면: round2 판정표의 (review_id, link)가 판정본에서 빠졌거나,
      판정본에 round2가 만들지 않은 글이 있거나, 재사용 파일이 round2 때와 다르다.

    이번 회차 key의 점포×그룹 중 그 그룹 구간 안 글이 하나도 없는 것('매칭 없음' — 날짜 필터로 글이 모두 빠진
    경우)은 에러 대신 건너뛰고 meta에 점포×그룹 단위로 남긴다. 겹침 점포는 한 그룹만 매칭 없음일 수 있다
    (manifest의 groups_in_window로 센다). 이 점포는 key·targets에 그대로 있으므로 summarize가
    `--separate-no-match`에서 '매칭 없음'으로 센다. manifest가 없으면 그룹 구간을 모르므로 점포 단위(글 0건인
    점포의 모든 그룹)로만 센다 — CLI merge는 manifest를 요구한다.

    출력은 summarize 입력 형식이다: review_id, item_no(점포별 1부터), post_date, link, verdict, note.
    post_date는 새 판정표의 열, 재사용 파일의 열을 쓰고, 재사용 파일에 없으면 old_dates(store_id, link,
    post_date — 지난 회차 판정표+key)로 채운다. 끝내 못 채운 글이 있으면 에러(날짜 없이는 그룹별 구간 재집계가 안 된다).
    """
    new_map = new_key.drop_duplicates("store_id").set_index("store_id")["review_id"]
    key_cols = ["store_id", "link"]
    # 재사용 파일의 review_id는 지난 회차 것일 수 있으니 store_id로 이번 회차 review_id를 다시 매긴다.
    reused = reused[reused["store_id"].isin(new_map.index)].drop_duplicates(key_cols).copy()
    reused["review_id"] = reused["store_id"].map(new_map)
    blank_r = reused["verdict"].fillna("").astype(str).str.strip() == ""
    if blank_r.any():
        raise ValueError(f"재사용 판정 파일에 verdict 미입력 글 {int(blank_r.sum())}건")
    if "post_date" not in reused:
        reused["post_date"] = ""
    if old_dates is not None and len(reused):
        lookup = old_dates.drop_duplicates(key_cols).set_index(key_cols)["post_date"]
        need = reused["post_date"].fillna("").astype(str).str.strip() == ""
        reused.loc[need, "post_date"] = [lookup.get((s, l), "") for s, l in
                                        zip(reused.loc[need, "store_id"], reused.loc[need, "link"])]

    new_sheet = new_sheet.copy()
    new_sheet = new_sheet[pd.to_numeric(new_sheet["item_no"], errors="coerce").fillna(0) > 0]
    if "post_date" not in new_sheet:
        new_sheet["post_date"] = ""
    unknown = sorted(set(new_sheet["review_id"].fillna("").astype(str)) - set(new_key["review_id"]))
    if unknown:
        raise ValueError(f"새 판정표에 이번 회차 key에 없는 review_id {len(unknown)}개: {unknown[:10]} — "
                         "다른 회차 판정표이거나 review_id가 바뀌었다")
    new_sheet = new_sheet.merge(new_key[["review_id", "store_id"]].drop_duplicates("review_id"),
                                on="review_id", how="left").drop_duplicates(key_cols)
    blank = new_sheet["verdict"].fillna("").astype(str).str.strip() == ""
    if blank.any():
        raise ValueError(f"새 판정표에 verdict 미입력 글 {int(blank.sum())}건 — 모두 채운 뒤 다시 시도한다")
    if manifest is not None:
        pairs = lambda df: set(zip(df["review_id"].astype(str), df["link"].astype(str)))  # noqa: E731
        expected_new = pairs(manifest[manifest["source"] == "new"])
        got_new = pairs(new_sheet)
        missing, extra = sorted(expected_new - got_new), sorted(got_new - expected_new)
        if missing:
            raise ValueError(f"판정본에서 빠진 글 {len(missing)}건 (review_id {sorted({r for r, _ in missing})[:10]}) — "
                             "round2 판정표의 행을 지우지 않고 모두 판정해야 한다 (판단할 수 없으면 '판단불가')")
        if extra:
            raise ValueError(f"round2 판정표에 없던 글 {len(extra)}건이 판정본에 있다 (review_id "
                             f"{sorted({r for r, _ in extra})[:10]}) — link·review_id를 고치지 않는다")
        if pairs(manifest[manifest["source"] == "reused"]) != pairs(reused):
            raise ValueError("재사용 판정 파일이 round2 manifest와 다르다 — 같은 round2 --out 폴더의 두 파일을 쓴다")

    conflict = set(map(tuple, reused[key_cols].to_numpy())) & set(map(tuple, new_sheet[key_cols].to_numpy()))
    n_conflict = len(conflict)
    kept_reused = reused[~reused[key_cols].apply(tuple, axis=1).isin(conflict)]
    cols = ["review_id", "store_id", "link", "post_date", "verdict", "note"]
    merged = pd.concat([kept_reused[cols], new_sheet[cols]], ignore_index=True)
    merged = merged.drop_duplicates(key_cols, keep="last").reset_index(drop=True)
    no_date = merged["post_date"].fillna("").astype(str).str.strip() == ""
    if no_date.any():
        raise ValueError(f"post_date를 못 채운 글 {int(no_date.sum())}건 — 재사용 파일에 post_date가 없으면 "
                         "--old-sheet/--old-key(지난 회차 판정표·key)를 함께 준다")

    all_new = new_key.drop_duplicates("store_id")
    if manifest is not None:
        # 판정본이 manifest와 (review_id, link) 단위로 같음을 위에서 확인했으므로 manifest의 그룹별 구간으로 센다
        no_match, basis = _no_match_store_groups(manifest, new_key), "store_group_window"
    else:
        counts = merged.groupby("store_id").size()
        no_match = [(r.review_id, g) for r in all_new.itertuples(index=False) for g in str(r.groups).split(GROUP_SEP)
                    if counts.get(r.store_id, 0) == 0]
        basis = "store (manifest 없음 — 그룹별 구간 미확인)"
    merged["item_no"] = merged.groupby("review_id").cumcount() + 1

    n_by_store = merged.groupby("store_id").size()
    dist = n_by_store.value_counts().reindex([1, 2, 3], fill_value=0).astype(int).to_dict()
    meta = {"n_reused_kept": len(kept_reused), "n_new": len(new_sheet), "n_conflict": n_conflict,
           "n_merged_items": len(merged), "n_stores": len(all_new), "items_per_store_distribution": dist,
           "n_no_match_store_groups": len(no_match), "no_match_basis": basis,
           "no_match_store_groups": [{"review_id": r, "group": g} for r, g in no_match],
           "no_match_by_group": _count_by_group(no_match, new_key),
           "no_match_review_ids": sorted({rid for rid, _ in no_match})}
    return merged[["review_id", "item_no", "post_date", "link", "verdict", "note"]], meta


def cmd_merge(a) -> pd.DataFrame:
    reused_path = a.reused or Path(a.new_sheet).parent / REUSED_FILE  # 기본: 새 판정표와 같은 폴더의 round2 재사용 파일
    if not Path(reused_path).exists():
        raise FileNotFoundError(f"재사용 판정 파일이 없다: {reused_path} — round2 --out 폴더에 있는 {REUSED_FILE}을 --reused로 준다")
    reused = pd.read_csv(reused_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    # 판정 누락 검증 기준 — round2가 재사용 파일과 같은 폴더에 만든다. 없으면 누락을 확인할 수 없으므로 멈춘다.
    manifest_path = getattr(a, "manifest", None) or Path(reused_path).parent / MANIFEST_FILE
    if not Path(manifest_path).exists():
        raise FileNotFoundError(f"round2 manifest가 없다: {manifest_path} — round2 --out 폴더의 {MANIFEST_FILE}을 "
                                "--manifest로 준다 (판정본 누락·추가 행을 확인하는 기준)")
    manifest = pd.read_csv(manifest_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    new_sheet = pd.read_csv(a.new_sheet, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    new_key = pd.read_csv(a.new_key, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    old_dates = None
    if getattr(a, "old_sheet", None) and getattr(a, "old_key", None):
        old_sheet = pd.read_csv(a.old_sheet, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        old_key = pd.read_csv(a.old_key, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        old_dates = old_sheet.merge(old_key[["review_id", "store_id"]], on="review_id", how="left")[
            ["store_id", "link", "post_date"]]
    merged, meta = merge_judgments(reused, new_sheet, new_key, old_dates, manifest)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out, index=False, encoding="utf-8-sig")
    (out.with_name(f"{out.stem}_meta.json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                                        encoding="utf-8")
    print(f"merge: 재사용 유지 {meta['n_reused_kept']} + 새 판정 {meta['n_new']} (충돌 {meta['n_conflict']}, "
          f"새 판정 우선) → {meta['n_merged_items']}건, {meta['n_stores']}개 점포 · 글 수 분포 "
          f"{meta['items_per_store_distribution']} → {out}")
    if meta["n_no_match_store_groups"]:
        print(f"경고: 유효 글 0건 점포×그룹 {meta['n_no_match_store_groups']}건은 건너뛴다 ({meta['no_match_by_group']}, "
              f"review_id {meta['no_match_review_ids']}) — summarize --separate-no-match에서 '매칭 없음'으로 센다")
    return merged


# ---------------------------------------------------------------------------- CLI
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="#28 상호 매칭 검수 보조 (blind 판정표)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("targets", help="검수 대상 목록 (분석 쪽)")
    t.add_argument("--diagnosis", type=Path, required=True)
    t.add_argument("--risk", type=Path, default=None, help="예측 확률 파일 (기본: diagnosis와 같은 폴더)")
    t.add_argument("--licenses", type=Path, required=True)
    t.add_argument("--mentions", type=Path, default=None,
                   help="online_mentions_monthly.parquet (짧은 상호 그룹 모집단: 블로그 언급 1건 이상). 없으면 그룹 생략")
    t.add_argument("--master", type=Path, default=None,
                   help="master_base.parquet (M2: 짧은 상호 모집단을 모형 대상 점포로 한정). 없으면 미제한")
    t.add_argument("--n-short-per-stratum", type=int, default=50, help="짧은 상호 층(영업/폐업)별 대상 수")
    t.add_argument("--n-random", type=int, default=20)
    t.add_argument("--n-priority", type=int, default=3)
    t.add_argument("--seed", type=int, default=20260927)
    t.add_argument("--out", type=Path, required=True, help="대상 목록 (전달용)")
    t.add_argument("--key-out", type=Path, default=None, help="선정 그룹 키 (기본: --out과 같은 폴더 name_match_key.csv)")
    s = sub.add_parser("sheet", help="판정표 (원본 블로그 글이 있는 컴퓨터)")
    s.add_argument("--targets", type=Path, required=True)
    s.add_argument("--items", type=Path, nargs="+", required=True, help="blog_items*.jsonl.gz (여러 개 가능)")
    s.add_argument("--per-store", type=int, default=3)
    s.add_argument("--seed", type=int, default=20260927)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--key", type=Path, default=None,
                   help="M1 날짜 필터용 선정 그룹 키 (groups·stratum 열). 없으면 필터 생략")
    s.add_argument("--licenses", type=Path, default=None, help="M1 필터용 (license_date/close_date)")
    s.add_argument("--master", type=Path, default=None, help="M1 필터용 master_base (짧은 상호 등장 구간)")
    s.add_argument("--serve-as-of", default=None,
                   help="M1 상한(YYYY-MM-DD). 기본: --targets 옆 serve_meta.json에서 읽음")
    m = sub.add_parser("summarize", help="판정 결과 집계 (분석 쪽) — merge 결과만 입력으로 받는다")
    m.add_argument("--targets", type=Path, required=True)
    m.add_argument("--sheet", type=Path, required=True, help="merge 서브커맨드의 출력(재사용+새 판정 합친 것)")
    m.add_argument("--key", type=Path, default=None, help="선정 그룹 키 (targets가 만든 name_match_key.csv)")
    m.add_argument("--key-meta", type=Path, default=None,
                   help="선정 그룹 키 메타 (기본: --key 옆 name_match_key_meta.json, short_name 가중치)")
    m.add_argument("--licenses", type=Path, default=None,
                   help="겹침 점포 그룹별 재집계용 (없으면 그룹별 날짜 재필터를 생략 — extraction 시점 합집합 그대로 집계)")
    m.add_argument("--master", type=Path, default=None)
    m.add_argument("--serve-as-of", default=None)
    m.add_argument("--separate-no-match", action="store_true",
                   help="유효 글 0건인 점포×그룹을 '매칭 없음'으로 따로 집계 (기본 꺼짐 — 최홍준 님 답 전까지 기본값 불변)")
    m.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("round2", help="회차 간 판정 재사용 — 부족분은 --raw로 새로 뽑는다 (분석 쪽)")
    r.add_argument("--old-sheet", type=Path, required=True)
    r.add_argument("--old-key", type=Path, required=True)
    r.add_argument("--new-targets", type=Path, required=True)
    r.add_argument("--new-key", type=Path, required=True)
    r.add_argument("--licenses", type=Path, required=True,
                   help="M1 날짜 필터 (short_name 인허가일·폐업일) — 필수: 빠지면 재사용·새 추출이 조용히 달라진다")
    r.add_argument("--master", type=Path, required=True, help="M1 날짜 필터 (short_name master_base 등장 구간) — 필수")
    r.add_argument("--serve-as-of", default=None)
    r.add_argument("--raw", type=Path, nargs="+", default=None,
                   help="blog_items*.jsonl.gz — 있으면 점포×그룹의 유효 판정 글이 3건 미만일 때 새로 채운다")
    r.add_argument("--seed", type=int, default=20260927)
    r.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("merge", help="재사용 판정 + 새 판정표(판정 완료본)를 합친다 (분석 쪽)")
    g.add_argument("--reused", type=Path, default=None,
                   help=f"round2가 만든 재사용 판정 (기본: --new-sheet와 같은 폴더의 {REUSED_FILE})")
    g.add_argument("--manifest", type=Path, default=None,
                   help=f"round2가 만든 판정 대상 목록 (기본: --reused와 같은 폴더의 {MANIFEST_FILE}, 없으면 멈춤)")
    g.add_argument("--new-sheet", type=Path, required=True, help="round2 sheet를 사람이 판정까지 채운 파일")
    g.add_argument("--new-key", type=Path, required=True, help="이번 회차 선정 그룹 키")
    g.add_argument("--old-sheet", type=Path, default=None,
                   help="재사용 파일에 post_date가 없을 때(이전 round2 산출물) 날짜를 채울 지난 회차 판정표")
    g.add_argument("--old-key", type=Path, default=None, help="--old-sheet의 지난 회차 key (store_id 연결용)")
    g.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    {"targets": cmd_targets, "sheet": cmd_sheet, "summarize": cmd_summarize, "round2": cmd_round2,
     "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
