"""Real local DB acceptance check, opt-in; records failure injection separately."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from .local import ingest, literal, migrate, query, sql
from .prepare import load, write_json


def verify(manifest_path, report_dir):
    start = perf_counter()
    manifest, ir, layout, artifacts = load(manifest_path)
    original = Path(manifest["source"])
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    migrate()
    batch = str(uuid4())
    run_id = str(uuid4())
    key = f"{ir.source_sha256}:staging-v1:{ir.run.run_id}"
    primary = ingest(ir, layout, Path(manifest["run_dir"]), original, key, run_id, artifacts)
    run_id = primary["run_id"]
    repeat = ingest(ir, layout, Path(manifest["run_dir"]), original, key, str(uuid4()), artifacts)
    assert repeat["duplicate"] and repeat["run_id"] == run_id
    failed_id = str(uuid4())
    try:
        ingest(ir, layout, Path(manifest["run_dir"]), original,
               batch + ":fault", failed_id, artifacts, fail=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("FAILURE_INJECTION_NOT_TRIGGERED")
    failed = query(f"SELECT status,error FROM staging.processing_run WHERE run_id={literal(failed_id)}")
    assert failed[0]["status"] == "FAILED"
    rollback = {}
    for table in ("artifact", "page", "evidence", "content_block", "relation_candidate"):
        rollback[table] = query(f"SELECT count(*) AS n FROM staging.{table} WHERE run_id={literal(failed_id)}")[0]["n"]
    assert not any(rollback.values())
    recovery = ingest(ir, layout, Path(manifest["run_dir"]), original,
                      batch + ":recovery", str(uuid4()), artifacts)
    fetched = query(f"SELECT payload FROM staging.content_block WHERE run_id={literal(run_id)}")
    expected = {b.block_id: b.model_dump(mode="json") for b in layout.blocks}
    assert {r["payload"]["block_id"]: r["payload"] for r in fetched} == expected
    stored_ir = query(f"SELECT payload FROM staging.artifact WHERE run_id={literal(run_id)} AND artifact_id='document-ir'")[0]["payload"]
    assert stored_ir == ir.model_dump(mode="json")
    page_rows = query(f"SELECT page_id,diagnosis FROM staging.page WHERE run_id={literal(run_id)}")
    assert {r["page_id"]: r["diagnosis"] for r in page_rows} == {
        p.page_id: p.model_dump(mode="json") for p in layout.pages}
    stored_artifacts = query(f"SELECT artifact_id,payload FROM staging.artifact WHERE run_id={literal(run_id)}")
    assert {r["artifact_id"]: r["payload"] for r in stored_artifacts} == {
        aid: payload for aid, _, _, payload, _ in artifacts}
    recovered = query(f"SELECT artifact_id,sha256 FROM staging.artifact WHERE run_id={literal(recovery['run_id'])}")
    assert {r["artifact_id"]: r["sha256"] for r in recovered} == {
        aid: hashlib.sha256(path.read_bytes()).hexdigest() for aid, _, path, _, _ in artifacts}
    # FK enforcement tested with an actual invalid relation transaction.
    fk_rejected = False
    try:
        sql(f"BEGIN; INSERT INTO staging.relation_candidate VALUES "
            f"({literal(run_id)},'invalid-probe','missing','missing','{{}}'); COMMIT;")
    except RuntimeError:
        fk_rejected = True
    assert fk_rejected
    counts = {t: query(f"SELECT count(*) AS n FROM staging.{t} WHERE run_id={literal(run_id)}")[0]["n"]
              for t in ("artifact", "page", "evidence", "content_block", "relation_candidate")}
    size = query("SELECT pg_total_relation_size(c.oid) AS bytes, c.relname FROM pg_class c "
                 "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='staging' AND c.relkind='r'")
    payload_size = {}
    for table, column in (("artifact", "payload"), ("evidence", "payload"),
                          ("content_block", "payload"), ("relation_candidate", "payload"),
                          ("page", "diagnosis")):
        payload_size[table] = query(f"SELECT coalesce(sum(pg_column_size({column})),0) AS bytes "
                                   f"FROM staging.{table} WHERE run_id={literal(run_id)}")[0]["bytes"]
    trace = query(f"""SELECT d.original_path,v.sha256,p.page_number,b.block_id,
        b.payload->'bbox' AS bbox,b.payload->>'text_raw' AS text_raw
        FROM staging.content_block b JOIN staging.processing_run r USING(run_id)
        JOIN staging.document_version v USING(version_id)
        JOIN staging.source_document d USING(source_id)
        JOIN staging.page p ON p.run_id=b.run_id AND p.page_id=b.payload->>'page_id'
        WHERE b.run_id={literal(run_id)} AND b.payload->>'kind'='text'
        ORDER BY p.page_number,(b.payload->>'extraction_order')::int LIMIT 8""")
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    result = {
        "scope": "one exposed development PDF; not accuracy/generalization evaluation",
        "manifest": manifest, "primary": primary, "duplicate_replay": repeat,
        "injected_failure": {"run_id": failed_id, "state": failed, "remaining_records": rollback},
        "recovery": recovery, "counts": counts, "schema_storage": size,
        "checks": {"original_hash_unchanged": True, "all_block_payloads_exact": True,
                   "document_ir_json_equal": True, "foreign_key_rejection": fk_rejected,
                   "idempotent": True, "atomic_rollback": True,
                   "page_diagnosis_exact": True, "all_artifacts_exact": True,
                   "recovery_artifact_hashes_exact": True},
        "run_jsonb_payload_bytes": payload_size,
        "page_diagnosis": [p.model_dump(mode="json") for p in layout.pages],
        "trace": trace, "verification_seconds": perf_counter() - start,
        "natural_failures": 0, "injected_failures": 1,
        "meaning_accuracy": None, "ocr_accuracy": None, "facts_published": 0,
    }
    sql(f"UPDATE staging.processing_run SET metrics="
        f"{literal(json.dumps({'extraction_seconds':manifest['seconds'], 'artifact_disk_bytes':manifest['disk_bytes'], 'checks':result['checks']}))}::jsonb "
        f"WHERE run_id={literal(run_id)};")
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "result.json", result)
    print(json.dumps({"report": str(report_dir), "counts": counts, "checks": result["checks"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    verify(args.manifest, args.report)
