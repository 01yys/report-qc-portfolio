"""新版两模块的数据契约与行为验收；不包含真实医学标签。"""

import unittest
from unittest.mock import patch
from src.report_checker import check_report


class ReportCheckerTests(unittest.TestCase):
    def test_equivalent_units_do_not_raise_issue(self):
        result = check_report("左肺上叶见8mm结节。", "左肺上叶见0.8cm结节。")
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["issues"])
        self.assertEqual(result["modules"]["measurement"]["comparable_pairs"], 1)

    def test_unequal_sizes_have_traceable_evidence(self):
        findings, impression = "左肺上叶见8mm结节。", "左肺上叶见18mm结节。"
        result = check_report(findings, impression)
        self.assertEqual(result["status"], "needs_review")
        issue, = result["issues"]
        self.assertEqual(issue["type"], "measurement_inconsistency")
        self.assertEqual(issue["source"], "rule")
        self.assertIn("人工核对", issue["suggestion"])
        self.assertEqual(findings[slice(*issue["findings_span"])], issue["findings_evidence"])
        self.assertEqual(impression[slice(*issue["impression_span"])], issue["impression_evidence"])

    def test_different_locations_do_not_compare_sizes(self):
        result = check_report("左肺上叶见8mm结节。", "右肺上叶见18mm结节。")
        self.assertEqual([i["module"] for i in result["issues"]], ["laterality"])
        self.assertEqual(result["modules"]["measurement"]["comparable_pairs"], 0)

    def test_negative_or_uncertain_objects_never_raise_laterality_or_size(self):
        for expression in ("未见", "无", "不能排除", "考虑", "未见明显"):
            with self.subTest(expression=expression):
                result = check_report(f"左肺上叶{expression}8mm结节。", "右肺上叶见18mm结节。")
                self.assertFalse(result["issues"])
                self.assertEqual(result["status"], "unable_to_check")

    def test_positive_object_is_not_lost_because_other_sentence_is_negative(self):
        result = check_report("左肺上叶未见结节。右肺下叶见8mm炎性改变。",
                              "右肺上叶未见结节。左肺下叶见8mm炎性改变。")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("炎性改变", result["issues"][0]["findings_evidence"])

    def test_repeated_objects_are_not_paired_after_filtering_negatives(self):
        # 本例只约束旧两个模块；M3对同侧左肺的肯否差异可以独立提示。
        result = check_report("左肺上叶未见结节。右肺上叶8mm结节。", "左肺上叶18mm结节。", ["laterality", "measurement"])
        self.assertFalse(result["issues"])
        self.assertEqual(result["modules"]["laterality"]["comparable_pairs"], 0)

    def test_repeated_same_location_is_not_paired_by_size(self):
        result = check_report("左肺上叶8mm结节。左肺上叶10mm结节。", "左肺上叶18mm结节。")
        self.assertFalse(result["issues"])
        self.assertEqual(result["status"], "unable_to_check")

    def test_ambiguous_size_is_skipped_but_supported_laterality_still_runs(self):
        result = check_report("左肺上叶约8mm结节。", "左肺上叶18mm结节。")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["modules"]["measurement"]["coverage"], "none")
        self.assertTrue(result["modules"]["measurement"]["skipped_pairs"])

    def test_no_comparable_content_is_not_pass(self):
        result = check_report("左肺结节。", "左肺结节。")
        self.assertEqual(result["status"], "unable_to_check")
        self.assertTrue(result["skipped_segments"])
        self.assertIn("没有可明确比较", result["summary"])

    def test_partial_coverage_is_visible(self):
        result = check_report("左肺上叶8mm结节。双肺下叶炎性改变。", "左肺上叶8mm结节。")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["modules"]["laterality"]["coverage"], "partial")

    def test_module_selection_controls_issues(self):
        result = check_report("左肺上叶8mm结节。", "右肺上叶18mm结节。", ["measurement"])
        self.assertFalse(result["issues"])
        self.assertNotIn("laterality", result["modules"])

    def test_invalid_module_selection_is_rejected(self):
        for modules in ([], "measurement", ["llm"], ["laterality", "laterality"], [{}]):
            with self.subTest(modules=modules):
                result = check_report("左肺上叶结节。", "右肺上叶结节。", modules)
                self.assertEqual(result["status"], "unable_to_check")
                self.assertTrue(result["error_message"])

    def test_bad_inputs_and_limit_are_rejected(self):
        for findings in (None, 12, [], "", " ", "左" * 20001):
            with self.subTest(findings_type=type(findings)):
                self.assertTrue(check_report(findings, "左肺上叶结节。")["error_message"])

    def test_exception_clears_partial_output_and_never_passes(self):
        with patch("src.report_checker.compare_module", side_effect=RuntimeError("synthetic")):
            result = check_report("左肺上叶结节。", "右肺上叶结节。")
        self.assertEqual(result["status"], "unable_to_check")
        self.assertFalse(result["issues"])
        self.assertFalse(result["modules"])

    def test_two_objects_keep_original_issue_order_and_unique_ids(self):
        result = check_report("左肺上叶8mm结节。右肺下叶10mm炎性改变。",
                              "右肺上叶8mm结节。右肺下叶18mm炎性改变。")
        self.assertEqual([i["module"] for i in result["issues"]], ["laterality", "measurement"])
        self.assertEqual(len({i["id"] for i in result["issues"]}), 2)
        self.assertEqual(result["llm_status"], "not_implemented")
        self.assertEqual(result["index_unit"], "unicode_code_point")


if __name__ == "__main__":
    unittest.main()
