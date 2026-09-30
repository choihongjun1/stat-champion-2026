"""export_online_features의 temporal 메타데이터 검증.

핵심은 축A의 available_at이 master의 시점 검사(`available_at > origin_end`,
src/data/master.py)에서 TypeError 없이 비교되어야 한다는 것이다 — labels/landprice/
trdar의 available_at이 전부 tz-naive이기 때문이다.
축B source_snapshot(raw 파일 sha256 + collection_run_id + git SHA, 이슈 #23)은 임시 파일로 검증한다.
"""

import gzip
import hashlib
import importlib.util
import json
import pathlib
import tempfile
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


RUN_A, RUN_B = "20260923T143942Z-a589611d", "20260924T010000Z-0000beef"
SHA_A, SHA_B = "20dbcbceaa839830368d4e31ad1df7e8687730e5", "1111111111112222222222223333333333334444"


class SourceSnapshotTest(unittest.TestCase):
    """축B source_snapshot = raw 파일 바이트 sha256 + collection_run_id + git SHA (이슈 #23)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_inputs(self, raw_lines=None, manifest=None, qa_runs=None):
        d = self.dir
        pd.DataFrame({
            "store_id": ["GR_1", "GR_1", "SR_2"], "year_month": ["2024-01", "2024-03", "2025-06"],
            "platform": ["blog"] * 3, "mention_count": ["2", "1", "5"], "sponsor_filtered_count": ["0", "1", "0"],
            "collected_at": ["2026-09-23T15:00:00+00:00"] * 3,
        }).to_csv(d / "monthly.csv", index=False)
        runs = qa_runs or {"GR_1": RUN_A, "SR_2": RUN_A}
        pd.DataFrame({"store_id": list(runs), "collection_run_id": list(runs.values()), "error": ""}).to_csv(
            d / "qa.csv", index=False)
        # 기존 형식 manifest — raw checksum 필드가 없다
        manifest = manifest if manifest is not None else [
            {"collection_run_id": RUN_A, "git_sha": SHA_A, "status": "started", "input_checksum_sha256": "x"},
            {"collection_run_id": RUN_A, "git_sha": SHA_A, "status": "completed", "input_checksum_sha256": "x"},
        ]
        (d / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in manifest), encoding="utf-8")
        with gzip.open(d / "blog_items.jsonl.gz", "wt", encoding="utf-8") as f:
            for line in raw_lines or ['{"store_id": "GR_1", "link": "a", "postdate": "20240105", "matched": true}']:
                f.write(line + "\n")

    def build(self):
        d = self.dir
        return exp.build_monthly(str(d / "monthly.csv"), str(d / "qa.csv"), str(d / "manifest.jsonl"),
                                 str(d / "blog_items.jsonl.gz"))

    def test_file_sha256_is_raw_bytes_and_deterministic(self):
        self.write_inputs()
        path = self.dir / "blog_items.jsonl.gz"
        expected = hashlib.sha256(path.read_bytes()).hexdigest()  # gzip 해제가 아니라 파일 바이트
        self.assertEqual(exp.file_sha256(str(path)), expected)
        self.assertEqual(exp.file_sha256(str(path)), exp.file_sha256(str(path)))

    def test_snapshot_contains_raw_sha_run_and_git(self):
        self.write_inputs()
        raw = exp.file_sha256(str(self.dir / "blog_items.jsonl.gz"))
        out = self.build()
        self.assertEqual(set(out["source_snapshot"]), {f"blog_items.jsonl.gz@sha256:{raw}#run:{RUN_A}#git:{SHA_A[:12]}"})
        self.assertEqual(set(out["collection_run_id"]), {RUN_A})
        self.assertEqual(set(out["git_sha"]), {SHA_A})  # 기존 컬럼은 전체 SHA 그대로

    def test_same_inputs_same_snapshot_and_other_raw_differs(self):
        self.write_inputs()
        first = self.build()["source_snapshot"].tolist()
        self.assertEqual(first, self.build()["source_snapshot"].tolist())
        self.write_inputs(raw_lines=['{"store_id": "GR_1", "link": "b", "postdate": "20240105", "matched": true}'])
        second = self.build()["source_snapshot"].tolist()
        self.assertNotEqual(first, second)

    def test_only_source_snapshot_changes_feature_values_intact(self):
        self.write_inputs()
        out = self.build()
        self.assertEqual(len(out), 3)
        self.assertEqual(out[["store_id", "year_month"]].values.tolist(),
                         [["GR_1", "2024-01"], ["GR_1", "2024-03"], ["SR_2", "2025-06"]])
        self.assertEqual(out["mention_count"].tolist(), ["2", "1", "5"])
        self.assertEqual(out["sponsor_filtered_count"].tolist(), ["0", "1", "0"])
        self.assertEqual(out["feature_asof"].tolist(), ["2024-01-31", "2024-03-31", "2025-06-30"])
        self.assertEqual(out["available_at"].tolist(), out["feature_asof"].tolist())

    def test_rows_keep_their_own_run_and_git(self):
        # --resume: 중단된 run(completed 기록 없음)의 행도 그 run의 SHA로 식별된다
        manifest = [
            {"collection_run_id": RUN_A, "git_sha": SHA_A, "status": "started"},
            {"collection_run_id": RUN_A, "git_sha": SHA_A, "status": "interrupted_quota"},
            {"collection_run_id": RUN_B, "git_sha": SHA_B, "status": "started"},
            {"collection_run_id": RUN_B, "git_sha": SHA_B, "status": "completed"},
        ]
        self.write_inputs(manifest=manifest, qa_runs={"GR_1": RUN_A, "SR_2": RUN_B})
        out = self.build().set_index("store_id")
        self.assertTrue(out.loc["GR_1", "source_snapshot"].str.endswith(f"#run:{RUN_A}#git:{SHA_A[:12]}").all())
        self.assertTrue(out.loc[["SR_2"], "source_snapshot"].str.endswith(f"#run:{RUN_B}#git:{SHA_B[:12]}").all())
        # raw sha는 export가 참조한 최종 raw 하나라 run과 무관하게 같다
        self.assertEqual(out["source_snapshot"].str.split("#").str[0].nunique(), 1)

    def test_run_missing_from_manifest_fails(self):
        self.write_inputs(qa_runs={"GR_1": RUN_A, "SR_2": RUN_B})  # RUN_B는 manifest에 없음
        with self.assertRaises(ValueError):
            self.build()

    def test_store_without_run_id_fails(self):
        self.write_inputs(qa_runs={"GR_1": RUN_A})  # SR_2가 QA에 없음
        with self.assertRaises(ValueError):
            self.build()

    def test_unknown_git_sha_fails(self):
        self.write_inputs(manifest=[{"collection_run_id": RUN_A, "git_sha": "unknown", "status": "completed"}])
        with self.assertRaises(ValueError):
            self.build()

    def test_conflicting_git_sha_for_same_run_fails(self):
        manifest = [{"collection_run_id": RUN_A, "git_sha": SHA_A, "status": "started"},
                    {"collection_run_id": RUN_A, "git_sha": SHA_B, "status": "completed"}]
        with self.assertRaises(ValueError):
            self.write_inputs(manifest=manifest)
            self.build()


class AxisAUnchangedTest(unittest.TestCase):
    """축A(online_presence)의 시점 메타·source_snapshot은 이번 변경과 무관하다."""

    def test_presence_metadata_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i, (sid, ts) in enumerate([("GR_1", "2026-09-17T14:41:51+00:00"), ("SR_2", "2026-09-23T15:58:42+00:00")]):
                p = pathlib.Path(tmp) / f"presence_{i}.csv"
                pd.DataFrame({"store_id": [sid], "collected_at": [ts]}).to_csv(p, index=False)
                paths.append(str(p))
            old, exp.PRESENCE_PATHS = exp.PRESENCE_PATHS, paths
            try:
                out = exp.build_presence()
            finally:
                exp.PRESENCE_PATHS = old
        self.assertEqual(out["feature_asof"].tolist(),
                         [pd.Timestamp("2026-09-17 23:41:51"), pd.Timestamp("2026-09-24 00:58:42")])
        self.assertEqual(out["available_at"].tolist(), out["feature_asof"].tolist())
        self.assertEqual(set(out["source_snapshot"]), {"collect_online_presence.py:online_presence_all+remaining"})


if __name__ == "__main__":
    unittest.main()
