"""规则与模型候选的离线整理层；不读取凭据、不请求网络、不修改原结果。

模型包必须由服务端绑定原输入及版本，不能由浏览器声称其为真实模型结果。
去重仅比较同类别及相同原文区间，不能证明医学判断正确。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .semantic_review import validate_semantic_response, PROMPT_VERSION

FUSION_VERSION = "qc-fusion-v0.2"
EXECUTION_SOURCES = ("mock", "saved_live_replay", "live_api")
STATE_MESSAGES = {
    "disabled": "未启用AI辅助，当前仅有规则结果。",
    "not_configured": "AI未配置，没有取得模型结果；规则结果保留。",
    "not_completed": "AI尚未完成；规则结果保留。",
    "failed": "AI请求未完成；规则结果保留，不能视为AI无候选。",
    "invalid_result": "AI格式或原文证据校验失败；未接纳候选，规则结果保留。",
    "stale_result": "AI结果不属于当前输入版本，已忽略；不代表请求未计费。",
    "blocked_by_rule_error": "规则输入或执行有错误，不将模型候选整理为成功检查。",
    "invalid_packet": "模型结果包不符合融合约定；规则结果保留。",
    "configuration_mismatch": "模型任务版本与当前约定不同，未融合；规则结果保留。",
}
TITLES = {"laterality": "左右位置不一致候选", "measurement": "尺寸数值不一致候选",
          "assertion": "肯定与否定不一致候选", "semantic": "其他文本不一致候选"}
TYPES = {"laterality": "laterality_inconsistency", "measurement": "measurement_inconsistency",
         "assertion": "assertion_inconsistency", "semantic": "semantic_inconsistency"}
FAILURE_MESSAGES = {
    "timeout": "AI连接或读取超时；规则结果保留，是否计费不能由此推断。",
    "authentication_failed": "AI鉴权失败；规则结果保留，请在本机核对凭据。",
    "insufficient_balance": "AI服务返回余额不足；规则结果保留。",
    "rate_limited": "AI服务限流；规则结果保留，不自动重试。",
    "network_error": "AI网络连接未完成；规则结果保留。",
    "tls_error": "AI安全连接未完成；不关闭证书校验，规则结果保留。",
}


def _canonical_span(text: str, span: list[int]) -> tuple[int, int]:
    """只忽略首尾空白及句末分隔符，绝不删除内容词或改写证据。"""
    start, end = span
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in "。；;"):
        end -= 1
    return start, end


def _key(issue: dict, snapshot: dict) -> tuple:
    return (issue.get("model_kind", issue.get("kind", issue["module"])),
            _canonical_span(snapshot["findings_text"], issue["findings_span"]),
            _canonical_span(snapshot["impression_text"], issue["impression_span"]))


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) < min(a[1], b[1])


def _variant(issue: dict, source: str) -> dict:
    return {"source": source, "kind": issue.get("kind", issue["module"]),
            **{k: deepcopy(issue[k]) for k in (
                "findings_evidence", "impression_evidence", "findings_span", "impression_span")}}


def fuse_review_result(rule_result: dict, *, input_revision: int,
                       semantic_packet: Any = None) -> dict:
    """纯函数。semantic_packet=None表示AI未启用，非模型无候选。

    response_json为适配层输出或旧记录重建的受限JSON，始终重新校验；
    失败包的任意异常文本、Key、请求头与额外字段都不复制到输出。
    """
    if type(input_revision) is not int or input_revision < 0:
        raise ValueError("input_revision必须是非负整数。")
    if not isinstance(rule_result, dict) or not isinstance(rule_result.get("input_snapshot"), dict):
        raise ValueError("需要内部规则结果及输入快照。")
    result = deepcopy(rule_result)
    snapshot = result["input_snapshot"]
    result.update(fusion_version=FUSION_VERSION, input_revision=input_revision,
                  rule_status=result["status"], rule_summary=result["summary"],
                  rule_scope=deepcopy(result["scope"]), llm_status="disabled",
                  execution_completeness="rules_only")
    semantic = {"state": "disabled", "assessment": None, "execution_source": None,
                "message": STATE_MESSAGES["disabled"], "accepted_candidate_count": 0,
                "merged_with_rule_count": 0, "new_candidate_count": 0,
                "possible_related_pairs": [], "validation_errors": [],
                "failure_code": None,
                "caveat": "模型无候选不反驳规则；合并来源不代表医学确认。"}
    result["semantic_review"] = semantic
    for issue in result["issues"]:
        issue.update(kind=issue["module"], sources=["rule"],
                     evidence_variants=[_variant(issue, "rule")])

    def stop(state: str) -> dict:
        semantic.update(state=state, message=STATE_MESSAGES[state])
        result.update(llm_status=state, execution_completeness=
                      "blocked" if state == "blocked_by_rule_error" else "partial")
        return result

    if semantic_packet is None:
        return result
    if not isinstance(semantic_packet, dict):
        return stop("invalid_packet")
    result["scope"] = [*result["rule_scope"], "semantic_review"]
    if result.get("error_message"):
        return stop("blocked_by_rule_error")
    if semantic_packet.get("execution_source") not in EXECUTION_SOURCES:
        return stop("invalid_packet")
    semantic["execution_source"] = semantic_packet["execution_source"]
    if (type(semantic_packet.get("input_revision")) is not int or
            semantic_packet["input_revision"] != input_revision or
            semantic_packet.get("input_snapshot") != snapshot):
        return stop("stale_result")
    if semantic_packet.get("prompt_version") != PROMPT_VERSION:
        return stop("configuration_mismatch")
    status = semantic_packet.get("llm_status")
    if status == "not_called":
        return stop("not_configured" if semantic_packet.get("error_code") == "missing_api_key" else "not_completed")
    if status == "failed":
        stop("failed")
        code = semantic_packet.get("error_code")
        code = code if isinstance(code, str) else None
        semantic["failure_code"] = code if code in FAILURE_MESSAGES else "unknown_failure"
        semantic["message"] = FAILURE_MESSAGES.get(code, STATE_MESSAGES["failed"])
        return result
    if status == "invalid_result":
        return stop("invalid_result")
    if status != "completed":
        return stop("invalid_packet")

    validated = validate_semantic_response(semantic_packet.get("response_json"),
                                          snapshot["findings_text"], snapshot["impression_text"])
    semantic["validation_errors"] = validated["validation_errors"]
    if validated["validation_status"] != "accepted":
        return stop("invalid_result")
    candidates = validated["candidates"]
    if any(not any(c.isalnum() for c in candidate[field]) for candidate in candidates
           for field in ("findings_evidence", "impression_evidence")):
        semantic["validation_errors"] = ["evidence_without_text_or_number"]
        return stop("invalid_result")
    semantic.update(state="completed", assessment=validated["assessment"],
                    accepted_candidate_count=len(candidates),
                    prompt_version=validated["prompt_version"],
                    response_schema_version=validated["schema_version"])
    result.update(llm_status="completed", execution_completeness="both_completed")
    semantic["message"] = {
        "candidates_found": "AI提出文本不一致候选，仍需人工核对。",
        "no_candidate": "AI未提出候选，不代表整份报告正确，也不否定规则提示。",
        "undetermined": "AI表示依据或对应关系不足，无法判断；不是检查通过。",
    }[validated["assessment"]]

    for index, candidate in enumerate(candidates, 1):
        # 重新算出的区间用于匹配，传来的accepted标记或位置均不使用。
        candidate["module"] = candidate["kind"]
        key = _key(candidate, snapshot)
        same = next((issue for issue in result["issues"] if _key(issue, snapshot) == key), None)
        if same is not None:
            if "rule" in same["sources"]:
                semantic["merged_with_rule_count"] += 1
            if "llm" not in same["sources"]:
                same["sources"].append("llm")
            same["source"] = "rule_and_llm" if "rule" in same["sources"] else "llm"
            same["evidence_variants"].append(_variant(candidate, "llm"))
            same["llm_execution_source"] = semantic["execution_source"]
            continue
        # 格式和原文证据校验不证明类别正确。仅有模型依据的新候选归入待核对，
        # 原类别留档用于追溯和去重，不冒充已确认的规则类别。
        issue = {"id": f"{result['run_id']}:llm:{index}", "module": "semantic",
                 "kind": "semantic", "type": TYPES["semantic"],
                 "model_kind": candidate["kind"],
                 "classification_status": "unverified_model_category",
                 "title": "AI文本不一致候选（类别待核对）", "source": "llm", "sources": ["llm"],
                 "llm_execution_source": semantic["execution_source"],
                 "review_state": "unreviewed", "evidence_variants": [_variant(candidate, "llm")],
                 **{k: deepcopy(candidate[k]) for k in (
                     "findings_evidence", "impression_evidence", "findings_span", "impression_span", "suggestion")}}
        for other in result["issues"]:
            other_key = _key(other, snapshot)
            if key[0] == other_key[0] and _overlap(key[1], other_key[1]) and _overlap(key[2], other_key[2]):
                semantic["possible_related_pairs"].append({"issue_ids": [other["id"], issue["id"]],
                    "reason": "possibly_related", "message": "同类别证据部分重叠，未强合并，请人工核对是否同一问题。"})
        result["issues"].append(issue)
    semantic["new_candidate_count"] = sum(issue["sources"] == ["llm"] for issue in result["issues"])
    if result["issues"]:
        result.update(status="needs_review", summary=f"整理后有 {len(result['issues'])} 条待人工核对候选；请查看来源与覆盖范围。")
    # 无候选时继续保留规则自己的pass/unable_to_check及范围说明，不升级为全报告通过。
    return result
