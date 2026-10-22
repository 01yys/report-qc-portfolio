"""新增知识助手的检索、引用、工具边界与请求额度关键验证。无外部模型调用。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from src.knowledge_assistant import KnowledgeLibrary, KnowledgeService, search_pubmed
from src.web_ai_service import RequestBudget


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = KnowledgeLibrary()

    def service(self, actions, maximum=3):
        caller = Mock(side_effect=[{"ok":True,"action":a,"usage":{"total_tokens":100}} for a in actions])
        budget = RequestBudget(self.root / "budget.jsonl", batch_date="2026-10-22", today=lambda:"2026-10-22", max_requests=maximum)
        service = KnowledgeService(library=self.library, api_key="synthetic", budget=budget,
                                   record_dir=self.root/"records", caller=caller, execution_source="mock")
        return service, caller, budget

    def test_retrieval_relevant_and_unknown(self):
        for query, expected in [("什么是次级肺小叶SPL？","K4"),("树芽征是否一定是感染？","K6"),
                                ("为什么比较既往影像？","K2"),("微结节分布有哪些类别？","K5"),("MIP有什么用途？","K7")]:
            with self.subTest(query=query):
                self.assertEqual(self.library.search(query)[0]["id"], expected)
        self.assertEqual(self.library.search("如何制作巧克力蛋糕"), [])

    def test_free_mode_never_calls_model(self):
        service, caller, budget = self.service([])
        result = service.query({"question":"什么是次级肺小叶？", "mode":"library"})
        self.assertEqual(result["status"], "sources_found")
        self.assertEqual(result["execution_source"], "local_retrieval")
        caller.assert_not_called()
        self.assertEqual(budget.count(), 0)

    def test_rag_requires_real_evidence_and_valid_citations(self):
        service, caller, budget = self.service([{"action":"answer","claims":[{"text":"资料说明次级肺小叶是CT可见的最小功能单位。","citations":["K4"]}]}])
        result = service.query({"question":"什么是SPL？", "mode":"rag","confirm_paid":True})
        self.assertEqual(result["status"], "answered")
        self.assertEqual(budget.count(), 1)
        self.assertEqual(result["claims"][0]["citations"], ["K4"])
        result = service.query({"question":"如何制作巧克力蛋糕", "mode":"rag","confirm_paid":True})
        self.assertEqual(result["status"], "no_evidence")
        self.assertEqual(caller.call_count, 1)

    def test_fabricated_citation_fails_without_retry(self):
        service, caller, budget = self.service([{"action":"answer","claims":[{"text":"无依据结论","citations":["K999"]}]}])
        result = service.query({"question":"树芽征是什么", "mode":"rag","confirm_paid":True})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["claims"], [])
        self.assertEqual(caller.call_count, 1)

    def test_agent_rewrites_once_then_answers(self):
        service, caller, budget = self.service([{"action":"search_library","query":"树芽征"},
            {"action":"answer","claims":[{"text":"分支状小叶中心结节并不总表示感染或误吸。","citations":["K6"]}]}])
        result = service.query({"question":"tree-in-bud是什么", "mode":"agent","confirm_paid":True})
        self.assertEqual(result["status"], "answered")
        self.assertEqual(caller.call_count, 2)
        self.assertEqual(budget.count(), 2)
        self.assertTrue(any(s.get("source")=="model" and s["action"]=="search_library" for s in result["trace"]))

    def test_agent_cannot_loop_or_call_arbitrary_tools(self):
        for actions in [[{"action":"open_url","query":"https://example.com"}],
                        [{"action":"search_library","query":"树芽征"},{"action":"search_library","query":"感染"}],
                        [None]]:
            with self.subTest(actions=actions):
                # 每个独立测试使用不同额度文件。
                self.root = self.root / str(len(list(self.root.iterdir())))
                service, caller, budget = self.service(actions)
                result = service.query({"question":"树芽征是什么", "mode":"agent","confirm_paid":True})
                self.assertEqual(result["status"], "failed")
                self.assertLessEqual(caller.call_count, 2)

    def test_patient_question_stops_before_network(self):
        service, caller, budget = self.service([])
        result = service.query({"question":"患者65岁有结节是不是癌", "mode":"agent","confirm_paid":True})
        self.assertEqual(result["status"], "out_of_scope")
        caller.assert_not_called()
        self.assertEqual(budget.count(),0)

    def test_paid_confirmation_and_expired_budget(self):
        service, caller, budget = self.service([])
        self.assertEqual(service.query({"question":"什么是SPL","mode":"rag"})["status"], "not_confirmed")
        budget.today = lambda:"2026-10-23"
        self.assertEqual(service.query({"question":"什么是SPL","mode":"rag","confirm_paid":True})["status"], "not_configured")
        caller.assert_not_called()

    def test_agent_budget_can_stop_second_request(self):
        service, caller, budget = self.service([{"action":"search_library","query":"树芽征"}], maximum=1)
        result=service.query({"question":"tree-in-bud是什么","mode":"agent","confirm_paid":True})
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(caller.call_count,1)

    def test_pubmed_returns_only_constructed_source_links(self):
        getter=Mock(side_effect=[{"esearchresult":{"idlist":["123"]}}, {"result":{"123":{"title":"Test paper", "pubdate":"2025", "authors":[{"name":"A"}],"fulljournalname":"Journal"}}}])
        result=search_pubmed("pulmonary nodules", getter=getter)
        self.assertEqual(result["items"][0]["url"], "https://pubmed.ncbi.nlm.nih.gov/123/")
        self.assertTrue(all(x.args[0].startswith("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/") for x in getter.call_args_list))
        self.assertEqual(getter.call_count,2)

    def test_agent_pubmed_returns_bibliography_without_medical_generation(self):
        service, caller, budget=self.service([{"action":"search_pubmed","query":"pulmonary nodules review"}])
        service._literature_search=Mock(return_value={"query":"pulmonary nodules review","items":[{"pmid":"123"}],"message":"目录"})
        result=service.query({"question":"查找肺结节相关文献","mode":"agent","confirm_paid":True})
        self.assertEqual(result["status"],"literature_found")
        self.assertEqual(result["claims"],[])
        self.assertEqual(caller.call_count,1)

    def test_corpus_license_and_source_metadata(self):
        for source in self.library.sources.values():
            self.assertEqual(source["license"], "CC BY 4.0")
            self.assertTrue(source["url"].startswith("https://link.springer.com/chapter/"))
            self.assertTrue(all(source[k] for k in ("authors","published","doi","license_url","changes")))


if __name__ == "__main__":
    unittest.main()
