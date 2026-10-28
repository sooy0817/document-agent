"""Run unchanged runtime code, then compare this authored fixture independently.

Expected cells are used only after extraction; they never enter parser inputs.
Outputs contain actual IRs and candidate states, not fabricated RAG answers.
"""
import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from doc_understanding.staging.prepare import prepare
from doc_understanding.document_rag.document import build_from_run, validate_document
from doc_understanding.document_rag.nested_content import build_nested_content

ROOT = Path(__file__).resolve().parent


def evaluate(doc, nested):
    tables = sorted(doc.physical_tables, key=lambda t: (t.page_number, t.bounding_box.y0))
    assert len(tables) == 3
    merged, outer, inner = tables
    xs = [42,120,270,345,420,495,570]
    ys = [193,229,265,311,357,403,449]
    gold = [(0,0,2,1,'Group'),(0,1,2,1,'Item'),(0,2,1,2,'2024'),(0,4,1,2,'2025')]
    gold += [(1,c,1,1,v) for c,v in zip(range(2,6),['Amount','Share','Amount','Share'])]
    gold += [(2,0,2,1,'Assets'),(4,0,2,1,'Liabilities')]
    rows = [('Cash','120','30','150','33.3'),('Receivables','280','70','300','66.7'),
            ('Short-term debt','80','','0',''),('Adjustment','-10','','-12','')]
    gold += [(r,c,1,1,v) for r,row in enumerate(rows,2) for c,v in enumerate(row,1)]

    def check_cells(table, expected, x, y, text=True):
        found = {(c.row_index,c.column_index): c for c in table.cells}
        assert len(found) == len(expected) == len(table.cells)
        for r,col,rs,cs,value in expected:
            cell = found[r,col]
            assert (cell.rowspan,cell.colspan) == (rs,cs)
            assert set(cell.occupied_slots) == {(a,b) for a in range(r,r+rs) for b in range(col,col+cs)}
            actual = cell.source_bbox.xyxy
            assert all(abs(a-b)<.05 for a,b in zip(actual,[x[col],y[r],x[col+cs],y[r+rs]]))
            if text: assert cell.original_text.strip() == value, (value,cell.original_text)
        return len(expected)

    checked_merged = check_cells(merged,gold,xs,ys)
    inner_rows = [('Household','2024','2025'),('One person','40','45'),
                  ('Two people','65','70'),('Three or more','80','90')]
    inner_gold = [(r,c,1,1,v) for r,row in enumerate(inner_rows) for c,v in enumerate(row)]
    checked_inner = check_cells(inner,inner_gold,[177,352,449,546],[340,374,416,458,500])
    check_cells(outer,[(r,c,1,1,'') for r in range(3) for c in range(2)],
                [42,154,570],[174,212,650,700],text=False)

    assert len(nested.cells)==1
    link = nested.cells[0]
    assert link.containment_status=='linked' and link.coverage_status=='complete'
    assert link.reading_order_status=='total'
    assert link.child_table_ids == (inner.table_id,)
    assert [i.kind for i in link.items] == ['content','content','content','table','content','content']
    assert not (link.uncovered_span_ids or link.duplicate_span_ids or link.outside_parent_span_ids)
    assert not doc.fact_publication_allowed

    paths=[]
    for table in (merged,inner):
        structured=next(s for s in doc.table_structure if s.table_id==table.table_id)
        labels={n.node_id:n.label_raw for n in (*structured.row_tree,*structured.column_tree)}
        for mapping in structured.cell_mappings:
            cell=next(c for c in table.cells if c.cell_id==mapping.cell_id)
            if table.table_id==merged.table_id:
                r,c=cell.row_index,cell.column_index
                expected_row=['Assets' if r<4 else 'Liabilities',rows[r-2][0]]
                expected_col=['2024' if c<4 else '2025','Amount' if c%2==0 else 'Share']
            else:
                expected_row=[inner_rows[cell.row_index][0]]
                expected_col=[inner_rows[0][cell.column_index]]
            row=[labels[i] for i in mapping.row_path]
            col=[labels[i] for i in mapping.column_path]
            assert (row,col)==(expected_row,expected_col),(row,col)
            paths.append(dict(table_id=table.table_id,cell_id=cell.cell_id,raw_value=mapping.original_text,
                              row_path_labels=row,column_path_labels=col,evidence_state=mapping.evidence_state))
    assert len(paths)==22
    return dict(physical_cells=48,geometry_and_span_checks=48,
                exact_text_checks=checked_merged+checked_inner,matched_value_paths=len(paths),
                nested_parent_links=1,reading_order='paragraph, paragraph, caption, table, note, paragraph',
                semantic_status='not_validated',facts_published=0,api_calls=0,database_calls=0,
                limitations=['Authored development fixture, not an independent generalization benchmark.',
                            'Outer-cell text coverage is checked via original spans; 42 cell texts are exact-matched.',
                            'Correct fixture paths do not override inferred/unresolved runtime evidence states.']),paths


def run(output):
    output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='document-agent-complex-') as tmp:
        run_dir,_=prepare(ROOT/'complex-synthetic.pdf',Path(tmp))
        doc=build_from_run(run_dir)
        nested=build_nested_content(doc)
        summary,paths=evaluate(doc,nested)
        for ref in doc.source_artifact_refs:
            target=output/ref.path;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(run_dir/ref.path,target)
            assert hashlib.sha256(target.read_bytes()).hexdigest()==ref.sha256
        for ref in [doc.document_ir.source]+[p.image.artifact for p in doc.document_ir.pages if p.image]:
            target=output/ref.path;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(run_dir/ref.path,target)
        payloads={'canonical-document.json':doc.model_dump(mode='json'),
                  'nested-content.json':nested.model_dump(mode='json'),
                  'value-paths.json':paths,'summary.json':summary,'document-checks.json':validate_document(doc)}
        for name,value in payloads.items():
            (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
