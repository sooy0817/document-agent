// Reads persisted requests; the server alone runs bounded, explicitly enabled model review.
const reviewStatus={queued:'접수 · 대기',running:'검사 중',completed:'검사 완료',failed:'실행 실패',cancelled:'취소됨'};
const reviewStages={queued:'실행 대기',source_integrity:'1/4 원본 보존 확인',structure_checks:'2/4 셀·경로 검사',context_checks:'3/4 연결 근거 검사',report_ready:'결과 저장 완료',failed:'실행 실패',cancelled:'취소됨'};
Object.assign(reviewStages,{source_review:'원본 독립 검토',analyst_v2:'관계 조회 기반 분석',expand_evidence:'추가 근거 조회',reanalysis:'보완 근거로 재분석',judge_v2:'원본·분석 비교 검수'});
const reviewVerdicts={suspected_issue:'원본 대조 필요',insufficient_evidence:'근거 부족·판단 불일치 · 보류',no_structural_issue_detected:'검토 범위 내 이상 미검출 · 의미 미확인'};
Object.assign(reviewStages,{collect_evidence:'1/5 원본·주변 근거 수집',analyze:'2/5 모델 오류 분석',judge:'3/5 독립 검수',validate_review:'4/5 근거 ID·인용 검사'});
Object.assign(reviewStages,{classify_roles:'1/3 셀 역할 판단',infer_relations:'2/3 적용 관계 판단',judge_relations:'3/3 관계 검수',validate_relations_and_storage:'원문·누락·저장 관계 검사'});
let queueState=null, queueSelected=null, queueBusy=false, queueSignature='';
const queuePanel=document.createElement('details');queuePanel.id='review-queue-panel';
queuePanel.innerHTML='<summary id="review-queue-title">검토 요청 · 불러오는 중</summary><p class="hint">오류 담기·메모·정답 참조를 저장하면 새 검토 요청이 생깁니다. 기존 기록은 그대로 유지됩니다.</p><p class="hint">현재는 로컬 근거 검사입니다. 이미지 의미 검수·LLM 호출·자동 수정은 하지 않습니다.</p><p id="review-queue-counts" role="status" aria-live="polite"></p><div class="basket-actions"><button class="quiet" id="review-queue-pause">일시정지</button><button class="quiet" id="review-queue-refresh">새로고침</button></div><p class="hint">일시정지는 다음 요청부터 적용됩니다. 진행 중 요청은 개별 취소할 수 있습니다.</p><label class="basket-archived"><input type="checkbox" id="review-queue-history"> 이전 요청도 보기</label><p id="review-queue-message" role="status" aria-live="polite"></p><div id="review-queue-list"></div>';
basketShelf.after(queuePanel);
queuePanel.querySelectorAll('p.hint')[1].textContent='자동 모델 검토는 기존 방식으로 실행합니다. ‘관계 조회 포함 검토 (실험)’는 원본 독립 검토와 추가 조회 최대 1회를 사용하는 별도 실행이며 최대 4회 호출합니다. 두 방식 모두 파서를 수정하지 않습니다.';
const agentControls=document.createElement('div');agentControls.className='basket-actions';
agentControls.innerHTML='<button class="quiet" id="review-agents-toggle">자동 모델 검토 상태 확인</button><p class="hint" id="review-agents-status"></p>';
$('review-queue-counts').before(agentControls);
const queueDialog=document.createElement('dialog');queueDialog.id='review-queue-detail';queueDialog.setAttribute('aria-labelledby','queue-detail-title');
queueDialog.innerHTML='<header><h2 id="queue-detail-title">검토 결과</h2><button class="quiet" id="queue-detail-close">닫기</button></header><div id="queue-detail-body"></div>';
document.body.append(queueDialog);
$('queue-detail-close').onclick=()=>queueDialog.close();
queueDialog.addEventListener('close',()=>queueSelected=null);
const queueDate=s=>new Date(s).toLocaleString('ko-KR');

