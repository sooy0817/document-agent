"""Source identity and geometry must agree; labels/document names are not features."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from doc_understanding.domain.models import BBox
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.assembly.nested_content import build_nested_content, resolve_cell_content

@pytest.fixture(scope='module')
def hana():
    summary=json.loads(Path('data/nested-region-split/v1/evaluation/summary.json').read_text(encoding='utf8'))
    record=next(r for r in summary['documents'] if r['id']=='H04')
    return CanonicalDocument.model_validate_json((Path(record['run'])/'document.json').read_bytes())

def test_development_existing_children_link_once_without_changing_ir(hana):
    before=hana.model_dump_json();links=build_nested_content(hana)
    assert len(links.cells)==2
    assert all(c.status=='geometrically_linked' for c in links.cells)
    assert sorted(len(c.child_table_ids) for c in links.cells)==[1,2]
    assert len(links.candidates)==3
    assert all(not c.uncovered_span_ids and not c.duplicate_span_ids and not c.outside_parent_span_ids for c in links.cells)
    for c in links.cells:
        hydrated=resolve_cell_content(hana,links,c.parent_cell_id)
        assert all(i['kind']!='native_table' for i in hydrated['items'])
        assert c.semantic_status=='not_validated'
    assert hana.model_dump_json()==before

def fixture_geometry():
    b=lambda *v:BBox(x0=v[0],y0=v[1],x1=v[2],y1=v[3])
    spans=[NS(span_id='s1',text_raw='0',source=NS(bbox=b(20,15,25,20),page_number=1,document_id='d')),
           NS(span_id='s2',text_raw='0',source=NS(bbox=b(60,15,65,20),page_number=1,document_id='d'))]
    nc=[NS(cell_id='n1',span_refs=('s1',),source=NS(bbox=b(10,10,50,30))),NS(cell_id='n2',span_refs=('s2',),source=NS(bbox=b(50,10,90,30)))]
    pc=[NS(cell_id='p1',span_refs=('s1',),source_bbox=b(5,10,50,30)),NS(cell_id='p2',span_refs=('s2',),source_bbox=b(50,10,95,30))]
    native=NS(table_candidate_id='n',source=NS(bbox=b(10,10,90,30)),cells=nc)
    physical=NS(table_id='p',page_number=1,bounding_box=b(5,10,95,30),cells=pc)
    return NS(page_number=1,tables=[native],blocks=[NS(spans=spans)]),physical,b

def test_nonidentical_boxes_with_same_source_and_cell_coordinates_match():
    from doc_understanding.parser.physical.table_identity import match_tables
    page,p,b=fixture_geometry();mapping,audit=match_tables(page,[p])
    assert mapping=={'n':('p',)}
    assert audit[0].status=='matched' and audit[0].shared_span_ids==('s1','s2')

@pytest.mark.parametrize('damage',['partial','wrong_cell','distant','extra','unknown','no_support'])
def test_matching_text_or_span_overlap_alone_is_insufficient(damage):
    from doc_understanding.parser.physical.table_identity import match_tables
    page,p,b=fixture_geometry()
    if damage=='partial':p.cells[1].span_refs=()
    if damage=='wrong_cell':p.cells[0].span_refs=('s2',);p.cells[1].span_refs=('s1',)
    if damage=='distant':p.bounding_box=b(200,10,290,30)
    if damage=='extra':p.cells[0].span_refs+=('foreign',)
    if damage=='unknown':page.blocks[0].spans.pop()
    if damage=='no_support':
        for s in page.blocks[0].spans:s.text_raw=' '
    mapping,audit=match_tables(page,[p])
    assert not mapping.get('n')

def test_ambiguous_both_directions_do_not_choose_a_winner():
    from copy import deepcopy
    from doc_understanding.parser.physical.table_identity import match_tables
    page,p,b=fixture_geometry();other=deepcopy(p);other.table_id='p2'
    mapping,audit=match_tables(page,[p,other]);assert not mapping.get('n')
    assert all(a.status=='ambiguous' for a in audit)
    n=deepcopy(page.tables[0]);n.table_candidate_id='n2';page.tables.append(n)
    mapping,audit=match_tables(page,[p]);assert not any(mapping.values())

def test_repeated_values_with_different_source_ids_do_not_match():
    from doc_understanding.parser.physical.table_identity import match_tables
    page,p,b=fixture_geometry()
    page.blocks[0].spans += [NS(span_id='s3',text_raw='0',source=NS(bbox=b(20,15,25,20))),NS(span_id='s4',text_raw='0',source=NS(bbox=b(60,15,65,20)))]
    p.cells[0].span_refs=('s3',);p.cells[1].span_refs=('s4',)
    mapping,audit=match_tables(page,[p]);assert not mapping.get('n')

def test_duplicate_physical_child_blocks_parent_link_instead_of_fallback(hana):
    child=hana.physical_tables[1]
    duplicate=child.model_copy(update={'table_id':child.table_id+'/duplicate'})
    result=build_nested_content(hana.model_copy(update={'physical_tables':hana.physical_tables+(duplicate,)}))
    cell=next(c for c in result.cells if c.parent_cell_id.endswith('/c0013'))
    assert cell.status=='unresolved' and 'TABLE_IDENTITY_NOT_ONE_TO_ONE' in cell.reasons
    assert not cell.child_table_ids

def test_previous_sidecar_is_readable_but_not_silently_revalidated(hana):
    from doc_understanding.parser.assembly.nested_content import NestedContent,validate_links,snapshot_hash
    old=json.loads(Path('data/nested-region-split/v1/evaluation/H04/nested-content.json').read_text(encoding='utf8'))
    links=NestedContent.model_validate(old)
    assert links.linkage_algorithm=='bbox/1'
    # 저장된 구버전 결과는 원본 스냅샷도 다르므로 먼저 해시 검증에서 거부되어야 한다.
    with pytest.raises(ValueError,match='SNAPSHOT_MISMATCH'):
        validate_links(hana,links)
    # 메모리상의 비교 후보만 현재 해시에 맞춰 알고리즘 버전 검증도 독립적으로 확인한다.
    # 실제 sidecar 파일은 갱신하지 않으며 구버전 연결을 유효한 결과로 승인하지 않는다.
    same_snapshot=links.model_copy(update={'canonical_sha256':snapshot_hash(hana)})
    with pytest.raises(ValueError,match='LINKAGE_ALGORITHM_MISMATCH'):
        validate_links(hana,same_snapshot)
