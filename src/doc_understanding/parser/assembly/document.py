# 파일 역할: 본문·표·섹션·주석·행 경로 후보를 CanonicalDocument로 묶고 출처를 검사한다.
# 읽는 순서: build_from_run(저장 결과 읽기) → assemble(조립) → validate_document(검증).
# assemble은 numbered_view(번호 기반 행 후보)와 _lines/_inside(본문 배치)를 사용한다.
# 원본 파일을 덮어쓰지 않으며 조립 결과도 사람 검증 전 후보로 남긴다.
"""Complete the existing CanonicalDocument draft without mutating source artifacts."""
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from doc_understanding.domain.models import ArtifactRef, BBox, DocumentIR, Issue
from doc_understanding.domain.pipeline_contracts import CanonicalDocument, Content, Section
from doc_understanding.domain.text_markers import number_marker, roles
from doc_understanding.parser.contracts import ContextLink
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import path_for
from doc_understanding.parser.structure.models import StructuredTableIR


# 파일 바이트의 SHA256을 반환한다. 원본 동일성 확인과 산출물 참조 기록에 사용한다.
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# 같은 패널·기존 부모·라벨 열 범위 안에서 번호 체계를 따라 행 부모 후보를 보완한다.
# 입력 구조를 수정하지 않고, 보완한 행 트리·값 셀 경로·진단을 가진 복사본을 반환한다.
# 배치 근거와 충돌하는 부모는 채택하지 않으며, 번호 순서도 의미 정답으로 승인하지 않는다.
def numbered_view(table, physical):
    """Number-family sequence within the SAME physical group/stub. Never a Gold Tree."""
    groups = defaultdict(list)
    for n in table.row_tree:
        groups[(n.panel_id, n.parent_id, n.column_range)].append(n)
    updates, issues = {}, []
    cells = {c.cell_id: c for c in physical.cells}
    for nodes in groups.values():
        stack = []
        for n in sorted(nodes, key=lambda x: (x.row_range, x.node_id)):
            marker = number_marker(n.label_raw or '')
            if not marker or 'total_candidate' in roles(n.label_raw or ''):
                stack = []
                continue
            families = [m.family for _, m in stack]
            if marker.family in families:
                stack = stack[:families.index(marker.family)]
            if stack:
                parent, _ = stack[-1]
                # Competing geometric parents or backwards indentation are abstentions.
                if (not n.candidate_parent_ids and n.bbox.x0 + 3 >= parent.bbox.x0
                        and n.row_range[0] >= parent.row_range[1]):
                    updates[n.node_id] = n.model_copy(update={
                        'parent_id': parent.node_id, 'evidence_state': 'inferred'})
                    issues.append(Issue(
                        code='NUMBER_SEQUENCE_PARENT_CANDIDATE', severity='info',
                        message='Derived numbering-family parent; original geometry retained in source artifact; not semantic approval.',
                        source_references=(cells[parent.source_cell_id].source, cells[n.source_cell_id].source)))
            stack.append((n, marker))
    nodes = tuple(updates.get(n.node_id, n) for n in table.row_tree)
    index = {n.node_id: n for n in nodes}
    mappings = []
    for m in table.cell_mappings:
        if m.row_path:
            leaf = index[m.row_path[-1]]
            m = m.model_copy(update={'row_path': path_for(leaf, nodes)})
        mappings.append(m)
    return table.model_copy(update={'row_tree': nodes, 'cell_mappings': tuple(mappings),
                                    'issues': table.issues + tuple(issues)})


# 영역 a의 중심이 표 영역 b 안에 있는지 검사한다. 완전 포함 여부를 검사하는 함수는 아니다.
# 본문과 표의 중복 표현 후보를 표시할 때만 쓰며, 주석의 의미상 적용 범위를 확정하지 않는다.
def _inside(a, b):
    # Center/containment is a duplicate REPRESENTATION hint, not semantic scope.
    return b.x0 <= (a.x0+a.x1)/2 <= b.x1 and b.y0 <= (a.y0+a.y1)/2 <= b.y1


# 텍스트 블록의 span을 원본 line_index별로 묶어 (문자열, 출처 목록, 합친 좌표)를 반환한다.
# span이 없는 블록은 블록 자체의 텍스트·출처·좌표를 한 줄로 보존한다.
def _lines(block):
    groups = defaultdict(list)
    for span in block.spans:
        groups[span.line_index].append(span)
    if not groups:
        return [(block.text_raw or '', (block.source,), block.source.bbox)]
    result = []
    for _, spans in sorted(groups.items()):
        box = BBox(x0=min(s.source.bbox.x0 for s in spans), y0=min(s.source.bbox.y0 for s in spans),
                   x1=max(s.source.bbox.x1 for s in spans), y1=max(s.source.bbox.y1 for s in spans))
        result.append((''.join(s.text_raw for s in spans), tuple(s.source for s in spans), box))
    return result


