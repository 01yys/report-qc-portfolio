"""一次性冻结三规则合成验收；不改标签，不请求模型，不写旧结果。

这是草案契约符合度，不是临床准确率或外部独立盲测。
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from src.report_checker import check_report, CHECKER_VERSION, SUPPORTED_MODULES

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "05_测试与评测/18_三规则冻结验收集-v0.1.json"
FROZEN_SHA256 = "8019ea592886331d2a4a7815d5e8aa2055f42f2e843a2d060e6ee436406ad374"


def validate_cases(cases):
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"] or case["id"] in ids:
            raise ValueError("案例须有不重复ID。")
        ids.add(case["id"])
        if case.get("module") not in SUPPORTED_MODULES:
            raise ValueError("未知模块。")
        if any(not isinstance(case.get(k), str) or not case[k].strip() for k in ("findings_text", "impression_text")):
            raise ValueError("验收输入必须是非空合成文本。")
        statuses, count = case.get("expected_statuses"), case.get("expected_issue_count")
        if statuses is None and count is None:
            continue
        if (not isinstance(statuses, list) or not statuses or len(statuses) != len(set(statuses)) or
            any(s not in ("pass", "needs_review", "unable_to_check") for s in statuses) or
            type(count) is not int or count < 0 or
            (count > 0 and statuses != ["needs_review"]) or (count == 0 and "needs_review" in statuses)):
            raise ValueError("预期状态/问题数不一致；未知须同时null。")


def evidence_valid(actual, findings, impression):
    for issue in actual["issues"]:
        for prefix, original in (("findings", findings), ("impression", impression)):
            span, evidence = issue.get(prefix + "_span"), issue.get(prefix + "_evidence")
            if (not isinstance(span, list) or len(span) != 2 or
                any(type(v) is not int for v in span) or not (0 <= span[0] < span[1] <= len(original)) or
                original[span[0]:span[1]] != evidence):
                return False
    return True


def evaluate(cases):
    validate_cases(cases)
    records, groups = [], {}
    for case in cases:
        actual = check_report(case["findings_text"], case["impression_text"], [case["module"]])
        valid = evidence_valid(actual, case["findings_text"], case["impression_text"])
        count = len(actual["issues"])
        labeled = case["expected_statuses"] is not None
        state_ok = labeled and actual["status"] in case["expected_statuses"]
        count_ok = labeled and count == case["expected_issue_count"]
        type_ok = all(i["module"] == case["module"] for i in actual["issues"])
        verdict = "unscored" if not labeled else "matched" if state_ok and count_ok and valid and type_ok and not actual.get("error_message") else "mismatched"
        reasons = []
        if labeled:
            if not state_ok: reasons.append("state_mismatch")
            if not count_ok: reasons.append("issue_count_mismatch")
            if not valid: reasons.append("invalid_evidence")
            if not type_ok: reasons.append("issue_kind_mismatch")
            if actual.get("error_message"): reasons.append("execution_error")
        record = {**case, "actual": actual, "verdict": verdict, "mismatch_reasons": reasons,
                  "evidence_valid": valid, "comparable_pairs": actual.get("modules", {}).get(case["module"], {}).get("comparable_pairs", 0)}
        records.append(record)
    for module in SUPPORTED_MODULES:
        selected = [r for r in records if r["module"] == module]
        scored = [r for r in selected if r["verdict"] != "unscored"]
        positives = [r for r in scored if r["expected_issue_count"] > 0]
        negatives = [r for r in scored if r["expected_issue_count"] == 0]
        groups[module] = {
            "total": len(selected), "scored": len(scored), "unscored": len(selected)-len(scored),
            "matched": sum(r["verdict"] == "matched" for r in scored),
            "mismatched": sum(r["verdict"] == "mismatched" for r in scored),
            "expected_positive_cases": len(positives),
            "missed_positive_cases": sum(not r["actual"]["issues"] for r in positives),
            "expected_no_issue_cases": len(negatives),
            "unexpected_positive_cases": sum(bool(r["actual"]["issues"]) for r in negatives),
            "comparable_case_count": sum(r["comparable_pairs"] > 0 for r in selected),
            "actual_states": dict(Counter(r["actual"]["status"] for r in selected)),
        }
    scored = [r for r in records if r["verdict"] != "unscored"]
    return {"total": len(records), "scored": len(scored), "unscored": len(records)-len(scored),
            "matched": sum(r["verdict"] == "matched" for r in scored),
            "mismatched": sum(r["verdict"] == "mismatched" for r in scored),
            "groups": groups, "records": records,
            "caveat": "分母为有预期草案的合成案例；符合状态、数量、类别与原文定位，不是临床准确率。覆盖和不提示须分开看。"}


def run_frozen(path=DATASET):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_SHA256:
        raise ValueError("冻结数据指纹已变，停止；不得根据结果改标签，需新版本评审。")
    data = json.loads(raw)
    result = evaluate(data["cases"])
    result["provenance"] = {"dataset_file": path.name, "dataset_sha256": FROZEN_SHA256,
        "dataset_version": data["version"], "label_status": data["label_status"],
        "checker_version": CHECKER_VERSION, "created_on": "2026-10-18",
        "independence_caveat": data["independence_caveat"],
        "model_requests": 0,
        "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in ("src/report_checker.py", "src/report_structure.py", "src/laterality_checker.py", "run_acceptance.py")}}
    return result


def main():
    result = run_frozen()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 未标注本就不计分；只有有标签的差异阻断这项回归验收。
    return 1 if result["mismatched"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
