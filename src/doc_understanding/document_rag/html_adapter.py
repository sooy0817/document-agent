"""Bounded STATIC HTML DOM adapter into existing Document/Physical/Table contracts.

All coordinates are synthetic layout units expressed in the existing bbox space;
the explicit issue forbids treating them as browser-rendered evidence. Source HTML
bytes and DOM-order IDs remain authoritative. No scripts, network or browser code.
"""
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

import pymupdf

from doc_understanding.domain.models import (
    ArtifactRef, BBox, BlockIR, CellIR, DocumentIR, ExtractionRun, Issue,
    PageIR, RasterRef, SourceRef, TableCandidateIR, TextSpanIR,
)
from doc_understanding.staging.layout import project
from doc_understanding.staging.prepare import write_json
from doc_understanding.table_understanding.physical_models import (
    GridBand, GridPolicy, PhysicalCell, PhysicalTableIR, SourceCellLink,
)
from doc_understanding.table_understanding.semantic import SemanticTableAnalyzer


class StaticParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.text = [], []
        self.text_role = 'text'
        self.table, self.row, self.cell = None, None, None
        self.ignore = 0
        self.depth = 0
        self.caption = []
        self.in_caption = False
        self.issues = []

    def flush(self):
        text = ' '.join(''.join(self.text).split())
        if text:
            self.events.append((self.text_role, text))
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.ignore += 1
        if self.ignore:
            return
        if tag == 'table':
            self.depth += 1
            if self.depth > 1:
                raise ValueError('NESTED_HTML_TABLE_REQUIRES_REVIEW')
            self.flush()
            self.table = []
            return
        if self.table is not None:
            if tag == 'caption':
                self.in_caption = True
                self.caption = []
            if tag == 'tr':
                self.row = []
            if tag in ('td', 'th'):
                if self.row is None:
                    raise ValueError('HTML_CELL_WITHOUT_ROW')
                attrs = dict(attrs)
                rs, cs = int(attrs.get('rowspan', 1)), int(attrs.get('colspan', 1))
                if not 1 <= rs <= 100 or not 1 <= cs <= 100:
                    raise ValueError('HTML_SPAN_LIMIT')
                self.cell = dict(text=[], rowspan=rs, colspan=cs, header=tag == 'th')
            if tag == 'br' and self.cell is not None:
                self.cell['text'].append('\n')
        elif tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'br', 'hr'):
            self.flush()
            if tag in ('h1','h2','h3','h4','h5','h6'):
                self.text_role = tag

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.ignore = max(0, self.ignore-1)
            return
        if self.ignore:
            return
        if self.table is not None:
            if tag in ('td', 'th') and self.cell is not None:
                self.cell['text'] = ' '.join(''.join(self.cell['text']).split())
                self.row.append(self.cell)
                self.cell = None
            elif tag == 'caption':
                self.in_caption = False
                self.events.append(('text', ' '.join(''.join(self.caption).split())))
            elif tag == 'tr' and self.row is not None:
                if self.row:
                    self.table.append(self.row)
                self.row = None
                if len(self.table) > 500:
                    raise ValueError('HTML_ROW_LIMIT')
            elif tag == 'table':
                self.events.append(('table', self.table))
                self.table = None
                self.depth = 0
        elif tag in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'):
            self.flush()
            if tag in ('h1','h2','h3','h4','h5','h6'):
                self.text_role = 'text'

    def handle_data(self, data):
        if self.ignore:
            return
        if self.in_caption:
            self.caption.append(data)
        elif self.cell is not None:
            self.cell['text'].append(data)
        elif self.table is None:
            self.text.append(data)


def html_grid(rows):
    occupied, records = {}, []
    for ri, row in enumerate(rows):
        ci = 0
        for item in row:
            while (ri, ci) in occupied:
                ci += 1
            if ci + item['colspan'] > 100 or ri + item['rowspan'] > len(rows):
                raise ValueError('HTML_GRID_BOUNDS')
            slots = tuple((r, c) for r in range(ri, ri+item['rowspan'])
                          for c in range(ci, ci+item['colspan']))
            if any(s in occupied for s in slots):
                raise ValueError('HTML_SPAN_OVERLAP')
            record = dict(**item, row=ri, column=ci, slots=slots)
            records.append(record)
            for s in slots:
                occupied[s] = len(records)-1
            ci += item['colspan']
    cols = max((c for _, c in occupied), default=-1)+1
    if not cols or len(rows)*cols > 20000:
        raise ValueError('EMPTY_OR_OVERSIZED_HTML_GRID')
    # Holes are unavailable cells, not fabricated blanks.
    return records, occupied, cols


