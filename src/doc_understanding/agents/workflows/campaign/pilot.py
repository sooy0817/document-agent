"""Selected real PDFs -> current parser -> real specialist team in the campaign graph.

No repair executor, source-code application or human approval is performed here.
The old review database is read-only input, never the output of this execution.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz

from doc_understanding.evaluation.parser.golden import iou
from doc_understanding.review import error_basket
from doc_understanding.runtime.review_queue import ReviewQueue
from doc_understanding.services.documents import worker

from .locking import campaign_lock
from .service import execute


def read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    """Replace only mutable execution indexes, with an atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def local_path(root, relative):
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("MANIFEST_PATH_MUST_BE_RELATIVE")
    resolved = (root / path).resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("MANIFEST_PATH_OUTSIDE_PROJECT")
    return resolved


def code_fingerprint(root):
    digest = hashlib.sha256()
    for p in sorted((root / "src/doc_understanding").rglob("*.py")):
        digest.update(p.relative_to(root).as_posix().encode())
        digest.update(p.read_bytes())
    return digest.hexdigest()


def prepare(store, manifest_path, project_root):
    """Pin the user-selected evidence once; repeated starts find the same campaign."""
    project_root = Path(project_root).resolve()
    raw = Path(manifest_path).read_bytes()
    manifest = json.loads(raw)
    limits = manifest.get('limits', {})
    max_items = limits.get('max_items', 20)
    daily_limit = limits.get('max_daily_calls', 20)
    if (type(max_items) is not int or not 1 <= max_items <= 20
            or type(daily_limit) is not int or not 1 <= daily_limit <= 200):
        raise ValueError('INVALID_CAMPAIGN_LIMITS')
    if manifest.get("role") != "development" or not 1 <= len(manifest["items"]) <= max_items:
        raise ValueError("INVALID_PILOT_SCOPE")
    digest = hashlib.sha256(raw).hexdigest()
    index = store.root / ("pilot-" + digest[:20] + ".json")
    with campaign_lock(store.root / "pilot-create.lock"):
        if index.exists():
            prior = read(index)
            if prior["manifest_sha256"] != digest:
                raise ValueError("PILOT_MANIFEST_CHANGED")
            store.get(prior["campaign_id"])
            return prior["campaign_id"]
        documents = {}
        for d in manifest["documents"]:
            if d["role"] != "development" or d["label"] in documents:
                raise ValueError("INVALID_DOCUMENT_ROLE_OR_DUPLICATE")
            source = local_path(project_root, d["source_path"])
            canonical = local_path(project_root, d["canonical_path"])
            if sha(source) != d["source_sha256"] or sha(canonical) != d["canonical_sha256"]:
                raise ValueError("PILOT_SOURCE_CHANGED")
            documents[d["label"]] = {**d, "source": source}
        snapshots, scopes = {}, set()
        for item in manifest["items"]:
            if (item["role"] != "development" or item["item_id"] in snapshots
                    or not re.fullmatch(r"[a-f0-9]{32}", item["item_id"])):
                raise ValueError("INVALID_SELECTION_ROLE_OR_DUPLICATE")
            path = local_path(project_root, item["snapshot_path"])
            if sha(path) != item["snapshot_sha256"]:
                raise ValueError("PILOT_SNAPSHOT_CHANGED")
            snapshot = read(path)
            doc = documents[item["document"]]
            if (snapshot["source_pdf_sha256"] != doc["source_sha256"]
                    or snapshot["canonical_sha256"] != doc["canonical_sha256"]
                    or snapshot["selection"] != item["selection"]):
                raise ValueError("PILOT_SELECTION_MISMATCH")
            key = (item["document"], item["selection"]["page"], item["selection"]["table_id"])
            if key in scopes:
                # Existing mapping table is table-scoped; never silently collapse cells.
                raise ValueError("MULTIPLE_SELECTIONS_PER_TABLE_NOT_SUPPORTED")
            scopes.add(key)
            snapshots[item["item_id"]] = snapshot
        cid = store.create(manifest.get('goal') or 'Selected document structure review')
        folder = store.root / "pilots" / cid
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_bytes(raw)
        mapping = {}
        for label, d in documents.items():
            mapping[label] = store.add_sample(cid, d["source"], "development")
        save(folder / "snapshots.json", snapshots)
        spec = dict(campaign_id=cid, manifest_sha256=digest, samples=mapping,
                    snapshots_sha256=sha(folder / "snapshots.json"),
                    source_code_sha256=code_fingerprint(project_root),
                    additional_review_authorized=True, max_daily_calls=daily_limit,
                    reference_confirmed=False, parser_approved=False)
        save(folder / "spec.json", spec)
        config_path = store.root / "review-agent-config.json"
        if not config_path.exists():
            save(config_path, dict(enabled=True, daily_limit=daily_limit,
                                   activated_at=datetime.now(timezone.utc).isoformat(),
                                   model="gpt-4.1-mini-2025-04-14"))
        # Preserve an existing configuration, budget ledger and all prior calls.
        save(folder / "review-config-at-start.json", read(config_path))
        store.event(cid, "collect", "selected_manifest_registered")
        store.event(cid, "supervisor", f"additional_review_authorized_max_{daily_limit}_calls_per_day")
        save(index, dict(campaign_id=cid, manifest_sha256=digest))
        return cid


