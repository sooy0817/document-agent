const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../src/doc_understanding/web/static/monitor_model.js');
function item(overrides={}){return {id:'a',item_id:'case',mode:'agents_roles',status:'running',stage:'infer_relations',attempt:1,history:[
  {attempt:1,kind:'stage',payload:{stage:'source_integrity'}},{attempt:1,kind:'stage',payload:{stage:'classify_roles'}},
  {attempt:1,kind:'stage',payload:{stage:'infer_relations'}}],...overrides};}
test('only observed stages are visited; upcoming judge is not completed',()=>{
  const s=M.stages(item());assert.equal(s.find(v=>v.key==='relations').state,'running');
  assert.equal(s.find(v=>v.key==='judge').state,'pending');assert.equal(s.find(v=>v.key==='roles').state,'visited');
});
test('failed run marks last actual stage, not every stage as finished',()=>{
  const s=M.stages(item({status:'failed',stage:'failed'}));assert.equal(s.find(v=>v.key==='relations').state,'failed');
  assert.equal(s.find(v=>v.key==='validate').state,'pending');
});
test('completed but uncertain routes to human, never semantic pass',()=>{
  const i=item({status:'completed',stage:'report_ready',result:{verdict:'insufficient_evidence'}});
  assert.ok(M.actionable(i));assert.equal(M.stages(i).find(v=>v.key==='human').state,'attention');
  i.decision={child_id:'b'};assert.equal(M.actionable(i),false);assert.equal(M.stages(i).find(v=>v.key==='human').state,'decided');
});
test('normal result is history without decision alert',()=>{
  assert.equal(M.actionable(item({status:'completed',result:{verdict:'no_structural_issue_detected'}})),false);
});
test('latest per case suppresses old alerts and old attempts do not imply progress',()=>{
  assert.deepEqual(M.latest([item({id:'b'}),item()]).map(i=>i.id),['b']);
  const s=M.stages(item({attempt:2,stage:'source_integrity'}));assert.equal(s.find(v=>v.key==='roles').state,'pending');
});
test('local runs do not show model roles; implementation paths reflect actual mode',()=>{
  assert.ok(!M.stages(item({mode:'local'})).some(s=>s.owner==='GPT-4.1'));
  assert.equal(M.stages(item({mode:'agents'})).find(s=>s.key==='judge').file,'demo/review_agents.py');
});
test('nested and duplicate observations keep source identity without diagnosing parser',()=>{
  const r=M.reviewNote({document_name:'합성 문서',selection:{page:3}},{outcome:'issue',issues:['nesting','duplicate'],target_id:'doc/table/cell1',target_label:'오른쪽 설명',location:'왼쪽 항목명 옆',note:'아래 표 모양은 맞음'});
  assert.equal(r.choice,'disagree');assert.deepEqual(r.source_ids,['doc/table/cell1']);
  assert.match(r.note,/밖에 따로/);assert.match(r.note,/중복 표시/);assert.match(r.note,/아래 표 모양은 맞음/);
  assert.match(r.note,/실제 원인은 재검토/);assert.match(r.note,/정답 승인 아님/);
});
test('whole-region observation has no fabricated cell ID; unknown stays uncertain',()=>{
  const r=M.reviewNote({document_name:'합성',selection:{page:2}},{outcome:'uncertain',issues:['duplicate'],note:'원본이 흐림'});
  assert.equal(r.choice,'uncertain');assert.deepEqual(r.source_ids,[]);
  assert.doesNotMatch(r.note,/중복 표시/);assert.match(r.note,/판단 보류/);
});
test('changing to no observed difference does not submit previously checked errors',()=>{
  const r=M.reviewNote({document_name:'합성',selection:{page:2}},{outcome:'correct',issues:['nesting']});
  assert.equal(r.choice,'agree');assert.doesNotMatch(r.note,/밖에 따로/);
  assert.match(r.note,/문서 전체나 모든 의미 관계를 승인하는 것은 아닙니다/);
});
test('human confirmation clears attention, partial cell confirmation does not',()=>{
  const i=item({status:'completed',result:{verdict:'insufficient_evidence'},confirmations:[{source_ids:['C']}]});
  assert.ok(M.actionable(i));i.confirmation={confirmed:true,source_ids:[]};
  assert.equal(M.actionable(i),false);assert.equal(M.stages(i).find(s=>s.key==='human').state,'confirmed');
});
test('saved opinion tracks its child execution; a draft is not a submitted opinion',()=>{
  const parent=item({decision:{child_id:'b',note:'내부 표 위치 확인'}});
  const child=item({id:'b',parent_id:'a',submitted_opinion:{note:'내부 표 위치 확인'},status:'queued'});
  assert.equal(M.opinionRun(parent,[child,parent]).run.id,'b');
  assert.equal(M.opinionRun(child,[child,parent]).opinion.note,'내부 표 위치 확인');
  assert.equal(M.opinionRun(item(),[]),null);
});
test('extended categories are carried as observations, never silently dropped',()=>{
  const r=M.reviewNote({document_name:'합성',selection:{page:1}},{outcome:'issue',issues:['merge','header','value','context','continuation']});
  assert.match(r.note,/병합/);assert.match(r.note,/역할/);assert.match(r.note,/기간/);
  assert.match(r.note,/주석/);assert.match(r.note,/페이지 사이/);
});

