"""不启动服务器的接口路由/请求契约单元测试。"""

from io import BytesIO
import json
import unittest
from unittest.mock import Mock
from web_app import ReportQcHandler


def post(path, payload):
    handler = object.__new__(ReportQcHandler)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.path, handler.headers, handler.rfile = path, {"Content-Length": str(len(body))}, BytesIO(body)
    handler._send_json, handler.send_error = Mock(), Mock()
    handler.do_POST()
    return handler


class ApiTests(unittest.TestCase):
    def test_legacy_route_retains_single_module_contract(self):
        handler = post("/api/check", {"findings_text": "左肺上叶8mm结节。", "impression_text": "左肺上叶18mm结节。"})
        status, result = handler._send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(result["scope"], ["laterality_consistency"])
        self.assertFalse(result["issues"])

    def test_v1_route_runs_measurement_module(self):
        handler = post("/api/check-v1", {"findings_text": "左肺上叶8mm结节。", "impression_text": "左肺上叶18mm结节。"})
        status, result = handler._send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(result["issues"][0]["type"], "measurement_inconsistency")

    def test_nonobject_json_returns_bad_request(self):
        handler = post("/api/check-v1", [])
        self.assertEqual(handler._send_json.call_args.args[0], 400)


if __name__ == "__main__":
    unittest.main()
