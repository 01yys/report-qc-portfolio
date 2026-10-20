"""产品材料入口：限定允许清单、来源和安全展示，不开启全盘浏览。"""

import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from web_app import ReportQcHandler, evaluation_summary, DOCUMENTS, ASSETS


def handler(path):
    instance = object.__new__(ReportQcHandler)
    instance.path = path
    instance._send_content, instance._send_json, instance.send_error = Mock(), Mock(), Mock()
    return instance


class ProductMaterialsTests(unittest.TestCase):
    def test_document_routes_exist_and_do_not_expose_entire_workspace(self):
        for path in DOCUMENTS:
            with self.subTest(path=path):
                instance = handler(path)
                instance.do_GET()
                self.assertTrue(instance._send_content.called)
        instance = handler("/docs/../../01_个人档案与规划")
        instance.do_GET()
        self.assertEqual(instance.send_error.call_args.args[0], 404)

    def test_markdown_html_is_escaped_not_executed(self):
        instance = handler("/docs/prd")
        with patch.object(Path, "read_text", return_value="<script>alert(1)</script>"):
            instance.do_GET()
        content = instance._send_content.call_args.args[0].decode()
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<script>", content)

    def test_diagram_assets_are_svg_and_source_backed(self):
        for path in ASSETS:
            with self.subTest(path=path):
                instance = handler(path)
                instance.do_GET()
                content, content_type = instance._send_content.call_args.args
                self.assertEqual(content_type, "image/svg+xml")
                self.assertIn(b"<svg", content)

    def test_evaluation_summary_is_limited_to_real_snapshot_fields(self):
        result = evaluation_summary()
        self.assertEqual([r["matched_count"] for r in result["reports"]], [17, 20])
        self.assertNotIn("provenance", result["reports"][0])
        self.assertIn("不是临床准确率", result["caveat"])

    def test_missing_evaluation_is_not_faked_as_zero_or_success(self):
        instance = handler("/api/evaluation")
        with patch("web_app.evaluation_summary", side_effect=OSError("synthetic")):
            instance.do_GET()
        self.assertEqual(instance._send_json.call_args.args[0], 503)


if __name__ == "__main__":
    unittest.main()
