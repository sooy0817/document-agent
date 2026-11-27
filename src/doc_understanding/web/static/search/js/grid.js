// 역할: 표 격자를 그린다. 인용 칸과 같은 행·열을 표시하고, 큰 표는 인용 칸 주변과 머리글만 보여 준다.
import {h, one} from './dom.js';

const range = (from, to) => { const out = []; for (let i = from; i <= to; i++) out.push(i); return out; };
export const isLarge = grid => grid.rows > 16 || grid.columns > 10;

// 반환: {box: 격자 요소, partial: 일부만 그렸는가, found: 인용 칸이 표에 있는가}. onPick(칸): 칸을 눌렀을 때.
export function renderGrid(grid, citedId, full, onPick) {
  const cited = grid.cells.find(c => c.id === citedId);
  const covers = (c, axis, index) => axis === 'r' ? c.r <= index && index < c.r + c.rs : c.c <= index && index < c.c + c.cs;
  let rows = range(0, grid.rows - 1), cols = range(0, grid.columns - 1);
  const partial = !full && !!cited && isLarge(grid);
  if (partial) {
    if (grid.rows > 16) {
      const head = rows.filter(r => grid.cells.some(c => ['column_header', 'corner'].includes(c.role) && covers(c, 'r', r))).slice(0, 6);
      rows = [...new Set([...head, ...range(Math.max(0, cited.r - 3), Math.min(grid.rows - 1, cited.r + cited.rs + 2))])].sort((a, b) => a - b);
    }
    if (grid.columns > 10) {
      const head = cols.filter(k => grid.cells.some(c => ['row_label', 'corner'].includes(c.role) && covers(c, 'c', k))).slice(0, 4);
      cols = [...new Set([...head, ...range(Math.max(0, cited.c - 2), Math.min(grid.columns - 1, cited.c + cited.cs + 1))])].sort((a, b) => a - b);
    }
  }
  // 건너뛴 행·열 자리에는 '생략' 줄을 둔다. 병합 칸은 보이는 줄에 걸친 만큼만 차지한다.
  const tracks = list => { const out = []; list.forEach((v, i) => { if (i && v - list[i - 1] > 1) out.push({gap: v - list[i - 1] - 1}); out.push({index: v}); }); return out; };
  const rowTracks = tracks(rows), colTracks = tracks(cols);
  const span = (list, start, size) => { const hit = list.map((t, i) => t.index !== undefined && t.index >= start && t.index < start + size ? i : -1).filter(i => i >= 0); return hit.length ? [hit[0] + 1, hit[hit.length - 1] + 2] : null; };
  const box = h('div', {class: 'grid', role: 'table', 'aria-label': '인용 근거가 있는 표', style: `grid-template-columns:repeat(${colTracks.length},minmax(64px,max-content))`});
  rowTracks.forEach((t, i) => { if (t.gap) box.append(h('div', {class: 'gcell gap', style: `grid-row:${i + 1};grid-column:1/${colTracks.length + 1}`}, t.gap + '행 생략')); });
  colTracks.forEach((t, i) => { if (t.gap) rowTracks.forEach((r, j) => { if (!r.gap) box.append(h('div', {class: 'gcell gap', style: `grid-row:${j + 1};grid-column:${i + 1}`, title: t.gap + '열 생략'}, '⋯')); }); });
  for (const c of grid.cells) {
    const r = span(rowTracks, c.r, c.rs), k = span(colTracks, c.c, c.cs);
    if (!r || !k) continue;
    const sameLine = cited && c !== cited && ((c.r < cited.r + cited.rs && cited.r < c.r + c.rs) || (c.c < cited.c + cited.cs && cited.c < c.c + c.cs));
    const path = c.role === 'value' ? [c.row_path?.length && '행: ' + c.row_path.map(one).join(' → '), c.column_path?.length && '열: ' + c.column_path.map(one).join(' → '), !c.eligible && '경로 미확정'].filter(Boolean).join('\n') : '';
    box.append(h('button', {class: 'gcell' + (c.role !== 'value' ? ' label' : '') + (c === cited ? ' cited' : sameLine ? ' line' : ''), style: `grid-row:${r[0]}/${r[1]};grid-column:${k[0]}/${k[1]}`, title: path || undefined,
      'aria-current': c === cited ? 'true' : undefined, onclick: () => onPick(c)}, c.text || ' '));
  }
  return {box, partial, found: !!cited};
}