# DocumentIR과 대응하는 물리·구조 표 목록을 받아 문서 전체의 후보 표현을 반환한다.
# 행 번호 보정 → 본문/표 읽기 순서 → 섹션 → 주변 문맥/주석 → 논리 표 연결 순으로 조립한다.
# 원본 IR을 함께 보존하고 마지막에 참조 일관성을 검사한다. 파일·DB 저장이나 모델 호출은 없다.
def assemble(ir, physical, structured, artifact_refs=(), source_uri=None):
    from doc_understanding.parser.relations.logical_tables import build_logical_links
    if len(physical) != len(structured):
        raise ValueError('TABLE_ARTIFACT_COUNT_MISMATCH')
    raw_index = {t.table_id: t for t in structured}
    tables = tuple(numbered_view(raw_index[t.table_id], t) for t in physical)
    contents, section_specs, sequence = [], [], []
    for page in ir.pages:
        local_tables = [t for t in physical if t.page_number == page.page_number]
        for t in local_tables:
            c = Content(content_id=t.table_id + '/content', section_id=None, kind='table',
                        text_raw=None, source_references=t.source_references, table_id=t.table_id)
            sequence.append(((page.page_number, t.bounding_box.y0, t.bounding_box.x0, 0), c))
        for b in page.blocks:
            if b.kind != 'text':
                continue  # images/drawings remain losslessly in document_ir
            for i, (text, refs, box) in enumerate(_lines(b)):
                duplicates = tuple(t.table_id for t in local_tables if _inside(box, t.bounding_box))
                kind = 'note' if re.match(r'^\s*(?:주\s*[)）:.]|※|Note\b|Source\s*:|자료\s*:)', text, re.I) else 'paragraph'
                c = Content(content_id=b.block_id + f'/line{i}', section_id=None, kind=kind,
                            text_raw=text, source_references=refs, duplicate_of_table_ids=duplicates)
                sequence.append(((page.page_number, box.y0, box.x0, 1), c))
    source_blocks = {b.block_id:b for p in ir.pages for b in p.blocks}
    stack, current = [], None
    for order, (_, c) in enumerate(sorted(sequence, key=lambda x: (x[0], x[1].content_id))):
        text = c.text_raw or ''
        marker = number_marker(text)
        chapter = re.match(r'^\s*제\s*\d+\s*([장절])', text)
        family = ('chapter' if chapter.group(1) == '장' else 'section') if chapter else (marker.family if marker else None)
        method = source_blocks[c.source_references[0].block_id].extraction_method if c.source_references and c.source_references[0].block_id in source_blocks else ''
        html_heading = re.search(r'_dom_h([1-6])_', method)
        if html_heading:
            level = int(html_heading.group(1))
            stack = [(f,s) for f,s in stack if f.startswith('html_h') and int(f[-1]) < level]
            family = f'html_h{level}'
        date_line = re.match(r'^\s*\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}', text)
        form_revision = re.fullmatch(r"\s*\d+(?:-\d+){2,}\s*\(\d+-\d+\)\s*\(\d{4}[./]\d{1,2}\s*개정\)\s*", text)
        if family and not form_revision and not date_line and len(text) < 100 and not c.duplicate_of_table_ids and c.kind == 'paragraph':
            families = [f for f, _ in stack]
            if family in families:
                stack = stack[:families.index(family)]
            sid = c.content_id + '/section'
            section_specs.append(dict(section_id=sid, parent_id=stack[-1][1] if stack else None,
                                      title_raw=text, source_references=c.source_references,
                                      reading_order=order))
            stack.append((family, sid))
            current = sid
        c = c.model_copy(update={'reading_order': order, 'section_id': current})
        contents.append(c)
    from doc_understanding.parser.assembly.local_headings import recover_unassigned_headings
    contents, added = recover_unassigned_headings(contents)
    section_specs.extend(added)
    section_specs.sort(key=lambda s: s["reading_order"])
    sections = tuple(Section(**s, content_ids=tuple(c.content_id for c in contents
                                                   if c.section_id == s['section_id']))
                     for s in section_specs)
    links = []
    context_ids = {}
    for c in contents:
        if c.kind != 'table':
            continue
        page_number = next(t.page_number for t in physical if t.table_id == c.table_id)
        nearby = [x for x in contents if x.kind != 'table' and not x.duplicate_of_table_ids
                  and x.source_references[0].page_number == page_number
                  and x.section_id == c.section_id]
        before = [x for x in nearby if x.reading_order < c.reading_order][-3:]
        after = [x for x in nearby if x.reading_order > c.reading_order][:2]
        attached = before + after
        context_ids[c.content_id] = tuple(x.content_id for x in attached)
        st = next(t for t in tables if t.table_id == c.table_id)
        for x in attached:
            text = x.text_raw or ''
            relation = 'note' if x.kind == 'note' else 'unit' if re.search(r'단\s*위\s*[:：]|\bunit\s*:', text, re.I) else 'caption'
            target, scope = c.table_id, 'table'
            if relation == 'note':
                remainder = re.sub(r'^\s*(?:주\s*[)）:.]|※|Note\s*:?)\s*', '', text, flags=re.I)
                note_marker = number_marker(remainder)
                matches = [n for n in st.row_tree if note_marker and number_marker(n.label_raw or '')
                           and number_marker(n.label_raw or '').family == note_marker.family
                           and number_marker(n.label_raw or '').token == note_marker.token
                           and re.search(r'주\s*[)）]?', n.label_raw or '')]
                if len(matches) == 1:
                    target, scope = matches[0].node_id, 'row'
                else:
                    target, scope = None, 'unknown'
            links.append(ContextLink(source=x.source_references[0], target_id=target, scope=scope,
                                     relation=relation, status='ambiguous'))
    contents = tuple(c.model_copy(update={'context_content_ids': context_ids.get(c.content_id, ())})
                     for c in contents)
    issues = [Issue(code='UNVALIDATED_DOCUMENT_ASSEMBLY', severity='warning', message=
                    'Reading order, section membership, numbering parents and context scopes are candidates; no approved financial facts.')]
    for page in ir.pages:
        body = [c for c in contents if c.source_references[0].page_number == page.page_number
                and not c.duplicate_of_table_ids and c.kind != 'table']
        # Side-by-side lines can imply multi-column reading order; retain uncertainty.
        if any(abs(a.source_references[0].bbox.y0-b.source_references[0].bbox.y0) < 3
               and abs(a.source_references[0].bbox.x0-b.source_references[0].bbox.x0) > page.width*.25
               for i, a in enumerate(body) for b in body[i+1:]):
            issues.append(Issue(code='MULTICOLUMN_ORDER_UNRESOLVED', severity='warning',
                                message=f'Page {page.page_number}: geometric sequence is not verified reading order.'))
    from doc_understanding.parser.relations.header_inheritance import inherit
    tables, logical_links = inherit(ir, physical, tables, build_logical_links(ir, physical))
    result = CanonicalDocument(
        document_id=ir.document_id, institution=ir.metadata.institution,
        reporting_period=ir.metadata.reporting_period, sections=sections, contents=contents,
        table_structure=tables, column_hierarchy=tuple(n for t in tables for n in t.column_tree),
        row_hierarchy=tuple(n for t in tables for n in t.row_tree),
        cell_mappings=tuple(m for t in tables for m in t.cell_mappings),
        notes=tuple(c for c in contents if c.kind == 'note' and not c.duplicate_of_table_ids),
        source_references=tuple(t.source_references[0] for t in physical), template_match=(),
        validation_status='review_required', issues=tuple(issues), document_ir=ir,
        physical_tables=tuple(physical), context_links=tuple(links),
        reading_order_status='unresolved' if len(issues) > 1 else 'inferred',
        source_artifact_refs=tuple(artifact_refs), source_uri=source_uri,
        logical_table_links=logical_links)
    from doc_understanding.parser.assembly.continued_sections import coalesce_reprints
    result = coalesce_reprints(result)
    validate_document(result)
    return result


