# -*- coding: utf-8 -*-
"""지번주소 파싱·PNU 조립 테스트 (raw 데이터 불필요 — 매핑은 인라인 고정값)."""
import pytest

from src.data.address import (
    STATUS_ADDR_MISSING,
    STATUS_BUNJI_MISSING,
    STATUS_DONG_UNMATCHED,
    STATUS_NOT_SEOUL,
    STATUS_OK,
    assemble_pnu,
    parse_jibun_address,
)

# 테스트용 최소 매핑 (실제 소진공 audit에서 확인된 코드)
MAPPING = {
    ("마포구", "서교동"): "1144012000",
    ("광진구", "광장동"): "1121510300",
    ("광진구", "화양동"): "1121510700",
    ("영등포구", "당산동"): "1156012200",
    ("영등포구", "당산동3가"): "1156011700",
    ("영등포구", "여의도동"): "1156011000",
}


def test_basic_jibun():
    p = parse_jibun_address("서울특별시 마포구 서교동 358-18", MAPPING)
    assert p.status == STATUS_OK
    assert (p.gu, p.dong, p.san) == ("마포구", "서교동", False)
    assert (p.bunji_main, p.bunji_sub) == (358, 18)
    assert p.pnu == "1144012000103580018"


def test_no_sub_bunji_defaults_zero():
    p = parse_jibun_address("서울특별시 마포구 서교동 358", MAPPING)
    assert p.status == STATUS_OK
    assert p.pnu == "1144012000103580000"


def test_san_with_space():
    p = parse_jibun_address("서울특별시 광진구 광장동 산 21", MAPPING)
    assert p.status == STATUS_OK
    assert p.san is True
    assert p.pnu == "1121510300200210000"


def test_san_without_space():
    p = parse_jibun_address("서울특별시 광진구 광장동 산21-3", MAPPING)
    assert p.san is True
    assert (p.bunji_main, p.bunji_sub) == (21, 3)


def test_san_in_building_name_not_flagged():
    # 번지 뒤 건물명에 '산'이 들어가도 산 여부로 오인하지 않는다
    p = parse_jibun_address("서울특별시 영등포구 여의도동 15-16 산정빌딩 102동", MAPPING)
    assert p.status == STATUS_OK
    assert p.san is False
    assert (p.bunji_main, p.bunji_sub) == (15, 16)


def test_longest_dong_match():
    # '당산동3가'가 '당산동'보다 먼저 매칭되어야 한다
    p = parse_jibun_address("서울특별시 영등포구 당산동3가 370-5", MAPPING)
    assert p.dong == "당산동3가"
    assert p.pnu == "1156011700103700005"


def test_trailing_text_after_bunji():
    p = parse_jibun_address("서울특별시 마포구 서교동 358-18 2층 (홍대입구)", MAPPING)
    assert p.status == STATUS_OK
    assert (p.bunji_main, p.bunji_sub) == (358, 18)


def test_bunji_beonji_suffix():
    p = parse_jibun_address("서울특별시 마포구 서교동 358-18번지", MAPPING)
    assert p.status == STATUS_OK
    assert (p.bunji_main, p.bunji_sub) == (358, 18)


def test_dong_only_address():
    p = parse_jibun_address("서울특별시 광진구 화양동", MAPPING)
    assert p.status == STATUS_BUNJI_MISSING
    assert p.dong == "화양동"
    assert p.pnu is None


def test_placeholder_bunji():
    # 상암동 택지지구식 표기: 동 뒤에 번지 대신 '.' 등
    p = parse_jibun_address("서울특별시 마포구 서교동 . 무슨빌딩 102호", MAPPING)
    assert p.status == STATUS_BUNJI_MISSING


def test_missing_address():
    assert parse_jibun_address(None, MAPPING).status == STATUS_ADDR_MISSING
    assert parse_jibun_address("   ", MAPPING).status == STATUS_ADDR_MISSING


def test_not_seoul():
    p = parse_jibun_address("인천광역시 부평구 부개동 500-1", MAPPING)
    assert p.status == STATUS_NOT_SEOUL


def test_unknown_dong():
    p = parse_jibun_address("서울특별시 마포구 없는동 1-1", MAPPING)
    assert p.status == STATUS_DONG_UNMATCHED


def test_assemble_pnu_format():
    assert assemble_pnu("1144012000", False, 358, 18) == "1144012000103580018"
    assert assemble_pnu("1144012000", True, 1, 0) == "1144012000200010000"
    assert len(assemble_pnu("1144012000", False, 9999, 9999)) == 19


def test_assemble_pnu_invalid():
    with pytest.raises(ValueError):
        assemble_pnu("114401200", False, 1, 0)  # 9자리
    with pytest.raises(ValueError):
        assemble_pnu("1144012000", False, 10000, 0)
