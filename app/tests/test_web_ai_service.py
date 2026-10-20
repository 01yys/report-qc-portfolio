from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import json
import unittest
from unittest.mock import Mock, patch

from src.web_ai_service import RequestBudget, WebAIService, BATCH_DATE
from src.semantic_review import SCHEMA_VERSION, validate_semantic_response
from src.deepseek_client import call_deepseek_once
from web_app import ReportQcHandler
import web_app

F, I = "左肺上叶见8毫米结节。", "左肺上叶见18毫米结节。"


def response(assessment="candidates_found", bad=False):
    raw = json.dumps({"schema_version": SCHEMA_VERSION, "assessment": assessment,
        "candidates": [{"kind": "measurement", "findings_evidence": "不存在原文" if bad else F,
                        "impression_evidence": I}] if assessment == "candidates_found" else []})
    return {"llm_status": "completed", "semantic_validation": validate_semantic_response(raw, F, I),
            "attempted_requests": 1, "usage": {"total_tokens": 100}, "elapsed_ms": 25}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.budget = RequestBudget(self.root / "budget.jsonl", today=lambda: BATCH_DATE)
        self.caller = Mock(return_value=response())
        self.svc = WebAIService(api_key="test-key-not-real", budget=self.budget,
            record_dir=self.root / "records", caller=self.caller, execution_source="mock")
        self.payload = {"findings_text": F, "impression_text": I, "input_revision": 3}

    def check(self, **kwargs):
        return self.svc.check({**self.payload, **kwargs})

    def review(self, run, **kwargs):
        return self.svc.review({"ai_ticket": run["ai_ticket"], "input_revision": run["input_revision"],
                                "confirm_synthetic": True, "confirm_paid": True, **kwargs})

    def test_default_never_reads_env_or_calls(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "ignored-secret"}):
            s = WebAIService()
            self.assertFalse(s.capabilities()["ready"])
            r = s.check(self.payload)
            self.assertEqual(r["semantic_review"]["state"], "disabled")
            self.assertEqual(s.review({"ai_ticket": r["ai_ticket"], "input_revision": 3,
                "confirm_synthetic": True, "confirm_paid": True})[0], 503)
        self.caller.assert_not_called()

    def test_same_run_is_enriched_and_cached_without_second_call(self):
        run = self.check()
        status, out = self.review(run)
        self.assertEqual(status, 200)
        self.assertEqual(out["run_id"], run["run_id"])
        self.assertEqual(len(out["issues"]), 1)
        self.assertEqual(out["issues"][0]["id"], run["issues"][0]["id"])
        self.assertEqual(out["issues"][0]["sources"], ["rule", "llm"])
        self.assertEqual(out["semantic_review"]["execution_source"], "mock")
        self.assertEqual(self.review(run)[1], out)
        self.assertEqual(self.caller.call_count, 1)
        self.assertEqual(self.budget.count(), 1)
        saved = next((self.root / "records").glob("*.json")).read_text()
        self.assertNotIn("test-key-not-real", saved)
        self.assertNotIn("csrf", saved)

    def test_no_candidate_and_undetermined_are_not_failures(self):
        for assessment in ("no_candidate", "undetermined"):
            self.caller.return_value = response(assessment)
            out = self.review(self.check())[1]
            self.assertEqual(out["semantic_review"]["assessment"], assessment)
            self.assertEqual(len(out["issues"]), 1)
            self.assertEqual(out["issues"][0]["sources"], ["rule"])

    def test_provider_failure_no_retry_rule_retained(self):
        self.caller.return_value = {"llm_status": "failed", "error_code": "timeout"}
        out = self.review(self.check())[1]
        self.assertEqual(out["semantic_review"]["state"], "failed")
        self.assertEqual(len(out["issues"]), 1)
        self.assertEqual(self.caller.call_count, 1)

    def test_invalid_evidence_is_rejected_again(self):
        self.caller.return_value = response(bad=True)
        out = self.review(self.check())[1]
        self.assertEqual(out["semantic_review"]["state"], "invalid_result")
        self.assertEqual(out["issues"][0]["sources"], ["rule"])

    def test_consent_required_without_request_or_reservation(self):
        for flag in ("confirm_synthetic", "confirm_paid"):
            self.assertEqual(self.review(self.check(), **{flag: False})[0], 400)
        self.assertEqual(self.budget.count(), 0)
        self.caller.assert_not_called()

    def test_bad_revision_ticket_and_input_are_free_rejections(self):
        run = self.check()
        for data in ({"input_revision": 4}, {"input_revision": True}, {"ai_ticket": "unknown"}, {"ai_ticket": []}):
            self.assertEqual(self.review(run, **data)[0], 409)
        self.assertNotIn("ai_ticket", self.check(findings_text=""))
        with self.assertRaises(ValueError): self.check(input_revision=True)
        self.caller.assert_not_called()

    def test_expired_ticket_and_restart_do_not_call(self):
        run = self.check()
        with patch.object(self.svc, "_clock", return_value=self.svc._clock() + 1201):
            self.assertEqual(self.review(run)[0], 409)
        other = WebAIService()
        self.assertEqual(other.review({"ai_ticket": run["ai_ticket"], "input_revision": 3,
                                      "confirm_synthetic": True, "confirm_paid": True})[0], 409)
        self.caller.assert_not_called()

    def test_ai_input_limit_rejects_without_reservation(self):
        run = self.check(findings_text="左肺上叶见结节。" + "x" * 2001)
        self.assertEqual(self.review(run)[0], 400)
        self.assertEqual(self.budget.count(), 0)

    def test_double_click_and_parallel_runs_allow_only_one_inflight(self):
        entered, release = Event(), Event()
        def wait(*args, **kwargs):
            entered.set(); release.wait(3); return response()
        self.caller.side_effect = wait
        a, b = self.check(), self.check()
        thread = Thread(target=lambda: self.review(a)); thread.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertEqual(self.review(a)[0], 409)
            self.assertEqual(self.review(b)[0], 409)
        finally:
            release.set(); thread.join(3)
        self.assertEqual(self.caller.call_count, 1)

    def test_ten_reservations_across_instances_and_restart(self):
        for _ in range(10): self.assertEqual(self.review(self.check())[0], 200)
        self.assertEqual(self.review(self.check())[0], 503)
        self.assertEqual(RequestBudget(self.budget.path).count(), 10)
        self.assertEqual(self.caller.call_count, 10)

    def test_malformed_ledger_blocks_and_is_not_rewritten(self):
        self.budget.path.write_text("not-json\n")
        self.assertFalse(self.svc.capabilities()["ready"])
        self.assertEqual(self.review(self.check())[0], 503)
        self.assertEqual(self.budget.path.read_text(), "not-json\n")
        self.caller.assert_not_called()

    def test_batch_expiry_blocks_new_requests(self):
        self.budget.today = lambda: "2026-10-19"
        self.assertFalse(self.svc.capabilities()["ready"])
        self.assertEqual(self.review(self.check())[0], 503)
        self.caller.assert_not_called()

    def test_write_failure_blocks_subsequent_calls(self):
        self.svc._record_dir = self.root / "file"
        self.svc._record_dir.write_text("block")
        out = self.review(self.check())[1]
        self.assertIn("persistence_warning", out)
        self.assertEqual(self.review(self.check())[0], 503)
        self.assertEqual(self.caller.call_count, 1)

    def test_unknown_exception_does_not_leak_or_retry(self):
        self.caller.side_effect = RuntimeError("fake-secret-should-not-leak")
        out = self.review(self.check())[1]
        self.assertEqual(out["semantic_review"]["state"], "failed")
        self.assertNotIn("fake-secret", json.dumps(out))
        self.assertEqual(self.caller.call_count, 1)

    def test_two_budget_instances_share_atomic_cap(self):
        a, b = self.budget, RequestBudget(self.budget.path, today=lambda: BATCH_DATE)
        errors = []
        def reserve(budget):
            for _ in range(8):
                try: budget.reserve("run")
                except ValueError: errors.append(True)
        threads = [Thread(target=reserve, args=(x,)) for x in (a,b)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(a.count(), 10)
        self.assertEqual(len(errors), 6)

    def test_http_origin_host_token_guards(self):
        def post(path, headers, payload):
            h = object.__new__(ReportQcHandler)
            body = json.dumps(payload).encode()
            h.path, h.headers, h.rfile = path, {"Content-Length":str(len(body)), **headers}, BytesIO(body)
            h.server = Mock(server_port=8765, ai_service=self.svc)
            h._send_json, h.send_error = Mock(), Mock()
            h.do_POST()
            return h._send_json.call_args.args
        headers = {"Host":"127.0.0.1:8765", "Origin":"http://127.0.0.1:8765",
                   "Content-Type":"application/json", "X-QC-Token":self.svc.csrf}
        for key, value in (("Host","evil.test"), ("Origin","http://evil.test"), ("Content-Type","text/plain"), ("X-QC-Token","bad")):
            self.assertEqual(post("/api/ai-review", {**headers,key:value}, {})[0], 403)
        self.assertEqual(post("/api/check-v2", headers, self.payload)[0], 200)
        self.caller.assert_not_called()

    @patch("src.deepseek_client.request.build_opener")
    def test_explicit_empty_key_does_not_fall_back_to_environment(self, opener):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY":"unrelated-secret"}):
            result = call_deepseek_once(F, I, allow_paid=True, api_key="")
        self.assertEqual(result["error_code"], "missing_api_key")
        opener.assert_not_called()


class ExplicitBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def budget(self, **kwargs):
        return RequestBudget(self.root / "quota.jsonl", batch_date="2026-10-20",
                             max_requests=3, today=lambda: "2026-10-20", **kwargs)

    def test_custom_batch_cap_restart_and_record_names(self):
        budget = self.budget()
        caller = Mock(return_value=response())
        service = WebAIService(api_key="not-real", budget=budget,
            record_dir=self.root / "records", caller=caller, execution_source="mock")
        self.assertEqual(service.capabilities()["batch_date"], "2026-10-20")
        self.assertEqual(service.capabilities()["max_requests"], 3)
        for revision in range(3):
            run = service.check({"findings_text": F, "impression_text": I, "input_revision": revision})
            self.assertEqual(service.review({"ai_ticket": run["ai_ticket"], "input_revision": revision,
                "confirm_synthetic": True, "confirm_paid": True})[0], 200)
        self.assertFalse(service.capabilities()["ready"])
        self.assertEqual(self.budget().count(), 3)
        with self.assertRaises(ValueError): self.budget().reserve("extra")
        self.assertEqual(caller.call_count, 3)
        self.assertEqual(len(list((self.root / "records").glob("网页AI-2026-10-20-*.json"))), 3)
        rows = [json.loads(line) for line in budget.path.read_text().splitlines()]
        self.assertTrue(all(row["max_requests"] == 3 for row in rows))

    def test_strict_configuration_and_ledger_mismatch(self):
        for day in ("2026-1-2", "2026-02-30", "today", None, 123):
            with self.subTest(day=day), self.assertRaises(ValueError):
                RequestBudget(self.root / "x", batch_date=day)
        for maximum in (True, False, 0, -1, 11, 3.0, "3", None):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                RequestBudget(self.root / "x", max_requests=maximum)
        budget = self.budget()
        budget.reserve("one")
        original = budget.path.read_bytes()
        for day, cap in (("2026-10-20", 2), ("2026-10-20", 4), ("2026-10-21", 3)):
            changed = RequestBudget(budget.path, batch_date=day, max_requests=cap, today=lambda: day)
            with self.assertRaises(ValueError): changed.count()
            with self.assertRaises(ValueError): changed.reserve("blocked")
        self.assertEqual(budget.path.read_bytes(), original)

    def test_legacy_ledger_implies_ten_not_new_three(self):
        path = self.root / "quota.jsonl"
        path.write_text(json.dumps({"batch_date": BATCH_DATE, "request_id": "old"}) + "\n")
        self.assertEqual(RequestBudget(path).count(), 1)
        with self.assertRaises(ValueError):
            RequestBudget(path, max_requests=3).count()

    def test_cli_bad_or_expired_args_never_bind_or_prompt(self):
        cases = (["--ai"], ["--ai-date", "2026-10-20"],
                 ["--ai", "--ai-date", "2026-10-20"],
                 ["--ai", "--ai-date", "2026-10-18", "--ai-max-requests", "3"],
                 ["--ai", "--ai-date", "2026-10-20", "--ai-max-requests", "11"])
        for args in cases:
            with self.subTest(args=args), patch("sys.argv", ["web_app.py", *args]), \
                 patch("web_app.current_batch_date", return_value="2026-10-20"), \
                 patch("web_app.EVALUATION_DIRECTORY", self.root), \
                 patch("web_app.ThreadingHTTPServer") as server, \
                 patch("web_app.getpass.getpass") as key, patch("builtins.input") as consent:
                with self.assertRaises(SystemExit): web_app.main()
                server.assert_not_called(); key.assert_not_called(); consent.assert_not_called()

    def test_cli_explicit_limit_confirmed_before_hidden_key(self):
        server = Mock()
        events = []
        with patch("sys.argv", ["web_app.py", "--ai", "--ai-date", "2026-10-20", "--ai-max-requests", "3"]), \
             patch("web_app.EVALUATION_DIRECTORY", self.root), \
             patch("web_app.current_batch_date", return_value="2026-10-20"), \
             patch("web_app.ThreadingHTTPServer", return_value=server), \
             patch("web_app.sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=lambda prompt: events.append("confirm") or "3"), \
             patch("web_app.getpass.getpass", side_effect=lambda prompt: events.append("key") or "sk-test-placeholder"), \
             patch("web_app.valid_key_transport_format", return_value=True):
            web_app.main()
        self.assertEqual(events, ["confirm", "key"])
        self.assertEqual(server.ai_service.capabilities()["max_requests"], 3)
        self.assertEqual(server.ai_service.capabilities()["batch_date"], "2026-10-20")