function renderQueue(){
  if(!queueState)return;
  const all=queueState.items, latest=new Map();
  window.reviewDecisionUI?.refresh(queueState);
  all.forEach(i=>{if(!latest.has(i.item_id))latest.set(i.item_id,i)});
  const current=[...latest.values()], shown=$('review-queue-history').checked?all:current;
  const count=s=>current.filter(i=>i.status===s).length;
  $('review-queue-title').textContent='검토 요청 · '+current.length+'건'+(queueState.paused?' · 일시정지':'');
  $('review-queue-counts').textContent='대기 '+count('queued')+' · 검사 중 '+count('running')+' · 완료 '+count('completed')+' · 실패 '+count('failed')+' · 취소 '+count('cancelled');
  $('review-queue-pause').textContent=queueState.paused?'검토 재개':'일시정지';
  $('review-agents-toggle').textContent=queueState.agents?.enabled?'자동 모델 검토 끄기':'새 요청 자동 모델 검토 켜기';
  $('review-agents-status').textContent=(queueState.agents?.enabled?'새로 담는 요청: 모델 분석 + 별도 검수':'새로 담는 요청: 로컬 검사')+' · 요청당 최대 '+(queueState.agents?.roles_activated_at?3:queueState.agents?.accuracy_activated_at?4:2)+'회 · 하루 최대 '+(queueState.agents?.daily_limit||20)+'회. 모델 동의는 정답 승인이 아닙니다.';
  if(queueState.sync_error)$('review-queue-message').textContent=queueState.sync_error.message;
  $('review-queue-list').innerHTML=shown.map(i=>'<article class="queue-card"><strong>'+esc(i.document_name)+' · '+i.selection.page+'쪽</strong><p><span class="queue-state" data-status="'+i.status+'">'+esc(reviewStatus[i.status])+'</span> '+esc(reviewStages[i.stage]||i.stage)+'</p><p>'+esc(errorLabels[i.feedback.error_type]||i.feedback.error_type)+'</p>'+(i.feedback.note?'<p class="queue-note">'+esc(i.feedback.note)+'</p>':'')+'<p class="hint">'+queueDate(i.created_at)+' · '+i.attempt+'회 실행</p>'+(i.result?'<p>'+esc(reviewVerdicts[i.result.verdict])+'</p><p class="hint">사용자 확인 대기 · 자동 승인 아님</p>':'')+(i.error?'<p class="warning">검사 실행 실패: '+esc(i.error==='DailyReviewBudgetExceeded'?'오늘 모델 호출 한도가 부족합니다. 날짜가 바뀐 후 재시도할 수 있습니다.':i.error)+' · 저장된 의견은 보존됩니다.</p>':'')+'<div class="basket-actions"><button class="quiet" data-queue-detail="'+i.id+'">결과·진행 이력</button><button class="quiet" data-queue-source="'+i.id+'">원본에서 보기</button>'+(['failed','cancelled'].includes(i.status)?'<button class="quiet" data-queue-action="retry" data-request="'+i.id+'">재시도</button>':'')+(['queued','running'].includes(i.status)?'<button class="quiet" data-queue-action="cancel" data-request="'+i.id+'">요청 취소</button>':'')+'</div></article>').join('')||'<p class="hint">오류를 담으면 이곳에서 진행 상태를 확인할 수 있습니다.</p>';
  queuePanel.querySelectorAll('[data-queue-detail]').forEach(b=>b.onclick=()=>openQueueDetail(b.dataset.queueDetail));
  queuePanel.querySelectorAll('[data-queue-source]').forEach(b=>b.onclick=()=>openQueueSource(b.dataset.queueSource));
  queuePanel.querySelectorAll('[data-queue-action]').forEach(b=>b.onclick=()=>queueAction(b.dataset.request,b.dataset.queueAction));
  queuePanel.querySelectorAll('[data-queue-detail]').forEach(b=>{
    const item=all.find(i=>i.id===b.dataset.queueDetail);
    if(item?.mode?.startsWith('agents')){
      const tag=document.createElement('p');tag.className='hint';tag.textContent='모델 분석·검수 요청';b.closest('article').prepend(tag);
    }
    if(item&&item.mode!=='agents_v2'&&queueState.agents?.enabled){
      const run=document.createElement('button');run.className='quiet';run.textContent='관계 조회 포함 검토 (실험)';
      run.onclick=async()=>{run.disabled=true;try{await api('/api/error-basket/'+item.item_id+'/review-request?mode=agents_v2',{method:'POST',headers:{'X-Demo-Token':token}});await loadQueue(true)}catch(e){$('review-queue-message').textContent=e.message;run.disabled=false}};
      b.parentElement.append(run);
    }
  });
}

