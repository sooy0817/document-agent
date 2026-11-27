"""Compare semantic candidates over frozen physical IR, without running a parser.

Only existing page-continuation candidates are considered. Detached-header work
is deliberately excluded. Nothing here approves a reference or changes a job.
"""

import base64
import hashlib
import html
import json
from pathlib import Path
from typing import Literal

from doc_understanding.domain.models import EvidenceModel
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.relations.logical_tables import LogicalTableLink, validate_logical_links
from doc_understanding.tools.semantic_candidates import continuation_links, generate


class PathView(EvidenceModel):
    row_labels: tuple[str, ...] = ()
    column_labels: tuple[str, ...] = ()
    row_source_ids: tuple[str, ...] = ()
    column_source_ids: tuple[str, ...] = ()
    state: Literal['observed', 'inferred', 'unresolved'] = 'unresolved'
    reasons: tuple[str, ...] = ()


class CellComparison(EvidenceModel):
    cell_id: str
    table_id: str
    page: int
    row: int | None
    column: int | None
    text: str | None
    baseline: PathView
    candidate: PathView | None = None
    changed: bool = False


class SemanticPathPacket(EvidenceModel):
    schema_version: Literal['semantic-path-review/1.0'] = 'semantic-path-review/1.0'
    document_id: str
    physical_sha256: str
    pages: tuple[int, ...]
    links: tuple[LogicalTableLink, ...]
    cells: tuple[CellComparison, ...]
    counts: dict[str, int]
    semantic_verified: Literal[False] = False
    accuracy_score: None = None
    physical_modified: Literal[False] = False
    deferred_link_kinds: tuple[Literal['detached_header'], ...] = ('detached_header',)


def physical_digest(doc):
    raw = [t.model_dump(mode='json') for t in doc.physical_tables]
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_packet(doc: CanonicalDocument, pages: tuple[int, ...]) -> SemanticPathPacket:
    if doc.document_ir is None:
        raise ValueError('MISSING_DOCUMENT_IR')
    selected = tuple(sorted(set(pages)))
    if not selected or not set(selected) <= {p.page_number for p in doc.document_ir.pages}:
        raise ValueError('PAGE_NOT_FOUND')
    cells = {c.cell_id: c for t in doc.physical_tables for c in t.cells}
    if len(cells) != sum(len(t.cells) for t in doc.physical_tables):
        raise ValueError('DUPLICATE_SOURCE_CELL')
    validate_logical_links(doc)
    saved_paths = {p.cell_id: p for link in doc.logical_table_links for p in link.paths}
    selected_links = continuation_links(doc, selected)
    target_ids = [c.cell_id for t in doc.physical_tables if t.page_number in selected for c in t.cells]
    proposed = {c.cell_id: c for c in generate(doc.model_dump(mode='json'), target_ids)
                if c.basis != 'unavailable' and 'DEFERRED_FRAGMENT' not in c.reasons}
    structured = {t.table_id: t for t in doc.table_structure}
    comparisons = []
    for table in doc.physical_tables:
        if table.page_number not in selected:
            continue
        st = structured[table.table_id]
        nodes = {n.node_id: n for n in st.row_tree + st.column_tree}
        mappings = {m.cell_id: m for m in st.cell_mappings}
        for cell in table.cells:
            baseline = PathView()
            if cell.cell_id in mappings:
                m = mappings[cell.cell_id]
                baseline = PathView(
                    row_labels=tuple(nodes[n].label_raw or '' for n in m.row_path),
                    column_labels=tuple(nodes[n].label_raw or '' for n in m.column_path),
                    row_source_ids=tuple(nodes[n].source_cell_id for n in m.row_path),
                    column_source_ids=tuple(nodes[n].source_cell_id for n in m.column_path),
                    state=m.evidence_state,
                )
            if cell.cell_id in saved_paths:
                saved = saved_paths[cell.cell_id]
                baseline = PathView(
                    row_labels=tuple(cells[cid].original_text or '' for cid in saved.row_source_cell_ids),
                    column_labels=tuple(cells[cid].original_text or '' for cid in saved.column_source_cell_ids),
                    row_source_ids=saved.row_source_cell_ids,
                    column_source_ids=saved.column_source_cell_ids,
                    state=saved.state, reasons=saved.reasons,
                )
            candidate = None
            if cell.cell_id in proposed:
                p = proposed[cell.cell_id]
                reasons = tuple('UNRESOLVED_PHYSICAL_CONTEXT' if r == 'PHYSICAL_CONTEXT_UNRESOLVED' else r
                                for r in p.reasons)
                candidate = PathView(
                    row_labels=tuple(cells[cid].original_text or '' for cid in p.row_ids),
                    column_labels=tuple(cells[cid].original_text or '' for cid in p.column_ids),
                    row_source_ids=p.row_ids, column_source_ids=p.column_ids,
                    state='unresolved' if reasons or not p.row_ids or not p.column_ids else 'inferred',
                    reasons=reasons,
                )
            changed = candidate is not None and (
                baseline.row_source_ids != candidate.row_source_ids
                or baseline.column_source_ids != candidate.column_source_ids)
            comparisons.append(CellComparison(
                cell_id=cell.cell_id, table_id=table.table_id, page=table.page_number,
                row=cell.row_index, column=cell.column_index, text=cell.original_text,
                baseline=baseline, candidate=candidate, changed=changed))
    paired = [r for r in comparisons if r.candidate is not None]
    counts = dict(
        source_cells=len(comparisons), candidate_cells=len(paired),
        changed_paths=sum(r.changed for r in comparisons),
        baseline_both_axes=sum(bool(r.baseline.row_source_ids and r.baseline.column_source_ids)
                               for r in paired),
        candidate_both_axes=sum(bool(r.candidate.row_source_ids and r.candidate.column_source_ids)
                                for r in paired),
        candidate_unresolved=sum(r.candidate.state == 'unresolved' for r in paired),
    )
    return SemanticPathPacket(document_id=doc.document_id, physical_sha256=physical_digest(doc),
                              pages=selected, links=selected_links, cells=tuple(comparisons), counts=counts)


