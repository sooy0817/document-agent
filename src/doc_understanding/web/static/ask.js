// 역할: 코퍼스 질문 화면. 적재된 문서 전체에서 검색·답변하고, 완료 문서를 일괄 적재한다. 문서 데이터는 textContent로만 그린다.
(() => {
  const $ = id => document.getElementById(id);
  const errors = {
    EXTERNAL_CALL_NOT_ENABLED: 'OpenAI를 쓰려면 외부 전송 동의를 체크하세요.',
    API_KEY_UNAVAILABLE: '서버에 OPENAI_API_KEY가 없습니다. 키를 설정한 터미널에서 서버를 다시 시작하세요.',
    RAG_BUSY: '다른 작업이 실행 중입니다. 끝난 뒤 다시 시도하세요.',
    CORPUS_EMPTY: '코퍼스에 문서가 없습니다. 왼쪽에서 문서를 먼저 적재하세요.',
    RUN_NOT_IN_CORPUS: '선택한 문서가 현재 코퍼스에 없습니다. 목록을 새로고침하세요.',
    DOCUMENT_NOT_READY: '분석이 끝나지 않은 문서는 적재할 수 없습니다.',
    SOURCE_MANIFEST_REQUIRED: '원본 추출 기록을 찾을 수 없는 문서입니다. PDF를 다시 업로드하세요.',
    EMBEDDING_CONFIG_REQUIRED: '임베딩 설정을 확인하세요.',
    EXPLICIT_MODEL_PRICING_REQUIRED: '답변 모델 단가를 입력하세요.',
    AGENT_REQUIRES_ANSWER: '질문 이해 에이전트는 답변 생성과 함께 실행합니다.',
    NO_MATCHING_EMBEDDINGS: '선택한 임베딩 모델의 벡터가 없습니다. 같은 모델로 적재했는지 확인하세요.',
    EMBEDDING_CALL_FAILED_SEE_AUDIT: '임베딩 호출에 실패했습니다.',
    MODEL_REQUEST_BUDGET_EXCEEDED: '답변 입력 또는 비용 한도를 초과했습니다.',
    SERVER_RESTARTED: '서버 재시작으로 중단됐습니다. 다시 실행하세요.',
  };
  let catalog = null, state = null, shown = new Set(), submitting = false;
  // 질문 범위: 비어 있으면 전체 검색. 고른 문서만 run_id → 이름으로 기억한다(전체 목록은 화면에 싣지 않는다).
  const chosen = new Map();
  let listed = {text: '', offset: 0, total: 0}, listing = 0, paging = false;
  const show = (id, message) => { const box = $(id); box.textContent = errors[message] || message; box.hidden = false; box.scrollIntoView({block: 'nearest'}); };
  const hide = id => { $(id).hidden = true; };
  // 화면에서 모델·비용을 고르지 않는다. 단가는 2026-11-10 OpenAI 공식 모델 페이지 기준이며 서버가 상한을 검사한다.
  const REVIEW_MODEL = {structure_review_model: 'gpt-4.1-mini', structure_review_input_usd_per_million: 0.4, structure_review_output_usd_per_million: 1.6};
  const EMBEDDING = {provider: 'openai', model: 'text-embedding-3-small', dimensions: 1536, input_usd_per_million: 0.02, max_cost_usd: 1};
  const ANSWER = {model: 'gpt-4.1', input_usd_per_million: 2.0, output_usd_per_million: 8.0, max_cost_usd: 0.15,
                  plan_model: 'gpt-4.1-mini', plan_input_usd_per_million: 0.4, plan_output_usd_per_million: 1.6, plan_max_cost_usd: 0.05,
                  rerank_model: 'gpt-4.1-mini', rerank_input_usd_per_million: 0.4, rerank_output_usd_per_million: 1.6, rerank_max_cost_usd: 0.05};
  const rid = () => crypto.randomUUID().replaceAll('-', '');
  async function call(path, options) {
    const response = await fetch(path, options);
    const value = await response.json();
    if (!response.ok) throw Error(response.status === 404 && value.detail === 'Not Found' ? '새 기능을 쓰려면 서버를 재시작하세요.' : typeof value.detail === 'string' ? value.detail : '요청 형식 오류: ' + JSON.stringify(value.detail).slice(0, 200));
    return value;
  }
  const post = (path, body) => call(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Demo-Token': window.token}, body: JSON.stringify(body)});
  const badge = (text, kind) => { const b = document.createElement('span'); b.className = 'badge' + (kind ? ' ' + kind : ''); b.textContent = text; return b; };

  function renderScope() {
    const box = $('scope-chosen'); box.replaceChildren();
    $('scope-summary').textContent = chosen.size ? '고른 문서 ' + chosen.size + '개에서만 검색합니다.' : '전체 문서에서 검색합니다.';
    for (const [run, name] of chosen) {
      const chip = badge(name); const x = Object.assign(document.createElement('button'), {textContent: '×', title: '범위에서 빼기'});
      x.onclick = () => { chosen.delete(run); renderScope(); document.querySelectorAll('#corpus input[data-run]').forEach(c => { if (c.dataset.run === run) c.checked = false; }); };
      chip.append(' ', x); box.append(chip);
    }
  }
  // 문서 목록은 이름으로 찾아 한 쪽씩만 받아 온다(코퍼스 전체를 화면에 나열하지 않는다).
  async function loadDocuments(append) {
    // 겹친 요청 방지: 새 검색은 앞선 응답을 버리게 하고(번호 비교), "더 보기"는 한 번에 하나만 받는다.
    if (append && paging) return;
    const text = append ? listed.text : $('filter-text').value.trim();
    if (!append) listed = {text, offset: 0, total: 0};
    const ticket = append ? listing : ++listing, offset = listed.offset;
    let page;
    paging = append;
    try {
      page = await call('/api/rag/corpus/documents?limit=20&offset=' + offset + (text ? '&q=' + encodeURIComponent(text) : ''));
    } finally { if (append) paging = false; }
    if (ticket !== listing) return;
    const corpus = $('corpus'); if (!append) corpus.replaceChildren();
    if (!page.total) corpus.append(Object.assign(document.createElement('p'), {className: 'hint', textContent: text ? '이름에 "' + text + '"이(가) 들어간 문서가 없습니다.' : '아직 코퍼스에 문서가 없습니다.'}));
    for (const d of page.documents) {
      const box = document.createElement('div'); box.className = 'doc';
      const label = document.createElement('label'); const check = Object.assign(document.createElement('input'), {type: 'checkbox', checked: chosen.has(d.run_id)}); check.dataset.run = d.run_id;
      check.onchange = () => { if (check.checked) chosen.set(d.run_id, d.name); else chosen.delete(d.run_id); renderScope(); };
      label.append(check, document.createTextNode(d.name + ' · ' + d.pages + '쪽')); box.append(label);
      box.append(badge('검색 항목 ' + d.entries));
      if (d.structure_review) box.append(badge('구조 검토 적용'));
      if (!d.embeddings.length) box.append(badge('임베딩 없음 · 키워드만', 'warn'));
      for (const e of d.embeddings) box.append(badge(e.provider + ' ' + e.model + ' ' + e.vectors + '/' + d.entries, e.vectors < d.entries ? 'warn' : ''));
      corpus.append(box);
    }
    listed.offset = offset + page.documents.length; listed.total = page.total;
    $('filter-more').hidden = listed.offset >= listed.total;
    $('filter-count').textContent = page.total ? listed.offset + ' / ' + page.total + '개 표시' : '';
  }
  async function loadStale() {
    const page = await call('/api/rag/corpus/documents?stale=true&limit=100');
    const stale = $('stale'); stale.replaceChildren();
    for (const d of page.documents) { const p = document.createElement('div'); p.className = 'hint'; p.textContent = d.name + ' · ' + d.index_version; stale.append(p); }
    if (page.total > page.documents.length) stale.append(Object.assign(document.createElement('p'), {className: 'hint', textContent: '… ' + (page.total - page.documents.length) + '개 더'}));
  }

  function renderCatalog() {
    const s = catalog.stats;
    $('corpus-stats').textContent = '문서 ' + s.documents + '개 · ' + s.pages + '쪽 · 표 ' + s.tables + '개 · 검색 항목 ' + s.entries + '개 · 임베딩 ' + s.vectors + '개';
    $('stale-count').textContent = '(' + s.stale_documents + ')';
    if ($('stale-box').open) loadStale().catch(() => {});
    if ($('filter-box').open) loadDocuments(false).catch(error => show('ask-error', error.message));
    renderScope();
    const jobs = $('jobs'); jobs.replaceChildren();
    const labels = {in_corpus: ['코퍼스에 있음', ''], not_ingested: ['미적재', 'warn'], stale: ['재색인 필요', 'warn'], stored_not_latest: ['같은 문서의 다른 실행이 최신', 'warn']};
    for (const j of catalog.jobs.filter(j => j.state !== 'in_corpus').concat(catalog.jobs.filter(j => j.state === 'in_corpus'))) {
      const box = document.createElement('div'); box.className = 'doc';
      const label = document.createElement('label'); const check = Object.assign(document.createElement('input'), {type: 'checkbox'}); check.dataset.job = j.job_id;
      label.append(check, document.createTextNode((j.name || j.job_id) + (j.pages ? ' · ' + j.pages + '쪽' : ''))); box.append(label);
      const [text, kind] = labels[j.state] || [j.state, 'warn']; box.append(badge(text, kind)); jobs.append(box);
    }
  }
  function renderTurn(task) {
    // 문서 이름·원문 보기 작업은 서버가 근거에 나온 문서에 한해 함께 보낸다.
    const runName = runId => task.documents?.[runId]?.name || chosen.get(runId) || runId;
    const jobOf = runId => task.documents?.[runId]?.job_id;
    const turn = document.createElement('article'); turn.className = 'turn';
    turn.append(Object.assign(document.createElement('h3'), {textContent: 'Q. ' + task.config.question}));
    const meta = document.createElement('p'); meta.className = 'hint';
    const docs = task.run_ids ? task.run_ids.map(runName).join(', ') : '전체 문서' + (task.scope ? ' ' + task.scope.documents + '개' : '');
    meta.textContent = '검색 범위: ' + docs + ' · 방식 ' + task.config.mode + ' · 근거 ' + (task.evidence || []).length + '개' + (task.updated_at ? ' · ' + new Date(task.updated_at).toLocaleString() : '');
    turn.append(meta);
    if (task.agent) {
      const roles = {scope: '적용 범위', item: '항목', condition: '조건', other: '기타'};
      const plan = document.createElement('p'); plan.className = 'hint';
      plan.textContent = '질문 이해: ' + (task.agent.plan_check?.fallback ? '계획 실패 · 원문 검색' : (task.agent.plan?.requirements || []).map(r => '[' + (roles[r.role] || r.role) + '] ' + r.text).join(' · '))
        + (task.agent.retried ? ' · 1회 재검색' : '') + (task.agent.trace_id ? ' · Langfuse ' + task.agent.trace_id : '');
      turn.append(plan);
    }
    if (task.status === 'failed') turn.append(Object.assign(document.createElement('div'), {className: 'error', textContent: errors[task.error] || task.error}));
    const result = task.result || {};
    const selection = result.selection || {evidence_ids: [], values_raw: []};
    const used = new Map(selection.evidence_ids.map((id, i) => [id, selection.values_raw[i]]));
    if (task.result) {
      // 답변: 모델 문장(값·숫자 검사 통과) 또는 근거로 만든 문장. 보류면 보류 사유.
      const text = result.answer_text || (selection.abstained || result.abstained ? '조회한 근거만으로 답을 확정하지 못했습니다.' : result.answer);
      const box = Object.assign(document.createElement('div'), {className: 'answer', textContent: text});
      turn.append(box);
      if (result.answer_text_source === 'template') turn.append(Object.assign(document.createElement('p'), {className: 'hint', textContent: '모델 문장이 검사를 통과하지 못해 근거 값으로 만든 문장입니다.'}));
      for (const alt of result.alternatives || []) {
        turn.append(Object.assign(document.createElement('p'), {className: 'hint', textContent: '※ 다른 해석: ' + alt.interpretation + ' (근거 ' + alt.evidence_ids.join(', ') + ')'}));
      }
      const why = result.assessment_validation?.assessment?.explanation;
      if (why) turn.append(Object.assign(document.createElement('p'), {className: 'hint', textContent: '판단 근거(모델 설명): ' + why}));
      const cost = (result.estimated_cost_usd || 0) + (task.agent?.plan_estimated_cost_usd || 0) + (task.embedding_audit?.estimated_cost_usd || 0);
      turn.append(Object.assign(document.createElement('p'), {className: 'hint', textContent: '추정 비용 $' + cost.toFixed(5) + ' · 답변은 사람 검증 전 후보입니다. 원문과 함께 확인하세요.'}));
    }
    const card = e => {
      const box = document.createElement('div'); box.className = 'evidence';
      const head = document.createElement('header');
      head.append(document.createTextNode(e.evidence_id + ' · ' + runName(e.run_id) + ' · ' + e.page + '쪽' + (e.lookup_eligible === false ? ' · 경로 미확정' : '')));
      const job = jobOf(e.run_id);
      if (job) { const a = document.createElement('a'); a.href = '/?job=' + encodeURIComponent(job) + '&page=' + e.page; a.target = '_blank'; a.textContent = '원문 보기'; head.append(a); }
      box.append(head);
      const scope = (e.scope_context || []).map(s => s.text_raw).join(' / ');
      const picked = used.get(e.evidence_id);
      const value = picked && picked !== e.value_raw ? picked + '   (셀 원문: ' + (e.value_raw || '').replaceAll('\n', ' / ') + ')' : (e.value_raw ?? '(읽기 불가)');
      box.append(document.createTextNode([scope && '범위: ' + scope, (e.row_path || []).join(' → '), (e.column_path || []).join(' → '), value].filter(Boolean).join('\n')));
      return box;
    };
    const evidence = task.evidence || [];
    const usedCards = evidence.filter(e => used.has(e.evidence_id));
    if (usedCards.length) { turn.append(Object.assign(document.createElement('h3'), {textContent: '답변에 쓴 근거'})); usedCards.forEach(e => turn.append(card(e))); }
    const others = evidence.filter(e => !used.has(e.evidence_id));
    if (others.length) {
      const more = document.createElement('details'); more.append(Object.assign(document.createElement('summary'), {textContent: '검색된 다른 근거 ' + others.length + '개'}));
      others.forEach(e => more.append(card(e))); turn.append(more);
    }
    const details = document.createElement('details'); details.append(Object.assign(document.createElement('summary'), {textContent: '검색 후보·비용 기록'}));
    details.append(Object.assign(document.createElement('pre'), {textContent: JSON.stringify({answer_raw: result.answer, hits: task.hits, retrieval: task.retrieval_audit, embedding: task.embedding_audit, agent: task.agent}, null, 2)}));
    turn.append(details);
    $('history').prepend(turn);
  }

  function renderState() {
    const lines = [];
    if (catalog) lines.push('코퍼스 문서 ' + catalog.stats.documents + '개 · 색인 ' + catalog.index_version);
    if (!state.api_key_available) lines.push('서버 API 키 없음: 키워드 검색만 가능');
    const ingest = state.ingest;
    if (ingest) {
      const items = ingest.items || [];
      $('ingest-status').hidden = false;
      $('ingest-status').textContent = '적재 ' + ({queued: '대기', running: '진행 중', completed: '완료', partial: '일부 실패', failed: '실패'}[ingest.status] || ingest.status) + '\n'
        + items.map(i => '· ' + ((catalog?.jobs.find(j => j.job_id === i.job_id) || {}).name || i.job_id) + ': ' + i.status + (i.stage && i.status === 'running' ? ' (' + i.stage + ')' : '')
          + (i.total ? ' · 항목 ' + i.total : '') + (i.copied_from_other_runs ? ' · 복사 ' + i.copied_from_other_runs : '') + (i.written !== undefined ? ' · 새 임베딩 ' + i.written : '') + (i.error ? ' · ' + (errors[i.error] || i.error) : '')).join('\n');
    }
    const q = state.question;
    if (q && ['queued', 'running'].includes(q.status)) lines.push('질문 처리 중… (' + q.stage + ')');
    if (state.busy) lines.push('작업 실행 중');
    $('status').textContent = lines.join('\n');
    $('ingest-controls').disabled = submitting || state.busy;
    $('ask-controls').disabled = submitting || state.busy;
    if (q && !['queued', 'running'].includes(q.status) && !shown.has(q.request_id)) { shown.add(q.request_id); renderTurn(q); }
  }
  async function refresh(full) {
    try {
      const wasBusy = state?.busy;
      state = await call('/api/rag/corpus/state');
      // 화면을 열기 전의 질문 기록(다른 창·테스트 실행)은 이 화면 기록에 섞지 않는다.
      if (full && state.question) shown.add(state.question.request_id);
      if (full || !catalog || (wasBusy && !state.busy)) { catalog = await call('/api/rag/corpus'); renderCatalog(); }
      renderState();
    } catch (error) { show('ask-error', error.message); }
  }

  async function ingest(embed) {
    hide('ingest-error');
    try {
      const jobIds = [...document.querySelectorAll('#jobs input[data-job]:checked')].map(c => c.dataset.job);
      if (!jobIds.length) throw Error('적재할 문서를 선택하세요.');
      const body = {request_id: rid(), job_ids: jobIds, embed, allow_external: embed, structure_review: $('review').checked};
      // 구조 검토 모델: 사용자가 켰을 때만, 규칙이 모호로 남긴 표에만 쓰인다(외부 호출을 허용한 적재에서만).
      if (embed && body.structure_review && $('review-model').checked) Object.assign(body, REVIEW_MODEL);
      if (embed) body.embedding = EMBEDDING;
      submitting = true; await post('/api/rag/corpus/ingest', body);
    } catch (error) { show('ingest-error', error.message); } finally { submitting = false; refresh(); }
  }
  $('ingest').onclick = () => ingest(true);
  $('ingest-store').onclick = () => ingest(false);
  $('ask').onclick = async () => {
    hide('ask-error');
    try {
      const question = $('question').value.trim(); if (!question) throw Error('질문을 입력하세요.');
      if (chosen.size > 200) throw Error('문서는 200개까지 고를 수 있습니다. 더 넓게 찾으려면 전체 검색을 쓰세요.');
      // 문서를 고르지 않으면 전체 검색이다. 이때 문서 ID를 보내지 않는다(서버가 현재 코퍼스 전체로 범위를 정한다).
      const body = {request_id: rid(), question, mode: 'hybrid', answer: true, agent: true, allow_external: true,
                    run_ids: chosen.size ? [...chosen.keys()] : null, answer_mode: 'selection',
                    embedding: EMBEDDING, ...ANSWER};
      submitting = true; renderState(); await post('/api/rag/corpus/question', body);
    } catch (error) { show('ask-error', error.message); } finally { submitting = false; refresh(); }
  };
  $('question').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) $('ask').click(); });
  let typing = null;
  $('filter-text').addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => loadDocuments(false).catch(error => show('ask-error', error.message)), 250); });
  $('filter-box').addEventListener('toggle', () => { if ($('filter-box').open) loadDocuments(false).catch(error => show('ask-error', error.message)); });
  $('stale-box').addEventListener('toggle', () => { if ($('stale-box').open) loadStale().catch(error => show('ask-error', error.message)); });
  $('filter-more').onclick = () => loadDocuments(true).catch(error => show('ask-error', error.message));
  $('filter-clear').onclick = () => { chosen.clear(); renderScope(); document.querySelectorAll('#corpus input[data-run]').forEach(c => { c.checked = false; }); };
  refresh(true); setInterval(refresh, 2500);
})();