function agentResultHTML(r){
  if(!r?.mode?.startsWith('agents'))return '';
  if(r.mode==='agents_roles')return '<p>역할 판단과 관계 판단을 분리한 검토입니다. 위 원본·경로·Judge 근거에서 확인하세요.</p>';
  const a=r.analysis,j=r.judge;
  return '<section class="queue-agent-result"><h3>분석 에이전트</h3><p>'+esc(a?.summary||'분석 결과 없음')+'</p>'+(a?.issues||[]).map(i=>'<article class="queue-card"><strong>'+esc(i.issue_id+' · '+i.stage)+'</strong><p>'+esc(i.observation)+'</p><p>제안: '+esc(i.proposal)+'</p><p>'+esc(i.certainty)+'</p><details><summary>분석 근거</summary><pre>'+esc(JSON.stringify({target_ids:i.target_ids,relation:i.relation,source_id:i.source_id,target_id:i.target_id,citations:i.citations},null,2))+'</pre></details></article>').join('')+'<h3>별도 검수 에이전트</h3><p>'+esc(j?.summary||'검수 결과 없음')+'</p>'+(j?.assessments||[]).map(i=>'<article class="queue-card"><strong>'+esc(i.issue_id+' · '+({agree:'동의 후보',disagree:'반대',insufficient_evidence:'근거 부족'})[i.verdict])+'</strong><p>'+esc(i.reason)+'</p><details><summary>검수 근거</summary><pre>'+esc(JSON.stringify(i.citations,null,2))+'</pre></details></article>').join('')+'<p>추가 확인: '+esc([...(a?.missing_evidence||[]),...(j?.missing_evidence||[]),...(j?.additional_concerns||[])].join(' / ')||'모델이 요청한 추가 자료 없음')+'</p><p>코드 검사: '+esc(r.validation_errors?.join(', ')||'ID·인용·판정 개수 검사 통과 (의미 승인 아님)')+'</p><p>문맥 제외 항목: '+(r.evidence_scope?.omitted_ids?.length||0)+'개</p><p>Trace ID: '+esc(r.trace_id||'미연결')+'</p></section>';
}

