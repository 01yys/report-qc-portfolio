"""共同结构化层测试；原文位置、断言和测量均使用合成文本。"""

import unittest
from src.report_structure import parse_report


class StructureTests(unittest.TestCase):
    def test_evidence_and_measurement_offsets_recover_original_unicode(self):
        text = "  😀左肺上叶见 0.8 cm 结节。\n  右肺下叶见炎性改变。"
        parsed = parse_report(text, "findings")
        self.assertEqual(len(parsed.findings), 2)
        for finding in parsed.findings:
            self.assertEqual(text[finding.span_start:finding.span_end], finding.evidence)
            self.assertEqual(finding.source, "findings")
        measurement = parsed.findings[0].measurement
        self.assertEqual(text[measurement.span_start:measurement.span_end], "0.8 cm")
        self.assertEqual(measurement.normalized_mm, "8.0")

    def test_assertions_are_local_and_conservative(self):
        cases = {
            "左肺上叶见结节。": "present", "左肺上叶结节。": "present",
            "左肺上叶未见结节。": "absent", "左肺上叶无结节。": "absent",
            "左肺上叶未发现结节。": "absent", "左肺上叶不能排除结节。": "uncertain",
            "左肺上叶考虑结节。": "uncertain", "左肺上叶未见明显结节。": "uncertain",
            "左肺上叶并非无结节。": "uncertain", "左肺上叶无明显结节。": "uncertain",
            "左肺上叶否认结节。": "unknown", "左肺上叶结节，未见增大。": "present",
            "左肺上叶排除结节。": "uncertain",
            "左肺上叶无钙化结节。": "unknown", "左肺上叶结节未见。": "unknown",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_report(text, "findings").findings[0].assertion, expected)

    def test_negation_does_not_skip_the_whole_report(self):
        parsed = parse_report("左肺上叶未见结节。右肺下叶见炎性改变。", "findings")
        self.assertEqual([f.assertion for f in parsed.findings], ["absent", "present"])

    def test_followup_months_are_not_misclassified_as_multiple_lesions(self):
        self.assertEqual(len(parse_report("右肺上叶结节，建议3个月后复查。", "impression").findings), 1)

    def test_multilesion_bilateral_or_unknown_location_are_retained_as_skips(self):
        for text in ("双肺上叶结节。", "左肺上叶和右肺下叶结节。", "左肺结节。",
                     "左肺上叶多发结节。", "左肺上叶结节与结节。", "左肺上叶两枚结节。", "左肺上叶3枚结节。",
                     "左肺上叶2个结节。", "左肺上叶10枚结节。"):
            with self.subTest(text=text):
                parsed = parse_report(text, "findings")
                self.assertFalse(parsed.findings)
                self.assertTrue(parsed.skipped)
                self.assertIn(parsed.skipped[0]["evidence"], text)

    def test_decimal_measurements_are_normalized_without_binary_float(self):
        for raw, expected in (("8 mm", "8"), ("0.8 cm", "8.0"), ("0.1 CM", "1.0"), ("1.25mm", "1.25")):
            with self.subTest(raw=raw):
                finding = parse_report(f"左肺上叶见 {raw} 结节。", "findings").findings[0]
                self.assertEqual(finding.measurement.normalized_mm, expected)

    def test_unsupported_measurements_are_not_silently_simplified(self):
        cases = ("约8mm", "8mm左右", "8×6mm", "8 x 6 mm", "8-10mm", "8mm至10mm",
                 "8mm、10mm", "-8mm", "0mm", ">8mm", "8cm²", "8mm/s", "原为8mm",
                 "8至10mm", "8到10mm", "8mm以下", "8e2mm", "1234567890123456789012345mm")
        for raw in cases:
            with self.subTest(raw=raw):
                finding = parse_report(f"左肺上叶见 {raw} 结节。", "findings").findings[0]
                self.assertIsNone(finding.measurement)
                self.assertTrue(finding.measurement_skip_reason)


if __name__ == "__main__":
    unittest.main()
