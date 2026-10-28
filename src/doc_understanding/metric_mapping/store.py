"""Append derived artifacts in separate runs; source ingestion fingerprints stay intact."""
import json
from pathlib import Path
from uuid import uuid4
from time import perf_counter

from doc_understanding.staging.local import insert, js, literal, query, sql
from .adapter import digest
from .models import MetricMappingBundle


def store(root, report):
    outcomes=[]
    for path in sorted(root.glob("D*.json")):
        start=perf_counter()
        bundle=MetricMappingBundle.model_validate_json(path.read_bytes())
        for filename, expected in bundle.input_hashes.items():
            if digest(Path(filename))!=expected:
                raise ValueError("INPUT_ARTIFACT_CHANGED")
        parent=literal(bundle.source_run_id)
        source=query(f"SELECT version_id FROM staging.processing_run WHERE run_id={parent}")
        original=query(f"SELECT artifact_id,sha256 FROM staging.artifact WHERE run_id={parent}")
        assert next(r["sha256"] for r in original if r["artifact_id"]=="document-ir")==bundle.source_ir_sha256
        evidence=query(f"SELECT evidence_id,payload FROM staging.evidence WHERE run_id={parent}")
        registry={e["evidence_id"]:e["payload"] for e in evidence}
        checked=set()
        for row in bundle.rows:
            anchors=[row.context["source_anchor"]]
            anchors.extend(e for b in row.bindings for e in b["evidence"])
            for e in anchors:
                eid=e.get("cell_id") or e.get("block_id")
                original_e=registry.get(eid)
                if not original_e or original_e.get("text_raw")!=e["raw_text"]:
                    raise ValueError("ORIGINAL_TEXT_REFERENCE_MISMATCH")
                if original_e["source"]["bbox"]!=e["bbox"] or original_e["source"]["page_number"]!=e["page_number"]:
                    raise ValueError("ORIGINAL_COORDINATE_REFERENCE_MISMATCH")
                if e["artifact_sha256"]!=bundle.source_ir_sha256:
                    raise ValueError("SOURCE_HASH_REFERENCE_MISMATCH")
                checked.add(eid)
        payload=bundle.model_dump(mode="json")
        sha=digest(path)
        key="metric-context-v1:"+bundle.source_run_id+":"+sha
        existing=query(f"SELECT run_id,status FROM staging.processing_run WHERE idempotency_key={literal(key)}")
        if existing:
            run_id=existing[0]["run_id"]
        else:
            run_id=str(uuid4())
            sql("BEGIN;\n"+insert("processing_run",dict(
                run_id=literal(run_id),version_id=literal(source[0]["version_id"]),
                idempotency_key=literal(key),status=literal("REVIEW_REQUIRED"),
                metrics=js(dict(parent_run_id=bundle.source_run_id,kind="metric_mapping",
                                checked_evidence=len(checked),external_calls=0))))+
                insert("artifact",dict(run_id=literal(run_id),artifact_id=literal("metric-mapping"),
                    kind=literal("MetricMappingBundle"),sha256=literal(sha),
                    local_path=literal(path.resolve()),payload=js(payload)))+
                f"UPDATE staging.processing_run SET completed_at=now() WHERE run_id={literal(run_id)};\nCOMMIT;")
        fetched=query(f"SELECT payload,sha256 FROM staging.artifact WHERE run_id={literal(run_id)} AND artifact_id='metric-mapping'")[0]
        assert fetched["payload"]==payload and fetched["sha256"]==sha
        assert query(f"SELECT artifact_id,sha256 FROM staging.artifact WHERE run_id={parent}")==original
        duplicate=query(f"SELECT count(*) AS n FROM staging.processing_run WHERE idempotency_key={literal(key)}")[0]["n"]
        assert duplicate==1
        rows=query(f"""SELECT r->>'row_id' AS row_id,r->>'label' AS label,
            r->>'metric' AS metric,r->>'decision' AS decision,
            jsonb_array_length(r->'candidates') AS values
            FROM staging.artifact a CROSS JOIN LATERAL jsonb_array_elements(a.payload->'rows') r
            WHERE a.run_id={literal(run_id)} AND a.artifact_id='metric-mapping'""")
        assert len(rows)==len(bundle.rows)
        outcomes.append(dict(document=path.stem,run_id=run_id,source_run_id=bundle.source_run_id,
            row_count=len(rows),candidate_count=sum(len(r.candidates) for r in bundle.rows),
            checked_evidence=len(checked),sql_rows=rows,roundtrip_equal=True,
            source_artifacts_unchanged=True,idempotency_count=duplicate,
            seconds=perf_counter()-start,payload_bytes=path.stat().st_size))
        print(path.stem, len(rows), "rows: SQL roundtrip passed",flush=True)
    report.write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__":
    import sys
    store(Path(sys.argv[1]),Path(sys.argv[2]))
