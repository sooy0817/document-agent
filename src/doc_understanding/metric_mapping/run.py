import hashlib
import json
import re
from pathlib import Path
from time import perf_counter

from doc_understanding.staging.metric_candidate import MetricCandidate
from .adapter import (anchor, auxiliary, block_ref, digest, proposal_context,
                      structured_context, table_context)
from .core import ALIASES, classify, compact, header_candidates, number, period_binding
from .models import MappingRow, MetricMappingBundle


def make_binding(cell, label, table, context, snippets, blocks, ir_hash, metric):
    headers = header_candidates(table,cell,label)
    ht=compact(" ".join(h["text_raw"] or "" for h in headers))
    role = ("ratio" if any(x in ht for x in ("구성비","비율","%")) else
            "change" if "증감" in ht else "amount_candidate")
    period=period_binding(headers,snippets)
    evidence=[anchor(label,"label",ir_hash),anchor(cell,"value",ir_hash)]
    for h in headers:
        evidence.append(anchor(h,"period",ir_hash))
    raw_units=set()
    for s in context["unit_candidates"]:
        raw_units.update(re.findall(r"(백만원|천원|억원|(?<![가-힣])원)",s["text_raw"]))
    unit=next(iter(raw_units)) if len(raw_units)==1 and role=="amount_candidate" else None
    unit_blocks=[b for b in blocks if b["block_id"] in
                 {s["block_id"] for s in context["unit_candidates"]}]
    evidence.extend(block_ref(b,"unit",ir_hash) for b in unit_blocks)
    period_blocks=[b for b in blocks if b["block_id"] in
                   {s["block_id"] for s in period["evidence"]}]
    evidence.extend(block_ref(b,"period",ir_hash) for b in period_blocks)
    scope_options = {scope for b in context["title_candidates"]
                     for scope in re.findall(r"\[\s*(통합|신용|일반)\s*\]",b["text_raw"])}
    scope=next(iter(scope_options)) if len(scope_options)==1 else None
    scope_blocks=[b for b in blocks if b["block_id"] in
                  {s["block_id"] for s in context["title_candidates"]}]
    if scope:
        evidence.extend(block_ref(b,"accounting_scope",ir_hash) for b in scope_blocks)
    institutions = [b for b in blocks if len(compact(b["text_raw"]))<65
                    and b["source"]["bbox"]["y0"]<=table["source"]["bbox"]["y0"]+65
                    and re.search(r"농협|축협|축산업협동조합",b["text_raw"])]
    institution = institutions[0]["text_raw"].split("\n")[0] if len(institutions)==1 else None
    if institution:
        evidence.append(block_ref(institutions[0],"institution",ir_hash))
    parsed=number(cell["text_raw"])
    missing=[]
    if role != "amount_candidate":
        missing.append("NON_AMOUNT_COLUMN")
    if parsed is None:
        missing.append("VALUE_UNAVAILABLE_OR_AMBIGUOUS")
    if not period["year"]:
        missing.append("PERIOD_UNRESOLVED")
    if not period["end"]:
        missing.append("EXACT_PERIOD_DATES_UNRESOLVED")
    if not unit:
        missing.append("UNIT_SCOPE_UNRESOLVED")
    if not institution:
        missing.append("INSTITUTION_UNRESOLVED")
    if not scope:
        missing.append("ACCOUNTING_SCOPE_UNRESOLVED")
    if "손실" in label["text_raw"] and parsed and not parsed.startswith("-"):
        missing.append("LOSS_SIGN_UNRESOLVED")
    if "또는" in label["text_raw"]:
        missing.append("ALTERNATE_PROFIT_LOSS_ROW_UNRESOLVED")
    return dict(value_cell_id=cell["cell_id"],raw_value=cell["text_raw"],parsed_literal=parsed,
                column_role=role,headers=[h["text_raw"] for h in headers],
                period=period,unit=unit,institution=institution,accounting_scope=scope,
                evidence=evidence,unresolved=missing,ready_for_publication=False)


