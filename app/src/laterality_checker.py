"""P0：影像所见与诊断意见的左右侧一致性检查。

本模块只使用 Python 标准库，不调用网络、API 或大模型。
它的职责是：对于可明确配对的“部位 + 病变”描述，检查两段文本的左右侧是否相反。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

try:  # 同时兼容旧的单文件演示导入和包导入。
    from .report_structure import ANATOMY_TERMS, FINDING_TERMS, extract_side, parse_report
except ImportError:
    from report_structure import ANATOMY_TERMS, FINDING_TERMS, extract_side, parse_report


# 词表/侧别/断言的唯一来源是共同结构化层。


def validate_inputs(findings_text: Any, impression_text: Any) -> str | None:
    """校验两个输入是否都是非空字符串；合法时返回 None。"""
    if not isinstance(findings_text, str) or not findings_text.strip():
        return "请补全影像所见模拟文本后重试。"
    if not isinstance(impression_text, str) or not impression_text.strip():
        return "请补全诊断意见模拟文本后重试。"
    return None


def split_segments(text: str) -> list[str]:
    """按常见句末符号切分文本，同时保留可展示的原文片段。"""
    return [segment.strip() for segment in re.split(r"[。；;\n]+", text) if segment.strip()]


def extract_mentions(segments: list[str]) -> list[dict[str, str]]:
    """提取拥有“单一侧别 + 单一部位 + 单一病变词”的可比较记录。

    若一句中包含多个候选部位或病变词，P0 不猜测它们之间的对应关系，直接跳过。
    """
    mentions: list[dict[str, str]] = []
    for segment in segments:
        for mention in parse_report(segment, "legacy").findings:
            mentions.append({"side": mention.side, "anatomy": mention.anatomy,
                             "finding": mention.observation, "evidence": mention.evidence,
                             "assertion": mention.assertion})
    return mentions


def make_match_key(mention: dict[str, str]) -> str:
    """生成忽略左右差异后的保守配对键。"""
    return f"{mention['anatomy']}|{mention['finding']}"


def find_conflicts(
    findings_mentions: list[dict[str, str]],
    impression_mentions: list[dict[str, str]],
) -> list[dict[str, str]]:
    """寻找同一配对键下左右侧相反的记录，并生成可展示的问题项。"""
    findings_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    impression_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)

    for mention in findings_mentions:
        findings_by_key[make_match_key(mention)].append(mention)
    for mention in impression_mentions:
        impression_by_key[make_match_key(mention)].append(mention)

    issues: list[dict[str, str]] = []
    # 按影像所见中的原始顺序展示，避免集合遍历让问题卡顺序随运行变化。
    for key in findings_by_key:
        if key not in impression_by_key:
            continue
        left_mentions = findings_by_key[key]
        right_mentions = impression_by_key[key]

        # 同一键在任一侧出现多次时，缺少可靠的进一步配对依据，P0 不猜测。
        if len(left_mentions) != 1 or len(right_mentions) != 1:
            continue

        findings_mention = left_mentions[0]
        impression_mention = right_mentions[0]
        if any(m.get("assertion", "present") != "present" for m in (findings_mention, impression_mention)):
            continue
        if findings_mention["side"] == impression_mention["side"]:
            continue

        anatomy = findings_mention["anatomy"]
        finding = findings_mention["finding"]
        issues.append(
            {
                "type": "laterality_inconsistency",
                "title": "部位矛盾：左右位置不一致",
                "findings_evidence": findings_mention["evidence"],
                "impression_evidence": impression_mention["evidence"],
                "suggestion": (
                    f"“{anatomy}{finding}”在影像所见与诊断意见中的左右位置不一致，"
                    "请人工核对原始影像及报告后修改。"
                ),
            }
        )
    return issues


def build_result(
    issues: list[dict[str, str]] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """按产品约定构造唯一的页面结果格式。"""
    if error_message:
        return {
            "status": "unable_to_check",
            "scope": ["laterality_consistency"],
            "summary": "本次未完成检查。",
            "issues": [],
            "error_message": error_message,
        }

    if issues:
        return {
            "status": "needs_review",
            "scope": ["laterality_consistency"],
            "summary": f"发现 {len(issues)} 条需人工核对项。",
            "issues": issues,
            "error_message": None,
        }

    return {
        "status": "pass",
        "scope": ["laterality_consistency"],
        "summary": "未发现当前检查范围内的左右位置矛盾。",
        "issues": [],
        "error_message": None,
    }


def check_laterality(findings_text: str, impression_text: str) -> dict[str, Any]:
    """P0 对外唯一入口：校验、抽取、配对、比较并返回结构化质控结果。"""
    validation_error = validate_inputs(findings_text, impression_text)
    if validation_error:
        return build_result(error_message=validation_error)

    try:
        findings_mentions = extract_mentions(split_segments(findings_text))
        impression_mentions = extract_mentions(split_segments(impression_text))
        issues = find_conflicts(findings_mentions, impression_mentions)
        return build_result(issues=issues)
    except Exception as error:  # 兜底：程序失败时绝不能伪装成“通过”。
        return build_result(error_message=f"检查程序发生异常：{error}")
