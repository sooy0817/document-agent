"""Local JSON and review UI. Text and IDs are HTML escaped."""

import html
import json
from collections import Counter
from pathlib import Path
from .engine import resolve_document
from .models import SemanticOverlay, SemanticPatchProposal, ResolverRequest, ResolverConfig


def save_run(document, physical, structured, output_root, **options):
    overlay, requests = resolve_document(document, physical, structured, **options)
    run = Path(output_root) / str(overlay.run_id)
    run.mkdir(parents=True)
    (run / "semantic-overlay.json").write_text(overlay.model_dump_json(indent=2), encoding="utf8")
    (run / "structured-before.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in structured], ensure_ascii=False, indent=2),
        encoding="utf8",
    )
    for i, r in enumerate(requests):
        (run / f"request-{i:03d}.json").write_text(r.model_dump_json(indent=2), encoding="utf8")
    summary = dict(
        document_id=overlay.document_id,
        status=overlay.status,
        mode=overlay.mode,
        accounting_scope=overlay.accounting_scope,
        pages=len(document.pages),
        physical_tables=len(physical),
        table_nodes=len(overlay.table_nodes),
        document_nodes=len(overlay.document_nodes),
        candidate_relations=len(overlay.candidates),
        decisions=dict(Counter(d.validation_status for d in overlay.decisions)),
        relation_types=dict(Counter(c.relation_type for c in overlay.candidates)),
        node_states=dict(
            Counter(n.validation_status for n in overlay.table_nodes + overlay.document_nodes)
        ),
        rejected_reasons=dict(
            Counter(
                r for d in overlay.decisions if d.validation_status == "rejected" for r in d.reasons
            )
        ),
        source_cell_count=len(overlay.source_cells),
        source_mutations=0,
        calls=[c.model_dump(mode="json") for c in overlay.calls],
        prepared_requests=len(requests),
        escalations=overlay.escalations,
        independent_review="pending",
        semantic_accuracy=None,
    )
    (run / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8"
    )
    features = {f.node_id: f for f in overlay.features}

    def esc(x):
        return html.escape(str(x))

    parts = [
        "<!doctype html><meta charset='utf-8'><title>Semantic Overlay review</title><style>body{font:15px system-ui;margin:30px;color:#16253a}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ddd;vertical-align:top}pre{white-space:pre-wrap}small{color:#765000}</style>",
        "<h1>Phase 2-C1 · Semantic Overlay</h1><p>Provisional · 독립 사람 검토 대기. accepted는 기계적 허용이며 의미 정답 인증이 아닙니다.</p>",
        f"<pre>{esc(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>",
        '<p><a href="semantic-overlay.json">Overlay JSON</a> · <a href="structured-before.json">기존 Structured IR</a></p>',
    ]
    for title, nodes in [
        ("Table Semantic Hierarchy", overlay.table_nodes),
        ("Document Semantic Hierarchy", overlay.document_nodes),
    ]:
        parts.append(
            f"<h2>{title}</h2><table><tr><th>원문·페이지</th><th>기존 물리 부모</th><th>의미 후보 경로/관계</th><th>상태·사유</th></tr>"
        )
        for n in nodes:
            f = features[n.semantic_node_id]
            label = (
                features[n.physical_parent_id].text_raw if n.physical_parent_id in features else "—"
            )
            path = " → ".join(features[i].text_raw for i in n.hierarchy_path if i in features)
            if (
                n.relation_type
                and n.relation_type != "parent_child"
                and n.semantic_parent_id in features
            ):
                path = f"{features[n.semantic_parent_id].text_raw} [{n.relation_type}] {f.text_raw}"
            parts.append(
                f"<tr><td>p{f.page_number} {esc(f.text_raw)}<br><small>{esc(f.panel_id)}</small></td><td>{esc(label)}</td><td>{esc(path)}</td><td>{n.validation_status}<br>{esc(n.unresolved_reason)}</td></tr>"
            )
        parts.append("</table>")
    parts.append(
        "<details><summary>모든 후보·충돌·출처</summary><pre>"
        + esc(
            json.dumps(
                [c.model_dump(mode="json") for c in overlay.candidates],
                ensure_ascii=False,
                indent=2,
            )
        )
        + "</pre></details>"
    )
    (run / "index.html").write_text("\n".join(parts), encoding="utf8")
    return run, summary


def export_schemas(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, model in [
        ("semantic-overlay", SemanticOverlay),
        ("semantic-patch-proposal", SemanticPatchProposal),
        ("text-resolver-request", ResolverRequest),
        ("text-resolver-config", ResolverConfig),
    ]:
        (folder / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf8"
        )
