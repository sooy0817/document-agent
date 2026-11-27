// 역할: 근거 한 건에서 화면에 필요한 것(종류 이름, 그 쪽의 좌표)을 읽는다.
export const kindOf = e => e.cell_id && e.table_id ? (e.nested_context ? '표 안의 표 셀' : '표 셀') : e.value_kind === 'cell_text' ? '표 칸 안 문장' : '본문';

// 그 쪽에 있는 근거 영역들(근거의 대표 영역과, 여러 줄에 걸친 본문의 나머지 영역).
export function boxesOf(e, page) {
  const main = e.source?.bbox && (e.source.page_number ?? e.page) === page ? [e.source.bbox] : [];
  const more = (e.source_references || []).filter(r => r.bbox && r.page_number === page).map(r => r.bbox);
  return [...main, ...more];
}
