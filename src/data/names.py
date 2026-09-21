# -*- coding: utf-8 -*-
"""상호명 정규화.

- name_raw는 원본 그대로 보존하고, 매칭용 name_norm을 별도 생성한다.
- 지점명(branch)은 삭제하지 않고 별도 컬럼으로 보존한다.
  (소진공 결합 키가 PNU+정규화 상호이므로, 지점 표기 차이가 매칭을 깨지 않도록
   name_norm에서는 제외하되 branch로 남겨 검증·구분에 사용한다.)

규칙 (순서대로):
1. NFKC 정규화(전각→반각), 영문 대문자화, 공백 정리
2. 괄호 안 내용 분리 — 내용이 '…점' 형태면 branch 후보, 아니면 부가 표기로 간주해
   name_norm에서 제외 (branch에는 '…점' 형태만 채운다)
3. 괄호 제거 후 마지막 공백 구분 토큰이 '…점'이고 유일 토큰이 아니면 branch로 분리
   (예: "쥬씨 서울숲점" → base "쥬씨", branch "서울숲점".
    단독 토큰 "구멍가게상점" 등은 상호 자체로 보고 분리하지 않는다)
4. name_norm = base에서 한글·영문·숫자 외 문자 제거
5. 제거 후 빈 문자열이면 branch 포함 전체에서 재계산, 그래도 비면 결측
"""
from __future__ import annotations

import re
import unicodedata

_PAREN_RE = re.compile(r"\(([^()]*)\)")
_BRANCH_TOKEN_RE = re.compile(r"^[가-힣A-Za-z0-9·]+점$")
_KEEP_RE = re.compile(r"[^가-힣A-Za-z0-9]")


def _clean_base(text: str) -> str:
    return _KEEP_RE.sub("", text)


def normalize_name(name_raw: str | None) -> tuple[str | None, str | None]:
    """상호명에서 (name_norm, branch)를 생성한다. 입력이 결측이면 (None, None)."""
    if name_raw is None or str(name_raw).strip() == "":
        return None, None

    text = unicodedata.normalize("NFKC", str(name_raw)).upper()
    text = re.sub(r"\s+", " ", text).strip()

    branch: str | None = None

    # 괄호 안 내용: '…점'이면 branch, 아니면 부가 표기로 제외
    def _take_paren(m: re.Match) -> str:
        nonlocal branch
        inner = m.group(1).strip()
        if branch is None and _BRANCH_TOKEN_RE.match(inner):
            branch = inner
        return " "

    text = _PAREN_RE.sub(_take_paren, text)
    text = re.sub(r"\s+", " ", text).strip()

    # 마지막 토큰이 '…점'이면 branch로 분리 (유일 토큰은 상호 자체로 유지)
    tokens = text.split(" ")
    if len(tokens) >= 2 and _BRANCH_TOKEN_RE.match(tokens[-1]):
        if branch is None:
            branch = tokens[-1]
        tokens = tokens[:-1]
    base = " ".join(tokens)

    name_norm = _clean_base(base)
    if not name_norm:
        # base가 특수문자뿐이면 branch 포함 전체로 재계산
        name_norm = _clean_base(text)
    return (name_norm or None), branch
