"""融合逻辑契约测试，全部手写响应或本地函数，不调用模型。"""
import copy
import json
import unittest

from src.report_checker import check_report
from src.review_fusion import fuse_review_result
from src.semantic_review import SCHEMA_VERSION, PROMPT_VERSION


def packet(rule, assessment="candidates_found", *, evidence=None, kind="measurement", revision=3):
    snapshot = copy.deepcopy(rule["input_snapshot"])
    f, i = evidence or (snapshot["findings_text"], snapshot["impression_text"])
    return {"input_snapshot": snapshot, "input_revision": revision, "execution_source": "mock",
            "prompt_version": PROMPT_VERSION, "llm_status": "completed",
            "response_json": json.dumps({"schema_version": SCHEMA_VERSION, "assessment": assessment,
                "candidates": [{"kind": kind, "findings_evidence": f, "impression_evidence": i}]
                if assessment == "candidates_found" else []}, ensure_ascii=False)}


class ReviewFusionTests(unittest.TestCase):
    def setUp(self):
        self.rule = check_report("左肺上叶见8毫米结节。", "左肺上叶见18毫米结节。")

    def fuse(self, data=None, rule=None):
        return fuse_review_result(rule or self.rule, input_revision=3, semantic_packet=data)

    def test_disabled_is_not_a_model_no_candidate(self):
        result = self.fuse()
        self.assertEqual(result["semantic_review"]["state"], "disabled")
        self.assertIsNone(result["semantic_review"]["assessment"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["scope"], self.rule["scope"])
        self.assertEqual(result["llm_status"], "disabled")
        self.assertEqual(result["execution_completeness"], "rules_only")

    def test_same_evidence_with_sentence_punctuation_merges_sources(self):
        result = self.fuse(packet(self.rule))
        issue, = result["issues"]
        self.assertEqual(issue["id"], self.rule["issues"][0]["id"])
        self.assertEqual(issue["sources"], ["rule", "llm"])
        self.assertEqual(issue["source"], "rule_and_llm")
        self.assertEqual(issue["llm_execution_source"], "mock")
        self.assertEqual(len(issue["evidence_variants"]), 2)
        self.assertEqual(result["semantic_review"]["merged_with_rule_count"], 1)
        self.assertEqual(result["semantic_review"]["new_candidate_count"], 0)
        self.assertEqual(result["execution_completeness"], "both_completed")
        self.assertEqual(result["llm_status"], result["semantic_review"]["state"])

    def test_partial_overlap_is_not_forced_into_one_card(self):
        result = self.fuse(packet(self.rule, evidence=("8毫米", "18毫米")))
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual(result["semantic_review"]["new_candidate_count"], 1)
        pair, = result["semantic_review"]["possible_related_pairs"]
        self.assertEqual(pair["reason"], "possibly_related")

    def test_different_kind_does_not_merge_even_with_same_evidence(self):
        result = self.fuse(packet(self.rule, kind="semantic"))
        self.assertEqual(len(result["issues"]), 2)
        self.assertFalse(result["semantic_review"]["possible_related_pairs"])

    def test_model_only_category_is_unverified_not_a_product_assertion(self):
        rule = check_report("左肺上叶仅见一枚结节，直径8毫米。", "左肺上叶见三枚结节，直径8毫米。")
        result = self.fuse(packet(rule, kind="assertion"), rule)
        issue, = result["issues"]
        self.assertEqual(issue["module"], "semantic")
        self.assertEqual(issue["kind"], "semantic")
        self.assertEqual(issue["model_kind"], "assertion")
        self.assertEqual(issue["classification_status"], "unverified_model_category")
        self.assertEqual(issue["title"], "AI文本不一致候选（类别待核对）")
        self.assertEqual(issue["evidence_variants"][0]["kind"], "assertion")
        self.assertEqual(result["rule_status"], "unable_to_check")
        self.assertEqual(result["semantic_review"]["new_candidate_count"], 1)

    def test_fallback_does_not_merge_distinct_original_model_kinds(self):
        rule = check_report("左肾可见囊肿。", "左肾未见囊肿。")
        data = packet(rule, kind="assertion")
        raw = json.loads(data["response_json"])
        raw["candidates"].append({**raw["candidates"][0], "kind": "semantic"})
        data["response_json"] = json.dumps(raw)
        result = self.fuse(data, rule)
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual({i["model_kind"] for i in result["issues"]}, {"assertion", "semantic"})
        self.assertTrue(all(i["module"] == "semantic" for i in result["issues"]))
        self.assertFalse(result["semantic_review"]["possible_related_pairs"])

    def test_no_candidate_never_removes_rule_or_creates_disagreement(self):
        result = self.fuse(packet(self.rule, "no_candidate"))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["semantic_review"]["assessment"], "no_candidate")
        self.assertNotIn("disagreement", result)
        self.assertEqual(result["issues"][0]["sources"], ["rule"])

    def test_no_candidate_and_undetermined_preserve_unable_rule_status(self):
        rule = check_report("左肺上叶可能有结节。", "左肺上叶结节待排。")
        for assessment in ("no_candidate", "undetermined"):
            with self.subTest(assessment=assessment):
                result = self.fuse(packet(rule, assessment), rule)
                self.assertEqual(result["status"], "unable_to_check")
                self.assertEqual(result["semantic_review"]["assessment"], assessment)
                self.assertEqual(result["semantic_review"]["state"], "completed")

    def test_unknown_word_rule_can_retain_ai_candidate_without_claiming_rule_coverage(self):
        rule = check_report("左肾可见囊肿。", "左肾未见囊肿。")
        result = self.fuse(packet(rule, kind="semantic"), rule)
        issue, = result["issues"]
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["rule_status"], "unable_to_check")
        self.assertEqual(issue["sources"], ["llm"])
        self.assertEqual(result["modules"], rule["modules"])

    def test_failure_keeps_rule_and_does_not_copy_provider_error(self):
        data = packet(self.rule)
        data.update(llm_status="failed", error_code="timeout", error_message="DO_NOT_COPY_SECRET", authorization="DO_NOT_COPY_SECRET")
        result = self.fuse(data)
        self.assertEqual(result["semantic_review"]["state"], "failed")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIsNone(result["semantic_review"]["assessment"])
        self.assertNotIn("DO_NOT_COPY_SECRET", json.dumps(result))
        self.assertEqual(result["execution_completeness"], "partial")
        self.assertEqual(result["llm_status"], "failed")
        self.assertEqual(result["semantic_review"]["failure_code"], "timeout")
        data["error_code"] = "DO_NOT_COPY_SECRET"
        self.assertEqual(self.fuse(data)["semantic_review"]["failure_code"], "unknown_failure")
        for code in ([], {}, None):
            data["error_code"] = code
            self.assertEqual(self.fuse(data)["semantic_review"]["failure_code"], "unknown_failure")

    def test_missing_key_and_not_completed_are_distinct(self):
        data = packet(self.rule)
        data.update(llm_status="not_called", error_code="missing_api_key")
        self.assertEqual(self.fuse(data)["semantic_review"]["state"], "not_configured")
        data["error_code"] = "paid_call_not_enabled"
        self.assertEqual(self.fuse(data)["semantic_review"]["state"], "not_completed")

    def test_stale_revision_even_if_identical_text_is_rejected(self):
        data = packet(self.rule, revision=2)
        result = self.fuse(data)
        self.assertEqual(result["semantic_review"]["state"], "stale_result")
        self.assertEqual(len(result["issues"]), 1)

    def test_stale_text_even_if_same_revision_is_rejected(self):
        data = packet(self.rule)
        data["input_snapshot"]["impression_text"] = "左肺上叶见8毫米结节。"
        self.assertEqual(self.fuse(data)["semantic_review"]["state"], "stale_result")

    def test_prompt_version_drift_is_rejected(self):
        data = packet(self.rule)
        data["prompt_version"] = "other-version"
        self.assertEqual(self.fuse(data)["semantic_review"]["state"], "configuration_mismatch")

    def test_claiming_accepted_does_not_bypass_evidence_validation(self):
        data = packet(self.rule, evidence=("编造的句子", "18毫米"))
        data["validation_status"] = "accepted"
        result = self.fuse(data)
        self.assertEqual(result["semantic_review"]["state"], "invalid_result")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("evidence_missing_or_nonunique", result["semantic_review"]["validation_errors"])

    def test_malformed_json_and_punctuation_only_evidence_fail_closed(self):
        for content in ("not json", json.dumps({"schema_version":SCHEMA_VERSION,
                "assessment":"candidates_found", "candidates":[{"kind":"measurement",
                 "findings_evidence":"。", "impression_evidence":"。"}]})):
            data = packet(self.rule)
            data["response_json"] = content
            with self.subTest(content=content):
                self.assertEqual(self.fuse(data)["semantic_review"]["state"], "invalid_result")

    def test_duplicate_model_candidates_do_not_produce_duplicate_cards(self):
        data = packet(self.rule)
        raw = json.loads(data["response_json"])
        raw["candidates"] *= 2
        data["response_json"] = json.dumps(raw)
        result = self.fuse(data)
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["semantic_review"]["accepted_candidate_count"], 1)

    def test_unicode_offsets_and_evidence_remain_exact(self):
        rule = check_report("😀左肺上叶见8毫米结节。", "🧪左肺上叶见18毫米结节。")
        result = self.fuse(packet(rule), rule)
        issue, = result["issues"]
        for variant in issue["evidence_variants"]:
            self.assertEqual(rule["input_snapshot"]["findings_text"][slice(*variant["findings_span"])], variant["findings_evidence"])

    def test_rule_error_blocks_fusion(self):
        rule = check_report("", "左肺上叶结节。")
        result = self.fuse(packet(rule, "no_candidate"), rule)
        self.assertEqual(result["semantic_review"]["state"], "blocked_by_rule_error")
        self.assertEqual(result["status"], "unable_to_check")

    def test_inputs_and_nested_metadata_are_not_mutated(self):
        rule_copy = copy.deepcopy(self.rule)
        data = packet(self.rule)
        data_copy = copy.deepcopy(data)
        result = self.fuse(data)
        self.assertEqual(self.rule, rule_copy)
        self.assertEqual(data, data_copy)
        result["modules"]["measurement"]["coverage"] = "changed"
        self.assertEqual(self.rule, rule_copy)

    def test_provenance_is_explicit_and_not_guessed(self):
        for source in ("mock", "saved_live_replay", "live_api"):
            data = packet(self.rule)
            data["execution_source"] = source
            self.assertEqual(self.fuse(data)["issues"][0]["llm_execution_source"], source)
        data["execution_source"] = "unknown"
        self.assertEqual(self.fuse(data)["semantic_review"]["state"], "invalid_packet")

    def test_invalid_revision_or_packet_cannot_look_like_success(self):
        for revision in (True, -1, "3", None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                fuse_review_result(self.rule, input_revision=revision)
        for data in ([], "accepted", {"execution_source":"mock", "input_revision":True}):
            with self.subTest(data=data):
                self.assertIn(self.fuse(data)["semantic_review"]["state"], ("invalid_packet", "stale_result"))


if __name__ == "__main__":
    unittest.main()
