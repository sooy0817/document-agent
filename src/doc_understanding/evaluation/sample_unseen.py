"""Metadata-only stratified selection. Never opens PDFs or calls models.

Usage: python -m doc_understanding.evaluation.sample_unseen inventory.json freeze.json output.json
"""

import argparse
import hashlib
import json
import random
import re
from collections import defaultdict
from pathlib import Path


def select(inventory, freeze, seed=20261019, count=20):
    if not freeze.get("files") or not freeze.get("model_versions") or not freeze.get("settings"):
        raise ValueError("Freeze code, settings and model versions before sampling")
    rows = sorted(inventory, key=lambda r: r["document_id"])
    if len({r["document_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate document_id")
    required = (
        "document_id",
        "institution_id",
        "institution_name",
        "period",
        "document_type",
        "format_family",
        "native_text_group",
        "source_path",
        "sha256",
        "duplicate_cluster",
    )
    for r in rows:
        if any(not r.get(k) for k in required) or not re.fullmatch("[0-9a-f]{64}", r["sha256"]):
            raise ValueError("Incomplete metadata or missing duplicate review cluster")
    # Union leakage components across institution-period, byte hash and near-duplicate clusters.
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, r in enumerate(rows):
        keys = [
            ("period", r["institution_id"], r["period"]),
            ("hash", r["sha256"]),
            ("cluster", r["duplicate_cluster"]),
        ]
        for key in keys:
            if key in seen:
                parent[find(i)] = find(seen[key])
            seen[key] = i
    excluded_names = tuple(freeze.get("excluded_institution_names", []))
    excluded_ids = set(freeze.get("excluded_institution_ids", []))
    blocked = {
        find(i)
        for i, r in enumerate(rows)
        if r["institution_id"] in excluded_ids
        or any(n in r["institution_name"] for n in excluded_names)
        or r.get("prior_exposure", False)
    }
    rng = random.Random(seed)
    strata = defaultdict(list)
    for i, r in enumerate(rows):
        if find(i) in blocked:
            continue
        strata[
            (r["period"], r["document_type"], r["format_family"], r["native_text_group"])
        ].append(i)
    for key in sorted(strata):
        rng.shuffle(strata[key])
    keys = sorted(strata)
    rng.shuffle(keys)
    selected = []
    used_groups = set()
    used_institutions = set()
    while len(selected) < count:
        grew = False
        for key in keys:
            while strata[key]:
                i = strata[key].pop()
                r = rows[i]
                if find(i) in used_groups or r["institution_id"] in used_institutions:
                    continue
                selected.append(
                    {
                        **r,
                        "leakage_group": min(
                            rows[j]["document_id"] for j in range(len(rows)) if find(j) == find(i)
                        ),
                    }
                )
                used_groups.add(find(i))
                used_institutions.add(r["institution_id"])
                grew = True
                break
            if len(selected) >= count:
                break
        if not grew:
            break
    def canonical(v):
        return hashlib.sha256(
            json.dumps(v, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    return {
        "schema_version": "unseen-selection/0.1",
        "seed": seed,
        "requested": count,
        "selected_count": len(selected),
        "shortfall": max(0, count - len(selected)),
        "inventory_sha256": canonical(rows),
        "freeze_sha256": canonical(freeze),
        "selected": selected,
        "sampling": "seeded balanced round-robin strata; one institution and leakage component per selected document",
        "pdf_hash_verification": "provided metadata only; verify bytes before evaluation",
        "executed": False,
        "metrics": None,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inventory", type=Path)
    p.add_argument("freeze", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--seed", type=int, default=20261019)
    p.add_argument("--count", type=int, default=20)
    a = p.parse_args()
    if a.count < 1:
        p.error("count must be positive")
    result = select(
        json.loads(a.inventory.read_text(encoding="utf-8")),
        json.loads(a.freeze.read_text(encoding="utf-8")),
        a.seed,
        a.count,
    )
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