test('default inbox excludes confirmed requests but retains partial and pending reviews',()=>{
  const done=item({id:'done',item_id:'one',confirmation:{confirmed:true}});
  const partial=item({id:'partial',item_id:'two',confirmations:[{source_ids:['c']}]});
  const old=item({id:'old',item_id:'one'});
  assert.deepEqual(M.listItems([done,partial,old],'current',[]).map(i=>i.id),['partial']);
  assert.deepEqual(M.listItems([done,partial,old],'confirmed',[]).map(i=>i.id),['done']);
  assert.equal(M.listItems([done,partial,old],'all',[]).length,3);
  assert.deepEqual(M.listItems([{...done,confirmation:null},partial],'current',[]).map(i=>i.id),['done','partial']);
});

function candidate(ids,status='explicit'){
  return {cell_id:'value',role:'description',context_ids:[],row_axis:{header_ids:ids,status,citations:ids.map(id=>({source_id:id,quote:id==='head'?'가입대상':'다른 항목'}))},column_axis:{header_ids:[],status:'no_header',citations:[]}};
}
function reviewed(id,parent_id,targets){return item({id,parent_id,status:'completed',source_pdf_sha256:'pdf',canonical_sha256:'json',result:{interpretation:{targets},relations:{targets:[{cell_id:'value',target_quote:'제한없음'}]},validation_errors:[],verdict:'insufficient_evidence'}});}
test('comparison skips failed ancestors without treating missing output as empty paths',()=>{
  const before=reviewed('before',null,[candidate(['head'])]);
  const failed=item({id:'failed',parent_id:'before',status:'failed',result:null});
  const after=reviewed('after','failed',[candidate(['other'])]);
  const v=M.reviewComparison(after,[after,failed,before]);
  assert.equal(v.baseline.id,'before');assert.equal(v.skipped,1);
  assert.equal(v.changed.length,1);assert.equal(v.rows[0].before.row.text,'가입대상');
  assert.equal(v.rows[0].after.row.text,'다른 항목');assert.equal(v.rows[0].quote,'제한없음');
  assert.equal(v.persistedChanged,false);
});
test('same path with different applicability is a change; removed targets remain visible',()=>{
  const before=reviewed('b',null,[candidate([], 'unknown'),{...candidate(['head']),cell_id:'removed'}]);
  const after=reviewed('a','b',[candidate([],'no_header')]);
  const v=M.reviewComparison(after,[after,before]);
  assert.equal(v.changed.length,2);assert.equal(v.rows.find(r=>r.id==='removed').after,null);
});
test('no baseline or changed source cannot be presented as improvement',()=>{
  const after=reviewed('a','missing',[candidate(['head'])]);
  assert.equal(M.reviewComparison(after,[after]).comparable,false);
  const before=reviewed('missing',null,[candidate(['other'])]);before.canonical_sha256='different';
  const v=M.reviewComparison(after,[after,before]);assert.equal(v.comparable,false);assert.equal(v.persistedChanged,true);assert.equal(v.changed.length,0);
});
test('unfinished run does not publish a fabricated before/after comparison',()=>{
  const before=reviewed('b',null,[candidate(['head'])]);
  const after={...reviewed('a','b',[]),status:'running',result:null};
  assert.equal(M.reviewComparison(after,[after,before]).comparable,false);
});

test('rule-only gated review marks model stages skipped, never completed',()=>{
 const i=item({status:'completed',history:[],result:{routing_summary:{total:1,rule_retained:1},call_records:[],verdict:'suspected_issue'}});
 const stages=M.stages(i);
 assert.ok(stages.some(s=>s.key==='gate'));
 for(const key of ['roles','relations','judge'])assert.equal(stages.find(s=>s.key===key).state,'skipped');
 assert.equal(stages.find(s=>s.key==='human').state,'attention');
});
test('an attempted model call is not shown as skipped',()=>{
 const i=item({status:'completed',history:[],result:{routing_summary:{total:1},call_records:[{role:'infer_relations',status:'failed'}]}});
 assert.notEqual(M.stages(i).find(s=>s.key==='relations').state,'skipped');
});
