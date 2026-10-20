"""肯否模块：明确定位、对象、断言才比较。"""

import unittest
from src.report_checker import check_report


class AssertionTests(unittest.TestCase):
    def test_positive_negative_same_object_conflicts_both_directions(self):
        for a, b in (("见", "未见"), ("无", "见")):
            with self.subTest(a=a):
                result = check_report(f"右肺下叶{a}炎性改变。", f"右肺下叶{b}炎性改变。")
                issue, = result["issues"]
                self.assertEqual(issue["type"], "assertion_inconsistency")
                self.assertEqual(issue["source"], "rule")
                self.assertEqual(result["modules"]["laterality"]["comparable_pairs"], 0)

    def test_both_negative_have_no_issue_and_are_compared(self):
        result = check_report("右肺下叶未见炎性改变。", "右肺下叶无炎性改变。")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["modules"]["assertion"]["comparable_pairs"], 1)
        self.assertFalse(result["issues"])

    def test_meiyou_means_absent_not_present(self):
        for f, i in [("左肺上叶见结节。", "左肺上叶没有结节。"),
                     ("左肺上叶没有结节。", "左肺上叶见结节。")]:
            with self.subTest(f=f):
                result = check_report(f, i)
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual([x["module"] for x in result["issues"]], ["assertion"])
        self.assertFalse(check_report("左肺上叶没有结节。", "左肺上叶未见结节。")["issues"])

    def test_meiyou_double_negation_and_growth_do_not_mean_absent(self):
        for f in ("左肺上叶并非没有结节。", "左肺上叶没有明确结节。", "左肺上叶没有钙化结节。"):
            with self.subTest(f=f):
                self.assertEqual(check_report(f, "左肺上叶结节。", ["assertion"])["status"], "unable_to_check")
        result = check_report("左肺上叶结节没有增大。", "左肺上叶结节。", ["assertion"])
        self.assertEqual(result["status"], "pass")

    def test_uncertain_and_descriptor_negation_are_not_polarity_errors(self):
        for text in ("右肺上叶不能排除结节。", "右肺上叶无钙化结节。", "右肺上叶未见明显结节。"):
            with self.subTest(text=text):
                result = check_report(text, "右肺上叶结节。", ["assertion"])
                self.assertEqual(result["status"], "unable_to_check")
                self.assertFalse(result["issues"])

    def test_different_side_or_anatomy_do_not_pair(self):
        for impression in ("左肺下叶炎性改变。", "右肺上叶炎性改变。"):
            with self.subTest(impression=impression):
                self.assertFalse(check_report("右肺下叶未见炎性改变。", impression, ["assertion"])["issues"])

    def test_duplicate_objects_are_not_paired(self):
        result = check_report("左肺上叶未见结节。左肺上叶结节。", "左肺上叶结节。", ["assertion"])
        self.assertEqual(result["modules"]["assertion"]["comparable_pairs"], 0)

    def test_concept_sample_yields_two_real_issues(self):
        result = check_report("左肺上叶见 8 mm 结节。右肺下叶未见炎性改变。",
                              "右肺上叶结节，约 8 mm。右肺下叶炎性改变。")
        self.assertEqual([i["module"] for i in result["issues"]], ["laterality", "assertion"])

    def test_disabling_assertion_module_does_not_emit_its_issues(self):
        result = check_report("右肺下叶未见炎性改变。", "右肺下叶炎性改变。", ["laterality"])
        self.assertFalse(result["issues"])


if __name__ == "__main__":
    unittest.main()
