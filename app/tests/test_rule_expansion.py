"""v1.3有限表达扩展的合成回归，不代表医学金标准或保留集。"""

import unittest
from src.report_checker import check_report
from src.report_structure import parse_report


# 在改规则前固定期望；每项只描述文本关系，不判哪一段正确。
REGRESSION_CASES = [
    ("CN-01", "左肺上叶见8毫米结节。", "左肺上叶见18毫米结节。", "measurement", "needs_review"),
    ("CN-02", "左肺上叶见8毫米结节。", "左肺上叶见0.8厘米结节。", "measurement", "pass"),
    ("CN-03", "左肺上叶见0.8厘米结节。", "左肺上叶见8 mm结节。", "measurement", "pass"),
    ("CN-04", "左肺上叶见约8毫米结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-05", "左肺上叶见8毫米左右结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-06", "左肺上叶见8至10毫米结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-07", "左肺上叶见8毫米至1厘米结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-08", "左肺上叶见8×6毫米结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-09", "左肺上叶见8毫米、10毫米结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-10", "左肺上叶见两枚结节，直径8毫米。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-11", "左肺上叶结节原为8毫米。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-12", "左肺上叶见8平方毫米结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("CN-13", "左肺上叶见8毫米/秒结节。", "左肺上叶见18毫米结节。", "measurement", "unable_to_check"),
    ("POST-01", "左肺上叶可见一枚结节。", "左肺上叶这枚结节不存在。", "assertion", "needs_review"),
    ("POST-02", "左肺上叶这枚结节不存在。", "左肺上叶可见一枚结节。", "assertion", "needs_review"),
    ("POST-03", "左肺上叶这枚结节不存在。", "左肺上叶未见结节。", "assertion", "pass"),
    ("POST-04", "左肺上叶这枚结节可能不存在。", "左肺上叶见结节。", "assertion", "unable_to_check"),
    ("POST-05", "左肺上叶这枚结节并非不存在。", "左肺上叶见结节。", "assertion", "unable_to_check"),
    ("POST-06", "左肺上叶结节没有增大。", "左肺上叶见结节。", "assertion", "pass"),
    ("POST-07", "左肺上叶结节不存在增大。", "左肺上叶见结节。", "assertion", "unable_to_check"),
    ("POST-08", "左肺上叶结节，未见明显增大。", "左肺上叶见结节。", "assertion", "pass"),
    ("POST-09", "左肺上叶结节待排。", "左肺上叶见结节。", "assertion", "unable_to_check"),
    ("POST-10", "左肺上叶两枚结节不存在。", "左肺上叶见结节。", "assertion", "unable_to_check"),
    ("POST-11", "左肺上叶结节不存在。", "右肺上叶见结节。", "assertion", "unable_to_check"),
    ("POST-12", "左肺上叶见结节。左肺上叶这枚结节不存在。", "左肺上叶见结节。", "assertion", "unable_to_check"),
]


class RuleExpansionTests(unittest.TestCase):
    def test_fixed_regression_cases_and_coverage(self):
        for case_id, findings, impression, module, expected in REGRESSION_CASES:
            with self.subTest(case_id=case_id):
                result = check_report(findings, impression, [module])
                self.assertIsNone(result["error_message"])
                self.assertEqual(result["status"], expected)
                self.assertEqual([i["module"] for i in result["issues"]],
                                 [module] if expected == "needs_review" else [])
                self.assertEqual(result["modules"][module]["comparable_pairs"],
                                 0 if expected == "unable_to_check" else 1)

    def test_chinese_units_preserve_original_spans_and_canonical_units(self):
        for raw, unit, length in (("8 毫米", "mm", "8"), ("0.8 厘米", "cm", "8.0"),
                                  ("0.1厘米", "cm", "1.0"), ("1.25毫米", "mm", "1.25")):
            with self.subTest(raw=raw):
                text = f"  😀左肺上叶见{raw}结节。"
                finding = parse_report(text, "findings").findings[0]
                measurement = finding.measurement
                self.assertIsNotNone(measurement)
                self.assertEqual(measurement.raw, raw)
                self.assertEqual(text[measurement.span_start:measurement.span_end], raw)
                self.assertEqual(measurement.unit, unit)
                self.assertEqual(measurement.normalized_mm, length)
                self.assertEqual(text[finding.span_start:finding.span_end], finding.evidence)

    def test_chinese_unsupported_measurements_are_not_shortened_to_single_length(self):
        for raw in ("-8毫米", "0厘米", "8毫米²", "8厘米³", "8毫米^2", "8毫米每秒",
                    "8平方厘米", "8立方毫米", "8毫米以下", "大于8毫米", "8e2毫米",
                    "8毫米×6毫米", "8毫米至1cm", "8cm至10毫米", "123456789012345678901毫米"):
            with self.subTest(raw=raw):
                finding = parse_report(f"左肺上叶见{raw}结节。", "findings").findings[0]
                self.assertIsNone(finding.measurement)
                self.assertTrue(finding.measurement_skip_reason)

    def test_approximate_chinese_size_does_not_hide_supported_laterality(self):
        result = check_report("左肺上叶见8毫米左右结节。", "右肺上叶见18毫米结节。")
        self.assertEqual([i["module"] for i in result["issues"]], ["laterality"])
        self.assertEqual(result["modules"]["measurement"]["comparable_pairs"], 0)

    def test_post_object_absence_never_leaks_to_laterality_or_measurement(self):
        result = check_report("左肺上叶这枚8毫米结节不存在。", "右肺上叶见18毫米结节。")
        self.assertFalse(result["issues"])
        self.assertEqual(result["status"], "unable_to_check")

    def test_chinese_measurement_in_pre_object_negation_is_not_positive(self):
        result = check_report("左肺上叶未见8毫米结节。", "左肺上叶见8毫米结节。")
        self.assertEqual([i["module"] for i in result["issues"]], ["assertion"])

    def test_growth_negation_does_not_override_uncertainty(self):
        for text in ("左肺上叶结节可能不存在，未见增大。", "左肺上叶结节未见明确增大，待排。"):
            with self.subTest(text=text):
                result = check_report(text, "左肺上叶见结节。", ["assertion"])
                self.assertEqual(result["status"], "unable_to_check")

    def test_post_object_negation_with_qualifier_is_not_treated_as_absence(self):
        for tail in ("不存在恶性征象", "不存在线索", "并不是不存在", "不可能不存在", "不存在明显增大"):
            with self.subTest(tail=tail):
                result = check_report(f"左肺上叶结节{tail}。", "左肺上叶见结节。", ["assertion"])
                self.assertEqual(result["status"], "unable_to_check")
                self.assertFalse(result["issues"])


if __name__ == "__main__":
    unittest.main()
