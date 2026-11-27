# 역할: 수정 전후의 셀·경로·원문·기존 중첩 연결 보존을 비교한다. 의미 정확도 점수는 아니다.

def signature(doc):
    """Deterministic structural delta, never a semantic correctness score."""
    cells = {c["cell_id"]: c for t in doc["physical_tables"] for c in t["cells"]}
    paths = {
        m["cell_id"]: [m.get("row_path"), m.get("column_path"), m.get("evidence_state")]
        for t in doc["table_structure"]
        for m in t["cell_mappings"]
    }
    spans = {
        s["span_id"]: s.get("text", s.get("text_raw"))
        for p in doc["document_ir"]["pages"]
        for b in p.get("blocks", [])
        for s in b.get("spans", [])
    }
    return dict(cells=cells, paths=paths, spans=spans)


def delta(before, after):
    a, b = signature(before), signature(after)
    ids = set(a["cells"]) | set(b["cells"])
    changed = [
        i
        for i in sorted(ids)
        if a["cells"].get(i) != b["cells"].get(i) or a["paths"].get(i) != b["paths"].get(i)
    ]
    return dict(
        before_cells=len(a["cells"]),
        after_cells=len(b["cells"]),
        changed_cell_ids=changed,
        native_text_preserved=a["spans"] == b["spans"],
        removed_cells=sorted(set(a["cells"]) - set(b["cells"])),
        added_cells=sorted(set(b["cells"]) - set(a["cells"])),
        semantic_accuracy_verified=False,
    )


def preservation(before, after, old_links, new_links):
    a,b=signature(before),signature(after)
    # Exclude only the enclosing run hash; all accepted parent items/order remain compared.
    new={c['parent_cell_id']:c for c in new_links['cells']}
    changed=[c['parent_cell_id'] for c in old_links['cells'] if c['containment_status']=='linked' and c!=new.get(c['parent_cell_id'])]
    return dict(physical_cells_unchanged=a['cells']==b['cells'],
                semantic_paths_unchanged=a['paths']==b['paths'], native_text_preserved=a['spans']==b['spans'],
                changed_accepted_parent_ids=changed,
                passed=a==b and not changed)
