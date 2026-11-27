"""Resume-safe bounded team cycle; existing source snapshots stay immutable."""

import json
import shutil
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.runtime.parser_freeze import check as check_parser_freeze
from doc_understanding.services.semantic_path_review import build_packet, create_review
from doc_understanding.agents.workflows.campaign.locking import campaign_lock
from doc_understanding.agents.workflows.campaign.pilot import save
from doc_understanding.agents.workflows.campaign.store import CampaignStore
from doc_understanding.tools.semantic_candidates import generate
from doc_understanding.validation.semantic_gate import decide
from .inventory import digest, file_hash, profile, select

VERSION = "semantic-diversity/1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def check_freeze(project):
    # 승인된 경로 이동만 허용하는 공통 검사로 코드와 원본 ZIP·KB 근거를 함께 확인한다.
    result = check_parser_freeze(project)
    if result['changed_or_missing']:
        raise ValueError("PARSER_FREEZE_CHANGED:" + result['changed_or_missing'][0])


def code_identity(project):
    return digest(
        {
            p.relative_to(project).as_posix(): file_hash(p)
            for p in sorted((project / "src/doc_understanding").rglob("*.py"))
        }
    )


def pin_job(path):
    doc = read(path / "document.json")
    if file_hash(path / "input.pdf") != doc["document_ir"]["source_sha256"]:
        raise ValueError("SOURCE_HASH_MISMATCH")
    return {
        "job_id": path.name,
        "folder": str(path.resolve()),
        "source_sha256": doc["document_ir"]["source_sha256"],
        "hashes": {
            n: file_hash(path / n)
            for n in ("input.pdf", "document.json", "nested.json", "result.json")
        },
        "dataset_role": "development",
        "family_status": "unconfirmed",
    }


def verify_job(job):
    for name, expected in job["hashes"].items():
        if file_hash(Path(job["folder"]) / name) != expected:
            raise ValueError("PINNED_JOB_CHANGED")


def create(
    project, data, output, jobs, *, public=False, per_type=3, max_tables=12, max_llm_cells=1
):
    project, data, output = map(lambda p: Path(p).resolve(), (project, data, output))
    check_freeze(project)
    if not 0 <= max_llm_cells <= 4 or not 1 <= len(jobs) <= 20:
        raise ValueError("INVALID_CAMPAIGN_BUDGET")
    select([], per_type, max_tables)
    # Never automatically inspect registered regression or holdout documents.
    protected = set()
    for db_path in (project / "work").rglob("campaigns.sqlite3"):
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
            protected.update(
                r[0] for r in db.execute("SELECT sha256 FROM samples WHERE role != 'development'")
            )
    pinned = []
    seen = set()
    for job in jobs:
        if len(job) != 32 or any(c not in "0123456789abcdef" for c in job):
            raise ValueError("INVALID_JOB_ID")
        path = (data / job).resolve()
        if not path.is_relative_to(data):
            raise ValueError("JOB_OUTSIDE_DATA")
        p = pin_job(path)
        if p["source_sha256"] in protected:
            raise ValueError("PROTECTED_DATASET_SOURCE")
        if p["source_sha256"] in seen:
            raise ValueError("DUPLICATE_SOURCE_SELECT_ONE_RUN")
        seen.add(p["source_sha256"])
        pinned.append(p)
    store = CampaignStore(output)
    cid = store.create("표 구조 다양성 수집 및 동결 IR 의미 검토")
    folder = output / cid
    folder.mkdir()
    save(
        folder / "spec.json",
        {
            "version": VERSION,
            "campaign_id": cid,
            "project": str(project),
            "data": str(data),
            "jobs": pinned,
            "public": public,
            "per_type": per_type,
            "max_tables": max_tables,
            "max_llm_cells": max_llm_cells,
            "code_sha256": code_identity(project),
            "holdout_evaluated": False,
        },
    )
    return cid


class State(TypedDict, total=False):
    stage: str


