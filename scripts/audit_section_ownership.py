"""Reproduce the supplied size-selected section sample against its source manifest."""
import argparse
import json
from pathlib import Path

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.export.clean import clean_document


def audit(manifest, sample):
    entries = json.loads(manifest.read_bytes())
    supplied = json.loads(sample.read_bytes())
    smallest = sorted(entries, key=lambda e: Path(e["document_path"]).stat().st_size)[:len(supplied)]
    if [e["document_id"] for e in smallest] != [e["document_id"] for e in supplied]:
        raise ValueError("The supplied sample differs from the size-selected source documents")
    expected = {e["document_id"]: e for e in supplied}
    rows = []
    for entry in entries:
        data = Path(entry["document_path"]).read_bytes()
        doc = json.loads(data)
        tables = [c for c in doc["contents"] if c["kind"] == "table"]
        missing = [c for c in tables if c["section_id"] is None]
        first = min((s["reading_order"] for s in doc["sections"]), default=None)
        row = dict(id=doc["document_id"], pages=len(doc["document_ir"]["pages"]),
                   sample=doc["document_id"] in expected, tables=len(tables), orphan=len(missing),
                   no_headings=len(missing) if first is None else 0,
                   before_first=sum(first is not None and c["reading_order"] < first for c in missing))
        row["other"] = len(missing) - row["no_headings"] - row["before_first"]
        if doc["document_id"] != entry["document_id"] or len(tables) != len(doc["physical_tables"]):
            raise ValueError(f"Document or table count discrepancy in {entry['document_path']}")
        if row["sample"]:
            recorded = expected[row["id"]]
            if (len(data), len(tables), len(missing), len(doc["physical_tables"])) != (
                recorded["bytes"], recorded["tables"], recorded["tables_without_section"], recorded["physical_tables"]
            ):
                raise ValueError(f"Sample counts differ for {row['id']}")
            public = clean_document(CanonicalDocument.model_validate_json(data))
            actual = {i["table_id"] for p in public["pages"] for i in p["items"]
                      if i["kind"] == "table" and i.get("section_id") is None}
            numbers = {t["table_id"]: n for n, t in enumerate(doc["physical_tables"], 1)}
            if actual != {numbers[c["table_id"]] for c in missing}:
                raise ValueError(f"Public membership differs for {row['id']}")
        rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("manifest", "sample", "out"):
        parser.add_argument(field, type=Path)
    args = parser.parse_args()
    result = audit(args.manifest, args.sample)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf8")
    for name, rows in (("sample", [r for r in result if r["sample"]]), ("all", result)):
        print(name, len(rows), {k: sum(r[k] for r in rows)
                                for k in ("tables", "orphan", "no_headings", "before_first", "other")})
