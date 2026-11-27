// Collection is feedback only. Nothing here edits extraction or approves Gold.
const errorLabels={table_missing:'표 누락',nested_table:'내부 표 누락·부모 연결 오류',cell_boundary:'셀 경계·병합 오류',source_text:'원문 누락·문자 오류',row_path:'행 경로 오류',column_path:'열 경로 오류',context:'설명·기간·주석 연결 오류',other:'기타 / 아직 원인 모름'};
let basketItems=[], basketCell=null, basketTarget=null, basketSaving=false, basketMode='cell';
const basketBox=document.createElement('section');
basketBox.id='basket-controls';basketBox.className='basket-controls hidden';
basketBox.innerHTML='<div class="basket-actions"><button class="quiet" id="collect-cell" disabled>선택한 셀 오류 담기</button><button class="quiet" id="collect-table" disabled>이 표 전체 오류 담기</button><button class="quiet hidden" id="collect-region">선정 영역 오류 담기</button></div><p class="hint" id="basket-selection">오른쪽 표에서 셀을 눌러 주세요.</p><form id="basket-form" class="hidden"><strong id="basket-target"></strong><label for="basket-kind">어떤 부분이 잘못됐나요?</label><select id="basket-kind">'+Object.entries(errorLabels).map(([k,v])=>'<option value="'+k+'">'+v+'</option>').join('')+'</select><label for="basket-note">메모 · 어떻게 보여야 하는지 (선택)</label><textarea id="basket-note" rows="2" maxlength="4000" placeholder="예: 이 설명은 열 제목이 아님 / 바깥 셀 안에 금리표가 빠짐"></textarea><div class="basket-actions"><button class="primary" id="basket-save" type="submit">오류 사례 담기</button><button class="quiet" id="basket-cancel" type="button">접기</button></div><p class="hint">원본 잘라보기·셀 ID·현재 JSON을 로컬에 저장합니다. 정답 승인이나 자동 학습은 아닙니다.</p></form><p id="basket-message" class="hint" role="status" aria-live="polite"></p>';
$('result').before(basketBox);
const basketScopeControls=document.createElement('div');
basketScopeControls.innerHTML='<label for="basket-mode">클릭해서 선택할 범위</label><select id="basket-mode"><option value="cell">셀 선택</option><option value="table">표 전체 선택</option></select><div id="basket-table-picker" class="hidden"><label for="basket-table-choice">담을 표 전체 · 중첩표는 안쪽/바깥쪽을 구분해 선택</label><select id="basket-table-choice"></select></div>';
basketBox.prepend(basketScopeControls);
const basketReferenceNext=document.createElement('button');basketReferenceNext.className='quiet hidden';
basketReferenceNext.textContent='연결까지 지정하기 (선택)';basketBox.append(basketReferenceNext);
const basketShelf=document.createElement('details');basketShelf.id='basket-shelf';
basketShelf.innerHTML='<summary id="basket-count">오류 사례 보관함 · 0건</summary><p class="hint">담은 사례는 서버에 저장됩니다. 제외한 사례도 복원할 수 있습니다.</p><a href="/api/error-basket/export" class="download" download>담은 사례 다운로드 (ZIP)</a><label class="basket-archived"><input id="basket-show-excluded" type="checkbox"> 제외한 사례 보기</label><div id="basket-list"></div>';
$('saved-reviews').after(basketShelf);

