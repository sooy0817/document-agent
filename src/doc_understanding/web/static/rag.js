// 역할: 현재 문서의 RAG 작업과 검색 입력·출처를 표시한다. API 키는 받거나 저장하지 않는다.
(() => {
  const panel = document.createElement('section');
  panel.className = 'rag-panel'; panel.setAttribute('aria-label', '문서 검색과 질문');
  panel.innerHTML = `
    <div class="rag-heading"><div><h2>이 문서로 검색하고 질문하기</h2><p id="rag-document">문서를 업로드하거나 왼쪽 실행 이력에서 완료 결과를 열어주세요.</p></div><span class="badge">원문 근거 연결</span></div>
    <p id="rag-key" class="hint"></p><div id="rag-error" class="error" role="alert" hidden></div>
    <div id="rag-status" class="rag-status" role="status">문서 선택 대기</div><progress id="rag-progress" hidden></progress>
    <fieldset id="rag-controls" disabled>
    <details id="rag-settings"><summary>1. 모델·비용 설정</summary>
      <p class="hint">단가는 공급자 요금표에서 확인한 USD / 100만 토큰을 입력하세요. 기본값은 2026-11-10 OpenAI 공식 모델 페이지 기준입니다(text-embedding-3-small 0.02, gpt-4.1-mini 입력 0.4 · 출력 1.6). 한도는 실행할 때마다 적용되며, 재시도는 새 실행입니다.</p>
      <div class="rag-settings">
        <label>임베딩 공급자<select id="rag-provider"><option value="openai">OpenAI (외부 전송 · 유료)</option><option value="local">로컬 (Ollama · 비용 없음)</option></select></label>
        <label>로컬 서버 주소<input id="rag-base-url" value="http://127.0.0.1:11434"></label>
        <label>임베딩 모델<input id="rag-embed-model" value="text-embedding-3-small"></label>
        <label>벡터 차원<input id="rag-dimensions" type="number" value="1536" min="1" max="3072"></label>
        <label>임베딩 입력 단가<input id="rag-embed-rate" type="number" min="0" step="any" value="0.02" placeholder="확인한 단가"></label>
        <label>임베딩 실행 한도 (USD)<input id="rag-embed-budget" type="number" value="1" min="0.001" max="10" step="0.01"></label>
        <label>답변 모델<input id="rag-model" value="gpt-4.1-mini"></label>
        <label>답변 입력 단가<input id="rag-input-rate" type="number" min="0" step="any" value="0.4" placeholder="확인한 단가"></label>
        <label>답변 출력 단가<input id="rag-output-rate" type="number" min="0" step="any" value="1.6" placeholder="확인한 단가"></label>
        <label>답변 1회 한도 (USD)<input id="rag-answer-budget" type="number" value="0.05" min="0.001" max="10" step="0.01"></label>
        <label>답변 방식<select id="rag-answer-mode"><option value="selection">선택형 (근거 값 선택 · 권장)</option><option value="narrative">서술형 (문장 인용 · 검사가 더 엄격)</option></select></label>
      </div>
      <p class="hint">문서와 질문은 같은 공급자·모델·차원으로 검색합니다. 로컬은 이 컴퓨터의 Ollama(예: <code>ollama pull bge-m3</code>, 1024차원)를 사용하며 문서를 외부로 보내지 않습니다. OpenAI와 답변 생성은 서버의 OPENAI_API_KEY 환경변수를 사용합니다.</p>
    </details>
    <label class="rag-consent"><input type="checkbox" id="rag-consent">OpenAI를 사용할 때 선택한 문서의 검색 텍스트·질문·답변용 근거를 전송하는 데 동의합니다. (로컬 임베딩만 쓸 때는 필요 없습니다.)</label>
    <label class="rag-consent"><input type="checkbox" id="rag-review" checked>구조 검토 후보 적용 (규칙 기반 · 외부 호출 없음): 헤더 근거가 분명한 표의 경로와 중첩 표의 적용 범위를 보완한 별도 문서로 색인합니다. 원본 결과는 바뀌지 않습니다.</label>
    <div class="rag-actions"><button type="button" class="primary" id="rag-prepare">2. 검색 준비 · 임베딩</button><button type="button" class="quiet" id="rag-store">DB 적재만 실행</button></div>
    <details id="rag-entries"><summary>검색 항목 보기 · 실제 임베딩 입력과 원문 위치</summary>
      <p class="hint">본문·표 개요·행·긴 셀 조각별 검색 텍스트입니다. 현재 저장 프로필과 입력 해시로 임베딩 상태를 확인합니다.</p>
      <div class="rag-filters"><label>종류<select id="rag-kind"><option value="">전체</option></select></label><label>페이지<input id="rag-page" type="number" min="1" placeholder="전체"></label><button type="button" class="quiet" id="rag-entries-refresh">항목 확인</button></div>
      <p id="rag-entry-summary" class="hint"></p><div id="rag-entry-list"></div>
      <div class="rag-next"><button type="button" class="quiet" id="rag-previous">이전</button><span id="rag-entry-range"></span><button type="button" class="quiet" id="rag-next">다음</button></div>
    </details>
    <form id="rag-question-form" class="rag-question"><label for="rag-question">3. 문서에 질문하기</label>
      <textarea id="rag-question" maxlength="2000" placeholder="예: 중도해지 시 적용되는 조건과 이율을 알려줘." required></textarea>
      <div class="rag-actions"><label>검색 방식 <select id="rag-mode"><option value="hybrid">하이브리드</option><option value="lexical">키워드</option><option value="dense">벡터</option></select></label><label><input type="checkbox" id="rag-answer">근거로 답변도 생성</label><label><input type="checkbox" id="rag-agent">질문 이해 에이전트 (계획 모델 1회 추가 · 답변 생성 포함)</label><button class="primary" id="rag-ask" type="submit">검색 실행</button></div>
      <div id="rag-ask-error" class="error" role="alert" hidden></div>
      <p class="hint">키워드 검색만 실행하면 외부 모델을 호출하지 않습니다. 검색 결과는 정답이 아니며 원문과 함께 확인하세요.</p>
    </form>
    </fieldset><div id="rag-result" aria-live="polite"></div>`;
  // 긴 검토 이력에 가려지지 않도록 업로드 다음, 원문 뷰어 앞에 둔다.
  document.querySelector('.workspace').before(panel);
  const el = id => document.getElementById('rag-' + id);
  const labels = {queued:'대기',running:'진행 중',stored:'DB 적재 완료 · 임베딩 미실행',ready:'임베딩 완료',partial:'임베딩 일부 완료 · 제외 항목 확인',failed:'실패',interrupted:'중단 · 다시 실행 필요',completed:'검색 완료'};
  const kinds = {table:'표 개요',table_row:'표의 행',table_cell_chunk:'긴 셀 조각',paragraph:'본문',text:'본문',nested_prose:'중첩 표 주변 본문',cell_text:'중첩 표 주변 본문'};
  const errors = {API_KEY_UNAVAILABLE:'서버에 OPENAI_API_KEY가 없습니다. 키를 설정한 PowerShell에서 서버를 다시 실행하세요.',EXTERNAL_CALL_NOT_ENABLED:'외부 전송 동의를 확인하세요.',EMBEDDING_CONFIG_REQUIRED:'임베딩 단가와 설정을 입력하세요.',SEARCH_PREPARATION_REQUIRED:'먼저 검색 준비 또는 DB 적재를 실행하세요.',SEARCH_PREPARATION_STALE:'문서나 검색 버전이 바뀌었습니다. 검색 준비를 다시 실행하세요.',RAG_BUSY:'다른 RAG 작업이 실행 중입니다. 완료 후 다시 시도하세요.',SOURCE_MANIFEST_REQUIRED:'원본 추출 기록을 찾을 수 없습니다. PDF를 새로 업로드해 주세요.',LOCAL_POSTGRES_STATEMENT_FAILED:'DB 연결 또는 SQL 실행에 실패했습니다. Docker와 PostgreSQL 상태를 확인하세요.',EMBEDDING_RUN_BUDGET_EXCEEDED:'임베딩 예산·호출 한도에 도달했습니다. 완료 배치는 보존됩니다.',AGENT_REQUIRES_ANSWER:'질문 이해 에이전트는 답변 생성과 함께 실행합니다.',EMBEDDING_CALL_FAILED_SEE_AUDIT:'임베딩 호출에 실패했습니다. 로컬이면 Ollama 실행 여부와 모델 설치(ollama pull), 차원 설정을 확인하세요. 상세 종류는 비용 기록에 남습니다.',MODEL_REQUEST_BUDGET_EXCEEDED:'답변 입력 또는 비용 한도를 초과했습니다.',NO_MATCHING_EMBEDDINGS:'선택한 모델·차원의 임베딩이 없습니다. 같은 설정으로 검색 준비를 실행하세요.',SERVER_RESTARTED:'서버 재시작으로 중단됐습니다. 완료된 임베딩은 다시 실행할 때 재사용합니다.'};
  let selected = null, state = null, offset = 0, total = 0, lastResult = '', submitting = false, entryVersion = 0;
  // 오류는 패널 위쪽과 질문 버튼 바로 아래에 함께 보여 준다. 버튼을 눌렀는데 반응이 없어 보이지 않게 한다.
  const fail = error => { const text = errors[error.message] || error.message; for (const id of ['error','ask-error']) { el(id).textContent = text; el(id).hidden = false; } el('ask-error').scrollIntoView({block:'nearest'}); };
  const clearErrors = () => { el('error').hidden = true; el('ask-error').hidden = true; };
  function number(id) { const input = el(id); if (!input.value.trim() || !input.checkValidity()) throw Error('모델 설정의 단가·차원·한도를 확인하세요.'); return Number(input.value); }
  const isLocal = () => el('provider').value==='local';
  // 로컬 임베딩은 과금이 없으므로 단가 0, 큰 문서도 한 번에 처리하도록 호출 한도를 높인다.
  function embedding() { const base={model:el('embed-model').value.trim(),dimensions:number('dimensions')}; return isLocal()?{...base,provider:'local',base_url:el('base-url').value.trim(),input_usd_per_million:0,max_cost_usd:1,max_calls:1000,batch_size:8,timeout_seconds:120}:{...base,provider:'openai',input_usd_per_million:number('embed-rate'),max_cost_usd:number('embed-budget')}; }
  function syncProvider(){const local=isLocal();el('base-url').disabled=!local;el('embed-rate').disabled=local;el('embed-budget').disabled=local;}
  el('provider').onchange=()=>{const local=isLocal();el('embed-model').value=local?'bge-m3':'text-embedding-3-small';el('dimensions').value=local?1024:1536;if(!local&&!el('embed-rate').value)el('embed-rate').value='0.02';syncProvider();};syncProvider();
  async function call(path, options) { const response = await fetch(path,options); const value = await response.json(); if(!response.ok)throw Error(response.status===404&&value.detail==='Not Found'?'새 RAG API를 사용하려면 실행 중인 서버를 종료하고 다시 시작하세요.':typeof value.detail==='string'?value.detail:'입력 설정을 확인하세요.'); return value; }
  const base = id => '/api/jobs/'+encodeURIComponent(id)+'/rag';
  function update() {
    const ready = job?.status==='done', id = ready ? job.id : null;
    if (selected !== id) {
      selected=id;state=null;offset=0;lastResult='';entryVersion++;el('result').replaceChildren();el('entry-list').replaceChildren();clearErrors();el('consent').checked=false;
      el('kind').innerHTML='<option value="">전체</option>';el('page').value='';el('entry-summary').textContent='';el('entry-range').textContent='';el('progress').hidden=true;
      el('document').textContent=ready?job.name+' · '+job.pages+'쪽':'문서를 업로드하거나 왼쪽 실행 이력에서 완료 결과를 열어주세요.';
      el('status').textContent=ready?'아직 검색 준비 상태를 확인하지 않았습니다.':'문서 선택 대기';
      if(id)refresh(id,true);
    }
    el('controls').disabled=!ready||submitting||Boolean(state?.busy);
  }
  function renderStatus() {
    const p=state?.prepare,q=state?.question;
    const lines=[p?'검색 준비: '+(labels[p.status]||p.status):'검색 준비 전 · 검색 항목 미리보기는 가능합니다.'];
    if(!state.prepared_current)lines.unshift('⚠ 질문하려면 먼저 "검색 준비 · 임베딩"을 한 번 실행하세요'+(p&&p.index_version&&p.index_version!==state.index_version?' (색인 버전이 '+p.index_version+' → '+state.index_version+'로 바뀜)':'')+'. 한 번 준비하면 임베딩이 저장되어 이후 질문은 질문 문장만 임베딩합니다.');
    if(p?.total!==undefined)lines.push('저장 '+((p.reused||0)+(p.written||0))+' / '+p.total+'개 · 재사용 '+(p.reused||0)+'개 · 제외 '+(p.skipped?.length||0)+'개');
    const review=p?.structure_review;
    if(review)lines.push(review.status==='failed'?'구조 검토 실패('+review.error+') · 원본 문서로 색인':'구조 검토: '+(review.status==='applied'?'적용':'변경 없음')+' · 레이아웃 '+(review.layout_decided||0)+' · 적용 범위 '+(review.scope_decided||0)+' · 답변 가능 값 '+review.usable_values_before+' → '+review.usable_values_after+(review.layout_ambiguous||review.scope_ambiguous?' · 보류 '+((review.layout_ambiguous||0)+(review.scope_ambiguous||0)):''));
    if(p?.error)lines.push(errors[p.error]||p.error);
    if(q)lines.push('질문: '+(labels[q.status]||q.status)+(q.stage==='answering'?' · 답변 생성 중':''));
    if(q?.error)lines.push(errors[q.error]||q.error);
    if(q?.answer_failure_audit){const reserved=q.answer_failure_audit.reserved_cost_usd;lines.push('답변 호출 실패'+(typeof reserved==='number'?' · 예약 비용 $'+reserved.toFixed(6):'')+' · 실제 비용은 로컬 실행 기록 확인');}
    const audit=p?.embedding_audit;
    if(audit)lines.push('임베딩 추정 비용 $'+audit.estimated_cost_usd.toFixed(6)+(audit.cost_complete?'':' · 일부 호출 비용 미확인'));
    el('status').textContent=lines.join('\n');
    el('progress').hidden=!p||!['queued','running'].includes(p.status);
    if(p?.total){el('progress').max=p.total;el('progress').value=(p.reused||0)+(p.written||0);}else el('progress').removeAttribute('value');
    el('key').textContent=state.api_key_available?'서버 API 키 설정됨 · 실제 연결 여부는 호출 시 확인':'서버 API 키 미설정 · 검색 항목 확인과 DB 적재·키워드 검색은 가능';
  }
  async function refresh(id=selected, initial=false) {
    if(!id)return;
    try {
      const value=await call(base(id)); if(selected!==id)return; state=value;
      if(initial && value.prepare?.config.embedding){const c=value.prepare.config.embedding;el('provider').value=c.provider||'openai';el('base-url').value=c.base_url||'http://127.0.0.1:11434';el('embed-model').value=c.model;el('dimensions').value=c.dimensions;if(c.provider!=='local'){el('embed-rate').value=c.input_usd_per_million;el('embed-budget').value=c.max_cost_usd;}syncProvider();}
      renderStatus();update();
      const q=value.question;
      if(q && q.status!=='completed' && q.request_id!==lastResult)el('result').replaceChildren();
      if(q?.status==='completed' && lastResult!==q.request_id){lastResult=q.request_id;renderResult(q);}
    } catch(error){if(selected===id)fail(error);}
  }
  async function start(action,data) {
    if(!selected||submitting)return;
    const id=selected;submitting=true;update();clearErrors();
    try {await call(base(id)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({request_id:crypto.randomUUID().replaceAll('-',''),...data})});await refresh(id);}
    catch(error){if(selected===id)fail(error);}finally{submitting=false;update();}
  }
  el('prepare').onclick=()=>{try{const external=!isLocal();if(external&&!el('consent').checked)throw Error('EXTERNAL_CALL_NOT_ENABLED');start('prepare',{embed:true,allow_external:external,embedding:embedding(),structure_review:el('review').checked});}catch(error){el('settings').open=true;fail(error);}};
  el('store').onclick=()=>start('prepare',{embed:false,structure_review:el('review').checked});
  el('question-form').onsubmit=event=>{event.preventDefault();try{
    const agent=el('agent').checked,mode=el('mode').value,answer=el('answer').checked||agent,external=(mode!=='lexical'&&!isLocal())||answer;
    if(external&&!el('consent').checked)throw Error('EXTERNAL_CALL_NOT_ENABLED');
    const data={question:el('question').value.trim(),mode,answer,agent,allow_external:external};
    if(!data.question)throw Error('질문을 입력하세요.');
    if(mode!=='lexical')data.embedding=embedding();
    if(answer)Object.assign(data,{model:el('model').value.trim(),input_usd_per_million:number('input-rate'),output_usd_per_million:number('output-rate'),max_cost_usd:number('answer-budget'),answer_mode:el('answer-mode').value});
    start('question',data);
  }catch(error){el('settings').open=true;fail(error);}};
  function sourceButton(pageNumber,cellIds=[]) {
    const button=document.createElement('button');button.type='button';button.className='quiet';button.textContent=pageNumber+'쪽 원문 보기';
    button.onclick=()=>{if(!job?.result)return;page=pageNumber;tableID=null;showPage();render();const cell=cellIds[0],table=job.result.tables.find(t=>t.cells.some(c=>c.id===cell));if(table){tab='structure';tableID=table.id;document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-selected',String(b.dataset.tab===tab));});render();selectCell(table,cell);}document.querySelector('.workspace').scrollIntoView({block:'start'});};return button;
  }
  async function showEntries() {
    if(!selected)return;const id=selected,version=++entryVersion;
    const params=new URLSearchParams({offset:String(offset),limit:'15'});if(el('kind').value)params.set('kind',el('kind').value);if(el('page').value)params.set('page',el('page').value);
    try{const data=await call(base(id)+'/entries?'+params);if(selected!==id||version!==entryVersion)return;total=data.filtered;
      const selectedKind=el('kind').value;el('kind').replaceChildren(new Option('전체',''));for(const [k,n] of Object.entries(data.kinds))el('kind').add(new Option((kinds[k]||k)+' · '+n,k));el('kind').value=selectedKind;
      el('entry-summary').textContent='총 '+data.total+'개 · 임베딩 완료 '+data.counts.embedded+' · 대기 '+data.counts.pending+' · 제외 '+data.counts.excluded+' · 상태 미확인 '+data.counts.unknown+(data.database_error?' · DB 상태 조회 실패':'');
      el('entry-list').replaceChildren();for(const item of data.items){const card=document.createElement('article');card.className='rag-entry';const header=document.createElement('header');const title=document.createElement('strong');title.textContent=(kinds[item.kind]||item.kind)+' · '+({embedded:'임베딩 완료',pending:'미임베딩',excluded:'제외',unknown:'상태 미확인'}[item.embedding_status]);header.append(title,sourceButton(item.page,item.cell_ids));card.append(header);const info=document.createElement('p');info.className='hint';info.textContent=item.entry_id+' · '+item.input_bytes+'바이트'+(item.exclusion_reason?' · '+item.exclusion_reason:'');card.append(info);const pre=document.createElement('pre');pre.textContent=item.text;card.append(pre);const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='출처·행열 경로·청크 범위';const metadata=document.createElement('pre');const {text,primary_text,...rest}=item;metadata.textContent=JSON.stringify(rest,null,2);details.append(summary,metadata);card.append(details);el('entry-list').append(card);}
      el('entry-range').textContent=total?(offset+1)+'–'+Math.min(offset+15,total)+' / '+total:'검색 항목 없음';el('previous').disabled=offset===0;el('next').disabled=offset+15>=total;
    }catch(error){if(selected===id)fail(error);}
  }
  el('entries').ontoggle=()=>{if(el('entries').open)showEntries();};el('entries-refresh').onclick=()=>{offset=0;showEntries();};el('kind').onchange=el('page').onchange=()=>{offset=0;showEntries();};el('previous').onclick=()=>{offset=Math.max(0,offset-15);showEntries();};el('next').onclick=()=>{offset+=15;showEntries();};
  function renderResult(task) {
    const target=el('result');target.replaceChildren();const title=document.createElement('h3');title.textContent=task.config.question;target.append(title);
    const meta=document.createElement('p');meta.className='hint';const audit=task.retrieval_audit?.retrieval?.[0];meta.textContent='검색 방식: '+task.config.mode+' · 후보 '+task.hits.length+'개 · 근거 '+task.evidence.length+'개'+(audit?.status==='no_matching_embeddings'?' · 일치하는 벡터가 없어 키워드 검색만 적용됨':'');target.append(meta);
    if(task.agent){const roles={scope:'적용 범위',item:'항목',condition:'조건',other:'기타'};const plan=document.createElement('p');plan.className='hint';const reqs=(task.agent.plan?.requirements||[]).map(r=>'['+(roles[r.role]||r.role)+'] '+r.text).join(' · ');plan.textContent='질문 이해: '+(task.agent.plan_check?.fallback?'계획 실패·원문 검색 ('+(task.agent.plan_check.reasons||[]).join(', ')+')':reqs)+' · 구조 일치 '+(task.agent.structured||[]).length+'개'+(task.agent.retried?' · 근거 부족 구간 1회 재검색':'')+' · 계획 비용 $'+(task.agent.plan_estimated_cost_usd||0).toFixed(6)+(task.agent.trace_id?' · Langfuse trace '+task.agent.trace_id:'');target.append(plan);}
    if(task.result){const answer=document.createElement('div');answer.className='rag-answer';answer.textContent=task.result.answer;target.append(answer);const cost=document.createElement('p');cost.className='hint';cost.textContent='답변 추정 비용 $'+(task.result.estimated_cost_usd||0).toFixed(6)+' · 의미 정확도는 원문 대조가 필요합니다.';target.append(cost);}
    for(const e of task.evidence){const card=document.createElement('article');card.className='rag-entry';const header=document.createElement('header');const title=document.createElement('strong');title.textContent=e.evidence_id+' · '+e.page+'쪽'+(e.lookup_eligible===false?' · 의미 경로 미해결':'');header.append(title,sourceButton(e.page,e.cell_id?[e.cell_id]:[]));const text=document.createElement('p');text.className='rag-evidence';text.textContent=[(e.row_path||[]).join(' → '),(e.column_path||[]).join(' → '),e.value_raw??'(읽기 불가)'].filter(Boolean).join('\n');card.append(header,text);target.append(card);}
    const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='검색 후보·점수·주변 문맥·비용 기록';const pre=document.createElement('pre');pre.textContent=JSON.stringify({hits:task.hits,evidence:task.evidence,retrieval:task.retrieval_audit,embedding:task.embedding_audit,validation:task.result?.assessment_validation},null,2);details.append(summary,pre);target.append(details);
  }
  // 보이지 않는 탭은 상태를 묻지 않는다(여러 탭이 서버를 계속 두드려 질문 처리가 느려졌다). 다시 보이면 바로 한 번 묻는다.
  update();setInterval(()=>{if(document.hidden)return;update();refresh();},2500);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){update();refresh();}});
})();
