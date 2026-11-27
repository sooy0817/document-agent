"""Source-independent examples: no bank names, financial answers or network calls."""
from doc_understanding.agents.axis_review.axis_resolver import AxisLink, AxisProposal, geometry_options
from doc_understanding.agents.axis_review.axis_validation import audit_region
from tests.test_axis_resolver import cell,region,matrix


def fragments():
    return region([cell('h0','범주',0,0,'f0'),cell('h1','안내',0,1,'f0'),
        cell('r1','대상',1,0,'f1'),cell('v1','모두가능',1,1,'f1'),
        cell('r2','방법',2,0,'f2'),cell('v2','인터넷 또는 창구를 이용합니다.',2,1,'f2'),
        cell('r3','종류',3,0,'f3'),cell('v3','자유선택',3,1,'f3')])


def propose(r):
    links=geometry_options(r)
    return AxisProposal(links=links,abstain_cell_ids=[c.id for c in r.cells if c.id not in {p.cell_id for p in links}])


def test_short_aligned_header_band_keeps_real_values_without_domain_words():
    r=fragments();before=r.model_dump_json();a=audit_region(r,propose(r))
    assert len(a.checked_links)==3
    assert all(c.status=='supported_candidate' for c in a.checked_links)
    assert len(a.cells)==len(r.cells) and not a.semantic_verified
    assert r.model_dump_json()==before
    assert any(e.kind=='compact_band_body_contrast' for e in a.header_evidence)


def test_continuation_prose_cannot_be_header_despite_correct_geometry():
    r=fragments();r=r.model_copy(update={'cells':tuple(c.model_copy(update={'text':'This paragraph describes an actual account condition, not a column heading.'}) if c.id=='h1' else c for c in r.cells)})
    a=audit_region(r,propose(r))
    assert len(a.checked_links)==3
    assert all('HEADER_EVIDENCE_MISSING' in c.errors for c in a.checked_links)
    assert all(c.status=='unresolved' for c in a.checked_links)
    assert len(a.cells)==8


def test_all_source_cells_survive_and_omission_is_different_from_abstention():
    r=matrix();p=propose(r);dropped=p.links[0].cell_id
    p=p.model_copy(update={'links':p.links[1:]})
    a=audit_region(r,p)
    row=next(c for c in a.cells if c.cell_id==dropped)
    assert 'UNACCOUNTED_CELL' in row.issues and 'UNMAPPED_VALUE_CANDIDATE' in row.issues
    p=p.model_copy(update={'abstain_cell_ids':p.abstain_cell_ids+[dropped]})
    a=audit_region(r,p);row=next(c for c in a.cells if c.cell_id==dropped)
    assert row.disposition=='explicit_abstention'
    assert 'UNACCOUNTED_CELL' not in row.issues and 'UNMAPPED_VALUE_CANDIDATE' in row.issues
    assert len(a.cells)==len(r.cells)


def test_numeric_row_key_still_needs_a_value_mapping():
    r=matrix();r=r.model_copy(update={'cells':tuple(c.model_copy(update={'text':'1,000원'}) if c.id=='d' else c for c in r.cells)})
    p=propose(r);a=audit_region(r,p)
    d=next(c for c in a.cells if c.cell_id=='d')
    assert 'NUMERIC_AXIS_VALUE_UNMAPPED' in d.issues
    assert any('NUMERIC_AXIS_VALUE_UNMAPPED' in c.errors for c in a.checked_links if 'd' in c.proposal.row_path)


def test_role_conflicts_invalidate_all_involved_links_not_only_header_use():
    r=matrix();p=propose(r)
    p=p.model_copy(update={'links':[p.links[0],p.links[1].model_copy(update={'row_path':['e']})]})
    a=audit_region(r,p)
    assert all('ROLE_CONFLICT' in c.errors for c in a.checked_links)


def test_header_evidence_not_created_by_claiming_model_confidence():
    r=fragments();r=r.model_copy(update={'cells':tuple(c.model_copy(update={'text':'aa'}) for c in r.cells)})
    p=propose(r);p=p.model_copy(update={'links':[v.model_copy(update={'reason':'100% verified by another model'}) for v in p.links]})
    a=audit_region(r,p)
    assert not a.header_evidence
    assert all(c.status=='unresolved' for c in a.checked_links)


def test_unknown_abstention_and_duplicate_source_ids_are_reported():
    r=matrix();p=propose(r);p=p.model_copy(update={'abstain_cell_ids':p.abstain_cell_ids+['invented']})
    a=audit_region(r,p)
    assert any(f.code=='UNKNOWN_ABSTENTION_ID' for f in a.findings)
    r=r.model_copy(update={'cells':r.cells+(r.cells[0],)})
    a=audit_region(r,p)
    assert any(f.code=='DUPLICATE_SOURCE_CELL_ID' for f in a.findings)
    assert all(c.status=='unresolved' for c in a.checked_links)


def test_baseline_numeric_value_omission_is_visible_without_any_new_proposal():
    from doc_understanding.agents.axis_review.axis_validation import audit_mapping_coverage
    r=matrix();r=r.model_copy(update={'cells':tuple(c.model_copy(update={'text':'1,000원'}) if c.id=='d' else c for c in r.cells)})
    baseline=[{'row_tree':[{'source_cell_id':'d'}],'column_tree':[], 'cell_roles':[], 'cell_mappings':[]}]
    rows=audit_mapping_coverage(r,baseline)
    assert len(rows)==9
    assert 'NUMERIC_AXIS_VALUE_UNMAPPED' in next(c for c in rows if c.cell_id=='d').issues


def test_rejected_candidate_cannot_fall_back_to_same_bad_baseline_mapping():
    from doc_understanding.agents.axis_review.axis_validation import validated_views
    from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
    from doc_understanding.agents.axis_review.axis_resolver import CellEvidence
    from tests.semantic_fixtures import compound
    t=compound()[0];baseline=SemanticTableAnalyzer().analyze(t)
    cs=tuple(CellEvidence(id=c.cell_id,table_id=t.table_id,text=c.original_text or '',bbox=c.source_bbox,
        spans=c.span_refs,physical_usable=True,row=c.row_index,col=c.column_index,rs=c.rowspan,cs=c.colspan,
        filled=False,bold=False) for c in t.cells)
    r=region(cs);m=next(m for m in baseline.cell_mappings if m.row_path and m.column_path)
    nodes={n.node_id:n for n in baseline.row_tree+baseline.column_tree}
    p=AxisProposal(links=[AxisLink(cell_id=m.cell_id,row_path=[nodes[i].source_cell_id for i in m.row_path],
        column_path=[nodes[i].source_cell_id for i in m.column_path],row_axis='present',column_axis='present',reason='test')],abstain_cell_ids=[])
    a=audit_region(r,p);assert a.checked_links[0].status=='unresolved'
    before=baseline.model_dump_json();view=validated_views(r,a,[baseline.model_dump(mode='json')])[0]
    held=next(c for c in view.cell_mappings if c.cell_id==m.cell_id)
    assert not held.row_path and not held.column_path and held.original_text==m.original_text
    assert baseline.model_dump_json()==before
