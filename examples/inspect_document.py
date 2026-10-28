"""Actual local pipeline on synthetic source; no model/database calls."""
import argparse,json
from pathlib import Path
from doc_understanding.staging.prepare import prepare
from doc_understanding.document_rag.document import build_from_run,validate_document
from doc_understanding.document_rag.index import make_entries,cells_for_table
from doc_understanding.document_rag.nested_content import build_nested_content

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    run,_=prepare(Path(__file__).parent/'synthetic.pdf',a.output/'extraction')
    doc=build_from_run(run);checks=validate_document(doc)
    entries=make_entries(doc);cells=[c for t in doc.table_structure for c in cells_for_table(doc,t.table_id)]
    nested=build_nested_content(doc)
    values={'document.json':doc.model_dump(mode='json'),'search-projections.json':entries,
        'cell-evidence.json':cells,'nested-content.json':nested.model_dump(mode='json'),'checks.json':checks}
    for name,value in values.items():
        (a.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(tables=len(doc.physical_tables),cells=sum(len(t.cells) for t in doc.physical_tables),
       search_entries=len(entries),cell_candidates=len(cells),facts_published=0,api_calls=0),indent=2))
    print('Inspect the generated JSON. Structural consistency is not semantic approval.')
if __name__=='__main__':main()
