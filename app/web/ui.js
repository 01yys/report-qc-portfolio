import { moduleLabels } from "/samples.js";
import { sourceLabel, comparisonDetails, coverageReason, coverageObject } from "/review.js";

export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function highlighted(text, module) {
  const pattern = module === "laterality" ? /左|右/g : module === "measurement" ? /\d+(?:\.\d+)?\s*(?:mm|cm|毫米|厘米)/gi : /不存在|未见|未发现|没有|无|炎性改变|结节/g;
  let output = "", cursor = 0;
  for (const match of text.matchAll(pattern)) {
    output += escapeHtml(text.slice(cursor, match.index)) + `<mark>${escapeHtml(match[0])}</mark>`;
    cursor = match.index + match[0].length;
  }
  return output + escapeHtml(text.slice(cursor));
}

export function renderCoverage(result) {
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : "未知";
  const coverageLabels = { none: "无可比较内容", partial: "部分内容可比较", supported_content: "已比较本版支持内容" };
  const states = Object.entries(result.modules || {}).map(([key, value]) => {
    const complete = !result.error_message && value?.state === "completed";
    return `<span>${escapeHtml(moduleLabels[key] || "未识别模块")}：${complete ? "已执行" : "未完成或状态未知"}；比较 ${complete ? count(value.comparable_pairs) : "未知"} 组 / ${complete ? count(value.issue_count) : "未知"} 项；${escapeHtml(complete ? coverageLabels[value.coverage] || "范围未知" : "范围未知")}；规则跳过说明 ${complete && Array.isArray(value.skipped_pairs) ? value.skipped_pairs.length : "未知"} 条</span>`;
  }).join("");
  const reasons = Object.entries(result.modules || {}).flatMap(([key, value]) =>
    (Array.isArray(value?.skipped_pairs) ? value.skipped_pairs : []).map(pair =>
      `${moduleLabels[key] || "未识别模块"} · ${coverageObject(pair.key)}：${coverageReason(pair.reason)}`));
  const segments = Array.isArray(result.skipped_segments) ? result.skipped_segments : [];
  reasons.push(...segments.map(segment => {
    const source = { findings: "影像所见", impression: "诊断意见" }[segment.source];
    return `未覆盖原文${source ? `（${source}）` : ""}“${segment.evidence}”：${coverageReason(segment.reason)}`;
  }));
  return `<div class="coverage">
    <details><summary>查看规则比较范围与跳过说明</summary><div class="module-states">${states || "规则范围未知"}</div>
      <p>未覆盖原文说明 ${Array.isArray(result.skipped_segments) ? segments.length : "未知"} 条。两类说明可能涉及同一段内容，不能相加当作独立问题数。技术原因保留在导出记录中。</p>
      <p>以上比较数仅属于规则，不包含AI候选。规则已执行不等于有内容可比较；未发现问题不代表整份报告正确；AI执行状态另行显示。</p>
      <ul>${reasons.slice(0, 20).map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
      ${reasons.length > 20 ? "<p>更多原因请查看导出结果。</p>" : ""}</details></div>`;
}

export function renderResult(result, previous, feedback) {
  const details = comparisonDetails(previous, result);
  const count = value => value === null ? "未知" : value;
  const statusLabels = { pass: "可比较内容中未发现不一致", needs_review: "有待核对项", unable_to_check: "无法明确比较", error: "检查未完成", unknown: "未知" };
  const comparison = previous ? details ?
    `<p class="comparison">本报告复检：提示项 ${count(details.previous.issue_count)} → ${count(details.current.issue_count)}；规则状态：${statusLabels[details.previous.rule_status]} → ${statusLabels[details.current.rule_status]}。<br>规则可比较 ${count(details.previous.comparable_pairs)} → ${count(details.current.comparable_pairs)} 组；规则跳过说明 ${count(details.previous.skipped_pair_count)} → ${count(details.current.skipped_pair_count)} 条；未覆盖原文说明 ${count(details.previous.skipped_segment_count)} → ${count(details.current.skipped_segment_count)} 条。<br>${details.coverage_changed ? "覆盖有变化，不能只看问题数判断改进；提示数量不代表报告已改善。" : "覆盖记录未变，提示数量不代表报告已改善。"}仅同任务、版本、范围及AI模式可比，不代表医学正确性。</p>` :
    `<p class="comparison">任务、规则版本、规则范围或AI完成状态不同或缺失，本次不作前后问题数比较。</p>` : "";
  if (!result.issues.length) {
    const heading = result.error_message ? "检查未完成" : result.status === "pass" ? "已选规则范围内无修改建议" : "没有可明确比较的内容";
    return `${comparison}<div class="status-message ${result.error_message ? "status-error" : ""}"><h3>${heading}</h3><p>${escapeHtml(result.error_message || result.summary)}</p></div>${renderCoverage(result)}`;
  }
  return `${comparison}<p class="result-summary"><span class="issue-count">${result.issues.length}</span>条需人工核对项</p>
    <div class="issue-list">${result.issues.map((issue, index) => {
      const state = feedback[issue.id];
      return `<article class="issue-card" data-issue="${index}">
        <div class="issue-heading"><span class="issue-number">${index + 1}</span><h3>${escapeHtml(issue.title)}</h3><span class="source-label">${sourceLabel(issue)}${issue.llm_execution_source === "mock" ? " · 模拟" : ""}</span></div>
        <dl><div class="evidence-row"><dt>影像所见</dt><dd>${highlighted(issue.findings_evidence, issue.module)}</dd></div>
        <div class="evidence-row"><dt>诊断意见</dt><dd>${highlighted(issue.impression_evidence, issue.module)}</dd></div></dl>
        <div class="issue-footer"><p class="suggestion"><strong>建议：</strong>请结合原始影像和报告人工核对。</p>
          <button class="button feedback-button ${state === "reviewed" ? "selected" : ""}" data-feedback="reviewed" data-index="${index}" aria-pressed="${state === "reviewed"}">已核对</button>
          <button class="button feedback-button ${state === "inaccurate" ? "selected" : ""}" data-feedback="inaccurate" data-index="${index}" aria-pressed="${state === "inaccurate"}">提示不准确</button></div>
        ${(result.semantic_review?.possible_related_pairs || []).some(p => p.issue_ids.includes(issue.id)) ? '<p class="feedback-note">与另一提示证据部分重叠，可能相关；未强行合并，请人工核对。</p>' : ""}
        ${state ? `<p class="feedback-note">已记录${state === "reviewed" ? "核对" : "不准确"}反馈，报告未自动修改；修改后需重新检查。</p>` : ""}
      </article>`;
    }).join("")}</div>${renderCoverage(result)}`;
}

export function emptyState(message) {
  return `<div class="empty-state"><svg class="icon"><use href="#icon-report"/></svg><p>${escapeHtml(message)}</p><small>规则先检查，AI需另行主动确认；不自动发送报告。</small></div>`;
}
