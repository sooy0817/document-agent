"""Bounded, verbatim source-derived search text, never a new Document schema."""
import hashlib
import json
import re
from pydantic import BaseModel, ConfigDict
from doc_understanding.document_rag.index import cells_for_table, display_path

VERSION = 'table-path-experiment/0.1'


class Projection(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    projection_id: str
    kind: str
    run_id: str
    document_id: str
    table_id: str
    page: int
    institution: str | None
    provenance: str = 'source_rule'
    semantic_status: str = 'unvalidated_candidate'
    snapshot_sha256: str
    text: str
    text_sha256: str
    source_ids: list[str]
    row_node_ids: list[str] = []
    cell_ids: list[str] = []
    metadata: dict = {}


def normalize(text):
    text = re.sub(r'주\s*\)?$', '', text or '')
    return re.sub(r'[\W_0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]', '', text).lower()


def institution_for(doc):
    if doc.institution:
        return doc.institution
    candidate = doc.document_ir.source_name.split('_', 1)[0]
    return candidate if re.fullmatch(r'[가-힣A-Za-z0-9]+(?:농협|축협)', candidate) else None


def path_integrity(table, evidence):
    nodes = {n.node_id: n for n in table.row_tree + table.column_tree}
    selected = {evidence['cell_id'], *evidence['row_node_ids'], *evidence['column_node_ids']}
    if any(a.code in ('PATH_COLLISION', 'PARENT_AMBIGUOUS', 'HEADER_REGION_AMBIGUOUS')
           and selected.intersection(a.target_ids) for a in table.ambiguities):
        return False
    for axis in ('row_node_ids', 'column_node_ids'):
        path = evidence[axis]
        if not path or any(n not in nodes for n in path) or len(path) != len(set(path)):
            return False
        if any(nodes[child].parent_id != parent for parent, child in zip(path, path[1:])):
            return False
        if any(getattr(nodes[n], 'candidate_parent_ids', ()) or
               getattr(nodes[n], 'evidence_state', None) == 'unresolved' for n in path):
            return False
    return evidence['lookup_eligible']


def project_document(doc, run_id, snapshot_sha256):
    contents = {c.content_id: c for c in doc.contents}
    sections = {s.section_id: s for s in doc.sections}
    result = []
    for table in doc.table_structure:
        tc = next(c for c in doc.contents if c.table_id == table.table_id)
        context = [contents[cid] for cid in tc.context_content_ids]
        section = sections.get(tc.section_id)
        title = section.title_raw if section else ''
        values = cells_for_table(doc, table.table_id)
        by_row = {}
        for cell in values:
            key = tuple(cell['row_node_ids'])
            if key:
                by_row.setdefault(key, []).append(cell)
        column_paths = list(dict.fromkeys(display_path(v['column_path']) for v in values if v['column_path']))
        row_paths = list(dict.fromkeys(display_path(v['row_path']) for v in values if v['row_path']))
        # Do not turn prose/geometry into a claim about financial meaning or unit scope.
        context_text = '\n'.join((c.text_raw or '')[:800] for c in context if not c.duplicate_of_table_ids)[:2400]
        description = (f'원문 문서: {doc.document_ir.source_name}\n제목 후보: {title}\n'
                       f'표 주변 원문: {context_text}\n'
                       f'구조 후보: 행 경로 {len(row_paths)}개, 열 경로 {len(column_paths)}개.\n'
                       '열 경로 원문: '+' / '.join(column_paths)+'\n'
                       '내용(행 라벨 원문): '+' / '.join(row_paths))
        original_bytes = len(description.encode())
        description = description.encode()[:8000].decode('utf8', errors='ignore')
        common = dict(run_id=run_id, document_id=doc.document_id, table_id=table.table_id,
                      page=table.page_number, institution=institution_for(doc), snapshot_sha256=snapshot_sha256)
        source_ids = [tc.content_id]+list(tc.context_content_ids)
        metadata = dict(title_raw=title, context_content_ids=list(tc.context_content_ids),
                        description_rule=VERSION, analysis_state=table.analysis_state,
                        description_original_bytes=original_bytes, description_truncated=original_bytes > 8000,
                        row_paths=row_paths, column_paths=column_paths,
                        features=[f.type for f in table.features],
                        institution_origin='canonical' if doc.institution else 'source_filename_candidate',
                        reading_order=tc.reading_order,
                        context_reading_order=[dict(content_id=c.content_id, reading_order=c.reading_order) for c in context],
                        eligible_cells=sum(path_integrity(table, v) for v in values),
                        unresolved_cells=sum(not path_integrity(table, v) for v in values))
        result.append(Projection(**common, projection_id=table.table_id+'/description', kind='table',
            text=description, text_sha256=hashlib.sha256(description.encode()).hexdigest(),
            source_ids=source_ids, metadata=metadata))
        for path, cells in by_row.items():
            text = display_path(cells[0]['row_path'])
            row_id = table.table_id+'/row-path/'+hashlib.sha256(json.dumps(path).encode()).hexdigest()[:20]
            result.append(Projection(**common, projection_id=row_id, kind='row', text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                source_ids=[*path], row_node_ids=list(path), cell_ids=[c['cell_id'] for c in cells],
                metadata=dict(row_path=cells[0]['row_path'], column_paths=[c['column_path'] for c in cells],
                              eligible=all(path_integrity(table, c) for c in cells),
                              description_rule=VERSION)))
    return result


def parse_question(question, institutions):
    years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question)
    names = [i for i in institutions if i and i in question]
    # Longest exact source metadata name avoids substring matches inside another name.
    names = [i for i in names if not any(i != other and i in other for other in names)]
    measure = '구성비' if re.search(r'구성비|비중|차지하는\s*비율|percentage|share|ratio', question, re.I) else (
        '증감' if re.search(r'증감|B\s*-\s*A|늘어난|줄어든|change', question, re.I) else
        '금액' if re.search(r'금액|얼마|잔액|액수|amount|balance', question, re.I) else None)
    query = question
    for name in names:
        query = query.replace(name, '')
    query = re.sub(r'(?:19|20)\d{2}\s*년?', '', query)
    query = re.sub(r'재무현황(?:에서|의)?|원문|금액|구성비|얼마|액수|알려\s*주세요|알려줘|무엇|인가요|은\s*\?|는\s*\?|칸에는|표기는|표시되어|있나요|찾아주세요|어떻게|되나요|비중|차지하는\s*비율|증감|아래|하위|항목', ' ', query)
    query = re.sub(r'\b(?:amount|balance|share|ratio|percentage|change)\b', ' ', query, flags=re.I)
    query = re.sub(r'\s+', ' ', query).strip(' ?.,')
    query = re.sub(r'(?:은|는|의|을|를|에서|이|가)$', '', query).strip()
    return dict(institution=names[0] if len(names) == 1 else None, institutions=names,
                periods=years, measure=measure, row_query=query or question,
                provenance='explicit_question_tokens', institution_ambiguous=len(names) > 1)


def llm_description_candidate(table_projection, text, evidence_ids):
    """Inactive candidate lane; never silently mixed with source-rule vectors."""
    if not evidence_ids or not set(evidence_ids).issubset(table_projection.source_ids):
        raise ValueError('LLM_DESCRIPTION_SOURCE_NOT_ALLOWED')
    return table_projection.model_copy(update=dict(
        projection_id=table_projection.projection_id+'/llm-candidate', kind='table_llm_candidate',
        provenance='llm_hypothesis', text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        source_ids=list(evidence_ids), metadata={**table_projection.metadata, 'active': False}))
