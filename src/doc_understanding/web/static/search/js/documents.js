// 역할: 문서 목록. 검색 가능한 문서를 한 쪽씩 나눠 보여 주고(쪽 번호로 이동), 문서를 누르면 문서 보기 팝업을 연다.
import {$, h, fill, icon} from './dom.js';
import {call} from './api.js';
import {EMBEDDING, say} from './config.js';
import {openReader} from './reader.js';

const PER_PAGE = 12;
let state = {text: '', page: 1, total: 0}, ticket = 0, loaded = false;

// 이 화면의 질문이 쓰는 임베딩이 다 있어야 '검색 가능'이다. 색인만 된 문서는 키워드로만 검색된다.
function ready(d) {
  const mine = (d.embeddings || []).find(e => e.provider === EMBEDDING.provider && e.model === EMBEDDING.model && e.dimensions === EMBEDDING.dimensions);
  return !!mine && mine.vectors >= d.entries && d.entries > 0;
}
const typeOf = d => (d.source_media_type || '').includes('html') ? 'HTML' : (d.source_media_type || '').includes('pdf') ? 'PDF' : '문서';

function row(d) {
  return h('li', {}, h('button', {class: 'doc-row', 'aria-label': d.name + ' 열기', onclick: () => openReader(d)},
    h('span', {class: 'name', title: d.name}, d.name, h('span', {class: 'sub muted'}, typeOf(d))),
    h('span', {class: 'pages'}, d.pages + '쪽'),
    h('span', {class: 'state'}, ready(d) ? h('span', {class: 'tag ok'}, '검색 가능') : h('span', {class: 'tag warn'}, '키워드로만 검색')),
    icon('right')));
}

// 쪽 번호: 7쪽 이하면 모두, 넘으면 처음·끝·지금 쪽 앞뒤만 보여 주고 나머지는 줄인다.
function pager(pages) {
  const nav = $('docs-pager');
  if (pages <= 1) return nav.replaceChildren();
  const near = pages <= 7 ? Array.from({length: pages}, (_, i) => i + 1) : [1, pages, state.page - 1, state.page, state.page + 1];
  const wanted = [...new Set(near.filter(p => p >= 1 && p <= pages))].sort((a, b) => a - b);
  const items = [h('button', {class: 'btn', 'aria-label': '이전 쪽', disabled: state.page <= 1, onclick: () => go(state.page - 1)}, icon('left'))];
  wanted.forEach((p, i) => {
    if (i && p - wanted[i - 1] > 1) items.push(h('span', {class: 'gap', 'aria-hidden': 'true'}, '⋯'));
    items.push(h('button', {class: 'btn', 'aria-label': p + '쪽', 'aria-current': p === state.page ? 'page' : undefined, onclick: () => go(p)}, String(p)));
  });
  items.push(h('button', {class: 'btn', 'aria-label': '다음 쪽', disabled: state.page >= pages, onclick: () => go(state.page + 1)}, icon('right')));
  fill(nav, items);
}

async function load() {
  const mine = ++ticket, list = $('docs-list');
  $('docs-error').hidden = true;
  // 받아 오는 동안 목록 자리를 같은 높이의 뼈대로 채운다(화면이 출렁이지 않게).
  if (!list.querySelector('.doc-row')) fill(list, Array.from({length: 6}, () => h('li', {class: 'skeleton', 'aria-hidden': 'true'}, h('i', {}))));
  let found;
  try { found = await call('/api/rag/corpus/documents?limit=' + PER_PAGE + '&offset=' + (state.page - 1) * PER_PAGE + (state.text ? '&q=' + encodeURIComponent(state.text) : '')); }
  catch (error) { if (mine !== ticket) return; list.replaceChildren(); $('docs-error').hidden = false; $('docs-error').textContent = '문서 목록을 읽지 못했습니다: ' + say(error.message); return; }
  if (mine !== ticket) return;
  state.total = found.total; loaded = true;
  const pages = Math.max(1, Math.ceil(found.total / PER_PAGE));
  if (state.page > pages) return go(pages);
  if (!found.total) fill(list, h('li', {class: 'empty'}, h('h3', {}, state.text ? '"' + state.text + '"이(가) 이름에 들어간 문서가 없습니다' : '검색 가능한 문서가 없습니다'),
    h('p', {}, state.text ? '다른 이름으로 찾아 보세요.' : '오른쪽 위 "문서 추가"로 문서를 올리면 여기에 나타납니다.')));
  else fill(list, found.documents.map(row));
  $('docs-summary').textContent = found.total ? '문서 ' + found.total + '개 중 ' + ((state.page - 1) * PER_PAGE + 1) + '~' + Math.min(state.page * PER_PAGE, found.total) + '번째' : '';
  pager(pages);
}

function go(page) { state.page = page; load(); window.scrollTo({top: 0}); }

export function showDocuments(force) { if (force || !loaded) load(); }

export function initDocuments() {
  let typing = null;
  $('docs-search').addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => { state = {text: $('docs-search').value.trim(), page: 1, total: 0}; load(); }, 250); });
  // 새 문서가 검색 가능해지면 목록을 다시 읽는다(문서 수가 달라졌을 때만).
  let count = null;
  document.addEventListener('corpus-status', () => {
    const now = Number($('docs-count').textContent);
    // 탭이 가려져 있으면 다음에 열 때 다시 읽도록 표시만 해 둔다.
    if (count !== null && now !== count) { if ($('view-docs').hidden) loaded = false; else load(); }
    count = now;
  });
}
