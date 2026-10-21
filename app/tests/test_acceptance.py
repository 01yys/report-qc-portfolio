from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import json

from run_acceptance import evaluate, validate_cases, run_frozen, DATASET
from web_app import acceptance_summary


def case(id="T", status=None, count=None):
    return {"id":id,"module":"measurement","group":"合成",
        "findings_text":"左肺上叶见6mm结节。","impression_text":"左肺上叶见16mm结节。",
        "expected_statuses":status,"expected_issue_count":count,"label_reason":"仅测试草案契约"}


class AcceptanceTests(unittest.TestCase):
    def test_frozen_dataset_is_complete_but_not_all_scored(self):
        data=run_frozen()
        self.assertEqual(data["total"],36)
        self.assertEqual(data["scored"],34)
        self.assertEqual(data["unscored"],2)
        self.assertEqual(data["matched"],34)
        self.assertEqual(data["mismatched"],0)
        self.assertEqual(sum(g["total"] for g in data["groups"].values()),36)
        self.assertEqual(data["provenance"]["model_requests"],0)
        self.assertIn("不是严格外部盲测",data["provenance"]["independence_caveat"])

    def test_unknown_labels_do_not_enter_match_denominator(self):
        data=evaluate([case()])
        self.assertEqual(data["scored"],0)
        self.assertEqual(data["unscored"],1)
        self.assertEqual(data["matched"],0)
        self.assertEqual(data["records"][0]["verdict"],"unscored")

    def test_unexpected_and_missed_candidates_are_separate(self):
        d=evaluate([case(status=["pass"],count=0)])
        self.assertEqual(d["groups"]["measurement"]["unexpected_positive_cases"],1)
        missing=case(status=["needs_review"],count=1)
        missing["impression_text"]="左肺上叶见0.6cm结节。"
        d=evaluate([missing])
        self.assertEqual(d["groups"]["measurement"]["missed_positive_cases"],1)
        self.assertEqual(d["matched"],0)

    def test_abstention_is_not_a_positive_or_pass(self):
        c=case(status=["unable_to_check"],count=0)
        c["findings_text"]="左肺上叶见约6mm结节。"
        d=evaluate([c])
        self.assertEqual(d["matched"],1)
        self.assertEqual(d["groups"]["measurement"]["comparable_case_count"],0)
        self.assertEqual(d["groups"]["measurement"]["actual_states"],{"unable_to_check":1})

    def test_status_and_count_mismatch_are_reported_not_relabelled(self):
        c=case(status=["pass"],count=0);original=deepcopy(c)
        d=evaluate([c])
        self.assertIn("state_mismatch",d["records"][0]["mismatch_reasons"])
        self.assertIn("issue_count_mismatch",d["records"][0]["mismatch_reasons"])
        self.assertEqual(c,original)

    def test_invalid_or_duplicate_labels_fail_early(self):
        for rows in ([case(),case()], [{**case(),"module":"unknown"}],
                     [case(status=["pass"],count=1)], [case(status=None,count=0)],
                     [case(status=[],count=0)], [case(status=["pass"],count=True)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):validate_cases(rows)

    def test_evidence_index_error_prevents_matching(self):
        from src.report_checker import check_report
        actual=check_report(case()["findings_text"],case()["impression_text"],["measurement"])
        actual["issues"][0]["findings_span"]=[0,1]
        with patch("run_acceptance.check_report",return_value=actual):
            data=evaluate([case(status=["needs_review"],count=1)])
        self.assertEqual(data["matched"],0)
        self.assertIn("invalid_evidence",data["records"][0]["mismatch_reasons"])

    def test_fingerprint_drift_blocks_execution(self):
        with TemporaryDirectory() as temp:
            p=Path(temp)/'changed.json';p.write_bytes(DATASET.read_bytes()+b' ')
            with self.assertRaises(ValueError):run_frozen(p)

    def test_web_summary_preserves_unscored_and_avoids_private_paths(self):
        data=acceptance_summary()
        self.assertEqual(data["unscored"],2)
        self.assertEqual(len(data["cases"]),36)
        # 旧21号源码指纹原样保留；v0.8中性化模块注释后字节已不同。
        self.assertFalse(data["snapshot_implementation_matches"])
        self.assertNotIn('/Users/',json.dumps(data))
        self.assertNotIn('api_key',json.dumps(data))

    def test_frozen_run_does_not_change_input_file(self):
        before=DATASET.read_bytes();run_frozen()
        self.assertEqual(DATASET.read_bytes(),before)

    def test_cli_returns_failure_on_scored_mismatch_but_not_unscored(self):
        import run_acceptance
        from contextlib import redirect_stdout
        from io import StringIO
        for mismatched, expected_exit in ((0,0),(1,1)):
            with self.subTest(mismatched=mismatched), patch("run_acceptance.run_frozen",return_value={"mismatched":mismatched,"unscored":2}):
                with redirect_stdout(StringIO()):
                    self.assertEqual(run_acceptance.main(),expected_exit)


if __name__ == '__main__': unittest.main()
