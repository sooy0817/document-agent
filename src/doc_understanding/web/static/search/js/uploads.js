// 역할: 문서 추가 팝업. 파일을 올리고, 문서마다 ① 분석과 ② 검색 준비(색인·임베딩)의 상태를 따로 보여 준다.
import {$, h, fill, store, keep} from './dom.js';
import {call, post, rid, tokenHeader} from './api.js';
import {EMBEDDING, REVIEW_MODEL, say} from './config.js';
import {refreshStatus} from './status.js';

const PARSE = {queued: '분석 대기', running: '분석 중', done: '분석 완료', error: '분석 실패', unknown: '분석 상태 확인 필요'};
const SEARCH = {waiting: ['분석이 끝나면 검색 준비', ''], not_ingested: ['검색 준비 전', 'warn'], ingesting: ['검색 준비 중', 'warn'], keyword_only: ['색인 완료 · 임베딩 미완료(키워드로만 검색됨)', 'warn'],
  ready: ['검색 가능', 'ok'], stale: ['예전 색인 · 다시 준비 필요', 'warn'], stored_not_latest: ['같은 문서의 다른 판이 검색에 쓰임', ''], failed: ['검색 준비 실패', 'bad']};
const INGEST_STAGE = {queued: '대기', reviewing: '구조 검토', storing: '색인', describing: '문서 정보 추출', embedding: '임베딩'};

let tracked = store(localStorage, 'search.uploads', []);   // 이 브라우저에서 올린 문서 작업
const requested = new Set(), autoIds = new Set(), ingestError = {};
let timer = null;

const showError = message => { $('upload-error').textContent = say(message); $('upload-error').hidden = false; };
const track = id => { tracked = [id, ...tracked.filter(x => x !== id)].slice(0, 30); keep(localStorage, 'search.uploads', tracked); };

// 검색 준비(구조 검토 → 색인 → 임베딩)를 시작한다. 시작했으면 true.
async function ingest(ids) {
  const body = {request_id: rid(), job_ids: ids, embed: true, allow_external: true, structure_review: true, metadata: true, embedding: EMBEDDING, ...REVIEW_MODEL};
  ids.forEach(id => { requested.add(id); delete ingestError[id]; });
  try { await post('/api/rag/corpus/ingest', body); return true; }
  catch (error) {
    ids.forEach(id => requested.delete(id));
    // 다른 문서를 준비하는 중이면 끝난 뒤 다음 확인 때 다시 시도한다.
    if (error.code !== 'RAG_BUSY') ids.forEach(id => { ingestError[id] = error.message; });
    return false;
  }
}

function row(item) {
  const [label, kind] = SEARCH[item.search] || [item.search, 'warn'];
  const stage = item.search === 'ingesting' ? ' · ' + (INGEST_STAGE[item.stage] || item.stage || '') + (item.progress?.total ? ' ' + (item.progress.embedded ?? 0) + '/' + item.progress.total : '') : '';
  const line = h('div', {class: 'row'},
    h('span', {class: 'tag ' + (item.parsing === 'done' ? 'ok' : item.parsing === 'error' ? 'bad' : 'warn')}, '① ' + (PARSE[item.parsing] || item.parsing)),
    h('span', {class: 'tag ' + kind}, '② ' + label + stage));
  if (item.parsing === 'error') line.append(h('button', {class: 'btn small', onclick: async () => {
    try { const next = await call('/api/jobs/' + item.job_id + '/retry', {method: 'POST', headers: tokenHeader()}); tracked = tracked.map(x => x === item.job_id ? next.id : x); keep(localStorage, 'search.uploads', tracked); poll(); }
    catch (error) { showError(error.message); } }}, '다시 분석'));
  if (['not_ingested', 'failed', 'stale', 'keyword_only'].includes(item.search) && !requested.has(item.job_id))
    line.append(h('button', {class: 'btn small', onclick: () => ingest([item.job_id]).then(poll)}, item.search === 'not_ingested' ? '검색 준비 시작' : '검색 준비 다시 시도'));
  const problem = item.parsing_error || (item.search === 'failed' && say(item.search_error)) || (ingestError[item.job_id] && say(ingestError[item.job_id]));
  return h('div', {class: 'upload-item'}, h('div', {class: 'name'}, (item.name || item.job_id) + (item.pages ? ' · ' + item.pages + '쪽' : '')), line,
    problem ? h('p', {class: 'hint', style: 'color:var(--bad)'}, problem) : null,
    item.search === 'ready' ? h('p', {class: 'hint'}, '색인과 임베딩이 끝났습니다(검색 항목 ' + item.entries + '개). 지금부터 질문에 쓰입니다.') : null,
    item.search === 'failed' || item.search === 'keyword_only' ? h('p', {class: 'hint'}, '다시 시도해도 문서가 중복으로 쌓이지 않습니다. 이미 만든 임베딩은 다시 받지 않습니다.') : null);
}

