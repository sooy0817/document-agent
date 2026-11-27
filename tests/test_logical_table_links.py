"""Frozen PDF regression plus adversarial edits, never parser input Gold."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.relations.logical_tables import build_logical_links, validate_logical_links

LH = Path('work/document-explorer/85373bab3d984c23beb3abe9663b27d1/document.json')


def synthetic_table(name, page, top, texts, heights):
    from doc_understanding.domain.models import BBox
    rows=[];cells=[];y=top
    for ri,(values,height) in enumerate(zip(texts,heights)):
        rows.append(NS(index=ri,start=y,end=y+height))
        for ci,text in enumerate(values):
            cells.append(NS(cell_id=f'{name}/{ri}/{ci}',row_index=ri,column_index=ci,
                rowspan=1,colspan=1,original_text=text,status='resolved'))
        y+=height
    return NS(table_id=name,page_number=page,rows=rows,cells=cells,
        columns=[NS(index=0,start=10,end=60),NS(index=1,start=60,end=200)],
        bounding_box=BBox(x0=10,y0=top,x1=200,y1=y))


def test_synthetic_headers_link_by_structure_without_document_vocabulary():
    ir=NS(pages=[NS(page_number=1,height=800,blocks=[])])
    head=synthetic_table('alpha',1,100,[['Category','Reading']],[20])
    body=synthetic_table('beta',1,124,[['Sample A','17'],['Sample B','0']],[40,40])
    link,=build_logical_links(ir,[head,body])
    assert link.paths[1].row_source_cell_ids==('beta/0/0',)
    assert link.paths[1].column_source_cell_ids==('alpha/0/1',)
    assert body.cells[-1].original_text=='0'
    distant=synthetic_table('gamma',1,200,[['Sample A','17']],[40])
    assert not build_logical_links(ir,[head,distant])


def test_synthetic_continuation_and_separate_table_negative():
    ir=NS(pages=[NS(page_number=p,height=800,blocks=[]) for p in [1,2]])
    a=synthetic_table('a',1,680,[['Group','Details'],['Oak','A long description']],[20,60])
    b=synthetic_table('b',2,40,[['','continued explanation']],[80])
    link,=build_logical_links(ir,[a,b])
    assert link.paths[0].row_source_cell_ids==('a/1/0',)
    b.cells[0].original_text='Maple'
    assert not build_logical_links(ir,[a,b])


@pytest.fixture(scope='module')
def frozen():
    if not LH.exists():
        pytest.skip('local source PDF regression is not bundled')
    return CanonicalDocument.model_validate_json(LH.read_bytes())


def target(links, page):
    return next(l for l in links if f'/p{page:04d}/' in l.target_table_id)


def test_continuation_and_detached_header_preserve_raw(frozen):
    before = frozen.model_dump_json()
    links = build_logical_links(frozen.document_ir, frozen.physical_tables)
    cells = {c.cell_id: c for t in frozen.physical_tables for c in t.cells}
    six = target(links, 6)
    assert [cells[c].original_text for c in six.paths[0].row_source_cell_ids] == ['세대구성원']
    assert six.paths[0].column_source_cell_ids == ()
    assert six.paths[0].state == 'unresolved'
    eleven = target(links, 11)
    assert all(p.row_source_cell_ids == eleven.inherited_row_source_cell_ids for p in eleven.paths[:3])
    assert not eleven.paths[-1].row_source_cell_ids  # do not inherit into the next full-width item
    twelve = target(links, 12)
    day = next(p for p in twelve.paths if '2025.07.24' in cells[p.cell_id].original_text)
    assert [cells[c].original_text for c in day.column_source_cell_ids] == ['신청접수']
    assert [cells[c].original_text for c in day.row_source_cell_ids] == ['2순위\n3순위']
    assert any(p.state == 'unresolved' and 'MULTIPLE_ROW_SCOPES' in p.reasons for p in twelve.paths)
    thirtyone = target(links, 31)
    row = next(p for p in thirtyone.paths if p.cell_id.endswith('cell0013'))
    assert [''.join(cells[c].original_text.split()) for c in row.row_source_cell_ids] == ['소득', '사업소득', '농업소득']
    assert [cells[c].original_text for c in row.column_source_cell_ids] == ['소득 및 재산 항목 설명']
    assert frozen.model_dump_json() == before
    validate_logical_links(frozen.model_copy(update={'logical_table_links': links}))


def test_new_heading_misalignment_and_competing_source_block_links(frozen):
    source = next(t for t in frozen.physical_tables if t.page_number == 10)
    dest = next(t for t in frozen.physical_tables if t.page_number == 11)
    # Nonblank new group is not a continuation even when the grid aligns.
    c = dest.cells[0].model_copy(update={'original_text':'독립적인 새 항목'})
    changed = dest.model_copy(update={'cells':(c,)+dest.cells[1:]})
    assert not build_logical_links(frozen.document_ir,(source,changed))
    cols = tuple(c.model_copy(update={'start':c.start+10,'end':c.end+10}) for c in dest.columns)
    assert not build_logical_links(frozen.document_ir,(source,dest.model_copy(update={'columns':cols})))
    duplicate = source.model_copy(update={'table_id':source.table_id+'/alternative'})
    assert not build_logical_links(frozen.document_ir,(source,duplicate,dest))


def test_intervening_text_blocks_detached_header(frozen):
    source,dest = [t for t in frozen.physical_tables if t.page_number == 12]
    page = frozen.document_ir.pages[11]
    original = next(b for b in page.blocks if b.spans)
    span = original.spans[0]
    box = span.source.bbox.model_copy(update={'y0':source.bounding_box.y1+.1,'y1':dest.bounding_box.y0-.1,
                                             'x0':100,'x1':180})
    sp = span.model_copy(update={'text_raw':'새로운 절', 'source':span.source.model_copy(update={'bbox':box})})
    page = page.model_copy(update={'blocks':page.blocks+(original.model_copy(update={'spans':(sp,)}),)})
    pages = tuple(page if p.page_number==12 else p for p in frozen.document_ir.pages)
    assert not build_logical_links(frozen.document_ir.model_copy(update={'pages':pages}),(source,dest))


def test_logical_refs_cannot_point_to_unrelated_cells(frozen):
    links = build_logical_links(frozen.document_ir,frozen.physical_tables)
    link = target(links,12)
    foreign = frozen.physical_tables[0].cells[0].cell_id
    path = link.paths[0].model_copy(update={'row_source_cell_ids':(foreign,)})
    link = link.model_copy(update={'paths':(path,)+link.paths[1:]})
    with pytest.raises(ValueError,match='OUTSIDE_LINK_SCOPE'):
        validate_logical_links(frozen.model_copy(update={'logical_table_links':(link,)}))
