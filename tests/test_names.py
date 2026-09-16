# -*- coding: utf-8 -*-
"""상호명 정규화 테스트."""
from src.data.names import normalize_name


def test_plain_name():
    assert normalize_name("쥬씨") == ("쥬씨", None)


def test_trailing_branch_token():
    assert normalize_name("쥬씨 서울숲점") == ("쥬씨", "서울숲점")


def test_single_token_ending_jeom_is_not_branch():
    # 유일 토큰이 '…점'으로 끝나면 상호 자체로 본다
    assert normalize_name("구멍가게상점") == ("구멍가게상점", None)


def test_paren_branch():
    assert normalize_name("스타벅스(홍대점)") == ("스타벅스", "홍대점")


def test_paren_non_branch_removed():
    name_norm, branch = normalize_name("스파포레스트 (SPA Forest)")
    assert name_norm == "스파포레스트"
    assert branch is None


def test_latin_uppercase_and_fullwidth():
    name_norm, _ = normalize_name("ｃａｆｅ　ｍｏｃａ")
    assert name_norm == "CAFEMOCA"


def test_special_chars_removed():
    assert normalize_name("루-42 & 카페!") == ("루42카페", None)


def test_spaces_collapsed():
    assert normalize_name("  김밥  천국  ")[0] == "김밥천국"


def test_missing():
    assert normalize_name(None) == (None, None)
    assert normalize_name("   ") == (None, None)


def test_hosu_branch():
    assert normalize_name("파리바게뜨 여의도2호점") == ("파리바게뜨", "여의도2호점")


def test_branch_preserved_not_deleted():
    # branch는 삭제가 아니라 별도 보존
    name_norm, branch = normalize_name("올리브영 광진화양점")
    assert name_norm == "올리브영"
    assert branch == "광진화양점"
