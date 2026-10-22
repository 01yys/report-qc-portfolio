import { escapeHtml as esc } from "/ui.js";

let initialized = false, overview = null, busy = false, revision = 0, lastResult = null;
const byId = id => document.getElementById(id);
const labels = {sources_found:"找到相关资料", answered:"基于资料的回答", no_evidence:"未找到足够依据", literature_found:"PubMed 文献目录", clarification_needed:"请补充问题", out_of_scope:"需要专业人员判断", failed:"本次查询未完成", not_configured:"AI暂不可用", not_confirmed:"尚未确认调用", busy:"正在处理其他任务"};
const traceLabels = {search_library:"检索教材选段", search_pubmed:"查询PubMed文献", answer:"生成带引用回答", clarify:"提出澄清", abstain:"依据不足，停止回答"};

function controls() {
  const valid = byId("knowledge-question").value.trim().length >= 2;
  byId("knowledge-search").disabled = busy || !valid || !overview;
  byId("knowledge-pubmed").disabled = busy || !valid || !overview;
  byId("knowledge-ai").disabled = busy || !valid || !overview?.ai?.ready || !byId("knowledge-consent").checked;
  byId("knowledge-export").disabled = busy || !lastResult;
  byId("knowledge-result").setAttribute("aria-busy", String(busy));
}

function render(result) {
  const claims = (result.claims || []).map(c => '<p class="knowledge-claim">'+esc(c.text)+' <span class="knowledge-citations">'+c.citations.map(id => '<a href="#knowledge-'+esc(id)+'">['+esc(id)+']</a>').join(" ")+'</span></p>').join("");
  const evidence = (result.evidence || []).map(e => '<article class="knowledge-evidence" id="knowledge-'+esc(e.id)+'"><h3>['+esc(e.id)+'] '+esc(e.title)+'</h3><p>'+esc(e.text)+'</p><details><summary>核对英文原文</summary><blockquote>'+esc(e.quote)+'</blockquote></details><p class="muted">'+esc(e.source.authors)+' · '+esc(e.source.published)+' · '+esc(e.section)+'</p><a href="'+esc(e.source.url)+'" target="_blank" rel="noopener">'+esc(e.source.title)+' ↗</a><small class="knowledge-license">'+esc(e.source.license)+' · 中文为AI翻译摘要</small></article>').join("");
  const literature = (result.literature?.items || []).map(p => '<article class="knowledge-evidence"><h3><a href="'+esc(p.url)+'" target="_blank" rel="noopener">'+esc(p.title)+' ↗</a></h3><p class="muted">'+esc(p.authors.join(", "))+' · '+esc(p.published)+' · '+esc(p.journal)+'</p><small>PMID '+esc(p.pmid)+' · 目录结果不作为医学答案依据</small></article>').join("");
  const trace = (result.trace || []).map(s => '<li>'+esc(traceLabels[s.action] || "动作校验失败")+(s.query?'：'+esc(s.query):'')+(s.evidence_ids?' → '+esc(s.evidence_ids.join(", ") || "未命中"):'')+'</li>').join("");
  byId("knowledge-result").innerHTML = '<h3>'+esc(labels[result.status] || "查询状态未知")+'</h3><p role="status">'+esc(result.message)+'</p>'+claims+literature+(evidence?'<h3 class="knowledge-evidence-title">可核对的教材依据</h3>'+evidence:'')+
    '<details class="coverage"><summary>查看本次查询过程</summary><p>来源：'+esc(result.execution_source)+'；资料版本：'+esc(result.corpus_version)+'；模型请求：'+result.model_calls.length+'次</p><ol>'+trace+'</ol></details>'+(result.persistence_warning?'<p>'+esc(result.persistence_warning)+'</p>':'');
}

async function query(mode) {
  if (busy || !overview) return;
  const question = byId("knowledge-question").value.trim();
  if (question.length < 2) return;
  const atRevision = revision;
  busy = true; lastResult = null; controls();
  byId("knowledge-result").innerHTML = '<p role="status">正在查阅资料…</p>';
  try {
    const response = await fetch("/api/knowledge/query", {method:"POST", headers:{"Content-Type":"application/json", "X-QC-Token":overview.ai.csrf_token},
      signal:AbortSignal.timeout(95000), body:JSON.stringify({question, mode, confirm_paid:byId("knowledge-consent").checked})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "查询未完成。");
    if (atRevision !== revision) return;
    if (result.question !== question || result.mode !== mode || !Array.isArray(result.evidence) || !Array.isArray(result.trace) || !Array.isArray(result.model_calls)) throw new Error("查询结果与当前问题不一致。");
    lastResult = result; render(result);
  } catch (error) {
    if (atRevision !== revision) return;
    byId("knowledge-result").textContent = error.name === "TimeoutError" ? "等待超时。没有自动重试；若已发送模型请求，可能仍计费。" : error.message || "无法连接本机服务。";
  } finally {
    busy = false; byId("knowledge-consent").checked = false;
    // 更新共享额度；不自动再查询或调用模型。
    try { const response = await fetch("/api/ai-capabilities"); if(response.ok) overview.ai = await response.json(); } catch {}
    controls();
  }
}

export async function initKnowledgeAssistant() {
  if (initialized) return;
  initialized = true;
  byId("knowledge-question").addEventListener("input", () => {
    revision++; lastResult = null; byId("knowledge-consent").checked = false;
    byId("knowledge-result").textContent = busy ? "问题已修改，正在处理的旧问题不会覆盖当前内容。" : "问题已修改，请重新查询。";
    controls();
  });
  byId("knowledge-consent").addEventListener("change", controls);
  byId("knowledge-search").addEventListener("click", () => query("library"));
  byId("knowledge-pubmed").addEventListener("click", () => query("pubmed"));
  byId("knowledge-ai").addEventListener("click", () => query(byId("knowledge-mode").value));
  byId("knowledge-export").addEventListener("click", () => {
    if (!lastResult) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(lastResult,null,2)],{type:"application/json"}));
    const a = document.createElement("a"); a.href=url; a.download="knowledge-"+lastResult.request_id+".json"; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  try {
    const response = await fetch("/api/knowledge");
    if (!response.ok) throw new Error("资料目录暂时不可用，请刷新重试。");
    overview = await response.json();
    byId("knowledge-scope").textContent = overview.scope+" "+overview.review_status+"。";
    byId("knowledge-capability").textContent = overview.ai.ready ? "本机AI已启用；与报告复核共享本批请求额度，Agent一次查询最多2次模型请求。" : "AI尚未启用。可先使用免费资料检索与PubMed文献查询。";
    byId("knowledge-sources").innerHTML = overview.sources.map(s => '<p><a href="'+esc(s.url)+'" target="_blank" rel="noopener">'+esc(s.title)+' ↗</a><br><small>'+esc(s.authors)+' · '+esc(s.published)+' · '+esc(s.license)+'</small></p>').join("");
    for (const example of overview.examples) {
      const b = document.createElement("button"); b.type="button"; b.className="button small"; b.textContent=example;
      b.addEventListener("click", () => { byId("knowledge-question").value=example; byId("knowledge-question").dispatchEvent(new Event("input")); });
      byId("knowledge-examples").append(b);
    }
  } catch (error) { initialized=false; byId("knowledge-result").textContent=error.message; }
  controls();
}
