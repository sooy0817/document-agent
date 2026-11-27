"""tools / evidence_expansion."""

import json

import fitz


def expand(packet, images, requests, index, folder):
    """Tools are bounded, read-only, validated against actual IDs/pages."""
    packet = {
        **packet,
        "evidence": list(packet["evidence"]),
        "omitted_ids": list(packet.get("omitted_ids", [])),
    }
    images = list(images)
    results = []
    evidence = {e["id"]: e for e in packet["evidence"]}

    def add(sid, kind, text, **extra):
        if sid in evidence:
            return
        if len(text) > 14000 or len(evidence) >= 420:
            if sid not in packet["omitted_ids"]:
                packet["omitted_ids"].append(sid)
            return
        e = dict(id=sid, kind=kind, text=text, **extra)
        evidence[sid] = e
        packet["evidence"].append(e)

    page = packet["selection"]["page"]
    for q in requests[:2]:
        result = dict(request=q, status="unknown", source_refs=[])
        if q["tool"] == "lookup_relation":
            result.update(index.lookup(q["relation"], q["source_id"], q["target_id"]))
        elif q["tool"] == "read_source":
            sid = q["source_id"]
            e = evidence.get(sid) or index.sources.get(sid)
            if e:
                result.update(status="found", source_refs=[sid], source=e)
                add(
                    sid,
                    e.get("kind", "cell"),
                    e.get("text", ""),
                    **{k: v for k, v in e.items() if k not in ("id", "kind", "text")},
                )
                if e.get("kind") == "table":
                    cells = [(i, v) for i, v in index.sources.items() if v.get("table_id") == sid]
                    result["omitted_ids"] = [i for i, _ in cells[80:]]
                    for i, v in cells[:80]:
                        add(
                            i,
                            v["kind"],
                            v.get("text", ""),
                            **{k: x for k, x in v.items() if k not in ("kind", "text")},
                        )
                    result["source_refs"] += [i for i, _ in cells[:80]]
            else:
                result["reason"] = "SOURCE_ID_NOT_FOUND"
        else:
            pn = q["page"]
            if pn is None or abs(pn - page) > 1:
                result["reason"] = "PAGE_OUTSIDE_ALLOWED_CONTEXT"
            else:
                p = next(
                    (
                        p
                        for p in index.doc.get("document_ir", {}).get("pages", [])
                        if p["page_number"] == pn
                    ),
                    None,
                )
                if p:
                    spans = [s for b in p.get("blocks", []) for s in b.get("spans", [])]
                    for s in spans[:100]:
                        add(s["span_id"], "span", s["text_raw"], page=pn, bbox=s.get("bbox"))
                    result.update(
                        status="found",
                        source_refs=[s["span_id"] for s in spans[:100]],
                        omitted_ids=[s["span_id"] for s in spans[100:]],
                    )
                    with fitz.open(folder / "input.pdf") as pdf:
                        if 1 <= pn <= len(pdf):
                            image_id = index.doc["document_id"] + f"/review-full-page-{pn}"
                            p = pdf[pn - 1]
                            add(
                                image_id,
                                "page_region",
                                "",
                                page=pn,
                                bbox=dict(zip(("x0", "y0", "x1", "y1"), list(p.rect))),
                            )
                            if not any(i["source_id"] == image_id for i in images):
                                images.append(
                                    dict(
                                        source_id=image_id,
                                        bytes=p.get_pixmap(matrix=fitz.Matrix(1.7, 1.7)).tobytes(
                                            "png"
                                        ),
                                    )
                                )
                else:
                    result["reason"] = "PAGE_NOT_IN_DOCUMENT_IR"
        results.append(result)
    packet["tool_results"] = results
    if len(json.dumps(packet, ensure_ascii=False).encode()) > 200000:
        return None, images, results
    return packet, images, results
