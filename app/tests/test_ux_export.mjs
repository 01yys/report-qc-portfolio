import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';

// 纯函数和HTML字符串契约；真实下载与浏览器交互另由主助手隔离验证。
const reviewSource = await readFile(new URL('../web/review.js', import.meta.url), 'utf8');
const reviewUrl = 'data:text/javascript;base64,' + Buffer.from(reviewSource).toString('base64');
const review = await import(reviewUrl);
const sampleSource = await readFile(new URL('../web/samples.js', import.meta.url), 'utf8');
const samplesUrl = 'data:text/javascript;base64,' + Buffer.from(sampleSource).toString('base64');
const uiSource = await readFile(new URL('../web/ui.js', import.meta.url), 'utf8');
const uiUrl = 'data:text/javascript;base64,' + Buffer.from(uiSource
  .replaceAll('"/review.js"', JSON.stringify(reviewUrl))
  .replaceAll('"/samples.js"', JSON.stringify(samplesUrl))).toString('base64');
const ui = await import(uiUrl);
const appSource = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');

// 与浏览器textContent转HTML相同的最小替身，只为测试现有escapeHtml。
globalThis.document = {
  createElement() {
    return {
      value: '',
      set textContent(value) { this.value = String(value); },
      get innerHTML() { return this.value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'); },
    };
  },
};

const clone = value => JSON.parse(JSON.stringify(value));
const run = (overrides = {}) => ({
  run_id: 'run-1', task_id: 'task-1', checker_version: 'v1.3.1-rules',
  status: 'pass', rule_status: 'pass', error_message: null, summary: '规则范围未发现不一致。',
  scope: ['measurement'], rule_scope: ['measurement'], issues: [],
  input_snapshot: { findings_text: '左肺上叶见结节，直径8毫米。', impression_text: '左肺上叶见结节，直径0.8厘米。' },
  modules: { measurement: { state: 'completed', comparable_pairs: 1, issue_count: 0, coverage: 'supported_content', skipped_pairs: [] } },
  skipped_segments: [], semantic_review: { state: 'disabled' },
  ...overrides,
});
const skippedRun = () => run({ status: 'unable_to_check', rule_status: 'unable_to_check',
  summary: '没有可明确比较的内容。',
  modules: { measurement: { state: 'completed', comparable_pairs: 0, issue_count: 0, coverage: 'none',
    skipped_pairs: [{ key: '肺上叶|结节|left', reason: '同类片段存在不唯一/未识别对象，不能过滤后猜测配对' }] } },
});
const deepFreeze = obj => {
  if (obj && typeof obj === 'object') { Object.values(obj).forEach(deepFreeze); Object.freeze(obj); }
  return obj;
};

test('EXP01 app exports project transport-safe results for single/history and session downloads', () => {
  assert.ok((appSource.match(/result:\s*exportResult\(run\.result\)/g) || []).length >= 2,
    'existing app puts raw run.result into both export paths');
});

test('UX01 rendered zero to zero comparison must mention changed coverage', () => {
  const html = ui.renderResult(skippedRun(), run(), {});
  const comparison = html.match(/<p class="comparison">[\s\S]*?<\/p>/)?.[0] || html;
  assert.match(comparison, /覆盖/);
  assert.match(comparison, /不(?:代表|等于)|不能/);
});

test('UX02 rendered known skip information hides raw side/key and extraction jargon', () => {
  const html = ui.renderCoverage(skippedRun());
  assert.doesNotMatch(html, /\|left|过滤后猜测/);
  assert.match(html, /左/);
});

test('EXP01 removes only transport ticket and keeps exact original, technical reasons and feedback unchanged', () => {
  const source = run({ ai_ticket: 'synthetic-transport-ticket', findings: [{ evidence: 'ai_ticket是原文中的词，不应删除' }],
    issues: [{ id: 'run-1:1', module: 'measurement', findings_evidence: '8毫米', impression_evidence: '18毫米' }],
    metadata: { nested: [{ raw: '原文', ai_ticket: 'not-a-transport-field' }] } });
  const before = clone(source);
  deepFreeze(source);
  const feedback = deepFreeze({ 'run-1:1': 'reviewed' });
  const exported = { result: review.exportResult(source), feedback };
  assert.ok(!Object.hasOwn(exported.result, 'ai_ticket'));
  const expected = clone(before); delete expected.ai_ticket;
  assert.deepEqual(exported.result, expected);
  assert.deepEqual(source, before);
  assert.deepEqual(exported.feedback, { 'run-1:1': 'reviewed' });
  assert.notEqual(exported.result.input_snapshot, source.input_snapshot);
  assert.notEqual(exported.result.metadata.nested, source.metadata.nested);
  exported.result.metadata.nested[0].raw = '仅改导出副本';
  assert.equal(source.metadata.nested[0].raw, '原文');
});

test('EXP01 one run, history run and session routes use ticket-free projection', () => {
  const current = run({ ai_ticket: 'synthetic-current' });
  const older = skippedRun(); older.ai_ticket = 'synthetic-older';
  const history = [{ title: '当前', result: current }, { title: '历史', result: older }];
  const packets = [review.exportResult(current), review.exportResult(history[1].result),
    history.map(item => ({ ...item, result: review.exportResult(item.result) }))];
  assert.ok(!JSON.stringify(packets).includes('synthetic-current'));
  assert.ok(!JSON.stringify(packets).includes('synthetic-older'));
  assert.equal(current.ai_ticket, 'synthetic-current');
  assert.equal(older.ai_ticket, 'synthetic-older');
  assert.ok((appSource.match(/result:\s*exportResult\(run\.result\)/g) || []).length >= 2,
    'single/history shared export and session mapper must both project result');
  assert.match(appSource, /exportButton\.addEventListener[\s\S]*?exportRun\(currentRun\)/);
  assert.match(appSource, /data-history-export[\s\S]*?exportRun\(run\)/);
});

test('UX01 equal issue counts do not hide pass to no-coverage transition', () => {
  const details = review.comparisonDetails(run(), skippedRun());
  assert.ok(details);
  assert.equal(details.previous.issue_count, 0);
  assert.equal(details.current.issue_count, 0);
  assert.equal(details.previous.rule_status, 'pass');
  assert.equal(details.current.rule_status, 'unable_to_check');
  assert.equal(details.previous.comparable_pairs, 1);
  assert.equal(details.current.comparable_pairs, 0);
  assert.equal(details.current.skipped_pair_count, 1);
  assert.equal(details.coverage_changed, true);
  const html = ui.renderResult(skippedRun(), run(), {});
  assert.match(html, /覆盖/);
  assert.match(html, /不代表[^<]*(?:改善|修复|改好)/);
  assert.doesNotMatch(html, /已(?:修复|改正|改好)|<h3>检查通过<\/h3>/);
});

test('UX01 comparison still rejects different or missing task, version, modules and AI completion mode', () => {
  const first = run();
  for (const [label, second] of [
    ['task', run({ task_id: 'task-2' })], ['missing-task', run({ task_id: null })],
    ['version', run({ checker_version: 'v-next' })], ['missing-version', run({ checker_version: '' })],
    ['modules', run({ scope: ['laterality'], rule_scope: ['laterality'] })],
    ['AI-mode', run({ semantic_review: { state: 'completed' } })],
  ]) {
    assert.equal(review.comparableRuns(first, second), false, label);
    assert.equal(review.comparisonDetails(first, second), null, label);
  }
  assert.equal(review.comparableRuns(first, run()), true);
});

test('UX01 missing counts are unknown rather than invented zero and failures are not pass', () => {
  const incomplete = run({ modules: {}, skipped_segments: undefined, rule_status: undefined });
  const details = review.comparisonDetails(run(), incomplete);
  assert.equal(details.current.comparable_pairs, null);
  assert.equal(details.current.skipped_pair_count, null);
  assert.equal(details.current.skipped_segment_count, null);
  assert.equal(details.current.coverage_states.measurement, 'unknown');
  const a = run({ semantic_review: { state: 'completed' } });
  const b = run({ semantic_review: { state: 'completed' }, rule_status: undefined });
  assert.equal(review.comparisonDetails(a, b).current.rule_status, 'unknown');
  assert.equal(review.comparisonDetails(run(), run({ error_message: '合成失败', status: 'unable_to_check' })).current.rule_status, 'error');
});

test('UX01 changed skipped reason or original evidence changes coverage even when counts match', () => {
  const a = skippedRun();
  const b = clone(a); b.modules.measurement.skipped_pairs[0].reason = '范围值或多径测量暂未支持';
  assert.equal(review.comparisonDetails(a, b).coverage_changed, true);
  const c = run({ skipped_segments: [{ source: 'findings', evidence: '去年左肺结节', reason: '历史片段' }] });
  const e = clone(c); e.skipped_segments[0].evidence = '今年左肺结节';
  assert.equal(review.comparisonDetails(c, e).coverage_changed, true);
  assert.equal(review.comparisonDetails(a, clone(a)).coverage_changed, false);
});

test('UX01 error response takes priority over stale pass status and completed module fields', () => {
  const failed = run({ status: 'pass', rule_status: 'pass', error_message: '合成失败，请重试' });
  const html = ui.renderResult(failed, run(), {});
  assert.match(html, /<h3>检查未完成<\/h3>/);
  assert.doesNotMatch(html, /<h3>已选规则范围内无修改建议<\/h3>/);
  assert.equal(review.comparisonDetails(run(), failed).current.rule_status, 'error');
  const coverage = ui.renderCoverage(failed);
  assert.doesNotMatch(coverage, /：已执行；比较 1/);
});

test('UX01 legacy status fallback requires complete module data instead of inventing successful coverage', () => {
  const legacyComplete = run({ rule_status: undefined });
  assert.equal(review.comparisonDetails(run(), legacyComplete).current.rule_status, 'pass');
  for (const incomplete of [
    run({ rule_status: undefined, modules: {} }),
    run({ rule_status: undefined, modules: { measurement: { state: 'running', comparable_pairs: 1, skipped_pairs: [], coverage: 'supported_content' } } }),
  ]) {
    const details = review.comparisonDetails(run(), incomplete);
    assert.equal(details.current.rule_status, 'unknown');
    assert.equal(details.current.comparable_pairs, null);
    assert.equal(details.current.coverage_states.measurement, 'unknown');
  }
});

test('UX02 known object keys and reasons become readable without technical tokens', () => {
  for (const reason of [
    '同类片段存在不唯一/未识别对象，不能过滤后猜测配对',
    '对象无法唯一配对', '范围值或多径测量暂未支持',
    '不确定/未知断言不参与肯否冲突比较',
  ]) {
    const human = review.coverageReason(reason);
    assert.ok(typeof human === 'string' && human.length > 0);
    assert.doesNotMatch(human, /过滤后|left|right|\|/);
  }
  assert.match(review.coverageObject('肺上叶|结节|left'), /左/);
  assert.match(review.coverageObject('肺下叶|结节|right'), /右/);
  for (const value of ['肺上叶|结节|left', '肺下叶|结节|right', '肺上叶|结节']) {
    assert.doesNotMatch(review.coverageObject(value), /left|right|\|/);
  }
  const html = ui.renderCoverage(skippedRun());
  assert.doesNotMatch(html, /\|left|过滤后猜测/);
  assert.match(html, /左/);
});

test('UX02 unknown reasons do not invent clinical meaning and raw export evidence is kept', () => {
  const raw = '<img src=x onerror=alert(1)>陌生技术原因';
  const value = review.coverageReason(raw);
  assert.match(value, /人工核对/);
  assert.match(value, /导出/);
  assert.doesNotMatch(value, /已确诊|报告正确|患者|恶性|<img/);
  assert.equal(review.coverageObject('mystery|<script>|left'), '这组描述');
  const source = skippedRun(); source.modules.measurement.skipped_pairs[0].reason = raw;
  const projected = review.exportResult(source);
  assert.equal(projected.modules.measurement.skipped_pairs[0].reason, raw);
});

test('UX02 untrusted evidence is escaped rather than rendered as markup', () => {
  const source = skippedRun();
  source.skipped_segments = [{ source: 'findings', evidence: '<img src=x onerror=alert(1)>8毫米 &报告', reason: '侧别、部位或对象不唯一/未识别' }];
  const html = ui.renderCoverage(source);
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&amp;报告'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script'));
});
