"""collect_online_presence.py 의 매칭·키 전환·오류 분류 검증 (실제 API 호출 없음)."""

import importlib.util
import json
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "data" / "collect_online_presence.py"
spec = importlib.util.spec_from_file_location("collect_online_presence", MODULE_PATH)
cop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cop)

QUOTA_BODY = json.dumps({"error": {"errorCode": 429, "message": "일별 사용량 한도를 초과하였습니다."}}).encode("utf-8")
QUOTA_BODY_EN = json.dumps({"error": {"errorCode": 429, "message": "Daily quota exceeded."}}).encode("utf-8")


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"{}"):
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8", errors="ignore")

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        raise RuntimeError(f"http {self.status_code}")


class FakeSession:
    def __init__(self, behavior: dict):
        self.behavior = behavior
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        key = headers["k"]
        self.calls.append(key)
        return self.behavior[key]()


class FakeHolder:
    def __init__(self, session):
        self._s = session

    def get(self):
        return self._s


def make_ctx(behavior: dict):
    keys = [{"k": name} for name in behavior]
    session = FakeSession(behavior)
    ctx = cop.Context(FakeHolder(session), cop.KeyPool(keys, "naver"), cop.KeyPool([{"k": "kakao"}], "kakao"))
    return ctx, session


class KeyFailoverTest(unittest.TestCase):
    def test_moves_to_next_key_on_quota(self):
        ctx, session = make_ctx({"A": lambda: FakeResponse(429, QUOTA_BODY), "B": lambda: FakeResponse(200)})
        resp = cop.pooled_get(ctx, ctx.naver, "url", {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.calls, ["A", "B"])
        self.assertEqual(ctx.naver.active_index(), 1)

    def test_dead_key_is_not_retried(self):
        ctx, session = make_ctx({"A": lambda: FakeResponse(429, QUOTA_BODY), "B": lambda: FakeResponse(200)})
        cop.pooled_get(ctx, ctx.naver, "url", {})
        cop.pooled_get(ctx, ctx.naver, "url", {})
        self.assertEqual(session.calls, ["A", "B", "B"])

    def test_all_keys_exhausted_raises(self):
        ctx, _ = make_ctx({"A": lambda: FakeResponse(429, QUOTA_BODY), "B": lambda: FakeResponse(429, QUOTA_BODY)})
        with self.assertRaises(cop.QuotaExhausted):
            cop.pooled_get(ctx, ctx.naver, "url", {})

    def test_invalid_key_is_skipped(self):
        ctx, session = make_ctx({"A": lambda: FakeResponse(401), "B": lambda: FakeResponse(200)})
        resp = cop.pooled_get(ctx, ctx.naver, "url", {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.calls, ["A", "B"])

    def test_429_without_quota_structure_is_retried_not_killed(self):
        # errorCode==429 구조가 없는 429는 (메시지 언어와 무관하게) 일시적 오류로 보고 재시도한다.
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return FakeResponse(429, b"{}") if calls["n"] == 1 else FakeResponse(200)

        orig_sleep = cop.time.sleep
        cop.time.sleep = lambda s: None
        try:
            ctx, _ = make_ctx({"A": flaky})
            resp = cop.pooled_get(ctx, ctx.naver, "url", {})
        finally:
            cop.time.sleep = orig_sleep
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ctx.naver.active_index(), 0)

    def test_english_quota_message_is_still_detected(self):
        # PR #21 리뷰 지적: 예전엔 "한도"(한글) 문구로만 판정해 영문 메시지를 놓쳤다.
        # errorCode 구조로 판정하면 메시지 언어와 무관하게 동일하게 동작해야 한다.
        ctx, session = make_ctx({"A": lambda: FakeResponse(429, QUOTA_BODY_EN), "B": lambda: FakeResponse(200)})
        resp = cop.pooled_get(ctx, ctx.naver, "url", {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.calls, ["A", "B"])
        self.assertEqual(ctx.naver.active_index(), 1)


class IsQuotaResponseTest(unittest.TestCase):
    def test_true_for_error_code_429(self):
        self.assertTrue(cop.is_quota_response(FakeResponse(429, QUOTA_BODY)))

    def test_false_for_empty_body(self):
        self.assertFalse(cop.is_quota_response(FakeResponse(429, b"{}")))

    def test_false_for_non_json_body(self):
        self.assertFalse(cop.is_quota_response(FakeResponse(429, b"not json")))


class ExtractDongTest(unittest.TestCase):
    def test_from_jibun(self):
        self.assertEqual(cop.extract_dong("서울특별시 광진구 구의동 71-28"), "구의동")

    def test_falls_back_to_road_when_jibun_missing(self):
        self.assertEqual(cop.extract_dong(float("nan"), "서울특별시 광진구 광나루로36길 67, 1층 (구의동)"), "구의동")

    def test_empty_when_nothing_available(self):
        self.assertEqual(cop.extract_dong(float("nan"), None), "")


class NameMatchesTest(unittest.TestCase):
    def test_substring_both_directions(self):
        self.assertTrue(cop.name_matches("김밥천국", "김밥천국 리뷰"))
        self.assertTrue(cop.name_matches("김밥", "김밥천국"))

    def test_empty_never_matches(self):
        self.assertFalse(cop.name_matches("", "아무거나"))
        self.assertFalse(cop.name_matches("아무거나", ""))


class DongGuConsistentTest(unittest.TestCase):
    def test_dong_requires_presence(self):
        self.assertFalse(cop.dong_consistent("", "서울특별시 광진구 구의동 1"))
        self.assertFalse(cop.dong_consistent("구의동", ""))
        self.assertTrue(cop.dong_consistent("구의동", "서울특별시 광진구 구의동 1"))

    def test_gu_missing_bypasses_check(self):
        # gu 컬럼이 없는 입력(광진구 단일구 시범)에서는 구 제약을 걸지 않는다.
        self.assertTrue(cop.gu_consistent("", "경기도 안산시 단원구 신길동 123"))

    def test_gu_present_rejects_other_sigungu(self):
        # PR #21 리뷰 실측 사례 재현: 영등포구 신길동 vs 안산시 단원구 신길동.
        self.assertFalse(cop.gu_consistent("영등포구", "경기도 안산시 단원구 신길동 123"))
        self.assertTrue(cop.gu_consistent("영등포구", "서울특별시 영등포구 신길동 123"))


class NaverLocalTest(unittest.TestCase):
    def _ctx(self, items):
        ctx, _ = make_ctx({"A": lambda: FakeResponse(200, json.dumps({"items": items}).encode("utf-8"))})
        return ctx

    def test_registered_true_when_name_dong_gu_match(self):
        ctx = self._ctx([{"title": "선화분식", "address": "서울특별시 광진구 군자동 100"}])
        r = cop.naver_local(ctx, "선화분식 군자동", "선화분식", "군자동", "광진구")
        self.assertTrue(r["registered"])
        self.assertEqual(r["rank"], 1)
        self.assertEqual(r["n_candidates"], 1)
        self.assertFalse(r["ambiguous"])

    def test_registered_false_when_gu_mismatches_other_sigungu(self):
        # 동명이동: 신길동이 영등포구에도, 안산 단원구에도 있음.
        ctx = self._ctx([{"title": "선화분식", "address": "경기도 안산시 단원구 신길동 123"}])
        r = cop.naver_local(ctx, "선화분식 신길동", "선화분식", "신길동", "영등포구")
        self.assertFalse(r["registered"])
        self.assertEqual(r["n_candidates"], 1)  # 검색은 됐지만(0건이 아님) 조건 불일치로 미매칭

    def test_zero_results_vs_no_match_distinguishable_via_n_candidates(self):
        ctx_empty = self._ctx([])
        r_empty = cop.naver_local(ctx_empty, "무관 군자동", "무관", "군자동", "광진구")
        ctx_nomatch = self._ctx([{"title": "전혀다른가게", "address": "서울특별시 마포구 합정동 1"}])
        r_nomatch = cop.naver_local(ctx_nomatch, "무관 군자동", "무관", "군자동", "광진구")
        self.assertEqual(r_empty["n_candidates"], 0)
        self.assertEqual(r_nomatch["n_candidates"], 1)
        self.assertFalse(r_empty["registered"])
        self.assertFalse(r_nomatch["registered"])

    def test_ambiguous_when_multiple_candidates_match(self):
        ctx = self._ctx(
            [
                {"title": "수정", "address": "서울특별시 광진구 구의동 1"},
                {"title": "수정", "address": "서울특별시 광진구 구의동 2"},
            ]
        )
        r = cop.naver_local(ctx, "수정 구의동", "수정", "구의동", "광진구")
        self.assertTrue(r["registered"])
        self.assertTrue(r["ambiguous"])
        self.assertEqual(r["n_candidates"], 2)
        self.assertEqual(r["rank"], 1)  # 첫 매칭(검색 API 정렬 순)을 채택


class KakaoLocalTest(unittest.TestCase):
    def _ctx(self, docs):
        ctx, _ = make_ctx({"kakao": lambda: FakeResponse(200, json.dumps({"documents": docs}).encode("utf-8"))})
        return ctx

    def test_registered_requires_gu_match(self):
        ctx = self._ctx([{"place_name": "선화분식", "address_name": "경기도 안산시 단원구 신길동 123"}])
        r = cop.kakao_local(ctx, "선화분식 신길동", "선화분식", "신길동", "영등포구")
        self.assertFalse(r["registered"])
        self.assertEqual(r["n_candidates"], 1)


class FilterMentionsTest(unittest.TestCase):
    def test_keeps_only_items_mentioning_store_name(self):
        items = [
            {"title": "선화분식 방문기", "description": "맛있었다"},
            {"title": "요즘 날씨가", "description": "덥네요"},
        ]
        mentions = cop.filter_mentions(items, "선화분식")
        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["title"], "선화분식 방문기")


class ClassifyErrorTest(unittest.TestCase):
    def test_timeout(self):
        self.assertEqual(cop.classify_error(cop.requests.exceptions.Timeout()), "timeout")

    def test_connection_error(self):
        self.assertEqual(cop.classify_error(cop.requests.exceptions.ConnectionError()), "connection_error")

    def test_http_error(self):
        self.assertEqual(cop.classify_error(cop.requests.exceptions.HTTPError()), "http_error")

    def test_parse_error(self):
        self.assertEqual(cop.classify_error(ValueError("bad json")), "parse_error")

    def test_other_falls_back(self):
        self.assertEqual(cop.classify_error(RuntimeError("?")), "other")


if __name__ == "__main__":
    unittest.main()