async function poll() {
  clearTimeout(timer);
  if (!tracked.length) { $('uploads').replaceChildren(); $('add-dot').hidden = true; return; }
  let active = false;
  try {
    const found = await call('/api/search/uploads?ids=' + tracked.join(','));
    const known = found.items.filter(i => i.parsing !== 'unknown' || i.name);
    for (const i of known) if (['ready', 'failed', 'keyword_only'].includes(i.search)) requested.delete(i.job_id);
    // 분석이 끝난 문서는 자동으로 검색 준비를 시작한다(적재가 비어 있을 때, 한 번에 묶어서).
    // 시작하지 못했으면(다른 적재가 먼저 시작됨 등) 곧바로 다시 묻지 않고 다음 확인 주기를 기다린다.
    const due = known.filter(i => i.search === 'not_ingested' && !requested.has(i.job_id) && !ingestError[i.job_id] && autoIds.has(i.job_id)).map(i => i.job_id);
    if (due.length && !found.busy_ingest && await ingest(due)) return poll();
    fill($('uploads'), known.map(row));
    active = known.some(i => ['queued', 'running'].includes(i.parsing) || i.search === 'ingesting' || requested.has(i.job_id) || (i.search === 'not_ingested' && autoIds.has(i.job_id) && !ingestError[i.job_id]));
    if (known.some(i => i.search === 'ready')) refreshStatus();
  } catch (error) { showError('처리 상태를 읽지 못했습니다: ' + say(error.message)); active = true; }
  $('add-dot').hidden = !active;  // 팝업을 닫아도 처리 중임을 머리의 버튼에 표시한다
  if (active) timer = setTimeout(poll, 2000);
}

async function upload(file) {
  $('upload-error').hidden = true;
  if (!file) return showError('올릴 파일을 고르세요.');
  if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) return showError('PDF, PNG 또는 JPG 파일만 올릴 수 있습니다.');
  if (file.size > 20 * 1024 * 1024) return showError('20MB 이하 파일을 선택해 주세요.');
  $('upload').disabled = true;
  try {
    const job = await call('/api/upload', {method: 'POST', body: file, headers: {'Content-Type': file.type || 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name), ...tokenHeader()}});
    track(job.id); if ($('auto-ingest').checked) autoIds.add(job.id);
    $('file').value = ''; poll();
  } catch (error) { showError(error.message); } finally { $('upload').disabled = false; }
}

// 분석은 끝났지만 검색에 아직 없는 문서(다른 화면이나 예전에 올린 것).
async function loadPending() {
  try {
    const found = await call('/api/rag/corpus');
    const waiting = found.jobs.filter(j => j.state !== 'in_corpus');
    $('pending-count').textContent = '(' + waiting.length + ')';
    const labels = {not_ingested: '검색 준비 전', stale: '예전 색인', stored_not_latest: '다른 판이 검색에 쓰임'};
    fill($('pending'), waiting.length ? waiting.slice(0, 50).map(j => h('div', {class: 'row'}, h('span', {}, (j.name || j.job_id) + (j.pages ? ' · ' + j.pages + '쪽' : '')),
      h('span', {class: 'row', style: 'gap:8px'}, h('span', {class: 'tag warn'}, labels[j.state] || j.state),
        h('button', {class: 'btn small', onclick: () => { track(j.job_id); poll(); loadPending(); }}, '위 목록에 추가'))))
      : h('p', {class: 'hint'}, '분석이 끝난 문서는 모두 검색에 들어 있습니다.'));
  } catch (error) { fill($('pending'), h('p', {class: 'hint'}, '목록을 읽지 못했습니다: ' + say(error.message))); }
}

export function initUploads() {
  const dialog = $('adder'), drop = $('drop');
  $('add-open').onclick = () => { dialog.showModal(); poll(); };
  $('adder-close').onclick = () => dialog.close();
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
  $('upload').onclick = () => upload($('file').files[0]);
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });
  $('pending-box').addEventListener('toggle', () => { if ($('pending-box').open) loadPending(); });
  poll();
}