def rebind(snapshot, document):
    """Find exactly one matching table/cell in fresh output, never trust ordinal IDs."""
    selection = snapshot["selection"]
    old = next(t for t in snapshot["physical_tables"] if t["table_id"] == selection["table_id"])
    candidates = [t for t in document["physical_tables"]
                  if t["page_number"] == selection["page"]
                  and iou(old["bounding_box"], t["bounding_box"]) >= 0.95]
    if len(candidates) != 1:
        raise ValueError("SELECTED_TABLE_CHANGED_OR_AMBIGUOUS")
    table = candidates[0]
    cell = None
    if selection["scope"] == "cell":
        old_cell = snapshot["selected_cell"]
        def norm(value):
            return "".join((value or "").split())
        cells = [c for c in table["cells"]
                 if iou(c["source_bbox"], old_cell["source_bbox"]) >= 0.95
                 and norm(c["original_text"]) == norm(old_cell["original_text"])]
        if len(cells) != 1:
            raise ValueError("SELECTED_CELL_CHANGED_OR_AMBIGUOUS")
        cell = cells[0]
    elif selection["scope"] != "table":
        raise ValueError("UNSUPPORTED_SELECTION_SCOPE")
    return error_basket.CollectionRequest(page=selection["page"], scope=selection["scope"],
                                         table_id=table["table_id"],
                                         cell_id=cell["cell_id"] if cell else None,
                                         error_type="other", note="Campaign-selected evidence; not confirmed Gold.")


def collect_once(root, output, job, request):
    """Reuse the deterministic basket identity, including after dispatch interruption."""
    for p in root.glob("*/snapshot.json"):
        s = read(p)
        q = s["selection"]
        if (s["job_id"] == job["id"] and s["canonical_sha256"] == sha(output / "document.json")
                and q["page"] == request.page and q["scope"] == request.scope
                and q["table_id"] == request.table_id and q.get("cell_id") == request.cell_id
                and any(p.parent.glob("event-*.json"))):
            return error_basket.detail(root, p.parent.name)["id"]
    return error_basket.collect(root, output, job, request)["id"]


