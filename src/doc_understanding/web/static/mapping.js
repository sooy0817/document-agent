// 셀 매핑 검토 화면: 표 셀의 분석 결과(역할·행열 경로·근거 사용 여부·이유)와 테스트셋 기대값을 비교하고 기대값을 고친다.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const compact = s => String(s ?? '').replace(/\s+/g, '');
const clean = s => String(s ?? '').split(/\s+/).filter(Boolean).join(' ');
const samePath = (a, b) => a.length === b.length && a.every((x, i) => compact(x) === compact(b[i]));
const ROLE = {value: '값', row_label: '행 라벨', column_header: '열 머리글', corner: '모서리 머리글', container: '내용을 담는 칸', unknown: '미정', ignore: '채점 제외'};
const LABELS = ['row_label', 'column_header', 'corner'];
const BLOCKER = {
  PATH_NODE_UNRESOLVED: '경로의 라벨 노드가 미확정이다(표 머리글 영역이 모호하면 표 전체가 보류된다).',
  CELL_ROLE_UNRESOLVED: '역할 미정: 셀이 머리글·라벨 경계에 걸쳐 값으로 분류되지 않았다.',
  NOT_A_VALUE_CELL: '값 셀로 분류되지 않았다(라벨·머리글로 봄).',
  ROW_PATH_MISSING: '행 경로가 없다.',
  COLUMN_PATH_MISSING: '열 경로가 없다(열 머리글이 라벨 영역에 걸치거나 없음).',
  LABEL_UNAVAILABLE: '경로 라벨 원문이 비어 있다.',
  PATH_COLLISION: '같은 행·열 경로에 다른 셀이 있어(충돌) 확정하지 않았다.',
  PHYSICAL_UNRESOLVED: '물리 셀 경계가 미확정이다.',
  MAPPING_UNRESOLVED: '매핑이 미확정이다.',
};
const CODE = {
  HEADER_REGION_AMBIGUOUS: '첫 행이 머리글인지 데이터인지 가를 근거가 없어 표 전체 경로를 보류했다.',
  PANEL_SPLIT_AMBIGUOUS: '좌우로 나란한 표일 수 있어 패널을 넘는 경로를 보류했다.',
  PATH_COLLISION: '경로 충돌', CELL_ROLE_UNRESOLVED: '역할 미정', CELL_PATH_UNRESOLVED: '경로 미확정',
  LABEL_UNAVAILABLE: '라벨 원문 없음', PARENT_AMBIGUOUS: '부모 라벨 후보가 여럿',
};
const PATTERN = {contains_content: '내용을 담는 칸 있음', nested_in_cell: '칸 안에 담긴 표', regular_grid: '정상 격자', variable_stub_depth: '행별 라벨 깊이 다름', header_spans_stub: '머리글이 라벨 열에 걸침',
  single_row_box: '1행 상자', key_value: '라벨 | 내용', header_region_ambiguous: '머리글 영역 모호', path_collision: '경로 충돌',
  nested_rows: '중첩 행', multi_level_columns: '다단 머리글'};

let setName = new URLSearchParams(location.hash.slice(1)).get('set') || '';
const setQuery = (first) => setName ? (first ? '?' : '&') + 'set=' + encodeURIComponent(setName) : '';
let overview = null, current = null, edits = {}, selected = null, addMode = null, dirty = false, filter = 'all';

function fail(message) { const e = $('error'); e.hidden = !message; e.textContent = message || ''; }
async function api(url, options = {}) {
  const r = await fetch(url, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : r.status);
  return body;
}
const pct = m => m && m.rate !== null && m.rate !== undefined ? Math.round(m.rate * 100) + '%' : '–';

function status(actual, exp) {
  if (!exp || !actual) return 'ignore';
  if (exp.role === 'ignore') return 'ignore';
  if (exp.role === 'container') return actual.eligible ? 'dup' : 'container';
  if (LABELS.includes(exp.role)) return actual.eligible ? 'false' : 'label';
  if (!exp.row_path.length && !exp.column_path.length) return 'ctx';
  const right = samePath(actual.row_path, exp.row_path) && samePath(actual.column_path, exp.column_path);
  return actual.eligible ? (right ? 'ok' : 'wrong') : (right ? 'withheld' : 'miss');
}

