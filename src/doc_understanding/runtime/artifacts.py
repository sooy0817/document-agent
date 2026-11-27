"""runtime / artifacts."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def read(path):
    return json.loads(path.read_text(encoding="utf8"))
