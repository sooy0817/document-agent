import hashlib
import json
from collections import Counter
from pathlib import Path

from .models import MetricMappingBundle


def render(data,root):
    selection=json.loads(Path("reports/staging-five/manifest.json").read_text(encoding="utf-8"))
    rows=[]
    files={}
    for doc in selection["documents"]:
        path=data/f"{doc['id']}.json"
        bundle=MetricMappingBundle.model_validate_json(path.read_bytes())
        files[doc["id"]]=hashlib.sha256(path.read_bytes()).hexdigest()
        prior=json.loads(Path(f"reports/staging-five/{doc['id']}/result.json").read_text(encoding="utf-8"))
        run=Path(prior["manifest"]["run_dir"])
        ir=json.loads((run/"document-ir.json").read_bytes())
        for row in bundle.rows:
            page=ir["pages"][row.context["page"]-1]
            rows.append(row.model_dump(mode="json") | dict(
                document=doc["id"],name=Path(doc["source"]).name,source_sha256=bundle.source_sha256,
                source_run_id=bundle.source_run_id,bundle_sha256=files[doc["id"]],
                image=(run/page["image"]["artifact"]["path"]).as_uri(),
                page_width=page["width"],page_height=page["height"]))
    evaluation=json.loads((root/"evaluation.json").read_text(encoding="utf-8"))
    sql_name="sql-final.json" if (root/"sql-final.json").exists() else "sql-results.json"
    sql=json.loads((root/sql_name).read_text(encoding="utf-8"))
    stats=dict(rows=len(rows),decisions=dict(Counter(r["decision"] for r in rows)),
               value_candidates=sum(len(r["candidates"]) for r in rows),
               binding_missing=dict(Counter(u for r in rows if r["decision"]=="PROPOSE"
                    for c in r["candidates"] for u in c["unresolved"])),
               unreviewed=True,facts_published=0,source_hashes=files)
    (root/"summary.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"mapping.schema.json").write_text(json.dumps(MetricMappingBundle.model_json_schema(),ensure_ascii=False,indent=2),encoding="utf-8")
    template=Path(__file__).with_name("review.html").read_text(encoding="utf-8")
    payload=json.dumps(dict(rows=rows,stats=stats,evaluation=evaluation,sql=sql),ensure_ascii=False).replace("<","\\u003c")
    (root/"index.html").write_text(template.replace("@@DATA@@",payload).replace(
        'href="sql-results.json"',f'href="{sql_name}"'),encoding="utf-8")


if __name__=="__main__":
    import sys
    render(Path(sys.argv[1]),Path(sys.argv[2]))