def execute(root):
    root.mkdir(parents=True,exist_ok=False)
    selection=json.loads(Path("reports/staging-five/manifest.json").read_text(encoding="utf-8"))
    results=[]
    for doc in selection["documents"]:
        started=perf_counter()
        prior=json.loads(Path(f"reports/staging-five/{doc['id']}/result.json").read_text(encoding="utf-8"))
        run_dir=Path(prior["manifest"]["run_dir"])
        ir_path=run_dir/"document-ir.json"
        ir=json.loads(ir_path.read_bytes())
        if digest(Path(doc["source"])) != ir["source_sha256"]:
            raise ValueError("SOURCE_CHANGED")
        structures=[]
        hashes={str(ir_path):digest(ir_path)}
        for a in prior["manifest"]["artifacts"]:
            if a["kind"]=="PhysicalTableIR":
                path=Path(a["path"])
                sp=path.with_name(path.stem+"-structured.json")
                structures.append((json.loads(path.read_bytes()),json.loads(sp.read_bytes()),sp))
                hashes[str(path)]=digest(path)
                hashes[str(sp)]=digest(sp)
        aux=auxiliary(ir["source_sha256"])
        for a in aux:
            hashes[a["path"]]=a["sha256"]
            hashes[a["description_path"]]=a["description_sha256"]
        rows=[]
        for page in ir["pages"]:
            for table in page["tables"]:
                ctx,snippets,blocks=table_context(page,table,digest(ir_path))
                for label in table["cells"]:
                    raw=label["text_raw"] or ""
                    if len(raw)>120:
                        continue
                    for metric,aliases in ALIASES.items():
                        if not any(a in compact(raw) for a in aliases):
                            continue
                        structure=structured_context(label,structures)
                        proposal=proposal_context(label,aux)
                        decision,alternative,reasons=classify(metric,raw,ctx["family"],bool(structure),
                            any(not p["consistent"] for p in proposal),
                            parent_labels=[label for s in structure+proposal for label in s.get("parent_labels",[])])
                        row_id=hashlib.sha256((label["cell_id"]+metric).encode()).hexdigest()[:24]
                        values=[c for c in table["cells"] if c["row_index"]==label["row_index"]
                                and c["column_index"]>label["column_index"]]
                        bindings=[make_binding(c,label,table,ctx,snippets,blocks,digest(ir_path),metric)
                                  for c in values]
                        if alternative in ("ratio", "per_unit_income"):
                            for binding in bindings:
                                binding["column_role"]=alternative
                                binding["unit"]=None
                                binding["unresolved"].append("DERIVED_MEASURE_UNIT_REQUIRES_OWN_SCOPE")
                        candidates=[]
                        for i,b in enumerate(bindings):
                            if decision!="PROPOSE" or b["column_role"]!="amount_candidate" or b["parsed_literal"] is None:
                                continue
                            p=b["period"]
                            instant=metric.startswith("total_")
                            kind=("instant" if p["end"] and not p["start"] and instant else
                                  "duration" if p["start"] and p["end"] and not instant else "unresolved")
                            candidates.append(MetricCandidate(
                                candidate_id=f"{row_id}-{i}",source_sha256=ir["source_sha256"],
                                processing_run_id=prior["primary"]["run_id"],metric=metric,
                                raw_value=b["raw_value"],parsed_value=b["parsed_literal"],
                                sign_policy="literal only; unsigned loss interpretation unresolved",
                                institution=b["institution"],period_kind=kind,
                                period_start=p["start"] if kind=="duration" else None,
                                period_end=p["end"] if kind!="unresolved" else None,
                                displayed_unit=b["unit"],
                                multiplier={"원":1,"천원":1000,"백만원":1000000,"억원":100000000}.get(b["unit"]),
                                accounting_scope={"통합":"integrated","신용":"credit","일반":"general"}.get(b["accounting_scope"],"unresolved"),
                                evidence=b["evidence"],unresolved=tuple(b["unresolved"])))
                        missing=[]
                        if not aux:
                            missing.append("NO_MATCHED_PROPOSAL_DESCRIPTION_FOR_DOCUMENT")
                        elif not proposal:
                            missing.append("NO_ANCHORED_PROPOSAL_DESCRIPTION_FOR_ROW")
                        if any(s["physical_state"]!="resolved" or s["boundaries_unresolved"] for s in structure):
                            missing.append("PHYSICAL_GRID_PARTIAL")
                        if not bindings:
                            missing.append("NO_SAME_ROW_VALUE_CELL")
                        rows.append(MappingRow(
                            row_id=row_id,label=raw,metric=metric,decision=decision,
                            alternative_metric=alternative,reasons=tuple(reasons),unresolved=tuple(missing),
                            context=dict(document_id=doc["id"],page=page["page_number"],
                                table_id=table["table_candidate_id"],label_cell_id=label["cell_id"],
                                label_bbox=label["source"]["bbox"],table=ctx,structured=structure,
                                interpretation=proposal,source_anchor=anchor(label,"label",digest(ir_path))),
                            bindings=tuple(bindings),candidates=tuple(candidates)))
        bundle=MetricMappingBundle(
            source_sha256=ir["source_sha256"],source_run_id=prior["primary"]["run_id"],
            source_ir_sha256=digest(ir_path),input_hashes=hashes,rows=tuple(rows),
            coverage=dict(pages=len(ir["pages"]),label_rows=len(rows),
                proposal_bundles=len(aux),metrics_without_label=[m for m in ALIASES if not any(r.metric==m for r in rows)],
                caveat="label retrieval recall unknown; paragraph-only metrics not automatically mapped",
                paragraph_only_status="ABSTAIN_not_implemented"),elapsed_seconds=perf_counter()-started)
        (root/f"{doc['id']}.json").write_text(bundle.model_dump_json(indent=2),encoding="utf-8")
        results.append(dict(document=doc["id"],rows=len(rows),candidates=sum(len(r.candidates) for r in rows)))
    (root/"generation.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(results)


if __name__=="__main__":
    import sys
    execute(Path(sys.argv[1]))
