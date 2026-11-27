from doc_understanding.domain.models import BBox
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, CellEvidence, AxisLink, AxisProposal, rule_proposal, check_proposal


def cell(id, text, row, col, table='t', filled=False, usable=True):
    return CellEvidence(id=id,table_id=table,text=text,bbox=BBox(x0=col*100,y0=row*30,
        x1=(col+1)*100,y1=(row+1)*30),spans=(id+'/span',),physical_usable=usable,
        row=row,col=col,rs=1,cs=1,filled=filled,bold=False)


def region(cells):
    return AxisRegion(id='region',document_id='doc',page=1,bbox=BBox(x0=0,y0=0,x1=300,y1=100),
        source_table_id='native',cells=tuple(cells))


def matrix():
    return region([cell('a','종류',0,0),cell('b','첫 기간',0,1),cell('c','다음 기간',0,2),
        cell('d','가',1,0),cell('e','0',1,1),cell('f','-2.5',1,2),
        cell('g','나',2,0),cell('h','1.2',2,1),cell('i','3',2,2)])


def test_numeric_matrix_and_source_preservation():
    r=matrix();before=r.model_dump_json();p=rule_proposal(r)
    assert len(p.links)==4
    checked=check_proposal(r,p)
    assert all(c.status=='supported_candidate' and not c.semantic_verified for c in checked)
    assert [c.original_text for c in checked]==['0','-2.5','1.2','3']
    assert r.model_dump_json()==before


def test_aligned_fragments_need_source_appearance():
    cells=[cell('a','분류',0,0,'t0',True),cell('b','설명',0,1,'t0',True),
           cell('c','항목A',1,0,'t1'),cell('d','상세A',1,1,'t1'),
           cell('e','항목B',2,0,'t2'),cell('f','상세B',2,1,'t2')]
    p=rule_proposal(region(cells)); assert len(p.links)==2
    assert p.links[0].column_path==['b']
    assert not rule_proposal(region([c.model_copy(update={'filled':False}) for c in cells])).links


def test_wrong_row_unknown_id_malformed_cell_blocked():
    r=matrix();p=rule_proposal(r)
    bad=p.links[0].model_copy(update={'row_path':['g']})
    assert 'ROW_GEOMETRY_CONFLICT' in check_proposal(r,AxisProposal(links=[bad],abstain_cell_ids=[]))[0].errors
    bad=bad.model_copy(update={'row_path':['fabricated']})
    assert 'UNKNOWN_CELL_ID' in check_proposal(r,AxisProposal(links=[bad],abstain_cell_ids=[]))[0].errors
    r=r.model_copy(update={'cells':tuple(c.model_copy(update={'physical_usable':False}) if c.id=='e' else c for c in r.cells)})
    assert check_proposal(r,p)[0].status=='unresolved'


def test_absent_axis_not_same_as_unknown_or_verified():
    p=AxisProposal(links=[AxisLink(cell_id='e',row_path=[],column_path=['b'],row_axis='absent',
        column_axis='present',reason='no visible row label')],abstain_cell_ids=[])
    assert check_proposal(matrix(),p)[0].errors==('AXIS_ABSENCE_NEEDS_REVIEW',)


def test_no_rules_on_text_only_or_numeric_first_row():
    r=matrix();r=r.model_copy(update={'cells':tuple(c.model_copy(update={'text':'arbitrary'}) for c in r.cells)})
    assert not rule_proposal(r).links


def test_conflicting_header_value_and_abstain_rejected():
    r=matrix();p=rule_proposal(r)
    p=p.model_copy(update={'abstain_cell_ids':['e'], 'links':[p.links[0],p.links[1].model_copy(update={'row_path':['e']})]})
    c=check_proposal(r,p)
    assert 'LINK_ABSTENTION_CONFLICT' in c[0].errors
    assert 'HEADER_VALUE_ROLE_CONFLICT' in c[1].errors


def test_multilevel_regions_are_not_reinterpreted_as_flat():
    from doc_understanding.agents.axis_review.axis_resolver import geometry_options
    r=matrix();r=r.model_copy(update={'cells':tuple(c.model_copy(update={'cs':2}) if c.id=='b' else c for c in r.cells)})
    assert not rule_proposal(r).links
    assert not geometry_options(r)
