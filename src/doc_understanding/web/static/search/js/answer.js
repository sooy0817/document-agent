// 역할: 질문 한 건의 결과를 그린다. 답변, 답변 보류, 검색 근거 없음, 처리 오류를 서로 다르게 보여 주고 인용 근거를 붙인다.
import {$, h, add, one} from './dom.js';
import {say} from './config.js';
import {kindOf} from './evidence.js';
import {closeSource, openSource} from './viewer.js';
import {feedbackBox} from './feedback.js';

export function outcome(task) {
  if (task.status !== 'completed') return 'error';
  // 문서 고르기에서 끝난 질문: 되묻기(후보 여럿) 또는 해당 문서 없음. 검색을 하지 않았으므로 근거가 없다.
  const ask = task.result?.clarification;
  if (ask?.kind === 'needs_context') return 'needs_context';
  if (ask) return ask.kind === 'needs_choice' ? 'choose_document' : 'document_missing';
  if (!(task.evidence || []).length) return 'no_evidence';
  const result = task.result;
  if (!result) return 'retrieval_only';
  const selection = result.selection || {};
  return (selection.abstained ?? result.abstained) ? 'abstained' : 'answered';
}

// 근거 한 건: 문서 이름, 쪽, 인용 원문. 누르면 원문 패널이 열린다. 경로와 문맥은 펼쳐서 본다.
function citation(task, e, number, picked) {
  const name = task.documents?.[e.run_id]?.name || e.source_name || '문서';
  const box = h('div', {class: 'cite', data: {evidence: e.evidence_id}});
  const changed = picked && one(picked) !== one(e.value_raw);
  box.append(h('button', {class: 'cite-open', 'aria-label': (number ? '인용 ' + number + ' ' : '') + name + ' ' + e.page + '쪽 원문 열기', onclick: () => { document.querySelectorAll('.turn.current').forEach(t => t.classList.remove('current')); box.closest('.turn')?.classList.add('current'); openSource(e, box); }},
    h('span', {class: 'head'}, h('span', {class: 'num' + (number ? '' : ' other')}, number ? String(number) : '·'), h('span', {}, name), h('span', {class: 'muted'}, e.page + '쪽 · ' + kindOf(e)),
      e.lookup_eligible === false ? h('span', {class: 'tag warn'}, '경로 미확정') : null),
    h('span', {class: 'quote'}, changed ? picked : (e.value_raw ?? '(읽을 수 없는 칸)'))));
  const rows = [];
  const row = (label, value) => { if (value) rows.push(h('dt', {}, label), h('dd', {}, value)); };
  if (changed) row('칸 원문', e.value_raw);
  row('행 경로', (e.row_path || []).map(one).join(' → '));
  row('열 경로', (e.column_path || []).map(one).join(' → '));
  row('적용 범위', (e.scope_context || []).map(s => one(s.text_raw)).join(' / '));
  row('표 머리글', (e.table_context || []).map(one).join(' | '));
  row('담긴 위치', (e.nested_context?.path || []).map(p => one(p.label)).filter(Boolean).join(' → '));
  row('단위', (e.unit_context || []).map(u => one(u.text_raw)).join(' / '));
  row('주석', (e.notes || []).map(n => one(n.text_raw) + (n.status === 'unknown_scope' ? ' (적용 대상 미확정)' : '')).join('\n'));
  if (rows.length) box.append(h('details', {}, h('summary', {}, '행·열 경로와 문맥'), h('dl', {class: 'paths'}, rows),
    h('p', {class: 'hint'}, '경로와 문맥은 분석 후보이며 사람 검증 전입니다.')));
  return box;
}

// 문서 한 줄: 기관 · 문서 이름 · 기준 시점. 메타데이터가 비어 있으면 표시 이름을 쓴다.
const documentLine = d => [d.organization, d.title || d.name, d.as_of].filter(Boolean).join(' · ') + (d.pages ? ' (' + d.pages + '쪽)' : '');

// 후보 문서 버튼들. 누르면 같은 질문을 그 문서 안에서 다시 묻는다(ask.js가 받는다).
function documentChoices(task, documents) {
  return h('div', {class: 'choices'}, documents.map(d => h('button', {class: 'btn choice', onclick: () =>
    document.dispatchEvent(new CustomEvent('ask-in-documents', {detail: {question: task.config.question, run_ids: [d.run_id]}}))},
    documentLine(d))));
}

