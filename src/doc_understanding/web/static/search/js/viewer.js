// 역할: 원문 패널. 인용 근거의 문서·쪽을 열고, 좌표를 쓸 수 있을 때만 근거 영역을 표시한다. 표 근거는 격자, 본문 근거는 앞뒤 문맥을 함께 보여 준다.
import {$, h, fill, one} from './dom.js';
import {call} from './api.js';
import {REASONS, say} from './config.js';
import {boxesOf, kindOf} from './evidence.js';
import {isLarge, renderGrid} from './grid.js';

let view = null, token = 0;

// 쪽 그림 위에 근거 영역(노랑)과, 표에서 고른 칸(보조색)을 그린다. 서버가 좌표를 쓸 수 없다고 하면 아무것도 그리지 않는다.
function drawBoxes() {
  const sheet = $('page-wrap').querySelector('.sheet'); if (!sheet || !view?.info) return;
  sheet.querySelectorAll('.hl').forEach(b => b.remove());
  if (!view.info.positions) return;
  const {width, height} = view.info;
  const draw = (b, cls) => sheet.append(h('div', {class: 'hl' + (cls ? ' ' + cls : ''), style: `left:${b.x0 / width * 100}%;top:${b.y0 / height * 100}%;width:${(b.x1 - b.x0) / width * 100}%;height:${(b.y1 - b.y0) / height * 100}%`}));
  if (view.page === view.citedPage) boxesOf(view.e, view.page).forEach(b => draw(b));
  const extra = view.extra && view.extra.page === view.page;
  if (extra) draw(view.extra.bbox, 'second');
  const first = sheet.querySelector(extra ? '.hl.second' : '.hl');
  const scroll = () => first && first.scrollIntoView({block: 'center', inline: 'nearest'});
  const img = sheet.querySelector('img'); if (img.complete) scroll(); else img.addEventListener('load', scroll, {once: true});
}

async function showPage() {
  const mine = ++token, e = view.e;
  let info;
  try { info = await call('/api/search/source?run_id=' + encodeURIComponent(e.run_id) + '&page=' + view.page); }
  catch (error) { if (mine !== token) return; $('viewer-note').hidden = false; $('viewer-note').textContent = '원문 정보를 읽지 못했습니다: ' + say(error.message); $('page-wrap').hidden = true; return; }
  if (mine !== token) return;
  view.info = info;
  $('viewer-title').textContent = info.name; $('viewer-title').title = info.name;
  const wrap = $('page-wrap'), notes = [];
  $('viewer-controls').hidden = info.kind !== 'pdf';
  $('page-label').textContent = view.page + ' / ' + info.pages;
  $('page-prev').disabled = view.page <= 1; $('page-next').disabled = view.page >= info.pages;
  $('page-cited').hidden = view.page === view.citedPage;
  if (info.kind === 'pdf' && info.image) {
    fill(wrap, h('div', {class: 'sheet', style: 'width:' + $('zoom').value + '%'}, h('img', {alt: info.name + ' ' + view.page + '쪽 원문', src: info.image})));
    wrap.hidden = false;
    if (!info.positions) notes.push(REASONS[info.positions_reason] || '근거 위치를 표시할 수 없습니다.');
    else if (view.page === view.citedPage && !boxesOf(e, view.page).length) notes.push('이 근거에는 위치 정보가 없습니다. 쪽만 열었습니다.');
    drawBoxes();
  } else if (info.kind === 'html') {
    if (!wrap.querySelector('iframe')) fill(wrap, h('iframe', {class: 'source', title: info.name + ' 원문', src: info.html, sandbox: ''}));
    wrap.hidden = false;
    notes.push(REASONS.NO_PAGE_COORDINATES);
  } else {
    wrap.replaceChildren(); wrap.hidden = true;
    notes.push(REASONS[info.positions_reason] || REASONS.SOURCE_NOT_AVAILABLE);
  }
  if (!info.searchable) notes.push('이 근거는 지금 검색에 쓰이는 판이 아닌 예전 색인의 문서입니다.');
  $('viewer-note').hidden = !notes.length; $('viewer-note').textContent = notes.join('\n');
  const quote = $('viewer-quote'); quote.hidden = false;
  fill(quote, h('p', {class: 'muted'}, '인용 원문 · ' + view.citedPage + '쪽 · ' + kindOf(e)), h('span', {class: 'quote'}, e.value_raw ?? '(읽을 수 없는 칸)'));
  if (info.kind === 'html') {
    const words = one(e.value_raw).slice(0, 80);
    if (words) quote.append(h('p', {class: 'hint'}, h('a', {href: info.html + '#:~:text=' + encodeURIComponent(words), target: '_blank', rel: 'noopener'}, '새 창에서 원문 열고 같은 문구 찾기'),
      '. 같은 문구가 처음 나오는 곳으로 이동하므로 인용한 위치와 다를 수 있습니다.'));
  }
}

// 표의 칸을 누르면 원문에서 그 칸의 위치를 보조색으로 표시한다.
function pick(grid, cell) {
  if (!cell.bbox || !view) return;
  view.extra = {page: grid.page, bbox: cell.bbox};
  if (view.page !== grid.page) { view.page = grid.page; showPage(); } else drawBoxes();
}

