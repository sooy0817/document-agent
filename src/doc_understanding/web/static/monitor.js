/* The monitor observes persisted events; opening it never starts a model call. */
const $=id=>document.getElementById(id), M=MonitorModel;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=v=>new Date(v).toLocaleString('ko-KR');
const verdicts={insufficient_evidence:'근거 부족·판단 불일치 — 확인 필요',suspected_issue:'오류 후보 — 원본 확인 필요',no_structural_issue_detected:'검토 범위 내 이상 미검출 — 의미 승인 아님'};
const modes={agents_roles:'역할·관계 분리 검토',agents_v2:'관계 조회 포함 검토',agents:'기존 분석·검수',local:'로컬 구조 검사'};
const errors={DailyReviewBudgetExceeded:'오늘 모델 호출 한도가 부족합니다. 한도가 회복된 후 재시도해주세요.',ValueError:'검토 범위 또는 모델 응답을 처리하지 못했습니다. 원본과 실행 기록을 확인해주세요.',
  CANDIDATE_COVERAGE_MISMATCH:'Judge가 검토할 관계 후보를 모두 확인하지 않았습니다.',COVERAGE_ENDPOINT_QUOTES_MISSING:'관계 양쪽 셀의 원문 인용이 부족합니다.',QUOTE_NOT_GROUNDED:'일부 인용문이 원문과 일치하지 않습니다.',
  REQUIRED_HEADER_MISSING:'필요하다고 판단한 항목이 경로에서 빠졌습니다. Judge의 판단도 원본 대조가 필요합니다.',JUDGE_AXIS_COVERAGE_MISMATCH:'Judge의 행·열 검토가 중복되거나 빠졌습니다.',
  COVERAGE_REASON_OR_EVIDENCE_MISSING:'검수 판단의 이유나 원본 근거가 빠진 항목이 있습니다.',ROLE_QUOTE_NOT_GROUNDED:'셀 역할을 판단할 때 인용한 문장이 원문과 일치하지 않습니다.',
  VALUE_AXIS_MARKED_NOT_APPLICABLE:'내용·값 셀의 행 또는 열 관계를 적용 대상이 아니라고 판단했습니다. 원본 확인이 필요합니다.',AXIS_NEEDS_HUMAN_REVIEW:'행·열 관계에 미확정 항목이 남아 있습니다.'};