// 답을 어느 문서에서 찾았는지. 고른 문서는 항상 보여 준다(자동으로 고른 것이라 틀릴 수 있다).
function foundIn(task) {
  const chosen = task.agent?.documents;
  if (!chosen) return null;
  const subjects = chosen.subjects || [];
  if (chosen.status === 'selected') {
    const docs = subjects.flatMap(s => s.status === 'selected' ? s.documents : []);
    const names = docs.length <= 3 ? docs.map(documentLine).join(' / ')
      : subjects.filter(s => s.status === 'selected').map(s => '‘' + s.subject + '’').join(', ') + ' 문서 ' + docs.length + '개';
    return h('p', {class: 'found-in'}, h('b', {}, '찾은 문서 '), names,
      h('span', {class: 'muted'}, ' · 질문에 나온 이름으로 자동으로 골랐습니다. 다르면 "특정 문서에서만 찾기"로 직접 고르세요.'));
  }
  if (chosen.status === 'unconfirmed') return h('p', {class: 'found-in'}, h('b', {}, '대상 문서 미확정 '),
    '질문이 가리킨 대상(' + subjects.filter(s => s.status === 'unconfirmed').map(s => s.subject).join(', ')
    + ')의 문서를 이름으로 정하지 못해 전체 문서에서 찾았습니다. 인용 근거의 문서 이름을 확인하세요.');
  return null;
}

function stateNotice(kind, task, result, scope) {
  if (kind === 'needs_context') return h('div', {class: 'notice ask'}, result.answer_text || result.answer);
  if (kind === 'choose_document') {
    const ask = result.clarification;
    return h('div', {class: 'notice ask'}, h('b', {}, '어느 문서인가요?'), ' · ‘' + (ask.subject || '') + '’에 해당하는 문서가 '
      + ask.documents.length + '개입니다. 문서를 고르면 그 문서에서 같은 질문의 답을 찾습니다.', documentChoices(task, ask.documents));
  }
  if (kind === 'document_missing') {
    const ask = result.clarification;
    return h('div', {class: 'notice warn'}, h('b', {}, '해당 문서 없음'), ' · ‘' + (ask.subject || '') + '’에 해당하는 문서가 검색 대상에 없습니다. 답변을 만들지 않았습니다.'
      + (ask.documents.length ? ' 이름이 일부 겹치는 문서가 있습니다. 찾던 문서면 고르세요.' : ''),
      ask.documents.length ? documentChoices(task, ask.documents) : null);
  }
  if (kind === 'error') {
    const model = task.answer_failure_audit || ['ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT', 'EMBEDDING_CALL_FAILED_SEE_AUDIT'].includes(task.error);
    return h('div', {class: 'notice error'}, h('b', {}, '처리 오류'), ' · ' + (task.status === 'interrupted' ? say('SERVER_RESTARTED') : say(task.error))
      + (model ? '\n외부 모델 호출 단계에서 실패했습니다. 잠시 뒤 다시 질문해 보세요.' : '') + '\n답을 만들지 못했습니다. "답변 보류"나 "근거 없음"이 아니라 실행 실패입니다.');
  }
  if (kind === 'no_evidence') return h('div', {class: 'notice warn'}, h('b', {}, '검색 근거 없음'), ' · ' + (scope || '검색 범위') + '에서 질문과 맞는 내용을 찾지 못했습니다. 답변을 만들지 않았습니다.');
  if (kind === 'abstained') {
    if (result.subject_check && result.subject_check.passed === false) return h('div', {class: 'notice warn'}, h('b', {}, '답변 보류'),
      ' · 찾은 근거가 질문의 대상(' + result.subject_check.missing.join(', ') + ')을 언급하지 않는 문서의 것이어서 답으로 쓰지 않았습니다. 다른 대상의 값일 수 있습니다.');
    const why = result.assessment_validation?.assessment?.explanation;
    return h('div', {class: 'notice warn'}, h('b', {}, '답변 보류'), ' · 관련 내용은 찾았지만, 찾은 근거만으로는 답을 확정하지 못했습니다.'
      + (result.missing_sources?.length ? '\n질문에 나온 대상이 근거에 없습니다: ' + result.missing_sources.join(', ') : '') + (why ? '\n모델 설명: ' + why : ''));
  }
  return null;
}

// 실행 ID·색인 버전·비용 같은 개발 정보는 여기에만 둔다.
function details(task, result, evidence, scope) {
  const cost = (result.estimated_cost_usd || 0) + (task.agent?.plan_estimated_cost_usd || 0) + (task.agent?.rerank_estimated_cost_usd || 0)
    + (result.generation_mode === 'raw_grid_reading' ? 0 : (task.agent?.grid_estimated_cost_usd || 0)) + (task.embedding_audit?.estimated_cost_usd || 0);
  const plan = task.agent ? (task.agent.plan_check?.fallback ? '질문 이해 실패, 질문 원문으로 검색' : (task.agent.plan?.requirements || []).map(r => r.text).join(' · ')) : '';
  return h('details', {class: 'more'}, h('summary', {}, '상세 정보'),
    h('p', {class: 'hint'}, [scope && '검색 범위: ' + scope, '근거 ' + evidence.length + '개', plan && '질문 이해: ' + plan, task.agent?.retried && '부족한 부분으로 1회 재검색',
      '추정 비용 $' + cost.toFixed(5), task.updated_at && new Date(task.updated_at).toLocaleString()].filter(Boolean).join(' · ')),
    h('pre', {}, JSON.stringify({request_id: task.request_id, index_version: task.index_version, status: task.status, stage: task.stage, error: task.error, scope: task.scope,
      answer_raw: result.answer, reasons: result.reasons, model: result.model, trace_id: task.agent?.trace_id, rerank: task.agent?.rerank, retrieval: task.retrieval_audit,
      answer_failure: task.answer_failure_audit, evidence: evidence.map(e => ({id: e.evidence_id, run_id: e.run_id, table_id: e.table_id, cell_id: e.cell_id, content_id: e.content_id}))}, null, 2)));
}

