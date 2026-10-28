"""Explicit local Docker PostgreSQL transport. SQL is sent via stdin, never shell interpolation."""
import json
import subprocess
from pathlib import Path


def literal(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def js(value):
    return literal(json.dumps(value, ensure_ascii=False, allow_nan=False)) + "::jsonb"


def sql(statement):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-X",
         "-U", "nh_document", "-d", "nh_document", "-v", "ON_ERROR_STOP=1", "-At"],
        input="SET standard_conforming_strings=on;\n" + statement,
        encoding="utf-8", capture_output=True, check=False,
    )
    if result.returncode:
        # Do not expose SQL DETAIL (may contain source text) or credentials in logs.
        raise RuntimeError("LOCAL_POSTGRES_STATEMENT_FAILED")
    return "\n".join(x for x in result.stdout.splitlines() if x != "SET")


def migrate():
    if sql("SELECT current_database();").strip() != "nh_document":
        raise RuntimeError("LOCAL_DATABASE_GUARD")
    return sql("BEGIN;\n" + Path(__file__).with_name("schema.sql").read_text() + "\nCOMMIT;")


def query(statement):
    return json.loads(sql(f"SELECT coalesce(json_agg(q),'[]'::json) FROM ({statement}) q;"))


def insert(table, values):
    return f"INSERT INTO staging.{table} ({','.join(values)}) VALUES ({','.join(values.values())});"


def ingest(ir, layout, run_dir, original, key, run_id, artifacts, fail=False):
    """Atomic records; request key is explicit and successful replays are no-ops."""
    from hashlib import sha256
    from time import perf_counter
    started = perf_counter()
    source_id = sha256(str(original.resolve()).encode()).hexdigest()
    version_id = source_id + ":" + ir.source_sha256
    if sha256(original.read_bytes()).hexdigest() != ir.source_sha256:
        raise ValueError("SOURCE_CHANGED")
    if layout.document_id != ir.document_id or len(layout.pages) != len(ir.pages):
        raise ValueError("LAYOUT_SOURCE_MISMATCH")
    valid_refs = {p.page_id for p in ir.pages}
    valid_refs.update(b.block_id for p in ir.pages for b in p.blocks)
    valid_refs.update(t.table_candidate_id for p in ir.pages for t in p.tables)
    valid_refs.update(aid for aid, _, _, _, pid in artifacts if pid)
    if any(r.source not in valid_refs or r.target not in valid_refs for r in layout.relations):
        raise ValueError("DANGLING_LAYOUT_REFERENCE")
    for _, _, path, payload, _ in artifacts:
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError("ARTIFACT_CONTENT_MISMATCH")
    existing = query(f"SELECT run_id,status,version_id FROM staging.processing_run WHERE idempotency_key={literal(key)}")
    if existing:
        if existing[0]["version_id"] != version_id:
            raise ValueError("IDEMPOTENCY_SOURCE_CONFLICT")
        if existing[0]["status"] in ("FAILED", "RUNNING"):
            raise ValueError("FAILED_OR_INTERRUPTED_ATTEMPT_REQUIRES_NEW_KEY")
        saved = query(f"SELECT artifact_id,sha256 FROM staging.artifact WHERE run_id={literal(existing[0]['run_id'])}")
        if {r["artifact_id"]: r["sha256"] for r in saved} != {
            aid: sha256(p.read_bytes()).hexdigest() for aid, _, p, _, _ in artifacts
        }:
            raise ValueError("IDEMPOTENCY_ARTIFACT_CONFLICT")
        return {"duplicate": True, "run_id": existing[0]["run_id"]}
    sql("BEGIN;\n" + insert("source_document", {
        "source_id": literal(source_id), "original_path": literal(original.resolve())
    }).replace(");", ") ON CONFLICT DO NOTHING;") + insert("document_version", {
        "version_id": literal(version_id), "source_id": literal(source_id),
        "sha256": literal(ir.source_sha256), "bytes": str(original.stat().st_size),
    }).replace(");", ") ON CONFLICT DO NOTHING;") + insert("processing_run", {
        "run_id": literal(run_id), "version_id": literal(version_id),
        "idempotency_key": literal(key), "status": literal("RUNNING"),
    }) + "\nCOMMIT;")
    statements = ["BEGIN;"]
    def add(table, **values):
        statements.append(insert(table, values))
    evidence_ids = set()
    def evidence(eid, pid, kind, payload):
        if eid in evidence_ids:
            return
        evidence_ids.add(eid)
        add("evidence", run_id=literal(run_id), evidence_id=literal(eid),
            page_id=literal(pid), kind=literal(kind), payload=js(payload))
    for page, diagnosis in zip(ir.pages, layout.pages, strict=True):
        add("page", run_id=literal(run_id), page_id=literal(page.page_id),
            page_number=str(page.page_number), status=literal(diagnosis.status),
            diagnosis=js(diagnosis.model_dump(mode="json")))
        evidence(page.page_id, page.page_id, "page",
                 {"page_number": page.page_number, "image": page.image.model_dump(mode="json")})
        for block in page.blocks:
            evidence(block.block_id, page.page_id, block.kind, block.model_dump(mode="json"))
            for span in block.spans:
                evidence(span.span_id, page.page_id, "span", span.model_dump(mode="json"))
        for table in page.tables:
            evidence(table.table_candidate_id, page.page_id, "table", table.model_dump(mode="json"))
            for cell in table.cells:
                evidence(cell.cell_id, page.page_id, "cell", cell.model_dump(mode="json"))
    for block in layout.blocks:
        add("content_block", run_id=literal(run_id), block_id=literal(block.block_id),
            payload=js(block.model_dump(mode="json")))
    for aid, kind, path, payload, page_id in artifacts:
        raw = path.read_bytes()
        add("artifact", run_id=literal(run_id), artifact_id=literal(aid), kind=literal(kind),
            sha256=literal(sha256(raw).hexdigest()), local_path=literal(path.resolve()),
            payload=js(payload))
        if page_id:
            evidence(aid, page_id, kind, {"artifact_id": aid, "sha256": sha256(raw).hexdigest()})
    for i, rel in enumerate(layout.relations):
        if rel.source not in evidence_ids or rel.target not in evidence_ids:
            raise ValueError("DANGLING_LAYOUT_REFERENCE")
        add("relation_candidate", run_id=literal(run_id), relation_id=literal(str(i)),
            source_ref=literal(rel.source), target_ref=literal(rel.target),
            payload=js(rel.model_dump(mode="json")))
    if fail:
        statements.append("SELECT 1 / 0;")
    statements.append(f"UPDATE staging.processing_run SET status={literal(layout.processing_status)}, "
                      f"completed_at=now() WHERE run_id={literal(run_id)};")
    statements.append("COMMIT;")
    try:
        sql("\n".join(statements))
    except Exception:
        sql(f"UPDATE staging.processing_run SET status='FAILED',error='TRANSACTION_ROLLED_BACK',"
            f"completed_at=now() WHERE run_id={literal(run_id)};")
        raise
    return {"duplicate": False, "run_id": run_id, "database_seconds": perf_counter() - started}
