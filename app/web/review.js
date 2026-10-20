// 会话管理只汇总保留的运行快照，不推断独立报告数或临床错误率。
export const HISTORY_LIMIT = 12;
export const reviewLabels = { pending: "待核对", reviewed: "已核对", inaccurate: "提示不准确" };
export const sourceLabel = issue => issue.source === "rule_and_llm" ? "规则＋AI" : issue.source === "llm" ? "AI候选" : "规则检查";
// 来源名来自原始样例和当前逐字文本，不从上一次检查结论推断。
export function taskTitle(origin, findings, impression, samples) {
  const sample = samples.find(item => item.id === origin?.sample_id);
  if (!sample) return "手动模拟文本";
  return sample.title + (findings === sample.findings && impression === sample.impression ? "" : "（已修改）");
}
export function comparableRuns(a, b) {
  const mode = r => (r.semantic_review?.state || "disabled") === "completed" ? "rules_and_ai" : "rules_only";
  return typeof a.task_id === "string" && a.task_id.length > 0 && a.task_id === b.task_id &&
    typeof a.checker_version === "string" && a.checker_version.trim().length > 0 && a.checker_version === b.checker_version &&
    JSON.stringify(a.rule_scope || a.scope) === JSON.stringify(b.rule_scope || b.scope) && mode(a) === mode(b);
}
// 分享文件只保留结果证据；页面内的临时操作票据不随文件流转。
export function exportResult(result) {
  const copy = JSON.parse(JSON.stringify(result));
  delete copy.ai_ticket;
  return copy;
}

const knownRuleStatuses = ["pass", "needs_review", "unable_to_check"];
const knownCoverage = ["none", "partial", "supported_content"];
const isCount = value => Number.isSafeInteger(value) && value >= 0;
function comparisonSnapshot(result) {
  const selected = result.rule_scope || result.scope || [];
  const modules = result.modules || {};
  const names = [...new Set([...selected, ...Object.keys(modules)])].sort();
  const complete = names.length > 0 && !result.error_message &&
    names.every(name => modules[name]?.state === "completed");
  const total = field => complete && names.every(name => isCount(modules[name][field])) ?
    names.reduce((sum, name) => sum + modules[name][field], 0) : null;
  const skipped = complete && names.every(name => Array.isArray(modules[name].skipped_pairs)) ?
    names.reduce((sum, name) => sum + modules[name].skipped_pairs.length, 0) : null;
  const fallback = result.semantic_review?.state === "completed" || !complete ? "unknown" : result.status;
  const state = result.rule_status ?? fallback;
  return {
    issue_count: Array.isArray(result.issues) ? result.issues.length : null,
    rule_status: result.error_message ? "error" : knownRuleStatuses.includes(state) ? state : "unknown",
    comparable_pairs: total("comparable_pairs"),
    skipped_pair_count: skipped,
    skipped_segment_count: Array.isArray(result.skipped_segments) ? result.skipped_segments.length : null,
    coverage_states: Object.fromEntries(names.map(name => [name,
      modules[name]?.state === "completed" && knownCoverage.includes(modules[name].coverage) ? modules[name].coverage : "unknown"])),
  };
}
function coverageSignature(result) {
  const names = Object.keys(result.modules || {}).sort();
  return JSON.stringify({ modules: names.map(name => {
    const value = result.modules[name];
    return [name, value?.state, value?.coverage, value?.comparable_pairs, value?.skipped_pairs];
  }), skipped_segments: result.skipped_segments });
}
export function comparisonDetails(previous, result) {
  if (!previous || !result || !comparableRuns(previous, result)) return null;
  return { previous: comparisonSnapshot(previous), current: comparisonSnapshot(result),
    coverage_changed: coverageSignature(previous) !== coverageSignature(result) };
}