// 질문 한 건을 고른다: 그 질문의 첫 인용 근거를 원문 패널에 연다. 인용이 없으면 앞 질문의 원문이 남지 않게 패널을 비운다.
function showTurn(turn, used, kind) {
  document.querySelectorAll('.turn.current').forEach(t => t.classList.remove('current'));
  turn.classList.add('current');
  if (used.length) return openSource(used[0], turn.querySelector('.cite'));
  closeSource('이 질문에는 인용 근거가 없습니다', kind === 'needs_context' ? '질문의 조건을 확인한 뒤 원문을 검색합니다.'
    : kind === 'abstained' ? '답변을 보류해 인용한 근거가 없습니다. 검색된 근거는 질문 아래 "검색된 근거"를 펼쳐 하나씩 열어 볼 수 있습니다.'
    : kind === 'error' ? '처리 오류로 답을 만들지 못했습니다.' : '질문과 맞는 내용을 찾지 못했습니다.');
}

// top: 새 답변을 맨 위에 둔다. open: 새 답변이면 그 질문의 근거를 바로 원문 패널에 연다(답을 읽으며 곧바로 대조).
export function renderTurn(task, top = true, open = true) {
  const turn = h('article', {class: 'turn', data: {request: task.request_id}});
  const result = task.result || {}, selection = result.selection || {evidence_ids: [], values_raw: []};
  const evidence = task.evidence || [];
  const picked = new Map((selection.evidence_ids || []).map((id, i) => [id, (selection.values_raw || [])[i]]));
  const used = (selection.evidence_ids || []).map(id => evidence.find(e => e.evidence_id === id)).filter(Boolean);
  const kind = outcome(task);
  turn.prepend(h('button', {class: 'q', title: '이 질문의 근거를 원문 패널에 열기', onclick: () => showTurn(turn, used, kind)}, task.config.question));
  const scope = task.scope ? (task.scope.kind === 'corpus' ? '전체 문서 ' + task.scope.documents + '개' : '고른 문서 ' + task.scope.documents + '개') : '';
  add(turn, stateNotice(kind, task, result, scope));
  add(turn, foundIn(task));
  if (kind === 'answered') {
    turn.append(h('div', {class: 'answer'}, result.answer_text || result.answer || ''));
    if (result.generation_mode === 'raw_grid_reading') turn.append(h('div', {class: 'notice warn'}, h('b', {}, '구조가 확정되지 않은 표를 읽은 답'),
      ' · 이 표는 행·열 구조를 확정하지 못해, 표의 칸 배치를 모델이 직접 읽고 고른 답입니다. 옆 칸의 값을 고른 것일 수 있으니 인용 근거를 눌러 원문 표에서 위치를 확인하세요.'));
    if (result.reading_incomplete) turn.append(h('div', {class: 'notice warn'}, h('b', {}, '일부만 답했을 수 있습니다'),
      ' · 전체를 묻는 질문인데 ' + (result.reading_incomplete.reason === 'CELLS_WITHHELD' ? '표에 위치가 확정되지 않아 근거로 쓰지 못한 칸이 있습니다'
        : '표가 커서 통째로 읽지 못했습니다') + '. 아래 인용 근거의 원문 표에서 나머지 항목을 확인하세요.'));
    if (result.answer_text_source === 'template') turn.append(h('p', {class: 'hint'}, '모델이 쓴 문장이 검사를 통과하지 못해, 근거 값으로 만든 문장을 보여 줍니다.'));
    for (const alt of result.alternatives || []) turn.append(h('p', {class: 'hint'}, '다른 해석: ' + alt.interpretation));
  }
  if (used.length) add(turn, h('h3', {}, '인용 근거 ' + used.length + '개'), used.map((e, i) => citation(task, e, i + 1, picked.get(e.evidence_id))));
  const others = evidence.filter(e => !picked.has(e.evidence_id));
  if (others.length) turn.append(h('details', {class: 'more'}, h('summary', {}, (kind === 'answered' ? '검색됐지만 답변에 쓰이지 않은 근거 ' : '검색된 근거 ') + others.length + '개'), others.map(e => citation(task, e, 0, null))));
  if (task.status === 'completed' || evidence.length) turn.append(feedbackBox(task, used));
  turn.append(details(task, result, evidence, scope));
  $('history-empty').hidden = true;
  if (top) $('history').prepend(turn); else $('history').append(turn);
  if (open) showTurn(turn, used, kind);
}
