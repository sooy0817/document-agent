"""검색 대상 112건의 모든 표에서, 값이 근거로 쓰이지 못하는 이유를 센다. 읽기만 한다."""
import collections
import gc
import json
from pathlib import Path

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.rag.evidence.inspect import containment, table_view
from doc_understanding.rag.evidence.inspect import structure_of
from doc_understanding.evaluation.label_diagnostics import label_diagnostics
from doc_understanding.evaluation.path_diagnostics import REASONS, path_diagnostics

import argparse
from doc_understanding.rag.evidence import cells as cell_evidence, containers
from doc_understanding.rag.indexing import nested

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--manifest", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
if args.out.exists(): raise SystemExit("OUTPUT_ALREADY_EXISTS")
args.out.parent.mkdir(parents=True, exist_ok=True)
entries = json.loads(args.manifest.read_bytes())
link_counts = collections.Counter()
link_reasons = collections.Counter()
label_counts = {k: collections.Counter() for k in ('first_reason','all_blockers','axes','nodes_by_cause')}
label_examples = {}
path_counts = {k:{r:collections.Counter() for r in REASONS} for k in ('first_reason','all_blockers')}
path_examples = {}
tables, cell_blockers = [], collections.Counter()
for n, entry in enumerate(entries, 1):
    doc = CanonicalDocument.model_validate_json(Path(entry["document_path"]).read_bytes())
    info = containment(doc)
    pages = {p.page_number:p for p in doc.document_ir.pages}
    for link in doc.logical_table_links:
        if link.algorithm == 'header-inheritance/1.0':
            link_counts[link.status] += 1
            if link.status == 'candidate': link_reasons.update(link.reason_codes)
    for physical in doc.physical_tables:
        try:
            view = table_view(doc, physical.table_id, info)
        except Exception as error:
            tables.append(dict(doc=entry["document_id"][7:15], table=physical.table_id.split("/", 1)[-1], error=type(error).__name__))
            continue
        values = [c for c in view["cells"] if c["mapping_state"] is not None]
        usable = [c for c in values if c["eligible"]]
        reasons = collections.Counter(b for c in values if not c["eligible"] for b in (c["blockers"] or ["NO_REASON"]))
        first = collections.Counter((c["blockers"] or ["NO_REASON"])[0] for c in values if not c["eligible"])
        cell_blockers.update(first)
        roles = collections.Counter(c["role"] for c in view["cells"])
        structure = structure_of(doc,physical.table_id)[0]
        labels = label_diagnostics(physical, structure, pages.get(physical.page_number), view)
        for key, counter in label_counts.items(): counter.update(labels[key])
        for cause, sample in labels['examples'].items():
            examples = label_examples.setdefault(cause,[])
            if len(examples)<5:
                examples.append(dict(document_id=doc.document_id,table_id=physical.table_id,page=physical.page_number,**sample))
        paths = path_diagnostics(physical, structure, view)
        for mode, counters in path_counts.items():
            for reason, counter in counters.items(): counter.update(paths[mode][reason])
        for kind, sample in paths['examples'].items():
            examples = path_examples.setdefault(kind,[])
            if len(examples)<5:
                examples.append(dict(document_id=doc.document_id,table_id=physical.table_id,page=physical.page_number,**sample))
        tables.append(dict(
            doc=entry["document_id"][7:15], name=Path(entry["source_path"]).name, table=physical.table_id.split("/", 1)[-1],
            page=view["page"], rows=view["rows"], columns=view["columns"], state=view["analysis_state"],
            values=len(values), usable=len(usable), reasons=dict(reasons), main_reason=first.most_common(1)[0][0] if first else None,
            table_codes=view["table_codes"], issues=view["issues"], panels=len(view["panels"]),
            header_rows=max((p["header_rows"] for p in view["panels"]), default=0),
            label_columns=max((p["stub_columns"] for p in view["panels"]), default=0),
            headers=roles.get("column_header", 0), labels=roles.get("row_label", 0), unknown=roles.get("unknown", 0),
            nested=bool(view["parents"]), label_diagnostics=labels,path_diagnostics=paths))
    cell_evidence._REVERSED.clear()
    containers._CACHE.clear()
    containers._LINKS.clear()
    nested._PROJECTIONS.clear()
    del doc, pages
    gc.collect()
    print(n, len(tables), flush=True)
good = [t for t in tables if "error" not in t and t["values"]]
none = [t for t in good if t["usable"] == 0]
part = [t for t in good if 0 < t["usable"] < t["values"]]


def bucket(t):
    size = t["rows"] * t["columns"]
    return "1행 또는 1열" if min(t["rows"], t["columns"]) == 1 else "작음(≤12칸)" if size <= 12 else "보통(≤100칸)" if size <= 100 else "큼(>100칸)"


report = dict(
    path_diagnostics=dict(**{mode:{r:dict(v) for r,v in counts.items()} for mode,counts in path_counts.items()},examples=path_examples),
    label_diagnostics=dict(**{k:dict(v) for k,v in label_counts.items()},examples=label_examples),
    inheritance=dict(link_counts), inheritance_candidate_reasons=dict(link_reasons),
    tables=len(tables), with_values=len(good), all_usable=sum(t["usable"] == t["values"] for t in good),
    none_usable=len(none), partly=len(part), value_cells=sum(t["values"] for t in good), usable_cells=sum(t["usable"] for t in good),
    unusable_cells_by_first_reason=dict(cell_blockers.most_common()),
    none_usable_tables_by_main_reason=dict(collections.Counter(t["main_reason"] for t in none).most_common()),
    none_usable_cells_by_main_reason=dict(collections.Counter({k: sum(t["values"] for t in none if t["main_reason"] == k)
                                                               for k in {t["main_reason"] for t in none}}).most_common()),
    partly_tables_by_main_reason=dict(collections.Counter(t["main_reason"] for t in part).most_common()),
    none_usable_by_state=dict(collections.Counter(t["state"] for t in none)),
    none_usable_by_shape=dict(collections.Counter(bucket(t) for t in none)),
    all_usable_by_shape=dict(collections.Counter(bucket(t) for t in good if t["usable"] == t["values"])),
    none_usable_table_codes=dict(collections.Counter(c for t in none for c in t["table_codes"]).most_common(12)),
    none_usable_issues=dict(collections.Counter(c for t in none for c in t["issues"]).most_common(12)),
    none_usable_header_rows=dict(collections.Counter(t["header_rows"] for t in none)),
    none_usable_nested=sum(t["nested"] for t in none),
    none_usable_by_document=collections.Counter(t["name"] for t in none).most_common(15),
    biggest_none_usable=[dict(name=t["name"], page=t["page"], table=t["table"], values=t["values"], rows=t["rows"],
                              columns=t["columns"], reason=t["main_reason"], codes=t["table_codes"])
                         for t in sorted(none, key=lambda t: -t["values"])[:15]])
args.out.write_text(json.dumps(dict(report=report, tables=tables), ensure_ascii=False, indent=1), encoding="utf8")
print("REPORT", json.dumps({k:report[k] for k in ('tables','value_cells','usable_cells','none_usable','unusable_cells_by_first_reason','inheritance')}, ensure_ascii=False))
