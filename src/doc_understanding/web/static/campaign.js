(() => {
  // 평가 팀은 새 문서 업로드와 별개인 선택 기능으로 접어 둔다.
  const panel = document.createElement('details');
  panel.className = 'campaign-panel';
  panel.innerHTML = '<summary>문서 평가 팀 <span id="campaign-summary">선택 기능 · 상태 확인 중</span></summary>' +
    '<div class="campaign-content"><p>미리 설정한 문서 묶음의 검토 상태입니다. 위에서 업로드한 문서의 분석 상태와는 별개입니다.</p>' +
    '<div class="campaign-actions"><button class="primary" id="auto-evaluate">설정한 문서로 시작</button>' +
    '<button class="quiet" id="campaign-resume" hidden>재개</button>' +
    '<button class="quiet" id="campaign-retry" hidden>실패 검토 다시 실행</button></div>' +
    '<div id="auto-status" aria-live="polite"></div><div id="source-candidates"></div></div>';
  document.querySelector('main > footer').before(panel);
  const summary = panel.querySelector('#campaign-summary');
  const status = panel.querySelector('#auto-status');
  const start = panel.querySelector('#auto-evaluate');
  const resume = panel.querySelector('#campaign-resume');
  const retry = panel.querySelector('#campaign-retry');
  const states = {ready:'준비', running:'실행 중', paused:'대기', blocked:'진행 차단', failed:'실패'};
  const names = {collect:'표본 수집', parse:'문서 처리', review:'로컬 검사', specialist:'전문 LLM 검토', supervisor:'관리자'};
  const reasons = {
    selected_manifest_registered:'선택한 표본 등록 완료',
    registered_sample_pending:'문서 처리 시작', completed:'문서 처리 완료',
    local_review_pending:'로컬 검사 시작', local_review_completed:'로컬 검사 완료',
    local_review_incomplete:'로컬 검사 미완료', specialist_review_pending:'전문 검토 팀 실행',
    specialist_review_not_connected:'이 실행에는 전문 검토가 연결되지 않았습니다',
    'specialist_blocked:review_incomplete':'전문 검토가 완료되지 않았습니다',
    'specialist_blocked:preparation':'선택 근거 연결에 확인이 필요합니다',
    improvement_not_connected:'전문 검토 완료 · 개선·평가 단계 연결 대기',
    execution_state_unknown:'중단된 작업의 실행 상태 확인 필요',
    sample_processing_failed:'문서 처리 실패', DNS_RESOLUTION_FAILED:'모델 서버 DNS 조회 실패',
    DailyReviewBudgetExceeded:'일일 호출 예산 초과'
  };
  let latest = null;
  async function refresh() {
    try {
      const response = await fetch('/api/campaigns');
      if (!response.ok) throw new Error('상태 조회 실패');
      const data = await response.json();
      latest = data.campaigns[0] || null;
      summary.textContent = latest ? '기존 평가: ' + (states[latest.status] || latest.status) + ' · 상세 보기' : '선택 기능 · 실행 이력 없음';
      start.disabled = data.running || Boolean(latest && latest.pilot);
      resume.hidden = retry.hidden = !(latest && latest.pilot && ['paused','blocked','running'].includes(latest.status));
      resume.disabled = retry.disabled = data.running;
      const sources = panel.querySelector('#source-candidates');
      sources.replaceChildren();
      const lines = [];
      if (latest) {
        lines.push('현재 상태: ' + (states[latest.status] || latest.status));
        if (latest.status === 'running' && !data.running) lines.push('별도 실행 또는 중단 상태입니다. 재개 시 실행 잠금을 확인합니다.');
        lines.push('문서 ' + latest.samples.length + '건 · 처리 완료 ' + latest.samples.filter(s=>s.status==='completed').length + '건');
        lines.push('로컬 검사 완료 ' + latest.samples.filter(s=>s.review_status==='completed').length + '건');
        if (latest.pilot) {
          const p = latest.pilot;
          lines.push('전문 검토 완료 ' + (p.items || []).filter(i=>i.specialist_review_completed??i.model_review_completed).length + '개 / 선택 항목 ' + (p.planned_items ?? '확인 중') + '개');
          for (const i of p.items || []) {
            lines.push(i.document + ' ' + (i.page || '') + '쪽: ' + (i.model_review_completed ? '모델 검토 완료' : i.specialist_review_completed ? '규칙 검토 완료 · 모델 호출 없음' : '미완료') + (i.error ? ' · ' + (reasons[i.error] || i.error) : ''));
            for (const cause of i.causes || []) lines.push('  ' + (reasons[cause.code] || cause.code || cause.error));
          }
          lines.push('사람 정답 확정 전 · 파서 후보 승인 전');
          lines.push('현재 연결 범위는 전문 검토까지이며, 파서 개선·평가·최종 확정은 후속 구현입니다.');
        } else {
          lines.push('이전 로컬 검사 실행입니다. 모델 검토나 정확도 검증을 의미하지 않습니다.');
        }
        for (const e of latest.events.slice(-5)) {
          const reason = e.reason.startsWith('request_linked:') ? '검토 요청 연결' : (reasons[e.reason] || e.reason);
          lines.push((names[e.team] || e.team) + ' · ' + reason);
        }
        for (const candidate of latest.source_candidates || []) {
          const a = document.createElement('a');
          a.textContent = candidate.title;
          a.href = candidate.url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.style.display = 'block';
          sources.append(a);
        }
      } else lines.push('설정한 표본 또는 로컬 수집함의 문서로 시작합니다.');
      status.textContent = lines.join('\n');
      status.style.whiteSpace = 'pre-line';
    } catch (error) { summary.textContent = '상태 조회 실패 · 상세 보기'; status.textContent = error.message; }
  }
  async function launch(url) {
    start.disabled = resume.disabled = retry.disabled = true;
    try {
      const response = await fetch(url, {method:'POST', headers:{'X-Demo-Token':token}});
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '실행 요청 실패');
      }
      await refresh();
    } catch (error) { status.textContent = error.message; start.disabled = resume.disabled = retry.disabled = false; }
  }
  start.onclick = () => launch('/api/campaigns/automatic');
  resume.onclick = () => latest && launch('/api/campaigns/' + latest.id + '/resume');
  retry.onclick = () => latest && launch('/api/campaigns/' + latest.id + '/resume?retry_failed=true');
  refresh();
  setInterval(refresh, 5000);
})();