async function loadOverview(reload = false) {
  overview = await api('/api/mapping' + (reload ? '?reload=true' + setQuery(false) : setQuery(true)));
  const picker = $('set-picker');
  picker.innerHTML = overview.sets.map(s => `<option value="${esc(s)}"${s === overview.name ? ' selected' : ''}>${esc(s)}</option>`).join('');
  picker.onchange = () => {
    if (dirty && !confirm('저장하지 않은 변경이 있습니다. 버리고 다른 테스트셋으로 갈까요?')) { picker.value = overview.name; return; }
    setName = picker.value === overview.default_set ? '' : picker.value;
    current = null; selected = null; setDirty(false);
    history.replaceState(null, '', setName ? '#set=' + encodeURIComponent(setName) : '#');
    $('grid').innerHTML = '<p class="hint" style="padding:10px">왼쪽 목록에서 표를 고르세요.</p>'; $('page').innerHTML = ''; renderCell();
    $('table-title').textContent = '표를 고르세요'; $('table-meta').textContent = '';
    loadOverview().catch(e => fail('테스트셋을 불러오지 못했습니다: ' + e.message));
  };
  $('set-name').textContent = overview.name;
  $('set-state').textContent = (overview.reviewed_by_human ? '사람 검토 완료' : '사람 검토 전 초안') + (overview.draft_by ? ' · 초안: ' + overview.draft_by : '');
  const s = overview.summary;
  $('metrics').innerHTML = [
    ['근거 사용 가능', pct(s.answerable), s.targets + '개 대상 값 셀'],
    ['경로 정확', pct(s.exact), '사용 가능 + 경로 일치'],
    ['틀린 확정', s.wrong_confirmed, '사용 가능인데 경로 틀림'],
    ['맞지만 보류', s.path_right_but_withheld, '경로 맞는데 빠짐'],
    ['역할 정확도', pct(s.role_accuracy), s.role_accuracy.n + '셀'],
    ['사람 확인', s.reviewed_by_human + ' / ' + s.cells, '셀 (Claude 확인 ' + (s.reviewed - s.reviewed_by_human) + ')'],
    ['담긴 표 맥락 실림', (() => { const n = overview.tables.filter(t => t.parents); return n.filter(t => t.parents.context_carried).length + ' / ' + n.length; })(), '칸 안에 담긴 표의 근거가 담은 칸 라벨을 함께 싣는가'],
  ].map(([k, v, h]) => `<div class="metric" title="${esc(h)}"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('');
  renderList();
  loadFailures();
}

const FAIL_KIND = {wrong_confirmed: '틀린 확정 (근거로 쓰이는데 경로가 다름)', withheld: '보류 (값인데 근거에서 빠짐)',
  false_value: '값이 아닌데 근거로 쓰임', role_wrong: '역할이 다름', container_missing: '내용을 담는 칸을 못 찾음',
  container_as_value: '담는 칸 원문이 값으로 쓰임', content_mismatch: '칸 안 항목(순서·구성)이 다름', cell_mismatch: '좌표의 원문이 다름(조각난 표 등)',
  parent_wrong: '담긴 위치가 다름', context_missing: '담긴 위치 맥락이 근거에 없음', table_missing: '표가 코퍼스에 없음'};

async function loadFailures() {
  let data;
  try { data = await api('/api/mapping/failures' + setQuery(true)); } catch (e) { $('failures').textContent = '불러오지 못했습니다: ' + e.message; return; }
  $('failures-count').textContent = `(${data.summary.total}건 · 표 ${data.summary.tables}개)`;
  const groups = {};
  for (const f of data.failures) (groups[f.kind] ||= []).push(f);
  const path = (r, c) => (r.join(' › ') || '–') + ' × ' + (c.join(' › ') || '–');
  $('failures').innerHTML = Object.entries(groups).map(([kind, list]) =>
    `<div class="fail-kind">${esc(FAIL_KIND[kind] || kind)} · ${list.length}</div>` + list.slice(0, 200).map(f =>
      `<button class="fail" data-doc="${esc(f.doc)}" data-table="${esc(f.table)}" data-cell="${f.r === null ? '' : f.r + ',' + f.c}"
        title="${f.r === null ? '' : esc('파싱: ' + path(f.parsed_row_path, f.parsed_column_path) + '\n기대: ' + path(f.expected_row_path, f.expected_column_path))}">
        ${esc(f.doc)} · ${esc(f.table.replace('/', ' · '))}${f.r === null ? '' : ` r${f.r}c${f.c}`} <small>${esc((f.text || '').replace(/\s+/g, ' ').slice(0, 24))}</small></button>`).join('')
      + (list.length > 200 ? `<p class="hint">… ${list.length - 200}건 더</p>` : '')).join('') || '<p class="hint">실패가 없습니다.</p>';
  document.querySelectorAll('#failures .fail').forEach(b => b.onclick = () => goTo(b.dataset.doc, b.dataset.table, b.dataset.cell || null));
}

function renderList() {
  const groups = {};
  for (const t of overview.tables) (groups[t.doc] ||= []).push(t);
  $('tables').innerHTML = Object.entries(groups).map(([doc, list]) => {
    const items = list.filter(t => filter === 'all'
      || (filter === 'fail' && t.summary && ((t.summary.exact.rate ?? 1) < 1 || t.summary.wrong_confirmed || t.summary.false_value))
      || (filter === 'todo' && t.reviewed_by_human < t.cells));
    if (!items.length) return '';
    return `<div class="doc-name">${esc(doc)}</div>` + items.map(t => {
      const s = t.summary;
      const exact = s && s.targets ? `<span class="badge ${s.exact.rate === 1 ? '' : s.exact.rate > 0 ? 'warn' : 'bad'}">정확 ${pct(s.exact)} · ${s.targets}셀</span>` : '<span class="badge gray">대상 값 없음</span>';
      const wrong = s && s.wrong_confirmed ? `<span class="badge bad">틀린 확정 ${s.wrong_confirmed}</span>` : '';
      const done = `<span class="badge ${t.reviewed_by_human === t.cells ? '' : 'gray'}">사람 확인 ${t.reviewed_by_human}/${t.cells}</span>`
        + (t.parents && !t.parents.context_carried ? '<span class="badge warn">담긴 위치 맥락 빠짐</span>' : '');
      const active = current && current.doc === t.doc && current.table === t.table ? ' active' : '';
      return `<button class="tbl${active}" data-doc="${esc(t.doc)}" data-table="${esc(t.table)}">
        <div class="t"><span>${esc(t.table.replace('/', ' · '))}</span></div>${exact}${wrong}${done}
        <div>${t.patterns.map(p => `<span class="badge gray">${esc(PATTERN[p] || p)}</span>`).join('')}</div></button>`;
    }).join('');
  }).join('');
  document.querySelectorAll('.tbl').forEach(b => b.onclick = () => openTable(b.dataset.doc, b.dataset.table));
}

async function openTable(doc, table) {
  if (dirty && !confirm('저장하지 않은 변경이 있습니다. 버리고 다른 표로 갈까요?')) return;
  fail('');
  try {
    current = await api(`/api/mapping/table?doc=${encodeURIComponent(doc)}&table=${encodeURIComponent(table)}` + setQuery(false));
  } catch (e) { fail('표를 불러오지 못했습니다: ' + e.message); return; }
  edits = Object.fromEntries(current.expected.map(c => [c.r + ',' + c.c, JSON.parse(JSON.stringify(c))]));
  selected = null; addMode = null; setDirty(false);
  $('table-title').textContent = `${doc} · ${table.replace('/', ' · ')}`;
  const panels = current.panels.map(p => `머리글 ${p.header_rows}행 · 라벨 ${p.stub_columns}열`).join(', ');
  $('table-meta').innerHTML = `${esc(current.source_name)} ${current.page}쪽 · ${current.rows}×${current.columns} · 분석: ${esc(panels)}`
    + (current.table_codes.length ? ` · <span class="badge warn">${current.table_codes.map(c => esc(CODE[c] || c)).join(' / ')}</span>` : '')
    + (current.note ? `<br>메모: ${esc(current.note)}` : '')
    + (current.parents.length ? `<br><span class="crumb">담긴 위치: ${current.parents.map(p => `<button data-goto="${esc(p.table.split('/').slice(-2).join('/'))}|${p.r},${p.c}">${esc(p.table.split('/').slice(-2).join(' · '))} r${p.r}c${p.c}${p.label.length ? ' [' + esc(p.label.join(' › ').slice(0, 40)) + ']' : ''}</button>`).join(' › ')} › 이 표</span>`
      + ` <span class="badge ${current.context_carried ? '' : 'warn'}">근거에 담은 칸 라벨 ${current.context_carried ? '실림' : '안 실림'}</span>`
      + (current.parent_check && current.parent_check.expected.length && !current.parent_check.correct ? ' <span class="badge bad">기대 담긴 위치와 다름</span>' : '') : '');
  document.querySelectorAll('[data-goto]').forEach(b => b.onclick = () => { const [t, k] = b.dataset.goto.split('|'); goTo(current.doc, t, k); });
  $('mark-all').disabled = false;
  renderGrid(); renderPage(); renderCell(); renderList();
  setHash();
}

async function goTo(doc, table, key) {
  await openTable(doc, table);
  if (current && current.table === table && key && current.cells.some(c => c.r + ',' + c.c === key)) {
    selected = key; renderGrid(); renderCell(); highlight(); setHash();
  }
}

// 주소 #doc=…&table=…&cell=r,c 로 표·셀을 바로 연다(검토 링크 공유용).
function setHash() {
  if (!current) return;
  const h = new URLSearchParams({doc: current.doc, table: current.table});
  if (setName) h.set('set', setName);
  if (selected) h.set('cell', selected);
  history.replaceState(null, '', '#' + h.toString());
}
async function openFromHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (!h.get('doc') || !h.get('table')) return;
  await openTable(h.get('doc'), h.get('table'));
  if (current && h.get('cell') && current.cells.some(c => c.r + ',' + c.c === h.get('cell'))) {
    selected = h.get('cell'); renderGrid(); renderCell(); highlight(); setHash();
  }
}

function renderGrid() {
  const cells = current.cells, at = {}, covered = new Set();
  for (const c of cells) {
    at[c.r + ',' + c.c] = c;
    for (let i = c.r; i < c.r + c.rs; i++) for (let j = c.c; j < c.c + c.cs; j++) if (i !== c.r || j !== c.c) covered.add(i + ',' + j);
  }
  let html = '<table class="grid">';
  for (let r = 0; r < current.rows; r++) {
    html += '<tr>';
    for (let c = 0; c < current.columns; c++) {
      const key = r + ',' + c;
      if (covered.has(key)) continue;
      const cell = at[key];
      if (!cell) { html += '<td class="s-ignore"></td>'; continue; }
      const exp = edits[key];
      const s = status(cell, exp);
      const cls = ['s-' + s, selected === key ? 'sel' : '', addMode && exp && LABELS.includes(exp.role) ? 'addable' : ''].join(' ');
      html += `<td class="${cls}" rowspan="${cell.rs}" colspan="${cell.cs}" data-key="${key}" title="${esc(cell.text)}">`
        + `<span class="rc">r${r}c${c}</span><div class="tx">${esc(cell.text.trim() || ' ')}</div>${exp && exp.reviewed ? (exp.reviewer === 'human' ? '<span class="ok">✓</span>' : '<span class="ok claude" title="Claude 확인(원본 대조)">C</span>') : ''}</td>`;
    }
    html += '</tr>';
  }
  $('grid').innerHTML = html + '</table>';
  document.querySelectorAll('td[data-key]').forEach(td => td.onclick = () => clickCell(td.dataset.key));
}

function clickCell(key) {
  if (addMode && selected && key !== selected) {
    const exp = edits[selected], cell = current.cells.find(c => c.r + ',' + c.c === key);
    if (exp && cell && cell.text.trim()) {
      (addMode === 'row' ? exp.row_path : exp.column_path).push(clean(cell.text));
      setDirty(true); renderGrid(); renderCell();
    }
    return;
  }
  selected = key; addMode = null;
  renderGrid(); renderCell(); highlight(); setHash();
}

function renderPage() {
  $('page-title').textContent = `원본 ${current.page}쪽`;
  const link = current.source_uri ? ` <a href="${esc(current.source_uri)}" target="_blank" rel="noopener noreferrer">원문 주소 열기</a>` : '';
  if (current.page_source === 'html' && !current.page_job) {
    // HTML 원문은 쪽·좌표가 없어 위치 표시 없이 문서 전체를 보여 준다(격리된 틀: 스크립트·외부 요청 없음).
    $('page-title').textContent = '원문(HTML)';
    $('page').innerHTML = `<p class="hint" style="padding:6px 10px;margin:0">HTML 원문 전체입니다. 표 위치 표시는 없으니 표의 첫 셀 글자로 찾으세요.${link}</p>`
      + `<iframe class="source-frame" sandbox title="원문" src="/api/mapping/source?doc=${encodeURIComponent(current.doc)}${setQuery(false)}"></iframe>`;
    return;
  }
  const image = current.page_job ? `/api/jobs/${encodeURIComponent(current.page_job)}/page/${current.page}`
    : current.page_source === 'pdf' ? `/api/mapping/page?doc=${encodeURIComponent(current.doc)}&number=${current.page}${setQuery(false)}` : null;
  if (!image || !current.page_size) {
    $('page').innerHTML = `<p class="hint" style="padding:10px">이 문서의 원본 파일을 찾지 못했습니다(화면에서 올린 작업도, data/ 아래의 같은 파일도 없음).${link}</p>`;
    return;
  }
  $('page').innerHTML = `<div class="sheet" id="sheet"><img id="page-img" alt="원본 ${current.page}쪽" src="${esc(image)}"><div class="highlight" id="highlight"></div></div>`;
  applyZoom();
  // 이미지가 뜬 뒤에야 위치를 계산할 수 있다. 뜨면 선택 셀(없으면 표 전체)로 옮긴다.
  $('page-img').onload = () => highlight();
}

function applyZoom() {
  const sheet = $('sheet');
  if (sheet) sheet.style.width = $('zoom').value + '%';
}

// 선택한 셀(없으면 표 전체 영역)을 원본에 표시하고 그 위치가 보이게 원본 칸을 스크롤한다.
function highlight() {
  const h = $('highlight'), pane = $('page');
  if (!h || !current || !current.page_size) return;
  const cell = current.cells.find(c => c.r + ',' + c.c === selected);
  const boxes = cell ? [cell.bbox] : current.cells.map(c => c.bbox);
  if (!boxes.length) return;
  const b = {x0: Math.min(...boxes.map(x => x.x0)), y0: Math.min(...boxes.map(x => x.y0)),
             x1: Math.max(...boxes.map(x => x.x1)), y1: Math.max(...boxes.map(x => x.y1))};
  const {width, height} = current.page_size;
  Object.assign(h.style, {display: 'block', left: b.x0 / width * 100 + '%', top: b.y0 / height * 100 + '%',
    width: (b.x1 - b.x0) / width * 100 + '%', height: (b.y1 - b.y0) / height * 100 + '%',
    borderStyle: cell ? 'solid' : 'dashed'});
  const img = $('page-img');
  if (!img || !img.complete) return;
  const sheet = h.parentElement;
  const top = b.y0 / height * sheet.offsetHeight, left = b.x0 / width * sheet.offsetWidth;
  const boxH = (b.y1 - b.y0) / height * sheet.offsetHeight;
  pane.scrollTo({top: Math.max(0, top - Math.max(40, (pane.clientHeight - boxH) / 2)),
                 left: Math.max(0, left - 40)});
}
$('zoom').onchange = () => { applyZoom(); highlight(); };

const arrow = p => p.length ? p.map(x => esc(clean(x))).join(' <span class="sep">→</span> ') : '<span class="hint">(없음)</span>';

function chips(list, kind) {
  return `<div class="path">${list.map((x, i) => `<span class="chip">${esc(x)}<button data-rm="${kind}" data-i="${i}" title="빼기">×</button></span>`).join('<span class="sep">→</span>')
    || '<span class="hint">비어 있음</span>'}</div>
    <div class="row"><button data-add="${kind}" class="${addMode === kind ? 'on' : ''}">${addMode === kind ? '추가 중… 표에서 라벨 셀을 누르세요 (다시 누르면 끝)' : '표에서 셀 눌러 추가'}</button>
    <button data-copy="${kind}">분석 결과 복사</button><button data-clear="${kind}">비우기</button></div>`;
}

function renderCell() {
  const box = $('cell');
  const cell = current && current.cells.find(c => c.r + ',' + c.c === selected);
  if (!cell) { box.innerHTML = '<h2>선택한 셀</h2><p class="hint">셀을 누르세요.</p>'; return; }
  const exp = edits[selected];
  const s = status(cell, exp);
  const verdict = {
    ok: ['ok', '근거로 쓰이고 경로도 기대와 같다.'], wrong: ['wrong', '근거로 쓰이지만 경로가 기대와 다르다(틀린 답을 확정할 위험).'],
    withheld: ['withheld', '경로는 기대와 같은데 근거에서 빠진다.'], miss: ['miss', '값인데 근거에서 빠지고 경로도 기대와 다르다.'],
    label: ['neutral', '라벨·머리글 셀이다(근거 값으로 쓰이지 않음).'], false: ['wrong', '라벨이어야 하는데 근거 값으로 쓰인다.'],
    ctx: ['neutral', '표 안에 라벨이 없는 이어진 내용이라 경로 채점에서 뺀다.'], ignore: ['neutral', '채점 제외 셀이다.'],
    container: ['neutral', '내용을 담는 칸이다. 칸 안의 문장·표가 하나의 내용이며, 안쪽 표는 그 내용 안의 문맥이다.'],
    dup: ['wrong', '내용을 담는 칸인데, 안쪽 표·문장을 이어 붙인 원문이 값 근거로도 쓰인다(중복).'],
  }[s];
  const reasons = cell.eligible ? '' : `<ul class="reasons">${cell.blockers.map(b => `<li>${esc(BLOCKER[b] || b)}</li>`).join('')}</ul>`;
  const codes = [...new Set([...cell.codes, ...current.table_codes])];
  box.innerHTML = `<h2>선택한 셀</h2>
    <dl><dt>위치</dt><dd>r${cell.r} c${cell.c} · 병합 ${cell.rs}행 × ${cell.cs}열</dd></dl>
    <div class="full">${esc(cell.text || '(빈 셀)')}</div>
    <div class="status ${verdict[0]}">${esc(verdict[1])}</div>
    <h3>분석 결과 (현재 구조 분석기)</h3>
    <dl><dt>역할</dt><dd>${esc(ROLE[cell.role] || cell.role)}${cell.role_state === 'unresolved' ? ' (미확정)' : ''}</dd>
      <dt>행의 의미 경로</dt><dd>${arrow(cell.row_path)}</dd>
      <dt>열의 의미 경로</dt><dd>${arrow(cell.column_path)}</dd>
      <dt>매핑 상태</dt><dd>${esc(cell.mapping_state || '매핑 없음')} · 물리 ${esc(cell.physical_state)}</dd>
      <dt>근거 사용</dt><dd>${cell.eligible ? '가능' : '불가'}${reasons}</dd>
      ${codes.length ? `<dt>모호 코드</dt><dd>${codes.map(c => `<span class="badge warn" title="${esc(CODE[c] || '')}">${esc(c)}</span>`).join('')}</dd>` : ''}
    </dl>
    ${cell.container ? contentBlock(cell, exp) : ''}
    <h3>기대값 (테스트셋)</h3>
    ${exp ? `
    <div class="row"><select id="role">${Object.entries(ROLE).filter(([k]) => k !== 'unknown').map(([k, v]) => `<option value="${k}"${exp.role === k ? ' selected' : ''}>${v}</option>`).join('')}</select></div>
    ${exp.role === 'value' ? `<h3>행의 의미 경로 (바깥 → 안쪽)</h3>${chips(exp.row_path, 'row')}<h3>열의 의미 경로 (위 → 아래)</h3>${chips(exp.column_path, 'column')}` : ''}
    <h3>메모</h3><textarea id="note" placeholder="왜 이렇게 판단했는지(선택)">${esc(exp.note || '')}</textarea>
    <div class="row"><label><input type="checkbox" id="reviewed"${exp.reviewed && exp.reviewer === 'human' ? ' checked' : ''}> 이 셀의 기대값을 내가 확인했다</label>${exp.reviewed && exp.reviewer === 'claude' ? ' <span class="badge gray">Claude 확인(원본 대조) · 사람 검토 전</span>' : ''}</div>
    <div class="row"><button id="accept">분석 결과를 기대값으로 + 확인</button><button id="confirm" class="primary">기대값 맞음 · 확인</button></div>
    ` : '<p class="hint">테스트셋에 없는 셀입니다(원문이 바뀌었을 수 있음).</p>'}`;
  if (!exp) return;
  $('role').onchange = e => { exp.role = e.target.value; if (exp.role !== 'value') { exp.row_path = []; exp.column_path = []; addMode = null; } change(); };
  $('note').oninput = e => { exp.note = e.target.value || null; setDirty(true); };
  $('reviewed').onchange = e => { exp.reviewed = e.target.checked; exp.reviewer = e.target.checked ? 'human' : null; change(); };
  $('confirm').onclick = () => { exp.reviewed = true; exp.reviewer = 'human'; addMode = null; change(); };
  $('accept').onclick = () => {
    exp.role = cell.container ? 'container' : cell.role === 'unknown' ? exp.role : cell.role;
    exp.row_path = exp.role === 'value' ? cell.row_path.map(clean) : [];
    exp.column_path = exp.role === 'value' ? cell.column_path.map(clean) : [];
    exp.reviewed = true; exp.reviewer = 'human'; addMode = null; change();
  };
  box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => goTo(current.doc, b.dataset.open, null));
  box.querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { pathOf(exp, b.dataset.rm).splice(+b.dataset.i, 1); change(); });
  box.querySelectorAll('[data-add]').forEach(b => b.onclick = () => { addMode = addMode === b.dataset.add ? null : b.dataset.add; renderGrid(); renderCell(); });
  box.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => {
    const src = b.dataset.copy === 'row' ? cell.row_path : cell.column_path;
    pathOf(exp, b.dataset.copy).splice(0, Infinity, ...src.map(clean)); change();
  });
  box.querySelectorAll('[data-clear]').forEach(b => b.onclick = () => { pathOf(exp, b.dataset.clear).length = 0; change(); });
}

// 담는 칸 안의 내용: 원본 순서의 문장·표. 표는 눌러서 연다. 기대 항목(테스트셋)과 같은지도 보여 준다.
function contentBlock(cell, exp) {
  const items = cell.container.items;
  const short = t => t.split('/').slice(-2).join('/');
  const same = exp && exp.role === 'container' && exp.content.length === items.length && items.every((it, i) => {
    const e = exp.content[i];
    return it.kind === 'table' ? e.kind === 'table' && e.table === short(it.table)
      : it.kind === 'text' ? e.kind === 'text' && e.text === compact(it.text).slice(0, 30) : e.kind === 'other';
  });
  const list = items.map(it => it.kind === 'table'
    ? `<li class="tbl-item">표 <button data-open="${esc(short(it.table))}">${esc(short(it.table).replace('/', ' · '))} 열기</button></li>`
    : it.kind === 'text' ? `<li>${esc(it.text)}</li>` : `<li class="hint">(연결되지 않은 원본 표 조각)</li>`).join('');
  return `<h3>이 칸의 내용 (원본 순서 · 파서 기하 연결, 의미 검증 전)</h3>
    <p class="hint">연결 상태 ${esc(cell.container.status)} · 읽기 순서 ${esc(cell.container.reading_order)}${exp && exp.role === 'container' ? ' · 기대 항목과 ' + (same ? '일치' : '<b>다름</b>') : ''}</p>
    <ul class="items">${list}</ul>`;
}

const pathOf = (exp, kind) => kind === 'row' ? exp.row_path : exp.column_path;
function change() { setDirty(true); renderGrid(); renderCell(); }
function setDirty(v) { dirty = v; $('dirty').textContent = v ? '저장하지 않은 변경 있음' : '변경 없음'; $('save').disabled = !v; }

$('save').onclick = async () => {
  if (!current) return;
  $('save').disabled = true;
  try {
    await api('/api/mapping/table', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Demo-Token': window.token},
      body: JSON.stringify({doc: current.doc, table: current.table, note: current.note, cells: Object.values(edits),
                            ...(setName ? {set: setName} : {})})});
    setDirty(false);
    const {doc, table} = current, keep = selected;
    await loadOverview();
    await openTable(doc, table);
    selected = keep; renderGrid(); renderCell(); highlight();
  } catch (e) { fail('저장하지 못했습니다: ' + e.message); $('save').disabled = false; }
};
$('mark-all').onclick = () => {
  if (!current || !confirm('이 표의 모든 셀 기대값을 확인한 것으로 표시할까요? (저장해야 반영됩니다)')) return;
  Object.values(edits).forEach(e => { e.reviewed = true; e.reviewer = 'human'; });
  change();
};
document.querySelectorAll('#filters button').forEach(b => b.onclick = () => {
  filter = b.dataset.f;
  document.querySelectorAll('#filters button').forEach(x => x.classList.toggle('on', x === b));
  renderList();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && addMode) { addMode = null; renderGrid(); renderCell(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty) $('save').click(); }
});
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
loadOverview().then(openFromHash).catch(e => fail('테스트셋을 불러오지 못했습니다: ' + e.message));
