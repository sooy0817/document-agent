// 역할: 질문을 보내고, 자기가 보낸 질문의 실행 단계를 따라가다가 끝나면 답변을 그린다.
import {$, h, fill, sleep, store, keep} from './dom.js';
import {call, post, rid} from './api.js';
import {ANSWER, EMBEDDING, STAGES, say} from './config.js';
import {corpus, refreshStatus} from './status.js';
import {chosen} from './scope.js';
import {renderTurn} from './answer.js';

let asking = false;

export function showAskError(message) { const box = $('ask-error'); box.textContent = say(message); box.hidden = false; }

function sync() { $('ask-controls').disabled = asking || !!(corpus.status && corpus.status.busy_question); }
function setAsking(value) { asking = value; sync(); if (!value) $('progress').hidden = true; }

// 서버가 알려 준 단계만 보여 준다(지나간 단계와 지금 단계).
function steps(seen, now) {
  $('progress').hidden = false;
  fill($('steps'), seen.map(s => h('li', {class: s === now ? 'now' : s === 'retry' ? 'retry' : 'done'}, STAGES[s] || s)));
  $('progress-text').textContent = (STAGES[now] || now) + ' 중';
}

async function follow(id) {
  const seen = [];
  for (;;) {
    let task;
    try { task = await call('/api/rag/corpus/tasks/' + id); }
    catch (error) { showAskError('질문 진행 상태를 읽지 못했습니다: ' + say(error.message)); break; }
    if (['queued', 'running'].includes(task.status)) {
      const stage = task.status === 'queued' ? 'queued' : task.stage;
      if (seen[seen.length - 1] !== stage) {
        // 첫 답변에서 근거가 부족하면 한 번 더 검색한다. 같은 단계 이름이 두 번 나열되지 않게, 두 번째는 '다시 찾는 중'으로 새로 시작한다.
        if (stage === 'searching' && seen.includes('answering')) seen.splice(0, seen.length, 'retry');
        seen.push(stage);
      }
      steps(seen, stage);
      await sleep(600); continue;
    }
    renderTurn(task, true); break;
  }
  setAsking(false); refreshStatus();
}

function ask() {
  $('ask-error').hidden = true;
  const question = $('question').value.trim();
  if (!question) return showAskError('질문을 입력하세요.');
  if (chosen.size > 200) return showAskError('문서는 200개까지 고를 수 있습니다. 더 넓게 찾으려면 전체 검색을 쓰세요.');
  // 문서를 고르지 않으면 전체 검색이다. 이때 문서 ID를 보내지 않는다(서버가 지금 검색 가능한 전체로 범위를 정한다).
  return send(question, chosen.size ? [...chosen.keys()] : null);
}

// runIds: 이 문서들 안에서만 찾는다(되묻기에서 고른 문서 포함). null이면 전체에서 찾고 서버가 질문의 대상 문서를 고른다.
async function send(question, runIds) {
  $('ask-error').hidden = true;
  if (asking) return showAskError('RAG_BUSY');
  const id = rid();
  const body = {request_id: id, question, mode: 'hybrid', answer: true, agent: true, allow_external: true,
    run_ids: runIds, answer_mode: 'selection', embedding: EMBEDDING, ...ANSWER};
  setAsking(true); steps(['queued'], 'queued');
  try { await post('/api/rag/corpus/question', body); }
  catch (error) { setAsking(false); return showAskError(error.message); }
  const turns = store(sessionStorage, 'search.turns', []); turns.push(id); keep(sessionStorage, 'search.turns', turns.slice(-12));
  follow(id);
}

// 이 창에서 했던 질문은 새로고침해도 다시 보여 준다(서버에 저장된 실행 기록을 읽는다).
async function restore() {
  for (const id of store(sessionStorage, 'search.turns', [])) {
    try {
      const task = await call('/api/rag/corpus/tasks/' + id);
      if (['queued', 'running'].includes(task.status)) { setAsking(true); follow(id); } else renderTurn(task, true, false);
    } catch (_) { /* 지워진 기록은 건너뛴다 */ }
  }
}

export function initAsk() {
  $('ask').onclick = ask;
  $('question').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) ask(); });
  document.addEventListener('corpus-status', sync);
  // 되묻기 카드에서 문서를 고르면 같은 질문을 그 문서 안에서 다시 묻는다.
  document.addEventListener('ask-in-documents', e => send(e.detail.question, e.detail.run_ids));
  restore();
}