# 추출 실행 폴더의 DocumentIR·물리 표·구조 표 JSON을 읽고 원본 해시를 확인한 뒤 조립한다.
# PDF 표에 값 매핑이 없으면 기존 text_grid 보완 후보를 만들고 의미 분석을 다시 수행할 수 있다.
# 그 후보는 반환 문서에만 사용하며 저장된 IR 파일은 변경하지 않는다.
# 보완 구현은 parser/structure/text_grid.py에 있으며, 이 조립 단계는 RAG를 호출하지 않는다.
def build_from_run(run):
    run = Path(run)
    ir = DocumentIR.model_validate_json((run / 'document-ir.json').read_bytes())
    physical, structured, refs = [], [], []
    paths = sorted((run / 'physical').glob('page-*-table-*.json'))
    paths = [p for p in paths if not p.stem.endswith('-structured')]
    for p in paths:
        s = p.with_name(p.stem + '-structured.json')
        physical.append(PhysicalTableIR.model_validate_json(p.read_bytes()))
        structured.append(StructuredTableIR.model_validate_json(s.read_bytes()))
        refs.extend(ArtifactRef(path=x.relative_to(run).as_posix(), sha256=sha(x),
                                media_type='application/json') for x in (p, s))
    from doc_understanding.parser.structure.text_grid import aligned_two_column
    from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
    for i,(pt,st) in enumerate(zip(physical,structured,strict=True)):
        if not st.cell_mappings and ir.source.media_type=='application/pdf':
            fallback=aligned_two_column(ir.pages[pt.page_number-1],pt)
            if fallback:
                candidate,header_count=fallback
                physical[i]=candidate
                structured[i]=SemanticTableAnalyzer().analyze(candidate,header_rows=header_count,stub_columns=1,source_page=ir.pages[pt.page_number-1])
    refs.append(ArtifactRef(path='document-ir.json', sha256=sha(run / 'document-ir.json'),
                            media_type='application/json'))
    if sha(run / ir.source.path) != ir.source_sha256:
        raise ValueError('SOURCE_HASH_MISMATCH')
    origin_path = run/'source-origin.json'
    source_uri = None
    if origin_path.exists():
        origin = json.loads(origin_path.read_text(encoding='utf8'))
        if origin['sha256'] != ir.source_sha256 or not re.match(r'^https?://',origin['url']):
            raise ValueError('SOURCE_ORIGIN_MISMATCH')
        source_uri = origin['url']
        refs.append(ArtifactRef(path='source-origin.json',sha256=sha(origin_path),media_type='application/json'))
    return assemble(ir, physical, structured, refs, source_uri)