const plainReasons = {
  "侧别、部位或对象不唯一/未识别": "没能找到唯一明确的左右侧、部位和对象，暂时跳过。",
  "同类片段存在不唯一/未识别对象，不能过滤后猜测配对": "同一部位有多个或不明确的对象，不能猜测哪一个对应哪一个。",
  "对象无法唯一配对": "两段中的对象无法一一对应，不能确定是不是同一个。",
  "不确定/未知断言不参与肯否冲突比较": "这段话没有明确说有还是没有，因此不比较有无是否冲突。",
  "否定或不确定对象不参与左右/尺寸比较": "这段话说没有这个对象，或表达不确定，因此不比较它的左右和尺寸。",
  "非ASCII数字或特殊数字格式暂未支持，不能截取部分数值": "数字写法不在本版支持范围内，不能只取其中一部分来比较。",
  "带测量轴/最大最小限定的长度暂未比较": "这段尺寸带有长径、短径、最大或最小等限定，不能直接当作同一种长度比较。",
  "明确时间/历史描述暂未支持跨时间配对": "这段话涉及不同时间，不能直接把过去和现在的描述放在一起比较。",
  "范围值或多径测量暂未支持": "尺寸是一个范围，或同时包含长宽等多个方向，本版暂不直接比较。",
  "科学计数表达暂未支持": "这段数字用了科学计数写法，本版暂不能比较。",
  "没有单一受支持长度": "没找到一个本版能识别的明确长度。",
  "一个片段中有多个测量值": "同一段有多个尺寸，不能确定应该比较哪一个。",
  "近似值、比较值或非长度单位暂未支持": "这里写的是大约、大于、小于等数值，或不是普通长度单位，本版暂不直接比较。",
  "历史对比/变化描述暂未支持": "这段话描述了前后变化，本版暂不能区分不同时点的尺寸。",
  "测量数字超过本版支持长度": "数字位数太长，超出本版支持范围。",
  "长度必须大于零": "尺寸为零或不符合正长度要求，暂时不能比较。",
  "缺少单值长度": "缺少一个明确的长度，暂时不能比较。",
};
export function coverageReason(reason) {
  if (typeof reason === "string" && Object.hasOwn(plainReasons, reason)) return plainReasons[reason];
  if (typeof reason === "string" && reason.includes("；")) return reason.split("；").map(coverageReason).join(" ");
  return "本版暂不能可靠比较这部分内容；请人工核对，技术原因见导出记录。";
}
export function coverageObject(key) {
  if (typeof key !== "string") return "这组描述";
  const parts = key.split("|");
  if (![2, 3].includes(parts.length) || !parts.slice(0, 2).every(part => /^[\u4e00-\u9fff\s]+$/.test(part))) return "这组描述";
  const sides = { left: "左侧", right: "右侧", bilateral: "双侧", both: "双侧" };
  if (parts.length === 3 && !Object.hasOwn(sides, parts[2])) return "这组描述";
  return [parts.length === 3 ? sides[parts[2]] : "", ...parts.slice(0, 2)].filter(Boolean).join(" ");
}
export function reviewState(issue, feedback) {
  return ["reviewed", "inaccurate"].includes(feedback[issue.id]) ? feedback[issue.id] : "pending";
}
export function reviewRows(history, feedback) {
  return history.flatMap((run, runIndex) => run.result.issues.map((issue, issueIndex) =>
    ({ run, runIndex, issue, issueIndex, state: reviewState(issue, feedback) })));
}
export function summarizeSession(history, feedback) {
  const rows = reviewRows(history, feedback);
  const states = { pending: 0, reviewed: 0, inaccurate: 0 };
  const modules = { laterality: 0, measurement: 0, assertion: 0, semantic: 0 };
  rows.forEach(row => { states[row.state]++; if (row.issue.module in modules) modules[row.issue.module]++; });
  return { retained_run_count: history.length, needs_review_run_count: history.filter(run => run.result.status === "needs_review").length,
    no_issue_run_count: history.filter(run => run.result.status === "pass").length,
    unable_run_count: history.filter(run => run.result.status === "unable_to_check").length,
    issue_count: rows.length, states, modules };
}
export function filterReviewRows(rows, module, state) {
  return rows.filter(row => (module === "all" || row.issue.module === module) && (state === "all" || row.state === state));
}
