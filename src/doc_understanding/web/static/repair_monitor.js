/* A proposal, applied code and a saved document are different versions. */
let repairState=null,repairSignature='',repairPreviewKey='';
function renderFinalVerification(item){
 const box=$('final-verification');if(!box)return;
 const v=RepairMetrics.verification(item);
 const rows=[['새 파서 후보',v.candidate],['기존·후보 비교 결과',v.comparison],['기존·후보 실행 검사',v.tests],['기존 문서 보존 회귀 검사',v.regression]];
 box.innerHTML='<p class="eyebrow">마지막 사람 검증</p><h3>'+esc(v.headline)+'</h3>'+
 '<p>이곳에서 원본 PDF와 기존·후보 결과를 비교하게 됩니다. 지금은 아래 준비 상태를 확인해 주세요.</p>'+
 '<ul>'+rows.map(([label,ready])=>'<li>'+esc(label)+' — '+(ready?'기록 있음':'준비 필요')+'</li>').join('')+'</ul>'+
 '<details><summary>후보가 준비되면 무엇을 판단하나요?</summary><table class="repair-matrix"><thead><tr><th>항목</th><th>원본과 대조할 내용</th></tr></thead><tbody>'+
 [['표 모양','표 경계, 행·열 분리, 병합 셀이 맞는지'],['행 경로','값이 올바른 행 제목과 상위 그룹에 연결되는지'],['열 경로','값이 올바른 다단 헤더·기간·단위에 연결되는지'],['값·출처','원문 값이 보존되고 페이지·셀 근거가 맞는지']].map(([a,b])=>'<tr><th>'+a+'</th><td>'+b+'</td></tr>').join('')+
 '</tbody></table><p>각 항목을 맞음 / 틀림 / 판단 불가로 판단합니다. 틀리면 위치와 이유를 남깁니다. 점수 계산은 시스템이 담당합니다.</p></details>'+
 '<p>확정 reference 기반 정확도: 아직 제공되지 않습니다. 변경된 셀 수나 모델의 동의는 정확도 점수가 아닙니다.</p>'+
 '<button class="button" disabled aria-describedby="final-verification-reason">후보 확정·계속</button><p id="final-verification-reason">'+
 (!v.candidate?'새 후보와 비교·회귀 자료가 먼저 필요합니다. ':'')+'판단 저장 → reference 확정 → 재채점 → 후보 반영 연결은 아직 구현 중입니다.</p>'+
 '<p><strong>아래 “관찰 내용 저장하고 재검토”는 추가 오류 의견을 모델에 보내는 기능입니다. 파서 후보를 확정하지 않습니다.</strong></p>';
}
const repairLabels={queued:'실행 대기',running:'수정안 검사 중',applied:'파서에 반영됨',awaiting_source_review:'전후 원본 확인 필요',validation_failed:'검사 실패 · 반영 안 됨',needs_source_reference:'정답 연결 확인 필요 · 반영 안 됨',no_improvement:'개선 미확인 · 반영 안 됨',failed:'실행 실패 · 반영 안 됨',interrupted:'실행 중단 · 반영 안 됨'};
function repairError(e){const reasons={CURRENT_EXTRACTION_FAILED:'후보 생성 전 격리 재추출에 실패했습니다. 새 후보와 채점 결과는 생성되지 않았습니다. 실행 환경 로그를 확인해야 합니다.',EVIDENCE_TOO_LARGE_REQUIRES_NARROWER_SELECTION:'선택 영역의 근거가 너무 커서 모델 호출 전에 멈췄습니다. 표 하나로 범위를 좁혀 주세요.',PATCH_CONTEXT_MISMATCH:'모델이 지정한 수정 위치가 실제 코드와 일치하지 않아 적용하지 않았습니다.',DAILY_CALL_BUDGET:'오늘 수정안 생성 한도에 도달했습니다.',SOURCE_HASH_MISMATCH:'저장 당시 원본과 현재 파일이 달라 실행하지 않았습니다.',WORKSPACE_CHANGED_DURING_VALIDATION:'검사 중 코드가 바뀌어 적용하지 않았습니다.'};return Object.entries(reasons).find(([k])=>String(e).includes(k))?.[1]||'자동 처리 중 문제가 발생했습니다. 이전 결과와 코드는 보존했습니다.';}
function repairTitle(i){return i.result?.stable_parser_modified?'파서에 반영됨':repairLabels[i.status]||i.status;}
function criteriaRows(d){
 const c=d.comparison?.source_checks;if(!c)return '';
 const before=c.baseline.checks.filter(x=>x.passed).length,after=c.candidate.checks.filter(x=>x.passed).length,total=c.candidate.checks.length;
 return '<p><strong>원본 조건 충족: '+before+'/'+total+' → '+after+'/'+total+'</strong> · 전체 의미 정확도와는 별개입니다.</p><details class="technical-detail"><summary>조건별로 무엇이 달라졌는지 보기</summary><table class="repair-matrix"><thead><tr><th>확인할 내용</th><th>수정 전</th><th>수정 후</th></tr></thead><tbody>'+c.candidate.checks.map(x=>'<tr><th>'+esc(x.label)+'</th><td>'+(c.baseline.checks.find(b=>b.id===x.id)?.passed?'충족':'미충족')+'</td><td>'+(x.passed?'충족':'미충족')+'</td></tr>').join('')+'</tbody></table></details>';
}
function previewButtons(i,index){return ['baseline','candidate'].map(v=>'<button class="button" data-repair-preview="'+i.id+','+index+','+v+'">'+(v==='baseline'?'수정 전 보기':'수정 후 보기')+'</button>').join('');}
function resultCard(i){
 const r=i.result,applied=!!r?.stable_parser_modified;
 let h='<article class="repair-card" data-applied="'+applied+'"><p class="eyebrow">선택한 의견의 처리 결과</p><h2>'+esc(repairTitle(i))+'</h2>';
 if(['running','queued'].includes(i.status))h+='<p role="status">'+esc(i.events.at(-1)?.message||'순서를 기다리고 있습니다.')+'</p>';
 if(r){
  h+='<div class="repair-story"><section><h3>무엇을 바꾸려 했나요?</h3><p>'+esc(r.diagnosis)+'</p></section><section><h3>실제로 반영됐나요?</h3><p>'+(applied?'검사를 통과한 코드가 로컬 파서에 반영됐습니다. 아래 ‘수정 후 보기’가 새 실행 결과입니다.':r.candidate_modified?'수정안은 만들었지만 파서에는 반영하지 않았습니다. 아래 수정 후 화면은 시험 결과입니다.':'이번 실행에서는 파서 코드를 바꾸지 않았습니다.')+'</p></section></div>';
  if(r.next_action)h+='<p class="repair-next"><strong>내가 확인할 것</strong><br>'+esc(r.next_action)+'</p>';
  if(i.error||r.error)h+='<p class="repair-next" role="alert">실패 이유: '+esc(repairError(i.error||r.error))+'</p><details><summary>오류 코드</summary><code>'+esc(i.error||r.error)+'</code></details>';
  const rows=r.datasets||[],target=rows.find(d=>d.dataset.role==='development')||rows[0];
  if(target?.comparison){h+=previewButtons(i,rows.indexOf(target))+criteriaRows(target);if(!target.comparison.source_checks)h+='<p>원본 정답 연결이 없어 정확도가 좋아졌는지는 아직 판정하지 않았습니다.</p>';}
  if(rows.length)h+='<details class="technical-detail"><summary>문서별 회귀 검사 '+rows.length+'건</summary><table class="repair-matrix"><thead><tr><th>문서</th><th>검사 결과</th><th>전후 확인</th></tr></thead><tbody>'+rows.map((d,n)=>'<tr><th>'+esc(d.dataset.label==='feedback'?i.document_name:d.dataset.label)+'</th><td>'+(!d.comparison?'추출 실패':d.comparison.preservation?(d.comparison.preservation.passed?'기존 원문·셀·경로·정상 연결 보존':'보존 검사 실패'):d.comparison.native_text_preserved?'원문 보존 · 구조 정확도 미검증':'원문 차이 발생')+'</td><td>'+(d.comparison?previewButtons(i,n):'비교 불가')+'</td></tr>').join('')+'</tbody></table><p>새 Holdout '+(r.holdout_count||0)+'건. 개발 사례의 통과를 범용성으로 해석하지 않습니다.</p></details>';
  h+='<details class="technical-detail"><summary>어느 코드를 왜 바꿨나요?</summary>'+(r.changed_files||[]).map(f=>'<p>'+esc(f.reason)+'<br><code>'+esc(f.path)+'</code></p>').join('')+(r.candidate_modified?'<a href="/api/parser-repairs/'+i.id+'/diff" target="_blank" rel="noopener">실제 코드 차이 보기 ↗</a>':'코드 변경 없음')+'<p>'+esc(r.limits)+'</p></details>';
 }
 return h+'<details class="technical-detail"><summary>실행 로그 · 이전 시도 '+(i.attempt_history||[]).length+'건</summary><ol>'+i.events.map(e=>'<li>'+esc(e.message)+' <small>'+date(e.at)+'</small></li>').join('')+'</ol>'+(i.attempt_history||[]).map(a=>'<p>'+esc(a.attempt)+' · '+esc(repairLabels[a.status]||a.status)+' · '+esc(a.diagnosis)+'</p>').join('')+'</details></article>';
}
async function showRepairPreview(id,index,v,scroll=true){
 try{
  const result=await api('/api/parser-repairs/'+id+'/preview/'+index+'/'+v,{}),i=repairState.items.find(x=>x.id===id),r=i.result;
  $('repair-preview').hidden=false;
  $('document-review').hidden=true;
  $('repair-preview-label').textContent=(v==='baseline'?'수정 전':r.stable_parser_modified?'반영된 수정 후':'미반영 수정안')+' · 실제 문서·셀·JSON';
  $('repair-frame').src='/?job='+result.job_id+'&page='+(Number(index)===0?(r.target_page||i.page||1):1);
  repairPreviewKey=[id,index,v].join(',');
  if(scroll)$('repair-preview').scrollIntoView({block:'start',behavior:'smooth'});
 }catch(e){$('repair-status').textContent=e.message;}
}
async function pollRepairs(){
 try{
  repairState=await api('/api/parser-repairs');
  const sig=JSON.stringify([repairState,selected]);if(sig===repairSignature)return;repairSignature=sig;
  const {items,config:cfg}=repairState,box=$('parser-experiments'),i=items.find(x=>x.review_id===selected),applied=items.find(x=>x.result?.stable_parser_modified),cost=RepairMetrics.metrics(items);
  renderFinalVerification(i);
  $('improvement-summary').innerHTML='<div class="parser-current"><div><strong>현재 파서</strong><p>'+(applied?'마지막 반영: '+esc(applied.document_name)+' '+(applied.page||'')+'쪽 · '+date(applied.updated_at):'아직 자동 반영된 수정 없음')+'</p></div>'+(applied?'<button class="button" data-repair-review="'+applied.review_id+'">반영 결과 보기</button>':'')+'</div><details class="technical-detail"><summary>자동 처리 '+(cfg.enabled?'켜짐':'꺼짐')+' · 설정과 지난 기록</summary><p>새 오류 의견마다 수정안을 한 번 생성합니다. 후보 생성과 검사까지만 자동화합니다. 최종 사람 검증 후 반영 연결은 준비 중입니다. 이전 문서는 덮어쓰지 않습니다.</p><label>처리 방식 <select id="repair-mode"><option value="automatic" '+(cfg.mode==='automatic'?'selected':'')+'>GPT-4.1 mini 수정안 → 검사 → 사람 검증 대기</option><option value="local_refresh" '+(cfg.mode==='local_refresh'?'selected':'')+'>재추출만 · API 호출 없음</option></select></label><button class="button" id="repair-toggle">'+(cfg.enabled?'자동 처리 끄기':'자동 처리 켜기')+'</button><p>하루 최대 '+cfg.daily_limit+'회 수정안 생성. 자동 재시도 없음. Claude는 사용하지 않습니다. 역할·의미 경로 변경은 아직 자동 반영 대상이 아닙니다.</p><p>기록된 모델 추정 비용 $'+cost.knownCost.toFixed(4)+' · 비용 미확인 '+cost.unknownCost+'회 (과거 Claude 기록 포함, 실제 청구액과 다를 수 있음)</p><ul class="repair-history">'+items.map(x=>'<li><button class="text-button" data-repair-review="'+x.review_id+'">'+esc(x.document_name)+' '+(x.page||'')+'쪽 · '+esc(repairTitle(x))+'</button></li>').join('')+'</ul></details>';
  box.innerHTML=i?resultCard(i):'<div class="repair-empty"><strong>이 의견의 파서 수정 결과는 아직 없습니다.</strong><p>새 오류 의견은 자동 접수됩니다. 이전 의견도 아래 버튼으로 접수할 수 있습니다.</p><button class="button" id="repair-current" '+(!selected?'disabled':'')+'>이 의견으로 수정안 요청</button></div>';
  if(!i||!repairPreviewKey.startsWith(i.id+',')){
   $('repair-preview').hidden=true;$('document-review').hidden=false;repairPreviewKey='';
   if(i?.result?.stable_parser_modified&&i.result.datasets?.[0]?.comparison)await showRepairPreview(i.id,0,'candidate',false);
  }
  $('repair-toggle').onclick=async()=>{try{await api('/api/parser-repairs/control',{enabled:!cfg.enabled,mode:$('repair-mode').value});repairSignature='';await pollRepairs();}catch(e){$('repair-status').textContent=e.message;}};
  $('repair-mode').onchange=async()=>{await api('/api/parser-repairs/control',{enabled:false,mode:$('repair-mode').value});repairSignature='';await pollRepairs();};
  if($('repair-current'))$('repair-current').onclick=async()=>{try{await api('/api/parser-repairs/from-review/'+selected,{});repairSignature='';await pollRepairs();}catch(e){$('repair-status').textContent=e.message;}};
  document.querySelectorAll('[data-repair-review]').forEach(b=>b.onclick=async()=>{await choose(b.dataset.repairReview);await pollRepairs();});
  box.querySelectorAll('[data-repair-preview]').forEach(b=>b.onclick=()=>showRepairPreview(...b.dataset.repairPreview.split(',')));
  $('repair-status').textContent=repairState.sync_error?'자동 처리 오류: '+repairState.sync_error:'';
 }catch(e){$('repair-status').textContent='파서 수정 결과를 불러오지 못했습니다: '+e.message;}
}
pollRepairs();setInterval(()=>{if(!document.hidden)pollRepairs();},3000);