def specialist(store, cid, *, queue_factory=ReviewQueue, retry_failed=False):
    folder = store.root / "pilots" / cid
    spec = read(folder / "spec.json")
    manifest = read(folder / "manifest.json")
    if sha(folder / "manifest.json") != spec["manifest_sha256"]:
        raise ValueError("PINNED_MANIFEST_CHANGED")
    if sha(folder / "snapshots.json") != spec["snapshots_sha256"]:
        raise ValueError("PINNED_SNAPSHOTS_CHANGED")
    snapshots = read(folder / "snapshots.json")
    names = {d["label"]: d.get("name", d["label"]) for d in manifest["documents"]}
    samples = {s["id"]: s for s in store.samples(cid)}
    queue = queue_factory(store.root)
    results = []
    first = manifest.get("first_connected_review", {}).get("item_id")
    items = sorted(manifest["items"], key=lambda x: x["item_id"] != first)
    for item in items:
        item_id = item["item_id"]
        sample = samples[spec["samples"][item["document"]]]
        output = Path(sample["output"])
        receipt_path = folder / ("dispatch-" + item_id + ".json")
        try:
            snapshot = snapshots[item_id]
            encoded_snapshot = json.dumps(snapshot, sort_keys=True)
            # Snapshot bytes are also pinned individually in the selected manifest.
            if snapshot["source_pdf_sha256"] != sha(output / "input.pdf"):
                raise ValueError("SOURCE_CHANGED")
            document = read(output / "document.json")
            request = rebind(snapshot, document)
            with fitz.open(output / "input.pdf") as pdf:
                pages = len(pdf)
            basket_id = collect_once(store.root / "error-basket", output,
                                     dict(id=sample["id"], name=names[item["document"]],
                                          pages=pages, status="done"), request)
            receipt = read(receipt_path) if receipt_path.exists() else dict(
                generation=0, historical_request_id=item["request_id"],
                basket_id=basket_id, canonical_sha256=sha(output / "document.json"),
                selection_sha256=hashlib.sha256(encoded_snapshot.encode()).hexdigest(), previous_requests=[])
            if (receipt["basket_id"] != basket_id
                    or receipt["canonical_sha256"] != sha(output / "document.json")
                    or receipt["selection_sha256"] != hashlib.sha256(encoded_snapshot.encode()).hexdigest()):
                raise ValueError("PINNED_REVIEW_INPUT_CHANGED")
            if retry_failed and receipt.get("request_id"):
                previous = queue.detail(receipt["request_id"])
                if previous["status"] == "failed" or (
                    previous["status"] == "completed"
                    and (previous.get("result") or {}).get("review_complete") is False
                ):
                    receipt["previous_requests"].append(receipt.pop("request_id"))
                    receipt["generation"] += 1
                    store.event(cid, "specialist", "explicit_failed_review_retry", sample["id"])
            save(receipt_path, receipt)
            rid = queue.enqueue(basket_id, mode="agents_roles",
                                execution_key=f"{cid}:{item_id}:{receipt['generation']}")
            if not rid:
                raise ValueError("REVIEW_SELECTION_INACTIVE")
            receipt["request_id"] = rid
            save(receipt_path, receipt)
            with store.db() as db:
                db.execute("""INSERT INTO campaign_review_requests VALUES(?,?,?,?,?)
                    ON CONFLICT(sample_id,page,table_id) DO UPDATE SET
                    item_id=excluded.item_id,request_id=excluded.request_id""",
                           (sample["id"], request.page, request.table_id, basket_id, rid))
            store.event(cid, "specialist", "request_linked:" + rid, sample["id"])
            current = queue.detail(rid)
            if current["status"] == "queued":
                queue.run_one(request_id=rid)
                current = queue.detail(rid)
            result = current.get("result") or {}
            calls = result.get("call_records", [])
            if "routing_summary" in result:
                routing = result["routing_summary"]
                selected_table = next(t for t in document['physical_tables'] if t['table_id'] == request.table_id)
                expected_ids = {request.cell_id} if request.cell_id else {c['cell_id'] for c in selected_table['cells']}
                outcomes = result.get('outcomes', [])
                ok = (current["status"] == "completed" and result.get("review_complete") is True
                      and routing.get("total", 0) > 0
                      and len(outcomes) == routing["total"] == len(expected_ids)
                      and {o['cell_id'] for o in outcomes} == expected_ids
                      and all(o['status'] in ('rule_candidate', 'llm_candidate') for o in outcomes)
                      and routing["rule_retained"] + routing["llm_completed"] == routing["total"]
                      and all(c.get("status") == "completed" and c.get("output") for c in calls))
            else:
                ok = current["status"] == "completed" and len(calls) == 3 and all(
                    c.get("status") == "completed" and c.get("output") for c in calls)
            causes = []
            for p in (store.root / "review-queue/roles-loop" / rid).rglob("*.json"):
                value = read(p)
                if isinstance(value, dict) and value.get("error"):
                    causes.append(dict(file=p.name, error=value["error"], code=value.get("error_code")))
            results.append(dict(item_id=item_id, request_id=rid, page=request.page,
                                document=item["document"], table_id=request.table_id,
                                status=current["status"], error=current.get("error"), causes=causes,
                                specialist_review_completed=bool(ok),
                                model_review_completed=bool(ok and calls),
                                routing_summary=result.get('routing_summary'), accuracy_verified=False))
            if not ok:
                save(folder / "specialist.json", dict(items=results, planned_items=len(items),
                     reason="specialist_blocked:review_incomplete", parser_modified=False))
                return "specialist_blocked:review_incomplete"
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc).isupper() else type(exc).__name__
            results.append(dict(item_id=item_id, document=item["document"], status="blocked", error=code))
            save(folder / "specialist.json", dict(items=results, planned_items=len(items),
                 reason="specialist_blocked:preparation", parser_modified=False))
            store.event(cid, "specialist", code, sample["id"])
            return "specialist_blocked:preparation"
    save(folder / "specialist.json", dict(items=results, planned_items=len(items),
         reason="improvement_not_connected", parser_modified=False))
    return "improvement_not_connected"


def run(store, cid, project_root, *, resume=False, retry_failed=False,
        process=worker, queue_factory=ReviewQueue):
    store.get(cid)
    folder = store.root / "pilots" / cid
    spec = read(folder / "spec.json")
    if spec["source_code_sha256"] != code_fingerprint(Path(project_root)):
        raise ValueError("PILOT_CODE_CHANGED_CREATE_NEW_RUN")
    result = execute(store, cid, process=process, resume=resume,
                     specialist=lambda ident: specialist(store, ident, queue_factory=queue_factory,
                                                          retry_failed=retry_failed))
    save(folder / "execution.json", dict(result=result, parser_modified=False,
         human_approved=False, accuracy_verified=False,
         at=datetime.now(timezone.utc).isoformat()))
    return {"campaign_id": cid, **result}
