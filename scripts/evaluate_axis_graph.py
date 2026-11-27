"""Reproduce graph regression on existing source-reviewed targets; no live API.

Gold is read AFTER graph invocations and is never a graph/provider input.
Existing development and previously inspected holdouts are all regression now.
"""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re

from run_axis_graph import ROOT, read, run, write, source_image


def norm(s):
    return re.sub(r'[\s•▪■]', '', s)


def mappings(tables):
    result = {}
    for table in tables:
        nodes = {n['node_id']: n['label_raw'] for n in table['row_tree']+table['column_tree']}
        for m in table['cell_mappings']:
            result[m['cell_id']] = dict(row=[nodes[x] for x in m['row_path']],
                col=[nodes[x] for x in m['column_path']], value=m['original_text'])
    return result


def evaluate(previous, output):
    output.mkdir(parents=True, exist_ok=False)
    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (ROOT/'src/doc_understanding/parser').glob('axis_*.py')}
    write(output/'implementation-freeze.json', frozen)
    cases = [(name, previous/'final'/name) for name in
             ['hana-bounded', 'holdout-live-p1', 'holdout-live-p4', 'holdout-live-p6', 'gangdong']]
    cases += [(name, previous/'holdout'/name) for name in
              ['db2026-p1', 'db2026-p2', 'imrich-p1', 'imrich-p3']]
    results = {}; overview = []; equivalents = []
    for name, source in cases:
        for mode in ['rules', 'replay-text', 'replay-vision']:
            if mode != 'rules' and not list(source.glob('*-'+mode.removeprefix('replay-')+'.json')):
                continue
            dest = output/name/mode
            manifest = run(source, dest, replay=mode.removeprefix('replay-') if mode != 'rules' else None)
            states = [read(dest/r['result']) for r in manifest['regions']]
            results[name, mode] = states
            overview.append(dict(case=name, mode=mode,
                supported=sum(r['supported_candidates'] for r in manifest['regions']),
                rejected=sum(r['rejected'] for r in manifest['regions']),
                resolver_invocations=sum(r['model_invocations'] for r in manifest['regions']),
                route_reasons=[r['route_reason'] for r in manifest['regions']],
                actual_api_attempts=manifest['actual_api_attempts'],
                originals_unchanged=manifest['originals_unchanged']))
            if mode == 'rules':
                for record, state in zip(manifest['regions'], states):
                    old = read(source/record['source'].replace('-source.json', '-rules.json'))
                    identical = state['validation'] == old['validation']
                    equivalents.append(dict(case=name, region=record['region_id'],
                                            identical_to_direct_validator=identical))
                    assert identical

    # Only now load source-reviewed evaluation targets, never send to inference.
    gold = read(previous/'holdout-target-results.json')
    targets = []
    for target in gold:
        name = f"{target['doc']}-p{target['page']}"
        states = results[name, 'rules']
        by_id = mappings([t for s in states for t in s['structured']])
        cells = {c['cell_id']: c for s in states for c in s['validation']['cells']}
        cid = target['source_cell_id']; actual = by_id.get(cid)
        both = bool(actual and actual['row'] and actual['col'])
        row_exact = bool(actual and [norm(x) for x in actual['row']] == [norm(x) for x in target['row']])
        col_exact = bool(actual and [norm(x) for x in actual['col']] == [norm(x) for x in target['col']])
        value_exact = bool(actual and norm(actual['value']) == norm(target['value']))
        correct = bool(row_exact and col_exact and value_exact)
        option_targets = {i for s in states for i in s['pending_option_ids']}
        supported_targets = {i for s in states for i in s['rule_supported_ids']}
        if cid is None:
            failure = 'physical_value_not_isolated'
        elif correct:
            failure = 'none'
        elif cid not in option_targets | supported_targets:
            failure = 'candidate_generation_gap'
        else:
            failure = 'model_not_executed_or_path_unresolved'
        targets.append(dict(doc=target['doc'], page=target['page'], value=target['value'],
            expected_row=target['row'], expected_col=target['col'], source_cell_id=cid,
            isolated=cid is not None, physical_usable=bool(cid and cells[cid]['physical_usable']),
            actual=actual, both_paths=both, row_exact=row_exact, column_exact=col_exact,
            value_exact=value_exact, correct=correct, first_failure=failure,
            baseline_correct=target['baseline_value_paths_exact']))
    write(output/'target-results.json', targets)
    isolated = [t for t in targets if t['isolated']]
    usable = [t for t in targets if t['physical_usable']]
    linked = [t for t in isolated if t['both_paths']]
    def metric(rows, field='correct'):
        n = sum(t[field] for t in rows)
        return dict(numerator=n, denominator=len(rows), ratio=n/len(rows) if rows else None)

    hana = results['hana-bounded', 'replay-text'][0]
    hana_positive = next(c for c in hana['validation']['checked_links']
                         if c['proposal']['cell_id'].endswith('/physical001/cell0001'))
    assert hana_positive['status'] == 'supported_candidate'
    neg = results['holdout-live-p4', 'replay-vision'][0]
    assert len(neg['validation']['checked_links']) == 3
    assert all(c['status'] == 'unresolved' for c in neg['validation']['checked_links'])
    gang = [t for s in results['gangdong', 'rules'] for t in s['structured']]
    assert gang == read(previous/'final/gangdong/baseline-structured.json')
    summary = dict(dataset_status='all previously inspected; regression, NOT independent holdout',
        runs=overview, direct_rules_equivalence=equivalents,
        target_count=len(targets), isolated_values=len(isolated), usable_values=len(usable),
        overall_correct_coverage=metric(targets), isolated_correct_coverage=metric(isolated),
        usable_correct_coverage=metric(usable), linked_precision=metric(linked),
        row_accuracy_isolated=metric(isolated, 'row_exact'),
        column_accuracy_isolated=metric(isolated, 'column_exact'),
        wrong_filled_paths=sum(not t['correct'] for t in linked),
        target_changes=sum(t['correct'] != t['baseline_correct'] for t in targets),
        hana_positive_retained=True, wrong_continuation_links_held=3,
        gangdong_unchanged=True, actual_api_calls=0, estimated_cost_usd=0,
        semantic_verified=False, facts_published=0,
        limits=['Source-isolated/usable subsets are not complete physical-grid certification.',
                'Saved GPT replay is not new model accuracy.',
                'Untouched baseline paths can still be wrong; graph does not certify them.',
                'Merged/headerless candidate generation unchanged; no accuracy gain claimed.'])
    write(output/'summary.json', summary)
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == h for p,h in frozen.items())
    style = re.search(r'<style>(.*?)</style>', (previous/'comparison.html').read_text(encoding='utf-8'), re.S).group(1)
    def esc(x): return html.escape(str(x))
    def href(p, text): return '<a href="'+esc(os.path.relpath(p, output).replace('\\','/'))+'">'+esc(text)+'</a>'
    def table(headers, rows):
        return '<table><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr>'+''.join(
            '<tr>'+''.join('<td>'+str(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'
    chunks = ['<!doctype html><html lang="ko"><meta charset="utf-8"><title>행·열 경로 LangGraph 회귀</title><style>'+style+'</style><body>',
        '<h1>행·열 경로 판단: 조건부 LangGraph 실행</h1>',
        '<p>기존 규칙·모델 어댑터·검사기는 그대로 두고 실행 구조를 연결했습니다. 새 API 호출 0회 / $0. 기존 PDF와 JSON은 변경하지 않았습니다.</p>',
        '<p class="notice">이번 결과는 정확도 개선을 입증하는 실험이 아니라 실행 통제와 회귀 검증입니다. 모든 문서는 이미 확인한 자료입니다. 모델 결과는 저장된 응답 재생이며, 미사용 Holdout 또는 새 모델 성능으로 표시하지 않습니다.</p>',
        '<h2>실행 흐름</h2><pre>원본 확인 → 규칙 판단 → 미해결 후보 확인\n  ├ 후보 있음 + 모델 활성화 → LLM 판단(최대 1회)\n  └ 후보 없음 / 모델 비활성화 → 검사\n누락·역할·헤더 근거 검사 → 별도 StructuredTableIR 후보 저장</pre>',
        '<p>이 그래프는 질문 답변 이전의 문서 준비 단계입니다. 기본 CanonicalDocument/DB/RAG 실행에는 아직 적용하지 않았습니다. 경로가 없다는 이유만으로 모든 표를 LLM에 보내지 않습니다. 후보가 없는 병합 영역은 현재 생성기의 한계로 남습니다.</p>',
        '<h2>35개 원본 평가 대상: 값 분리와 의미 경로를 구분</h2>',
        table(['지표','분자 / 분모'], [[esc(k), f"{v['numerator']} / {v['denominator']}"]
            for k,v in summary.items() if isinstance(v,dict) and 'numerator' in v]),
        '<p>linked_precision은 검토한 대상 중 기존 뷰에 양쪽 경로가 채워진 값만의 정확도입니다. 자동 검증 승인율이나 전체 PDF 정확도가 아닙니다. 물리적 분리와 physical_usable도 완전한 격자 Gold 인증을 뜻하지 않습니다.</p>',
        '<h2>개발·회귀 실행</h2>', table(['사례','모드','지지 후보','거절','모델 노드 실행','결과'],[
            [esc(r['case']),esc(r['mode']),r['supported'],r['rejected'],r['resolver_invocations'],
             href(output/r['case']/r['mode']/'manifest.json','실제 JSON')] for r in overview]),
        '<h2>원본 이미지와 노드별 실제 기록</h2>']
    for name in ['hana-bounded','holdout-live-p4','db2026-p2','imrich-p1']:
        source = next(s for n,s in cases if n == name)
        chunks += ['<h3>'+esc(name)+'</h3>',
            '<img style="max-width:100%;max-height:600px" src="'+esc(os.path.relpath(source_image(source,'region-000'),output).replace('\\','/'))+'">',
            '<p>'+href(source/'region-000-source.json','원본 셀·span·좌표 JSON')+' · '+
            href(output/name/'rules/region-000-graph.json','규칙 실행 State')+'</p>']
        if (name,'replay-vision') in results:
            chunks += ['<p>'+href(output/name/'replay-vision/region-000-graph.json','LLM 노드 재생과 검증 State')+'</p>']
    chunks += ['<h2>개별 정답 경로와 실제 결과</h2>', table(['문서/쪽','원문 값','정답 행 / 열','실제 행 / 열','최초 실패'],[
        [esc(f"{t['doc']} p.{t['page']}"),esc(t['value']),esc(str(t['expected_row'])+' / '+str(t['expected_col'])),
         esc(str(t['actual']['row'])+' / '+str(t['actual']['col'])) if t['actual'] else '없음',esc(t['first_failure'])]
        for t in targets]),
        '<h2>남은 작업</h2><p>병합 셀이 포함됐다는 이유로 영역 전체를 후보 생성에서 제외하는 제약, 행 이름이 없는 표, 계속표의 원본 헤더 연결을 별도 실험으로 다뤄야 합니다. 이번 그래프에서는 그 규칙을 바꾸지 않았습니다. 새 후보 생성기를 비교할 때 물리 실패 16건도 전체 분모에 남겨야 합니다.</p>',
        '<p>'+href(output/'summary.json','전체 지표 JSON')+' · '+href(output/'target-results.json','35개 대상별 실제 경로')+'</p></body></html>']
    (output/'comparison.html').write_text(''.join(chunks), encoding='utf-8')
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    s = evaluate(args.previous, args.output)
    print(json.dumps({k:v for k,v in s.items() if k not in ['runs','direct_rules_equivalence']}, ensure_ascii=False))