async function showStructure() {
  if (!view) return;
  const e = view.e, box = $('viewer-structure'); box.replaceChildren();
  try {
    if (e.table_id && e.cell_id) {
      const grid = await call('/api/search/table?run_id=' + encodeURIComponent(e.run_id) + '&table_id=' + encodeURIComponent(e.table_id));
      if (view?.e !== e) return;
      const paint = full => {
        const {box: table, partial, found} = renderGrid(grid, e.cell_id, full, cell => pick(grid, cell));
        const nested = (grid.nested?.path || []).filter(Boolean).map(one).join(' → ');
        fill(box, h('h3', {style: 'margin-top:16px'}, '표에서 본 근거'),
          h('p', {class: 'hint'}, grid.page + '쪽의 표 · ' + grid.rows + '행 × ' + grid.columns + '열' + (partial ? ' · 인용 칸 주변만 표시 ' : ' '),
            partial ? h('button', {class: 'btn text', onclick: () => paint(true)}, '표 전체 보기') : full && isLarge(grid) ? h('button', {class: 'btn text', onclick: () => paint(false)}, '주변만 보기') : null),
          grid.scope.length ? h('p', {class: 'hint'}, '표의 적용 범위(후보): ' + grid.scope.map(one).join(' / ')) : null,
          nested ? h('p', {class: 'hint'}, '다른 표의 칸 안에 들어 있는 표입니다. 담긴 위치: ' + nested) : null,
          found ? null : h('div', {class: 'notice warn'}, '인용한 칸을 이 표에서 찾지 못했습니다. 표 전체를 보여 줍니다.'),
          e.lookup_eligible === false ? h('div', {class: 'notice warn'}, '이 칸의 행·열 경로는 확정되지 않았습니다. 표에서 직접 위치를 확인하세요.') : null,
          h('div', {class: 'grid-wrap'}, table),
          h('p', {class: 'hint'}, '노란 칸이 인용 근거, 옅은 칸이 같은 행·열입니다. 칸을 누르면 원문에서 그 칸의 위치를 표시합니다. 머리글과 경로 구분은 분석 후보입니다.'));
        table.querySelector('.cited')?.scrollIntoView({block: 'nearest', inline: 'center'});
      };
      paint(false);
    } else if (e.content_id) {
      const found = await call('/api/search/context?run_id=' + encodeURIComponent(e.run_id) + '&content_id=' + encodeURIComponent(e.content_id));
      if (view?.e !== e) return;
      fill(box, h('h3', {style: 'margin-top:16px'}, '앞뒤 문맥'), h('div', {class: 'context'}, found.items.map(i => h('p', {class: i.cited ? 'cited' : ''}, (i.page !== e.page ? '(' + i.page + '쪽) ' : '') + i.text))),
        h('p', {class: 'hint'}, '문서에서 읽은 순서로 앞뒤 두 문단씩입니다. 표시된 줄이 인용 근거입니다.'));
    }
  } catch (error) {
    if (view?.e !== e) return;
    fill(box, h('p', {class: 'hint'}, error.status === 404 ? '이 근거의 표·문맥 정보가 색인에 없습니다.' : '표·문맥을 읽지 못했습니다: ' + say(error.message)));
  }
}

export async function openSource(e, card) {
  document.querySelectorAll('.cite.open').forEach(c => c.classList.remove('open'));
  if (card) card.classList.add('open');
  $('viewer-empty').hidden = true;
  $('page-wrap').replaceChildren();
  view = {e, page: e.page, citedPage: e.page, extra: null, info: null};
  await showPage();
  if (view?.e !== e) return;  // 여는 동안 다른 근거를 열었거나 패널을 비웠으면 이어서 그리지 않는다.
  await showStructure();
  if (window.matchMedia('(max-width: 1020px)').matches) $('viewer').scrollIntoView({block: 'start'});
}

// 인용 근거가 없는 질문을 골랐을 때: 앞 질문의 원문이 남아 있으면 이 질문의 근거로 오해하므로 패널을 비우고 이유를 적는다.
export function closeSource(title, text) {
  token++; view = null;
  document.querySelectorAll('.cite.open').forEach(c => c.classList.remove('open'));
  $('viewer-title').textContent = '원문'; $('viewer-title').title = '';
  $('viewer-controls').hidden = true; $('viewer-note').hidden = true; $('viewer-quote').hidden = true;
  $('page-wrap').replaceChildren(); $('page-wrap').hidden = true; $('viewer-structure').replaceChildren();
  fill($('viewer-empty'), h('h3', {}, title), h('p', {}, text));
  $('viewer-empty').hidden = false;
}

export function initViewer() {
  $('page-prev').onclick = () => { if (view && view.page > 1) { view.page--; showPage(); } };
  $('page-next').onclick = () => { if (view && view.info && view.page < view.info.pages) { view.page++; showPage(); } };
  $('page-cited').onclick = () => { if (view) { view.page = view.citedPage; view.extra = null; showPage(); } };
  $('zoom').oninput = () => { const sheet = $('page-wrap').querySelector('.sheet'); if (sheet) sheet.style.width = $('zoom').value + '%'; };
}
