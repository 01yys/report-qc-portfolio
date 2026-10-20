"""P0 模拟评测集的自动化测试。"""

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from laterality_checker import check_laterality  # noqa: E402


class LateralityCheckerTests(unittest.TestCase):
    """每个测试对应模拟评测集中的一条产品验收场景。"""

    def test_tc_001_single_lesion_laterality_conflict(self) -> None:
        result = check_laterality(
            "左肺上叶见 8 mm 结节，边界尚清。",
            "右肺上叶结节，建议 3 个月后复查。",
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("左肺上叶", result["issues"][0]["findings_evidence"])
        self.assertIn("右肺上叶", result["issues"][0]["impression_evidence"])

    def test_tc_002_consistent_report_passes(self) -> None:
        result = check_laterality(
            "左肺上叶见 8 mm 结节，边界尚清。右肺下叶见少许炎性改变。",
            "左肺上叶结节，建议随访。右肺下叶炎性改变。",
        )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["summary"], "未发现当前检查范围内的左右位置矛盾。")

    def test_tc_003_two_lesions_both_swapped(self) -> None:
        result = check_laterality(
            "左肺上叶见 8 mm 结节。右肺下叶见少许炎性改变。",
            "右肺上叶结节，建议随访。左肺下叶炎性改变。",
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual(
            {issue["type"] for issue in result["issues"]},
            {"laterality_inconsistency"},
        )

    def test_tc_004_empty_input_returns_unable_to_check(self) -> None:
        result = check_laterality("", "右肺上叶结节，建议随访。")

        self.assertEqual(result["status"], "unable_to_check")
        self.assertEqual(result["issues"], [])
        self.assertIn("影像所见", result["error_message"])

    def test_tc_005_unexpected_error_does_not_become_pass(self) -> None:
        with patch("laterality_checker.split_segments", side_effect=RuntimeError("模拟异常")):
            result = check_laterality(
                "左肺上叶见 8 mm 结节。",
                "右肺上叶结节，建议随访。",
            )

        self.assertEqual(result["status"], "unable_to_check")
        self.assertEqual(result["issues"], [])
        self.assertIn("模拟异常", result["error_message"])

    def test_tc_006_unpaired_mentions_are_not_reported(self) -> None:
        result = check_laterality("左肺上叶结节。", "右肺下叶结节。")
        self.assertEqual(result["issues"], [])

    def test_tc_007_ambiguous_and_bilateral_mentions_are_skipped(self) -> None:
        for findings in ("双肺上叶结节。", "左肺上叶和右肺下叶结节。", "左肺上叶结节。右肺上叶结节。"):
            with self.subTest(findings=findings):
                result = check_laterality(findings, "右肺上叶结节。")
                self.assertEqual(result["issues"], [])

    def test_tc_008_issue_order_follows_findings(self) -> None:
        result = check_laterality(
            "左肺上叶结节。右肺下叶炎性改变。",
            "左肺下叶炎性改变。右肺上叶结节。",
        )
        self.assertEqual(
            [issue["findings_evidence"] for issue in result["issues"]],
            ["左肺上叶结节", "右肺下叶炎性改变"],
        )

    def test_negative_or_uncertain_mentions_do_not_raise_side_issue(self) -> None:
        for expression in ("未见", "无", "不能排除", "考虑", "未见明显"):
            with self.subTest(expression=expression):
                self.assertFalse(check_laterality(f"左肺上叶{expression}结节。", "右肺上叶结节。")["issues"])

    def test_negation_filter_does_not_lose_other_positive_object(self) -> None:
        result = check_laterality("左肺上叶未见结节。右肺下叶炎性改变。",
                                  "右肺上叶未见结节。左肺下叶炎性改变。")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("炎性改变", result["issues"][0]["findings_evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
