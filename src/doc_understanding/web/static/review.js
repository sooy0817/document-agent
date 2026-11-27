// Saved experiments are opened read-only. Human notes stay separate in this browser.
let savedReviews=[], selectedCase=null;
const reviewKey=()=> 'folio-review:'+job.result.document_id+':'+selectedCase.id;
async function openReview(id, requestedPage, requestedCase){
  if(busy)return;
  try{
    job=await api('/api/jobs/'+encodeURIComponent(id));
    if(job.status!=='done')throw Error('완료된 저장 결과가 아닙니다.');
    page=Math.max(1,Math.min(job.pages,Number(requestedPage)||job.review?.cases[0]?.page||1));
    tableID=null;selectedCase=job.review?.cases.find(c=>c.id===requestedCase)||job.review?.cases.find(c=>c.page===page)||null;
    $('name').textContent=job.name;$('badge').textContent='동결 결과 · 사람 검토 대기';
    $('download').href='/api/jobs/'+job.id+'/download/document';$('download').classList.remove('hidden');
    $('error').classList.add('hidden');$('saved-reviews').value=id;
    $('live').textContent='저장된 추출 결과입니다. 모델 검토는 오류 담기·검토 큐에서 별도로 실행하며 원본 결과를 바꾸지 않습니다.';
    showPage();render();
  }catch(e){fail(e.message);}
}
function reviewControls(){
  const panel=$('review-panel');
  if(!job?.review){panel.classList.add('hidden');return;}
  panel.classList.remove('hidden');
  if(selectedCase?.page!==page)selectedCase=job.review.cases.find(c=>c.page===page)||null;
  panel.innerHTML='<label for="review-case">원본에서 먼저 고른 평가 영역</label><select id="review-case"><option value="">'+page+'쪽 전체 보기</option>'+job.review.cases.map(c=>'<option value="'+esc(c.id)+'" '+(c===selectedCase?'selected':'')+'>'+esc(c.id+' · 원본 '+c.page+'쪽 · '+c.kind)+'</option>').join('')+'</select>';
  if(job.review.continuation_report)panel.innerHTML+='<p class="hint"><a target="_blank" rel="noopener" href="/api/jobs/'+encodeURIComponent(job.id)+'/continuation/'+encodeURIComponent(job.review.continuation_report_asset||'comparison.html')+'">앞뒤 표 원본 · 모델 판단 근거 · 규칙 비교 보기 ↗</a><br>모델 결과는 별도 검토 기록입니다. 이 화면의 기존 셀 경로를 덮어쓰지 않습니다.</p>';
  $('review-case').onchange=e=>{selectedCase=job.review.cases.find(c=>c.id===e.target.value)||null;if(selectedCase){page=selectedCase.page;tableID=selectedCase.table_ids[0]||null;}showPage();render();};
  if(!selectedCase){panel.innerHTML+='<p class="hint">이 페이지는 원본 문맥 확인용입니다. 선정한 표의 평가 분모에는 추가되지 않습니다.</p>';return;}
  const c=selectedCase, ref=c.example, assessment=c.assessment;
  if(assessment){panel.innerHTML+='<p class="hint"><strong>원본 비교 초안: '+esc(assessment.source_verdict)+'</strong> · '+esc(assessment.reason)+'</p>';if(assessment.judge)panel.innerHTML+='<p class="hint">Judge B: '+esc(assessment.judge.verdict)+' · 출력 검사: '+(assessment.judge_validation.valid?'통과 (의미 승인 아님)':'거절: '+esc(assessment.judge_validation.errors.join(', ')))+' · <a target="_blank" rel="noopener" href="'+esc(assessment.trace_url)+'">Langfuse 기록 ↗</a></p><details><summary>Judge 점수·근거 보기</summary><pre>'+esc(JSON.stringify(assessment.judge,null,2))+'</pre></details>';}
  const anchors=[...c.anchor_matches].sort((a,b)=>Number(b.id===assessment?.target_id)-Number(a.id===assessment?.target_id));
  panel.innerHTML+='<details><summary>원본 정답 참조 초안 · 아직 승인되지 않음</summary><p class="hint">'+esc(ref.value_anchor)+' ← 행: '+esc(ref.expected_row_labels.join(' → ')||'미정')+' / 열: '+esc(ref.expected_column_labels.join(' → ')||ref.column_axis)+'<br>검토 영역의 물리 셀 '+c.observed_cells+'개 · 양쪽 경로 채움 '+c.both_paths_present+'개. 채움 개수는 정확도가 아닙니다.</p><div id="anchor-buttons"></div><label for="human-verdict">원본과 비교한 내 판단</label><select id="human-verdict"><option value="pending">검토 대기</option><option value="correct">예시 연결이 맞음</option><option value="incorrect">예시 연결이 틀림</option><option value="unknown">근거 부족 / 판단 불가</option></select><textarea id="human-note" rows="2" style="width:100%" placeholder="빠진 상위 라벨, 잘못된 적용 범위 등을 기록" aria-label="검토 메모"></textarea><button class="quiet" id="save-review">메모 저장</button> <button class="quiet" id="export-review">검토 기록 다운로드</button><span class="hint" id="review-saved" role="status"></span></details>';
  $('review-case').onchange=e=>{selectedCase=job.review.cases.find(c=>c.id===e.target.value)||null;if(selectedCase){page=selectedCase.page;tableID=selectedCase.table_ids[0]||null;}showPage();render();};
  $('anchor-buttons').innerHTML=anchors.length?anchors.map((m,i)=>'<button class="quiet" data-anchor="'+i+'">원문 일치 후보 '+(i+1)+' 보기</button>').join(''):'<p class="warning">예시 원문에 대응하는 물리 셀이 없습니다. 추출 누락·셀 경계·문자 인식을 확인해야 합니다.</p>';
  document.querySelectorAll('[data-anchor]').forEach(b=>b.onclick=()=>{
    const m=anchors[Number(b.dataset.anchor)],t=job.result.tables.find(t=>t.id===m.table_id);
    tab='structure';tableID=t.id;
    document.querySelectorAll('[data-tab]').forEach(x=>{x.classList.toggle('active',x.dataset.tab===tab);x.setAttribute('aria-selected',String(x.dataset.tab===tab));});
    render();selectCell(t,m.id);$('evidence').scrollIntoView({block:'nearest'});
  });
  try{const saved=JSON.parse(localStorage.getItem(reviewKey())||'null');if(saved){$('human-verdict').value=saved.verdict;$('human-note').value=saved.note;}}catch{}
  $('save-review').onclick=()=>{
    try{localStorage.setItem(reviewKey(),JSON.stringify({document_id:job.result.document_id,case_id:c.id,page:c.page,verdict:$('human-verdict').value,note:$('human-note').value,source_sha256:job.review.document.sha256,at:new Date().toISOString(),scope:'example_only_not_whole_table_gold',candidate_cell_ids:c.anchor_matches.map(m=>m.id)}));$('review-saved').textContent=' 이 브라우저에 저장했습니다. 원본 결과는 그대로입니다.';}catch{$('review-saved').textContent=' 저장 실패: 브라우저 저장소를 확인해 주세요.';}
  };
  $('export-review').onclick=()=>{const records=Object.keys(localStorage).filter(k=>k.startsWith('folio-review:')).map(k=>JSON.parse(localStorage.getItem(k)));const url=URL.createObjectURL(new Blob([JSON.stringify({records},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='human-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const h=$('highlight'),p=job.result.pages.find(p=>p.number===page),b=c.review_bbox;
  if(h&&p)Object.assign(h.style,{display:'block',left:b[0]/p.width*100+'%',top:b[1]/p.height*100+'%',width:(b[2]-b[0])/p.width*100+'%',height:(b[3]-b[1])/p.height*100+'%'});
}
const originalShowPage=showPage;
showPage=function(){originalShowPage();if(job){reviewControls();history.replaceState(null,'','?job='+job.id+'&page='+page+(selectedCase?'&case='+selectedCase.id:''));}};
api('/api/reviews').then(data=>{
  data.sort((a,b)=>a.name.localeCompare(b.name,'ko',{numeric:true}));
  savedReviews=data;$('saved-reviews').innerHTML='<option value="">저장된 문서 '+data.length+'건</option>'+data.map(d=>'<option value="'+d.id+'">'+esc(d.name)+'</option>').join('');
  $('saved-reviews').onchange=e=>{if(e.target.value)openReview(e.target.value);};
  const params=new URLSearchParams(location.search);if(params.get('job'))openReview(params.get('job'),params.get('page'),params.get('case'));
}).catch(e=>fail(e.message));
