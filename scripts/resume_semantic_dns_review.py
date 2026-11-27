"""Resume the single DNS-failed review using its archived source, append-only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    folder = project / "work/semantic-campaigns/be1b76e50a914579a74516f45f071800"
    retry = folder / "dns-resume-01"
    spec = read(folder / "spec.json")
    archive = folder / "execution-source.zip"
    metadata = read(folder / "execution-source.json")
    if sha(archive) != metadata["archive_sha256"]:
        raise ValueError("ARCHIVE_CHANGED")
    with zipfile.ZipFile(archive) as z:
        hashes = {n: hashlib.sha256(z.read(n)).hexdigest() for n in sorted(z.namelist())}
        identity = hashlib.sha256(json.dumps(hashes, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if identity != spec["code_sha256"]:
            raise ValueError("ARCHIVED_CODE_CHANGED")
        for name in z.namelist():
            if not (retry / "source" / name).resolve().is_relative_to((retry / "source").resolve()):
                raise ValueError("UNSAFE_ARCHIVE_PATH")
        if not (retry / "source").exists():
            z.extractall(retry / "source")
    for name, expected in hashes.items():
        if sha(retry / "source" / name) != expected:
            raise ValueError("RETRY_SOURCE_CHANGED")
    os.environ["NH_DOCUMENT_ROOT"] = str(project)
    sys.path.insert(0, str(retry / "source/src"))
    from doc_understanding.runtime import artifacts, credentials, settings
    from doc_understanding.agents.workflows.semantic_campaign.service import check_freeze, verify_job
    from doc_understanding.agents.workflows.campaign.locking import campaign_lock
    from doc_understanding.agents.workflows.table_review import graph, profiles, service, version

    check_freeze(project)
    for job in spec["jobs"]:
        verify_job(job)
    state = read(folder / "state.json")
    for stage, expected in state["completed"].items():
        if sha(folder / (stage + ".json")) != expected:
            raise ValueError("COMPLETED_STAGE_CHANGED")
    items = read(folder / "specialists.json")["items"]
    if len(items) != 1 or items[0]["errors"] != ["DNS_RESOLUTION_FAILED", "MODEL_REVIEW_INCOMPLETE"]:
        raise ValueError("EXPECTED_SINGLE_DNS_FAILURE")
    original = folder / "specialists/review-queue/roles-loop" / items[0]["request_id"]
    records = list(original.glob("batch-*/*-started.json"))
    if len(records) != 1:
        raise ValueError("UNEXPECTED_PREVIOUS_CALLS")
    failure = read(original / "batch-000/infer_relations.json")
    if failure.get("error_code") != "DNS_RESOLUTION_FAILED" or failure.get("output") or failure.get("usage"):
        raise ValueError("CALL_NOT_SAFE_TO_RETRY")
    record = read(original / "request.json")
    graph.plan_review(record, folder / "specialists")
    print(json.dumps({"preflight": "passed", "completed_stages_reused": len(state["completed"]),
                      "completed_model_calls": 0, "failed_calls_to_retry": 1}), flush=True)
    if not args.execute:
        return
    with campaign_lock(folder / "run.lock"), campaign_lock(retry / "run.lock"):
        if (retry / "summary.json").exists():
            print(json.dumps(read(retry / "summary.json")), flush=True)
            return
        before = {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob("*")
                  if p.is_file() and not p.is_relative_to(retry) and p.name != "run.lock"}
        try:
            with urlopen("https://api.openai.com/v1/models", timeout=20) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        if status != 401 and not 200 <= status < 300:
            raise ValueError("CONNECTIVITY_FAILED")
        config = settings.configured(Path(spec["data"]))
        if not config["enabled"]:
            raise ValueError("MODEL_REVIEW_DISABLED")
        key = credentials.model_key()
        if not key:
            raise ValueError("API_KEY_UNAVAILABLE")
        calls = profiles.model_calls(Path(spec["data"]), key=key, daily_limit=config["daily_limit"])
        calls.model = failure["model"]
        calls.version = service.VERSION + "/" + version.VERSION
        result = graph.run_review(record, folder / "specialists", retry / "review", calls=calls,
                                  progress=lambda stage: print(stage, flush=True))
        for name, expected in before.items():
            if sha(folder / name) != expected:
                raise ValueError("ORIGINAL_ARTIFACT_CHANGED:" + name)
        check_freeze(project)
        summary = {"http_status": status, "source_code_sha256": identity,
                   "original_files_unchanged": len(before), "completed_stages_reused": len(state["completed"]),
                   "api_calls": result["api_calls"], "estimated_cost_usd": result["estimated_cost_usd"],
                   "review_complete": result["review_complete"], "routing_summary": result["routing_summary"],
                   "validation": result["result"], "parser_changed": False, "human_review_pending": True}
        artifacts.save(retry / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
