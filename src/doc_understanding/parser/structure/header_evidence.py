# 역할: 첫 행을 헤더 후보로 볼 수 있는 원문 근거를 검사하며 특정 문서 정답을 사용하지 않는다.
"""Narrow source-supported first-row hypothesis. No document names or answer keys.

Needs both a distinct source appearance band and a label/body contrast. It does
not repair geometry, infer multi-row headers, certify semantics, or clear other
ambiguities. Drawing fill presence is used, not an assumed fill colour.
"""
import json
import re
from collections import Counter
from doc_understanding.domain.models import Issue


# 비교용 문자열에서 공백만 제거한다. 원본 라벨 자체는 변경하지 않는다.
def _norm(text):
    return re.sub(r'\s+', '', text or '')


# 좌표 상자의 음수 너비·높이를 0으로 제한해 면적을 계산한다.
def _area(box):
    return max(0, box.x1-box.x0)*max(0, box.y1-box.y0)


# 두 좌표 상자의 교집합 면적을 계산해 원문 소유와 채움 근거의 범위를 판단한다.
def _overlap(a, b):
    return max(0,min(a.x1,b.x1)-max(a.x0,b.x0))*max(0,min(a.y1,b.y1)-max(a.y0,b.y0))


# 한 행 헤더·한 열 라벨 가설에 한해 글꼴/채움 차이와 본문 대비를 함께 확인한다.
# 근거가 충분하면 출처를 담은 Issue를, 부족하거나 격자가 불완전하면 None을 반환한다.
def source_header_evidence(table, page, panel):
    if (page is None or page.page_number != table.page_number
            or not page.page_id.startswith(table.document_id+'/')
            or panel.header_rows != 1 or panel.stub_columns != 1):
        return None
    start,end=panel.column_range
    cells=[c for c in table.cells if c.occupied_slots
           and all(start<=col<end for _,col in c.occupied_slots)]
    slots={}
    for c in cells:
        if c.status=='unresolved' or not _norm(c.original_text):return None
        rr={r for r,_ in c.occupied_slots};cc={col for _,col in c.occupied_slots}
        if len(rr)*len(cc)!=len(c.occupied_slots):return None
        for slot in c.occupied_slots:
            if slot in slots:return None
            slots[slot]=c
    if set(slots)!={(r,c) for r in range(len(table.rows)) for c in range(start,end)}:
        return None
    heads=[c for c in cells if c.row_index==0]
    body=[c for c in cells if c.row_index is not None and c.row_index>0]
    values=[c for c in body if c.column_index is not None and c.column_index>start]
    if (len(heads)!=end-start or any(c.rowspan!=1 or c.colspan!=1 for c in heads)
            or len(values)<2):return None
    labels=[_norm(c.original_text) for c in heads]
    # Reject purely numerical first rows, long prose and repeated-column ambiguity.
    if (len(set(labels))!=len(labels) or any(len(t)>40 or not re.search(r'[A-Za-z가-힣]',t) for t in labels)):
        return None
    numeric=all(re.search(r'\d',c.original_text or '') for c in values)
    long_count=sum(len(_norm(c.original_text))>=max(12,2*max(map(len,labels))) for c in values)
    narrative=long_count>=2 and long_count>=len(values)/2
    if not (numeric or narrative):return None
    spans={s.span_id:s for b in page.blocks for s in b.spans}
    used=[]
    # 동일 문서·페이지에 속하고 셀 안에 충분히 포함된 비어 있지 않은 원문 span을 찾는다.
    def owned(c):
        found=[spans[s] for s in c.span_refs if s in spans and spans[s].text_raw.strip()
               and spans[s].source.document_id==table.document_id
               and spans[s].source.page_number==table.page_number
               and _area(spans[s].source.bbox)>0
               and _overlap(spans[s].source.bbox,c.source_bbox)>=.85*_area(spans[s].source.bbox)]
        return found
    hs=[owned(c) for c in heads];bs=[owned(c) for c in values]
    if any(not x for x in hs+bs):return None
    # PDF object order may differ from reading order, and glyphs may be overprinted.
    # Check character coverage only; never rewrite or reorder the original label.
    if any(Counter(_norm(c.original_text))-Counter(_norm(''.join(s.text_raw for s in ss))) for c,ss in zip(heads,hs)):
        return None
    # 셀을 충분히 덮는 직사각형 채움만 근거로 수집한다. 임의 도형의 외접 상자는 제외한다.
    def fills(c):
        # Only rectangular fill geometry; arbitrary paths' bounding boxes are not evidence.
        out=[]
        for b in page.blocks:
            if b.kind!='drawing' or b.drawing_stroked is not False:continue
            commands=b.drawing_commands;bb=b.source.bbox
            rectangular=(len(commands)==1 and commands[0].operator=='re')
            if len(commands)==4 and all(x.operator=='l' and len(x.coordinates)==4 for x in commands):
                corners={(bb.x0,bb.y0),(bb.x1,bb.y0),(bb.x0,bb.y1),(bb.x1,bb.y1)}
                edges=[(tuple(x.coordinates[:2]),tuple(x.coordinates[2:])) for x in commands]
                rectangular=(all(a in corners and z in corners and a!=z and (a[0]==z[0] or a[1]==z[1]) for a,z in edges)
                             and len({frozenset(e) for e in edges})==4)
            if (rectangular and b.source.document_id==table.document_id
                    and b.source.page_number==table.page_number
                    and _area(bb)<=1.5*_area(c.source_bbox)
                    and _overlap(bb,c.source_bbox)>=.85*_area(c.source_bbox)):
                out.append(b)
        return out
    hf=[fills(c) for c in heads];bf=[fills(c) for c in values]
    fill_band=all(hf) and not any(bf)
    bold_band=all(all(s.flags & 16 for s in ss) for ss in hs) and all(not any(s.flags & 16 for s in ss) for ss in bs)
    if not (fill_band or bold_band):return None
    used=[c.source for c in heads+values]
    used += [s.source for ss in hs+bs for s in ss]
    if fill_band:used += [b.source for bb in hf for b in bb]
    reason=dict(rule='source-header/1',layout='one_header_row_one_stub_column',
                appearance=[x for x,yes in [('fill_band',fill_band),('bold_band',bold_band)] if yes],
                body_contrast='numeric_tokens' if numeric else 'long_prose',
                source_page_id=page.page_id,header_cell_ids=[c.cell_id for c in heads],
                body_cell_ids=[c.cell_id for c in values],
                meaning='supported layout hypothesis, not human-validated semantics')
    return Issue(code='SOURCE_HEADER_SUPPORTED',severity='info',message=json.dumps(reason,ensure_ascii=False),source_references=tuple(used))


