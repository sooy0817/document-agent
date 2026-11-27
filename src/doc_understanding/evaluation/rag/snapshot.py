"""평가 당시의 문항·정답 ID·설정을 고정한다. 재채점에서 현재 DB를 참조하지 않는다."""
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_snapshot(folder, data, items, resolved, *, provenance):
    references = {}
    for item in items:
        r = resolved[item.id]
        references[item.id] = dict(r, gold=sorted(r["gold"]), alternative=sorted(r["alternative"]),
                                   gold_groups=[sorted(group) for group in r["gold_groups"]],
                                   alternative_groups=[sorted(group) for group in r.get("alternative_groups", [])])
    payload = dict(schema_version=1, dataset=data.model_dump(), dataset_sha256=digest(data.model_dump()),
                   item_ids=[i.id for i in items], resolved=references, provenance=provenance)
    envelope = dict(payload=payload, sha256=digest(payload))
    path = Path(folder) / "evaluation-snapshot.json"
    # 기존 실행의 기준을 덮어쓰지 않는다.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(envelope, stream, ensure_ascii=False, indent=1)
    return envelope["sha256"]


def read_snapshot(folder, data):
    path = Path(folder) / "evaluation-snapshot.json"
    if not path.is_file():
        raise ValueError("EVALUATION_SNAPSHOT_REQUIRED: original anchors are unavailable; current corpus is not a substitute")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    if envelope["sha256"] != digest(payload):
        raise ValueError("EVALUATION_SNAPSHOT_CHANGED")
    if (payload["schema_version"] != 1 or payload["dataset_sha256"] != digest(payload["dataset"])
            or type(data).model_validate(payload["dataset"]) != data):
        raise ValueError("EVALUATION_DATASET_CHANGED")
    return payload