let state=null,selected=new URLSearchParams(location.search).get('request'),detail=null,busy=false,lastList='',lastDetail='';
const drafts=new Map();
async function api(path,body){const opts=body?{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':window.MONITOR_TOKEN},body:JSON.stringify(body)}:{};const r=await fetch(path,opts);const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'요청을 처리하지 못했습니다. 입력을 확인해주세요.');return data;}
function sourceURL(i){return '/?'+new URLSearchParams({job:i.job_id,page:i.selection.page,...(i.selection.case_id?{case:i.selection.case_id}:{})});}
function renderList(){
  if(!state)return;
  const current=M.latest(state.items),alerts=new Set((state.alerts||[]).map(a=>a.request_id));
  const filter=$('filter').value;
  $('menu-count').textContent=alerts.size;
  $('menu-attention').querySelector('.attention-dot').hidden=!alerts.size;
  $('menu-attention').setAttribute('aria-label','내 확인 필요 '+alerts.size+'건');
  const items=M.listItems(state.items,filter,state.alerts||[]);
  $('counters').innerHTML=[['분석 중',current.filter(i=>i.status==='running').length],['실행 대기',current.filter(i=>i.status==='queued').length],['내 확인 필요',alerts.size],['저장된 재검토',state.items.filter(i=>i.parent_id&&i.status==='completed').length]].map(([label,n])=>'<div class="counter"><strong>'+n+'</strong><span>'+label+'</span></div>').join('');
  const sig=JSON.stringify([selected,filter,items.map(i=>[i.id,i.status,i.stage,i.updated_at,i.decision,i.confirmation])]);
  if(sig!==lastList){
    lastList=sig;
    $('requests').innerHTML=items.map(i=>'<button class="request" data-request="'+i.id+'" aria-current="'+(i.id===selected)+'"><strong>'+(alerts.has(i.id)?'<span class="attention-dot" aria-label="확인 필요"></span>':'')+esc(i.document_name)+'</strong><small>'+i.selection.page+'쪽 · '+esc(i.confirmation?'내 확인 완료':alerts.has(i.id)?'내 확인 필요':M.labels[i.status])+'</small><small>'+esc(modes[i.mode]||i.mode)+(i.parent_id?' · 재검토':'')+'</small></button>').join('')||'<p>남은 요청이 없습니다. 확인한 항목은 ‘완료 이력’에서 볼 수 있습니다.</p>';
    $('requests').querySelectorAll('[data-request]').forEach(b=>b.onclick=()=>choose(b.dataset.request));
  }
  $('pause').textContent=state.paused?'검토 재개':'다음 요청부터 일시정지';
}
async function choose(id){
  selected=id;lastDetail='';detail=null;
  history.replaceState(null,'','/monitor?'+new URLSearchParams({request:id}));
  renderList();
  try{const item=await api('/api/review-queue/'+id);if(selected===id){detail=item;renderDetail(item,true);}}
  catch(e){$('action-message').textContent=e.message;}
}
function renderProgress(i){
  renderOpinionProgress(i);
  const stages=M.stages(i),stateLabels={skipped:'호출 생략',pending:'대기 / 실행 기록 없음',visited:'진행 기록 있음',running:'현재 실행 중',failed:'이 단계에서 중단',cancelled:'실행 취소',attention:'내 판단 필요',decided:'판단 저장됨',confirmed:'내 확인 완료'};
  $('flow').innerHTML=stages.map(s=>'<li><button class="'+s.state+'" data-stage="'+s.key+'"'+(s.state==='running'?' aria-current="step"':'')+'><strong>'+esc(s.label)+'</strong><small>'+esc(s.owner)+'</small><small>'+esc(stateLabels[s.state])+'</small></button></li>').join('');
  const active=stages.find(s=>['running','failed','attention'].includes(s.state));
  $('step-explanation').textContent=active?active.label+' — '+active.purpose:i.status==='queued'?'실행 대기 중입니다. 일시정지·예산·앞선 요청의 상태에 따라 시작됩니다.':'실제 단계 이벤트만 표시합니다. 단계 진행은 의미 정확도 통과를 뜻하지 않습니다.';
  $('flow').querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>{const s=stages.find(v=>v.key===b.dataset.stage);$('step-explanation').textContent=s.label+' — '+s.purpose+' 구현 위치: '+s.file;});
  const badge=i.confirmation?'confirmed':M.actionable(i)&&i.status==='completed'?'attention':i.status;
  $('run-status').dataset.state=badge;$('run-status').textContent='원인 분석 · '+(i.confirmation?'내 확인 완료':badge==='attention'?'내 확인 필요':M.labels[i.status]);
  $('code-map').innerHTML=[...new Set(stages.filter(s=>s.key!=='human').map(s=>s.file))].map(file=>'<div class="code-item"><code>'+esc(file)+'</code>'+esc(stages.filter(s=>s.file===file&&s.key!=='human').map(s=>s.label).join(' · '))+'<p>실행 구현 위치 · 자동 수정 없음</p></div>').join('');
  const eventName=e=>({queued:'요청 접수',started:'실행 시작',completed:'결과 저장 완료',failed:'실행 실패',retry:'재시도 요청',cancel:'요청 취소',interrupted:'서버 재시작 후 재개 대기',human_confirmation:e.payload.confirmed?'원본 대조 확인 완료':'원본 대조 확인 취소',human_decision:'사용자 판단 저장·재검토 접수',stage:M.steps[M.stageKey[e.payload.stage]]?.label||e.payload.stage})[e.kind]||e.kind;
  $('timeline').innerHTML=(i.history||[]).map(e=>'<li>'+esc(eventName(e))+'<time>'+date(e.at)+' · '+e.attempt+'회차</time></li>').join('');
  $('executor').textContent=(i.mode==='local'||(i.result?.routing_summary&&!(i.result.call_records||[]).length))?'로컬 규칙 검사 · 모델 호출 없음':(i.model?.startsWith('gpt-4.1-mini')?'GPT-4.1 mini':'GPT-4.1')+' · 역할별 순차 실행';
}
function renderOpinionProgress(i){
  const linked=M.opinionRun(i,state?.items||[]),box=$('opinion-progress');
  const signature=JSON.stringify([i.id,linked,state?.paused]);
  if(box.dataset.signature===signature)return;
  box.dataset.signature=signature;
  if(!linked){box.innerHTML='<strong>이 요청에 전달된 추가 의견은 없습니다.</strong><p>아래 작성 중인 내용은 초안입니다. 저장하면 의견과 재검토 진행 상태가 여기에 표시됩니다.</p>';return;}
  const {run,opinion}=linked;
  const stage=M.steps[M.stageKey[run.stage]]?.label||run.stage;
  const progress=run.confirmation?'원본 대조 확인 완료':run.status==='running'?'진행 중 · '+stage:run.status==='queued'?'접수 완료 · 실행 대기':run.status==='failed'?'실행 중단':run.status==='cancelled'?'취소됨':'재검토 결과 저장됨';
  const reason=run.status==='failed'?(errors[run.error]||run.error):run.status==='completed'?'결과가 저장됐습니다. 개선·수정 완료를 뜻하지는 않습니다. 원본과 다시 비교해주세요.':run.status==='queued'?(state.paused?'검토가 일시정지되어 있습니다.':'앞선 요청과 일일 호출 한도에 따라 시작됩니다.'):'원본을 확인하고 역할 판단 → 관계 판단 → 별도 검수 → 코드 검증을 진행합니다.';
  box.innerHTML='<div class="opinion-title"><h3>내 의견의 재검토</h3><span class="badge">'+esc(progress)+'</span></div><p>의견 접수 '+date(run.created_at)+'</p><details><summary>실제로 전달된 의견 보기</summary><pre>'+esc(opinion.note)+'</pre></details><p>'+esc(reason)+'</p><p class="hint">관계 판단과 별도 검수에 의견을 전달합니다. 셀 역할 판단은 원본을 독립적으로 살핍니다.</p>'+reviewOutcome(run)+'<div class="actions">'+(run.id!==i.id?'<button class="button" data-opinion-run="'+run.id+'">이 의견의 재검토 열기</button>':'')+'</div>';
  box.querySelectorAll('[data-opinion-run]').forEach(b=>b.onclick=()=>choose(b.dataset.opinionRun));
  box.querySelectorAll('[data-compare-cell]').forEach(b=>b.onclick=()=>locateTarget(b.dataset.compareCell));
}
function reviewOutcome(run){
  const v=M.reviewComparison(run,state?.items||[]),before=v.baseline?.result,after=run.result;
  const applied=v.persistedChanged===false?'원본 JSON 동일 · 표 배치 변경 없음':v.persistedChanged===true?'원본 JSON 버전이 달라 직접 비교할 수 없음':'비교할 이전 JSON 근거 없음';
  let html='<div class="application-state"><strong>실제 반영 상태</strong><p>'+esc(applied)+'</p><p>재검토는 분석 의견을 별도 저장합니다. 모델 후보를 원본에 적용하거나 파서를 자동 수정하지 않습니다.</p>'+(run.confirmation?'<p>사용자 확인 완료 · 기본 목록에서 제외됨</p>':'<p>사용자 확인: 아직 완료하지 않음</p>')+'</div>';
  if(run.status!=='completed')return html+'<p>분석이 끝나면 이전 판단과 새 판단의 차이를 여기에 표시합니다.</p>';
  const summary=r=>r?.interpretation?.summary||r?.analysis?.summary||'저장된 분석 요약이 없습니다.';
  html+='<h4>이전에는 / 이번에는</h4>'+(v.baseline?'<p>비교 기준: '+date(v.baseline.created_at)+'의 완료 결과'+(v.skipped?' · 중간 실패·미완료 '+v.skipped+'건 건너뜀':'')+' <button class="text-button" data-opinion-run="'+v.baseline.id+'">기준 결과 보기</button></p>':'<p>비교할 이전 완료 결과가 없습니다.</p>')+
    '<div class="review-delta"><section><strong>이전 분석 의견</strong><p>'+esc(before?verdicts[before.verdict]||before.verdict:'비교 결과 없음')+'</p><details><summary>이전 설명 읽기</summary><p class="model-summary">'+esc(summary(before))+'</p></details></section><section><strong>이번 분석 의견</strong><p>'+esc(verdicts[after?.verdict]||after?.verdict||'판단 정보 없음')+'</p><p class="model-summary">'+esc(summary(after))+'</p></section></div>';
  html+='<p class="comparison-warning">모델의 설명입니다. 사용자 의견에 동의했거나 경로가 달라졌다는 것만으로 오류 해결을 확정하지 않습니다.</p>';
  if(!v.comparable)return html+'<p>동일 원본의 행·열 후보가 양쪽에 있어야 셀별 변경을 계산할 수 있습니다. 변경 건수는 표시하지 않습니다.</p>';
  html+='<div class="opinion-diff"><span>판단 변화 <b>'+v.changed.length+'셀</b></span><span>동일한 판단 <b>'+(v.rows.length-v.changed.length)+'셀</b></span><span>남은 검사 문제 <b>'+v.remaining.length+'종류</b></span></div><p>모델·프롬프트 변동이 포함될 수 있어 의견 입력만의 효과로 해석하지 않습니다. 셀 역할·경로·문맥 연결의 변화를 비교합니다.</p>';
  const role={header:'제목·항목명',description:'설명',value:'값·내용',unknown:'미확정',note:'주석',unit:'단위'};
  const compact=(text,n=180)=>{const s=text.replace(/\s+/g,' ').trim();return esc(s.length>n?s.slice(0,n)+'…':s);};
  const axisText=a=>compact(a.text)+'<small class="axis-status">'+esc(({explicit:'명시적 연결 후보',no_header:'제목 없음 판정',unknown:'미확정'})[a.status]||a.status)+' · 연결 '+a.ids.length+'개</small>';
  const side=s=>s?'<dl><dt>셀 역할</dt><dd>'+esc(role[s.role]||s.role)+(s.role!==s.relationRole?' <span class="comparison-warning">(관계 단계: '+esc(role[s.relationRole]||s.relationRole)+')</span>':'')+'</dd><dt>행 경로</dt><dd>'+axisText(s.row)+'</dd><dt>열 경로</dt><dd>'+axisText(s.column)+'</dd><dt>연결 문맥</dt><dd>'+s.context.length+'개</dd></dl><details><summary>전체 경로·판단 이유·원본 ID</summary><p>행: '+esc(s.row.status)+' · '+esc(s.row.reason)+'</p><pre>'+esc(s.row.text)+'</pre><p>열: '+esc(s.column.status)+' · '+esc(s.column.reason)+'</p><pre>'+esc(s.column.text)+'</pre><pre>'+esc(JSON.stringify({row:s.row.ids,column:s.column.ids,context:s.context},null,2))+'</pre></details>':'<p>이 실행에 해당 셀의 후보가 없습니다.</p>';
  const changedFields=row=>{
    if(!row.before)return '이번에 후보 생성';if(!row.after)return '이번 후보에서 누락';
    const a=row.before,b=row.after,parts=[];
    if(a.role!==b.role||a.relationRole!==b.relationRole)parts.push('셀 역할');
    for(const [key,label] of [['row','행 경로·상태'],['column','열 경로·상태']])if(JSON.stringify([a[key].ids,a[key].status])!==JSON.stringify([b[key].ids,b[key].status]))parts.push(label);
    if(JSON.stringify([...a.context].sort())!==JSON.stringify([...b.context].sort()))parts.push('문맥 연결');
    return parts.length?parts.join(' / ')+' 변경':'판단 유지';
  };
  const card=row=>'<article class="change-card"><p class="change-label">'+esc(changedFields(row))+'</p><div class="change-title"><strong>'+compact(row.quote,160)+'</strong><button class="button" data-compare-cell="'+esc(row.id)+'">이 셀 원본 보기</button></div><small>'+esc(row.id.split('/').slice(-3).join('/'))+'</small><div class="review-delta"><section><h4>이전 후보</h4>'+side(row.before)+'</section><section><h4>이번 후보</h4>'+side(row.after)+'</section></div></article>';
  html+='<p>아래 명칭은 저장된 모델 인용입니다. 인용 검증 실패가 남을 수 있으므로 원본 셀과 대조해주세요.</p>'+v.changed.map(card).join('');
  const unchanged=v.rows.filter(r=>!r.changed);if(unchanged.length)html+='<details><summary>달라지지 않은 '+unchanged.length+'셀 보기</summary>'+unchanged.map(card).join('')+'</details>';
  const checks=(title,values)=>'<section><strong>'+title+' '+values.length+'종류</strong>'+(values.length?'<ul>'+values.map(c=>'<li>'+esc(errors[c]||c)+'</li>').join('')+'</ul>':'<p>없음</p>')+'</section>';
  return html+'<details class="delta-checks"><summary>검사 변화와 남은 문제 확인</summary><div class="review-delta">'+checks('이번에 사라진 검사',v.resolved)+checks('새로 나타난 검사',v.added)+'</div>'+checks('이번에도 확인이 필요한 검사',v.remaining)+'</details>';
}
async function nextAttention(current){
  const next=state.alerts?.find(a=>a.request_id!==current);
  if(next){$('filter').value='attention';lastList='';await choose(next.request_id);}
  else {await choose(current);$('action-message').textContent='확인을 기록했습니다. 지금 남은 확인 요청은 없습니다.';}
  return !!next;
}
function renderDetail(i,force=false){
  if(i.id!==selected)return;
  $('empty').hidden=true;$('selected').hidden=false;
  renderProgress(i);
  const signature=JSON.stringify([i.id,i.updated_at,i.status,i.decision,i.confirmations]);
  if(!force&&signature===lastDetail)return;
  const keepDraft=!force&&$('decision-form')?.dataset.dirty==='true'&&!i.decision&&!i.confirmation;
  lastDetail=signature;
  $('document-title').textContent=i.document_name;$('mode').textContent=modes[i.mode]||i.mode;
  $('selection').textContent=i.selection.page+'쪽 · '+(i.selection.cell_id?'선택 셀':'선택 표·영역')+' · '+date(i.created_at);
  $('issue-text').textContent=i.feedback.note||i.feedback.error_type||'메모 없음';
  const frameURL=sourceURL(i)+'&embed=monitor';
  if($('document-frame').getAttribute('src')!==frameURL)$('document-frame').src=frameURL;
  $('source-link').onclick=openDocument;
  const src='/api/error-basket/'+i.item_id+'/image';if($('source-image').getAttribute('src')!==src)$('source-image').src=src;
  $('parent-link').hidden=!i.parent_id;$('parent-link').onclick=()=>choose(i.parent_id);
  $('child-link').hidden=!i.decision;$('child-link').onclick=()=>choose(i.decision.child_id);
  $('retry').hidden=!['failed','cancelled'].includes(i.status);
  $('cancel').hidden=!['running','queued'].includes(i.status);
  const r=i.result, targets=r?.interpretation?.targets||[];
  $('trace-link').hidden=!r?.trace_url||!/^https?:\/\//.test(r.trace_url);
  if(!$('trace-link').hidden)$('trace-link').href=r.trace_url;
  let contents='<div class="review-status"><strong>'+esc(r?verdicts[r.verdict]||r.verdict:errors[i.error]||i.error||'분석 결과를 기다리고 있습니다.')+'</strong><p>자동 검토는 셀의 역할·행열 연결을 살폈습니다. 화면의 중복 표시나 내부 표 배치가 맞다는 뜻은 아닙니다. 원본과 화면에서 보이는 차이만 위에 남겨주세요.</p></div>';
  if(r?.routing_summary){
    const g=r.routing_summary;
    contents+='<div class="finding"><strong>규칙 우선 검토 · '+g.total+'개 셀</strong><p>규칙 후보 유지 '+g.rule_retained+'개 · LLM 대상 '+g.llm_requested+'개 · LLM 응답 검증 '+g.llm_completed+'개 · 미해결 '+g.unresolved+'개</p><p>처리 수이며 정확도 점수가 아닙니다. 최종 사람 검증은 별도입니다.</p><details><summary>셀별 처리 결과와 이유</summary><table class="paths"><thead><tr><th>셀</th><th>처리</th><th>이유</th></tr></thead><tbody>'+(r.outcomes||[]).map(o=>'<tr><td>'+esc(o.cell_id.split('/').at(-1))+'</td><td>'+esc(({rule_candidate:'규칙 후보',llm_candidate:'LLM 후보',llm_held:'LLM 검토 보류',blocked:'근거 부족·차단',not_run:'검토 미완료'})[o.status]||o.status)+'</td><td>'+esc(o.reasons.join(' · '))+'</td></tr>').join('')+'</tbody></table></details></div>';
  }
  function axis(t,a){const v=t[a+'_axis'];return v.header_ids.map(id=>v.citations.find(c=>c.source_id===id)?.quote||id.split('/').slice(-2).join('/')).join(' → ')||({no_header:'제목 없음 후보',unknown:'미확정'})[v.status]||v.status;}
  if(targets.length)contents+='<details><summary>검토한 행·열 경로 후보 '+targets.length+'건 보기</summary><p>아직 승인되지 않은 제안입니다. 원본 JSON의 경로와 다를 수 있습니다.</p><table class="paths"><thead><tr><th>대상</th><th>행 항목 후보</th><th>열 항목 후보</th></tr></thead><tbody>'+targets.map(t=>'<tr><td>'+esc((r.relations?.targets.find(v=>v.cell_id===t.cell_id)?.target_quote||t.cell_id).slice(0,100))+'</td><td>'+esc(axis(t,'row'))+'</td><td>'+esc(axis(t,'column'))+'</td></tr>').join('')+'</tbody></table></details>';
  if(r?.analysis?.summary)contents+='<div class="finding"><strong>분석 의견</strong><p>'+esc(r.analysis.summary)+'</p></div>';
  contents+='<details class="technical-details"><summary>개발자용 검사 기록 · 직접 해석하지 않아도 됩니다</summary>';
  for(const f of (r?.findings||[]))contents+='<div class="finding"><strong>'+esc(f.message)+'</strong><pre>'+esc(JSON.stringify(f,null,2))+'</pre></div>';
  for(const e of [...new Set([...(r?.validation_errors||[]),...(r?.validation_warnings||[])])])contents+='<div class="finding"><p>'+esc(errors[e]||e)+'</p></div>';
  if(r)contents+='<details><summary>모델 검수와 인용 보기</summary><pre>'+esc(JSON.stringify(r.batch_results||r.judgment||r.judge||{},null,2))+'</pre></details>';
  contents+='</details>';
  if(r)contents+='<p>모델 '+(r.api_calls||0)+'회 · 추정 비용 '+(r.estimated_cost_usd==null?'미확정':'$'+Number(r.estimated_cost_usd).toFixed(6))+'. 정답 승인 아님.</p>';
  $('analysis').innerHTML=contents;
  $('raw-result').textContent=JSON.stringify(i,null,2);
  $('comparison').innerHTML=i.parent_id?'<p>위 ‘내 의견의 재검토’에서 이전 완료 결과와 셀별 판단을 비교합니다. 원래 저장된 비교 기록은 전체 JSON에 보존됩니다.</p>':'<p>재검토가 끝나면 이전 판단과 새 판단을 비교할 수 있습니다.</p>';
  if(!keepDraft)renderDecision(i);
}
function renderDecision(i){
  const box=$('decision-container');
  const isLatest=M.latest(state?.items||[]).some(v=>v.id===i.id);
  if(i.confirmation){
    box.innerHTML='<div class="confirmed-review"><h3>이 선택 영역은 원본 대조 확인을 마쳤습니다</h3><p>'+date(i.confirmation.at)+' · 모델 재호출 없이 검토 완료로 반영했습니다.</p><p>원본 JSON·모델 후보·파서 코드는 변경하지 않았습니다.</p><details><summary>저장한 확인 내용</summary><pre>'+esc(i.confirmation.note)+'</pre></details><div class="actions"><button class="button" id="next-review">다음 확인할 항목</button>'+(isLatest?'<button class="button" id="undo-confirmation">확인 취소하고 다시 검토</button>':'')+'</div></div>';
    $('next-review').onclick=()=>nextAttention(i.id);
    if($('undo-confirmation'))$('undo-confirmation').onclick=async()=>{try{await api('/api/review-queue/'+i.id+'/confirmation',{confirmed:false,note:'사용자가 확인 완료를 취소하고 다시 검토합니다.',source_ids:[]});state=await api('/api/review-queue');await choose(i.id);}catch(e){$('action-message').textContent=e.message;}};
    return;
  }
  if(i.decision){box.innerHTML='<p class="finding">판단이 저장됐습니다: '+esc(i.decision.note)+'. 위 ‘재검토 결과’에서 이후 실행을 확인하세요.</p>';return;}
  if(!isLatest){box.innerHTML='<p class="finding">이전 기록입니다. 왼쪽 최신 요청에서 판단을 남겨주세요.</p>';return;}
  if(!['completed','failed'].includes(i.status)){box.innerHTML='';return;}
  const ids=[...new Set([...(i.result?.interpretation?.targets||[]).map(t=>t.cell_id),i.selection.cell_id].filter(Boolean))];
  const name=id=>(i.result?.relations?.targets||[]).find(t=>t.cell_id===id)?.target_quote||id.split('/').slice(-2).join('/');
  box.innerHTML=`${(i.confirmations||[]).length?'<p class="partial-confirmation">셀 단위 확인 '+i.confirmations.length+'건을 기록했습니다. 선택 영역 전체의 확인은 아직 남아 있습니다.</p>':''}<form id="decision-form">
    <div class="form-heading"><span class="step-number">2</span><div><h3>원본과 화면에서 무엇이 다른가요?</h3><p>원인을 진단할 필요는 없습니다. 직접 보이는 차이를 선택하세요.</p></div></div>
    <label for="review-target">어느 부분을 확인했나요?</label><div class="target-picker"><select id="review-target" name="target_id"><option value="">${i.selection.page}쪽 · 오류 담기 당시 선택 표·영역 전체</option>${ids.map((id,n)=>'<option value="'+esc(id)+'">대상 '+(n+1)+' · '+esc(name(id).slice(0,120))+'</option>').join('')}</select><button type="button" id="locate-target" class="button">원본에서 위치 보기</button></div>
    <p id="target-message" role="status">위 문서에서 검토 대상 셀을 누르면 이 선택에도 연결됩니다. 표 전체 문제는 ‘영역 전체’를 선택하세요.</p>
    <fieldset class="outcomes"><legend>원본과 비교한 결과</legend>${[['issue','차이가 보여요'],['correct','이 범위는 맞아 보여요'],['uncertain','판단하기 어려워요']].map(([v,l])=>'<label><input type="radio" name="outcome" value="'+v+'" required><span>'+l+'</span></label>').join('')}</fieldset>
    <fieldset id="issue-options" hidden><legend>어떤 차이인가요? <small>여러 개 선택 가능</small></legend><div class="observation-options">${Object.entries(M.observations).map(([key,v])=>'<label><input type="checkbox" name="issue" value="'+key+'"><span>'+esc(v.label)+'</span></label>').join('')}</div></fieldset>
    <div id="expected-view" class="expected-view" hidden></div>
    <label for="review-location">찾을 위치나 항목명 <span class="optional">선택</span></label><input id="review-location" name="location" maxlength="200" placeholder="예: 일반중도해지 이자율 오른쪽 설명 셀과 바로 아래 표">
    <label for="review-note">추가로 설명할 내용 <span class="optional">선택</span></label><textarea id="review-note" name="note" rows="2" maxlength="1200" placeholder="예: 아래 표의 모양은 맞아 보입니다. 위 셀 안에 넣고 글 나열은 중복되지 않게 해주세요."></textarea>
    <div class="send-preview"><strong>3. 이렇게 전달됩니다</strong><p id="preview-empty">위에서 비교 결과를 선택하면 보낼 내용을 미리 보여드립니다.</p><pre id="decision-preview" hidden></pre></div>
    <p id="submit-explanation">수정·불확실 의견은 근거 부족 셀만 모델 검토 · 묶음별 2~3회, 일일 한도 적용로 재검토합니다. ‘맞음’은 모델 호출 없이 원본 대조 확인으로 기록합니다.</p><button class="button" type="submit">관찰 내용 저장하고 재검토</button><p id="draft-status" role="status">아직 저장하지 않은 초안입니다.</p><p id="decision-message" role="alert"></p></form>`;
  const form=$('decision-form'),saved=drafts.get(i.id);
  if(saved){for(const k of ['outcome','note','location','target_id'])form.elements[k].value=saved[k]||'';form.querySelectorAll('[name=issue]').forEach(e=>e.checked=saved.issues.includes(e.value));form.dataset.dirty='true';}
  const draft=()=>({outcome:form.elements.outcome.value,note:form.elements.note.value,location:form.elements.location.value,target_id:form.elements.target_id.value,target_label:form.elements.target_id.selectedOptions[0].textContent,issues:[...form.querySelectorAll('[name=issue]:checked')].map(e=>e.value)});
  const preview=()=>{
    const d=draft();$('issue-options').hidden=d.outcome!=='issue';
    const issues=d.outcome==='issue'?d.issues:[];
    $('expected-view').hidden=!issues.length;
    $('expected-view').innerHTML='<strong>원본에서 확인할 것</strong>'+'<ul>'+issues.map(k=>'<li>'+esc(M.observations[k].check)+'</li>').join('')+'</ul>'+(issues.some(k=>['nesting','duplicate'].includes(k))?'<div class="nest-example"><div><span>비교 예시 · 현재 보이는 모습</span><div class="diagram-cell">바깥 셀 안에 글 나열</div><div class="diagram-cell">같은 내용의 안쪽 표가 따로 표시</div></div><b aria-hidden="true">→</b><div><span>원본이 중첩 구조인 경우의 기대 모습</span><div class="diagram-cell">바깥 셀 · 설명문<div class="diagram-child">안쪽 표</div>뒤따르는 주석</div></div></div><p>구조를 설명하는 예시이며, 이 문서의 정답을 확정한 그림은 아닙니다.</p>':'');
    $('preview-empty').hidden=!!d.outcome;$('decision-preview').hidden=!d.outcome;
    $('decision-preview').textContent=M.reviewNote(i,d).note;
    form.querySelector('[type=submit]').textContent=d.outcome==='correct'?(d.target_id?'이 셀은 맞음으로 기록':'맞음으로 기록하고 다음 항목'):'관찰 내용 저장하고 재검토';
    $('submit-explanation').textContent=d.outcome==='correct'?(d.target_id?'이 셀만 확인 완료로 기록합니다. 영역 전체 알림은 유지됩니다. 모델 재호출·JSON 수정은 없습니다.':'선택 영역의 확인을 완료하고 알림을 해제합니다. 모델 재호출·원본 JSON 변경·모델 후보 자동 적용은 없습니다.'):'저장하면 의견과 기존 결과를 보존한 새 재검토가 시작됩니다. 근거 부족 셀만 모델 검토 · 묶음별 2~3회, 일일 한도 적용 · 파서 자동 수정은 아닙니다.';
  };
  form.oninput=e=>{
    form.elements.note.setCustomValidity('');
    if(e.target===form.elements.target_id)$('target-message').textContent=form.elements.target_id.value?'확인 대상이 선택됐습니다. ‘원본에서 위치 보기’로 대조하세요.':'이번 의견은 오류 담기 당시 선택한 표·영역 전체에 연결됩니다.';
    form.dataset.dirty='true';drafts.set(i.id,draft());preview();
  };
  $('locate-target').onclick=()=>locateTarget(form.elements.target_id.value);
  preview();
  form.onsubmit=async e=>{
    e.preventDefault();const d=draft();
    if(d.outcome==='issue'&&!d.issues.length){$('decision-message').textContent='보이는 차이를 한 개 이상 선택해주세요.';return;}
    if(d.outcome==='issue'&&d.issues.includes('other')&&!d.note.trim()){form.elements.note.setCustomValidity('그 밖의 차이를 추가 설명에 적어주세요.');form.elements.note.reportValidity();form.elements.note.oninput=()=>form.elements.note.setCustomValidity('');return;}
    const payload=M.reviewNote(i,d);if(payload.note.length>4000){$('decision-message').textContent='전달할 내용이 너무 깁니다. 추가 설명을 줄여주세요.';return;}
    const button=form.querySelector('[type=submit]');button.disabled=true;$('decision-message').textContent='관찰 내용을 저장하고 재검토를 접수하고 있습니다…';
    try{
      if(d.outcome==='correct'){
        $('decision-message').textContent='원본 대조 확인을 기록하고 있습니다…';
        await api('/api/review-queue/'+i.id+'/confirmation',{confirmed:true,note:payload.note,source_ids:payload.source_ids});
        drafts.delete(i.id);form.dataset.dirty='false';state=await api('/api/review-queue');
        const moved=d.target_id?(await choose(i.id),false):await nextAttention(i.id);
        $('action-message').textContent=d.target_id?'선택한 셀의 확인을 기록했습니다. 나머지 부분도 확인해주세요.':moved?'선택 영역을 맞음으로 기록했습니다. 다음 확인 요청을 표시합니다. 모델 호출은 없습니다.':'선택 영역을 맞음으로 기록했습니다. 남은 확인 요청이 없습니다. 모델 호출은 없습니다.';
      }else{
        const child=await api('/api/review-queue/'+i.id+'/decision',payload);drafts.delete(i.id);form.dataset.dirty='false';state=await api('/api/review-queue');await choose(child.id);$('action-message').textContent='관찰 내용이 저장됐습니다. 아래 ‘내 의견의 재검토’에서 진행 상태를 확인하세요.';$('opinion-progress').scrollIntoView({block:'start'});
      }
    }
    catch(err){$('decision-message').textContent=err.message;button.disabled=false;}
  };
}
function locateTarget(id){
  openDocument();
  if(!id)return;
  const message=$('target-message')||$('action-message');
  const doc=$('document-frame').contentDocument;
  const cell=[...(doc?.querySelectorAll('#result [data-cell]')||[])].find(b=>b.dataset.cell===id);
  if(!cell){message.textContent='현재 문서 탭에 이 셀이 보이지 않습니다. 문서 보기 또는 셀 상세에서 해당 표를 선택해주세요.';return;}
  cell.click();cell.scrollIntoView({block:'nearest'});
  message.textContent='선택한 셀과 원본 좌표가 강조됐습니다. 전체 영역 판단으로 돌아가려면 위 선택을 바꾸세요.';
}
$('document-frame').addEventListener('load',()=>{
  const doc=$('document-frame').contentDocument;
  doc?.addEventListener('click',e=>{
    const cell=e.target.closest('[data-cell]'),form=$('decision-form');if(!cell||!form)return;
    const select=form.elements.target_id;
    if([...select.options].some(o=>o.value===cell.dataset.cell)){
      select.value=cell.dataset.cell;form.dispatchEvent(new Event('input',{bubbles:true}));
      $('target-message').textContent='문서에서 고른 셀이 확인 대상으로 연결됐습니다. 아직 의견을 저장하지 않았습니다.';
    }else $('target-message').textContent='이 셀은 이번 검토 대상 목록에 없습니다. 영역 전체 의견에 위치를 적거나, 문서 화면에서 별도 오류로 담아주세요.';
  });
});
async function poll(){
  if(busy)return;busy=true;
  try{
    state=await api('/api/review-queue');$('network-error').hidden=true;$('connection').textContent='2.5초 간격 갱신 · '+new Date().toLocaleTimeString('ko-KR');
    if(!selected)selected=M.listItems(state.items,'current',state.alerts||[]).find(i=>i.status==='running')?.id||state.alerts?.[0]?.request_id||M.listItems(state.items,'current',state.alerts||[])[0]?.id;
    renderList();
    if(selected){const id=selected,item=await api('/api/review-queue/'+id);if(selected===id){detail=item;renderDetail(item);}}
    if(!state.worker_alive){$('network-error').hidden=false;$('network-error').textContent='검토 작업자가 실행 중이지 않습니다. 저장된 기록을 표시하고 있습니다.';}
  }catch(e){$('connection').textContent='연결 끊김';$('network-error').hidden=false;$('network-error').textContent='서버 상태를 가져오지 못했습니다. 아래는 마지막으로 받은 기록입니다. '+e.message;}
  finally{busy=false;}
}
function openDocument(){if(detail){$('document-review').hidden=false;$('document-review').scrollIntoView({block:'start',behavior:'auto'});}}
$('menu-document').onclick=openDocument;
$('menu-attention').onclick=()=>{$('filter').value='attention';lastList='';renderList();$('requests').scrollIntoView({block:'nearest'});};
$('filter').onchange=()=>{lastList='';renderList();};$('refresh').onclick=poll;
$('pause').onclick=async()=>{if(!state)return;try{await api('/api/review-queue/control',{paused:!state.paused});await poll();}catch(e){$('action-message').textContent=e.message;}};
for(const action of ['retry','cancel'])$(action).onclick=async()=>{if(!selected)return;try{await api('/api/review-queue/'+selected+'/action',{action});lastDetail='';await poll();}catch(e){$('action-message').textContent=e.message;}};
poll();setInterval(()=>{if(!document.hidden)poll();},2500);document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
