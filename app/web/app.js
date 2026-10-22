import { samples, moduleLabels } from "/samples.js";
import { escapeHtml, renderResult, emptyState } from "/ui.js";
import { HISTORY_LIMIT, reviewLabels, reviewRows, summarizeSession, filterReviewRows, sourceLabel, comparableRuns, taskTitle, exportResult } from "/review.js";
import { initKnowledgeAssistant } from "/knowledge.js";

const findingsInput = document.querySelector("#findings-text");
const impressionInput = document.querySelector("#impression-text");
const checkButton = document.querySelector("#check-button");
const clearButton = document.querySelector("#clear-button");
const exportButton = document.querySelector("#export-button");
const sampleSelect = document.querySelector("#sample-select");
const resultContent = document.querySelector("#result-content");
const moduleInputs = [...document.querySelectorAll(".module-options input:not(:disabled)")];
const aiButton = document.querySelector("#ai-button"), aiConsent = document.querySelector("#ai-consent");
const aiStatus = document.querySelector("#ai-status"), aiCapability = document.querySelector("#ai-capability");
let capabilities = null, aiCalling = false;
let revision = 0, activeController = null, checking = false;
let currentRun = null, previousRun = null, taskName = "手动模拟文本";
let taskOrigin = { task_id: crypto.randomUUID(), sample_id: null };
let history = [], evaluationLoaded = false;
const feedback = {};
const sourceHint = document.createElement("p");
sourceHint.id = "task-source-hint"; sourceHint.className = "muted";
sourceHint.setAttribute("aria-live", "polite"); sampleSelect.after(sourceHint);
function updateTaskTitle() {
  taskName = taskTitle(taskOrigin, findingsInput.value, impressionInput.value, samples);
  sourceHint.textContent = "当前输入来源：" + taskName;
}

const selectedModules = () => moduleInputs.filter(input => input.checked).map(input => input.value);
function updateControls() {
  checkButton.disabled = checking || !findingsInput.value.trim() || !impressionInput.value.trim() || !selectedModules().length;
  checkButton.textContent = checking ? "正在检查…" : "开始检查";
  exportButton.disabled = !currentRun || currentRun.revision !== revision || currentRun.aiPending;
  aiButton.disabled = !capabilities?.ready || !currentRun?.result.ai_ticket || currentRun.revision !== revision ||
    !aiConsent.checked || aiCalling || currentRun.aiAttempted || checking;
  aiButton.textContent = aiCalling ? "AI复核中…规则结果仍可查看" : "追加 AI 复核";
  resultContent.setAttribute("aria-busy", String(checking));
}
function invalidate(message, newTask = false) {
  revision += 1;
  activeController?.abort();
  activeController = null;
  checking = false;
  if (newTask) previousRun = null;
  else if (currentRun) previousRun = currentRun.result;
  currentRun = null;
  aiConsent.checked = false;
  aiStatus.textContent = aiCalling ? "文本已变更，旧AI答案不会显示为当前结果；远端请求可能仍在执行并计费。" : "输入已变更，请先重新运行规则；不会自动调用AI。";
  resultContent.innerHTML = emptyState(message);
  updateControls();
}
function showView(view) {
  document.querySelectorAll(".view").forEach(node => { node.hidden = node.id !== view + "-view"; });
  document.querySelectorAll(".nav-button").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  if (view === "records") renderHistory();
  if (view === "review") renderReview();
  if (view === "evaluation" && !evaluationLoaded) loadEvaluation();
  if (view === "knowledge") initKnowledgeAssistant();
  window.scrollTo(0, 0);
}
function loadSample(id) {
  const sample = samples.find(item => item.id === id);
  if (!sample) return;
  findingsInput.value = sample.findings;
  impressionInput.value = sample.impression;
  sampleSelect.value = id;
  taskOrigin = { task_id: crypto.randomUUID(), sample_id: sample.id };
  updateTaskTitle();
  invalidate("已填入合成案例，请点击“开始检查”。", true);
  showView("workspace");
}
for (const sample of samples) {
  const option = document.createElement("option");
  option.value = sample.id; option.textContent = sample.title; sampleSelect.append(option);
}
document.querySelector("#sample-list").innerHTML = samples.map(sample =>
  '<div class="sample-row"><span>' + escapeHtml(sample.title) + '</span><button class="button small" data-sample="' + sample.id + '">载入案例</button></div>').join("");
