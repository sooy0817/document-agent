// Persistent in-page alerts. Human decisions create new reviews, never overwrite prior results.
const decisionLabels={agree:'분석과 일치',disagree:'수정 필요',uncertain:'원본만으로 판단 어려움'};
const decisionsPanel=document.createElement('section');decisionsPanel.id='review-attention';
decisionsPanel.innerHTML='<h3>내 확인이 필요한 항목 <span id="review-alert-count" role="status" aria-live="polite">0</span></h3><p class="hint">실패·근거 부족·오류 후보만 표시합니다. 페이지가 열려 있으면 자동 갱신됩니다.</p><div id="review-alerts"></div><details><summary>실행·재검토 기록</summary><div id="review-run-summary"></div><div id="review-comparisons"></div></details>';
queuePanel.before(decisionsPanel);
const monitorLink=document.createElement('a');monitorLink.href='/monitor';monitorLink.className='quiet';monitorLink.textContent='실시간 실행 대시보드 열기 →';decisionsPanel.prepend(monitorLink);

function refreshDecisions(state){
  const alerts=state.alerts||[];
  $('review-alert-count').textContent=alerts.length+'건';
  monitorLink.innerHTML=(alerts.length?'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#bb741b;margin-right:7px"></span>':'')+'실시간 대시보드 · 내 확인 '+alerts.length+'건';
  $('review-alerts').innerHTML=alerts.map(a=>'<article class="queue-card"><strong>'+esc(a.document_name)+' · '+a.page+'쪽</strong><p>'+esc(reviewVerdicts[a.reason]||({DailyReviewBudgetExceeded:'오늘 모델 호출 한도 부족',ValueError:'실행 조건·근거 확인 필요'})[a.reason]||a.reason)+'</p><button data-alert-open="'+a.request_id+'">원본 확인·판단</button></article>').join('')||'<p class="hint">지금 확인할 알림이 없습니다. 진행 중 작업과 이전 결과는 아래 검토 요청에서 볼 수 있습니다.</p>';
  decisionsPanel.querySelectorAll('[data-alert-open]').forEach(b=>b.onclick=()=>openQueueDetail(b.dataset.alertOpen));
  const completed=state.items.filter(i=>i.result), compared=completed.filter(i=>i.parent_id), costs=completed.map(i=>i.result.estimated_cost_usd);
  $('review-run-summary').textContent='저장된 결과 '+completed.length+'건 · 판단 후 재검토 '+compared.length+'건 · 기록된 모델 호출 '+completed.reduce((n,i)=>n+(i.result.api_calls||0),0)+'회 · 확인된 비용 $'+costs.filter(c=>c!==null).reduce((n,c)=>n+(c||0),0).toFixed(4)+'. 실패·중단 호출의 미확정 비용은 별도입니다. 이 숫자는 정확도가 아닙니다.';
  $('review-comparisons').innerHTML=compared.map(i=>'<article class="queue-card"><strong>'+esc(i.document_name)+'</strong><p>'+queueDate(i.created_at)+' · 경로 변화 '+(i.result.comparison?.changed_cells.length||0)+'셀 · 검사 항목 해소 '+(i.result.comparison?.resolved_checks.length||0)+'개 · 새 검사 문제 '+(i.result.comparison?.added_checks.length||0)+'개</p><button data-compare-open="'+i.id+'" class="quiet">전후 결과 보기</button></article>').join('')||'<p class="hint">판단 후 재검토가 완료되면 이곳에 쌓입니다.</p>';
  decisionsPanel.querySelectorAll('[data-compare-open]').forEach(b=>b.onclick=()=>openQueueDetail(b.dataset.compareOpen));
}

