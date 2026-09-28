"""export_online_features의 temporal 메타데이터 정규화 검증 (파일 입출력 없음).

핵심은 축A의 available_at이 master의 시점 검사(`available_at > origin_end`,
src/data/master.py)에서 TypeError 없이 비교되어야 한다는 것이다 — labels/landprice/
trdar의 available_at이 전부 tz-naive이기 때문이다.
"""

import importlib.util
import pathlib
import unittest

import pandas as pd

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "data" / "export_online_features.py"
)
spec = importlib.util.spec_from_file_location("export_online_features", MODULE_PATH)
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)


class ToNaiveKstTest(unittest.TestCase):
    def test_tz_aware_utc_is_converted_to_kst_wall_clock(self):
        out = exp._to_naive_kst(pd.Series(["2026-09-17T14:41:51.043583+00:00"]))
        self.assertIsNone(out.dt.tz)
        self.assertEqual(out.iloc[0], pd.Timestamp("2026-09-17 23:41:51.043583"))

    def test_utc_evening_rolls_to_next_kst_day(self):
        # 15:58Z = 다음 날 00:58 KST. 한국 달력일 기준으로 기록해야 origin_end와 같은 기준이 된다.
        out = exp._to_naive_kst(pd.Series(["2026-09-23T15:58:42+00:00"]))
        self.assertEqual(out.iloc[0], pd.Timestamp("2026-09-24 00:58:42"))

    def test_naive_input_is_treated_as_utc(self):
        out = exp._to_naive_kst(pd.Series(["2026-09-17 14:41:51"]))
        self.assertIsNone(out.dt.tz)
        self.assertEqual(out.iloc[0], pd.Timestamp("2026-09-17 23:41:51"))

    def test_mixed_offsets_normalize_to_same_instant(self):
        out = exp._to_naive_kst(pd.Series(["2026-09-17T14:41:51+00:00", "2026-09-17T23:41:51+09:00"]))
        self.assertEqual(out.iloc[0], out.iloc[1])

    def test_unparsable_value_raises_instead_of_silently_dropping(self):
        # available_at이 비면 master의 시점 검사를 그냥 통과해버린다(fail-open) -> 실패시킨다.
        with self.assertRaises(ValueError):
            exp._to_naive_kst(pd.Series(["2026-09-17T14:41:51+00:00", "not-a-date"]))

    def test_empty_value_raises(self):
        with self.assertRaises(ValueError):
            exp._to_naive_kst(pd.Series([""]))


class AsOfComparisonTest(unittest.TestCase):
    """master.temporal_leakage_counts와 같은 형태의 비교가 죽지 않는지."""

    def setUp(self):
        self.origin_end = pd.Series(pd.to_datetime(["2025-06-30", "2021-03-31"]))

    def test_raw_collected_at_string_would_break_comparison(self):
        aware = pd.to_datetime(pd.Series(["2026-09-17T14:41:51+00:00"] * 2))
        with self.assertRaises(TypeError):
            _ = aware > self.origin_end

    def test_normalized_value_compares_without_error(self):
        avail = exp._to_naive_kst(pd.Series(["2026-09-17T14:41:51+00:00"] * 2))
        self.assertTrue(bool((avail > self.origin_end).all()))

    def test_axis_a_is_blocked_for_every_origin(self):
        # 축A는 수집 시점(2026-09) 값이라 모든 origin에서 차단되어야 한다.
        avail = exp._to_naive_kst(pd.Series(["2026-09-17T14:41:51+00:00"]))
        origins = pd.to_datetime(pd.Series(["2021-03-31", "2023-12-31", "2025-06-30"]))
        self.assertTrue(bool((avail.iloc[0] > origins).all()))


if __name__ == "__main__":
    unittest.main()
