"""collect_online_presence.py 의 API 키 전환/소진 처리 검증 (실제 API 호출 없음)."""

import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "data" / "collect_online_presence.py"
spec = importlib.util.spec_from_file_location("collect_online_presence", MODULE_PATH)
cop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cop)

QUOTA_BODY = '{"error":{"errorCode":429,"message":"일별 사용량 한도를 초과하였습니다."}}'.encode("utf-8")


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"{}"):
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8", errors="ignore")

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

    def test_429_without_quota_message_is_retried_not_killed(self):
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


class ExtractDongTest(unittest.TestCase):
    def test_from_jibun(self):
        self.assertEqual(cop.extract_dong("서울특별시 광진구 구의동 71-28"), "구의동")

    def test_falls_back_to_road_when_jibun_missing(self):
        self.assertEqual(cop.extract_dong(float("nan"), "서울특별시 광진구 광나루로36길 67, 1층 (구의동)"), "구의동")

    def test_empty_when_nothing_available(self):
        self.assertEqual(cop.extract_dong(float("nan"), None), "")


if __name__ == "__main__":
    unittest.main()