function decisionDetail(i){
  const r=i.result, body=$('queue-detail-body');
  const section=document.createElement('section');section.id='decision-review';
  const targets=r?.interpretation?.targets||[];
  function axis(t,a){const v=t[a+'_axis'];return v.header_ids.map(id=>esc(v.citations.find(c=>c.source_id===id)?.quote||id)+'<small>'+esc(id)+'</small>').join(' → ')||esc(v.status);}
  section.innerHTML='<h3>원본과 판단 근거</h3><div class="decision-compare"><div class="decision-source"><img src="/api/error-basket/'+i.item_id+'/image" alt="오류 담기 당시 원본 영역"><p class="hint">수집 당시 원본입니다. 전체 페이지는 ‘원본·셀·JSON 보기’에서 확인할 수 있습니다.</p></div><div>'+(targets.length?'<table><thead><tr><th>셀 내용</th><th>행 항목</th><th>열 항목</th></tr></thead><tbody>'+targets.map(t=>'<tr><td>'+esc((r.relations?.targets.find(v=>v.cell_id===t.cell_id)?.target_quote||t.cell_id).slice(0,180))+'<small>'+esc(t.cell_id)+'</small></td><td>'+axis(t,'row')+'</td><td>'+axis(t,'column')+'</td></tr>').join('')+'</tbody></table>':'<p>선택 영역의 기존 결과와 아래 검사 근거를 확인해주세요.</p>')+'<p class="warning">'+esc((r?.validation_errors||[]).join(' / ')||r?.verdict||i.error||'검토 결과 없음')+'</p><details><summary>Judge 판단·인용 근거</summary><pre>'+esc(JSON.stringify(r?.judgment||r?.judge||{},null,2))+'</pre></details></div></div>';
  if(r?.comparison){
    section.innerHTML+='<h3>판단 전후 비교</h3><p>변경된 경로 '+r.comparison.changed_cells.length+'셀 · 해소된 검사 '+r.comparison.resolved_checks.length+'개 · 새 검사 '+r.comparison.added_checks.length+'개. 의미 정확도 개선으로 확정하지 않습니다.</p><button class="quiet" id="decision-parent">이전 결과 열기</button><pre>'+esc(JSON.stringify(r.comparison,null,2))+'</pre>';
  }
  if(i.confirmation){
    section.innerHTML+='<p>이 선택 영역은 사용자가 원본과 대조해 확인 완료로 기록했습니다. 원본 JSON과 모델 후보는 변경하지 않았습니다.</p><a href="/monitor?request='+encodeURIComponent(i.id)+'">확인 기록·취소는 대시보드에서 보기</a>';
  }else if(i.decision){
    section.innerHTML+='<p>저장한 판단: '+esc(decisionLabels[i.decision.choice])+' · '+esc(i.decision.note)+'</p><button id="decision-child">재검토 결과 열기</button>';
  }else if(['completed','failed'].includes(i.status)){
    section.innerHTML+='<form id="review-decision-form"><h3>내 판단을 남기고 재검토</h3><label>판단<select name="choice"><option value="disagree">수정 필요</option><option value="agree">분석과 일치</option><option value="uncertain">원본만으로 판단 어려움</option></select></label><label>원본에서 확인한 내용 또는 불확실한 이유<textarea name="note" required maxlength="4000" rows="3" placeholder="예: 오른쪽 설명은 같은 행의 왼쪽 항목에 적용됩니다."></textarea></label><label>근거 셀·표 ID (선택, 한 줄에 하나)<textarea name="source_ids" rows="2" placeholder="위 결과의 셀 ID를 복사할 수 있습니다."></textarea></label><p class="hint">원본·이전 결과는 보존됩니다. 새 모델 검토는 최대 3회 호출하며 기존 일일 한도를 사용합니다. 자동 코드 수정·정답 승인·학습은 하지 않습니다.</p><button type="submit">판단 저장·재검토 요청</button><p id="review-decision-message" role="status" aria-live="polite"></p></form>';
  }
  body.prepend(section);
  if($('decision-parent'))$('decision-parent').onclick=()=>openQueueDetail(i.parent_id);
  if($('decision-child'))$('decision-child').onclick=()=>openQueueDetail(i.decision.child_id);
  const form=$('review-decision-form');
  if(form){
    form.addEventListener('input',()=>form.dataset.dirty='true');
    form.onsubmit=async event=>{
      event.preventDefault();const button=form.querySelector('button[type=submit]');button.disabled=true;
      const payload={choice:form.elements.choice.value,note:form.elements.note.value,source_ids:form.elements.source_ids.value.split('\n').map(s=>s.trim()).filter(Boolean)};
      try{
        const child=await api('/api/review-queue/'+i.id+'/decision',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify(payload)});
        form.dataset.dirty='false';await openQueueDetail(child.id);await loadQueue(true);
        $('review-queue-message').textContent='판단을 저장하고 재검토를 접수했습니다. 이전 결과는 그대로 남아 있습니다.';
      }catch(e){$('review-decision-message').textContent=e.message;button.disabled=false;}
    };
  }
}
window.reviewDecisionUI={refresh:refreshDecisions,detail:decisionDetail};
if(queueState)refreshDecisions(queueState);