def run(output, cid):
    store = CampaignStore(output)
    store.get(cid)  # Validate identity before constructing paths.
    folder = Path(output) / cid
    with campaign_lock(folder / "run.lock"):
        spec = read(folder / "spec.json")
        project = Path(spec["project"])
        check_freeze(project)
        if code_identity(project) != spec["code_sha256"]:
            raise ValueError("CODE_CHANGED_NEW_CAMPAIGN_REQUIRED")
        for j in spec["jobs"]:
            verify_job(j)
        state_path = folder / "state.json"
        if not state_path.exists():
            save(state_path, {"status": "running", "completed": {}})

        def stage(name, operation):
            def invoke(state):
                status = read(state_path)
                artifact = folder / (name + ".json")
                if name in status["completed"]:
                    if file_hash(artifact) != status["completed"][name]:
                        raise ValueError("STAGE_ARTIFACT_CHANGED")
                    return {"stage": name}
                store.event(cid, name, "started")
                value = operation()
                save(artifact, value)
                status["completed"][name] = file_hash(artifact)
                status["status"] = "running"
                save(state_path, status)
                store.event(cid, name, "completed")
                return {"stage": name}

            return invoke

        def inventory():
            rows = []
            for job in spec["jobs"]:
                rows.extend(profile(read(Path(job["folder"]) / "document.json"), job["job_id"]))
            return {"profiles": rows, "documents": len(spec["jobs"])}

        def collection():
            from doc_understanding.agents.workflows.campaign.public_sources import discover
            from doc_understanding.agents.workflows.campaign.public_download import collect_public_pdfs

            initial = select(
                read(folder / "inventory.json")["profiles"], spec["per_type"], spec["max_tables"]
            )
            if not spec["public"] or not initial["gaps"]:
                return {
                    "status": "not_requested" if not spec["public"] else "no_gap",
                    "gaps": initial["gaps"],
                }
            # At most two official indexes, one candidate per source, one downloaded PDF.
            discover(store, cid, limit_per_source=1)
            collect_public_pdfs(store, cid, limit=1)
            return {
                "status": "attempted",
                "gaps": initial["gaps"],
                "sources": store.get(cid)["source_candidates"],
                "samples": store.samples(cid),
                "events": [e for e in store.get(cid)["events"] if e["team"] == "collect"],
            }

        def processing():
            from doc_understanding.services.documents import worker
            import fitz

            jobs, failures = [], []
            for sample in read(folder / "collection.json").get("samples", []):
                out = folder / "new-documents" / sample["id"]
                if out.exists():
                    failures.append(
                        {"sample": sample["id"], "reason": "INTERRUPTED_PARSE_REQUIRES_NEW_RUN"}
                    )
                    continue
                try:
                    check_freeze(project)
                    with fitz.open(sample["path"]) as pdf:
                        if len(pdf) > 80:
                            raise ValueError("PAGE_BUDGET_EXCEEDED")
                    out.mkdir(parents=True)
                    shutil.copyfile(sample["path"], out / "input.pdf")
                    worker(out / "input.pdf", out)
                    jobs.append(pin_job(out))
                except Exception as exc:
                    failures.append({"sample": sample["id"], "reason": type(exc).__name__})
            return {"jobs": jobs, "failures": failures, "reused_jobs": len(spec["jobs"])}

        def selection():
            rows = read(folder / "inventory.json")["profiles"]
            for job in read(folder / "processing.json")["jobs"]:
                rows.extend(profile(read(Path(job["folder"]) / "document.json"), job["job_id"]))
            return select(rows, spec["per_type"], spec["max_tables"])

        def candidates():
            jobs = {j["job_id"]: j for j in spec["jobs"] + read(folder / "processing.json")["jobs"]}
            selected = read(folder / "selection.json")["selected"]
            records = []
            cache = {}
            for t in selected:
                job = jobs[t["job_id"]]
                verify_job(job)
                key = (t["job_id"], t["page"])
                if key not in cache:
                    doc = CanonicalDocument.model_validate_json(
                        (Path(job["folder"]) / "document.json").read_bytes()
                    )
                    packet = build_packet(doc, (t["page"],))
                    target_ids = [
                        c.cell_id
                        for tab in doc.physical_tables
                        if tab.page_number == t["page"]
                        for c in tab.cells
                    ]
                    sources = {
                        c.cell_id: c.original_text or ""
                        for tab in doc.physical_tables
                        for c in tab.cells
                    }
                    rules = generate(doc.model_dump(mode="json"), target_ids)
                    routes = {c.cell_id: decide(c, sources).model_dump(mode="json") for c in rules}
                    # Page-level reports retain adjacent-page source context using the existing service.
                    report_dir = folder / "pages" / f"{t['job_id']}-{t['page']}"
                    if not report_dir.exists():
                        create_review(Path(job["folder"]), report_dir, (t["page"],))
                    if not all(
                        (report_dir / name).is_file()
                        for name in ("manifest.json", "comparison.html", "comparison.json")
                    ):
                        raise ValueError("INCOMPLETE_REPORT_REQUIRES_NEW_RUN")
                    if (
                        read(report_dir / "manifest.json")["source_document_sha256"]
                        != job["hashes"]["document.json"]
                    ):
                        raise ValueError("REPORT_SOURCE_CHANGED")
                    cache[key] = (packet, routes, report_dir)
                packet, routes, report_dir = cache[key]
                cells = [
                    c.model_dump(mode="json") for c in packet.cells if c.table_id == t["table_id"]
                ]
                records.append(
                    {
                        "profile": t,
                        "cells": cells,
                        "routes": [routes[c["cell_id"]] for c in cells],
                        "report": (report_dir / "comparison.html").relative_to(folder).as_posix(),
                        "physical_sha256": packet.physical_sha256,
                        "semantic_verified": False,
                    }
                )
            return {
                "tables": records,
                "rule_version": spec["code_sha256"],
                "new_rule_generated": False,
            }

        def specialists():
            from doc_understanding.review import error_basket
            from doc_understanding.agents.workflows.table_review.service import execute

            jobs = {j["job_id"]: j for j in spec["jobs"] + read(folder / "processing.json")["jobs"]}
            results = []
            total = 0
            data = folder / "specialists"
            for table in read(folder / "candidates.json")["tables"]:
                texts = {c["cell_id"]: c["text"] for c in table["cells"]}
                for route in table["routes"]:
                    if (
                        route["route"] != "llm"
                        or not texts[route["cell_id"]]
                        or total >= spec["max_llm_cells"]
                    ):
                        continue
                    total += 1
                    job = jobs[table["profile"]["job_id"]]
                    ident = digest({"campaign": cid, "cell": route["cell_id"]})[:32]
                    receipt = folder / ("dispatch-" + ident + ".json")
                    if receipt.exists():
                        results.append(read(receipt))
                        continue
                    # Persist before any external call: interrupted dispatches never auto-repeat paid work.
                    result = {
                        "cell_id": route["cell_id"],
                        "status": "interrupted_or_running",
                        "request_id": ident,
                    }
                    save(receipt, result)
                    try:
                        destination = data / job["job_id"]
                        destination.mkdir(parents=True, exist_ok=True)
                        verify_job(job)
                        for name in job["hashes"]:
                            target = destination / name
                            if not target.exists():
                                shutil.copyfile(Path(job["folder"]) / name, target)
                            if file_hash(target) != job["hashes"][name]:
                                raise ValueError("COPIED_SOURCE_CHANGED")
                        raw = read(destination / "document.json")
                        req = error_basket.CollectionRequest(
                            page=table["profile"]["page"],
                            scope="cell",
                            table_id=table["profile"]["table_id"],
                            cell_id=route["cell_id"],
                            error_type="other",
                            note="구조 다양성 캠페인: 규칙 근거 부족. 오류 확정 아님.",
                        )
                        item = error_basket.collect(
                            data / "error-basket",
                            destination,
                            {
                                "id": job["job_id"],
                                "name": "구조 표본",
                                "pages": len(raw["document_ir"]["pages"]),
                                "status": "done",
                            },
                            req,
                        )
                        record = error_basket.detail(data / "error-basket", item["id"])
                        config_path = Path(spec["data"]) / "review-agent-config.json"
                        if config_path.exists():
                            record["review_model"] = read(config_path).get(
                                "model", "gpt-4.1-2025-04-14"
                            )
                        outcome = execute(
                            record,
                            data,
                            lambda s: store.event(cid, "specialist", s),
                            ident,
                            budget_data=Path(spec["data"]),
                        )
                        result.update(
                            status="completed" if outcome.get("review_complete") else "blocked",
                            routing_summary=outcome.get("routing_summary"),
                            api_calls=outcome.get("api_calls", 0),
                            errors=outcome.get("validation_errors", []),
                        )
                    except Exception as exc:
                        result.update(status="blocked", error=type(exc).__name__)
                    save(receipt, result)
                    results.append(result)
                    if result["status"] != "completed":
                        return {"items": results, "stop_reason": "SPECIALIST_INCOMPLETE"}
            return {
                "items": results,
                "stop_reason": "CELL_BUDGET_REACHED" if total else "NO_DISPATCH",
            }

        def validation():
            check_freeze(project)
            for job in spec["jobs"] + read(folder / "processing.json")["jobs"]:
                verify_job(job)
            packets = read(folder / "candidates.json")["tables"]
            if not packets:
                raise ValueError("NO_REVIEWABLE_TABLES")
            routes = [r for p in packets for r in p["routes"]]
            completed = {
                r["cell_id"]
                for r in read(folder / "specialists.json")["items"]
                if r["status"] == "completed"
            }
            return {
                "status": "human_review_pending_partial"
                if any(r["route"] != "rule" and r["cell_id"] not in completed for r in routes)
                else "human_review_pending",
                "selected_tables": len(packets),
                "selected_cells": len(routes),
                "rule_candidates": sum(r["route"] == "rule" for r in routes),
                "llm_needed": sum(r["route"] == "llm" for r in routes),
                "blocked_cells": sum(r["route"] == "blocked" for r in routes),
                "llm_completed": len(completed),
                "accuracy_score": None,
                "reference_confirmed": False,
                "parser_changed": False,
                "holdout_evaluated": False,
                "regression_accuracy_measured": False,
            }

        operations = [
            ("inventory", inventory),
            ("collection", collection),
            ("processing", processing),
            ("selection", selection),
            ("candidates", candidates),
            ("specialists", specialists),
            ("validation", validation),
        ]
        builder = StateGraph(State)
        previous = START
        for name, operation in operations:
            builder.add_node(name, stage(name, operation))
            builder.add_edge(previous, name)
            previous = name
        builder.add_edge(previous, END)
        try:
            builder.compile().invoke({}, config={"recursion_limit": 20})
            from .report import render

            render(folder)
            status = read(state_path)
            status["status"] = read(folder / "validation.json")["status"]
            save(state_path, status)
            with store.db() as db:
                db.execute("UPDATE campaigns SET status='paused' WHERE id=?", (cid,))
            store.event(cid, "supervisor", status["status"])
            return read(folder / "validation.json")
        except Exception as exc:
            status = read(state_path)
            status.update(status="blocked", error=type(exc).__name__)
            save(state_path, status)
            store.event(cid, "supervisor", "blocked:" + type(exc).__name__)
            raise