async function loadQueue(force=false){
  if(queueBusy)return;
  queueBusy=true;
  try{
    const next=await api('/api/review-queue');
    const sig=JSON.stringify(next);
    queueState=next;
    if(force||sig!==queueSignature){queueSignature=sig;renderQueue();if(queueDialog.open&&queueSelected&&!document.querySelector('#review-decision-form[data-dirty="true"]'))await showQueueDetail(queueSelected);}
  }catch(e){$('review-queue-message').textContent='진행 상태를 읽지 못했습니다. 저장된 요청은 유지됩니다: '+e.message;}
  finally{queueBusy=false;}
}
async function openQueueDetail(id){
  queueSelected=id;
  $('queue-detail-body').textContent='검토 기록을 불러오는 중…';
  if(!queueDialog.open)queueDialog.showModal();
  await showQueueDetail(id);
}
async function showQueueDetail(id){
  try{
    const i=await api('/api/review-queue/'+id);if(queueSelected!==id)return;
    const r=i.result;
    $('queue-detail-body').innerHTML='<h3>'+esc(i.document_name)+' · '+i.selection.page+'쪽</h3><p>'+esc(reviewStatus[i.status])+' · '+esc(reviewStages[i.stage]||i.stage)+'</p><p class="queue-note">보낸 의견: '+esc(i.feedback.note||'메모 없음')+'</p><div class="basket-actions"><button class="quiet" id="queue-detail-source">원본·셀·JSON 보기</button><button class="quiet" id="queue-detail-reference">정답 참조 작성 / 보류</button></div>'+(r?'<h3>'+esc(reviewVerdicts[r.verdict])+'</h3><p>검사 대상: '+(r.counts.tables||0)+'표 · '+(r.counts.cells||0)+'셀. 사용자 확인 대기.</p><ul class="queue-findings">'+r.findings.map(f=>'<li><strong>'+esc(f.code)+'</strong><p>'+esc(f.message)+'</p><details><summary>근거 ID '+f.source_ids.length+'개</summary><pre>'+esc(f.source_ids.join('\n'))+'</pre></details></li>').join('')+'</ul><p class="hint">'+r.limitations.map(esc).join('<br>')+'</p><p class="hint">모델 호출 '+r.api_calls+'회 · 추정 비용 '+(r.estimated_cost_usd===null?'미확정':'$'+Number(r.estimated_cost_usd).toFixed(6))+' · 자동 학습/파서 수정 없음</p>':'<p>아직 검수 결과가 없습니다. '+esc(i.error||'상태가 바뀌면 자동으로 갱신됩니다.')+'</p>')+'<details><summary>요청·검수 결과 JSON</summary><pre>'+esc(JSON.stringify(i,null,2))+'</pre></details><h3>처리 이력</h3><ol>'+i.history.map(e=>'<li>'+queueDate(e.at)+' · '+esc(({queued:'접수',started:'검사 시작',stage:reviewStages[e.payload.stage],completed:'검사 완료',failed:'실행 실패',interrupted:'서버 재시작으로 재개 대기',retry:'재시도 요청',cancel:'취소'})[e.kind]||e.kind)+' · '+e.attempt+'회차</li>').join('')+'</ol>';
    $('queue-detail-source').onclick=()=>{queueDialog.close();openQueueSource(id)};
    $('queue-detail-reference').onclick=()=>{queueDialog.close();openReference(i.item_id)};
    window.reviewDecisionUI?.detail(i);
    if(r?.mode?.startsWith('agents')){
      const section=document.createElement('div');section.innerHTML=agentResultHTML(r);
      if(r.mode==='agents_v2'){
        const review=document.createElement('section');
        review.innerHTML='<h3>분석 의견을 보기 전 원본 검토</h3><p>'+esc(r.source_review?.summary||'결과 없음')+'</p><details><summary>원본에서 판단한 관계</summary><pre>'+esc(JSON.stringify(r.source_review,null,2))+'</pre></details><h3>저장된 관계 조회·반증</h3><p>관계가 저장되어 있다는 사실과 원본 의미가 맞는지는 별도로 판단합니다.</p><pre>'+esc(JSON.stringify({missing_claim_checks:r.relation_checks||[],stored_claim_checks:r.stored_claim_checks||[]},null,2))+'</pre><details><summary>최초 분석과 추가 조회 '+(r.expansion_count||0)+'회</summary><pre>'+esc(JSON.stringify({initial_analysis:r.initial_analysis,initial_relation_checks:r.initial_relation_checks,tool_results:r.tool_results},null,2))+'</pre></details>';
        section.prepend(review);
      }
      const notice=document.createElement('p');notice.className='warning';notice.textContent=(reviewVerdicts[r.verdict]||r.verdict)+' — 아래는 모델의 검토 의견이며, 원본과 다를 수 있습니다. 사용자 확인 전 정답으로 사용하지 않습니다.';section.prepend(notice);
      $('queue-detail-source').parentElement.after(section);
      if(r.trace_url && /^https?:\/\//.test(r.trace_url)){
        const link=document.createElement('a');link.href=r.trace_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Langfuse에서 이 검토 확인';section.append(link);
      }
      if(r.estimated_cost_usd===null){
        const p=document.createElement('p');p.className='warning';p.textContent='사용량이 확인되지 않은 호출이 있습니다. 비용은 $0이 아니라 미확정이며 자동 재호출하지 않습니다.';section.append(p);
      }
    }
  }catch(e){$('queue-detail-body').textContent='기록을 읽지 못했습니다: '+e.message;}
}
async function openQueueSource(id){
  const i=queueState?.items.find(v=>v.id===id);if(!i)return;
  await openReview(i.job_id,i.selection.page,i.selection.case_id);
  if(job?.id!==i.job_id)return;
  tableID=i.selection.table_id;tab='structure';
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-selected',String(b.dataset.tab===tab));});
  render();const t=job.result.tables.find(t=>t.id===tableID);
  if(t&&i.selection.cell_id){basketMode='cell';selectCell(t,i.selection.cell_id)}
  else if(t){basketMode='table';selectBasketTable(t)}
}
async function queueAction(id,action){
  try{await api('/api/review-queue/'+id+'/action',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({action})});$('review-queue-message').textContent=action==='cancel'?'요청을 취소했습니다. 원본과 이력은 보존됩니다.':'재시도를 접수했습니다.';await loadQueue(true);}
  catch(e){$('review-queue-message').textContent=e.message;}
}
$('review-queue-pause').onclick=async()=>{
  if(!queueState)return;
  const paused=!queueState.paused;
  try{await api('/api/review-queue/control',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({paused})});$('review-queue-message').textContent=paused?'다음 요청부터 멈춥니다. 진행 중 검사는 완료될 수 있습니다.':'검토를 재개합니다.';await loadQueue(true);}
  catch(e){$('review-queue-message').textContent=e.message;}
};
$('review-queue-refresh').onclick=()=>loadQueue(true);
$('review-queue-history').onchange=renderQueue;
$('review-agents-toggle').onclick=async()=>{
  try{await api('/api/review-agents/control',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({enabled:!queueState?.agents?.enabled})});await loadQueue(true)}
  catch(e){$('review-queue-message').textContent=e.message;}
};
window.addEventListener('review-request-saved',async e=>{
  queuePanel.open=true;
  $('review-queue-message').textContent=e.detail?.review_queue_error||'검토 요청을 접수했습니다. 의견을 다시 입력할 필요가 없습니다.';
  await loadQueue(true);
});
loadQueue();
setInterval(()=>{if(!document.hidden)loadQueue()},2500);