def type_contrast_header_evidence(table, page, panel):
    """Unstyled one-row header supported by repeated, source-backed column types.

    At least three body rows and two numeric/date columns are needed. A missing
    span, blank/duplicate/numeric header, mixed column or uncertain grid cannot
    become a heading merely because it occupies the first row.
    """
    from doc_understanding.parser.structure.trees import physical_state
    if (page is None or page.page_number!=table.page_number
            or not page.page_id.startswith(table.document_id+'/')
            or panel.header_rows!=1 or panel.stub_columns!=1
            or len(table.rows)<4 or panel.column_range!=(0,len(table.columns)) or len(table.columns)<3):
        return None
    cells=table.cells
    if (len(cells)!=len(table.rows)*len(table.columns)
            or any(c.rowspan!=1 or c.colspan!=1 or physical_state(c,table)=='unresolved'
                   or c.occupied_slots!=((c.row_index,c.column_index),) for c in cells)
            or {(c.row_index,c.column_index) for c in cells}!={(r,c) for r in range(len(table.rows)) for c in range(len(table.columns))}):
        return None
    heads=sorted((c for c in cells if c.row_index==0),key=lambda c:c.column_index)
    labels=[_norm(c.original_text) for c in heads]
    if (len(set(labels))!=len(labels) or any(not t or len(t)>60 or not any(ch.isalpha() for ch in t)
                                             for t in labels)):
        return None
    dated = [i for i, label in enumerate(labels) if any(ch.isdigit() for ch in label)]
    if dated:
        # Explicit, consecutive year headings can contain digits. A bare number
        # or a date embedded in an ordinary data row is insufficient.
        matches = [re.fullmatch(r'(?:(?:FY|year)((?:19|20)\d{2})|((?:19|20)\d{2})년)(?:\([A-Z]\))?',
                                labels[i], re.I) for i in dated]
        if (0 in dated or len(dated)<2 or dated != list(range(dated[0],dated[-1]+1))
                or not all(matches)):
            return None
        years = [int(m[1] or m[2]) for m in matches]
        steps = {b-a for a,b in zip(years,years[1:])}
        if steps not in ({1},{-1}):
            return None
    def numeric(text):
        value = _norm(text)
        if dated:
            value = value.removeprefix('△')
        return bool(re.fullmatch(r'[\d.,+\-−/%():]*\d[\d.,+\-−/%():]*', value))
    columns=[];body=[]
    for col in range(1,len(table.columns)):
        samples=[c for c in cells if c.column_index==col and c.row_index>0 and _norm(c.original_text)]
        if len(samples)>=3 and all(numeric(c.original_text) for c in samples):
            columns.append(col);body.extend(samples)
    if len(columns)<2 or 2*len(columns)<len(table.columns)-1:
        return None
    if dated:
        stubs = [c for c in cells if c.row_index and c.column_index==0]
        if len(columns)!=len(table.columns)-1 or any(not any(ch.isalpha() for ch in c.original_text or '')
                                                     for c in stubs):
            return None
        body.extend(stubs)
    spans={s.span_id:s for b in page.blocks for s in b.spans}
    used=[]
    for c in heads+body:
        owned=[spans[s] for s in c.span_refs if s in spans
               and spans[s].source.document_id==table.document_id and spans[s].source.page_number==table.page_number
               and _area(spans[s].source.bbox)>0 and _overlap(spans[s].source.bbox,c.source_bbox)>=.85*_area(spans[s].source.bbox)]
        if not owned or Counter(_norm(c.original_text))-Counter(_norm(''.join(s.text_raw for s in owned))):
            return None
        used.append(c.source);used.extend(s.source for s in owned)
    reason=dict(rule='source-header/consecutive-years-1' if dated else 'source-header/type-contrast-1',header_cell_ids=[c.cell_id for c in heads],
                body_cell_ids=[c.cell_id for c in body],numeric_or_date_columns=columns,
                source_page_id=page.page_id,meaning='supported layout hypothesis, not human-validated semantics')
    return Issue(code='SOURCE_HEADER_TYPE_CONTRAST',severity='info',message=json.dumps(reason,ensure_ascii=False),source_references=tuple(used))
