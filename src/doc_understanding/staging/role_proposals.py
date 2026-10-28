"""Offline, source-line anchored role proposals. Never modifies extraction IR."""
import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Literal

from doc_understanding.domain.models import BBox, EvidenceModel


class RoleProposal(EvidenceModel):
    id: str
    page: int
    block_id: str
    span_ids: tuple[str, ...]
    bbox: BBox
    text_raw: str
    role: Literal['heading', 'paragraph', 'table', 'unit', 'note', 'unknown']
    state: Literal['inferred', 'unresolved']
    reason: str
    target_candidates: tuple[str, ...] = ()
    relation: str | None = None
    relation_state: Literal['inferred', 'unresolved'] = 'unresolved'
    scope_state: Literal['unresolved'] = 'unresolved'
    structured_refs: tuple[str, ...] = ()
    review: Literal['unreviewed'] = 'unreviewed'


def classify(text, size, typical_size):
    if re.match(r'^\s*\(?\s*(단위|unit)\s*[:：]', text, re.I):
        return 'unit', 'inferred', '명시적인 단위 표기'
    if re.match(r'^\s*(주\s*\)|주석|※|note\b|footnote\b)', text, re.I):
        return 'note', 'inferred', '명시적인 주석 표기'
    if size > typical_size * 1.2 and len(text) < 100:
        return 'heading', 'inferred', '주변 텍스트보다 큰 글자와 짧은 줄'
    return 'paragraph', 'unresolved', '본문 후보이나 제목·비고와의 의미적 구분은 미확정'


def overlap(a, b):
    return max(0, min(a['x1'], b['x1']) - max(a['x0'], b['x0']))


def propose(bundle, pages):
    artifacts = bundle['artifacts']
    ir = artifacts['document-ir']['payload']
    layout = next(a['payload'] for a in artifacts.values()
                  if a['payload'].get('schema_version') == 'document-layout/0.1')
    duplicates = {r['source'] for r in layout['relations'] if r['kind'] == 'text_overlaps_table'}
    results = []
    for page in ir['pages']:
        if page['page_number'] not in pages:
            continue
        sizes = [s['font_size'] for b in page['blocks'] if b['block_id'] not in duplicates
                 for s in b.get('spans', []) for _ in s['text_raw'].strip()]
        typical = median(sizes) if sizes else 12
        tables = [b for b in page['blocks'] if b['kind'] == 'table_candidate']
        for block in page['blocks']:
            if block['kind'] not in ('text', 'table_candidate') or block['block_id'] in duplicates:
                continue
            lines = {}
            for span in block.get('spans', []):
                lines.setdefault(span['line_index'], []).append(span)
            if block['kind'] == 'table_candidate' or not lines:
                lines = {None: []}
            for line, spans in lines.items():
                boxes = [s['source']['bbox'] for s in spans] or [block['source']['bbox']]
                box = {k: f(b[k] for b in boxes) for k, f in
                       [('x0', min), ('y0', min), ('x1', max), ('y1', max)]}
                text = ''.join(s['text_raw'] for s in spans) if spans else (block['text_raw'] or '')
                role, state, reason = (('table', 'inferred', '기존 표 영역 탐지 후보')
                    if block['kind'] == 'table_candidate' else classify(
                        text, max((s['font_size'] for s in spans), default=0), typical))
                candidates = []
                for table in tables:
                    tb = table['source']['bbox']
                    distance = box['y0'] - tb['y1'] if role == 'note' else tb['y0'] - box['y1']
                    if role in ('heading', 'unit', 'note') and 0 <= distance <= 100 and overlap(box, tb) > 0:
                        candidates.append(table['block_id'])
                # Multiple nearby tables stay alternatives; never select by page number/title.
                refs = []
                if role == 'table':
                    for key, a in artifacts.items():
                        p = a['payload']
                        if p.get('table_id') and 'row_tree' in p:
                            physical = artifacts.get(p['table_id'], {}).get('payload', {})
                            pb = physical.get('bounding_box')
                            if isinstance(pb, dict) and physical.get('page_number', physical.get('page')) == page['page_number']:
                                if overlap(box, pb) > 0 and min(box['y1'], pb['y1']) > max(box['y0'], pb['y0']):
                                    refs.append(a['source_path'])
                results.append(RoleProposal(
                    id=block['block_id'] + (f'/line{line}' if line is not None else '/role'),
                    page=page['page_number'], block_id=block['block_id'],
                    span_ids=tuple(s['span_id'] for s in spans), bbox=BBox(**box), text_raw=text,
                    role=role, state=state, reason=reason,
                    target_candidates=tuple(candidates),
                    relation={'heading':'titles', 'unit':'unit_context', 'note':'note_context'}.get(role),
                    relation_state='inferred' if len(candidates) == 1 else 'unresolved',
                    structured_refs=tuple(refs)))
    return sorted(results, key=lambda r: (r.page, r.bbox.y0, r.bbox.x0, r.id))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--pages', type=int, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.bundle.read_bytes()
    bundle = json.loads(raw)
    proposals = propose(bundle, args.pages)
    args.output.mkdir(parents=True, exist_ok=True)
    full = {'schema_version':'document-role-proposals/0.1', 'method':'offline_heuristic',
            'source_bundle':str(args.bundle.resolve()), 'source_sha256':sha256(raw).hexdigest(),
            'reading_order_state':'unresolved', 'fact_publication_allowed':False,
            'items':[p.model_dump(mode='json') for p in proposals]}
    roles = dict(heading='제목', paragraph='본문', table='표', unit='단위', note='주석', unknown='미분류')
    aliases = {p.id: f'R{i:02}' for i, p in enumerate(proposals, 1)}
    block_alias = {p.block_id:aliases[p.id] for p in proposals if p.role == 'table'}
    review = {'안내':'원본 PDF와 비교해 판단에 맞음·수정·보류를 입력하세요. 수정할 때만 수정내용을 적으세요. 저장만으로 Gold 확정·운영 발행하지 않습니다.',
              '원본_PDF':bundle['source_pdf'], '검토자':'',
              '주의':'표 내부 중복 텍스트는 기존 Layout 참조로 생략했습니다. 원본은 유지됩니다. 배열 순서는 좌표 기반 후보입니다.',
              '검토':[{ '번호':aliases[p.id], '페이지':p.page, '시스템_분류':roles[p.role],
                 '원문':p.text_raw or '(표: 기존 StructuredTableIR 참조)',
                 '위치':p.bbox.model_dump(), '제안_이유':p.reason,
                 '연결_표':[block_alias.get(t,t) for t in p.target_candidates],
                 '연결_상태':p.relation_state, '세부_행열_적용범위':'unresolved',
                 '질문':f'이 부분을 {roles[p.role]}로 분류한 것이 맞나요?' + (' 표시된 표와 연결하는 것이 맞나요?' if p.target_candidates else ''),
                 '판단':None, '수정내용':'', '원본_제안_ID':p.id,
                 'StructuredTableIR':list(p.structured_refs)} for p in proposals]}
    for name, payload in [('proposals.json',full),('review.json',review)]:
        dest = args.output / name
        if dest.exists():
            raise FileExistsError('기존 검토 파일 보존: 새로운 output 경로를 사용하세요.')
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
