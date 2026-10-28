import hashlib
import json
import re
from pathlib import Path


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def sanitized(text):
    text = text or ""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.\w+|(?:[A-Za-z]:\\|https?://)\S+", "[PRIVATE]", text)
    text = re.sub(r"\b\d{2,3}[- ]\d{3,4}[- ]\d{4}\b", "[CONTACT]", text)
    # Keep dates/years as context; amounts, percentages and inline values are masked.
    return re.sub(
        r"(?P<period>\d{4}[./-]\d{1,2}[./-]\d{1,2}|\b(?:19|20)\d{2}(?=년|\([A-Z]\)))|[-+△▲]?\d[\d,.]*",
        lambda m: m[0] if m.group("period") else "[VALUE]",
        text,
    )


def prepare(evidence):
    registry = {f"E{i:04d}": e.evidence_id for i, e in enumerate(evidence.items)}
    short = {original: sid for sid, original in registry.items()}
    source_short = {e.source_id: short[e.evidence_id] for e in evidence.items}
    groups = {a.artifact_id: f"T{i}" for i, a in enumerate(evidence.artifacts)}
    packet = {
        "mode": evidence.mode,
        "untrusted_document_content": True,
        "items": [
            dict(
                id=short[e.evidence_id],
                region=groups[e.artifact_id],
                page=e.page,
                kind=e.kind,
                text=sanitized(e.text_raw),
                bbox=e.bbox,
                row=e.row_index,
                column=e.column_index,
                rowspan=e.rowspan,
                colspan=e.colspan,
                occupied_slots=e.occupied_slots,
                physical_state=e.physical_state,
            )
            for e in evidence.items
        ],
        "available_context": [sanitized(t) for t in evidence.available_context],
        "missing_context": [sanitized(t) for t in evidence.missing_context],
        "candidates": [],
    }
    candidate_hashes = []
    for a in evidence.candidates:
        if a.kind != "structured":
            raise ValueError("UNSUPPORTED_CANDIDATE_ADAPTER")
        raw = Path(a.uri).read_bytes()
        if hashlib.sha256(raw).hexdigest() != a.sha256:
            raise ValueError("CANDIDATE_HASH_MISMATCH")
        data = json.loads(raw)
        candidate_hashes.append(a.sha256)
        nodes = data["row_tree"] + data["column_tree"]
        ids = {n["node_id"]: f"C{len(packet['candidates'])}-{i}" for i, n in enumerate(nodes)}
        packet["candidates"].append(
            [
                dict(
                    id=ids[n["node_id"]],
                    axis=n["axis"],
                    evidence=source_short.get(n["source_cell_id"]),
                    parent=ids.get(n["parent_id"]),
                    alternative_parents=[ids[x] for x in n["candidate_parent_ids"] if x in ids],
                    state=n["evidence_state"],
                )
                for n in nodes
            ]
        )
    source_hash = digest(
        {
            "artifacts": [a.model_dump(mode="json") for a in evidence.artifacts],
            "items": [e.model_dump(mode="json") for e in evidence.items],
        }
    )
    return packet, registry, source_hash, candidate_hashes
