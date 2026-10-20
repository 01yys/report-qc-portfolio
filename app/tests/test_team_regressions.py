"""团队复查发现的保守拒绝回归；合成输入，不增加医学判断能力。"""
import unittest

from src.report_checker import check_report


class TeamRegressionTests(unittest.TestCase):
    def assert_skipped(self, findings, impression, module):
        result = check_report(findings, impression, [module])
        self.assertIsNone(result["error_message"])
        self.assertEqual(result["status"], "unable_to_check")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["modules"][module]["comparable_pairs"], 0)
        self.assertTrue(result["skipped_segments"] or result["modules"][module]["skipped_pairs"])
        return result

    def test_explicit_history_is_not_a_current_assertion_conflict(self):
        for prefix in ("既往", "去年", "上次", "术前", "切除前", "2025年3月"):
            with self.subTest(prefix=prefix):
                self.assert_skipped(f"{prefix}左肺上叶见结节。", "左肺上叶未见结节。", "assertion")

    def test_cross_time_is_not_a_laterality_conflict(self):
        self.assert_skipped("去年左肺上叶见结节。", "今年右肺上叶见结节。", "laterality")

    def test_explicit_history_is_not_a_measurement_conflict(self):
        for prefix in ("去年", "术前", "2025年3月"):
            with self.subTest(prefix=prefix):
                self.assert_skipped(f"{prefix}左肺上叶见8mm结节。", "左肺上叶见18mm结节。", "measurement")

    def test_chinese_plural_does_not_become_a_unique_object(self):
        for count in ("三枚", "数枚", "五个", "十余枚", "若干颗", "几枚"):
            with self.subTest(count=count):
                self.assert_skipped(f"左肺上叶见{count}结节，直径8mm。", "左肺上叶见结节，直径18mm。", "measurement")
                self.assert_skipped(f"左肺上叶见{count}结节。", "右肺上叶见结节。", "laterality")

    def test_rejected_same_key_plural_cannot_create_false_uniqueness(self):
        for count in ("两枚", "三枚", "数枚", "3枚"):
            with self.subTest(count=count):
                self.assert_skipped(f"左肺上叶见{count}结节，直径8mm。左肺上叶见结节，直径6mm。",
                                    "左肺上叶见结节，直径18mm。", "measurement")
                self.assert_skipped(f"左肺上叶见{count}结节。左肺上叶见结节。", "右肺上叶见结节。", "laterality")

    def test_plural_blocker_does_not_disable_an_unrelated_supported_object(self):
        result = check_report("左肺上叶见三枚结节。右肺下叶见8mm结节。",
                              "右肺下叶见18mm结节。", ["measurement"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("右肺下叶", result["issues"][0]["findings_evidence"])

    def test_incomparable_measurement_axes_are_conservatively_skipped(self):
        for axis in ("最大径", "最小径", "最大直径", "最小直径", "平均径", "长径", "短径", "横径", "纵径", "前后径"):
            with self.subTest(axis=axis):
                self.assert_skipped(f"左肺上叶见结节，{axis}8mm。", "左肺上叶见结节，直径18mm。", "measurement")

    def test_non_supported_number_formats_are_not_partially_extracted(self):
        for value in ("0,8cm", "0，8cm", "0．8cm", "１8mm", "1８mm", "８毫米", "١8mm", "−8mm", "－8mm", "＋8mm"):
            with self.subTest(value=value):
                self.assert_skipped(f"左肺上叶见结节，直径{value}。", "左肺上叶见结节，直径8mm。", "measurement")

    def test_measurement_rejection_does_not_disable_other_modules(self):
        for value in ("0,8cm", "８mm", "最大径8mm"):
            with self.subTest(value=value):
                result = check_report(f"左肺上叶见结节，{value}。", "右肺上叶见结节。", ["laterality"])
                self.assertEqual([issue["module"] for issue in result["issues"]], ["laterality"])
                result = check_report(f"左肺上叶见结节，{value}。", "左肺上叶未见结节。", ["assertion"])
                self.assertEqual([issue["module"] for issue in result["issues"]], ["assertion"])

    def test_supported_current_single_diameter_stays_comparable(self):
        for value, status in (("0.8cm", "pass"), ("18mm", "needs_review")):
            with self.subTest(value=value):
                result = check_report("😀左肺上叶见一枚结节，直径8mm。", f"左肺上叶见结节，直径{value}。", ["measurement"])
                self.assertEqual(result["status"], status)
                self.assertEqual(result["modules"]["measurement"]["comparable_pairs"], 1)
                for issue in result["issues"]:
                    start, end = issue["findings_span"]
                    self.assertEqual(result["input_snapshot"]["findings_text"][start:end], issue["findings_evidence"])


if __name__ == "__main__":
    unittest.main()
