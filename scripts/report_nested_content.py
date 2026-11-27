"""Evaluate local source-order references and reuse the baseline static report style."""
import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import re

import pymupdf


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def write_json(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def rect(b):
    return pymupdf.Rect(b['x0'], b['y0'], b['x1'], b['y1'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--baseline-report', type=Path, required=True)
    parser.add_argument('--report-stem', default='comparison')
    args = parser.parse_args()
    out = args.run
    reference = read(args.reference)
    assert hashlib.sha256(args.pdf.read_bytes()).hexdigest() == reference['source_sha256']
    summary = read(out/'summary.json')
    links = read(out/'canonical-document.nested-content.json')
    doc = read(out/'canonical-document.json')
    evaluation_name = args.report_stem+'.evaluation.json'
    linked = sum(c['status'] == 'geometrically_linked' for c in links['cells'])
    unresolved = len(links['cells'])-linked
    unresolved_candidates = sum(any(r != 'STRICT_BBOX_CONTAINMENT' for r in c['reasons'])
                                for c in links['candidates'])
    css = re.search(r'<style>(.*?)</style>', args.baseline_report.read_text(encoding='utf8'), re.S)[1]
    parts = ['<!doctype html><html lang="ko"><meta charset="utf-8">',
             '<title>LH 조건부 중첩 연결 검증</title><style>'+css+
             'pre{white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}.pair{display:flex;gap:12px}.pair figure{width:50%;margin:0}.pair img{width:100%}</style>',
             '<main><h1>LH 50쪽 · 후보가 있을 때만 중첩 연결</h1>',
             '<p>실제 PDF를 새로 추출하고 S0 physical → semantic → Canonical → 조건부 연결을 실행했습니다. '
             '기존 계약은 그대로이며 연결은 별도 JSON입니다. 아래 세 사례는 개발 자료 원본 검토 결과이며 범용 정확도/의미 검증 점수가 아닙니다.</p>',
             '<p><a href="canonical-document.nested-content.json">연결 JSON 전체</a> · '
             '<a href="canonical-document.json">Canonical 전체</a> · <a href="summary.json">실행 요약</a> · '
             f'<a href="{escape(evaluation_name)}">원본 순서 대조</a></p>',
             '<table><tr><th>항목</th><th>실행 결과</th></tr>']
    for key, value in [
        ('전체 실행', f"{summary['pages']}쪽 · Physical {summary['physical_tables']}표 / {summary['physical_cells']}셀 · mapping {summary['mappings']}개"),
        ('추가 처리 조건', f"{len(summary['pages_routed'])}쪽에서 {summary['candidate_count']}개 후보 / {summary['pages_skipped']}쪽 건너뜀"),
        ('결과 범위', f"{len(links['cells'])}개 바깥 셀: 기하 연결 {linked}, 보류 {unresolved}. {len(links['candidates'])}개 후보 중 {unresolved_candidates}개는 미해결 후보로 보존"),
        ('기존 결과 보존', f"{summary['protected_files']}개 파일 SHA-256 확인, 변경 {len(summary['protected_files_changed'])}개"),
        ('기준선 비교', 'Native block/table, Physical 셀, Structured Tree, Canonical 본문·매핑 일치 (run UUID와 결합 해시는 비교에서 제외)'),
        ('실행 시간', f"추출 {summary['native_seconds']:.2f}초, 구조·조립 {summary['physical_semantic_assembly_seconds']:.2f}초, 연결 {summary['conditional_link_seconds']:.2f}초. 전체 저장·검증 {summary['elapsed_seconds']:.2f}초"),
        ('외부 작업', 'LLM/API 0회 · DB 쓰기 0회 · 임베딩/검색 연동은 이번 범위 밖')]:
        parts.append(f'<tr><td>{escape(key)}</td><td>{escape(value)}</td></tr>')
    parts.append('</table><p>기하 연결은 셀 소속과 로컬 읽기 순서의 후보입니다. 내부 표 의미 관계의 승인, 문장 의미 해석, 페이지 간 연결을 뜻하지 않습니다. 후보 없는 페이지 역시 중첩이 없다는 보장은 아닙니다.</p>')
    results = []
    pdf = pymupdf.open(args.pdf)
    for case in reference['cases']:
        number = case['page']
        prefix = doc['document_id']+f'/p{number:04}/'
        parent_id = prefix+case['parent_suffix']
        cell = next(c for c in links['cells'] if c['parent_cell_id'] == parent_id)
        native = doc['document_ir']['pages'][number-1]
        parent = next(c for t in native['tables'] for c in t['cells'] if c['cell_id'] == parent_id)
        span_map = {s['span_id']: s['text_raw'] for b in native['blocks'] for s in b['spans']}
        expected = [prefix+x for x in case['sequence_suffixes']]
        actual = [i['ref_id'] for i in cell['items']]
        child_id = prefix+case['child_suffix']
        table = next(t for t in doc['physical_tables'] if t['table_id'] == child_id)
        semantics = next(t for t in doc['table_structure'] if t['table_id'] == child_id)
        represented = {s for i in cell['items'] for s in i['span_ids']}
        whitespace = set(cell['ignored_whitespace_span_ids'])
        result = dict(page=number, parent_cell_id=parent_id, child_table_id=child_id,
                      containment_matches_source_review=child_id in cell['child_table_ids'],
                      sequence_matches_source_review=actual == expected,
                      expected=expected, actual=actual,
                      parent_span_count=len(parent['span_refs']), represented_spans=len(represented),
                      whitespace_spans=len(whitespace), uncovered=cell['uncovered_span_ids'],
                      duplicate=cell['duplicate_span_ids'], outside=cell['outside_parent_span_ids'],
                      coverage_complete=set(parent['span_refs']) == represented | whitespace,
                      geometric_status=cell['status'],
                      child_semantic_issues=[i['code'] for i in semantics['issues']],
                      child_semantic_ambiguities=sorted({i['code'] for i in semantics['ambiguities']}),
                      child_analysis_state=semantics['analysis_state'],
                      child_mapping_count=len(semantics['cell_mappings']),
                      semantic_approval=False, scope_note=case['scope_note'])
        results.append(result)
        page = pdf[number-1]
        if not (out/f'source-p{number:03}.png').exists():
            page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(out/f'source-p{number:03}.png')
        overlay = pymupdf.open()
        overlay.insert_pdf(pdf, from_page=number-1, to_page=number-1)
        op = overlay[0]
        op.draw_rect(rect(parent['source']['bbox']), color=(0, .3, 1), width=2)
        for index, item in enumerate(cell['items'], 1):
            color = (0, .6, .2) if item['kind'] == 'table' else (.9, .45, 0)
            b = rect(item['bbox'])
            op.draw_rect(b, color=color, width=.7)
            op.insert_text((b.x0-12, b.y0+6), str(index), fontsize=6, color=color)
        if not (out/f'overlay-p{number:03}.png').exists():
            op.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(out/f'overlay-p{number:03}.png')
        overlay.close()
        suffix = case['parent_suffix'].replace('/', '-')
        parts.extend([f'<h2>{number}쪽 · {escape(case["visual_sequence"])}</h2>',
                      f'<p>포함 관계: {result["containment_matches_source_review"]} / 전체 순서: {result["sequence_matches_source_review"]} / '
                      f'parent span {len(parent["span_refs"])}개 = 표현 {len(represented)} + 공백 {len(whitespace)}. '
                      f'누락/중복/외부 span {len(cell["uncovered_span_ids"])}/{len(cell["duplicate_span_ids"])}/{len(cell["outside_parent_span_ids"])}.</p>',
                      f'<p>{escape(case["scope_note"])}</p>',
                      f'<p><a href="cells/p{number:03}-{suffix}.json">바깥 셀 → 순서 → 내부 Physical/Structured 값을 한 번에 조회한 실제 JSON</a> · '
                      f'<a href="pages/p{number:03}-physical.json">Physical 전체</a> · '
                      f'<a href="pages/p{number:03}-structured.json">원시 Structured 전체</a></p>',
                      f'<div class="pair"><figure><figcaption>원본 PDF</figcaption><a href="source-p{number:03}.png"><img src="source-p{number:03}.png"></a></figure>'
                      f'<figure><figcaption>파랑: 바깥 셀 / 초록: 내부 표 / 주황: 문장 순서</figcaption><a href="overlay-p{number:03}.png"><img src="overlay-p{number:03}.png"></a></figure></div>',
                      '<table><tr><th>순서 / 기존 ID</th><th>원문 또는 내부 표 참조</th></tr>'])
        content_map = {c['content_id']: c for c in doc['contents']}
        for index, item in enumerate(cell['items'], 1):
            if item['kind'] == 'content':
                text = content_map[item['ref_id']]['text_raw'] or ''
            else:
                text = f"내부 표: {len(table['cells'])}셀 / {len(semantics['cell_mappings'])} value mapping. 고유 Tree 유지. "
                text += '의미 상태 '+result['child_analysis_state']+' / 이슈·보류: '+', '.join(
                    sorted(set(result['child_semantic_issues']+result['child_semantic_ambiguities'])))
            text = re.sub(r'\b0\d{1,2}[)\-]\d{3,4}-\d{4}\b', '[연락처 생략]', text)
            parts.append(f'<tr><td>{index}. <code>{escape(item["ref_id"].removeprefix(prefix))}</code></td><td>{escape(text)}</td></tr>')
        parts.append('</table><details><summary>span별 소속 확인 (원문은 원본 JSON에서 보존)</summary><table><tr><th>span ID</th><th>항목</th></tr>')
        for sid in parent['span_refs']:
            owners = [str(i+1) for i, item in enumerate(cell['items']) if sid in item['span_ids']]
            label = ', '.join(owners) or ('whitespace' if sid in whitespace else 'UNRESOLVED')
            parts.append(f'<tr><td>{escape(sid.removeprefix(prefix))}</td><td>{escape(label)}</td></tr>')
        parts.append('</table></details>')
        # Full original values remain in hydrated JSON, never an expected-answer injection.
        assert all(s in span_map for s in parent['span_refs'])
    parts.append('<h2>전체 후보 보류 목록</h2><p>Native 후보와 Physical 후보를 함께 검사하여 이전 bbox 화면보다 넓게 수집합니다. '
                 '원본에 실제 중첩이 있는지 추가 검토되지 않은 후보를 성공으로 집계하지 않습니다.</p>'
                 '<table><tr><th>쪽 / child ID</th><th>선택된 바깥 셀</th><th>근거·보류 원인</th></tr>')
    for c in links['candidates']:
        parts.append('<tr><td>'+str(c['page_number'])+' / '+escape(c['child_ref_id'].split('/')[-1])+
                     '</td><td>'+escape('/'.join((c['selected_parent_id'] or 'unknown').split('/')[-2:]))+
                     '</td><td>'+escape(', '.join(c['reasons']))+'</td></tr>')
    parts.append('</table><h2>보존과 적용 범위</h2><p>기존 Canonical JSON의 읽기 순서는 그대로 두고, 셀 내부 순서만 연결 JSON으로 추가합니다. '
                 'build CLI는 동반 JSON을 생성하며 link-nested CLI는 기존 Canonical에 적용할 수 있습니다. '
                 '부모 셀은 Native ID이고 바깥 Physical 셀 복원은 새로 수행하지 않습니다. '
                 '범용 중첩 탐지, cross-page 부모 연결, 청킹·임베딩·RAG 소비 연동은 아직 구현하지 않았습니다.</p></main></html>')
    write_json(out/evaluation_name, dict(reference=str(args.reference), split='development',
                                          basis=reference['review_basis'], results=results))
    with (out/(args.report_stem+'.html')).open('x', encoding='utf8') as stream:
        stream.write(''.join(parts))
    print(json.dumps(dict(reviewed=len(results), sequence_matches=sum(r['sequence_matches_source_review'] for r in results),
                          coverage_complete=sum(r['coverage_complete'] for r in results))))


if __name__ == '__main__':
    main()
