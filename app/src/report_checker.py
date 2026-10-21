"""报告文本一致性规则入口：共享结构化记录、左右、尺寸与肯否检查。

不调用模型；无可比较内容时不伪装成通过。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .report_structure import ReportFinding, parse_report


CHECKER_VERSION = "v1.3.1-rules"
SUPPORTED_MODULES = ("laterality", "measurement", "assertion")


def check_report(findings_text: Any, impression_text: Any, modules: Any = None) -> dict[str, Any]:
    """默认运行已实现的三模块；保留输入快照和未覆盖原因。"""
    selected = list(SUPPORTED_MODULES) if modules is None else modules
    result: dict[str, Any] = {
        "run_id": str(uuid4()), "checked_at": datetime.now(timezone.utc).isoformat(),
        "checker_version": CHECKER_VERSION, "status": "unable_to_check",
        "summary": "本次未完成检查。", "issues": [], "error_message": None,
        "scope": selected if isinstance(selected, list) else [],
        "input_snapshot": {"findings_text": findings_text, "impression_text": impression_text},
        "findings": [], "skipped_segments": [], "modules": {},
        "llm_status": "not_implemented", "index_unit": "unicode_code_point",
    }
    if (not isinstance(selected, list) or not selected or
            any(not isinstance(m, str) or m not in SUPPORTED_MODULES for m in selected) or
            len(set(selected)) != len(selected)):
        result["error_message"] = "请选择不重复的已实现模块：laterality、measurement、assertion。"
        return result
    for source, text in (("findings", findings_text), ("impression", impression_text)):
        if not isinstance(text, str) or not text.strip():
            result["error_message"] = "请补全两段非空模拟文本后重试。"
            return result
        if len(text) > 20000:
            result["error_message"] = "每段模拟文本最多 20000 个字符。"
            return result
    try:
        parsed_f, parsed_i = parse_report(findings_text, "findings"), parse_report(impression_text, "impression")
        result["findings"] = [m.to_dict() for m in (*parsed_f.findings, *parsed_i.findings)]
        result["skipped_segments"] = [*parsed_f.skipped, *parsed_i.skipped]
        for module in selected:
            issues, compared, skipped = compare_module(parsed_f.findings, parsed_i.findings, module,
                                                       (*parsed_f.pairing_blockers, *parsed_i.pairing_blockers))
            result["issues"].extend(issues)
            result["modules"][module] = {
                "state": "completed", "comparable_pairs": compared, "skipped_pairs": skipped,
                "issue_count": len(issues),
                "coverage": "none" if compared == 0 else "partial" if skipped or result["skipped_segments"] else "supported_content",
            }
        result["issues"].sort(key=lambda issue: (issue["findings_span"][0], issue["module"]))
        for index, issue in enumerate(result["issues"], 1):
            issue["id"] = f"{result['run_id']}:{index}"
        total = sum(m["comparable_pairs"] for m in result["modules"].values())
        if result["issues"]:
            result.update(status="needs_review", summary=f"发现 {len(result['issues'])} 条需人工核对项。")
        elif total:
            result.update(status="pass", summary="在可明确比较的内容中未发现文本不一致；不代表整份报告正确。")
        else:
            result["summary"] = "没有可明确比较的内容；请查看跳过范围，不代表报告通过。"
        return result
    except Exception:
        result.update(status="unable_to_check", summary="本次未完成检查。", issues=[], findings=[],
                      skipped_segments=[], modules={}, error_message="规则检查异常，请重试；本次不作通过结论。")
        return result


def compare_module(
    findings: tuple[ReportFinding, ...], impression: tuple[ReportFinding, ...], module: str,
    pairing_blockers: tuple[dict[str, Any], ...] = (),
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]]:
    """唯一配对后再检查断言/测量，避免先过滤掉同类对象而造成假配对。"""
    by_f, by_i = defaultdict(list), defaultdict(list)
    for collection, mentions in ((by_f, findings), (by_i, impression)):
        for mention in mentions:
            key = (mention.anatomy, mention.observation)
            if module in ("measurement", "assertion"):
                key += (mention.side,)
            collection[key].append(mention)
    issues, compared, skipped = [], 0, []
    for key in dict.fromkeys([*by_f, *by_i]):
        left, right = by_f[key], by_i[key]
        reason = None
        blocker = next((item for item in pairing_blockers
                        if (item["anatomy"], item["observation"]) == key[:2] and
                        (module == "laterality" or item["side"] is None or item["side"] == key[2])), None)
        if blocker:
            reason = blocker["reason"]
        elif len(left) != 1 or len(right) != 1:
            reason = "对象无法唯一配对"
        elif left[0].comparison_skip_reason or right[0].comparison_skip_reason:
            reason = left[0].comparison_skip_reason or right[0].comparison_skip_reason
        elif module == "assertion" and any(m.assertion not in ("present", "absent") for m in (left[0], right[0])):
            reason = "不确定/未知断言不参与肯否冲突比较"
        elif module != "assertion" and (left[0].assertion != "present" or right[0].assertion != "present"):
            reason = "否定或不确定对象不参与左右/尺寸比较"
        elif module == "measurement" and (left[0].measurement is None or right[0].measurement is None):
            reason = "；".join(dict.fromkeys(m.measurement_skip_reason or "缺少单值长度" for m in (left[0], right[0]) if m.measurement is None))
        if reason:
            skipped.append({"key": "|".join(key), "reason": reason})
            continue
        a, b = left[0], right[0]
        compared += 1
        if module == "laterality":
            conflict = a.side != b.side
        elif module == "assertion":
            conflict = a.assertion != b.assertion
        else:
            conflict = Decimal(a.measurement.normalized_mm) != Decimal(b.measurement.normalized_mm)
        if not conflict:
            continue
        issue = {
            "module": module, "source": "rule", "review_state": "unreviewed",
            "type": {"laterality": "laterality_inconsistency", "measurement": "measurement_inconsistency",
                     "assertion": "assertion_inconsistency"}[module],
            "title": {"laterality": "左右位置不一致", "measurement": "尺寸数值不一致",
                      "assertion": "肯定与否定不一致"}[module],
            "findings_evidence": a.evidence, "impression_evidence": b.evidence,
            "findings_span": [a.span_start, a.span_end], "impression_span": [b.span_start, b.span_end],
            "suggestion": "两段存在文本不一致，请结合原始影像及报告人工核对；不能据此确定哪段正确。",
        }
        if module == "measurement":
            issue["normalized_mm"] = {"findings": a.measurement.normalized_mm, "impression": b.measurement.normalized_mm}
        elif module == "assertion":
            issue["assertions"] = {"findings": a.assertion, "impression": b.assertion}
        issues.append(issue)
    return issues, compared, skipped
