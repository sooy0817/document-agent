// Human references are versioned review records, never edits to extraction.
const roleLabels={unknown:'모름 / 판단 보류',column_header:'열 제목',row_header:'행 이름',row_group:'상위 행 그룹',body:'본문 / 값',note:'설명 / 주석'};
const claimLabels={role:'셀 역할',row_header:'행 이름 → 값·설명',column_header:'열 제목 → 값·설명',row_group:'상위 행 → 하위 항목',table_continuation:'앞 표 → 계속표',nested_table:'부모 셀 → 내부 표',context:'설명·기간·주석 → 적용 대상'};
const referenceStatuses={unreviewed:'참조 미작성',reference_draft:'정답 참조 초안',uncertain:'불확실',partial_reference:'참조 + 불확실'};
let refRecord=null,refCatalog=null,refClaims=[],refEvidence=[],refSelected=null,refSource=null,refTarget=null,refEdit=-1,refDirty=false,refBusy=false,refPage=1;
const refDialog=document.createElement('dialog');refDialog.id='reference-editor';
refDialog.setAttribute('aria-labelledby','ref-title');document.body.append(refDialog);
function refOptions(labels){return Object.entries(labels).map(([k,v])=>'<option value="'+k+'">'+v+'</option>').join('');}
function refName(id){const s=refCatalog?.sources.find(s=>s.id===id);return s?(s.page+'쪽 · '+({cell:'셀',table:'표',span:'문장',page:'페이지'}[s.kind])+' · '+(s.text.trim()||'빈 셀 / 원본 위치').slice(0,90)):id||'선택하지 않음';}
async function openReference(id){
  if(refDialog.open)return;
  try{
    const [record,catalog]=await Promise.all([api('/api/error-basket/'+id),api('/api/error-basket/'+id+'/sources')]);
    refRecord=record;refCatalog=catalog;refClaims=structuredClone(record.reference?.claims||[]);
    refPage=record.snapshot.selection.page;refDirty=false;refBusy=false;
    refDialog.innerHTML=`<div class="ref-top"><div><h2 id="ref-title">정답 참조 작성</h2><p>${esc(catalog.document_name)} · 담은 영역 ${record.snapshot.selection.page}쪽</p></div><button class="quiet" id="ref-close">닫기</button></div>
      <p class="ref-notice">오류 담기는 이미 완료됐습니다. 이 화면은 역할·연결을 구체적으로 지정하는 선택 단계입니다. 닫아도 담은 메모는 유지됩니다.</p>
      <details class="ref-saved-feedback" open><summary>이미 저장한 오류 메모 · 다시 입력하지 않아도 됩니다</summary><p id="ref-original-note">${esc(record.feedback.note||'작성한 메모 없음')}</p><p>담은 대상: ${esc(record.snapshot.selection.scope==='table'?'표 전체':record.snapshot.selection.scope==='cell'?'셀':'원본 영역')} · ${esc(record.snapshot.selection.cell_id||record.snapshot.selection.table_id||record.snapshot.selection.case_id||'')}</p></details>
      <div class="ref-layout"><section aria-label="원본 근거 선택"><div class="ref-filters"><label>원본 페이지<select id="ref-page">${catalog.sources.filter(s=>s.kind==='page').map(s=>`<option value="${s.page}">${s.page}쪽</option>`).join('')}</select></label><label>선택할 종류<select id="ref-kind"><option value="cell">셀</option><option value="table">표</option><option value="span">원문 문장</option><option value="page">페이지 (추출 누락 근거)</option></select></label></div>
      <div class="ref-image-scroll"><div id="ref-sheet"></div></div>
      <label>원문 찾기<input id="ref-search" type="search" placeholder="현재 페이지의 원문 검색"></label><select id="ref-sources" size="5" aria-label="원본 대상 목록"></select><p id="ref-selected" class="ref-selected">원본 위 영역이나 목록을 누르세요.</p>
      <div class="basket-actions"><button class="quiet" id="ref-from">판단할 셀 / 연결 시작으로</button><button class="quiet" id="ref-to">연결 대상으로</button><button class="quiet" id="ref-proof">원본 근거로 추가</button></div><p class="hint">앞뒤 페이지로 이동해 다른 페이지의 셀·문장을 함께 지정할 수 있습니다. 추출되지 않은 내용은 페이지를 근거로 선택하고 위치를 이유에 적어 주세요.</p></section>
      <section aria-label="역할과 연결 작성"><h3 id="ref-edit-title">판단 추가</h3>
      <label>판단 종류<select id="ref-claim-kind">${refOptions(claimLabels)}</select></label>
      <label>확실성<select id="ref-certainty"><option value="proposed">원본으로 판단 가능 · 참조 초안</option><option value="uncertain">불확실 · 정답으로 사용하지 않음</option></select></label>
      <div class="ref-anchor">판단할 셀 / 연결 시작<p id="ref-from-label"></p><button class="quiet" id="ref-clear-from">선택 해제</button></div>
      <div id="ref-role-fields" class="ref-filters"><label>현재 잘못된 역할<select id="ref-observed">${refOptions(roleLabels)}</select></label><label>올바른 역할<select id="ref-expected">${refOptions(roleLabels)}</select></label></div>
      <div id="ref-relation-fields" class="hidden"><div class="ref-anchor">연결 대상<p id="ref-to-label"></p><button class="quiet" id="ref-clear-to">선택 해제</button></div><label>올바른 관계<select id="ref-relation"><option value="link">연결해야 함</option><option value="no_link">연결하면 안 됨</option></select></label></div>
      <label>판단 근거 / 불확실한 이유<textarea id="ref-reason" rows="3" maxlength="4000" placeholder="예: 다음 페이지에 새로운 행 이름이 있어 이전 행의 적용 범위가 끝남"></textarea></label>
      <h4>선택한 원본 근거</h4><div id="ref-evidence"></div><button class="quiet" id="ref-stage">이 판단 목록에 추가</button> <button class="quiet" id="ref-new">새 판단 작성</button>
      <h3>저장할 판단 목록</h3><div id="ref-claims"></div><label>추가 메모 (선택)<textarea id="ref-note" rows="2" maxlength="4000"></textarea></label>
      <details id="ref-history"><summary>이전 정답 참조 이력 (${record.reference_history.length}회)</summary><pre>${esc(JSON.stringify(record.reference_history,null,2))}</pre></details>
      <button class="primary" id="ref-save">정답 참조 저장</button><p id="ref-message" role="status" aria-live="polite"></p></section></div>`;
    $('ref-page').value=refPage;$('ref-note').value=record.reference?.note||'';
    $('ref-close').onclick=closeReference;
    refDialog.oncancel=e=>{e.preventDefault();closeReference();};
    refDialog.oninput=e=>{if(!['ref-search','ref-page','ref-kind','ref-sources'].includes(e.target.id))refDirty=true;};
    $('ref-page').onchange=()=>{refPage=Number($('ref-page').value);refSelected=null;renderReferenceSources();};
    $('ref-kind').onchange=()=>{refSelected=null;renderReferenceSources();};
    $('ref-search').oninput=renderReferenceSources;
    $('ref-sources').onchange=()=>chooseReferenceSource($('ref-sources').value);
    $('ref-from').onclick=()=>{if(refSelected){refSource=refSelected;refDirty=true;renderReferenceAnchors();}};
    $('ref-to').onclick=()=>{if(refSelected){refTarget=refSelected;refDirty=true;renderReferenceAnchors();}};
    $('ref-proof').onclick=()=>{if(refSelected&&!refEvidence.some(e=>e.source_id===refSelected)){const s=refCatalog.sources.find(s=>s.id===refSelected);refEvidence.push({source_id:s.id,quote:['cell','span'].includes(s.kind)?s.text:''});refDirty=true;renderReferenceEvidence();}};
    $('ref-clear-from').onclick=()=>{refSource=null;refDirty=true;renderReferenceAnchors();};
    $('ref-clear-to').onclick=()=>{refTarget=null;refDirty=true;renderReferenceAnchors();};
    $('ref-claim-kind').onchange=()=>{refDirty=true;renderReferenceAnchors();};
    $('ref-stage').onclick=stageReferenceClaim;$('ref-new').onclick=()=>{if(!pendingReference()||confirm('작성 중인 판단을 비우고 새 판단을 작성할까요?'))resetReferenceClaim();};
    $('ref-save').onclick=saveReference;
    resetReferenceClaim();
    if(!record.reference){
      const selection=record.snapshot.selection;
      refSelected=selection.cell_id||selection.table_id||null;
      if(selection.scope==='cell')refSource=selection.cell_id;
      $('ref-kind').value=selection.scope==='table'?'table':'cell';
      $('ref-reason').value=record.feedback.note||'';
    }
    renderReferenceClaims();renderReferenceSources();refDialog.showModal();
  }catch(e){$('basket-message').textContent='정답 참조를 열지 못했습니다: '+e.message;}
}
function closeReference(){if(refBusy)return;if(refDirty&&!confirm('저장하지 않은 변경 사항을 닫을까요?'))return;refDialog.close();refRecord=null;}
function pendingReference(){return Boolean(refSource||refTarget||refEvidence.length||$('ref-reason').value.trim()||refEdit>=0);}
function resetReferenceClaim(){refSource=null;refTarget=null;refEvidence=[];refEdit=-1;$('ref-claim-kind').value='role';$('ref-certainty').value='proposed';$('ref-observed').value='unknown';$('ref-expected').value='unknown';$('ref-reason').value='';$('ref-relation').value='link';$('ref-edit-title').textContent='판단 추가';$('ref-stage').textContent='이 판단 목록에 추가';renderReferenceAnchors();renderReferenceEvidence();}
function renderReferenceAnchors(){
  const role=$('ref-claim-kind').value==='role';$('ref-role-fields').classList.toggle('hidden',!role);$('ref-relation-fields').classList.toggle('hidden',role);$('ref-to').disabled=role||!refSelected;
  $('ref-from-label').textContent=refName(refSource);$('ref-to-label').textContent=refName(refTarget);
}
function chooseReferenceSource(id){refSelected=id;$('ref-sources').value=id;$('ref-selected').textContent=id?refName(id)+'\n'+id:'원본 위 영역이나 목록을 누르세요.';refDialog.querySelectorAll('[data-ref-box]').forEach(b=>b.classList.toggle('selected',b.dataset.refBox===id));$('ref-from').disabled=!id;$('ref-proof').disabled=!id;renderReferenceAnchors();}
function renderReferenceSources(){
  const kind=$('ref-kind').value,q=$('ref-search').value.trim().toLowerCase();
  const sources=refCatalog.sources.filter(s=>s.page===refPage&&s.kind===kind&&(!q||s.text.toLowerCase().includes(q)));
  const pageSource=refCatalog.sources.find(s=>s.page===refPage&&s.kind==='page'),box=pageSource.bbox;
  $('ref-sheet').innerHTML='<img alt="원본" src="/api/jobs/'+encodeURIComponent(refCatalog.job_id)+'/page/'+refPage+'">'+sources.map(s=>{const b=s.bbox;return '<button type="button" data-ref-box="'+esc(s.id)+'" title="'+esc(refName(s.id))+'" aria-label="'+esc(refName(s.id))+'" style="left:'+b.x0/box.x1*100+'%;top:'+b.y0/box.y1*100+'%;width:'+(b.x1-b.x0)/box.x1*100+'%;height:'+(b.y1-b.y0)/box.y1*100+'%"></button>';}).join('');
  $('ref-sheet').querySelector('img').alt='원본 '+refPage+'쪽';
  $('ref-sources').innerHTML=sources.map(s=>'<option value="'+esc(s.id)+'">'+esc(refName(s.id))+'</option>').join('');
  refDialog.querySelectorAll('[data-ref-box]').forEach(b=>b.onclick=()=>chooseReferenceSource(b.dataset.refBox));
  if(!sources.some(s=>s.id===refSelected))refSelected=null;chooseReferenceSource(refSelected);
}
function renderReferenceEvidence(){
  $('ref-evidence').innerHTML=refEvidence.map((e,i)=>'<div class="ref-proof"><label>'+esc(refName(e.source_id))+'<textarea data-ref-quote="'+i+'" rows="2" maxlength="16000" aria-label="원문 인용">'+esc(e.quote)+'</textarea></label><button class="quiet" data-ref-remove-proof="'+i+'">근거 제외</button></div>').join('')||'<p class="hint">왼쪽에서 원본을 선택하고 ‘원본 근거로 추가’를 누르세요.</p>';
  refDialog.querySelectorAll('[data-ref-quote]').forEach(t=>t.oninput=()=>{refEvidence[Number(t.dataset.refQuote)].quote=t.value;refDirty=true;});
  refDialog.querySelectorAll('[data-ref-remove-proof]').forEach(b=>b.onclick=()=>{refEvidence.splice(Number(b.dataset.refRemoveProof),1);refDirty=true;renderReferenceEvidence();});
}
function stageReferenceClaim(){
  const kind=$('ref-claim-kind').value,certainty=$('ref-certainty').value,reason=$('ref-reason').value.trim();
  if(!reason){$('ref-message').textContent='판단 근거 또는 불확실한 이유를 적어 주세요.';return false;}
  if(certainty==='proposed'&&(!refSource||!refEvidence.length||(kind==='role'?$('ref-expected').value==='unknown':!refTarget))){$('ref-message').textContent='대상·올바른 역할 또는 연결·원본 근거를 지정해 주세요. 모르면 불확실로 남길 수 있습니다.';return false;}
  const c={kind,certainty,source_id:refSource,target_id:kind==='role'?null:refTarget,observed_role:$('ref-observed').value,expected_role:$('ref-expected').value,relation:$('ref-relation').value,evidence:structuredClone(refEvidence),reason};
  if(refEdit>=0)refClaims[refEdit]=c;else refClaims.push(c);
  refDirty=true;resetReferenceClaim();renderReferenceClaims();$('ref-message').textContent='목록에 반영했습니다. 아래 ‘정답 참조 저장’을 눌러 서버에 저장하세요.';return true;
}
function renderReferenceClaims(){
  $('ref-claims').innerHTML=refClaims.map((c,i)=>'<article class="ref-claim"><strong>'+(i+1)+'. '+esc(claimLabels[c.kind])+' · '+(c.certainty==='uncertain'?'불확실':'참조 초안')+'</strong><p>'+esc(refName(c.source_id))+'</p><p>'+esc(c.kind==='role'?roleLabels[c.observed_role]+' → '+roleLabels[c.expected_role]:(c.relation==='link'?'연결 → ':'연결 금지 → ')+refName(c.target_id))+'</p><p>'+esc(c.reason)+'</p><div class="basket-actions"><button class="quiet" data-ref-edit="'+i+'">수정</button><button class="quiet" data-ref-delete="'+i+'">목록에서 제외</button></div></article>').join('')||'<p class="hint">아직 판단이 없습니다. 불확실한 이유만 기록해도 됩니다.</p>';
  refDialog.querySelectorAll('[data-ref-edit]').forEach(b=>b.onclick=()=>{if(pendingReference()&&!confirm('작성 중인 판단을 버리고 목록의 판단을 수정할까요?'))return;const i=Number(b.dataset.refEdit),c=refClaims[i];refEdit=i;refSource=c.source_id;refTarget=c.target_id;refEvidence=structuredClone(c.evidence);$('ref-claim-kind').value=c.kind;$('ref-certainty').value=c.certainty;$('ref-observed').value=c.observed_role;$('ref-expected').value=c.expected_role;$('ref-relation').value=c.relation;$('ref-reason').value=c.reason;$('ref-edit-title').textContent=(i+1)+'번 판단 수정';$('ref-stage').textContent='수정한 판단 목록에 반영';renderReferenceAnchors();renderReferenceEvidence();});
  refDialog.querySelectorAll('[data-ref-delete]').forEach(b=>b.onclick=()=>{if(refEdit>=0){$('ref-message').textContent='수정 중인 판단을 먼저 목록에 반영해 주세요.';return;}refClaims.splice(Number(b.dataset.refDelete),1);refDirty=true;renderReferenceClaims();});
}
async function saveReference(){
  if(refBusy)return;
  if(pendingReference()&&!stageReferenceClaim())return;
  if(!refClaims.length){$('ref-message').textContent='판단을 한 개 이상 작성해 주세요.';return;}
  refBusy=true;
  const controls=[...refDialog.querySelectorAll('button,input,select,textarea')].map(el=>[el,el.disabled]);
  controls.forEach(([el])=>el.disabled=true);
  try{
    const response=await fetch('/api/error-basket/'+refRecord.id+'/reference',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({base_revision:refRecord.reference?.revision||null,claims:refClaims,note:$('ref-note').value})});
    const result=await response.json();
    if(!response.ok)throw Error(Array.isArray(result.detail)?result.detail.map(e=>e.msg).join('\n'):result.detail||'저장 실패');
    refRecord=result;refDirty=false;await loadBasket();$('ref-message').textContent='저장했습니다 · '+referenceStatuses[result.gold_status]+' · 기존 원본과 이전 참조 이력은 유지됩니다.'+(result.review_queue_error?' '+result.review_queue_error:' 새 검토 요청에 반영했습니다.');
    window.dispatchEvent(new CustomEvent('review-request-saved',{detail:result}));
    const history=$('ref-history');history.querySelector('summary').textContent='이전 정답 참조 이력 ('+result.reference_history.length+'회)';history.querySelector('pre').textContent=JSON.stringify(result.reference_history,null,2);
  }catch(e){$('ref-message').textContent='저장하지 못했습니다: '+e.message;}
  finally{refBusy=false;controls.forEach(([el,disabled])=>el.disabled=disabled);}
}
window.addEventListener('beforeunload',e=>{if(refDialog.open&&refDirty){e.preventDefault();e.returnValue='';}});
