"""Read-only diagnostic packaging; never changes predictions or reference annotations."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/tree-phase3a-triage'
TYPES = {
    'reference_binding': ('정답 영역과 원본 셀 대응 재검토', '금액은 원본 셀에 있습니다. 주황색 정답 영역과 파란색 실제 금액 셀의 범위가 같은지 확인해 주세요.'),
    'external_ancestor': ('표 밖 상위 연결의 전파', '설정대상채권이 이 표의 시작 그룹인지 확인해 주세요. UNBOUND_PARENT는 평가표 안에서 대응하지 못한 기존 부모이며 원문 항목명이 아닙니다.'),
    'missing_row_mapping': ('병합 행의 행 경로 누락', '강조한 소계 또는 합계가 왼쪽 정답 경로에 해당하는지 확인해 주세요. 숫자와 열은 있지만 자동 결과에 행 경로가 없습니다.'),
    'scalar_mapping': ('독립 비율 행의 행·열 의미 충돌', '이 비율이 특정 건전성 열의 값인지, 표 전체에 적용되는 B/A인지 확인해 주세요. 빈 정답 열 경로는 검토 기준의 독립 값 표현입니다.'),
    'wrong_ancestor': ('불필요한 부모 연결', '같은 수준의 항목을 부모·자식으로 연결한 것은 아닌지 확인해 주세요. 정답 경로에 없는 상위 항목이 자동 결과에 추가되어 있습니다.'),
    'unclassified': ('추가 분석 필요', '원본과 두 경로를 비교해 주세요. 원인이 아직 분류되지 않았습니다.'),
}

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def classify(issue, evidence_bad):
    if evidence_bad:
        return 'reference_binding'
    pr, er = issue['predicted'], issue['expected']
    if 'UNBOUND_PARENT' in pr['row']:
        return 'external_ancestor'
    if pr['column'] != er['column']:
        return 'scalar_mapping'
    if not pr['row']:
        return 'missing_row_mapping'
    if pr['row'] != er['row']:
        return 'wrong_ancestor'
    return 'unclassified'

def main():
    inputs = [ROOT/'data/tree-phase3a-repair-v3/results.json', ROOT/'data/tree-phase3a/golden.provisional.json', ROOT/'data/tree-phase3a-repair-v3/amount-investigation.json']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    result, gold, amounts = map(read, inputs)
    cells = {c['id']: c for t in gold['tables'] for c in t['cells']}
    tables = {t['id']: t for t in gold['tables']}
    def label(seq):
        return [cells.get(x, {}).get('text', x) for x in seq]
    groups = defaultdict(list)
    parents = []
    for t in result['after']['tables']:
        bad = {i['target'] for i in t['issues'] if i['code'] == 'EVIDENCE'}
        for c, p in t['predicted_row_parents'].items():
            if p and p != t['expected_row_parents'].get(c):
                parents.append({'table': t['table_id'], 'child': c, 'child_label': label([c])[0], 'predicted_parent': p, 'predicted_label': label([p])[0], 'expected_parent': t['expected_row_parents'].get(c), 'status': 'review_required'})
        for i in t['issues']:
            if i['code'] != 'CELL_PATH':
                continue
            kind = classify(i, i['target'] in bad)
            row = cells[i['target']]['row']
            # One external ancestor can affect multiple rows: review it once.
            key = (t['table_id'], kind, None if kind == 'external_ancestor' else row)
            groups[key].append({**i, 'bbox': cells[i['target']]['bbox'], 'bound_cell_id': t['bindings'].get(i['target']), 'expected_labels': {k: label(v) for k,v in i['expected'].items()}, 'predicted_labels': {k: label(v) for k,v in i['predicted'].items()}})
    cases = []
    for (tid, kind, row), members in groups.items():
        t = tables[tid]
        cases.append({'id': f'case-{len(cases)+1:02}', 'table_id': tid, 'page': t['page'], 'title': t['title'], 'category': kind, 'category_label': TYPES[kind][0], 'question': TYPES[kind][1], 'table_bbox': t['bbox'], 'members': members, 'amount_evidence': [a for a in amounts if a['page']==t['page']] if kind=='reference_binding' else [], 'review_status': 'review_required', 'diagnosis_status': 'agent_source_image_checked_not_independent_human_review'})
    counts = Counter(c['category'] for c in cases for _ in c['members'])
    assert sum(counts.values()) == 60
    assert len({m['target'] for c in cases for m in c['members']}) == 60
    payload = {'dataset': 'Namwon development only', 'input_sha256': hashes, 'counts': dict(counts), 'case_count': len(cases), 'cases': cases, 'extra_parents': parents, 'external_calls': 0, 'scores_changed': False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'diagnosis.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    template = (ROOT/'scripts/tree_triage_template.html').read_text(encoding='utf-8')
    template = template.replace('</header>', '<p><a href="arithmetic.html">소계·합계 직접 검산 결과 보기</a></p></header>')
    data = json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')
    (OUT/'index.html').write_text(template.replace('__DATA__',data),encoding='utf-8')
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    lines = ['# 남원 셀 경로 불일치 진단', '', '남원 개발셋 7개 표의 기존 결과를 읽기 전용으로 분석했다. 새 모델 실행이나 점수 변경은 없다.', '', f'60개 불일치를 {len(cases)}개 검토 묶음으로 정리했다. 분류는 원본 페이지 이미지와 저장된 경로에 대한 에이전트 진단이며 독립 사람 검토 완료를 뜻하지 않는다.', '', '| 주된 진단 분류 | 셀 수 |', '|---|---:|']
    lines += [f'| {TYPES[k][0]} | {v} |' for k,v in counts.items()]
    lines += ['', '분류는 중복 집계하지 않기 위한 우선순위 분류다. 정답 영역 문제가 있는 셀에도 잘못된 부모가 함께 존재할 수 있다.', '', '## 확인된 근거', '', '- p43·51의 소계·합계는 원본에서 항목명 칸이 병합되어 있다. 저장된 값 셀 경로는 행 경로가 비어 있고 기간 열 경로는 남아 있다. 행 매핑 단계의 우선 조사 대상이다.', '- p46 첫 표의 30개 값 셀은 동일한 UNBOUND_PARENT를 상속한다. 이는 대응 실패 표식이지 원문 부모 이름이 아니다. 실제 외부 부모의 정당성은 후속 추적 대상으로 남긴다.', '- p46 둘째 표에서 고정은 요주의 아래로, 합계는 추정손실 아래로 연결되어 있다. 원본의 같은 수준 행 배치와 맞지 않는 관계다.', '- p45·46의 B/A는 원본에서 표 전체 비율 행으로 보이며 현재 자동 경로는 행이 비어 있고 합계 열이 연결되어 있다. 독립 값 표현 기준도 함께 검토해야 한다.', '- 4,361,381과 4,315,397은 원본 물리 셀에 존재한다. 정답 주석의 넓은 범위와 실제 금액 셀의 대응을 재검토하며 금액을 추출 실패로 계산하지 않는다.', '', '## 개선 순서', '', '1. 병합 행의 값 셀과 행 노드 연결을 확인하고 일반화 가능한 매핑 수정안을 작성한다.', '2. 표 밖 부모와 동급 행 오연결을 추적한다. 정상 루트 근거 없이 관계를 삭제하지 않는다.', '3. 독립 비율 행의 열 의미와 정답 좌표를 검토한 후 별도 주석 revision을 만든다.', '4. 집계 소속과 산술 범위는 별도로 검증한다. 이번 작업에서 집계 복원 완료를 주장하지 않는다.', '', '## 보존 및 검토', '', '- 기존 부모 오류 9개는 diagnosis.json에 별도 수록했다. 60개 셀 오류와 더해서 오류 수로 계산하지 않는다.', '- 원본 IR, Resolver, 정답 주석, 기존 점수 불변. 입력 SHA-256을 기록하고 생성 후 동일성을 확인했다.', '- 검토 화면의 판정은 브라우저에 임시 보관하고 JSON으로 내보낸다. 파일 또는 정답지에 자동 적용하지 않는다.', '- 남평과 신규 문서 미실행. 일반화 성능과 RAG 성능은 N/A.', '- 원본 p43·45·46·51 이미지를 시각 확인했다. 생성 HTML의 브라우저 렌더링 검증은 별도 수행하지 않았다.']
    (ROOT/'reports/TREE_CELL_TRIAGE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'cells':60,'cases':len(cases),'counts':counts,'extra_parents':len(parents),'input_hashes_unchanged':True},ensure_ascii=False))

if __name__ == '__main__':
    main()