function basketSelection(t,id){
  basketCell={job_id:job.id,page,table_id:t.id,cell_id:id};tableID=t.id;
  closeBasketForm();updateBasketControls();
}
function closeBasketForm(){basketTarget=null;$('basket-form').classList.add('hidden');}
function updateBasketControls(){
  const ready=Boolean(job?.result);basketBox.classList.toggle('hidden',!ready);if(!ready)return;
  if(basketCell&&(basketCell.job_id!==job.id||basketCell.page!==page||basketCell.table_id!==tableID))basketCell=null;
  const t=job.result.tables.find(t=>t.id===tableID&&t.page===page);
  const c=t?.cells.find(c=>c.id===basketCell?.cell_id);
  const chosen=Boolean(t);
  $('basket-mode').value=basketMode;$('basket-table-picker').classList.toggle('hidden',basketMode!=='table');
  $('basket-table-choice').innerHTML='<option value="">표를 선택하세요</option>'+job.result.tables.filter(v=>v.page===page).map((v,i)=>'<option value="'+esc(v.id)+'" '+(v.id===tableID?'selected':'')+'>표 '+(i+1)+' · '+v.cells.length+'셀'+(job.result.contexts.some(x=>x.children.includes(v.id))?' · 안쪽 표 후보':'')+'</option>').join('');
  $('collect-cell').disabled=!c||basketSaving;$('collect-table').disabled=!chosen||basketSaving;
  const region=typeof selectedCase!=='undefined'&&selectedCase?.page===page;
  $('collect-region').classList.toggle('hidden',!region);$('collect-region').disabled=basketSaving;
  const already=c&&basketItems.some(i=>i.active&&i.job_id===job.id&&i.cell_id===c.id);
  $('basket-selection').textContent=basketMode==='table'&&t?('선택: 표 '+(job.result.tables.filter(v=>v.page===page).indexOf(t)+1)+' 전체 · '+t.cells.length+'개 셀 · 셀 하나가 아닌 표 전체를 저장합니다.'):c?('선택: '+(c.text.trim()||'빈 셀').slice(0,80)+(already?' · 이미 담은 셀 (다시 저장하면 메모 갱신)':'')):
    chosen?'선택: 현재 표 전체 · 내부 표가 빠졌다면 바깥 표를 담고 표시하세요.':'표의 셀을 누르거나 ‘셀 상세’에서 표를 선택해 주세요.';
}
function paintBasketTable(){
  if(basketMode!=='table'||!job?.result)return;
  const t=job.result.tables.find(t=>t.id===tableID&&t.page===page);if(!t)return;
  const ids=new Set(t.cells.map(c=>c.id));
  document.querySelectorAll('#result [data-cell]').forEach(b=>b.classList.toggle('selected',ids.has(b.dataset.cell)));
  document.querySelectorAll('#result table').forEach(el=>el.classList.toggle('basket-table-selected',el.dataset.table===t.id||(!el.dataset.table&&tab==='structure')));
  const boxes=t.cells.map(c=>c.bbox).filter(Boolean),p=job.result.pages.find(p=>p.number===page),h=$('highlight');
  if(boxes.length&&p&&h){const x0=Math.min(...boxes.map(b=>b.x0)),y0=Math.min(...boxes.map(b=>b.y0)),x1=Math.max(...boxes.map(b=>b.x1)),y1=Math.max(...boxes.map(b=>b.y1));Object.assign(h.style,{display:'block',left:x0/p.width*100+'%',top:y0/p.height*100+'%',width:(x1-x0)/p.width*100+'%',height:(y1-y0)/p.height*100+'%'});}
}
function selectBasketTable(t){
  tableID=t.id;basketCell=null;closeBasketForm();updateBasketControls();paintBasketTable();
  const e=$('evidence');if(e){e.innerHTML='<h3>표 전체 선택 · '+t.cells.length+'개 셀</h3><p>원본에서 초록색으로 표시한 표 전체를 담습니다. 중첩표 범위는 위의 표 목록에서 바꿀 수 있습니다.</p>';}
}
$('basket-mode').onchange=()=>{
  basketMode=$('basket-mode').value;basketCell=null;closeBasketForm();updateBasketControls();
  if(basketMode==='table'){const t=job?.result?.tables.find(t=>t.id===tableID&&t.page===page);if(t)selectBasketTable(t);}
  else{document.querySelectorAll('.basket-table-selected,#result [data-cell].selected').forEach(e=>{e.classList.remove('basket-table-selected');e.classList.remove('selected');});if($('highlight'))$('highlight').style.display='none';}
};
$('basket-table-choice').onchange=()=>{
  const t=job?.result?.tables.find(t=>t.id===$('basket-table-choice').value);if(!t)return;
  tableID=t.id;if(tab==='structure')render();selectBasketTable(t);
};
new MutationObserver(()=>paintBasketTable()).observe($('result'),{childList:true,subtree:true});
function beginCollection(scope){
  if(basketSaving)return;
  basketReferenceNext.classList.add('hidden');
  const t=job.result.tables.find(t=>t.id===tableID&&t.page===page);
  if(scope!=='case_region'&&!t)return;
  if(scope==='cell'&&!basketCell)return;
  basketTarget={job_id:job.id,page,scope,table_id:scope==='case_region'?null:t.id,
    cell_id:scope==='cell'?basketCell.cell_id:null,case_id:typeof selectedCase!=='undefined'&&selectedCase?.page===page?selectedCase.id:null};
  const saved=basketItems.find(i=>i.job_id===job.id&&i.page===page&&i.scope===scope&&
    (scope==='case_region'?i.case_id===basketTarget.case_id:i.table_id===t.id&&i.cell_id===basketTarget.cell_id));
  $('basket-kind').value=saved?.error_type||'other';$('basket-note').value=saved?.note||'';
  $('basket-target').textContent=scope==='cell'?'선택한 셀의 오류':scope==='table'?'선택한 표 전체의 오류':'선정한 원본 영역의 오류 (추출 셀이 없어도 저장)';
  $('basket-message').textContent='';$('basket-form').classList.remove('hidden');$('basket-kind').focus();
}
$('collect-cell').onclick=()=>beginCollection('cell');
$('collect-table').onclick=()=>{basketMode='table';basketCell=null;updateBasketControls();paintBasketTable();beginCollection('table');};
$('collect-region').onclick=()=>beginCollection('case_region');
$('basket-cancel').onclick=closeBasketForm;
$('basket-form').onsubmit=async e=>{
  e.preventDefault();if(!basketTarget||basketSaving)return;
  const {job_id,...target}=basketTarget;
  basketSaving=true;$('basket-save').disabled=true;updateBasketControls();$('basket-message').textContent='원본과 JSON을 저장하고 있습니다…';
  try{
    const collected=await api('/api/jobs/'+job_id+'/error-basket',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({...target,error_type:$('basket-kind').value,note:$('basket-note').value})});
    closeBasketForm();await loadBasket();$('basket-message').textContent=collected.review_queue_error||'메모와 원본을 저장하고 검토를 요청했습니다. 진행 상태는 검토 요청에서 확인하세요. 같은 내용을 다시 입력할 필요가 없습니다.';
    window.dispatchEvent(new CustomEvent('review-request-saved',{detail:collected}));
    basketReferenceNext.onclick=()=>openReference(collected.id);basketReferenceNext.classList.remove('hidden');
    basketShelf.open=true;
  }catch(err){$('basket-message').textContent='저장하지 못했습니다: '+err.message;}
  finally{basketSaving=false;$('basket-save').disabled=false;updateBasketControls();}
};
function renderBasket(){
  const active=basketItems.filter(i=>i.active);
  $('basket-count').textContent='오류 사례 보관함 · '+active.length+'건';
  const shown=basketItems.filter(i=>i.active||$('basket-show-excluded').checked);
  $('basket-list').innerHTML=shown.map(i=>'<article class="basket-item"><strong>'+esc(i.document_name)+' · '+i.page+'쪽</strong><span>'+esc(errorLabels[i.error_type])+' · '+({cell:'셀',table:'표 전체',case_region:'원본 영역'}[i.scope])+(i.active?'':' · 제외됨')+'</span>'+(i.text?'<p>'+esc(i.text)+'</p>':'')+(i.note?'<p>'+esc(i.note)+'</p>':'')+'<p class="hint">'+esc(({unreviewed:"참조 미작성",reference_draft:"정답 참조 초안",uncertain:"불확실",partial_reference:"참조 + 불확실"})[i.gold_status]||i.gold_status)+'</p><div class="basket-actions"><button class="quiet" data-reference-open="'+i.id+'">정답 참조 작성</button><button class="quiet" data-basket-open="'+i.id+'">보기 / 메모 수정</button><button class="quiet" data-basket-active="'+i.id+'">'+(i.active?'제외':'복원')+'</button></div></article>').join('')||'<p class="hint">아직 담은 사례가 없습니다.</p>';
  document.querySelectorAll('[data-reference-open]').forEach(b=>b.onclick=()=>openReference(b.dataset.referenceOpen));
  document.querySelectorAll('[data-basket-open]').forEach(b=>b.onclick=()=>openBasketItem(b.dataset.basketOpen));
  document.querySelectorAll('[data-basket-active]').forEach(b=>b.onclick=async()=>{
    const item=basketItems.find(i=>i.id===b.dataset.basketActive);b.disabled=true;
    try{await api('/api/error-basket/'+item.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify({active:!item.active})});await loadBasket();$('basket-message').textContent=item.active?'보관함에서 제외했습니다. 기록은 남아 있으며 복원할 수 있습니다.':'보관함에 복원했습니다.';}
    catch(err){$('basket-message').textContent=err.message;b.disabled=false;}
  });
}
async function loadBasket(){basketItems=await api('/api/error-basket');renderBasket();updateBasketControls();}
async function openBasketItem(id){
  const item=basketItems.find(i=>i.id===id);if(!item||basketSaving)return;
  basketMode=item.scope==='table'?'table':'cell';
  tab='structure';document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-selected',String(b.dataset.tab===tab));});
  await openReview(item.job_id,item.page,item.case_id);
  if(job?.id!==item.job_id)return;
  tableID=item.table_id;render();const t=job.result.tables.find(t=>t.id===item.table_id);
  if(t&&item.cell_id)selectCell(t,item.cell_id);
  beginCollection(item.scope);
}
$('basket-show-excluded').onchange=renderBasket;
const beforeBasketRender=render;
render=function(){basketCell=null;closeBasketForm();beforeBasketRender();updateBasketControls();};
const beforeBasketSelect=selectCell;
selectCell=function(t,id){
  if(basketMode==='table'){selectBasketTable(t);return;}
  beforeBasketSelect(t,id);
  const action=document.createElement('button');action.className='quiet';action.textContent='이 셀 오류 담기';
  action.onclick=()=>{beginCollection('cell');basketBox.scrollIntoView({block:'nearest'});};
  $('evidence').prepend(action);
};
const beforeBasketPage=showPage;
showPage=function(){basketCell=null;closeBasketForm();beforeBasketPage();updateBasketControls();};
loadBasket().catch(e=>{$('basket-list').textContent='보관함을 읽지 못했습니다: '+e.message;});