# 문서·표·셀·span 참조, 트리/섹션 순환, 값 보존, 경로와 문서 집계의 일관성을 검사한다.
# 위반 시 ValueError로 중단하고, 통과 시 검사 요약을 반환한다. 의미 정확도 승인은 하지 않는다.
def validate_document(doc):
    """Identity, topology, coverage, raw equality; deliberately NOT semantic approval."""
    ir = doc.document_ir
    from doc_understanding.parser.relations.logical_tables import validate_logical_links
    validate_logical_links(doc)
    if ir is None or ir.document_id != doc.document_id:
        raise ValueError('MISSING_OR_MISMATCHED_DOCUMENT_IR')
    physical = {t.table_id: t for t in doc.physical_tables}
    structured = {t.table_id: t for t in doc.table_structure}
    if len(physical) != len(doc.physical_tables) or set(physical) != set(structured):
        raise ValueError('DUPLICATE_OR_MISSING_TABLE')
    source_ids = {b.block_id for p in ir.pages for b in p.blocks}
    source_ids |= {s.span_id for p in ir.pages for b in p.blocks for s in b.spans}
    sections = {s.section_id: s for s in doc.sections}
    contents = {c.content_id: c for c in doc.contents}
    if len(contents) != len(doc.contents) or len(sections) != len(doc.sections):
        raise ValueError('DUPLICATE_DOCUMENT_NODE')
    # 각 노드의 부모 사슬을 따라 중복 ID·순환·존재하지 않는 부모를 검사한다.
    # 행 트리와 열 트리에 같은 검사를 적용하며 노드를 수정하거나 부모를 추정하지 않는다.
    def acyclic(nodes):
        index = {n.node_id: n for n in nodes}
        if len(index) != len(nodes):
            raise ValueError('DUPLICATE_TREE_NODE')
        for n in nodes:
            seen, cursor = set(), n
            while cursor:
                if cursor.node_id in seen:
                    raise ValueError('TREE_CYCLE')
                seen.add(cursor.node_id)
                if cursor.parent_id and cursor.parent_id not in index:
                    raise ValueError('DANGLING_TREE_PARENT')
                cursor = index.get(cursor.parent_id)
    for tid, table in physical.items():
        if table.document_id != doc.document_id:
            raise ValueError('TABLE_DOCUMENT_MISMATCH')
        st = structured[tid]
        if st.document_id != doc.document_id or st.page_number != table.page_number:
            raise ValueError('STRUCTURED_SOURCE_MISMATCH')
        if hashlib.sha256(table.model_dump_json().encode()).hexdigest() != st.physical_ir_sha256:
            raise ValueError('PHYSICAL_ARTIFACT_BINDING_MISMATCH')
        acyclic(st.row_tree)
        acyclic(st.column_tree)
        cells = {c.cell_id: c for c in table.cells}
        if len(cells) != len(table.cells):
            raise ValueError('DUPLICATE_PHYSICAL_CELL')
        for cell in cells.values():
            if cell.source.document_id != doc.document_id or cell.source.page_number != table.page_number:
                raise ValueError('CELL_SOURCE_MISMATCH')
            if not set(cell.span_refs) <= source_ids:
                raise ValueError('PHYSICAL_SPAN_MISSING')
        nodes = {n.node_id: n for n in st.row_tree + st.column_tree}
        if any(n.source_cell_id not in cells for n in nodes.values()):
            raise ValueError('NODE_SOURCE_MISSING')
        roles_by_id = {r.cell_id: r.role for r in st.cell_roles}
        mapped = {m.cell_id for m in st.cell_mappings}
        if len(mapped) != len(st.cell_mappings) or set(roles_by_id) != set(cells):
            raise ValueError('DUPLICATE_MAPPING_OR_ROLE_COVERAGE')
        if mapped != {cid for cid, role in roles_by_id.items() if role == 'value'}:
            raise ValueError('VALUE_MAPPING_COVERAGE')
        for m in st.cell_mappings:
            if m.cell_id not in cells or m.original_text != cells[m.cell_id].original_text:
                raise ValueError('CELL_VALUE_CHANGED')
            for path, tree in ((m.row_path, st.row_tree), (m.column_path, st.column_tree)):
                if not set(path) <= nodes.keys() or (path and path_for(nodes[path[-1]], tree) != path):
                    raise ValueError('INVALID_CELL_PATH')
    for c in doc.contents:
        if c.section_id and c.section_id not in sections:
            raise ValueError('DANGLING_SECTION')
        if c.kind == 'table' and c.table_id not in physical:
            raise ValueError('DANGLING_CONTENT_TABLE')
        if not set(c.context_content_ids) <= contents.keys():
            raise ValueError('DANGLING_CONTEXT')
        for ref in c.source_references:
            if ref.document_id != doc.document_id or not 1 <= ref.page_number <= len(ir.pages):
                raise ValueError('CONTENT_SOURCE_MISMATCH')
            if ref.span_id and ref.span_id not in source_ids:
                raise ValueError('CONTENT_SPAN_MISSING')
    for s in doc.sections:
        seen, cursor = set(), s
        while cursor:
            if cursor.section_id in seen or (cursor.parent_id and cursor.parent_id not in sections):
                raise ValueError('INVALID_SECTION_TREE')
            seen.add(cursor.section_id)
            cursor = sections.get(cursor.parent_id)
        if s.content_ids != tuple(c.content_id for c in doc.contents if c.section_id == s.section_id):
            raise ValueError('SECTION_CONTENT_ORDER_MISMATCH')
    if [c.reading_order for c in doc.contents] != list(range(len(doc.contents))):
        raise ValueError('INVALID_READING_SEQUENCE')
    if doc.row_hierarchy != tuple(n for t in doc.table_structure for n in t.row_tree):
        raise ValueError('ROW_VIEW_MISMATCH')
    if doc.column_hierarchy != tuple(n for t in doc.table_structure for n in t.column_tree):
        raise ValueError('COLUMN_VIEW_MISMATCH')
    if doc.cell_mappings != tuple(m for t in doc.table_structure for m in t.cell_mappings):
        raise ValueError('CELL_VIEW_MISMATCH')
    valid_targets = set(physical) | set(contents) | set(sections)
    valid_targets |= {n.node_id for t in doc.table_structure for n in t.row_tree+t.column_tree}
    valid_targets |= {c.cell_id for t in doc.physical_tables for c in t.cells}
    for link in doc.context_links:
        if link.target_id and link.target_id not in valid_targets:
            raise ValueError('DANGLING_CONTEXT_SCOPE')
        if link.source.document_id != doc.document_id or (link.source.span_id and link.source.span_id not in source_ids):
            raise ValueError('CONTEXT_EVIDENCE_MISSING')
    return {'reference_integrity': True, 'all_value_cells_preserved': True,
            'preservation_scope':'existing reconstructed value cells; undetected evidence remains in original IR',
            'unassigned_original_cell_ids':sum(len(t.unassigned_source_cell_ids) for t in doc.physical_tables),
            'semantics_validated': False, 'facts_published': 0}
