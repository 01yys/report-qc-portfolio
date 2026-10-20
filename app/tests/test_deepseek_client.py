import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.deepseek_client import call_deepseek_once, NoRedirect
from src.semantic_review import SCHEMA_VERSION


class DeepSeekClientTests(unittest.TestCase):
    f, i = "左肺上叶结节8mm。", "左肺上叶结节18mm。"

    def mock_response(self, opener, payload):
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()

    def envelope(self, content=None):
        content = json.dumps({"schema_version": SCHEMA_VERSION, "assessment": "no_candidate", "candidates": []}) if content is None else content
        return {"model": "test-model-fixture", "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "secret": "hidden"}}

    @patch("src.deepseek_client.request.build_opener")
    def test_default_and_missing_key_never_request(self, opener):
        self.assertEqual(call_deepseek_once(self.f, self.i)["error_code"], "paid_call_not_enabled")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(call_deepseek_once(self.f, self.i, allow_paid=True)["error_code"], "missing_api_key")
        opener.assert_not_called()

    @patch("src.deepseek_client.request.build_opener")
    def test_invalid_or_long_input_never_requests(self, opener):
        for f in ["", None, "x" * 2001]:
            self.assertEqual(call_deepseek_once(f, self.i, allow_paid=True)["attempted_requests"], 0)
        opener.assert_not_called()

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_one_request_uses_json_disabled_thinking_and_limits(self, opener):
        self.mock_response(opener, self.envelope())
        result = call_deepseek_once(self.f, self.i, allow_paid=True)
        self.assertEqual(result["llm_status"], "completed")
        args = opener.return_value.open.call_args
        payload = json.loads(args.args[0].data)
        self.assertEqual(payload["max_tokens"], 1200)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(args.kwargs["timeout"], 30)
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(result["usage"]["total_tokens"], 120)
        self.assertNotIn("secret", result["usage"])
        self.assertNotIn("test-key-not-real", json.dumps(result))

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_provider_errors_are_sanitized_and_not_retried(self, opener):
        errors = [(HTTPError("url", 401, "secret", {}, io.BytesIO(b"secret")), "authentication_failed"),
                  (HTTPError("url", 402, "secret", {}, None), "insufficient_balance"),
                  (HTTPError("url", 429, "secret", {}, None), "rate_limited"),
                  (TimeoutError("secret"), "timeout"), (URLError("secret"), "network_error")]
        for exc, code in errors:
            opener.return_value.open.reset_mock()
            opener.return_value.open.side_effect = exc
            result = call_deepseek_once(self.f, self.i, allow_paid=True)
            self.assertEqual(result["error_code"], code)
            self.assertEqual(opener.return_value.open.call_count, 1)
            self.assertNotIn("secret", json.dumps(result))

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_empty_and_invalid_content_are_not_success(self, opener):
        for content in ["", "```json {} ```", None]:
            payload = self.envelope()
            payload["choices"][0]["message"]["content"] = content
            self.mock_response(opener, payload)
            self.assertEqual(call_deepseek_once(self.f, self.i, allow_paid=True)["llm_status"], "invalid_result")

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_truncated_and_malformed_envelopes_fail(self, opener):
        payload = self.envelope()
        payload["choices"][0]["finish_reason"] = "length"
        self.mock_response(opener, payload)
        self.assertEqual(call_deepseek_once(self.f, self.i, allow_paid=True)["error_code"], "incomplete_provider_output")
        self.assertEqual(call_deepseek_once(self.f, self.i, allow_paid=True)["usage"]["total_tokens"], 120)
        for payload in [{}, [], {"choices": []}]:
            self.mock_response(opener, payload)
            self.assertEqual(call_deepseek_once(self.f, self.i, allow_paid=True)["error_code"], "invalid_provider_response")

    def test_redirect_is_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_oversized_or_non_json_provider_response_fails(self, opener):
        for raw, expected in [(b"x" * 1048577, "provider_response_too_large"), (b"not json", "provider_response_not_json")]:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw
            self.assertEqual(call_deepseek_once(self.f, self.i, allow_paid=True)["error_code"], expected)

    @patch("src.deepseek_client.request.build_opener")
    def test_bad_key_characters_blocked_before_request_and_never_leaked(self, opener):
        for value in ["本机假密钥", "test\u200bkey", "test\nkey", "Bearer test", "test\x7fkey"]:
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": value}):
                result = call_deepseek_once(self.f, self.i, allow_paid=True)
            self.assertEqual(result["error_code"], "invalid_api_key_format")
            self.assertEqual(result["failure_stage"], "credential_precheck")
            self.assertEqual(result["attempted_requests"], 0)
            self.assertFalse(result["response_received"])
            self.assertNotIn(value, json.dumps(result, ensure_ascii=False))
        opener.assert_not_called()

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_transport_value_error_is_not_provider_response_error(self, opener):
        for exception, code in [(ValueError("secret"), "request_setup_failed"),
                                (UnicodeEncodeError("ascii", "本机假密钥", 0, 1, "secret"), "request_encoding_error")]:
            opener.return_value.open.side_effect = exception
            result = call_deepseek_once(self.f, self.i, allow_paid=True)
            self.assertEqual(result["error_code"], code)
            self.assertEqual(result["failure_stage"], "transport")
            self.assertFalse(result["response_received"])
            self.assertNotIn("secret", json.dumps(result))

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_decode_and_envelope_stages_distinguish_received_response(self, opener):
        for raw, code, stage in [(b"\xff", "provider_response_not_utf8", "response_decode"),
                                  (b"<html>test</html>", "provider_response_not_json", "response_decode"),
                                  (b"{}", "invalid_provider_response", "provider_envelope")]:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.getcode.return_value = 200
            response.read.return_value = raw
            result = call_deepseek_once(self.f, self.i, allow_paid=True)
            self.assertEqual(result["error_code"], code)
            self.assertEqual(result["failure_stage"], stage)
            self.assertTrue(result["response_received"])
            self.assertEqual(result["http_status"], 200)

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_http_failure_contains_only_status_not_raw_error(self, opener):
        opener.return_value.open.side_effect = HTTPError("url", 503, "do-not-store-this", {}, None)
        result = call_deepseek_once(self.f, self.i, allow_paid=True)
        self.assertEqual(result["failure_stage"], "http_status")
        self.assertEqual(result["http_status"], 503)
        self.assertTrue(result["response_received"])
        self.assertNotIn("do-not-store-this", json.dumps(result))

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key-not-real"})
    @patch("src.deepseek_client.request.build_opener")
    def test_tls_protocol_io_errors_are_sanitized_and_not_retried(self, opener):
        import ssl
        from http.client import IncompleteRead
        for exception, code in [(URLError(ssl.SSLError("secret")), "tls_error"),
                                (ssl.SSLError("secret"), "tls_error"),
                                (IncompleteRead(b"secret"), "http_protocol_error"),
                                (ConnectionResetError("secret"), "network_io_error")]:
            opener.return_value.open.reset_mock()
            opener.return_value.open.side_effect = exception
            result = call_deepseek_once(self.f, self.i, allow_paid=True)
            self.assertEqual(result["error_code"], code)
            self.assertEqual(opener.return_value.open.call_count, 1)
            self.assertNotIn("secret", json.dumps(result))
