const test=require('node:test'),assert=require('node:assert/strict');
const {metrics}=require('../src/doc_understanding/web/static/repair_metrics.js');
const {verification}=require('../src/doc_understanding/web/static/repair_metrics.js');
test('missing candidate and local refresh never become final approval',()=>{
  for(const item of [undefined,{result:{mode:'local_refresh',candidate_modified:true}}]){
    const v=verification(item);assert.equal(v.candidate,false);assert.equal(v.canConfirm,false);
  }
});
test('passing tests and model agreement never authorize promotion',()=>{
  const v=verification({result:{mode:'automatic',candidate_modified:true,acceptance_passed:true,
    tests:{baseline:0,candidate:0},datasets:[{dataset:{role:'development'},comparison:{}},
      {dataset:{role:'regression'},comparison:{preservation:{passed:true}}}]}});
  assert.equal(v.candidate,true);assert.equal(v.comparison,true);assert.equal(v.tests,true);
  assert.equal(v.regression,true);assert.equal(v.canConfirm,false);
});
test('unknown or failed regression cannot be displayed as passing',()=>{
  for(const comparison of [{},{preservation:{passed:false}}]){
    const v=verification({result:{datasets:[{dataset:{role:'regression'},comparison}]}});
    assert.equal(v.regression,false);assert.equal(v.tests,false);
  }
});
test('missing usage is unknown, not free; changed cells are not accuracy',()=>{
  const m=metrics([{id:'1',costs:[{estimated_cost_usd:2.95},{}],result:{semantic_accuracy_verified:false,datasets:[{comparison:{changed_cell_ids:['x']}}]}}]);
  assert.equal(m.knownCost,2.95);assert.equal(m.unknownCost,1);assert.equal(m.finished,1);assert.equal(m.verified,0);
});
test('empty history does not manufacture trend or accuracy',()=>{
  const m=metrics([]);assert.equal(m.verified,0);assert.equal(m.comparisons.length,0);assert.equal(m.attempts.length,0);
});
