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
    python -m src.analysis.name_match_review targets --diagnosis outputs/serve/_trial/out/diagnosis.parquet \\
        --licenses outputs/standardized/licenses_3gu.parquet --mentions outputs/online/online_mentions_monthly.parquet \\
        --out outputs/review/name_match_targets.csv
    python -m src.analysis.name_match_review sheet --targets name_match_targets.csv \\
        --items data/raw/online/blog_items.jsonl.gz --out name_match_sheet.csv
    python -m src.analysis.name_match_review summarize --targets outputs/review/name_match_targets.csv \\
        --key outputs/review/name_match_key.csv --sheet name_match_sheet.csv --out outputs/review/
"""
from __future__ import annotations

import argparse
import gzip
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


def short_name_pool(licenses: pd.DataFrame, mention_totals: pd.Series) -> pd.DataFrame:
    """짧은 상호이면서 블로그 언급이 1건 이상인 점포 (언급이 없으면 판정할 글이 없다). 층(영업/폐업) 포함."""
    lic = licenses[licenses["name_raw"].map(is_short_name)]
    lic = lic[mention_totals.reindex(lic["store_id"]).fillna(0).to_numpy() >= 1]
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
    """온라인 요인이 검토 대기(display=false, data_missing=false)인 점포."""
    d = diagnosis[diagnosis["factor_id"] == "online_attention"]
    held = d[(~d["display"].astype(bool)) & (~d["data_missing"].astype(bool))]
    return held[["store_id", "gu", "biz_type"]].drop_duplicates("store_id").reset_index(drop=True)


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


def cmd_targets(a) -> pd.DataFrame:
    diagnosis = pd.read_parquet(a.diagnosis, columns=["store_id", "gu", "biz_type", "factor_id", "display",
                                                      "data_missing"])
    pool = review_pool(diagnosis)
    prob = _probabilities(Path(a.diagnosis), a.risk)
    lic = pd.read_parquet(a.licenses, columns=["store_id", "name_raw", "name_norm", "dong"]
                          + (["gu", "business_type", "status_code"] if a.mentions else []))
    short_pool = None
    if a.mentions:
        short_pool = short_name_pool(lic, blog_mention_totals(pd.read_parquet(
            a.mentions, columns=["store_id", "platform", "mention_count"])))
        n_s = short_pool["stratum"].value_counts()
        print(f"짧은 상호(정규화 후 {SHORT_NAME_MAX}자 이하)·블로그 언급 1건 이상 {len(short_pool):,}점포 "
              f"(영업 {int(n_s.get(OPEN, 0)):,}, 폐업 {int(n_s.get(CLOSED, 0)):,})")
    else:
        print("--mentions가 없어 짧은 상호(short_name) 그룹은 뽑지 않는다")
    out, key = select_targets(pool, prob, lic, n_priority=a.n_priority, n_random=a.n_random, seed=a.seed,
                              short_pool=short_pool, n_short_per_stratum=a.n_short_per_stratum)
    key_out = a.key_out or Path(a.out).parent / "name_match_key.csv"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(key_out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False, encoding="utf-8-sig")
    key.to_csv(key_out, index=False, encoding="utf-8-sig")
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
    sheet = build_sheet(targets, items, a.per_store, a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(out, index=False, encoding="utf-8-sig")
    (out.parent / f"{out.stem}_README.md").write_text(SHEET_README.format(sheet=out.name), encoding="utf-8")
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
    s = s[~blank]
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


def _counts(it: pd.DataFrame, st: pd.DataFrame) -> tuple[int, int, int, int]:
    return (int((it["verdict"] == OTHER).sum()), int(it["verdict"].isin([SAME, OTHER]).sum()),
            int((st["store_verdict"] == "오탐").sum()), int(st["store_verdict"].isin(["오탐", "정상"]).sum()))


def _has_ci(grp: str) -> bool:
    """무작위 표본에만 신뢰구간 (priority는 확률 상위라 모집단 추정에 쓰지 않는다)."""
    return grp.partition(":")[0] in ("random", SHORT)


def rates(items: pd.DataFrame, stores: pd.DataFrame) -> pd.DataFrame:
    out = []
    groups = [g for g in GROUPS if in_group(stores["groups"], g).any()]
    if SHORT in groups:
        i = groups.index(SHORT) + 1
        groups[i:i] = [f"{SHORT}:{s}" for s in (OPEN, CLOSED) if len(_rows_of(stores, f"{SHORT}:{s}"))]
    for grp in groups + ["전체"]:
        it, st = _rows_of(items, grp), _rows_of(stores, grp)
        ki, ni, ks, ns = _counts(it, st)
        lo_i, hi_i = wilson(ki, ni) if _has_ci(grp) else (float("nan"), float("nan"))
        lo_s, hi_s = wilson(ks, ns) if _has_ci(grp) else (float("nan"), float("nan"))
        out.append({"group": grp, "items_decided": ni, "items_other": ki, "item_fp_rate": ki / ni if ni else float("nan"),
                    "item_ci_low": lo_i, "item_ci_high": hi_i,
                    "stores_decided": ns, "stores_fp": ks, "store_fp_rate": ks / ns if ns else float("nan"),
                    "store_ci_low": lo_s, "store_ci_high": hi_s,
                    "stores_undecided": int((st["store_verdict"] == "판정불가").sum())})
    return pd.DataFrame(out)


def stratum_gap(items: pd.DataFrame, stores: pd.DataFrame) -> pd.DataFrame:
    """짧은 상호 그룹 안에서 폐업 − 영업 오탐률 차이 (점포·글 단위). 양수면 폐업 쪽 오탐이 더 높다."""
    (ki_o, ni_o, ks_o, ns_o), (ki_c, ni_c, ks_c, ns_c) = (
        _counts(_rows_of(items, f"{SHORT}:{s}"), _rows_of(stores, f"{SHORT}:{s}")) for s in (OPEN, CLOSED))
    out = []
    for level, (ko, no, kc, nc) in (("store", (ks_o, ns_o, ks_c, ns_c)), ("item", (ki_o, ni_o, ki_c, ni_c))):
        d, lo, hi = newcombe_diff(kc, nc, ko, no)
        out.append({"level": level, "open_fp": ko, "open_decided": no, "closed_fp": kc, "closed_decided": nc,
                    "open_rate": ko / no if no else float("nan"), "closed_rate": kc / nc if nc else float("nan"),
                    "diff_closed_minus_open": d, "diff_ci_low": lo, "diff_ci_high": hi})
    return pd.DataFrame(out)


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
    items, stores, n_blank = judge(sheet, targets, key)
    if n_blank:
        print(f"경고: verdict가 빈 글 {n_blank}건은 집계에서 뺐다")
    tab = rates(items, stores)
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
        lines.append(f"| {r.group} | {_pct(r.item_fp_rate)} ({r.items_other}/{r.items_decided}) | {ci_i} | "
                     f"{_pct(r.store_fp_rate)} ({r.stores_fp}/{r.stores_decided}) | {ci_s} | {r.stores_undecided} |")
    lines += ["", "- 신뢰구간(Wilson)은 무작위 표본(random, short_name)에만 붙인다. priority는 선정 기준이 달라 "
              "모집단 추정에 쓰지 않는다.",
              "- 두 그룹에 속한 점포는 두 그룹 모두에 센다. `전체`는 점포당 한 번.",
              "- `false_positive_stores.csv`(store_id)는 온라인 feature를 NA로 두는 재실행(#33·#34)에 쓴다. 저장소에 올리지 않는다."]
    if in_group(stores["groups"], SHORT).any():
        gap = stratum_gap(items, stores)
        gap.to_csv(out / "name_match_short_strata.csv", index=False, encoding="utf-8-sig")
        lines += ["", "## 짧은 상호: 폐업 − 영업 오탐률 차이", "",
                  "| 단위 | 영업 | 폐업 | 차이 (폐업−영업) | 95% CI (Newcombe) |", "|---|---|---|---|---|"]
        for r in gap.itertuples(index=False):
            lines.append(f"| {'점포' if r.level == 'store' else '글'} | {_pct(r.open_rate)} ({r.open_fp}/{r.open_decided})"
                         f" | {_pct(r.closed_rate)} ({r.closed_fp}/{r.closed_decided}) | {_pp(r.diff_closed_minus_open)}"
                         f" | {_pp(r.diff_ci_low)} – {_pp(r.diff_ci_high)} |")
        lines += ["", "- 차이 CI 하한이 0보다 크면 폐업 쪽 오탐이 더 높다 → 온라인 feature에 라벨과 상관된 노이즈가 있다.",
                  "- short_name 합계 행은 영업·폐업을 같은 수로 뽑은 표본 기준이라 짧은 상호 모집단 오탐률이 아니다 (층별로 본다).",
                  "- 글 단위 CI는 같은 점포 글끼리의 상관을 무시해 좁게 나온다. 판단은 점포 단위를 기준으로 한다."]
    (out / "name_match_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return tab


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
    t.add_argument("--n-short-per-stratum", type=int, default=50, help="짧은 상호 층(영업/폐업)별 대상 수")
    t.add_argument("--n-random", type=int, default=20)
    t.add_argument("--n-priority", type=int, default=3)
    t.add_argument("--seed", type=int, default=20260927)
    t.add_argument("--out", type=Path, required=True, help="대상 목록 (전달용)")
    t.add_argument("--key-out", type=Path, default=None, help="선정 그룹 키 (기본: --out과 같은 폴더 name_match_key.csv)")
    s = sub.add_parser("sheet", help="판정표 (원본 블로그 글이 있는 컴퓨터)")
    s.add_argument("--targets", type=Path, required=True)
    s.add_argument("--items", type=Path, nargs="+", required=True, help="blog_items*.jsonl.gz (여러 개 가능)")
    s.add_argument("--per-store", type=int, default=5)
    s.add_argument("--seed", type=int, default=20260927)
    s.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("summarize", help="판정 결과 집계 (분석 쪽)")
    m.add_argument("--targets", type=Path, required=True)
    m.add_argument("--sheet", type=Path, required=True)
    m.add_argument("--key", type=Path, default=None, help="선정 그룹 키 (targets가 만든 name_match_key.csv)")
    m.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    {"targets": cmd_targets, "sheet": cmd_sheet, "summarize": cmd_summarize}[a.cmd](a)


if __name__ == "__main__":
    main()