document.querySelector("#sample-list").addEventListener("click", event => {
  const button = event.target.closest("[data-sample]");
  if (button) loadSample(button.dataset.sample);
});
sampleSelect.addEventListener("change", () => { if (sampleSelect.value) loadSample(sampleSelect.value); });
document.querySelectorAll(".nav-button").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
function onTextInput() {
  updateTaskTitle();
  invalidate("文本已变更，请重新检查；旧结果不可导出。");
}
findingsInput.addEventListener("input", onTextInput);
impressionInput.addEventListener("input", onTextInput);
moduleInputs.forEach(input => input.addEventListener("change", () => invalidate("检查模块已改变，请重新检查。")));
clearButton.addEventListener("click", () => {
  findingsInput.value = ""; impressionInput.value = ""; sampleSelect.value = "";
  taskOrigin = { task_id: crypto.randomUUID(), sample_id: null }; updateTaskTitle();
  invalidate("填写两段模拟文本后，点击“开始检查”。", true); findingsInput.focus();
});

async function runCheck() {
  if (checkButton.disabled) return;
  const requestRevision = revision;
  const requestTask = { ...taskOrigin }, requestTitle = taskName;
  const payload = { findings_text: findingsInput.value, impression_text: impressionInput.value, modules: selectedModules(), input_revision: revision };
  const controller = new AbortController(); activeController = controller;
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 8000);
  checking = true; currentRun = null; updateControls();
  resultContent.innerHTML = emptyState("正在检查当前模拟文本…");
  try {
    const response = await fetch("/api/check-v2", { method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "检查请求失败，请重试。");
    if (requestRevision !== revision) return;
    if (!["pass", "needs_review", "unable_to_check"].includes(result.status) || !Array.isArray(result.issues) || !result.modules ||
        result.input_snapshot?.findings_text !== payload.findings_text || result.input_snapshot?.impression_text !== payload.impression_text ||
        result.input_revision !== requestRevision || JSON.stringify(result.rule_scope) !== JSON.stringify(payload.modules)) throw new Error("检查响应与当前输入不一致，本次结果不可使用，请重试。");
    result.task_id = requestTask.task_id;
    currentRun = { result, revision, previous: previousRun, title: requestTitle, task_origin: requestTask };
    aiConsent.checked = false;
    aiStatus.textContent = "规则检查已完成；AI尚未执行。需要时请确认合成文本与费用后追加。";
    resultContent.innerHTML = renderResult(result, previousRun, feedback);
    if (!result.error_message) {
      history.unshift(currentRun); history = history.slice(0, HISTORY_LIMIT); pruneFeedback(); renderHistory(); renderReview();
    }
  } catch (error) {
    if (requestRevision !== revision) return;
    const message = timedOut ? "检查等待超过 8 秒，请重试。本次未完成，不能作为通过结果。" :
      error instanceof TypeError ? "无法连接本机服务，请确认服务运行后重试。" : error.message;
    currentRun = null;
    resultContent.innerHTML = '<div class="status-message status-error"><h3>检查未完成</h3><p>' + escapeHtml(message) + '</p></div>';
  } finally {
    clearTimeout(timeout);
    if (activeController === controller) { activeController = null; checking = false; updateControls(); }
  }
}
checkButton.addEventListener("click", runCheck);
aiConsent.addEventListener("change", updateControls);
async function loadCapabilities() {
  try {
    const response = await fetch("/api/ai-capabilities", { signal: AbortSignal.timeout(8000) });
    if (!response.ok) throw new Error();
    capabilities = await response.json();
    aiCapability.textContent = (capabilities.execution_source === "mock" ? "模拟联调 · 非真实模型输出。" : "真实调用模式 · ") +
      (capabilities.ready ? "本机凭据已配置。" : capabilities.message) +
      ` 本批预留 ${capabilities.reserved_requests ?? "未知"}/${capabilities.max_requests ?? 10} 次。` + (capabilities.cost_caveat || "");
  } catch {
    capabilities = null;
    aiCapability.textContent = "无法核对AI配置，AI按钮已停用；规则仍可检查。";
  }
  updateControls();
}
async function runAI() {
  if (aiButton.disabled) return;
  const run = currentRun, requestRevision = revision, ticket = run.result.ai_ticket;
  const original = run.result;
  run.aiAttempted = true; run.aiPending = true; aiCalling = true; aiConsent.checked = false;
  run.result = { ...original, llm_status: "pending", semantic_review: { ...original.semantic_review,
    state: "pending", message: "AI复核进行中，规则结果保留；这不是AI无候选。" } };
  aiStatus.textContent = "AI复核中，规则结果保留。不会自动重试；编辑后旧答案不用于新文本，远端可能仍计费。";
  updateControls();
  try {
    const response = await fetch("/api/ai-review", { method: "POST",
      headers: { "Content-Type": "application/json", "X-QC-Token": capabilities.csrf_token },
      signal: AbortSignal.timeout(40000), body: JSON.stringify({ ai_ticket: ticket,
        input_revision: requestRevision, confirm_synthetic: true, confirm_paid: true }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "本机AI请求未完成。");
    if (result.run_id !== original.run_id || result.input_revision !== requestRevision ||
        result.input_snapshot?.findings_text !== original.input_snapshot.findings_text ||
        result.input_snapshot?.impression_text !== original.input_snapshot.impression_text ||
        !Array.isArray(result.issues) || !result.semantic_review) throw new Error("AI结果与本次输入不匹配，未采用。");
    // 更新同一运行，不新增历史。即使已编辑，只更新其旧输入快照，不覆盖当前工作台。
    result.task_id = original.task_id;
    run.result = result;
    if (currentRun === run && revision === requestRevision) {
      resultContent.innerHTML = renderResult(result, run.previous, feedback);
      aiStatus.textContent = result.semantic_review.message +
        (result.ai_call?.execution_source === "mock" ? "【模拟响应，不是DeepSeek效果】" : "【本次真实调用】") +
        (result.persistence_warning || "");
    }
  } catch (error) {
    run.result = { ...original, llm_status: "delivery_unknown", semantic_review: { ...original.semantic_review,
      state: "delivery_unknown", message: "网页未取得可用结果；请求是否送达或计费需查本机记录，不自动重试。" } };
    if (currentRun === run && revision === requestRevision) aiStatus.textContent =
      error.message + " 规则结果保留。没有自动重试；如曾发出请求，可能已计费，请先复核记录。";
  } finally {
    run.aiPending = false; aiCalling = false;
    renderHistory(); renderReview();
    await loadCapabilities();
  }
}
aiButton.addEventListener("click", runAI);
resultContent.addEventListener("click", event => {
  const button = event.target.closest("[data-feedback]");
  if (!button || !currentRun || currentRun.revision !== revision) return;
  const issue = currentRun.result.issues[Number(button.dataset.index)];
  const next = button.dataset.feedback;
  if (!issue || !["reviewed", "inaccurate"].includes(next)) return;
  toggleFeedback(issue, next);
  resultContent.innerHTML = renderResult(currentRun.result, currentRun.previous, feedback);
  resultContent.querySelector('[data-index="' + button.dataset.index + '"][data-feedback="' + next + '"]')?.focus();
});
function toggleFeedback(issue, next) {
  if (feedback[issue.id] === next) delete feedback[issue.id]; else feedback[issue.id] = next;
  renderReview();
}
function pruneFeedback() {
  const retainedIds = new Set([...history, ...(currentRun ? [currentRun] : [])].flatMap(run => run.result.issues.map(issue => issue.id)));
  Object.keys(feedback).forEach(id => { if (!retainedIds.has(id)) delete feedback[id]; });
}
function downloadJson(packet, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(packet, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportRun(run) {
  const relevantFeedback = Object.fromEntries(run.result.issues.filter(issue => feedback[issue.id]).map(issue => [issue.id, feedback[issue.id]]));
  const packet = { schema_version: "1.0", project: "报告文本一致性检查与评测工作台", boundary: "合成文本演示，不作医学诊断，不替代医师审核。", title: run.title, task_origin: run.task_origin, result: exportResult(run.result),
    feedback: relevantFeedback, comparison: run.previous && comparableRuns(run.previous, run.result) ?
      { previous_run_id: run.previous.run_id, previous_issue_count: run.previous.issues.length, current_issue_count: run.result.issues.length } : null };
  downloadJson(packet, "report-qc-" + run.result.run_id + ".json");
}
exportButton.addEventListener("click", () => { if (currentRun && currentRun.revision === revision) exportRun(currentRun); });
function renderHistory() {
  const root = document.querySelector("#history-list");
  root.innerHTML = history.length ? history.map((run, index) =>
    '<div class="history-row"><div class="history-description">' + escapeHtml(run.title) + ' · ' + run.result.issues.length +
    ' 项<small>' + escapeHtml(new Date(run.result.checked_at).toLocaleTimeString("zh-CN")) + ' · ' +
    escapeHtml((run.result.rule_scope || run.result.scope).map(module => moduleLabels[module] || module).join(" / ")) +
    ' · AI：' + escapeHtml(run.result.semantic_review?.state || "disabled") +
    '</small></div><button class="button small" data-history="' + index + '">载入并复检</button><button class="button small" data-history-export="' +
    index + '">导出该次记录</button></div>').join("") : '<p class="muted">尚无会话记录。完成检查后会显示在这里，最多保留12次。</p>';
}
document.querySelector("#history-list").addEventListener("click", event => {
  const load = event.target.closest("[data-history]");
  const download = event.target.closest("[data-history-export]");
  if (download) { const run = history[Number(download.dataset.historyExport)]; if (run) exportRun(run); }
  if (load) loadHistoryRun(Number(load.dataset.history));
});
function loadHistoryRun(index) {
  const run = history[index]; if (!run) return;
  findingsInput.value = run.result.input_snapshot.findings_text; impressionInput.value = run.result.input_snapshot.impression_text;
  moduleInputs.forEach(input => { input.checked = run.result.scope.includes(input.value); });
  taskOrigin = { ...(run.task_origin || { task_id: run.result.task_id || crypto.randomUUID(), sample_id: null }) };
  sampleSelect.value = taskOrigin.sample_id || ""; updateTaskTitle();
  invalidate("已载入历史输入，请重新检查当前版本。", true); previousRun = run.result; showView("workspace");
}
document.querySelector("#clear-history-button").addEventListener("click", () => { history = []; pruneFeedback(); renderHistory(); renderReview(); });
function renderReview() {
  const summary = summarizeSession(history, feedback);
  document.querySelector("#session-summary").innerHTML =
    '<div class="session-metric"><strong>' + summary.retained_run_count + '</strong>保留检查次数</div>' +
    '<div class="session-metric"><strong>' + summary.issue_count + '</strong>提示项（可重复）</div>' +
    '<div class="session-metric"><strong>' + summary.states.pending + '</strong>待核对提示项</div>' +
    '<div class="session-detail">需核对运行 ' + summary.needs_review_run_count + ' · 未发现不一致运行 ' + summary.no_issue_run_count + ' · 无法检查运行 ' + summary.unable_run_count + '</div>' +
    '<div class="session-detail">已核对 ' + summary.states.reviewed + ' · 提示不准确 ' + summary.states.inaccurate + '（用户反馈）</div>' +
    '<div class="session-detail">侧别 ' + summary.modules.laterality + ' · 尺寸 ' + summary.modules.measurement + ' · 肯否 ' + summary.modules.assertion + ' · 其他语义 ' + summary.modules.semantic + ' 项</div>';
  const rows = reviewRows(history, feedback);
  const filtered = filterReviewRows(rows, document.querySelector("#review-module").value, document.querySelector("#review-state").value);
  document.querySelector("#review-match-count").textContent = '当前筛选 ' + filtered.length + ' / 全会话 ' + rows.length + ' 项；按最近检查顺序展示。';
  document.querySelector("#export-session-button").disabled = !history.length;
  document.querySelector("#review-list").innerHTML = filtered.length ? filtered.map(row => {
    const { run, runIndex, issue, issueIndex, state } = row;
    const location = ' data-run="' + runIndex + '" data-issue="' + issueIndex + '"';
    return '<article class="review-item"><h3>' + escapeHtml(issue.title) + '</h3><div class="review-meta"><span>' + escapeHtml(run.title) + '</span><span>' +
      escapeHtml(new Date(run.result.checked_at).toLocaleTimeString("zh-CN")) + '</span><span>' + escapeHtml(moduleLabels[issue.module] || "其他语义候选") + '</span><span>' + escapeHtml(sourceLabel(issue) + (issue.llm_execution_source === "mock" ? " · 模拟" : "")) + '</span><span>' + reviewLabels[state] + '</span></div>' +
      '<details class="review-evidence"><summary>查看本次原文快照与证据</summary><p>所见证据：' + escapeHtml(issue.findings_evidence) + '</p><p>意见证据：' + escapeHtml(issue.impression_evidence) +
      '</p><hr><p>完整影像所见：' + escapeHtml(run.result.input_snapshot.findings_text) + '</p><p>完整诊断意见：' + escapeHtml(run.result.input_snapshot.impression_text) +
      '</p><p>版本：' + escapeHtml(run.result.checker_version || "见导出记录") + ' · 运行：' + escapeHtml(run.result.run_id) + '</p></details><div class="review-actions">' +
      '<button class="button small feedback-button' + (state === "reviewed" ? ' selected' : '') + '" aria-pressed="' + (state === "reviewed") + '" data-review-feedback="reviewed"' + location + '>已核对</button>' +
      '<button class="button small feedback-button' + (state === "inaccurate" ? ' selected' : '') + '" aria-pressed="' + (state === "inaccurate") + '" data-review-feedback="inaccurate"' + location + '>提示不准确</button>' +
      '<button class="button small" data-review-load="' + runIndex + '">载入该次输入并复检</button></div></article>';
  }).join("") : '<p class="muted">' + (rows.length ? '没有符合筛选的提示项，请调整条件。' : history.length ? '保留的检查中没有提示项；请结合覆盖状态查看结果，不能据此认定整份报告正确。' : '暂无检查记录。先在工作台检查合成案例。') + '</p>';
}
document.querySelectorAll("#review-module, #review-state").forEach(input => input.addEventListener("change", renderReview));
document.querySelector("#review-list").addEventListener("click", event => {
  const load = event.target.closest("[data-review-load]");
  if (load) { loadHistoryRun(Number(load.dataset.reviewLoad)); return; }
  const button = event.target.closest("[data-review-feedback]");
  if (!button) return;
  const runIndex = Number(button.dataset.run), issueIndex = Number(button.dataset.issue), next = button.dataset.reviewFeedback;
  const issue = history[runIndex]?.result.issues[issueIndex];
  if (!issue || !["reviewed", "inaccurate"].includes(next)) return;
  toggleFeedback(issue, next);
  if (currentRun && currentRun.revision === revision) resultContent.innerHTML = renderResult(currentRun.result, currentRun.previous, feedback);
  const replacement = document.querySelector('[data-run="' + runIndex + '"][data-issue="' + issueIndex + '"][data-review-feedback="' + next + '"]');
  (replacement || document.querySelector("#review-state")).focus();
});
document.querySelector("#export-session-button").addEventListener("click", () => {
  if (!history.length) return;
  const ids = new Set(reviewRows(history, feedback).map(row => row.issue.id));
  downloadJson({ schema_version: "1.1-session", project: "报告文本一致性检查与评测工作台", exported_at: new Date().toISOString(),
    boundary: "合成文本会话快照；不是独立报告量、临床错误率或诊断结论。", metric_basis: { grain: "retained_check_run_and_issue", limit: HISTORY_LIMIT,
      ordering: "newest_first", persistence: "page_memory_only", filters_affect_summary: false, repeat_checks_counted: true },
    summary: summarizeSession(history, feedback), runs: history.map(run => ({ title: run.title, task_origin: run.task_origin, result: exportResult(run.result) })),
    feedback: Object.fromEntries(Object.entries(feedback).filter(([id]) => ids.has(id))) }, "report-qc-session.json");
});
async function loadEvaluation() {
  const root = document.querySelector("#evaluation-content");
  root.innerHTML = '<p class="muted">正在读取实际评测文件…</p>';
  try {
    const response = await fetch("/api/evaluation", { signal: AbortSignal.timeout(8000) });
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.reports)) throw new Error(data.error || "评测文件暂不可用。");
    const a = data.acceptance;
    const statusLabels = { pass: "规则范围内无提示", needs_review: "有待核对项", unable_to_check: "无可比较内容" };
    const verdictLabels = { matched: "符合草案", mismatched: "与草案不同", unscored: "未标注 · 不计分" };
    const acceptanceHtml = a ? '<h3>三规则冻结合成验收 · ' + escapeHtml(a.created_on) + '</h3><p class="muted">' +
      escapeHtml(a.checker_version) + ' · 有预期草案 ' + a.scored + '/' + a.total + ' 例，符合 ' + a.matched + '/' + a.scored +
      '，未标注 ' + a.unscored + ' 例。不是临床准确率。</p><p class="muted">' + escapeHtml(a.independence_caveat) + '</p>' +
      (a.snapshot_implementation_matches ? '<p class="muted">验收快照与当前规则/评测代码指纹一致。</p>' : '<p class="status-error">当前实现与该快照不同；以下仅供历史对照，不能当新版验收。</p>') +
      '<div class="table-scroll"><table class="evaluation-table"><thead><tr><th>模块</th><th>符合 / 有标签</th><th>未标注</th><th>应提示却未提示</th><th>不应提示却提示</th><th>有可比较内容 / 总例数</th></tr></thead><tbody>' +
      Object.entries(a.groups).map(([key,g]) => '<tr><td>' + escapeHtml(moduleLabels[key] || key) + '</td><td>' + g.matched + '/' + g.scored + '</td><td>' + g.unscored +
        '</td><td>' + g.missed_positive_cases + '/' + g.expected_positive_cases + '</td><td>' + g.unexpected_positive_cases + '/' + g.expected_no_issue_cases +
        '</td><td>' + g.comparable_case_count + '/' + g.total + '</td></tr>').join('') + '</tbody></table></div>' +
      '<p class="muted">符合草案包含“正确跳过”，不是全部查过。请同时看覆盖：无可比较内容不叫报告正确；错报/漏报计数仅对应本组合成标签。</p>' +
      '<details class="coverage"><summary>逐例查看预期、实际与跳过边界（' + a.total + '例）</summary><div class="acceptance-cases">' + a.cases.map(c =>
        '<details class="acceptance-case"><summary>' + escapeHtml(c.id + ' · ' + c.group + ' · ' + verdictLabels[c.verdict]) + '</summary><p>所见：' + escapeHtml(c.findings_text) +
        '</p><p>意见：' + escapeHtml(c.impression_text) + '</p><p>预期：' + escapeHtml(c.expected_statuses ? c.expected_statuses.map(s=>statusLabels[s]).join(' 或 ') + ' / ' + c.expected_issue_count + '项' : '未标注，不计入符合分母') +
        '</p><p>实际：' + escapeHtml(statusLabels[c.actual_status] || c.actual_status) + ' / ' + c.actual_issue_count + '项 · 比较' + c.comparable_pairs + '对</p><p>标签理由：' + escapeHtml(c.label_reason) + '</p></details>').join('') + '</div></details>' +
      '<p class="muted">冻结数据SHA-256：' + escapeHtml(a.dataset_sha256) + '</p>' : '';
    root.innerHTML = acceptanceHtml + '<h3>历史左右开发集快照</h3><div class="table-scroll"><table class="evaluation-table"><thead><tr><th>评测版本</th><th>有标签 / 总数</th><th>符合草案</th><th>未定标签</th></tr></thead><tbody>' +
      data.reports.map(report => '<tr><td>' + escapeHtml(report.name) + '</td><td>' + report.labeled_count + ' / ' + report.sample_count +
      '</td><td>' + report.matched_count + ' / ' + report.labeled_count + '</td><td>' + report.unscored_count + '</td></tr>').join("") +
      '</tbody></table></div><p class="muted">' + escapeHtml(data.caveat) + '</p><ul class="scope-list">' +
      data.reports.map(report => '<li>' + escapeHtml(report.name + " · " + report.source) + '</li>').join("") + '</ul>';
    evaluationLoaded = true;
  } catch (error) {
    root.innerHTML = '<div class="status-message status-error"><p>' + escapeHtml(error.message) + '</p><button id="retry-evaluation" class="button small">重新读取</button></div>';
    root.querySelector("#retry-evaluation").addEventListener("click", loadEvaluation);
  }
}
updateTaskTitle(); updateControls(); renderHistory(); renderReview(); loadCapabilities();