def prepare_html(source: Path, output: Path):
    from time import perf_counter
    start = perf_counter()
    raw = source.read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError('HTML_BYTE_LIMIT')
    text = raw.decode('utf-8-sig')
    parser = StaticParser()
    parser.feed(text)
    parser.close()
    parser.flush()
    if parser.table is not None:
        raise ValueError('UNCLOSED_HTML_TABLE')
    digest = hashlib.sha256(raw).hexdigest()
    did = 'sha256:' + digest
    run_info = ExtractionRun(parser_version='static-html/0.1', engine='stdlib.HTMLParser',
                             engine_version='python3', policy_version='static-html/0.1')
    run = Path(output) / str(run_info.run_id)
    run.mkdir(parents=True, exist_ok=False)
    (run / 'source.html').write_bytes(raw)
    origin = source.with_suffix(source.suffix+'.origin.json')
    if origin.exists():
        (run/'source-origin.json').write_bytes(origin.read_bytes())
    (run / 'physical').mkdir()
    pid = did + '/p0001'
    def ref(box, **kwargs):
        return SourceRef(document_id=did, page_number=1, bbox=box, **kwargs)
    blocks, candidates, physical, hints = [], [], [], []
    y = 0
    for ei, (kind, content) in enumerate(parser.events):
        bid = f'{pid}/dom{ei:05d}'
        if kind != 'table':
            box = BBox(x0=0, y0=y, x1=1000, y1=y+20)
            sid = bid + '/s0'
            span = TextSpanIR(span_id=sid, source=ref(box, block_id=bid, span_id=sid),
                              text_raw=content, line_index=0, font='html-dom', font_size=12, flags=0)
            blocks.append(BlockIR(block_id=bid, kind='text', source=ref(box, block_id=bid),
                                   extraction_order=len(blocks), text_raw=content, spans=(span,),
                                   extraction_method=f'static_html_dom_{kind}_synthetic_coordinates'))
            y += 24
            continue
        records, occupied, cols = html_grid(content)
        header_count = 0
        for row in content:
            if row and all(c['header'] for c in row):
                header_count += 1
            else:
                break
        hints.append((header_count,1) if 0 < header_count < len(content) and cols > 1 else None)
        tid = f'{pid}/physical{len(physical):03d}'
        candidate_id = f'{pid}/table{len(physical):03d}'
        box = BBox(x0=0, y0=y, x1=1000, y1=y+len(content)*24)
        rawcells, pcells = [], []
        cw = 1000 / cols
        for ci, item in enumerate(records):
            cb = BBox(x0=item['column']*cw, y0=y+item['row']*24,
                      x1=(item['column']+item['colspan'])*cw,
                      y1=y+(item['row']+item['rowspan'])*24)
            cbid = bid + f'/cell{ci:05d}'
            sid = cbid + '/s0'
            cid = candidate_id + f'/c{ci:05d}'
            pcid = tid + f'/cell{ci:05d}'
            span = TextSpanIR(span_id=sid, source=ref(cb, block_id=cbid, span_id=sid),
                              text_raw=item['text'], line_index=0, font='html-dom', font_size=12,
                              flags=16 if item['header'] else 0)
            blocks.append(BlockIR(block_id=cbid, kind='text', source=ref(cb, block_id=cbid),
                                   extraction_order=len(blocks), text_raw=item['text'], spans=(span,),
                                   extraction_method='static_html_dom_synthetic_coordinates'))
            rawcells.append(CellIR(cell_id=cid, source=ref(cb, cell_id=cid),
                                    row_index=item['row'], column_index=item['column'],
                                    rowspan=item['rowspan'], colspan=item['colspan'], text_raw=item['text'],
                                    span_refs=(sid,), extraction_status='observed' if item['text'] else 'blank'))
            pcells.append(PhysicalCell(cell_id=pcid, row_index=item['row'], column_index=item['column'],
                                       rowspan=item['rowspan'], colspan=item['colspan'], original_text=item['text'],
                                       source_bbox=cb, source=ref(cb, cell_id=pcid), source_cell_ids=(cid,),
                                       span_refs=(sid,), extraction_method='unresolved', status='inferred',
                                       occupied_slots=item['slots']))
        grid = tuple(tuple(rawcells[occupied[(r,c)]].cell_id if (r,c) in occupied else None
                           for c in range(cols)) for r in range(len(content)))
        candidates.append(TableCandidateIR(table_candidate_id=candidate_id, source=ref(box),
                                            cells=tuple(rawcells), detector_grid=grid,
                                            row_count=len(content), column_count=cols))
        blocks.append(BlockIR(block_id=bid, kind='table_candidate', source=ref(box, block_id=bid),
                               extraction_order=len(blocks), table_ref=candidate_id,
                               extraction_method='static_html_dom_synthetic_coordinates'))
        physical.append(PhysicalTableIR(table_id=tid, document_id=did, analysis_run_id=uuid4(),
                                         analyzer_version='static-html-dom/0.1', policy=GridPolicy(),
                                         source_ir_sha256=digest, page_number=1, bounding_box=box,
                                         rows=tuple(GridBand(index=r, start=y+r*24, end=y+(r+1)*24) for r in range(len(content))),
                                         columns=tuple(GridBand(index=c, start=c*cw, end=(c+1)*cw) for c in range(cols)),
                                         cells=tuple(pcells), source_cells=tuple(rawcells),
                                         source_links=tuple(SourceCellLink(source_cell_id=a.cell_id,
                                                                           reconstructed_cell_id=b.cell_id, relation='exact')
                                                            for a,b in zip(rawcells, pcells, strict=True)),
                                         table_candidate_ids=(candidate_id,), page_refs=(1,),
                                         source_references=(ref(box),), lines=(), intersections=(),
                                         boundaries=(), unresolved_boundaries=(), analysis_state='candidate'))
        y += len(content)*24+24
    if len(blocks) > 30000 or y > 100000:
        raise ValueError('HTML_LAYOUT_LIMIT')
    # Explicit synthetic placeholder raster. Not a screenshot or extracted PDF page.
    with pymupdf.open() as image_doc:
        image_page = image_doc.new_page(width=500, height=100)
        image_page.insert_text((10,30), 'STATIC HTML DOM - SYNTHETIC LAYOUT, NOT A BROWSER SCREENSHOT', fontsize=9)
        image_raw = image_page.get_pixmap().tobytes('png')
    (run / 'page-dom.png').write_bytes(image_raw)
    page_box = BBox(x0=0, y0=0, x1=1000, y1=max(100,y))
    image_ref = RasterRef(artifact=ArtifactRef(path='page-dom.png',sha256=hashlib.sha256(image_raw).hexdigest(), media_type='image/png'),
                          width_pixels=500, height_pixels=100, dpi=72,
                          page_to_pixel=(.5,0,0,100/max(100,y),0,0), clip=page_box)
    issue = Issue(code='SYNTHETIC_HTML_LAYOUT_NOT_RENDERED', severity='warning',
                  message='Static DOM order; all bboxes and page 1 are synthetic, not browser/page coordinates. Original HTML bytes are authoritative.')
    page = PageIR(page_id=pid, page_number=1, width=1000, height=max(100,y), original_rotation=0,
                   original_cropbox=page_box, evidence_sha256=digest, image=image_ref,
                   blocks=tuple(blocks), tables=tuple(candidates), text_status='native', issues=(issue,))
    ir = DocumentIR(document_id=did, source_name=source.name, source_sha256=digest,
                     source=ArtifactRef(path='source.html', sha256=digest, media_type='text/html'),
                     pages=(page,), run=run_info, processing_status='review_required', issues=(issue,))
    write_json(run / 'document-ir.json', ir.model_dump(mode='json'))
    artifacts = [dict(id='document-ir',kind='DocumentIR',path=str((run/'document-ir.json').resolve()),page_id=None)]
    links = []
    for i,t in enumerate(physical):
        hint = hints[i]
        st = SemanticTableAnalyzer().analyze(t, header_rows=hint[0], stub_columns=hint[1]) if hint else SemanticTableAnalyzer().analyze(t)
        for aid, kind, name, value in (
            (t.table_id, 'PhysicalTableIR', f'page-001-table-{i:03d}.json', t),
            (t.table_id+':structured', 'StructuredTableIR', f'page-001-table-{i:03d}-structured.json', st)):
            path = run / 'physical' / name
            write_json(path, value.model_dump(mode='json'))
            artifacts.append(dict(id=aid,kind=kind,path=str(path.resolve()),page_id=pid))
            links.append(dict(source=pid,target=aid,kind='table_artifact',reason='Static HTML DOM table; synthetic geometry and unvalidated semantic roles.'))
    layout = project(ir, (run/'document-ir.json').read_bytes(), links)
    write_json(run/'document-layout.json', layout.model_dump(mode='json'))
    artifacts.append(dict(id='layout',kind='DocumentLayoutProjection',path=str((run/'document-layout.json').resolve()),page_id=None))
    manifest = dict(source=str(source.resolve()),source_sha256=digest,run_dir=str(run.resolve()),
                    seconds=perf_counter()-start,status=layout.processing_status,pages=1,blocks=len(blocks),
                    artifacts=artifacts, disk_bytes=sum(p.stat().st_size for p in run.rglob('*') if p.is_file()))
    write_json(run/'staging-manifest.json', manifest)
    return run, manifest


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--html',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    print(prepare_html(a.html,a.output)[0])
