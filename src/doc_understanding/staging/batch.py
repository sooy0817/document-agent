"""Explicit development manifest only; no automatic holdout discovery."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from .prepare import prepare, write_json


def run(manifest_path, output):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["split"] != "development" or len(manifest["documents"]) != 5:
        raise ValueError("EXPLICIT_FIVE_DEVELOPMENT_DOCUMENTS_REQUIRED")
    if len({d["sha256"] for d in manifest["documents"]}) != 5:
        raise ValueError("DUPLICATE_SOURCE")
    for item in manifest["documents"]:
        if item["split"] != "development" or "남평" in item["source"]:
            raise ValueError("HOLDOUT_NOT_ALLOWED")
    output.mkdir(parents=True, exist_ok=True)
    for item in manifest["documents"]:
        if item["split"] != "development" or "남평" in item["source"]:
            raise ValueError("HOLDOUT_NOT_ALLOWED")
        target = output / (item["id"] + ".json")
        if target.exists():
            continue
        start = perf_counter()
        source = Path(item["source"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("SOURCE_CHANGED")
        try:
            if item.get("existing_manifest"):
                run_dir = Path(item["existing_manifest"]).parent
                mode = "existing_full_extraction_reverified"
            else:
                run_dir, _ = prepare(source, output / item["id"])
                mode = "fresh_full_extraction"
            result = {"id": item["id"], "manifest": str((run_dir / "staging-manifest.json").resolve()),
                      "mode": mode, "status": "prepared", "seconds": perf_counter() - start}
        except Exception as exc:
            result = {"id": item["id"], "status": "failed", "error_type": type(exc).__name__,
                      "error": str(exc), "seconds": perf_counter() - start}
        write_json(target, result)
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.output)
