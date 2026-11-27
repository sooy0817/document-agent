// 역할: 질문 범위(선택). 비어 있으면 전체 검색이다. 고른 문서만 기억하고 전체 목록은 화면에 싣지 않는다.
import {$, h, fill, icon} from './dom.js';
import {call} from './api.js';

export const chosen = new Map();  // run_id → 문서 이름
let listed = {text: '', offset: 0, total: 0}, listing = 0, paging = false;

function uncheck(run) { document.querySelectorAll('#filter-list input').forEach(c => { if (!run || c.dataset.run === run) c.checked = false; }); }

export function renderScope() {
  $('scope-summary').textContent = chosen.size ? '고른 문서 ' + chosen.size + '개에서만 검색' : '전체 문서에서 검색';
  fill($('scope-chosen'), [...chosen].map(([run, name]) => h('span', {class: 'chip'}, name,
    h('button', {title: '범위에서 빼기', 'aria-label': name + ' 범위에서 빼기', onclick: () => { chosen.delete(run); renderScope(); uncheck(run); }}, icon('x')))));
}

// 문서는 이름으로 찾아 한 쪽 분량씩만 받아 온다.
async function load(append) {
  if (append && paging) return;
  const text = append ? listed.text : $('filter-text').value.trim();
  if (!append) listed = {text, offset: 0, total: 0};
  // 겹친 요청 방지: 새 검색은 앞선 응답을 버리고, "더 보기"는 한 번에 하나만 받는다.
  const ticket = append ? listing : ++listing, offset = listed.offset;
  let page; paging = append;
  try { page = await call('/api/rag/corpus/documents?limit=20&offset=' + offset + (text ? '&q=' + encodeURIComponent(text) : '')); }
  finally { if (append) paging = false; }
  if (ticket !== listing) return;
  const list = $('filter-list'); if (!append) list.replaceChildren();
  if (!page.total) list.append(h('p', {class: 'hint', style: 'padding:8px 12px;margin:0'}, text ? '이름에 "' + text + '"이(가) 들어간 문서가 없습니다.' : '검색 가능한 문서가 없습니다.'));
  for (const d of page.documents) {
    const check = h('input', {type: 'checkbox', checked: chosen.has(d.run_id), data: {run: d.run_id},
      onchange: () => { if (check.checked) chosen.set(d.run_id, d.name); else chosen.delete(d.run_id); renderScope(); }});
    list.append(h('div', {}, h('label', {}, check, h('span', {style: 'flex:1'}, d.name), h('span', {class: 'muted'}, d.pages + '쪽'))));
  }
  listed.offset = offset + page.documents.length; listed.total = page.total;
  $('filter-more').hidden = listed.offset >= listed.total;
  $('filter-count').textContent = page.total ? listed.offset + ' / ' + page.total + '개 표시' : '';
}

export function initScope(onError) {
  let typing = null;
  $('filter-text').addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => load(false).catch(onError), 250); });
  $('filter-box').addEventListener('toggle', () => { if ($('filter-box').open) load(false).catch(onError); });
  $('filter-more').onclick = () => load(true).catch(onError);
  $('filter-clear').onclick = () => { chosen.clear(); renderScope(); uncheck(); };
  renderScope();
}