def render_html(packet: SemanticPathPacket, images: dict[int, str]) -> str:
    def esc(value):
        return html.escape(str(value if value is not None else ''))

    def path(view):
        if view is None:
            return '새 연결 후보 없음'
        row = ' → '.join(view.row_labels) or '미연결'
        column = ' → '.join(view.column_labels) or '미연결'
        ids = '\n'.join(view.row_source_ids + view.column_source_ids)
        state = '불확실' if view.state == 'unresolved' else '추론 후보'
        return (f'<b>행</b> {esc(row)}<br><b>열</b> {esc(column)}<br><small>{state}</small>'
                f'<details><summary>원본 셀 근거</summary><pre>{esc(ids)}</pre></details>')

    rows = ''.join(
        f'<tr><td>{r.page}쪽 · 행 {(r.row or 0)+1} · 열 {(r.column or 0)+1}'
        f'<br><small>{esc(r.cell_id.rsplit("/",1)[-1])}</small></td>'
        f'<td><details><summary>{esc((r.text or "")[:90])}</summary>{esc(r.text)}</details></td>'
        f'<td>{path(r.baseline)}</td><td>{path(r.candidate)}</td></tr>'
        for r in packet.cells)
    sources = ''.join(f'<details><summary>원본 {p}쪽</summary><img alt="원본 {p}쪽" '
                      f'src="{esc(uri)}"></details>' for p, uri in sorted(images.items()))
    c = packet.counts
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>행·열 의미 경로 비교</title>
<style>body{{font:15px/1.65 system-ui,sans-serif;margin:32px;color:#20332e;background:#f7faf8}}
main{{max-width:1500px;margin:auto}}h1{{font-size:27px}}section{{background:white;padding:22px;margin:20px 0;border:1px solid #ccd9d2;border-radius:10px}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}th,td{{padding:14px;vertical-align:top;border-bottom:1px solid #d7e0dc;overflow-wrap:anywhere;white-space:pre-line}}th{{text-align:left;background:#e8f2ed}}th:first-child{{width:12%}}th:nth-child(2){{width:24%}}small{{color:#60756a}}summary{{cursor:pointer}}pre{{white-space:pre-wrap;font-size:11px}}img{{max-width:100%;height:auto}}.notice{{border-left:4px solid #ce9b37;padding:12px;background:#fff6df}}</style>
<main><h1>행·열 의미 경로 비교</h1><p class="notice">검토용 후보입니다. 사람 검증 전이며 파서·원본·기존 저장 결과는 변경하지 않았습니다.</p>
<section><h2>이번 비교 범위: {', '.join(map(str,packet.pages))}쪽</h2>
<p>물리 표를 재추출하지 않고 기존 페이지 연결 로직으로 의미 경로를 다시 구성했습니다. 분리 헤더 연결은 이번 범위에서 제외했습니다.</p>
<p>전체 원본 셀 {c['source_cells']}개 · 후보 제공 {c['candidate_cells']}개 · 경로 변경 {c['changed_paths']}개</p>
<p>같은 후보 대상 셀 중 행·열 경로가 모두 있는 셀: 기존 {c['baseline_both_axes']}개 → 후보 {c['candidate_both_axes']}개. 후보 불확실 {c['candidate_unresolved']}개.</p>
<p><strong>위 수치는 연결 범위이며 정확도 점수가 아닙니다.</strong> 모든 원본 셀을 포함하므로 헤더·분류 셀도 집계됩니다. 정답 reference와의 일치도, 잘못된 연결 비율은 아직 채점하지 않았습니다.</p>
<p>물리 IR SHA-256: <code>{packet.physical_sha256}</code></p></section>
<section><h2>원본 근거</h2>{sources}</section>
<section><h2>셀별 비교</h2><p>행은 상위 그룹부터 세부 항목까지, 열은 해당 값의 열 제목까지 확인합니다. 빈칸과 중복 원문은 그대로 보존합니다.</p>
<table><thead><tr><th>위치</th><th>원문</th><th>기존 저장 경로</th><th>새 의미 연결 후보</th></tr></thead><tbody>{rows}</tbody></table></section></main></html>'''


def create_review(source_folder: Path, output: Path, pages: tuple[int, ...]):
    raw = (source_folder / 'document.json').read_bytes()
    doc = CanonicalDocument.model_validate_json(raw)
    pdf = (source_folder / 'input.pdf').read_bytes()
    if hashlib.sha256(pdf).hexdigest() != doc.document_ir.source_sha256:
        raise ValueError('SOURCE_HASH_MISMATCH')
    packet = build_packet(doc, pages)
    context_pages = set(packet.pages)
    tables = {t.table_id: t for t in doc.physical_tables}
    context_pages.update(tables[link.source_table_id].page_number for link in packet.links)
    import pymupdf
    images = {}
    with pymupdf.open(stream=pdf, filetype='pdf') as source:
        for p in sorted(context_pages):
            png = source[p-1].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).tobytes('png')
            images[p] = 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
    if (source_folder / 'document.json').read_bytes() != raw:
        raise ValueError('SNAPSHOT_CHANGED_DURING_REVIEW')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'comparison.json').write_text(packet.model_dump_json(indent=2), encoding='utf8')
    (output / 'comparison.html').write_text(render_html(packet, images), encoding='utf8')
    from doc_understanding.parser.relations import logical_tables
    from doc_understanding.tools import semantic_candidates
    code = [Path(__file__), Path(logical_tables.__file__), Path(semantic_candidates.__file__)]
    manifest = dict(source_document_sha256=hashlib.sha256(raw).hexdigest(),
                    source_pdf_sha256=hashlib.sha256(pdf).hexdigest(),
                    physical_sha256=packet.physical_sha256, pages=packet.pages,
                    code_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in code},
                    semantic_verified=False, physical_modified=False, model_calls=0)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    return packet


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_folder', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--pages', type=int, nargs='+', required=True)
    args = parser.parse_args()
    packet = create_review(args.source_folder, args.output, tuple(args.pages))
    print(json.dumps(packet.counts))


if __name__ == '__main__':
    main()
