import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = await readFile(new URL('../web/review.js', import.meta.url), 'utf8');
const { HISTORY_LIMIT, reviewRows, reviewState, summarizeSession, filterReviewRows, comparableRuns, sourceLabel, taskTitle } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const issue = (id, module) => ({ id, module });
const run = (status, issues = []) => ({ result: { status, issues } });
test('empty session is real zero, not a success rate', () => {
  const summary = summarizeSession([], {});
  assert.equal(summary.retained_run_count, 0); assert.equal(summary.issue_count, 0);
  assert.equal(summary.no_issue_run_count, 0); assert.equal(summary.states.pending, 0);
  assert.ok(!('accuracy' in summary));
});
test('counts runs separately from issue cards and unavailable runs', () => {
  const s = summarizeSession([run('needs_review', [issue('1','laterality'), issue('2','assertion')]), run('pass'), run('unable_to_check')], {});
  assert.equal(s.retained_run_count, 3); assert.equal(s.issue_count, 2);
  assert.equal(s.needs_review_run_count, 1); assert.equal(s.no_issue_run_count, 1); assert.equal(s.unable_run_count, 1);
});
test('feedback partitions issues but never removes them', () => {
  const s = summarizeSession([run('needs_review', [issue('1','laterality'),issue('2','measurement'),issue('3','assertion')])], {'1':'reviewed','2':'inaccurate'});
  assert.deepEqual(s.states, {pending:1, reviewed:1, inaccurate:1}); assert.equal(s.issue_count, 3);
});
test('invalid and unrelated feedback does not count', () => {
  assert.equal(reviewState(issue('1','laterality'), {'1':'fixed','unused':'reviewed'}), 'pending');
  assert.equal(summarizeSession([], {'unused':'reviewed'}).states.reviewed, 0);
});
test('repeat checks are separate runs even with identical text', () => {
  const s = summarizeSession([run('needs_review',[issue('r1','laterality')]),run('needs_review',[issue('r2','laterality')])], {'r1':'reviewed'});
  assert.equal(s.retained_run_count, 2); assert.equal(s.issue_count, 2); assert.equal(s.states.pending, 1);
});
test('filters intersect without modifying global data', () => {
  const history = [run('needs_review',[issue('1','laterality'),issue('2','assertion'),issue('3','laterality')])];
  const rows = reviewRows(history, {'1':'reviewed'});
  assert.equal(filterReviewRows(rows,'laterality','pending').length, 1);
  assert.equal(filterReviewRows(rows,'all','reviewed').length, 1);
  assert.equal(rows.length, 3); assert.equal(summarizeSession(history, {'1':'reviewed'}).issue_count, 3);
});
test('rows retain exact origin indices for drilldown', () => {
  const rows = reviewRows([run('pass'),run('needs_review',[issue('2','measurement')])], {});
  assert.equal(rows[0].runIndex, 1); assert.equal(rows[0].issueIndex, 0); assert.equal(rows[0].issue.id, '2');
});
test('summary only reflects retained window', () => {
  assert.equal(HISTORY_LIMIT, 12);
  const all = Array.from({length:13}, (_,i) => run('needs_review',[issue(String(i),'laterality')]));
  const s = summarizeSession(all.slice(0,HISTORY_LIMIT), {'12':'reviewed'});
  assert.equal(s.retained_run_count,12); assert.equal(s.states.reviewed,0);
});
test('comparison does not attribute AI coverage changes to report correction', () => {
  const a = {task_id:'task-a',checker_version:'v1.3-rules',scope:['measurement'],rule_scope:['measurement'],semantic_review:{state:'disabled'}};
  const b = {...a,scope:['measurement','semantic_review'],semantic_review:{state:'completed'}};
  assert.equal(comparableRuns(a,b),false);
  assert.equal(comparableRuns(b,{...b}),true);
  assert.equal(comparableRuns(a,{...a,rule_scope:['laterality']}),false);
});
test('source names preserve rule, AI-only and shared provenance', () => {
  assert.equal(sourceLabel({source:'rule_and_llm'}),'规则＋AI');
  assert.equal(sourceLabel({source:'llm'}),'AI候选');
  assert.equal(sourceLabel({source:'rule'}),'规则检查');
});
test('semantic issues are included in category counts and filters', () => {
  const history=[run('needs_review',[issue('a','semantic')])];
  assert.equal(summarizeSession(history,{}).modules.semantic,1);
  assert.equal(filterReviewRows(reviewRows(history,{}),'semantic','all').length,1);
});
const sample = {id:'normal',title:'正常报告（两段一致）',findings:'左肺上叶结节。',impression:'左肺上叶结节。'};
test('sample title depends on exact current text, not a retained old verdict', () => {
  assert.equal(taskTitle({sample_id:'normal'},sample.findings,sample.impression,[sample]),sample.title);
  assert.equal(taskTitle({sample_id:'normal'},'左肺上叶8mm结节。','左肺上叶18mm结节。',[sample]),sample.title+'（已修改）');
});
test('restoring exact sample text restores only the source title', () => {
  assert.equal(taskTitle({sample_id:'normal'},sample.findings+' ',sample.impression,[sample]),sample.title+'（已修改）');
  assert.equal(taskTitle({sample_id:'normal'},sample.findings,sample.impression,[sample]),sample.title);
});
test('manual and unavailable sample identities do not invent a source', () => {
  assert.equal(taskTitle({sample_id:null},'a','b',[sample]),'手动模拟文本');
  assert.equal(taskTitle({sample_id:'missing'},'a','b',[sample]),'手动模拟文本');
});
test('modified marker is derived once, not stacked across history edits', () => {
  const origin={sample_id:'normal',task_id:'stable'};
  assert.equal(taskTitle(origin,'edit 1','edit 2',[sample]),sample.title+'（已修改）');
  assert.equal(taskTitle({...origin},'edit 3','edit 4',[sample]),sample.title+'（已修改）');
});
test('task identity, not title, gates comparison', () => {
  const a={task_id:'one',checker_version:'v1.3-rules',title:'old',rule_scope:['measurement'],semantic_review:{state:'disabled'}};
  assert.equal(comparableRuns(a,{...a,title:'old（已修改）'}),true);
  assert.equal(comparableRuns(a,{...a,task_id:'two'}),false);
  assert.equal(comparableRuns({...a,task_id:null},{...a,task_id:null}),false);
});
test('rule version changes cannot be attributed to editing the same task', () => {
  const a={task_id:'one',checker_version:'v1.3-rules',rule_scope:['measurement'],semantic_review:{state:'disabled'}};
  assert.equal(comparableRuns(a,{...a}),true);
  assert.equal(comparableRuns(a,{...a,checker_version:'v1.3.1-rules'}),false);
});
test('missing or empty rule versions cannot support before/after attribution', () => {
  const a={task_id:'one',checker_version:'v1.3-rules',rule_scope:['measurement'],semantic_review:{state:'disabled'}};
  assert.equal(comparableRuns(a,{...a,checker_version:null}),false);
  assert.equal(comparableRuns({...a,checker_version:undefined},{...a,checker_version:undefined}),false);
  assert.equal(comparableRuns({...a,checker_version:''},{...a,checker_version:''}),false);
});
