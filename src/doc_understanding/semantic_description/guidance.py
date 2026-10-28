"""Read-only supplementary source-review guidance; never a human verdict."""
import hashlib
import json


def supplement(root, case_id, target_id):
    path = root / "reports/semantic-priority-review/supplement.json"
    if not path.exists():
        return None
    item = next(
        (x for x in json.loads(path.read_text(encoding="utf8"))["items"]
         if x["case_id"] == case_id and x["description_id"] == target_id), None
    )
    if item is None:
        return None
    return dict(item, supplement_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
