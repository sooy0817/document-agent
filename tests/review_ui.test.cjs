// Offline script tests with a minimal DOM double; these are not browser visual tests.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('data/rag-phase3/index.html','utf8');
const script=html.match(/<script>([\s\S]*)<\/script>/)[1];
function setup(memory=new Map(),blocked=false){
  class Element {
    constructor(){this.children=[];this.value='';this.style={};this.attributes={};this.dataset={};this.checked=true;this.classList={add(){},remove(){},toggle(){}};this.events={};}
    append(...items){this.children.push(...items)}
    replaceChildren(...items){this.children=items}
    setAttribute(k,v){this.attributes[k]=v}
    addEventListener(k,v){this.events[k]=v}
    click(){this.onclick?.()}
  }
  const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
  get('filter').value='all';
  const choices=['correct','revise','hold'].map(s=>{const e=new Element();e.dataset.decision=s;return e});
  const downloads=[];
  const context=vm.createContext({console,Date,JSON,Blob,setTimeout:f=>f(),URL:{createObjectURL:b=>{downloads.push(b);return 'blob:test'},revokeObjectURL(){}},localStorage:{getItem:k=>{if(blocked)throw Error('blocked');return memory.get(k)||null},setItem:(k,v)=>{if(blocked)throw Error('blocked');memory.set(k,v)}},document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelectorAll:()=>choices,querySelector:()=>get('review'),addEventListener(){}}});
  vm.runInContext(script,context);
  return {get,choices,context,memory,downloads};
}
test('initial page contains source image, evidence highlights and 25 questions',()=>{
  const s=setup();assert.equal(s.get('nav').children.length,25);
  assert.equal(s.get('qid').textContent,'Q01');
  assert.equal(s.get('page-svg').children[0].attributes.href,'review/p42.png');
  assert.ok(s.get('page-svg').children.length>1);
});
test('every question displays specific human review instructions',()=>{
  const s=setup();
  for(let i=0;i<25;i++){
    s.get('nav').children[i].click();
    assert.ok(s.get('guide-where').textContent.length>20);
    assert.ok(s.get('guide-compare').textContent.length>10);
    assert.ok(s.get('guide-caution').textContent.length>20);
    assert.ok(s.get('original-question').textContent.length>0);
  }
  s.get('nav').children[0].click();
  assert.equal(s.get('question').textContent,'재고자산 항목을 빠짐없이 정리했나요?');
  assert.match(s.get('guide-caution').textContent,/숫자는 확인하지 않아도/);
});
test('decision, correction and notes survive navigation and reload',()=>{
  const s=setup();s.choices[1].click();s.get('corrected-answer').value='테스트 정답';s.get('notes').value='테스트 의견';s.get('notes').events.input();s.get('next').click();s.get('prev').click();
  assert.equal(s.get('notes').value,'테스트 의견');
  assert.equal(s.choices[1].attributes['aria-pressed'],'true');
  const reload=setup(s.memory);assert.equal(reload.get('corrected-answer').value,'테스트 정답');assert.equal(reload.get('notes').value,'테스트 의견');
});
test('cross-page question exposes both original pages',()=>{
  const s=setup();s.get('nav').children[15].click();assert.equal(s.get('pages').children.length,2);
  s.get('pages').children[1].click();assert.equal(s.get('page-svg').children[0].attributes.href,'review/p46.png');
});
test('unanswerable question exposes all five pages',()=>{
  const s=setup();s.get('nav').children[23].click();assert.equal(s.get('pages').children.length,5);
});
test('export includes data hashes and review without promoting source answers',async()=>{
  const s=setup();s.choices[0].click();s.get('export').click();const exported=JSON.parse(await s.downloads[0].text());
  assert.equal(exported.schema_version,'rag-human-review/1.0');assert.equal(exported.question_sha256.length,64);
  assert.equal(exported.reviews['NW-RAG-01'].decision,'correct');assert.match(exported.review_scope,/no automatic Gold/);
});
test('storage disabled still permits export',()=>{
  const s=setup(new Map(),true);s.choices[2].click();s.get('export').click();assert.equal(s.downloads.length,1);
});
test('import rejects wrong question version',async()=>{
  const s=setup();await s.get('import').onchange({target:{files:[{size:10,text:async()=>JSON.stringify({schema_version:'wrong'})}],value:'x'}});
  assert.match(s.get('message').textContent,/불러오기 실패/);
});
test('import restores reviewed records into an empty session',async()=>{
  const a=setup();a.choices[0].click();a.get('notes').value='review';a.get('notes').events.input();a.get('export').click();const exported=await a.downloads[0].text();
  const b=setup();await b.get('import').onchange({target:{files:[{size:exported.length,text:async()=>exported}],value:'x'}});
  assert.equal(b.get('notes').value,'review');assert.equal(b.choices[0].attributes['aria-pressed'],'true');
});
