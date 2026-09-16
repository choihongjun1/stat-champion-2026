# -*- coding: utf-8 -*-
"""지번주소 파싱과 PNU(19자리) 조립.

- 법정동명 -> 법정동코드(10자리) 매핑은 소진공 상가정보에서 구축한다.
  법정동코드는 시간 불변 행정 참조정보이므로 스냅샷 union 사용이
  시간 누수 규칙(소진공 union은 entity resolution 전용)에 저촉되지 않는다.
  (feature 계산이 아니라 주소 문자열의 코드 변환일 뿐이다.)
- PNU = 법정동코드(10) + 대지구분(1: 대지=1, 산=2) + 본번(4) + 부번(4).
  이 공식은 소진공 지번코드 전수(7개 스냅샷)와 100% 일치함이 audit에서 검증됨.
"""
from __future__ import annotations

import glob
import re
from dataclasses import dataclass

import pandas as pd

from src.data.config import SEMAS_DIR, TARGET_GUS

# 지번주소 파싱 상태 코드
STATUS_OK = "ok"
STATUS_ADDR_MISSING = "addr_missing"          # 지번주소 없음
STATUS_NOT_SEOUL = "addr_not_seoul"           # 서울특별시로 시작하지 않음
STATUS_GU_UNPARSED = "gu_unparsed"            # 구 추출 실패
STATUS_DONG_UNMATCHED = "dong_unmatched"      # 법정동명 매핑 실패
STATUS_BUNJI_MISSING = "bunji_missing"        # 본번 없음 (동까지만 있는 주소 등)

# 본번(-부번): 숫자 뒤에 숫자/하이픈이 바로 이어지지 않아야 함 ("번지" 접미는 허용)
_BUNJI_RE = re.compile(r"^\s*(산)?\s*(\d{1,4})(?:-(\d{1,4}))?(?![\d-])")
_GU_RE = re.compile(r"^서울특별시\s+(\S+?구)\s*")


@dataclass
class ParsedAddress:
    status: str
    gu: str | None = None          # 주소에서 추출한 구
    dong: str | None = None        # 법정동명
    san: bool | None = None        # 산 여부
    bunji_main: int | None = None  # 본번
    bunji_sub: int | None = None   # 부번 (없으면 0)
    bjd_code: str | None = None    # 법정동코드 10자리
    pnu: str | None = None         # PNU 19자리


def build_bjd_mapping(semas_dir=SEMAS_DIR) -> dict[tuple[str, str], str]:
    """소진공 전 스냅샷에서 (시군구명, 법정동명) -> 법정동코드(10자리) 매핑을 구축한다.

    서울 전체를 대상으로 구축하되, 하나의 (구, 동)이 복수 코드에 대응하면
    대상 3구에서는 오류로 중단하고, 그 외 구에서는 해당 키를 제외한다.
    """
    frames = []
    for f in sorted(glob.glob(str(semas_dir / "*.csv"))):
        df = pd.read_csv(f, dtype=str, usecols=["시군구명", "법정동명", "법정동코드"])
        frames.append(df.drop_duplicates())
    mp = pd.concat(frames).drop_duplicates().dropna()

    nuniq = mp.groupby(["시군구명", "법정동명"])["법정동코드"].nunique()
    ambiguous = nuniq[nuniq > 1]
    amb_target = [k for k in ambiguous.index if k[0] in TARGET_GUS]
    if amb_target:
        raise ValueError(f"대상 3구에서 법정동명-코드 매핑이 유일하지 않음: {amb_target}")
    if len(ambiguous):
        mp = mp[~mp.set_index(["시군구명", "법정동명"]).index.isin(ambiguous.index)]

    return {
        (r.시군구명, r.법정동명): r.법정동코드
        for r in mp.itertuples(index=False)
    }


def build_dong_index(mapping: dict[tuple[str, str], str]) -> dict[str, list[str]]:
    """구별 법정동명 리스트(긴 이름 우선 — '당산동1가'가 '당산동'보다 먼저 매칭되도록)."""
    idx: dict[str, list[str]] = {}
    for gu, dong in mapping:
        idx.setdefault(gu, []).append(dong)
    for gu in idx:
        idx[gu] = sorted(set(idx[gu]), key=len, reverse=True)
    return idx


def parse_jibun_address(
    addr: str | None,
    mapping: dict[tuple[str, str], str],
    dong_index: dict[str, list[str]] | None = None,
) -> ParsedAddress:
    """지번주소 문자열에서 구·법정동·산·본번·부번을 파싱하고 PNU를 조립한다."""
    if addr is None or (isinstance(addr, float)) or str(addr).strip() == "":
        return ParsedAddress(status=STATUS_ADDR_MISSING)
    text = re.sub(r"\s+", " ", str(addr)).strip()

    if not text.startswith("서울특별시"):
        return ParsedAddress(status=STATUS_NOT_SEOUL)

    m = _GU_RE.match(text)
    if not m:
        return ParsedAddress(status=STATUS_GU_UNPARSED)
    gu = m.group(1)
    rest = text[m.end():]

    # 법정동명: 해당 구의 동 이름 중 가장 긴 prefix 일치
    dong_lists = dong_index if dong_index is not None else build_dong_index(mapping)
    dong = None
    for cand in dong_lists.get(gu, []):
        if rest.startswith(cand):
            dong = cand
            rest = rest[len(cand):]
            break
    if dong is None:
        return ParsedAddress(status=STATUS_DONG_UNMATCHED, gu=gu)

    bm = _BUNJI_RE.match(rest)
    if not bm:
        return ParsedAddress(status=STATUS_BUNJI_MISSING, gu=gu, dong=dong)
    san = bm.group(1) == "산"
    main = int(bm.group(2))
    sub = int(bm.group(3)) if bm.group(3) else 0

    bjd_code = mapping[(gu, dong)]
    pnu = assemble_pnu(bjd_code, san, main, sub)
    return ParsedAddress(
        status=STATUS_OK, gu=gu, dong=dong, san=san,
        bunji_main=main, bunji_sub=sub, bjd_code=bjd_code, pnu=pnu,
    )


def assemble_pnu(bjd_code: str, san: bool, bunji_main: int, bunji_sub: int) -> str:
    """PNU 19자리 = 법정동코드(10) + 대지구분(1) + 본번(4) + 부번(4)."""
    if len(bjd_code) != 10 or not bjd_code.isdigit():
        raise ValueError(f"invalid bjd_code: {bjd_code!r}")
    if not (0 <= bunji_main <= 9999 and 0 <= bunji_sub <= 9999):
        raise ValueError(f"bunji out of range: {bunji_main}-{bunji_sub}")
    return f"{bjd_code}{'2' if san else '1'}{bunji_main:04d}{bunji_sub:04d}"


def parse_address_series(
    addrs: pd.Series, mapping: dict[tuple[str, str], str]
) -> pd.DataFrame:
    """지번주소 Series를 파싱해 컬럼별 DataFrame으로 반환한다."""
    dong_index = build_dong_index(mapping)
    parsed = [
        parse_jibun_address(a if pd.notna(a) else None, mapping, dong_index)
        for a in addrs
    ]
    return pd.DataFrame(
        {
            "parse_status": [p.status for p in parsed],
            "addr_gu": [p.gu for p in parsed],
            "dong": [p.dong for p in parsed],
            "san": [p.san for p in parsed],
            "bunji_main": [p.bunji_main for p in parsed],
            "bunji_sub": [p.bunji_sub for p in parsed],
            "bjd_code": [p.bjd_code for p in parsed],
            "pnu": [p.pnu for p in parsed],
        },
        index=addrs.index,
    )
